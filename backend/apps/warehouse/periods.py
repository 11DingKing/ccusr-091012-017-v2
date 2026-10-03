"""
会计期间领域服务：试算、封账、重开审批、更正版本与收发汇总。

关键约定：
- 期间按业务日期（business_date，缺省取录入日期）归属到 YYYY-MM；
- 封账期间任何对该月收发单据的直接写入一律拒绝，更正只能走"重开审批"；
- 重开获批后 current_version 加 1，期间再封账时生成新版本快照，旧版本快照永久保留、可重现；
- 所有状态迁移在事务内对期间/申请行加锁，保证重复封账、并发签署结果确定。
"""
import logging
import re
from datetime import datetime, timedelta

from django.db import transaction
from django.utils import timezone

from apps.core.exceptions import BusinessException, NotFoundException

from .models import (
    AccountingPeriod,
    Approval,
    PeriodAuditLog,
    PeriodVersion,
    ReopenRequest,
    StockIn,
    StockOut,
)

logger = logging.getLogger('apps')

PERIOD_RE = re.compile(r'^\d{4}-(0[1-9]|1[0-2])$')

# 出库计入汇总的状态：待审批/已拒绝不构成实际发出
OUT_COUNTED_STATUSES = ('approved', 'completed')

# 汇总规则。规则升级时递增 version，旧版本快照仍记录各自适用的规则口径。
SUMMARY_RULE = {
    'code': 'receipt_issue_summary',
    'version': 1,
    'in_statuses': '*',
    'out_statuses': list(OUT_COUNTED_STATUSES),
    'description': '入库记录全部计入；出库记录仅 approved/completed 计入；按业务日期归属月份',
}


# ==================== 期间工具 ====================

def normalize_period(value):
    """校验并返回 YYYY-MM 形式的期间键。"""
    value = str(value).strip()
    if not PERIOD_RE.match(value):
        raise BusinessException('会计期间格式应为 YYYY-MM')
    return value


def period_key_for_date(date):
    return date.strftime('%Y-%m')


def month_bounds(period_key):
    """返回该期间的起止日期（首日、末日）。"""
    year, month = (int(x) for x in period_key.split('-'))
    first = datetime(year, month, 1).date()
    if month == 12:
        next_first = datetime(year + 1, 1, 1).date()
    else:
        next_first = datetime(year, month + 1, 1).date()
    last = next_first - timedelta(days=1)
    return first, last


def _audit(period_key, version_no, action, detail, user):
    return PeriodAuditLog.objects.create(
        period=period_key,
        version_no=version_no,
        action=action,
        detail=detail or {},
        operator=user if user and user.is_authenticated else None,
    )


# ==================== 写保护 ====================

@transaction.atomic
def resolve_write_period(period_key, user=None):
    """写入前定位并锁定目标期间。

    返回 (period, version_no)；期间行尚不存在时视为开放中的 v1（惰性创建发生在试算/封账时）。
    封账期间抛出 403，任何角色都不能绕过——更正必须先获批重开。
    """
    period_key = normalize_period(period_key)
    period = (
        AccountingPeriod.objects
        .select_for_update()
        .filter(period=period_key)
        .first()
    )
    if period is not None and period.is_closed:
        raise BusinessException(
            f'会计期间 {period_key} 已封账，不能修改该月收发记录；如需更正请申请重开',
            code=403,
        )
    version_no = period.current_version if period is not None else 1
    return period, version_no


# ==================== 收发汇总快照 ====================

