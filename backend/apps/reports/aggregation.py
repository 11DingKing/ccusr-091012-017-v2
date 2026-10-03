"""
收发按业务日期聚合助手。

所有日报/月报口径统一按"业务日期"归属（business_date，缺省取录入/创建日期），
保证月底后跨月补录仍计入实际发生的那一天、那一月，与会计期间口径一致。
"""
from django.db.models import Count, Sum
from django.db.models.functions import Coalesce, TruncDate

from apps.warehouse.models import StockIn, StockOut

# 出库计入汇总的状态，与封账规则保持一致
OUT_COUNTED_STATUSES = ('approved', 'completed')


def stock_in_on(date_value):
    return StockIn.objects.annotate(
        effective_date=Coalesce('business_date', TruncDate('stock_in_time'))
    ).filter(effective_date=date_value)


def stock_out_on(date_value, only_completed=True):
    qs = StockOut.objects.annotate(
        effective_date=Coalesce('business_date', TruncDate('created_at'))
    ).filter(effective_date=date_value)
    if only_completed:
        qs = qs.filter(status='completed')
    else:
        qs = qs.filter(status__in=OUT_COUNTED_STATUSES)
    return qs


def in_totals_on(date_value):
    return stock_in_on(date_value).aggregate(count=Count('id'), total=Sum('quantity'))


def out_totals_on(date_value, only_completed=True):
    return stock_out_on(date_value, only_completed=only_completed).aggregate(
        count=Count('id'), total=Sum('quantity')
    )
