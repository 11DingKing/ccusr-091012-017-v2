"""
月结封账核心服务

状态机（每个月份可有多个版本，每个版本一行 PeriodClosure）：

    (无记录) ──发起封账──> sealing ──会签集齐──> sealed
                                  └─(冲突) 409
    sealed ──批准重开──> reopened（同事务创建下一版本 open 工作区）
    open   ──发起封账──> sealing

确定性保证：
- 发起封账用条件 UPDATE（status='open' -> 'sealing'）抢占，并发只有一个成功；
- 会签依赖 (closure, signer) 唯一约束，重复/并发签署只有一次插入成功；
- 重开审批用条件 UPDATE（status='pending'）抢占，重复审批只有一次生效；
- 待审批重开申请有部分唯一索引，同月份同时只存在一条。
"""
from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.authentication.models import User
from apps.warehouse.models import StockIn, StockOut

from .errors import AccountingError, ClosureConflictError, ClosurePermissionError, PeriodLockedError
from .locking import locked
from .models import ClosureSignature, PeriodClosure, ReopenRequest
from .periods import parse_period
from .snapshots import build_snapshots

ADMIN_ROLES = ('superadmin', 'admin')


# ==================== 期间状态查询 ====================

def get_latest_closure(period):
    """某月份最新版本的封账记录（无则 None）"""
    return PeriodClosure.objects.filter(period=period).order_by('-version_no').first()


def current_version(period):
    """当前工作版本号（无任何封账记录时为 1）"""
    latest = get_latest_closure(period)
    return latest.version_no if latest else 1


def get_period_state(period):
    """期间状态摘要"""
    latest = get_latest_closure(period)
    if latest is None:
        return {
            'period': period,
            'version_no': 1,
            'status': 'open',
            'editable': True,
            'locked': False,
        }
    return {
        'period': period,
        'version_no': latest.version_no,
        'status': latest.status,
        'editable': latest.status == 'open',
        'locked': latest.is_locked,
    }


def list_versions(period):
    """某月份所有版本（含冻结快照），新版本在前"""
    return list(
        PeriodClosure.objects.filter(period=period)
        .prefetch_related('signatures')
        .order_by('-version_no')
    )


# ==================== 修改守卫 ====================

def ensure_period_editable(period):
    """封账/签署期间禁止写入所属月份，违例抛 409"""
    latest = get_latest_closure(period)
    if latest is not None and latest.is_locked:
        raise PeriodLockedError(
            f'{period} 月份{latest.get_status_display()}，不能修改该月收发记录，请先申请重开'
        )
    return True


def ensure_record_mutable(record):
    """已有记录的修改/删除守卫：期间锁定或历史版本行均不可改"""
    period = record.period
    latest = get_latest_closure(period)
    if latest is not None and latest.is_locked:
        raise PeriodLockedError(
            f'{period} 月份{latest.get_status_display()}，记录已冻结，请先申请重开'
        )
    # 当前工作版本之前的行属于已封账历史版本，只允许追溯，不允许修改
    working_version = latest.version_no if latest is not None else 1
    if record.version_no < working_version:
        raise PeriodLockedError(
            f'该记录属于第 {record.version_no} 版（当前为第 {working_version} 版），'
            '历史版本记录不可修改，更正请在新版本中补录'
        )
    return True


def assign_version(period):
    """新建收发记录时调用：校验期间可写并返回应归属的版本号"""
    ensure_period_editable(period)
    return current_version(period)


# ==================== 试算 ====================

def trial(period):
    """试算：实时计算当前工作版本（不落库），并附期间状态与上一版封账快照便于比对"""
    parse_period(period)
    version_no = current_version(period)
    input_range, rules, summary = build_snapshots(period, version_no)
    state = get_period_state(period)
    result = {
        'period': period,
        'version_no': version_no,
        'state': state,
        'input_range': input_range,
        'rules': rules,
        'summary': summary,
    }
    # 与最近一个历史封账版本比对，展示重开后更正对汇总的累计影响
    previous = (
        PeriodClosure.objects.filter(period=period, version_no__lt=version_no)
        .order_by('-version_no').first()
    )
    if previous is not None:
        result['previous_sealed_version'] = previous.version_no
        result['previous_sealed_summary'] = previous.summary_snapshot
        result['delta_vs_previous'] = _summary_diff(previous.summary_snapshot, summary)
    return result


def _summary_diff(sealed, live):
    """已封账快照与实时数据的差异（检测封账后的迟到补录影响）"""
    keys = ('stock_in_count', 'stock_in_total', 'stock_out_count', 'stock_out_total', 'net_total')
    return {k: round(float(live.get(k, 0)) - float(sealed.get(k, 0)), 2) for k in keys}


