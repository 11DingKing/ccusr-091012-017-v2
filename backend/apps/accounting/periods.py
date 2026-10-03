"""月结期间工具：YYYY-MM 归属月"""
import re
from datetime import date, datetime

PERIOD_RE = re.compile(r'^\d{4}-(0[1-9]|1[0-2])$')


def period_of(value):
    """取日期/时间所属月份，返回 'YYYY-MM'"""
    if isinstance(value, datetime):
        return value.strftime('%Y-%m')
    if isinstance(value, date):
        return value.strftime('%Y-%m')
    raise TypeError('period_of 需要 date 或 datetime')


def parse_period(value):
    """校验 'YYYY-MM'，非法时抛 ValueError"""
    if not value or not PERIOD_RE.match(value):
        raise ValueError('月份格式应为 YYYY-MM')
    return value
