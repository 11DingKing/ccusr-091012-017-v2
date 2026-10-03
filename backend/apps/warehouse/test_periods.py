"""会计期间：试算、封账、重开审批、更正版本、并发签署测试。"""
import threading
from datetime import date
from decimal import Decimal

from django.db import connection
from django.test import TestCase, TransactionTestCase
from rest_framework.test import APIClient

from apps.authentication.backends import generate_token
from apps.authentication.models import User
from apps.core.exceptions import BusinessException

from .models import (
    AccountingPeriod, Approval, Category, Goods, PeriodVersion,
    ReopenRequest, StockIn, StockOut, Unit, Variety,
)
from . import periods as svc
from .records import create_stock_in, create_stock_out


PERIOD = '2026-08'
BUSINESS_DAY = date(2026, 8, 15)


class PeriodFixture(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user("period-admin", "testpass123", role="admin")
        self.user = User.objects.create_user("period-user", "testpass123", role="user")
        self.admin_client = self._client(self.admin)
        self.user_client = self._client(self.user)
        unit = Unit.objects.create(name="件", created_by=self.admin)
        category = Category.objects.create(name="受控器材", unit=unit, created_by=self.admin)
        variety = Variety.objects.create(name="记录终端", category=category, created_by=self.admin)
        self.goods = Goods.objects.create(
            variety=variety, name="执法记录终端", code="DEV-001",
            quantity=Decimal("12"), warning_threshold=Decimal("5"),
        )

    def _client(self, user):
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION=f"Bearer {generate_token(user)}")
        return client

    def stock_in(self, qty, day=BUSINESS_DAY, client=None):
        client = client or self.admin_client
        return client.post('/api/stock-in/', {
            'goods': self.goods.id, 'quantity': str(qty),
            'business_date': day.isoformat(),
        }, format='json')

    def stock_out(self, qty, day=BUSINESS_DAY, client=None):
        client = client or self.admin_client
        return client.post('/api/stock-out/', {
            'goods': self.goods.id, 'quantity': str(qty), 'receiver': '领用人',
            'receiver_dept': '一队', 'business_date': day.isoformat(),
        }, format='json')

    def trial(self, period=PERIOD, client=None):
        client = client or self.admin_client
        return client.post(f'/api/periods/{period}/trial/', {}, format='json')

    def close(self, period=PERIOD, client=None):
        client = client or self.admin_client
        return client.post(f'/api/periods/{period}/close/', {}, format='json')

    def summary(self, period=PERIOD, version=None, client=None):
        client = client or self.admin_client
        url = f'/api/periods/{period}/summary/'
        if version is not None:
            url += f'?version={version}'
        return client.get(url)


class PeriodAttributionTest(PeriodFixture):
    def test_business_date_determines_period_cross_month(self):
        # 9 月发生的业务在 10 月才补录，归属 9 月而非录入当月
        resp = self.stock_in(3, day=date(2026, 9, 30), client=self.user_client)
        self.assertEqual(resp.status_code, 200)
        record = StockIn.objects.get(id=resp.json()['data']['id'])
        self.assertEqual(record.period, '2026-09')
        self.assertEqual(record.version_no, 1)
        self.assertEqual(record.effective_date, date(2026, 9, 30))

    def test_period_key_validation(self):
        resp = self.admin_client.get('/api/periods/2026-13/summary/')
        self.assertEqual(resp.status_code, 400)


