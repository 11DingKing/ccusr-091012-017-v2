"""月结封账业务异常（由全局异常处理器或视图映射为 HTTP 响应）"""


class AccountingError(Exception):
    def __init__(self, message, code=400):
        self.message = message
        self.code = code
        super().__init__(message)


class PeriodLockedError(AccountingError):
    """封账/签署期间修改敏感记录，或历史版本记录被修改"""

    def __init__(self, message='该月份已封账，如需调整请先申请重开'):
        super().__init__(message, code=409)


class ClosureConflictError(AccountingError):
    """重复封账、重复签署、重复审批等并发冲突"""

    def __init__(self, message='操作冲突，请刷新后重试'):
        super().__init__(message, code=409)


class ClosurePermissionError(AccountingError):
    """非签署人/非管理员执行受限操作"""

    def __init__(self, message='无权执行该操作'):
        super().__init__(message, code=403)