def build_snapshot(period_key):
    """按规则实时计算某期间的输入范围、规则与汇总摘要。"""
    period_key = normalize_period(period_key)
    start, end = month_bounds(period_key)

    stock_ins = list(
        StockIn.objects
        .filter(period=period_key)
        .select_related('goods')
        .order_by('id')
    )
    stock_outs = list(
        StockOut.objects
        .filter(period=period_key, status__in=OUT_COUNTED_STATUSES)
        .select_related('goods')
        .order_by('id')
    )
    excluded_out_count = StockOut.objects.filter(period=period_key).exclude(
        status__in=OUT_COUNTED_STATUSES
    ).count()

    by_goods = {}

    def _bucket(goods):
        return by_goods.setdefault(goods.id, {
            'goods': goods.id,
            'goods_code': goods.code,
            'goods_name': goods.name,
            'in_total': 0.0,
            'out_total': 0.0,
        })

    in_total = 0.0
    for rec in stock_ins:
        in_total += float(rec.quantity)
        _bucket(rec.goods)['in_total'] += float(rec.quantity)

    out_total = 0.0
    for rec in stock_outs:
        out_total += float(rec.quantity)
        _bucket(rec.goods)['out_total'] += float(rec.quantity)

    goods_rows = sorted(by_goods.values(), key=lambda row: row['goods_code'])
    for row in goods_rows:
        row['in_total'] = round(row['in_total'], 2)
        row['out_total'] = round(row['out_total'], 2)

    in_records = [{
        'id': rec.id,
        'goods': rec.goods_id,
        'goods_code': rec.goods.code,
        'goods_name': rec.goods.name,
        'quantity': float(rec.quantity),
        'batch_no': rec.batch_no,
        'supplier': rec.supplier,
        'business_date': rec.business_date.isoformat() if rec.business_date else None,
        'recorded_at': rec.stock_in_time.isoformat(),
        'operator_name': rec.operator.username if rec.operator else None,
        'remark': rec.remark,
        'version_no': rec.version_no,
    } for rec in stock_ins]

    out_records = [{
        'id': rec.id,
        'goods': rec.goods_id,
        'goods_code': rec.goods.code,
        'goods_name': rec.goods.name,
        'quantity': float(rec.quantity),
        'receiver': rec.receiver,
        'receiver_dept': rec.receiver_dept,
        'status': rec.status,
        'business_date': rec.business_date.isoformat() if rec.business_date else None,
        'stock_out_time': rec.stock_out_time.isoformat() if rec.stock_out_time else None,
        'recorded_at': rec.created_at.isoformat(),
        'operator_name': rec.operator.username if rec.operator else None,
        'remark': rec.remark,
        'version_no': rec.version_no,
    } for rec in stock_outs]

    return {
        'period': period_key,
        'generated_at': timezone.now().isoformat(),
        'rule': dict(SUMMARY_RULE),
        'input_scope': {
            'start_date': start.isoformat(),
            'end_date': end.isoformat(),
            'stock_in_ids': [rec.id for rec in stock_ins],
            'stock_out_ids': [rec.id for rec in stock_outs],
            'stock_in_count': len(stock_ins),
            'stock_out_count': len(stock_outs),
            'excluded_stock_out_count': excluded_out_count,
            'stock_in_records': in_records,
            'stock_out_records': out_records,
        },
        'summary': {
            'in_count': len(stock_ins),
            'in_total': round(in_total, 2),
            'out_count': len(stock_outs),
            'out_total': round(out_total, 2),
            'by_goods': goods_rows,
        },
    }


# ==================== 试算 / 封账 ====================

@transaction.atomic
def get_or_create_period(period_key):
    period_key = normalize_period(period_key)
    period = (
        AccountingPeriod.objects
        .select_for_update()
        .filter(period=period_key)
        .first()
    )
    if period is None:
        period = AccountingPeriod.objects.create(period=period_key)
    return period


def trial_period(period_key, user):
    """试算：固化试算快照供封账前核对，不产生版本，也不锁定业务。重复试算为幂等刷新。"""
    period_key = normalize_period(period_key)
    with transaction.atomic():
        period = get_or_create_period(period_key)
        snapshot = build_snapshot(period_key)
        period.status = 'trial'
        period.trial_snapshot = snapshot
        period.tried_at = timezone.now()
        period.tried_by = user
        period.save(update_fields=['status', 'trial_snapshot', 'tried_at', 'tried_by', 'updated_at'])
        _audit(period_key, period.current_version, 'trial', {
            'in_total': snapshot['summary']['in_total'],
            'out_total': snapshot['summary']['out_total'],
        }, user)
    logger.info("period %s trialed by %s", period_key, user.username)
    return period, snapshot


@transaction.atomic
def close_period(period_key, user):
    """封账：生成不可变版本快照。已封账时幂等返回当前版本，重复封账不会产生新版本。"""
    period_key = normalize_period(period_key)
    period = (
        AccountingPeriod.objects
        .select_for_update()
        .filter(period=period_key)
        .first()
    )
    if period is None:
        raise BusinessException('该期间尚无业务记录或未试算，不能封账')

    if period.is_closed:
        version = period.versions.filter(version_no=period.current_version).first()
        return period, version, 'already_closed'

    if period.tried_at is None:
        raise BusinessException('请先试算并核对汇总后再封账')

    snapshot = build_snapshot(period_key)
    trial_summary = (period.trial_snapshot or {}).get('summary')
    # 试算后数据若再变动（补录、补签、删除等），必须重新试算核对，保证签字数字=封账数字
    if trial_summary != snapshot['summary']:
        raise BusinessException('试算后收发数据已变化，请重新试算核对后再封账', code=409)

    version = PeriodVersion.objects.create(
        period=period,
        version_no=period.current_version,
        input_scope=snapshot['input_scope'],
        rule=snapshot['rule'],
        summary=snapshot['summary'],
        sealed_by=user,
    )
    period.status = 'closed'
    period.closed_at = timezone.now()
    period.closed_by = user
    period.save(update_fields=['status', 'closed_at', 'closed_by', 'updated_at'])

    _audit(period_key, version.version_no, 'close', {
        'version_no': version.version_no,
        'in_total': snapshot['summary']['in_total'],
        'out_total': snapshot['summary']['out_total'],
    }, user)
    logger.info("period %s closed as v%s by %s", period_key, version.version_no, user.username)
    return period, version, 'closed'