class TrialCloseTest(PeriodFixture):
    def test_trial_then_close_freezes_snapshot(self):
        self.stock_in(10)
        out = self.stock_out(4)
        # 待审批出库不计入汇总
        trial = self.trial()
        self.assertEqual(trial.status_code, 200)
        self.assertEqual(trial.json()['data']['summary']['in_total'], 10.0)
        self.assertEqual(trial.json()['data']['summary']['out_total'], 0.0)

        # 签署通过后出库计入
        sign = self.admin_client.post(
            f"/api/stock-out/{out.json()['data']['id']}/sign/",
            {'approved': True, 'remark': '同意'}, format='json'
        )
        self.assertEqual(sign.status_code, 200)

        self.trial()
        closed = self.close()
        self.assertEqual(closed.status_code, 200)
        body = closed.json()['data']
        self.assertEqual(body['status'], 'closed')
        self.assertEqual(body['current_version'], 1)

        version = body['version']
        self.assertEqual(version['version_no'], 1)
        # 封账固化输入范围、规则、汇总摘要
        scope = version['input_scope']
        self.assertEqual(scope['start_date'], '2026-08-01')
        self.assertEqual(scope['end_date'], '2026-08-31')
        self.assertEqual(scope['stock_in_count'], 1)
        self.assertEqual(scope['stock_out_count'], 1)
        self.assertEqual(len(scope['stock_in_records']), 1)
        self.assertEqual(version['rule']['code'], 'receipt_issue_summary')
        self.assertEqual(version['summary']['out_total'], 4.0)
        self.assertEqual(version['summary']['in_total'], 10.0)

    def test_close_requires_trial(self):
        self.stock_in(1)
        resp = self.close()
        self.assertEqual(resp.status_code, 400)
        self.assertIn('试算', resp.json()['message'])

    def test_close_rejected_when_data_changed_after_trial(self):
        self.stock_in(10)
        self.trial()
        # 试算后又发生变动（补录），未重新试算不能封账
        resp_late = self.stock_in(5)
        self.assertEqual(resp_late.status_code, 200)
        resp = self.close()
        self.assertEqual(resp.status_code, 409)
        self.assertIn('重新试算', resp.json()['message'])
        # 重新试算后封账成功
        self.trial()
        self.assertEqual(self.close().status_code, 200)

    def test_close_requires_admin(self):
        self.stock_in(1)
        resp = self.close(client=self.user_client)
        self.assertEqual(resp.status_code, 403)

    def test_close_empty_period_rejected(self):
        resp = self.close(period='2027-01')
        self.assertEqual(resp.status_code, 400)


class ClosedWriteProtectionTest(PeriodFixture):
    def _seal(self):
        self.stock_in(10)
        self.trial()
        self.close()

    def test_closed_period_blocks_late_entry_for_everyone(self):
        self._seal()
        # 月底已封账，迟到补录 8 月业务——普通用户与管理员都被拒绝
        for client in (self.user_client, self.admin_client):
            resp = self.stock_in(5, client=client)
            self.assertEqual(resp.status_code, 403)
            self.assertIn('封账', resp.json()['message'])

    def test_closed_period_blocks_modify_and_delete(self):
        self._seal()
        record = StockIn.objects.get()
        resp = self.admin_client.put(f"/api/stock-in/{record.id}/",
                                     {'quantity': '99'}, format='json')
        self.assertEqual(resp.status_code, 403)
        resp = self.admin_client.delete(f"/api/stock-in/{record.id}/")
        self.assertEqual(resp.status_code, 403)
        record.refresh_from_db()
        self.assertEqual(record.quantity, Decimal('10'))

    def test_closed_period_blocks_signing(self):
        out = self.stock_out(2)
        self.trial()
        self.close()
        resp = self.admin_client.post(
            f"/api/stock-out/{out.json()['data']['id']}/sign/",
            {'approved': True}, format='json'
        )
        self.assertEqual(resp.status_code, 403)
        self.assertEqual(StockOut.objects.get(id=out.json()['data']['id']).status, 'pending')

    def test_other_months_unaffected_by_closed_period(self):
        self._seal()
        # 跨月：9 月、10 月业务照常
        resp = self.stock_in(7, day=date(2026, 9, 2))
        self.assertEqual(resp.status_code, 200)
        resp = self.stock_out(1, day=date(2026, 10, 5))
        self.assertEqual(resp.status_code, 200)

    def test_repeated_close_is_idempotent(self):
        self._seal()
        again = self.close()
        self.assertEqual(again.status_code, 200)
        self.assertEqual(again.json()['data']['current_version'], 1)
        self.assertEqual(PeriodVersion.objects.filter(period__period=PERIOD).count(), 1)


