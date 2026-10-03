"""
可由管理命令或进程内调度器调用的周期任务。
"""
import logging
from datetime import datetime, timedelta
from django.db.models import F
from apps.warehouse.models import Goods, Warning
from .aggregation import in_totals_on, out_totals_on

logger = logging.getLogger('apps')


def check_stock_warning():
    """
    检查库存预警 - 定时任务
    每天凌晨1点执行，检查库存低于预警阈值的货物
    """
    logger.info("开始执行库存预警检查定时任务...")
    
    # 查找库存低于预警阈值的货物
    warning_goods = Goods.objects.filter(
        is_active=True,
        quantity__lte=F('warning_threshold')
    )
    
    created_count = 0
    for goods in warning_goods:
        # 检查是否已有未读的预警记录
        existing_warning = Warning.objects.filter(
            goods=goods,
            type='low_stock',
            is_read=False
        ).exists()
        
        if not existing_warning:
            Warning.objects.create(
                goods=goods,
                type='low_stock',
                message=f'货物 {goods.name}（{goods.code}）库存不足，当前库存: {goods.quantity}，预警阈值: {goods.warning_threshold}'
            )
            created_count += 1
            logger.info(f"创建库存预警: {goods.name}")
    
    logger.info(f"库存预警检查完成，新增预警记录: {created_count} 条")


def generate_daily_report():
    """
    生成每日报表 - 定时任务
    每天凌晨2点执行，生成前一天的报表数据
    """
    from .models import DailyReport
    
    logger.info("开始执行每日报表生成定时任务...")
    
    yesterday = (datetime.now() - timedelta(days=1)).date()
    
    # 检查是否已生成
    if DailyReport.objects.filter(report_date=yesterday).exists():
        logger.info(f"{yesterday} 的报表已存在，跳过生成")
        return
    
    # 收发统计按业务日期归属，跨月补录计入实际发生日
    in_data = in_totals_on(yesterday)
    out_data = out_totals_on(yesterday, only_completed=True)

    # 预警统计
    warning_count = Warning.objects.filter(
        created_at__date=yesterday
    ).count()
    
    # 创建报表记录
    DailyReport.objects.create(
        report_date=yesterday,
        in_count=in_data['count'] or 0,
        in_total=in_data['total'] or 0,
        out_count=out_data['count'] or 0,
        out_total=out_data['total'] or 0,
        warning_count=warning_count
    )
    
    logger.info(f"每日报表生成完成: {yesterday}")


def clean_old_logs():
    """
    清理旧日志 - 定时任务
    每周日凌晨3点执行，清理30天前的操作日志
    """
    from apps.authentication.models import OperationLog
    
    logger.info("开始执行日志清理定时任务...")
    
    threshold_date = datetime.now() - timedelta(days=30)
    deleted_count, _ = OperationLog.objects.filter(
        created_at__lt=threshold_date
    ).delete()
    
    logger.info(f"日志清理完成，删除记录: {deleted_count} 条")
