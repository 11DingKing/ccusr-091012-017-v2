"""
月结封账测试：试算 / 封账 / 会签 / 重开 / 更正版本 / 守卫 / 并发确定性
"""
import threading
from datetime import date
from decimal import Decimal

from django.db import connections
from django.test import TestCase, TransactionTestCase
from rest_framework.test import APIClient

from apps.authentication.backends import generate_token
from apps.authentication.models import User
from apps.warehouse.models import Category, Goods, StockIn, StockOut, Unit, Variety

from .errors import AccountingError, ClosureConflictError, PeriodLockedError
from . import services
from .models import ClosureSignature, PeriodClosure, ReopenRequest


def make_client(user):
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {generate_token(user)}")
    return client


class AccountingFixture(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user("acc-admin", "pass12345", role="admin", real_name="会计")
        self.user = User.objects.create_user("acc-user", "pass12345", role="user")
        self.admin_client = make_client(self.admin)
        self.user_client = make_client(self.user)
        unit = Unit.objects.create(name="件", created_by=self.admin)
        category = Category.objects.create(name="监管器材", unit=unit, created_by=self.admin)
        variety = Variety.objects.create(name="封存设备", category=category, created_by=self.admin)
        self.goods = Goods.objects.create(
            variety=variety, name="封存终端", code="ACC-001",
            quantity=Decimal("10"), warning_threshold=Decimal("2"),
        )


class TrialAndSealFlowTest(AccountingFixture):
    def test_trial_and_period_state_open(self):
        StockIn.objects.create(
            goods=self.goods, operator=self.user, quantity=Decimal("3"),
            business_date=date(2026, 9, 10),
        )
        resp = self.admin_client.get("/api/accounting/trial/", {"period": "2026-09"})
        self.assertEqual(resp.status_code, 200)
        data = resp.json()["data"]
        self.assertEqual(data["summary"]["stock_in_count"], 1)
        self.assertEqual(data["summary"]["stock_in_total"], 3.0)
        self.assertEqual(data["state"]["status"], "open")
        self.assertIn("rule_version", data["rules"])
        self.assertEqual(data["input_range"]["stock_in_count"], 1)

    def test_trial_requires_valid_period(self):
        resp = self.admin_client.get("/api/accounting/trial/", {"period": "2026-9"})
        self.assertEqual(resp.status_code, 400)

    def test_full_seal_lifecycle_snapshot_freezes(self):
        period = "2026-09"
        StockIn.objects.create(
            goods=self.goods, operator=self.user, quantity=Decimal("4"),
            business_date=date(2026, 9, 5),
        )
        outbound = StockOut.objects.create(
            goods=self.goods, operator=self.user, receiver="办案人",
            quantity=Decimal("1"), business_date=date(2026, 9, 6),
        )
        outbound.status = "completed"
        outbound.save()

        # 发起封账
        resp = self.admin_client.post("/api/accounting/closures/", {"period": period}, format="json")
        self.assertEqual(resp.status_code, 200, resp.content)
        closure_id = resp.json()["data"]["id"]
        self.assertEqual(resp.json()["data"]["status"], "sealing")

        # 会签集齐后封账完成（只有一个管理员）
        resp = self.admin_client.post(f"/api/accounting/closures/{closure_id}/sign/")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["data"]["status"], "sealed")

        closure = PeriodClosure.objects.get(pk=closure_id)
        self.assertEqual(closure.summary_snapshot["stock_in_total"], 4.0)
        self.assertEqual(closure.summary_snapshot["stock_out_total"], 1.0)
        self.assertEqual(closure.summary_snapshot["net_total"], 3.0)
        self.assertEqual(closure.input_range["stock_in_count"], 1)

        # 封账后迟到补录同月入库 -> 被拒（409），已签字数字不会改变
        with self.assertRaises(PeriodLockedError):
            StockIn.objects.create(
                goods=self.goods, operator=self.user, quantity=Decimal("99"),
                business_date=date(2026, 9, 20),
            )

        # HTTP 层同样拒绝
        resp = self.user_client.post("/api/accounting/closures/", {"period": period}, format="json")
        self.assertEqual(resp.status_code, 403)  # 普通用户不能发起封账

        # 快照仍然是封账时的数字，复核可重现
        closure.refresh_from_db()
        self.assertEqual(closure.summary_snapshot["stock_in_total"], 4.0)

    def test_duplicate_seal_rejected(self):
        PeriodClosure.objects.create(period="2026-08", version_no=1, status="sealing")
        with self.assertRaises(ClosureConflictError):
            services.initiate_seal("2026-08", self.admin)
        PeriodClosure.objects.filter(period="2026-08").update(status="sealed")
        with self.assertRaises(ClosureConflictError):
            services.initiate_seal("2026-08", self.admin)

    def test_sealing_phase_blocks_writes_and_signature_rules(self):
        services.initiate_seal("2026-07", self.admin)
        with self.assertRaises(PeriodLockedError):
            StockIn.objects.create(
                goods=self.goods, operator=self.user, quantity=Decimal("1"),
                business_date=date(2026, 7, 1),
            )

        closure = PeriodClosure.objects.get(period="2026-07")
        # 非签署人不能签
        other = User.objects.create_user("acc-admin2", "pass12345", role="user")
        with self.assertRaises(AccountingError):
            services.sign_closure(closure.id, other)
        # 正常签署并完成
        services.sign_closure(closure.id, self.admin)
        # 重复签署
        with self.assertRaises(ClosureConflictError):
            services.sign_closure(closure.id, self.admin)

    def test_multi_signer_countersign(self):
        second_admin = User.objects.create_user("acc-admin-b", "pass12345", role="admin")
        closure = services.initiate_seal("2026-06", self.admin)
        self.assertEqual(set(closure.required_signer_ids), {self.admin.id, second_admin.id})

        services.sign_closure(closure.id, self.admin)
        closure.refresh_from_db()
        self.assertEqual(closure.status, "sealing")  # 未集齐

        services.sign_closure(closure.id, second_admin)
        closure.refresh_from_db()
        self.assertEqual(closure.status, "sealed")  # 最后一签自动封账
        self.assertEqual(ClosureSignature.objects.filter(closure=closure).count(), 2)


