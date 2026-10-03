"""
自定义 SQLite 后端：写事务以 BEGIN IMMEDIATE 起始。

Django 4.2 的标准后端在 autocommit 模式下只发普通 BEGIN（DEFERRED），
而 SQLite 不支持 SELECT ... FOR UPDATE 行锁。普通 BEGIN 在首次写时才升级锁，
两个并发事务可能同时读到 pending 后双双写入，无法保证并发签署/封账的确定性。

BEGIN IMMEDIATE 一进事务即获取 RESERVED 锁，第二个并发写事务在起点排队，
前者提交后后者读到已决状态并返回确定的 409，从而在单文件 SQLite 上串行化写事务。
"""
from django.db.backends.sqlite3.base import DatabaseWrapper as SQLiteDatabaseWrapper


class DatabaseWrapper(SQLiteDatabaseWrapper):
    def _start_transaction_under_autocommit(self):
        self.cursor().execute("BEGIN IMMEDIATE")
