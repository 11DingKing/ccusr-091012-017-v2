"""
库房管理模型
"""
from django.db import models
from apps.authentication.models import User


class Unit(models.Model):
    """单位模型"""
    name = models.CharField('单位名称', max_length=5, unique=True)
    created_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True,
        related_name='created_units', verbose_name='创建人'
    )
    is_active = models.BooleanField('是否启用', default=True)
    created_at = models.DateTimeField('创建时间', auto_now_add=True)
    updated_at = models.DateTimeField('更新时间', auto_now=True)
    
    class Meta:
        db_table = 'wh_unit'
        verbose_name = '单位'
        verbose_name_plural = verbose_name
        ordering = ['-created_at']
    
    def __str__(self):
        return self.name
    
    @property
    def is_linked(self):
        """是否已关联至品类"""
        return self.categories.exists()


class Category(models.Model):
    """品类模型"""
    name = models.CharField('品类名称', max_length=10, unique=True)
    unit = models.ForeignKey(
        Unit, on_delete=models.PROTECT,
        related_name='categories', verbose_name='单位'
    )
    created_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True,
        related_name='created_categories', verbose_name='创建人'
    )
    is_active = models.BooleanField('是否启用', default=True)
    created_at = models.DateTimeField('创建时间', auto_now_add=True)
    updated_at = models.DateTimeField('更新时间', auto_now=True)
    
    class Meta:
        db_table = 'wh_category'
        verbose_name = '品类'
        verbose_name_plural = verbose_name
        ordering = ['-created_at']
    
    def __str__(self):
        return self.name
    
    @property
    def is_linked(self):
        """是否已关联至品种"""
        return self.varieties.exists()


class Variety(models.Model):
    """品种模型"""
    name = models.CharField('品种名称', max_length=20)
    category = models.ForeignKey(
        Category, on_delete=models.PROTECT,
        related_name='varieties', verbose_name='所属品类'
    )
    created_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True,
        related_name='created_varieties', verbose_name='创建人'
    )
    is_active = models.BooleanField('是否启用', default=True)
    created_at = models.DateTimeField('创建时间', auto_now_add=True)
    updated_at = models.DateTimeField('更新时间', auto_now=True)
    
    class Meta:
        db_table = 'wh_variety'
        verbose_name = '品种'
        verbose_name_plural = verbose_name
        ordering = ['-created_at']
        unique_together = ['category', 'name']
    
    def __str__(self):
        return f"{self.category.name} - {self.name}"
    
    @property
    def is_in_stock(self):
        """是否已入库"""
        return self.goods.exists()
    
    @property
    def unit_name(self):
        """获取单位名称"""
        return self.category.unit.name if self.category and self.category.unit else ''


class Goods(models.Model):
    """货物模型"""
    variety = models.ForeignKey(
        Variety, on_delete=models.CASCADE,
        related_name='goods', verbose_name='所属品种'
    )
    name = models.CharField('货物名称', max_length=200)
    code = models.CharField('货物编码', max_length=50, unique=True)
    specification = models.CharField('规格型号', max_length=200, blank=True)
    quantity = models.DecimalField('库存数量', max_digits=12, decimal_places=2, default=0)
    warning_threshold = models.DecimalField('预警阈值', max_digits=12, decimal_places=2, default=10)
    location = models.CharField('存放位置', max_length=100, blank=True)
    remark = models.TextField('备注', blank=True)
    is_active = models.BooleanField('是否启用', default=True)
    created_at = models.DateTimeField('创建时间', auto_now_add=True)
    updated_at = models.DateTimeField('更新时间', auto_now=True)
    
    class Meta:
        db_table = 'wh_goods'
        verbose_name = '货物'
        verbose_name_plural = verbose_name
        ordering = ['-created_at']
    
    def __str__(self):
        return self.name
    
    @property
    def is_warning(self):
        """是否预警"""
        return self.quantity <= self.warning_threshold


class StockIn(models.Model):
    """入库记录模型"""
    goods = models.ForeignKey(
        Goods, on_delete=models.CASCADE,
        related_name='stock_ins', verbose_name='货物'
    )
    operator = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True,
        related_name='stock_in_operations', verbose_name='操作人'
    )
    quantity = models.DecimalField('入库数量', max_digits=12, decimal_places=2)
    batch_no = models.CharField('批次号', max_length=50, blank=True)
    supplier = models.CharField('供应商', max_length=200, blank=True)
    business_date = models.DateField('业务日期', default=None, null=True, blank=True,
                                     help_text='收发业务实际发生日期，决定所属会计期间；缺省取录入时间')
    stock_in_time = models.DateTimeField('入库时间', auto_now_add=True)
    remark = models.TextField('备注', blank=True)
    # 期间版本：单据落账的会计期间与所属版本（版本在重开更正后递增）
    period = models.CharField('所属期间', max_length=7, blank=True, default='', db_index=True)
    version_no = models.PositiveIntegerField('所属版本', default=1)

    class Meta:
        db_table = 'wh_stock_in'
        verbose_name = '入库记录'
        verbose_name_plural = verbose_name
        ordering = ['-stock_in_time']

    def __str__(self):
        return f"{self.goods.name} - {self.quantity}"

    @property
    def effective_date(self):
        """归属期间所用的业务日期（补录时为业务日期，否则为录入日期）"""
        return self.business_date or self.stock_in_time.date()


