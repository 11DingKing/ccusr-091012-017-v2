"""
仓库管理序列化器
"""
from decimal import Decimal

from rest_framework import serializers
from .models import (
    Unit, Category, Variety, Goods, StockIn, StockOut, Warning, Approval,
    AccountingPeriod, PeriodVersion, ReopenRequest,
)


class UnitSerializer(serializers.ModelSerializer):
    """单位序列化器"""
    is_linked = serializers.BooleanField(read_only=True)
    created_by_name = serializers.CharField(source='created_by.username', read_only=True)
    
    class Meta:
        model = Unit
        fields = [
            'id', 'name', 'is_linked', 'is_active',
            'created_by', 'created_by_name', 'created_at', 'updated_at'
        ]
        read_only_fields = ['id', 'created_at', 'updated_at']


class UnitCreateSerializer(serializers.Serializer):
    """单位创建序列化器"""
    name = serializers.CharField(min_length=1, max_length=5, required=True, error_messages={
        'required': '请输入单位名称',
        'blank': '单位名称不能为空',
        'min_length': '单位名称至少1个字',
        'max_length': '单位名称最多5个字',
    })
    
    def validate_name(self, value):
        instance = self.context.get('instance')
        if instance:
            if Unit.objects.filter(name=value).exclude(pk=instance.pk).exists():
                raise serializers.ValidationError('单位名称已存在')
        else:
            if Unit.objects.filter(name=value).exists():
                raise serializers.ValidationError('单位名称已存在')
        return value


class CategorySerializer(serializers.ModelSerializer):
    """品类序列化器"""
    is_linked = serializers.BooleanField(read_only=True)
    created_by_name = serializers.CharField(source='created_by.username', read_only=True)
    unit_name = serializers.CharField(source='unit.name', read_only=True)
    
    class Meta:
        model = Category
        fields = [
            'id', 'name', 'unit', 'unit_name', 'is_linked', 'is_active',
            'created_by', 'created_by_name', 'created_at', 'updated_at'
        ]
        read_only_fields = ['id', 'created_at', 'updated_at']


class CategoryCreateSerializer(serializers.Serializer):
    """品类创建序列化器"""
    name = serializers.CharField(min_length=1, max_length=10, required=True, error_messages={
        'required': '请输入品类名称',
        'blank': '品类名称不能为空',
        'min_length': '品类名称至少1个字',
        'max_length': '品类名称最多10个字',
    })
    unit = serializers.IntegerField(required=True, error_messages={
        'required': '请选择单位',
    })
    
    def validate_name(self, value):
        instance = self.context.get('instance')
        if instance:
            if Category.objects.filter(name=value).exclude(pk=instance.pk).exists():
                raise serializers.ValidationError('品类名称已存在')
        else:
            if Category.objects.filter(name=value).exists():
                raise serializers.ValidationError('品类名称已存在')
        return value
    
    def validate_unit(self, value):
        if not Unit.objects.filter(pk=value).exists():
            raise serializers.ValidationError('单位不存在')
        return value


class VarietySerializer(serializers.ModelSerializer):
    """品种序列化器"""
    is_in_stock = serializers.BooleanField(read_only=True)
    unit_name = serializers.CharField(read_only=True)
    created_by_name = serializers.CharField(source='created_by.username', read_only=True)
    category_name = serializers.CharField(source='category.name', read_only=True)
    
    class Meta:
        model = Variety
        fields = [
            'id', 'name', 'category', 'category_name', 'unit_name',
            'is_in_stock', 'is_active',
            'created_by', 'created_by_name', 'created_at', 'updated_at'
        ]
        read_only_fields = ['id', 'created_at', 'updated_at']


