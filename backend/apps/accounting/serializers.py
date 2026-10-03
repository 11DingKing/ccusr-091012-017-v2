"""月结封账序列化器"""
from rest_framework import serializers

from .models import ClosureSignature, PeriodClosure, ReopenRequest


class ClosureSignatureSerializer(serializers.ModelSerializer):
    class Meta:
        model = ClosureSignature
        fields = ['id', 'signer', 'signer_name', 'signed_at']


class PeriodClosureSerializer(serializers.ModelSerializer):
    status_display = serializers.CharField(source='get_status_display', read_only=True)
    signatures = ClosureSignatureSerializer(many=True, read_only=True)
    closed_by_name = serializers.CharField(source='closed_by.username', read_only=True, default=None)
    signed_count = serializers.SerializerMethodField()
    required_count = serializers.SerializerMethodField()
    is_locked = serializers.BooleanField(read_only=True)

    class Meta:
        model = PeriodClosure
        fields = [
            'id', 'period', 'version_no', 'status', 'status_display',
            'input_range', 'rules_snapshot', 'summary_snapshot',
            'required_signer_ids', 'signatures',
            'signed_count', 'required_count', 'is_locked',
            'closed_by', 'closed_by_name',
            'sealed_at', 'reopened_at', 'created_at', 'updated_at',
        ]

    def get_signed_count(self, obj):
        # 列表视图使用 prefetch_related，.all() 命中预取缓存不产生查询
        return len(obj.signatures.all())

    def get_required_count(self, obj):
        return len(obj.required_signer_ids or [])


class ReopenRequestSerializer(serializers.ModelSerializer):
    status_display = serializers.CharField(source='get_status_display', read_only=True)
    applicant_name = serializers.CharField(source='applicant.username', read_only=True, default=None)
    approver_name = serializers.CharField(source='approver.username', read_only=True, default=None)

    class Meta:
        model = ReopenRequest
        fields = [
            'id', 'period', 'version_no', 'closure',
            'applicant', 'applicant_name', 'reason',
            'status', 'status_display',
            'approver', 'approver_name', 'reply',
            'decided_at', 'created_at', 'updated_at',
        ]
        read_only_fields = [
            'version_no', 'closure', 'applicant', 'status',
            'approver', 'reply', 'decided_at',
        ]
