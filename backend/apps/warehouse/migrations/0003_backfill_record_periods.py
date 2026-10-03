"""回填历史收发单据的所属期间与版本号"""
from django.db import migrations


def backfill_periods(apps, schema_editor):
    StockIn = apps.get_model('warehouse', 'StockIn')
    StockOut = apps.get_model('warehouse', 'StockOut')

    for record in StockIn.objects.filter(period=''):
        base = record.business_date or record.stock_in_time.date()
        record.period = base.strftime('%Y-%m')
        record.version_no = 1
        record.save(update_fields=['period', 'version_no'])

    for record in StockOut.objects.filter(period=''):
        base = record.business_date or record.created_at.date()
        record.period = base.strftime('%Y-%m')
        record.version_no = 1
        record.save(update_fields=['period', 'version_no'])


def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('warehouse', '0002_accountingperiod_stockin_business_date_and_more'),
    ]

    operations = [
        migrations.RunPython(backfill_periods, noop),
    ]