class VarietyCreateSerializer(serializers.Serializer):
    """品种创建序列化器"""
    name = serializers.CharField(min_length=1, max_length=20, required=True, error_messages={
        'required': '请输入品种名称',
        'blank': '品种名称不能为空',
        'min_length': '品种名称至少1个字',
        'max_length': '品种名称最多20个字',
    })
    category = serializers.IntegerField(required=True, error_messages={
        'required': '请选择品类',
    })
    
    def validate_category(self, value):
        if not Category.objects.filter(pk=value).exists():
            raise serializers.ValidationError('品类不存在')
        return value
    
    def validate(self, data):
        instance = self.context.get('instance')
        name = data['name']
        category_id = data['category']
        
        if instance:
            if Variety.objects.filter(name=name, category_id=category_id).exclude(pk=instance.pk).exists():
                raise serializers.ValidationError('该品类下已存在同名品种')
        else:
            if Variety.objects.filter(name=name, category_id=category_id).exists():
                raise serializers.ValidationError('该品类下已存在同名品种')
        return data


class GoodsSerializer(serializers.ModelSerializer):
    """货物序列化器"""
    variety_name = serializers.CharField(source='variety.name', read_only=True)
    category_name = serializers.CharField(source='variety.category.name', read_only=True)
    unit_name = serializers.CharField(source='variety.category.unit.name', read_only=True)
    is_warning = serializers.BooleanField(read_only=True)
    
    class Meta:
        model = Goods
        fields = [
            'id', 'name', 'code', 'variety', 'variety_name',
            'category_name', 'unit_name', 'specification',
            'quantity', 'warning_threshold', 'location',
            'remark', 'is_active', 'is_warning',
            'created_at', 'updated_at'
        ]


class StockInSerializer(serializers.ModelSerializer):
    """入库记录序列化器"""
    goods_name = serializers.CharField(source='goods.name', read_only=True)
    operator_name = serializers.CharField(source='operator.username', read_only=True)
    effective_date = serializers.DateField(read_only=True)

    class Meta:
        model = StockIn
        fields = [
            'id', 'goods', 'goods_name', 'operator', 'operator_name',
            'quantity', 'batch_no', 'supplier', 'business_date', 'effective_date',
            'stock_in_time', 'remark', 'period', 'version_no'
        ]
        read_only_fields = ['operator', 'stock_in_time', 'period', 'version_no']


class StockInWriteSerializer(serializers.Serializer):
    """入库记录新增/修改序列化器"""
    goods = serializers.IntegerField(required=True, error_messages={'required': '请选择货物'})
    quantity = serializers.DecimalField(
        max_digits=12, decimal_places=2, min_value=Decimal('0.01'),
        required=True, error_messages={'required': '请输入入库数量', 'min_value': '入库数量必须大于0'}
    )
    business_date = serializers.DateField(required=False, allow_null=True,
                                          error_messages={'invalid': '业务日期格式应为 YYYY-MM-DD'})
    batch_no = serializers.CharField(max_length=50, required=False, allow_blank=True)
    supplier = serializers.CharField(max_length=200, required=False, allow_blank=True)
    remark = serializers.CharField(required=False, allow_blank=True)


class StockOutSerializer(serializers.ModelSerializer):
    """出库记录序列化器"""
    goods_name = serializers.CharField(source='goods.name', read_only=True)
    operator_name = serializers.CharField(source='operator.username', read_only=True)
    status_display = serializers.CharField(source='get_status_display', read_only=True)
    effective_date = serializers.DateField(read_only=True)

    class Meta:
        model = StockOut
        fields = [
            'id', 'goods', 'goods_name', 'operator', 'operator_name',
            'receiver', 'receiver_dept', 'quantity', 'status', 'status_display',
            'business_date', 'effective_date',
            'stock_out_time', 'remark', 'created_at', 'period', 'version_no'
        ]
        read_only_fields = ['operator', 'status', 'stock_out_time', 'created_at',
                            'period', 'version_no']