class SealedRecordGuardTest(AccountingFixture):
    def setUp(self):
        super().setUp()
        self.record = StockIn.objects.create(
            goods=self.goods, operator=self.user, quantity=Decimal("2"),
            business_date=date(2026, 9, 2),
        )
        closure = services.initiate_seal("2026-09", self.admin)
        services.sign_closure(closure.id, self.admin)

    def test_update_and_delete_blocked_while_sealed(self):
        self.record.refresh_from_db()
        self.record.quantity = Decimal("9")
        with self.assertRaises(PeriodLockedError):
            self.record.save()
        with self.assertRaises(PeriodLockedError):
            self.record.delete()

    def test_other_months_still_editable(self):
        # 封 9 月不影响 10 月
        other = StockIn.objects.create(
            goods=self.goods, operator=self.user, quantity=Decimal("5"),
            business_date=date(2026, 10, 1),
        )
        other.quantity = Decimal("6")
        other.save()
        other.delete()

    def test_stockout_create_blocked_while_sealed(self):
        # 9 月已封账：新建待审批出库单也被拒
        with self.assertRaises(PeriodLockedError):
            StockOut.objects.create(
                goods=self.goods, operator=self.user, receiver="x",
                quantity=Decimal("1"), business_date=date(2026, 9, 3),
            )


class CrossMonthLateEntryTest(AccountingFixture):
    def test_late_entry_for_unsealed_month_goes_to_that_month(self):
        # 10 月补录一笔 9 月业务，9 月未封账 -> 允许，归属 9 月 v1
        record = StockIn.objects.create(
            goods=self.goods, operator=self.user, quantity=Decimal("7"),
            business_date=date(2026, 9, 28),
        )
        self.assertEqual(record.period, "2026-09")
        self.assertEqual(record.version_no, 1)

        summary = services.trial("2026-09")["summary"]
        self.assertEqual(summary["stock_in_total"], 7.0)
        # 10 月不含该笔
        summary_oct = services.trial("2026-10")["summary"]
        self.assertEqual(summary_oct["stock_in_total"], 0.0)

    def test_late_entry_for_sealed_month_blocked_without_reopen(self):
        closure = services.initiate_seal("2026-09", self.admin)
        services.sign_closure(closure.id, self.admin)
        with self.assertRaises(PeriodLockedError):
            StockIn.objects.create(
                goods=self.goods, operator=self.user, quantity=Decimal("7"),
                business_date=date(2026, 9, 29),  # 10 月补录 9 月
            )


