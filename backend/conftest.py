"""
Pytest配置文件
"""
import os
import tempfile
import django
from django.conf import settings

# 设置Django配置
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'warehouse_system.settings')


def pytest_configure():
    """Pytest配置"""
    settings.DEBUG = False
    # 并发测试需要真实文件库：共享缓存内存库（:memory:）上写事务会令其他
    # 连接立即 SQLITE_LOCKED，无法验证 BEGIN IMMEDIATE 的锁等待串行化
    test_db = os.environ.get('TEST_SQLITE_PATH') or os.path.join(
        tempfile.gettempdir(), 'wh_pytest.sqlite3'
    )
    settings.DATABASES['default'].setdefault('TEST', {})['NAME'] = test_db
    django.setup()