# ==================== 发起封账 ====================

def _admin_signer_ids():
    """应签署人：全部在职管理员，按ID升序确定排序"""
    ids = list(
        User.objects.filter(role__in=ADMIN_ROLES, is_active=True)
        .order_by('id').values_list('id', flat=True)
    )
    if not ids:
        raise ClosureConflictError('系统中没有可参与封账签署的管理员')
    return ids


def initiate_seal(period, user):
    """发起封账：open -> sealing，并立即冻结输入范围/规则/汇总摘要"""
    parse_period(period)
    if not getattr(user, 'is_admin', False):
        raise ClosurePermissionError('仅管理员可发起封账')

    # 同月并发发起在进程内串行（跨进程由 (period, version_no) 唯一约束兜底）
    with locked(f'closure:{period}'):
        with transaction.atomic():
            latest = (
                PeriodClosure.objects.select_for_update()
                .filter(period=period).order_by('-version_no').first()
            )
            if latest is None:
                try:
                    # 内层事务（savepoint）保证捕获唯一约束冲突后外层事务仍可提交/回滚
                    with transaction.atomic():
                        closure = PeriodClosure.objects.create(
                            period=period, version_no=1, status='open',
                            required_signer_ids=_admin_signer_ids(),
                            closed_by=user,
                        )
                except IntegrityError:
                    # 并发的另一个发起者已创建 v1（(period, version_no) 唯一约束兜底）
                    raise ClosureConflictError(f'{period} 封账正在被其他用户发起，请刷新后重试')
                closure_id = closure.id
                seal_version_no = 1
            else:
                if latest.status == 'sealing':
                    raise ClosureConflictError(f'{period} 已在签署中，请勿重复发起封账')
                if latest.status == 'sealed':
                    raise ClosureConflictError(f'{period} 已封账（第{latest.version_no}版），不能重复封账')
                if latest.status == 'reopened':
                    # 理论上重开批准会立即创建下一 open 版本；防御性处理
                    raise ClosureConflictError(f'{period} 版本状态异常，请刷新后重试')
                closure_id = latest.id
                seal_version_no = latest.version_no

            input_range, rules, summary = build_snapshots(period, seal_version_no)
            updated = PeriodClosure.objects.filter(pk=closure_id, status='open').update(
                status='sealing',
                closed_by=user,
                input_range=input_range,
                rules_snapshot=rules,
                summary_snapshot=summary,
            )
            if updated != 1:
                # 并发发起者在此处必然落空
                raise ClosureConflictError(f'{period} 封账正在被其他用户发起，请刷新后重试')

    closure = PeriodClosure.objects.get(pk=closure_id)
    return closure


# ==================== 会签 ====================

def sign_closure(closure_id, user):
    """应签署人会签；最后一名签署人落签后自动完成封账"""
    # 同一封账单的并发签署在进程内串行（跨进程由 (closure, signer) 唯一约束兜底）
    with locked(f'closure-pk:{closure_id}'):
        with transaction.atomic():
            try:
                closure = PeriodClosure.objects.select_for_update().get(pk=closure_id)
            except PeriodClosure.DoesNotExist:
                raise ClosureConflictError('封账记录不存在')

            if closure.status == 'sealed':
                raise ClosureConflictError('该版本已完成封账，无需重复签署')
            if closure.status != 'sealing':
                raise ClosureConflictError(f'当前状态为「{closure.get_status_display()}」，不能签署')

            if user.id not in closure.required_signer_ids:
                raise ClosurePermissionError('您不在该月份的封账签署人名单中')

            try:
                with transaction.atomic():
                    ClosureSignature.objects.create(
                        closure=closure,
                        signer=user,
                        signer_name=user.username,
                    )
            except IntegrityError:
                raise ClosureConflictError('您已签署过该月份封账，请勿重复签署')

            signed_ids = set(
                ClosureSignature.objects.filter(closure=closure).values_list('signer_id', flat=True)
            )
            required = set(closure.required_signer_ids)
            closure.refresh_from_db(fields=['status'])
            if required and required.issubset(signed_ids):
                # 只有集齐全部签署的这一次落签会把状态推进到 sealed
                updated = PeriodClosure.objects.filter(
                    pk=closure.pk, status='sealing'
                ).update(status='sealed', sealed_at=timezone.now())
                if updated != 1:
                    raise ClosureConflictError('封账状态已变更，请刷新后重试')

    closure = PeriodClosure.objects.get(pk=closure_id)
    return closure


# ==================== 重开申请与审批 ====================