class ReopenAndVersionTest(AccountingFixture):
    def _seal_september(self):
        StockIn.objects.create(
            goods=self.goods, operator=self.user, quantity=Decimal("4"),
            business_date=date(2026, 9, 5),
        )
        closure = services.initiate_seal("2026-09", self.admin)
        services.sign_closure(closure.id, self.admin)
        return closure

    def test_reopen_request_approval_creates_new_version(self):
        self._seal_september()

        # 普通用户申请重开
        resp = self.user_client.post(
            "/api/accounting/reopen-requests/",
            {"period": "2026-09", "reason": "月底漏登一笔入库"},
            format="json",
        )
        self.assertEqual(resp.status_code, 200)
        req_id = resp.json()["data"]["id"]

        # 重复申请 -> 409
        resp = self.user_client.post(
            "/api/accounting/reopen-requests/",
            {"period": "2026-09", "reason": "再次申请"},
            format="json",
        )
        self.assertEqual(resp.status_code, 409)

        # 普通用户无权审批
        resp = self.user_client.post(
            f"/api/accounting/reopen-requests/{req_id}/decide/",
            {"action": "approved"}, format="json",
        )
        self.assertEqual(resp.status_code, 403)

        # 管理员批准
        resp = self.admin_client.post(
            f"/api/accounting/reopen-requests/{req_id}/decide/",
            {"action": "approved", "reply": "同意更正"}, format="json",
        )
        self.assertEqual(resp.status_code, 200)
        new_version = resp.json()["data"]["new_version"]
        self.assertEqual(new_version["version_no"], 2)
        self.assertEqual(new_version["status"], "open")

        state = services.get_period_state("2026-09")
        self.assertEqual(state["version_no"], 2)
        self.assertTrue(state["editable"])

    def test_corrections_enter_new_version_and_old_snapshot_frozen(self):
        v1 = self._seal_september()

        req = services.request_reopen("2026-09", self.user, "补录")
        services.decide_reopen(req.id, self.admin, "approved")

        # 迟到补录进入 v2
        correction = StockIn.objects.create(
            goods=self.goods, operator=self.user, quantity=Decimal("2"),
            business_date=date(2026, 9, 25),
        )
        self.assertEqual(correction.version_no, 2)

        # 默认查询当前版本（v2）
        qs = services.versioned_stock_in("2026-09")
        self.assertEqual(list(qs.values_list("version_no", flat=True)), [2])

        # 追溯旧版本（v1）仍可查询且数字不变
        qs_v1 = services.versioned_stock_in("2026-09", version=1)
        self.assertEqual(list(qs_v1.values_list("version_no", flat=True)), [1])

        # 历史版本记录不可改
        old = qs_v1.first()
        old.quantity = Decimal("99")
        with self.assertRaises(PeriodLockedError):
            old.save()

        # v1 快照保持冻结
        v1.refresh_from_db()
        self.assertEqual(v1.status, "reopened")
        self.assertEqual(v1.summary_snapshot["stock_in_total"], 4.0)

        # v2 试算包含更正，且给出与 v1 的差异
        trial = services.trial("2026-09")
        self.assertEqual(trial["summary"]["stock_in_total"], 2.0)
        self.assertEqual(trial["delta_vs_previous"]["stock_in_total"], -2.0)

        # 全部版本
        qs_all = services.versioned_stock_in("2026-09", version="all")
        self.assertEqual(qs_all.count(), 2)

    def test_reseal_v2_snapshot_independent(self):
        self._seal_september()
        req = services.request_reopen("2026-09", self.user, "补录")
        services.decide_reopen(req.id, self.admin, "approved")
        StockIn.objects.create(
            goods=self.goods, operator=self.user, quantity=Decimal("2"),
            business_date=date(2026, 9, 25),
        )
        closure2 = services.initiate_seal("2026-09", self.admin)
        self.assertEqual(closure2.version_no, 2)
        services.sign_closure(closure2.id, self.admin)
        closure2.refresh_from_db()
        self.assertEqual(closure2.summary_snapshot["stock_in_total"], 2.0)

        # 版本列表可追溯两版
        versions = services.list_versions("2026-09")
        self.assertEqual([c.version_no for c in versions], [2, 1])

    def test_reject_reopen_keeps_period_sealed(self):
        self._seal_september()
        req = services.request_reopen("2026-09", self.user, "理由不充分")
        services.decide_reopen(req.id, self.admin, "rejected", "不同意")
        req.refresh_from_db()
        self.assertEqual(req.status, "rejected")
        self.assertEqual(services.get_period_state("2026-09")["status"], "sealed")
        # 驳回后可以重新申请（待批唯一约束已释放）
        req2 = services.request_reopen("2026-09", self.user, "新理由")
        self.assertEqual(req2.status, "pending")

    def test_reopen_requires_sealed_period(self):
        with self.assertRaises(ClosureConflictError):
            services.request_reopen("2026-05", self.user, "x")

    def test_reopen_reason_required(self):
        self._seal_september()
        resp = self.user_client.post(
            "/api/accounting/reopen-requests/",
            {"period": "2026-09", "reason": ""}, format="json",
        )
        self.assertEqual(resp.status_code, 400)

    def test_pending_stockout_migrates_to_new_version(self):
        # 封账时存在待审批出库单：重开后随工作区迁入新版本，可继续审批
        pending_out = StockOut.objects.create(
            goods=self.goods, operator=self.user, receiver="待批",
            quantity=Decimal("1"), business_date=date(2026, 9, 8),
        )
        self.assertEqual(pending_out.version_no, 1)
        closure = services.initiate_seal("2026-09", self.admin)
        services.sign_closure(closure.id, self.admin)

        req = services.request_reopen("2026-09", self.user, "继续审批")
        services.decide_reopen(req.id, self.admin, "approved")

        pending_out.refresh_from_db()
        self.assertEqual(pending_out.version_no, 2)
        # 可以继续审批流转
        pending_out.status = "approved"
        pending_out.save()


