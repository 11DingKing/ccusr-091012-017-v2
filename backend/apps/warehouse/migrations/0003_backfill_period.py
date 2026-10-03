"""回填历史收发记录的业务日期与归属月份"""
from django.db import migrations
from django.utils import timezone


def backfill_stockin(apps, schema_editor):
    StockIn = apps.get_model('warehouse', 'StockIn')
    for row in StockIn.objects.all():
        base = row.stock_in_time
        if timezone.is_aware(base):
            base = timezone.localtime(base)
        row.business_date = base.date()
        row.period = base.strftime('%Y-%m')
        row.save(update_fields=['business_date', 'period'])


def backfill_stockout(apps, schema_editor):
    StockOut = apps.get_model('warehouse', 'StockOut')
    for row in StockOut.objects.all():
        # 出库完成时间可能为空（仍在审批中），回退到创建时间
        base = row.stock_out_time or row.created_at
        if base is not None and timezone.is_aware(base):
            base = timezone.localtime(base)
        row.business_date = base.date()
        row.period = base.strftime('%Y-%m')
        row.save(update_fields=['business_date', 'period'])


def noop_reverse(apps, schema_editor):
    # 新字段随 0002 回滚，无需逆向回填
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('warehouse', '0002_stockin_business_date_stockin_period_and_more'),
    ]

    operations = [
        migrations.RunPython(backfill_stockin, noop_reverse),
        migrations.RunPython(backfill_stockout, noop_reverse),
    ]