# ==================== 重开审批 ====================

@transaction.atomic
def request_reopen(period_key, user, reason):
    period_key = normalize_period(period_key)
    period = (
        AccountingPeriod.objects
        .select_for_update()
        .filter(period=period_key)
        .first()
    )
    if period is None or not period.is_closed:
        raise BusinessException('该期间尚未封账，无需重开')
    if period.reopen_requests.filter(status='pending').exists():
        raise BusinessException('该期间已有待审批的重开申请，请勿重复提交', code=409)

    request_obj = ReopenRequest.objects.create(
        period=period, requester=user, reason=reason, status='pending'
    )
    _audit(period_key, period.current_version, 'reopen_request', {
        'request_id': request_obj.id, 'reason': reason,
    }, user)
    logger.info("reopen requested for %s by %s", period_key, user.username)
    return request_obj


@transaction.atomic
def decide_reopen(request_id, approver, approved, remark):
    """审批重开：对申请行加锁，并发/重复签署时只有首次生效，其余得到确定的 409。"""
    try:
        request_obj = (
            ReopenRequest.objects
            .select_for_update()
            .select_related('period')
            .get(pk=request_id)
        )
    except ReopenRequest.DoesNotExist:
        raise NotFoundException('重开申请不存在')

    if request_obj.status != 'pending':
        raise BusinessException('该重开申请已有审批结果，以首次签署为准', code=409)

    period = AccountingPeriod.objects.select_for_update().get(pk=request_obj.period_id)

    request_obj.approver = approver
    request_obj.approve_remark = remark or ''
    request_obj.approved_at = timezone.now()

    if approved:
        if not period.is_closed:
            raise BusinessException('该期间当前已开放，无需重开')
        new_version = period.current_version + 1
        period.status = 'open'
        period.current_version = new_version
        period.closed_at = None
        period.closed_by = None
        # 更正期必须重新试算核对，旧试算结果不再代表当前数据
        period.tried_at = None
        period.tried_by = None
        period.trial_snapshot = {}
        period.save(update_fields=[
            'status', 'current_version', 'closed_at', 'closed_by',
            'tried_at', 'tried_by', 'trial_snapshot', 'updated_at',
        ])
        request_obj.status = 'approved'
        request_obj.reopened_version = new_version
        action = 'reopen_approve'
        detail = {'request_id': request_obj.id, 'new_version': new_version, 'reason': request_obj.reason}
    else:
        request_obj.status = 'rejected'
        action = 'reopen_reject'
        detail = {'request_id': request_obj.id, 'remark': remark or ''}

    request_obj.save()
    _audit(period.period, period.current_version, action, detail, approver)
    logger.info("reopen %s for %s by %s", request_obj.status, period.period, approver.username)
    return request_obj


# ==================== 出库签署 ====================

@transaction.atomic
def sign_stock_out(stock_out_id, signer, approved, remark):
    """签署出库单：仅 pending 可签署，行锁保证并发签署只有一份确定结果。"""
    try:
        stock_out = StockOut.objects.select_for_update().get(pk=stock_out_id)
    except StockOut.DoesNotExist:
        raise NotFoundException('出库记录不存在')

    if stock_out.status != 'pending':
        raise BusinessException('该出库单已签署，结果以首次签署为准', code=409)

    # 签署会改变该月汇总，封账期间同样禁止
    resolve_write_period(stock_out.period or period_key_for_date(stock_out.effective_date), signer)

    status = 'approved' if approved else 'rejected'
    stock_out.status = status
    if approved:
        stock_out.stock_out_time = timezone.now()
    stock_out.save(update_fields=['status', 'stock_out_time'])

    Approval.objects.create(
        stock_out=stock_out, approver=signer, status=status, remark=remark or ''
    )
    return stock_out


@transaction.atomic
def complete_stock_out(stock_out_id, user):
    """已批准出库单完成实际发放。"""
    try:
        stock_out = StockOut.objects.select_for_update().get(pk=stock_out_id)
    except StockOut.DoesNotExist:
        raise NotFoundException('出库记录不存在')
    if stock_out.status != 'approved':
        raise BusinessException('仅已批准的出库单可以完成发放')
    resolve_write_period(stock_out.period, user)
    stock_out.status = 'completed'
    stock_out.stock_out_time = stock_out.stock_out_time or timezone.now()
    stock_out.save(update_fields=['status', 'stock_out_time'])
    return stock_out


def get_sealed_snapshot(period_key, version_no):
    """取指定已封账版本快照；默认 None 表示由调用方回落到实时汇总。"""
    period_key = normalize_period(period_key)
    if version_no is None:
        return None
    try:
        return PeriodVersion.objects.get(period__period=period_key, version_no=version_no)
    except PeriodVersion.DoesNotExist:
        raise NotFoundException(f'期间 {period_key} 不存在版本 {version_no}')
