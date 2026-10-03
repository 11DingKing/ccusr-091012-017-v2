"""监管物资保管服务的运行配置。"""
import os
from datetime import timedelta
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY", "local-custody-service-key")
DEBUG = os.environ.get("DJANGO_DEBUG", "false").lower() == "true"
ALLOWED_HOSTS = ["*"]
INSTALLED_APPS = [
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "rest_framework",
    "django_filters",
    "apps.authentication",
    "apps.accounting",
    "apps.warehouse",
    "apps.personnel",
    "apps.reports",
]
MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "apps.core.logging_middleware.RequestLoggingMiddleware",
    "apps.core.logging_middleware.SecurityLoggingMiddleware",
    "apps.authentication.middleware.JWTAuthenticationMiddleware",
    "apps.authentication.middleware.OperationLogMiddleware",
]
ROOT_URLCONF = "warehouse_system.urls"
TEMPLATES = []
WSGI_APPLICATION = "warehouse_system.wsgi.application"
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": os.environ.get("SQLITE_PATH", str(BASE_DIR / "db.sqlite3")),
        "OPTIONS": {"timeout": 20},
    }
}


# SQLite 并发加固：WAL 允许读写并发、并发写者按 busy_timeout 排队，
# 避免会签/封账等并发提交时出现立即 "database is locked" 的不确定结果。
from django.db.backends.signals import connection_created  # noqa: E402


def _sqlite_pragmas(sender, connection, **kwargs):
    if connection.vendor == "sqlite":
        cursor = connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL;")
        cursor.execute("PRAGMA busy_timeout=20000;")
        cursor.execute("PRAGMA foreign_keys=ON;")


connection_created.connect(_sqlite_pragmas)
AUTH_PASSWORD_VALIDATORS = []
AUTH_USER_MODEL = "authentication.User"
LANGUAGE_CODE = "zh-hans"
TIME_ZONE = "Asia/Shanghai"
USE_I18N = True
USE_TZ = True
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": ["apps.authentication.backends.JWTAuthentication"],
    "DEFAULT_PERMISSION_CLASSES": ["rest_framework.permissions.IsAuthenticated"],
    "DEFAULT_FILTER_BACKENDS": [
        "django_filters.rest_framework.DjangoFilterBackend",
        "rest_framework.filters.SearchFilter",
        "rest_framework.filters.OrderingFilter",
    ],
    "DEFAULT_PAGINATION_CLASS": "rest_framework.pagination.PageNumberPagination",
    "PAGE_SIZE": 10,
    "EXCEPTION_HANDLER": "apps.core.exceptions.custom_exception_handler",
}
JWT_SECRET_KEY = os.environ.get("JWT_SECRET_KEY", "local-custody-jwt-key")
JWT_ALGORITHM = "HS256"
JWT_EXPIRATION_DELTA = timedelta(hours=24)
MEDIA_URL = "/media/"
MEDIA_ROOT = BASE_DIR / "media"
LOGS_DIR = BASE_DIR / "logs"
LOGS_DIR.mkdir(exist_ok=True)
from apps.core.logging_config import setup_logging
setup_logging(BASE_DIR, DEBUG)
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {"console": {"class": "logging.StreamHandler"}},
    "root": {"handlers": ["console"], "level": "WARNING"},
}
