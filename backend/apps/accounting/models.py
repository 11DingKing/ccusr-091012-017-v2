"""
月结封账模型

封账以「月份 + 版本号」为不可变单元：
- sealing  签署中（已发起会签，期间同样禁止修改敏感记录）
- sealed   已封账（快照冻结，下一次复核可原样重现）
- reopened 已获批重开（生成新版本工作区，变更只进入新版本）
"""
from django.conf import settings
from django.db import models


class PeriodClosure(models.Model):
    """月份封账记录（每个版本一行）"""

    STATUS_CHOICES = [
        ('sealing', '签署中'),
        ('sealed', '已封账'),
        ('reopened', '已重开'),
    ]

    period = models.CharField('所属月份', max_length=7, db_index=True)
    version_no = models.PositiveIntegerField('版本号', default=1)
    status = models.CharField('状态', max_length=20, choices=STATUS_CHOICES, default='sealing')

    # 封账时冻结的三类快照：输入范围、规则、汇总摘要
    input_range = models.JSONField('输入范围快照', default=dict)
    rules_snapshot = models.JSONField('规则快照', default=dict)
    summary_snapshot = models.JSONField('汇总摘要快照', default=dict)

    # 发起封账时所需签署人名单的快照（按用户ID升序，保证结果确定）
    required_signer_ids = models.JSONField('应签署人ID列表', default=list)

    closed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True,
        related_name='closed_periods', verbose_name='封账发起人'
    )
    sealed_at = models.DateTimeField('封账完成时间', null=True, blank=True)
    reopened_at = models.DateTimeField('重开时间', null=True, blank=True)
    created_at = models.DateTimeField('创建时间', auto_now_add=True)
    updated_at = models.DateTimeField('更新时间', auto_now=True)

    class Meta:
        db_table = 'ac_period_closure'
        verbose_name = '月份封账'
        verbose_name_plural = verbose_name
        ordering = ['-period', '-version_no']
        constraints = [
            models.UniqueConstraint(
                fields=['period', 'version_no'],
                name='uniq_closure_period_version',
            ),
        ]

    def __str__(self):
        return f'{self.period} 第{self.version_no}版 - {self.get_status_display()}'

    @property
    def is_locked(self):
        """签署中/已封账均禁止修改所属月份的敏感记录"""
        return self.status in ('sealing', 'sealed')


class ClosureSignature(models.Model):
    """封账会签记录"""

    closure = models.ForeignKey(
        PeriodClosure, on_delete=models.CASCADE,
        related_name='signatures', verbose_name='封账版本'
    )
    signer = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True,
        related_name='closure_signatures', verbose_name='签署人'
    )
    signer_name = models.CharField('签署人用户名', max_length=50)
    signed_at = models.DateTimeField('签署时间', auto_now_add=True)

    class Meta:
        db_table = 'ac_closure_signature'
        verbose_name = '封账签署'
        verbose_name_plural = verbose_name
        ordering = ['signed_at']
        constraints = [
            models.UniqueConstraint(
                fields=['closure', 'signer'],
                name='uniq_signature_closure_signer',
            ),
        ]

    def __str__(self):
        return f'{self.closure.period} v{self.closure.version_no} - {self.signer_name}'


class ReopenRequest(models.Model):
    """封账重开申请与审批"""

    STATUS_CHOICES = [
        ('pending', '待审批'),
        ('approved', '已批准'),
        ('rejected', '已拒绝'),
    ]

    period = models.CharField('所属月份', max_length=7, db_index=True)
    version_no = models.PositiveIntegerField('申请时封账版本号', default=1)
    closure = models.ForeignKey(
        PeriodClosure, on_delete=models.SET_NULL, null=True,
        related_name='reopen_requests', verbose_name='对应封账'
    )
    applicant = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True,
        related_name='reopen_requests', verbose_name='申请人'
    )
    reason = models.TextField('重开原因')
    status = models.CharField('状态', max_length=20, choices=STATUS_CHOICES, default='pending')
    approver = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='approved_reopen_requests', verbose_name='审批人'
    )
    reply = models.TextField('审批意见', blank=True)
    decided_at = models.DateTimeField('审批时间', null=True, blank=True)
    created_at = models.DateTimeField('创建时间', auto_now_add=True)
    updated_at = models.DateTimeField('更新时间', auto_now=True)

    class Meta:
        db_table = 'ac_reopen_request'
        verbose_name = '封账重开申请'
        verbose_name_plural = verbose_name
        ordering = ['-created_at']
        constraints = [
            # 同一月份同时只允许存在一条待审批申请
            models.UniqueConstraint(
                fields=['period'],
                condition=models.Q(status='pending'),
                name='uniq_pending_reopen_per_period',
            ),
        ]

    def __str__(self):
        return f'{self.period} 重开申请 - {self.get_status_display()}'