def request_reopen(period, user, reason):
    """普通用户/管理员均可申请重开已封账月份；同期仅允许一条待审批"""
    parse_period(period)
    reason = (reason or '').strip()
    if not reason:
        raise AccountingError('请填写重开原因', code=400)

    latest = get_latest_closure(period)
    if latest is None:
        raise ClosureConflictError(f'{period} 尚未进行过封账，无需重开')
    if latest.status != 'sealed':
        raise ClosureConflictError(f'{period} 当前为「{latest.get_status_display()}」状态，不能申请重开')

    # 同月并发申请在进程内串行（跨进程由待审批部分唯一索引兜底）
    with locked(f'reopen-period:{period}'):
        try:
            # savepoint 内插入：撞部分唯一索引时回滚到保存点，外层事务保持可用
            with transaction.atomic():
                return ReopenRequest.objects.create(
                    period=period,
                    version_no=latest.version_no,
                    closure=latest,
                    applicant=user,
                    reason=reason,
                )
        except IntegrityError:
            raise ClosureConflictError(f'{period} 已存在待审批的重开申请，请勿重复提交')


def decide_reopen(request_id, approver, action, reply=''):
    """审批重开申请；批准则封存旧版本并开启新的 open 版本工作区"""
    if not getattr(approver, 'is_admin', False):
        raise ClosurePermissionError('仅管理员可审批重开申请')
    if action not in ('approved', 'rejected'):
        raise ClosureConflictError("审批动作须为 approved 或 rejected")

    # 同一申请的并发审批在进程内串行（跨进程由条件 UPDATE 影响行数兜底）
    with locked(f'reopen-req:{request_id}'):
        with transaction.atomic():
            try:
                req = ReopenRequest.objects.select_for_update().get(pk=request_id)
            except ReopenRequest.DoesNotExist:
                raise ClosureConflictError('重开申请不存在')

            if req.status != 'pending':
                raise ClosureConflictError('该申请已审批，请勿重复操作')

            # 再锁月份，串行化「批准重开建新版本」与「同月发起封账」
            with locked(f'closure:{req.period}'):
                updated = ReopenRequest.objects.filter(pk=req.pk, status='pending').update(
                    status=action,
                    approver=approver,
                    reply=(reply or '')[:2000],
                    decided_at=timezone.now(),
                )
                if updated != 1:
                    raise ClosureConflictError('审批正在被其他管理员处理，请刷新后重试')

                new_closure = None
                if action == 'approved':
                    closure = (
                        PeriodClosure.objects.select_for_update()
                        .filter(period=req.period).order_by('-version_no').first()
                    )
                    if closure is None or closure.status != 'sealed' or closure.version_no != req.version_no:
                        # 批准时封账状态已变化，确定地回滚为冲突
                        raise ClosureConflictError(
                            f'{req.period} 封账状态已变化（当前第{closure.version_no if closure else 0}版），'
                            '该重开申请不能生效'
                        )
                    PeriodClosure.objects.filter(pk=closure.pk, status='sealed').update(
                        status='reopened', reopened_at=timezone.now()
                    )
                    try:
                        with transaction.atomic():
                            new_closure = PeriodClosure.objects.create(
                                period=req.period,
                                version_no=closure.version_no + 1,
                                status='open',
                                required_signer_ids=_admin_signer_ids(),
                            )
                    except IntegrityError:
                        raise ClosureConflictError(
                            f'{req.period} 新版本已被创建，封账状态冲突，请刷新后重试'
                        )
                    # 旧版本中尚在途（待审批/已批准未完成/被驳回）的出库单迁入新版本工作区，
                    # 使其后续审批流转可以继续；已完成记录作为历史版本冻结，更正须在新版本补录
                    StockOut.objects.filter(
                        period=req.period, version_no=closure.version_no
                    ).exclude(status='completed').update(version_no=new_closure.version_no)

    req.refresh_from_db()
    return req, new_closure


# ==================== 版本化查询 ====================

def versioned_stock_in(period, version='current'):
    """按版本取入库记录；默认当前版本"""
    qs = StockIn.objects.filter(period=period).select_related('goods', 'operator')
    if version != 'all':
        target = current_version(period) if version == 'current' else int(version)
        qs = qs.filter(version_no=target)
    return qs.order_by('business_date', 'id')


def versioned_stock_out(period, version='current'):
    """按版本做出库记录；默认当前版本"""
    qs = StockOut.objects.filter(period=period).select_related('goods', 'operator')
    if version != 'all':
        target = current_version(period) if version == 'current' else int(version)
        qs = qs.filter(version_no=target)
    return qs.order_by('business_date', 'id')
