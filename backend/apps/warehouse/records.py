"""
收发单据写入服务：统一期间归属与封账写保护。

所有对入库/出库单据（敏感记录）的新增、修改、删除都必须经过本模块：
先按业务日期确定所属期间，再校验期间未封账，并把期间与版本号落到单据上。
"""
import logging

from django.db import transaction
from django.utils import timezone

from apps.core.exceptions import BusinessException, NotFoundException

from .models import AccountingPeriod, Goods, PeriodAuditLog, StockIn, StockOut
from .periods import period_key_for_date, resolve_write_period

logger = logging.getLogger('apps')


def _log_change(period_key, version_no, kind, detail, user):
    """把收发单据变更记入期间审计（更正期内变更尤其依赖此痕迹）。"""
    PeriodAuditLog.objects.create(
        period=period_key,
        version_no=version_no,
        action='record_change',
        detail={'kind': kind, **detail},
        operator=user if user and user.is_authenticated else None,
    )


def _resolve_goods(goods_id):
    try:
        return Goods.objects.get(pk=goods_id, is_active=True)
    except Goods.DoesNotExist:
        raise NotFoundException('货物不存在或已停用')


def _period_key(business_date, fallback_dt):
    return period_key_for_date(business_date or fallback_dt.date())


@transaction.atomic
def create_stock_in(user, *, goods_id, quantity, business_date=None,
                    batch_no='', supplier='', remark=''):
    goods = _resolve_goods(goods_id)
    period_key = _period_key(business_date, timezone.now())
    _, version_no = resolve_write_period(period_key, user)
    record = StockIn.objects.create(
        goods=goods, operator=user, quantity=quantity,
        business_date=business_date, batch_no=batch_no or '',
        supplier=supplier or '', remark=remark or '',
        period=period_key, version_no=version_no,
    )
    _log_change(period_key, version_no, 'stock_in_create', {
        'record_id': record.id, 'goods': goods.id, 'quantity': float(quantity),
        'business_date': business_date.isoformat() if business_date else None,
    }, user)
    logger.info("stock-in %s created for period %s v%s by %s",
                record.id, period_key, version_no, user.username)
    return record


@transaction.atomic
def create_stock_out(user, *, goods_id, quantity, receiver, receiver_dept='',
                     business_date=None, remark=''):
    goods = _resolve_goods(goods_id)
    period_key = _period_key(business_date, timezone.now())
    _, version_no = resolve_write_period(period_key, user)
    record = StockOut.objects.create(
        goods=goods, operator=user, quantity=quantity,
        receiver=receiver, receiver_dept=receiver_dept or '',
        business_date=business_date, remark=remark or '',
        period=period_key, version_no=version_no,
    )
    _log_change(period_key, version_no, 'stock_out_create', {
        'record_id': record.id, 'goods': goods.id, 'quantity': float(quantity),
        'receiver': receiver,
        'business_date': business_date.isoformat() if business_date else None,
    }, user)
    logger.info("stock-out %s created for period %s v%s by %s",
                record.id, period_key, version_no, user.username)
    return record


def _guard_record(record, user, new_business_date):
    """修改/删除前校验：单据当前期间与目标期间都必须处于开放状态。"""
    resolve_write_period(record.period, user)
    if new_business_date is not None:
        target_key = period_key_for_date(new_business_date)
        if target_key != record.period:
            resolve_write_period(target_key, user)
            return target_key
    return record.period


@transaction.atomic
def update_stock_in(user, record_id, *, quantity=None, business_date=None,
                    batch_no=None, supplier=None, remark=None):
    try:
        record = StockIn.objects.select_for_update().get(pk=record_id)
    except StockIn.DoesNotExist:
        raise NotFoundException('入库记录不存在')

    # business_date 未提供时保持原值；显式置空则回落到录入日期
    new_business_date = business_date  # None 表示调用方未涉及该字段
    target_period = _guard_record(record, user, new_business_date)
    old_period, old_quantity = record.period, record.quantity

    if quantity is not None:
        record.quantity = quantity
    if business_date is not None:
        record.business_date = business_date
    if batch_no is not None:
        record.batch_no = batch_no
    if supplier is not None:
        record.supplier = supplier
    if remark is not None:
        record.remark = remark
    record.period = target_period
    record.version_no = _current_version(target_period)
    record.save()
    _log_change(target_period, record.version_no, 'stock_in_update', {
        'record_id': record.id, 'old_period': old_period,
        'period_moved': old_period != target_period,
        'old_quantity': float(old_quantity), 'new_quantity': float(record.quantity),
    }, user)
    return record


@transaction.atomic
def update_stock_out(user, record_id, *, quantity=None, receiver=None,
                     receiver_dept=None, business_date=None, remark=None):
    try:
        record = StockOut.objects.select_for_update().get(pk=record_id)
    except StockOut.DoesNotExist:
        raise NotFoundException('出库记录不存在')
    if record.status != 'pending':
        raise BusinessException('已审批的出库单不能直接修改，请通过更正流程处理')

    target_period = _guard_record(record, user, business_date)
    old_period, old_quantity = record.period, record.quantity

    if quantity is not None:
        record.quantity = quantity
    if receiver is not None:
        record.receiver = receiver
    if receiver_dept is not None:
        record.receiver_dept = receiver_dept
    if business_date is not None:
        record.business_date = business_date
    if remark is not None:
        record.remark = remark
    record.period = target_period
    record.version_no = _current_version(target_period)
    record.save()
    _log_change(target_period, record.version_no, 'stock_out_update', {
        'record_id': record.id, 'old_period': old_period,
        'period_moved': old_period != target_period,
        'old_quantity': float(old_quantity), 'new_quantity': float(record.quantity),
    }, user)
    return record


@transaction.atomic
def delete_stock_in(user, record_id):
    try:
        record = StockIn.objects.select_for_update().get(pk=record_id)
    except StockIn.DoesNotExist:
        raise NotFoundException('入库记录不存在')
    resolve_write_period(record.period, user)
    record_id, period, record_version = record.id, record.period, record.version_no
    record.delete()
    _log_change(period, record_version, 'stock_in_delete', {
        'record_id': record_id,
    }, user)
    logger.info("stock-in %s deleted from %s by %s", record_id, period, user.username)


@transaction.atomic
def delete_stock_out(user, record_id):
    try:
        record = StockOut.objects.select_for_update().get(pk=record_id)
    except StockOut.DoesNotExist:
        raise NotFoundException('出库记录不存在')
    if record.status != 'pending':
        raise BusinessException('已审批的出库单不能删除')
    resolve_write_period(record.period, user)
    record_id, period = record.id, record.period
    record.delete()
    _log_change(period, _current_version(period), 'stock_out_delete', {
        'record_id': record_id,
    }, user)
    logger.info("stock-out %s deleted from %s by %s", record_id, period, user.username)


def _current_version(period_key):
    period = AccountingPeriod.objects.filter(period=period_key).first()
    return period.current_version if period else 1
