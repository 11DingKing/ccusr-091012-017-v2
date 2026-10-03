"""
仓库管理URL配置
"""
from django.urls import path
from .views import (
    UnitListView, UnitDetailView, UnitBatchDeleteView, UnitAllView,
    CategoryListView, CategoryDetailView, CategoryBatchDeleteView, CategoryAllView,
    VarietyListView, VarietyDetailView, VarietyBatchDeleteView,
    VarietyTemplateView, VarietyImportView,
    GoodsListView,
    StockInListView, StockInDetailView,
    StockOutListView, StockOutDetailView, StockOutSignView, StockOutCompleteView,
    WarningListView, ApprovalListView,
)
from .period_views import (
    PeriodListView, PeriodDetailView, PeriodTrialView, PeriodCloseView,
    PeriodSummaryView, PeriodVersionListView, PeriodVersionDetailView,
    ReopenRequestListView, ReopenRequestDetailView, ReopenDecideView,
    PeriodAuditLogView,
)

urlpatterns = [
    # 单位管理
    path('units/', UnitListView.as_view(), name='unit-list'),
    path('units/all/', UnitAllView.as_view(), name='unit-all'),
    path('units/batch-delete/', UnitBatchDeleteView.as_view(), name='unit-batch-delete'),
    path('units/<int:pk>/', UnitDetailView.as_view(), name='unit-detail'),

    # 品类管理
    path('categories/', CategoryListView.as_view(), name='category-list'),
    path('categories/all/', CategoryAllView.as_view(), name='category-all'),
    path('categories/batch-delete/', CategoryBatchDeleteView.as_view(), name='category-batch-delete'),
    path('categories/<int:pk>/', CategoryDetailView.as_view(), name='category-detail'),

    # 品种管理
    path('varieties/', VarietyListView.as_view(), name='variety-list'),
    path('varieties/batch-delete/', VarietyBatchDeleteView.as_view(), name='variety-batch-delete'),
    path('varieties/template/', VarietyTemplateView.as_view(), name='variety-template'),
    path('varieties/import/', VarietyImportView.as_view(), name='variety-import'),
    path('varieties/<int:pk>/', VarietyDetailView.as_view(), name='variety-detail'),

    # 货物管理
    path('goods/', GoodsListView.as_view(), name='goods-list'),

    # 入库管理
    path('stock-in/', StockInListView.as_view(), name='stock-in-list'),
    path('stock-in/<int:pk>/', StockInDetailView.as_view(), name='stock-in-detail'),

    # 出库管理
    path('stock-out/', StockOutListView.as_view(), name='stock-out-list'),
    path('stock-out/<int:pk>/', StockOutDetailView.as_view(), name='stock-out-detail'),
    path('stock-out/<int:pk>/sign/', StockOutSignView.as_view(), name='stock-out-sign'),
    path('stock-out/<int:pk>/complete/', StockOutCompleteView.as_view(), name='stock-out-complete'),

    # 预警管理
    path('warnings/', WarningListView.as_view(), name='warning-list'),

    # 审批管理
    path('approvals/', ApprovalListView.as_view(), name='approval-list'),

    # 会计期间：试算 / 封账 / 重开 / 版本 / 汇总
    path('periods/', PeriodListView.as_view(), name='period-list'),
    path('periods/<str:period_key>/', PeriodDetailView.as_view(), name='period-detail'),
    path('periods/<str:period_key>/trial/', PeriodTrialView.as_view(), name='period-trial'),
    path('periods/<str:period_key>/close/', PeriodCloseView.as_view(), name='period-close'),
    path('periods/<str:period_key>/summary/', PeriodSummaryView.as_view(), name='period-summary'),
    path('periods/<str:period_key>/versions/', PeriodVersionListView.as_view(), name='period-version-list'),
    path('periods/<str:period_key>/versions/<int:version_no>/', PeriodVersionDetailView.as_view(), name='period-version-detail'),
    path('periods/<str:period_key>/audit-logs/', PeriodAuditLogView.as_view(), name='period-audit-logs'),

    # 重开审批
    path('reopen-requests/', ReopenRequestListView.as_view(), name='reopen-list'),
    path('reopen-requests/<int:pk>/', ReopenRequestDetailView.as_view(), name='reopen-detail'),
    path('reopen-requests/<int:pk>/decide/', ReopenDecideView.as_view(), name='reopen-decide'),
]
