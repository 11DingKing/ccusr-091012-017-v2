"""
进程内按键互斥锁

SQLite 只有单一写者，两个并发写事务在锁升级时会得到不可重试的
SQLITE_LOCKED（database/table is locked）。本服务部署在单个应用容器内，
用按键（月份/单据ID）临界区把并发的封账、会签、重开审批串行化，
数据库唯一约束则继续作为跨进程/跨实例的最终兜底，两者共同保证确定结果。
"""
import threading
from contextlib import contextmanager

_guard = threading.Lock()
_key_locks: dict[str, threading.RLock] = {}


def _get_lock(key: str) -> threading.RLock:
    with _guard:
        lock = _key_locks.get(key)
        if lock is None:
            lock = threading.RLock()
            _key_locks[key] = lock
        return lock


@contextmanager
def locked(key: str):
    """获取指定业务键的进程内互斥锁"""
    _get_lock(key).acquire()
    try:
        yield
    finally:
        _get_lock(key).release()
