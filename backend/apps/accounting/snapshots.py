"""
封账快照：输入范围、规则、汇总摘要

封账时按「月份 + 当前工作版本」一次性计算并冻结：
- 重开获批后旧版本行保持不变，迟到补录/更正全部进入新版本；
- 因此历史快照不会随后续补录改变，下一次复核可原样重现当时结果。
"""
from decimal import Decimal

from django.db.models import Count, Sum

from apps.warehouse.models import StockIn, StockOut

RULES_SNAPSHOT = {
    'rule_version': '1.0',
    'timezone': 'Asia/Shanghai',
    'in_scope': "StockIn 按业务日期归属月份、同属当前封账版本的全部记录计入",
    'out_scope': "StockOut 按业务日期归属月份、同属当前封账版本且 status='completed' 计入",
    'period_basis': 'business_date（业务日期），迟到补录按实际业务发生日归月',
    'versioning': '重开获批后变更只进入新版本，历史版本快照永久冻结',
    'net_formula': '入库总量 - 出库总量',
}


def _d(value):
    """Decimal 安全转 float（None -> 0.0）"""
    return float(value) if value is not None else 0.0


def build_input_range(period, version_no):
    """输入范围：该版本收发记录的ID清单与条数（封账时刻冻结）"""
    in_qs = StockIn.objects.filter(period=period, version_no=version_no)
    out_qs = StockOut.objects.filter(
        period=period, version_no=version_no, status='completed'
    )
    return {
        'period': period,
        'version_no': version_no,
        'period_start': f'{period}-01',
        'period_end': f'{period}-月末',
        'stock_in_ids': sorted(in_qs.values_list('id', flat=True)),
        'stock_out_ids': sorted(out_qs.values_list('id', flat=True)),
        'stock_in_count': in_qs.count(),
        'stock_out_count': out_qs.count(),
    }


def build_summary(period, version_no):
    """汇总摘要：按版本的收发笔数、总量与净额"""
    in_agg = StockIn.objects.filter(
        period=period, version_no=version_no
    ).aggregate(count=Count('id'), total=Sum('quantity'))
    out_agg = StockOut.objects.filter(
        period=period, version_no=version_no, status='completed'
    ).aggregate(count=Count('id'), total=Sum('quantity'))

    in_total = in_agg['total'] or Decimal('0')
    out_total = out_agg['total'] or Decimal('0')
    return {
        'period': period,
        'version_no': version_no,
        'stock_in_count': in_agg['count'] or 0,
        'stock_in_total': _d(in_total),
        'stock_out_count': out_agg['count'] or 0,
        'stock_out_total': _d(out_total),
        'net_total': _d(in_total - out_total),
    }


def build_snapshots(period, version_no):
    """返回 (input_range, rules, summary) 三元组"""
    return build_input_range(period, version_no), dict(RULES_SNAPSHOT), build_summary(period, version_no)


def trial_balance(period, version_no):
    """试算平衡：不落库，返回指定版本的实时汇总与范围（封账前预览）"""
    input_range, rules, summary = build_snapshots(period, version_no)
    return {
        'period': period,
        'version_no': version_no,
        'input_range': input_range,
        'rules': rules,
        'summary': summary,
    }
