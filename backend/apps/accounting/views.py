"""
月结封账视图

路由（均在 /api/ 前缀下）：
- GET  accounting/periods/<period>/        期间状态
- GET  accounting/trial/?period=YYYY-MM    试算（实时，不落库）
- GET  accounting/closures/?period=        版本列表（含快照，可追溯）
- POST accounting/closures/                发起封账
- POST accounting/closures/<id>/sign/      会签
- GET  accounting/reopen-requests/         重开申请列表
- POST accounting/reopen-requests/         提交重开申请
- POST accounting/reopen-requests/<id>/decide/  审批（approved/rejected）
- GET  accounting/records/?period=&version=&type=  版本化收发记录
"""
from rest_framework.permissions import IsAuthenticated
from rest_framework.views import APIView

from apps.core.response import error_response, success_response
from apps.warehouse.serializers import StockInSerializer, StockOutSerializer

from .errors import AccountingError
from . import services
from .models import PeriodClosure, ReopenRequest
from .periods import parse_period
from .serializers import PeriodClosureSerializer, ReopenRequestSerializer


def _period_param(request):
    period = request.query_params.get('period') or request.data.get('period')
    try:
        return parse_period(period)
    except (ValueError, TypeError):
        return None


def _paginate(queryset, request):
    try:
        page = max(int(request.query_params.get('page', 1)), 1)
        page_size = min(max(int(request.query_params.get('page_size', 10)), 1), 200)
    except (TypeError, ValueError):
        page, page_size = 1, 10
    total = queryset.count()
    return queryset[(page - 1) * page_size:page * page_size], total, page, page_size


class PeriodStateView(APIView):
    """单个月份的封账状态"""
    permission_classes = [IsAuthenticated]

    def get(self, request, period):
        try:
            parse_period(period)
        except ValueError:
            return error_response(message='月份格式应为 YYYY-MM')
        return success_response(data=services.get_period_state(period))


class TrialView(APIView):
    """试算：实时计算输入范围、规则与汇总，不落库"""
    permission_classes = [IsAuthenticated]

    def get(self, request):
        period = _period_param(request)
        if not period:
            return error_response(message='请提供 period=YYYY-MM')
        try:
            return success_response(data=services.trial(period), message='试算完成')
        except AccountingError as exc:
            return error_response(message=exc.message, code=exc.code)


class ClosureListCreateView(APIView):
    """版本列表（追溯）与发起封账"""
    permission_classes = [IsAuthenticated]

    def get(self, request):
        period = _period_param(request)
        if not period:
            return error_response(message='请提供 period=YYYY-MM')
        closures = services.list_versions(period)
        data = PeriodClosureSerializer(closures, many=True).data
        return success_response(data={
            'period': period,
            'current_version': services.current_version(period),
            'state': services.get_period_state(period),
            'versions': data,
        })

    def post(self, request):
        period = _period_param(request)
        if not period:
            return error_response(message='请提供 period=YYYY-MM')
        try:
            closure = services.initiate_seal(period, request.user)
        except AccountingError as exc:
            return error_response(message=exc.message, code=exc.code)
        return success_response(data=PeriodClosureSerializer(closure).data, message='封账已发起，等待会签')


class ClosureSignView(APIView):
    """封账会签"""
    permission_classes = [IsAuthenticated]

    def post(self, request, pk):
        try:
            closure = services.sign_closure(pk, request.user)
        except AccountingError as exc:
            return error_response(message=exc.message, code=exc.code)
        serializer = PeriodClosureSerializer(closure)
        if closure.status == 'sealed':
            return success_response(data=serializer.data, message='会签已集齐，封账完成')
        return success_response(data=serializer.data, message='签署成功，等待其他签署人')


class ReopenRequestListCreateView(APIView):
    """重开申请列表 / 提交重开申请"""
    permission_classes = [IsAuthenticated]

    def get(self, request):
        qs = ReopenRequest.objects.select_related('applicant', 'approver').all()
        period = request.query_params.get('period')
        if period:
            qs = qs.filter(period=period)
        status = request.query_params.get('status')
        if status:
            qs = qs.filter(status=status)
        page_items, total, page, page_size = _paginate(qs.order_by('-created_at'), request)
        return success_response(data={
            'list': ReopenRequestSerializer(page_items, many=True).data,
            'total': total, 'page': page, 'page_size': page_size,
        })

    def post(self, request):
        period = _period_param(request)
        if not period:
            return error_response(message='请提供 period=YYYY-MM')
        try:
            reopen = services.request_reopen(period, request.user, request.data.get('reason', ''))
        except AccountingError as exc:
            return error_response(message=exc.message, code=exc.code)
        return success_response(data=ReopenRequestSerializer(reopen).data, message='重开申请已提交')


class ReopenRequestDecideView(APIView):
    """管理员审批重开申请"""
    permission_classes = [IsAuthenticated]

    def post(self, request, pk):
        action = request.data.get('action')
        reply = request.data.get('reply', '')
        try:
            reopen, new_closure = services.decide_reopen(pk, request.user, action, reply)
        except AccountingError as exc:
            return error_response(message=exc.message, code=exc.code)
        data = {
            'request': ReopenRequestSerializer(reopen).data,
            'new_version': PeriodClosureSerializer(new_closure).data if new_closure else None,
        }
        message = '已批准重开，变更将进入新版本' if action == 'approved' else '已拒绝重开申请'
        return success_response(data=data, message=message)


class VersionedRecordsView(APIView):
    """版本化收发记录：默认当前版本，version=N 追溯旧版本，version=all 全部版本"""
    permission_classes = [IsAuthenticated]

    def get(self, request):
        period = _period_param(request)
        if not period:
            return error_response(message='请提供 period=YYYY-MM')
        version = request.query_params.get('version', 'current')
        if version not in ('current', 'all'):
            try:
                version = str(int(version))
            except ValueError:
                return error_response(message="version 须为 current、all 或版本号")
        record_type = request.query_params.get('type', 'all')

        result = {
            'period': period,
            'version': version,
            'resolved_version': services.current_version(period) if version == 'current' else version,
            'state': services.get_period_state(period),
        }

        if record_type in ('all', 'stock_in'):
            qs = services.versioned_stock_in(period, version)
            page_items, total, page, page_size = _paginate(qs, request)
            result['stock_in'] = {
                'list': StockInSerializer(page_items, many=True).data,
                'total': total, 'page': page, 'page_size': page_size,
            }
        if record_type in ('all', 'stock_out'):
            qs = services.versioned_stock_out(period, version)
            page_items, total, page, page_size = _paginate(qs, request)
            result['stock_out'] = {
                'list': StockOutSerializer(page_items, many=True).data,
                'total': total, 'page': page, 'page_size': page_size,
            }
        if record_type not in ('all', 'stock_in', 'stock_out'):
            return error_response(message="type 须为 all、stock_in 或 stock_out")
        return success_response(data=result)
