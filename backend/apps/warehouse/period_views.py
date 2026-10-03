"""
会计期间视图：试算、封账、重开审批、版本与收发汇总查询。
"""
import logging

from rest_framework.permissions import IsAuthenticated
from rest_framework.views import APIView

from apps.core.exceptions import PermissionException
from apps.core.response import error_response, success_response

from .models import AccountingPeriod, PeriodAuditLog, PeriodVersion, ReopenRequest
from . import periods as period_service
from .serializers import (
    AccountingPeriodSerializer,
    PeriodVersionSerializer,
    ReopenCreateSerializer,
    ReopenDecideSerializer,
    ReopenRequestSerializer,
)

logger = logging.getLogger('apps')


def _require_admin(user):
    if not user.is_admin:
        raise PermissionException('仅管理员可执行封账与审批操作')


def _parse_period(request):
    period_key = request.query_params.get('period') or request.data.get('period')
    if not period_key:
        return None
    return period_service.normalize_period(period_key)


class PeriodListView(APIView):
    """会计期间列表"""
    permission_classes = [IsAuthenticated]

    def get(self, request):
        queryset = AccountingPeriod.objects.all().order_by('-period')
        status = request.query_params.get('status')
        if status:
            queryset = queryset.filter(status=status)

        page = int(request.query_params.get('page', 1))
        page_size = int(request.query_params.get('page_size', 10))
        total = queryset.count()
        items = queryset[(page - 1) * page_size:page * page_size]
        return success_response(data={
            'list': AccountingPeriodSerializer(items, many=True).data,
            'total': total, 'page': page, 'page_size': page_size,
        })


class PeriodDetailView(APIView):
    """期间状态：即便从未试算（无期间行）也返回合成的开放状态，便于前端判断。"""
    permission_classes = [IsAuthenticated]

    def get(self, request, period_key):
        period_key = period_service.normalize_period(period_key)
        period = AccountingPeriod.objects.filter(period=period_key).first()
        if period is None:
            return success_response(data={
                'id': None, 'period': period_key, 'status': 'open',
                'status_display': '开放中', 'current_version': 1,
                'tried_at': None, 'closed_at': None,
            })
        return success_response(data=AccountingPeriodSerializer(period).data)


class PeriodTrialView(APIView):
    """试算"""
    permission_classes = [IsAuthenticated]

    def post(self, request, period_key):
        _require_admin(request.user)
        period_key = period_service.normalize_period(period_key)
        _, snapshot = period_service.trial_period(period_key, request.user)
        return success_response(data=snapshot, message='试算完成，请核对后封账')


class PeriodCloseView(APIView):
    """封账（重复封账幂等，返回同一版本）"""
    permission_classes = [IsAuthenticated]

    def post(self, request, period_key):
        _require_admin(request.user)
        period_key = period_service.normalize_period(period_key)
        period, version, outcome = period_service.close_period(period_key, request.user)
        message = '该期间已封账，重复封账未产生新版本' if outcome == 'already_closed' else '封账成功'
        data = {
            'period': period.period,
            'status': period.status,
            'current_version': period.current_version,
            'version': PeriodVersionSerializer(version).data if version else None,
        }
        return success_response(data=data, message=message)


class PeriodSummaryView(APIView):
    """收发汇总。

    默认返回当前版本：已封账返回当前封账版本快照，未封账返回实时试算快照；
    指定 version 时返回对应历史版本快照，旧版本可随时追溯、结果可重现。
    """
    permission_classes = [IsAuthenticated]

    def get(self, request, period_key):
        period_key = period_service.normalize_period(period_key)
        version_param = request.query_params.get('version')

        if version_param:
            try:
                version_no = int(version_param)
            except (TypeError, ValueError):
                return error_response(message='版本号必须为整数')
            sealed = period_service.get_sealed_snapshot(period_key, version_no)
            return success_response(data={
                'period': period_key,
                'version_no': sealed.version_no,
                'sealed': True,
                'sealed_at': sealed.sealed_at.isoformat(),
                'input_scope': sealed.input_scope,
                'rule': sealed.rule,
                'summary': sealed.summary,
            })

        period = AccountingPeriod.objects.filter(period=period_key).first()
        if period is not None and period.is_closed:
            sealed = period_service.get_sealed_snapshot(period_key, period.current_version)
            return success_response(data={
                'period': period_key,
                'version_no': sealed.version_no,
                'sealed': True,
                'sealed_at': sealed.sealed_at.isoformat(),
                'input_scope': sealed.input_scope,
                'rule': sealed.rule,
                'summary': sealed.summary,
            })

        snapshot = period_service.build_snapshot(period_key)
        return success_response(data={
            'period': period_key,
            'version_no': period.current_version if period else 1,
            'sealed': False,
            'sealed_at': None,
            'input_scope': snapshot['input_scope'],
            'rule': snapshot['rule'],
            'summary': snapshot['summary'],
        })