class StockOut(models.Model):
    """出库记录模型"""
    STATUS_CHOICES = [
        ('pending', '待审批'),
        ('approved', '已通过'),
        ('rejected', '已拒绝'),
        ('completed', '已完成'),
    ]
    
    goods = models.ForeignKey(
        Goods, on_delete=models.CASCADE,
        related_name='stock_outs', verbose_name='货物'
    )
    operator = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True,
        related_name='stock_out_operations', verbose_name='操作人'
    )
    receiver = models.CharField('领用人', max_length=100)
    receiver_dept = models.CharField('领用部门', max_length=100, blank=True)
    quantity = models.DecimalField('出库数量', max_digits=12, decimal_places=2)
    status = models.CharField('状态', max_length=20, choices=STATUS_CHOICES, default='pending')
    business_date = models.DateField('业务日期', default=None, null=True, blank=True,
                                     help_text='收发业务实际发生日期，决定所属会计期间；缺省取创建时间')
    stock_out_time = models.DateTimeField('出库时间', null=True, blank=True)
    remark = models.TextField('备注', blank=True)
    created_at = models.DateTimeField('创建时间', auto_now_add=True)
    # 期间版本：单据落账的会计期间与所属版本（版本在重开更正后递增）
    period = models.CharField('所属期间', max_length=7, blank=True, default='', db_index=True)
    version_no = models.PositiveIntegerField('所属版本', default=1)

    class Meta:
        db_table = 'wh_stock_out'
        verbose_name = '出库记录'
        verbose_name_plural = verbose_name
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.goods.name} - {self.quantity}"

    @property
    def effective_date(self):
        """归属期间所用的业务日期（补录时为业务日期，否则为创建日期）"""
        return self.business_date or self.created_at.date()


class Warning(models.Model):
    """预警记录模型"""
    TYPE_CHOICES = [
        ('low_stock', '库存不足'),
        ('expiring', '即将过期'),
        ('expired', '已过期'),
    ]
    
    goods = models.ForeignKey(
        Goods, on_delete=models.CASCADE,
        related_name='warnings', verbose_name='货物'
    )
    type = models.CharField('预警类型', max_length=20, choices=TYPE_CHOICES)
    message = models.TextField('预警信息')
    is_read = models.BooleanField('是否已读', default=False)
    created_at = models.DateTimeField('创建时间', auto_now_add=True)
    
    class Meta:
        db_table = 'wh_warning'
        verbose_name = '预警记录'
        verbose_name_plural = verbose_name
        ordering = ['-created_at']
    
    def __str__(self):
        return f"{self.goods.name} - {self.get_type_display()}"


class Approval(models.Model):
    """审批记录模型"""
    STATUS_CHOICES = [
        ('pending', '待审批'),
        ('approved', '已通过'),
        ('rejected', '已拒绝'),
    ]
    
    stock_out = models.ForeignKey(
        StockOut, on_delete=models.CASCADE,
        related_name='approvals', verbose_name='出库记录'
    )
    approver = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True,
        related_name='approvals', verbose_name='审批人'
    )
    status = models.CharField('审批状态', max_length=20, choices=STATUS_CHOICES, default='pending')
    remark = models.TextField('审批意见', blank=True)
    created_at = models.DateTimeField('创建时间', auto_now_add=True)
    updated_at = models.DateTimeField('更新时间', auto_now=True)
    
    class Meta:
        db_table = 'wh_approval'
        verbose_name = '审批记录'
        verbose_name_plural = verbose_name
        ordering = ['-created_at']
    
    def __str__(self):
        return f"{self.stock_out} - {self.get_status_display()}"


# ==================== 会计期间 / 封账与版本 ====================