# ==================== 并发确定性（需要真实提交，使用 TransactionTestCase） ====================

def run_threads(target, count):
    """启动 count 个线程同时执行 target(thread_index)，返回各自结果/异常"""
    barrier = threading.Barrier(count)
    results = [None] * count

    def worker(i):
        barrier.wait()
        try:
            results[i] = ("ok", target(i))
        except Exception as exc:  # noqa: BLE001 - 测试中需要收集跨线程异常
            results[i] = ("err", type(exc).__name__, str(exc))
        finally:
            # 只关闭当前线程自己的数据库连接（连接是线程本地的）
            connections['default'].close()

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(count)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    return results


class ConcurrencyDeterminismTest(TransactionTestCase):
    reset_sequences = True

    def setUp(self):
        self.admin = User.objects.create_user("c-admin", "pass12345", role="admin")
        self.admin2 = User.objects.create_user("c-admin2", "pass12345", role="admin")

    def test_concurrent_seal_only_one_wins(self):
        def attempt(_):
            return services.initiate_seal("2026-09", self.admin)

        results = run_threads(attempt, 2)
        oks = [r for r in results if r[0] == "ok"]
        errs = [r for r in results if r[0] == "err"]
        self.assertEqual(len(oks), 1, results)
        self.assertEqual(len(errs), 1)
        self.assertEqual(errs[0][1], "ClosureConflictError")
        self.assertEqual(PeriodClosure.objects.filter(period="2026-09").count(), 1)

    def test_concurrent_duplicate_signature_only_one(self):
        closure = services.initiate_seal("2026-08", self.admin)
        # 两个线程以同一签署人并发签署
        results = run_threads(lambda _: services.sign_closure(closure.id, self.admin2), 2)
        oks = [r for r in results if r[0] == "ok"]
        errs = [r for r in results if r[0] == "err"]
        self.assertEqual(len(oks), 1, results)
        self.assertEqual(errs[0][1], "ClosureConflictError")
        self.assertEqual(
            ClosureSignature.objects.filter(closure=closure, signer=self.admin2).count(), 1
        )

    def test_concurrent_reopen_decision_only_one(self):
        closure = services.initiate_seal("2026-07", self.admin)
        services.sign_closure(closure.id, self.admin)
        services.sign_closure(closure.id, self.admin2)
        req = services.request_reopen("2026-07", self.admin, "并发重开")
        req_id = req.id

        results = run_threads(lambda _: services.decide_reopen(req_id, self.admin, "approved"), 2)
        oks = [r for r in results if r[0] == "ok"]
        errs = [r for r in results if r[0] == "err"]
        self.assertEqual(len(oks), 1, results)
        self.assertEqual(errs[0][1], "ClosureConflictError")
        req.refresh_from_db()
        self.assertEqual(req.status, "approved")
        # 只产生一个新版本
        self.assertEqual(
            PeriodClosure.objects.filter(period="2026-07", status="open").count(), 1
        )