class PeriodVersionListView(APIView):
    """期间已封账版本列表（旧版本追溯入口）"""
    permission_classes = [IsAuthenticated]

    def get(self, request, period_key):
        period_key = period_service.normalize_period(period_key)
        versions = PeriodVersion.objects.filter(period__period=period_key).order_by('version_no')
        data = PeriodVersionSerializer(versions, many=True).data
        return success_response(data={'list': data, 'total': len(data)})


class PeriodVersionDetailView(APIView):
    """指定版本快照详情"""
    permission_classes = [IsAuthenticated]

    def get(self, request, period_key, version_no):
        period_key = period_service.normalize_period(period_key)
        sealed = period_service.get_sealed_snapshot(period_key, int(version_no))
        return success_response(data=PeriodVersionSerializer(sealed).data)


class ReopenRequestListView(APIView):
    """重开申请列表 / 提交重开申请"""
    permission_classes = [IsAuthenticated]

    def get(self, request):
        queryset = ReopenRequest.objects.select_related('period', 'requester', 'approver')
        period_key = request.query_params.get('period')
        if period_key:
            queryset = queryset.filter(period__period=period_service.normalize_period(period_key))
        status = request.query_params.get('status')
        if status:
            queryset = queryset.filter(status=status)

        page = int(request.query_params.get('page', 1))
        page_size = int(request.query_params.get('page_size', 10))
        total = queryset.count()
        items = queryset[(page - 1) * page_size:page * page_size]
        return success_response(data={
            'list': ReopenRequestSerializer(items, many=True).data,
            'total': total, 'page': page, 'page_size': page_size,
        })

    def post(self, request):
        serializer = ReopenCreateSerializer(data=request.data)
        if not serializer.is_valid():
            first_error = list(serializer.errors.values())[0][0]
            return error_response(message=str(first_error))
        period_key = period_service.normalize_period(serializer.validated_data['period'])
        req = period_service.request_reopen(
            period_key, request.user, serializer.validated_data['reason']
        )
        return success_response(data=ReopenRequestSerializer(req).data, message='重开申请已提交')


class ReopenRequestDetailView(APIView):
    """重开申请详情"""
    permission_classes = [IsAuthenticated]

    def get(self, request, pk):
        req = ReopenRequest.objects.select_related('period', 'requester', 'approver').filter(pk=pk).first()
        if req is None:
            return error_response(message='重开申请不存在', code=404)
        return success_response(data=ReopenRequestSerializer(req).data)


class ReopenDecideView(APIView):
    """重开审批：获批后期间开放并进入新版本（并发/重复签署结果确定）"""
    permission_classes = [IsAuthenticated]

    def post(self, request, pk):
        _require_admin(request.user)
        serializer = ReopenDecideSerializer(data=request.data)
        if not serializer.is_valid():
            first_error = list(serializer.errors.values())[0][0]
            return error_response(message=str(first_error))
        req = period_service.decide_reopen(
            pk, request.user,
            serializer.validated_data['approved'],
            serializer.validated_data.get('remark', ''),
        )
        message = '已批准重开，期间进入新版本' if req.status == 'approved' else '已拒绝重开'
        return success_response(data=ReopenRequestSerializer(req).data, message=message)


class PeriodAuditLogView(APIView):
    """期间敏感操作审计日志"""
    permission_classes = [IsAuthenticated]

    def get(self, request, period_key):
        period_key = period_service.normalize_period(period_key)
        logs = PeriodAuditLog.objects.filter(period=period_key).order_by('-created_at')
        data = [{
            'id': log.id,
            'period': log.period,
            'version_no': log.version_no,
            'action': log.action,
            'action_display': log.get_action_display(),
            'detail': log.detail,
            'operator': log.operator.username if log.operator else None,
            'created_at': log.created_at.isoformat(),
        } for log in logs]
        return success_response(data={'list': data, 'total': len(data)})