class StockOutWriteSerializer(serializers.Serializer):
    """出库记录新增/修改序列化器"""
    goods = serializers.IntegerField(required=True, error_messages={'required': '请选择货物'})
    quantity = serializers.DecimalField(
        max_digits=12, decimal_places=2, min_value=Decimal('0.01'),
        required=True, error_messages={'required': '请输入出库数量', 'min_value': '出库数量必须大于0'}
    )
    receiver = serializers.CharField(max_length=100, required=True,
                                     error_messages={'required': '请输入领用人', 'blank': '领用人不能为空'})
    receiver_dept = serializers.CharField(max_length=100, required=False, allow_blank=True)
    business_date = serializers.DateField(required=False, allow_null=True,
                                          error_messages={'invalid': '业务日期格式应为 YYYY-MM-DD'})
    remark = serializers.CharField(required=False, allow_blank=True)


class WarningSerializer(serializers.ModelSerializer):
    """预警记录序列化器"""
    goods_name = serializers.CharField(source='goods.name', read_only=True)
    type_display = serializers.CharField(source='get_type_display', read_only=True)
    
    class Meta:
        model = Warning
        fields = [
            'id', 'goods', 'goods_name', 'type', 'type_display',
            'message', 'is_read', 'created_at'
        ]


class ApprovalSerializer(serializers.ModelSerializer):
    """审批记录序列化器"""
    approver_name = serializers.CharField(source='approver.username', read_only=True)
    status_display = serializers.CharField(source='get_status_display', read_only=True)

    class Meta:
        model = Approval
        fields = [
            'id', 'stock_out', 'approver', 'approver_name',
            'status', 'status_display', 'remark', 'created_at', 'updated_at'
        ]


# ==================== 会计期间 / 封账与版本 ====================

class AccountingPeriodSerializer(serializers.ModelSerializer):
    """会计期间序列化器"""
    status_display = serializers.CharField(source='get_status_display', read_only=True)
    tried_by_name = serializers.CharField(source='tried_by.username', read_only=True)
    closed_by_name = serializers.CharField(source='closed_by.username', read_only=True)

    class Meta:
        model = AccountingPeriod
        fields = [
            'id', 'period', 'status', 'status_display', 'current_version',
            'tried_at', 'tried_by_name', 'closed_at', 'closed_by_name',
            'created_at', 'updated_at',
        ]


class PeriodVersionSerializer(serializers.ModelSerializer):
    """期间版本快照序列化器"""
    sealed_by_name = serializers.CharField(source='sealed_by.username', read_only=True)

    class Meta:
        model = PeriodVersion
        fields = [
            'id', 'period', 'version_no', 'input_scope', 'rule', 'summary',
            'sealed_at', 'sealed_by', 'sealed_by_name',
        ]


class ReopenRequestSerializer(serializers.ModelSerializer):
    """重开审批序列化器"""
    status_display = serializers.CharField(source='get_status_display', read_only=True)
    requester_name = serializers.CharField(source='requester.username', read_only=True)
    approver_name = serializers.CharField(source='approver.username', read_only=True)
    period_key = serializers.CharField(source='period.period', read_only=True)

    class Meta:
        model = ReopenRequest
        fields = [
            'id', 'period', 'period_key', 'requester', 'requester_name',
            'reason', 'status', 'status_display', 'approver', 'approver_name',
            'approve_remark', 'reopened_version', 'approved_at',
            'created_at', 'updated_at',
        ]
        read_only_fields = ['requester', 'status', 'approver', 'reopened_version', 'approved_at']


class ReopenCreateSerializer(serializers.Serializer):
    """重开申请序列化器"""
    period = serializers.CharField(required=True, error_messages={'required': '请指定会计期间'})
    reason = serializers.CharField(min_length=2, max_length=500, required=True, error_messages={
        'required': '请填写重开原因', 'blank': '重开原因不能为空',
        'min_length': '重开原因至少2个字',
    })


class ReopenDecideSerializer(serializers.Serializer):
    """重开审批序列化器"""
    approved = serializers.BooleanField(required=True, error_messages={'required': '请给出审批结果'})
    remark = serializers.CharField(max_length=500, required=False, allow_blank=True)