class VersionedRecordsAPITest(AccountingFixture):
    def test_records_endpoint_default_current_and_trace(self):
        record = StockIn.objects.create(
            goods=self.goods, operator=self.user, quantity=Decimal("4"),
            business_date=date(2026, 9, 5),
        )
        closure = services.initiate_seal("2026-09", self.admin)
        services.sign_closure(closure.id, self.admin)
        req = services.request_reopen("2026-09", self.user, "补录")
        services.decide_reopen(req.id, self.admin, "approved")
        StockIn.objects.create(
            goods=self.goods, operator=self.user, quantity=Decimal("2"),
            business_date=date(2026, 9, 25),
        )

        # 默认当前版本 v2
        resp = self.admin_client.get("/api/accounting/records/", {"period": "2026-09"})
        self.assertEqual(resp.status_code, 200)
        data = resp.json()["data"]
        self.assertEqual(data["resolved_version"], 2)
        self.assertEqual(data["stock_in"]["total"], 1)

        # 追溯 v1
        resp = self.admin_client.get(
            "/api/accounting/records/", {"period": "2026-09", "version": "1"}
        )
        data = resp.json()["data"]
        self.assertEqual(data["stock_in"]["total"], 1)
        self.assertEqual(data["stock_in"]["list"][0]["id"], record.id)

        # all
        resp = self.admin_client.get(
            "/api/accounting/records/", {"period": "2026-09", "version": "all"}
        )
        self.assertEqual(resp.json()["data"]["stock_in"]["total"], 2)

    def test_closures_list_exposes_snapshots(self):
        closure = services.initiate_seal("2026-09", self.admin)
        services.sign_closure(closure.id, self.admin)
        resp = self.admin_client.get("/api/accounting/closures/", {"period": "2026-09"})
        self.assertEqual(resp.status_code, 200)
        payload = resp.json()["data"]
        self.assertEqual(payload["current_version"], 1)
        self.assertEqual(payload["versions"][0]["summary_snapshot"]["period"], "2026-09")

    def test_reopen_list_filter(self):
        services.initiate_seal("2026-09", self.admin)
        closure = PeriodClosure.objects.get(period="2026-09")
        closure.status = "sealed"
        closure.save()
        ReopenRequest.objects.create(
            period="2026-09", version_no=1, closure=closure,
            applicant=self.user, reason="r",
        )
        resp = self.admin_client.get(
            "/api/accounting/reopen-requests/", {"period": "2026-09", "status": "pending"}
        )
        self.assertEqual(resp.json()["data"]["total"], 1)


class PeriodStateAPITest(AccountingFixture):
    def test_state_view(self):
        resp = self.admin_client.get("/api/accounting/periods/2026-09/")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["data"], {
            "period": "2026-09", "version_no": 1,
            "status": "open", "editable": True, "locked": False,
        })

    def test_state_view_bad_period(self):
        resp = self.admin_client.get("/api/accounting/periods/bad/")
        self.assertEqual(resp.status_code, 400)
