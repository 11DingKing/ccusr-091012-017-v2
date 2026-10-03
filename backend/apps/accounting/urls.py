"""月结封账URL配置"""
from django.urls import path

from .views import (
    ClosureListCreateView, ClosureSignView,
    PeriodStateView, ReopenRequestDecideView, ReopenRequestListCreateView,
    TrialView, VersionedRecordsView,
)

urlpatterns = [
    path('accounting/periods/<str:period>/', PeriodStateView.as_view(), name='accounting-period-state'),
    path('accounting/trial/', TrialView.as_view(), name='accounting-trial'),
    path('accounting/closures/', ClosureListCreateView.as_view(), name='accounting-closure-list'),
    path('accounting/closures/<int:pk>/sign/', ClosureSignView.as_view(), name='accounting-closure-sign'),
    path('accounting/reopen-requests/', ReopenRequestListCreateView.as_view(), name='accounting-reopen-list'),
    path('accounting/reopen-requests/<int:pk>/decide/', ReopenRequestDecideView.as_view(), name='accounting-reopen-decide'),
    path('accounting/records/', VersionedRecordsView.as_view(), name='accounting-records'),
]