class ReopenVersionTest(PeriodFixture):
    def _seal(self):
        self.stock_in(10)
        self.trial()
        self.close()

    def _request_reopen(self, reason='凭证补录', client=None):
        client = client or self.user_client
        return client.post('/api/reopen-requests/',
                           {'period': PERIOD, 'reason': reason}, format='json')

    def test_reopen_flow_creates_new_version_and_preserves_old(self):
        self._seal()
        v1 = self.summary(version=1).json()['data']
        self.assertEqual(v1['summary']['in_total'], 10.0)

        # 普通用户申请重开
        req = self._request_reopen()
        self.assertEqual(req.status_code, 200)
        request_id = req.json()['data']['id']

        # 普通用户不能审批
        forbidden = self.user_client.post(
            f'/api/reopen-requests/{request_id}/decide/',
            {'approved': True}, format='json'
        )
        self.assertEqual(forbidden.status_code, 403)

        # 封账期间即便有待审批申请，仍不能改账
        blocked = self.stock_in(5)
        self.assertEqual(blocked.status_code, 403)

        # 管理员批准 → 期间开放，进入 v2
        approved = self.admin_client.post(
            f'/api/reopen-requests/{request_id}/decide/',
            {'approved': True, 'remark': '同意更正'}, format='json'
        )
        self.assertEqual(approved.status_code, 200)
        self.assertEqual(approved.json()['data']['reopened_version'], 2)
        period = AccountingPeriod.objects.get(period=PERIOD)
        self.assertEqual(period.status, 'open')
        self.assertEqual(period.current_version, 2)

        # 所有变更进入新版本
        entry = self.stock_in(5)
        self.assertEqual(entry.status_code, 200)
        self.assertEqual(StockIn.objects.get(id=entry.json()['data']['id']).version_no, 2)

        # 再次封账生成 v2
        self.trial()
        self.close()
        current = self.summary().json()['data']
        self.assertEqual(current['version_no'], 2)
        self.assertEqual(current['summary']['in_total'], 15.0)

        # 旧版本 v1 仍可追溯，数字保持签字时结果
        old = self.summary(version=1).json()['data']
        self.assertTrue(old['sealed'])
        self.assertEqual(old['summary']['in_total'], 10.0)
        self.assertEqual(len(old['input_scope']['stock_in_records']), 1)

        versions = self.admin_client.get(f'/api/periods/{PERIOD}/versions/').json()['data']
        self.assertEqual(versions['total'], 2)

    def test_reject_reopen_keeps_period_closed(self):
        self._seal()
        request_id = self._request_reopen().json()['data']['id']
        resp = self.admin_client.post(
            f'/api/reopen-requests/{request_id}/decide/',
            {'approved': False, 'remark': '理由不充分'}, format='json'
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(AccountingPeriod.objects.get(period=PERIOD).status, 'closed')
        self.assertEqual(self.stock_in(1).status_code, 403)

    def test_duplicate_and_invalid_reopen_requests(self):
        self._seal()
        first = self._request_reopen()
        self.assertEqual(first.status_code, 200)
        duplicate = self._request_reopen()
        self.assertEqual(duplicate.status_code, 409)

        # 未封账期间不能申请重开
        resp = self.user_client.post('/api/reopen-requests/',
                                     {'period': '2027-03', 'reason': '测试'}, format='json')
        self.assertEqual(resp.status_code, 400)

    def test_repeated_decision_is_conflict(self):
        self._seal()
        request_id = self._request_reopen().json()['data']['id']
        url = f'/api/reopen-requests/{request_id}/decide/'
        first = self.admin_client.post(url, {'approved': True}, format='json')
        self.assertEqual(first.status_code, 200)
        again = self.admin_client.post(url, {'approved': False}, format='json')
        self.assertEqual(again.status_code, 409)
        # 首次签署结果不变
        self.assertEqual(ReopenRequest.objects.get(id=request_id).status, 'approved')

    def test_modified_record_moves_to_new_version_listing(self):
        self._seal()
        record = StockIn.objects.get()

        request_id = self._request_reopen().json()['data']['id']
        self.admin_client.post(f'/api/reopen-requests/{request_id}/decide/',
                               {'approved': True}, format='json')

        resp = self.admin_client.put(f"/api/stock-in/{record.id}/",
                                     {'quantity': '8'}, format='json')
        self.assertEqual(resp.status_code, 200)
        record.refresh_from_db()
        self.assertEqual(record.version_no, 2)

        v1_list = self.admin_client.get(f'/api/stock-in/?period={PERIOD}&version=1').json()['data']
        v2_list = self.admin_client.get(f'/api/stock-in/?period={PERIOD}&version=2').json()['data']
        self.assertEqual(v1_list['total'], 0)
        self.assertEqual(v2_list['total'], 1)
        self.assertEqual(v2_list['list'][0]['quantity'], '8.00')

        # 默认按期间查询显示当前全部有效记录（该记录已随更正在 v2）
        default_list = self.admin_client.get(f'/api/stock-in/?period={PERIOD}').json()['data']
        self.assertEqual(default_list['total'], 1)
        self.assertEqual(default_list['list'][0]['version_no'], 2)
        # 旧版本完整明细通过封账快照追溯（见 test_reopen_flow）
        snapshot_v1 = self.summary(version=1).json()['data']
        self.assertEqual(snapshot_v1['summary']['in_total'], 10.0)

    def test_audit_log_records_lifecycle(self):
        self.stock_in(2)
        self.trial()
        self.close()
        request_id = self._request_reopen().json()['data']['id']
        self.admin_client.post(f'/api/reopen-requests/{request_id}/decide/',
                               {'approved': True}, format='json')
        actions = self.admin_client.get(
            f'/api/periods/{PERIOD}/audit-logs/'
        ).json()['data']['list']
        names = [a['action'] for a in actions]
        self.assertEqual(names.count('record_change'), 1)
        self.assertEqual(
            sorted(names),
            ['close', 'record_change', 'reopen_approve', 'reopen_request', 'trial'],
        )


class StockOutRuleTest(PeriodFixture):
    def test_only_counted_statuses_enter_summary(self):
        self.stock_in(10)
        rejected = self.stock_out(3)
        self.admin_client.post(f"/api/stock-out/{rejected.json()['data']['id']}/sign/",
                               {'approved': False}, format='json')
        trial = self.trial().json()['data']
        self.assertEqual(trial['summary']['out_count'], 0)
        self.assertEqual(trial['input_scope']['excluded_stock_out_count'], 1)

    def test_approved_out_cannot_be_edited_directly(self):
        out = self.stock_out(2)
        out_id = out.json()['data']['id']
        self.admin_client.post(f'/api/stock-out/{out_id}/sign/',
                               {'approved': True}, format='json')
        resp = self.admin_client.put(f'/api/stock-out/{out_id}/',
                                     {'quantity': '1'}, format='json')
        self.assertEqual(resp.status_code, 400)


class ConcurrencyTest(TransactionTestCase):
    """并发签署 / 并发封账必须有确定结果（SQLite BEGIN IMMEDIATE 串行化）。"""

    def setUp(self):
        self.admin = User.objects.create_user("c-admin", "testpass123", role="admin")
        unit = Unit.objects.create(name="件", created_by=self.admin)
        category = Category.objects.create(name="受控器材", unit=unit, created_by=self.admin)
        variety = Variety.objects.create(name="终端", category=category, created_by=self.admin)
        self.goods = Goods.objects.create(
            variety=variety, name="终端机", code="C-1",
            quantity=Decimal("5"), warning_threshold=Decimal("1"),
        )

    def _run_concurrent(self, target):
        barrier = threading.Barrier(2)
        outcomes = []

        def worker():
            barrier.wait()
            try:
                outcomes.append(('ok', target()))
            except BusinessException as exc:
                outcomes.append(('error', exc.code))
            finally:
                connection.close()

        threads = [threading.Thread(target=worker) for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        return outcomes

    def test_concurrent_stock_out_sign_single_winner(self):
        out = create_stock_out(self.admin, goods_id=self.goods.id, quantity=Decimal('1'),
                               receiver='r', business_date=BUSINESS_DAY)

        def sign():
            svc.sign_stock_out(out.id, self.admin, True, 'ok')
            return 'signed'

        outcomes = self._run_concurrent(sign)
        self.assertEqual(sorted(outcomes), [('error', 409), ('ok', 'signed')])
        self.assertEqual(StockOut.objects.get(id=out.id).status, 'approved')
        self.assertEqual(Approval.objects.filter(stock_out_id=out.id).count(), 1)

    def test_concurrent_close_single_version(self):
        create_stock_in(self.admin, goods_id=self.goods.id, quantity=Decimal('3'),
                        business_date=BUSINESS_DAY)
        svc.trial_period(PERIOD, self.admin)

        outcomes = self._run_concurrent(lambda: svc.close_period(PERIOD, self.admin)[2])
        self.assertEqual(sorted(outcomes), [('ok', 'already_closed'), ('ok', 'closed')])
        self.assertEqual(PeriodVersion.objects.filter(period__period=PERIOD).count(), 1)
        self.assertEqual(AccountingPeriod.objects.get(period=PERIOD).current_version, 1)

    def test_concurrent_reopen_decision_single_winner(self):
        create_stock_in(self.admin, goods_id=self.goods.id, quantity=Decimal('3'),
                        business_date=BUSINESS_DAY)
        svc.trial_period(PERIOD, self.admin)
        svc.close_period(PERIOD, self.admin)
        req = svc.request_reopen(PERIOD, self.admin, '补录凭证')

        def decide():
            result = svc.decide_reopen(req.id, self.admin, True, 'ok')
            return result.status

        outcomes = self._run_concurrent(decide)
        self.assertEqual(sorted(outcomes), [('error', 409), ('ok', 'approved')])
        self.assertEqual(AccountingPeriod.objects.get(period=PERIOD).current_version, 2)
        self.assertEqual(ReopenRequest.objects.filter(period__period=PERIOD,
                                                       status='approved').count(), 1)