class AccountingPeriod(models.Model):
    """会计期间（按月）。

    状态机：open（开放）→ trial（试算）→ closed（已封账）；
    closed 经重开审批通过后回到 open，期间内变更沉淀为新的版本（current_version + 1）。
    每个期间最多一行记录，重复试算/封账为幂等操作。
    """
    STATUS_CHOICES = [
        ('open', '开放中'),
        ('trial', '试算中'),
        ('closed', '已封账'),
    ]

    period = models.CharField('会计期间', max_length=7, unique=True, help_text='格式 YYYY-MM')
    status = models.CharField('状态', max_length=10, choices=STATUS_CHOICES, default='open')
    current_version = models.PositiveIntegerField('当前版本号', default=1)
    # 最新一次试算结果（不落版本，仅作封账前核对）
    trial_snapshot = models.JSONField('试算快照', default=dict, blank=True)
    tried_at = models.DateTimeField('最近试算时间', null=True, blank=True)
    tried_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='trial_periods', verbose_name='试算人'
    )
    closed_at = models.DateTimeField('封账时间', null=True, blank=True)
    closed_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='closed_periods', verbose_name='封账人'
    )
    created_at = models.DateTimeField('创建时间', auto_now_add=True)
    updated_at = models.DateTimeField('更新时间', auto_now=True)

    class Meta:
        db_table = 'wh_accounting_period'
        verbose_name = '会计期间'
        verbose_name_plural = verbose_name
        ordering = ['-period']

    def __str__(self):
        return f"{self.period}（{self.get_status_display()} v{self.current_version}）"

    @property
    def is_closed(self):
        return self.status == 'closed'


class PeriodVersion(models.Model):
    """期间封账版本快照。

    每次封账生成一个版本（首次封账为 v1，重开后再次封账递增）。
    快照固化输入范围、汇总规则版本与汇总摘要，使历史复核结果可重现。
    """
    period = models.ForeignKey(
        AccountingPeriod, on_delete=models.PROTECT,
        related_name='versions', verbose_name='会计期间'
    )
    version_no = models.PositiveIntegerField('版本号')
    # 输入范围：纳入汇总的单据类型、状态、业务日期起止、记录主键集合
    input_scope = models.JSONField('输入范围', default=dict)
    # 汇总规则：规则标识、版本、口径说明（规则升级后旧版本仍按旧口径解释）
    rule = models.JSONField('汇总规则', default=dict)
    # 汇总摘要：收发笔数/数量合计、按货物明细及生成时间
    summary = models.JSONField('汇总摘要', default=dict)
    sealed_at = models.DateTimeField('封账时间', auto_now_add=True)
    sealed_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True,
        related_name='sealed_versions', verbose_name='封账人'
    )

    class Meta:
        db_table = 'wh_period_version'
        verbose_name = '期间版本'
        verbose_name_plural = verbose_name
        unique_together = ['period', 'version_no']
        ordering = ['period_id', '-version_no']

    def __str__(self):
        return f"{self.period.period} v{self.version_no}"


class ReopenRequest(models.Model):
    """封账期间重开申请与审批。

    closed 状态的期间只能通过获批重开修改；获批后期间回到 open、版本递增，
    此后所有变更进入新版本；审批结果确定（重复提交/并发审批幂等）。
    """
    STATUS_CHOICES = [
        ('pending', '待审批'),
        ('approved', '已通过'),
        ('rejected', '已拒绝'),
    ]

    period = models.ForeignKey(
        AccountingPeriod, on_delete=models.PROTECT,
        related_name='reopen_requests', verbose_name='会计期间'
    )
    requester = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True,
        related_name='reopen_requests', verbose_name='申请人'
    )
    reason = models.TextField('重开原因')
    status = models.CharField('状态', max_length=20, choices=STATUS_CHOICES, default='pending')
    approver = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='approved_reopens', verbose_name='审批人'
    )
    approve_remark = models.TextField('审批意见', blank=True)
    # 获批时开启的新版本号，便于审计"哪次重开产生了哪个版本"
    reopened_version = models.PositiveIntegerField('重开后版本号', null=True, blank=True)
    approved_at = models.DateTimeField('审批时间', null=True, blank=True)
    created_at = models.DateTimeField('创建时间', auto_now_add=True)
    updated_at = models.DateTimeField('更新时间', auto_now=True)

    class Meta:
        db_table = 'wh_reopen_request'
        verbose_name = '重开审批'
        verbose_name_plural = verbose_name
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.period.period} 重开申请-{self.get_status_display()}"


class PeriodAuditLog(models.Model):
    """期间敏感操作审计：试算、封账、重开申请/审批、更正期变更。"""
    ACTION_CHOICES = [
        ('trial', '试算'),
        ('close', '封账'),
        ('reopen_request', '申请重开'),
        ('reopen_approve', '批准重开'),
        ('reopen_reject', '拒绝重开'),
        ('record_change', '更正期变更'),
    ]

    period = models.CharField('会计期间', max_length=7, db_index=True)
    version_no = models.PositiveIntegerField('发生时版本号', default=1)
    action = models.CharField('操作类型', max_length=20, choices=ACTION_CHOICES)
    detail = models.JSONField('操作详情', default=dict, blank=True)
    operator = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True,
        related_name='period_audit_logs', verbose_name='操作人'
    )
    created_at = models.DateTimeField('操作时间', auto_now_add=True)

    class Meta:
        db_table = 'wh_period_audit_log'
        verbose_name = '期间审计日志'
        verbose_name_plural = verbose_name
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.period} v{self.version_no} {self.get_action_display()}"
