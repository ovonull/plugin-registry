"""统一业务响应与异常定义。"""

from typing import Any

from fastapi.responses import JSONResponse


class BusinessError(Exception):
    """可安全展示给用户的业务异常。"""

    def __init__(self, message: str, code: int = 40001) -> None:
        super().__init__(message)
        self.message = message
        self.code = code


def success(data: Any = None, message: str = "操作成功") -> dict[str, Any]:
    """构造统一成功响应。"""
    return {"success": True, "code": 200, "message": message, "data": data}


def failure(message: str, code: int = 40001) -> dict[str, Any]:
    """构造统一失败响应。"""
    return {"success": False, "code": code, "message": message, "data": None}


def failure_response(message: str, code: int = 40001) -> JSONResponse:
    """以 HTTP 200 返回统一业务错误。"""
    return JSONResponse(status_code=200, content=failure(message, code))


def onebot_error(message: str, retcode: int, echo: object = "") -> dict[str, Any]:
    """构造 OneBot 11 失败响应。"""
    return {
        "status": "failed",
        "retcode": retcode,
        "data": None,
        "message": message,
        "wording": message,
        "echo": echo,
    }

