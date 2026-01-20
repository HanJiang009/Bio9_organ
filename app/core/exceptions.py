"""异常处理模块：注册全局兜底异常处理，避免未捕获错误暴露细节。"""
from fastapi import Request, HTTPException
from fastapi.responses import JSONResponse
import logging

logger = logging.getLogger(__name__)

async def http_exception_handler(request: Request, exc: HTTPException):
    return JSONResponse({"code": 0, "msg": exc.detail or "Error", "data": None}, status_code=exc.status_code)

async def global_exception_handler(request: Request, exc: Exception):
    """通用异常处理器。

    记录异常栈并返回统一的 500 JSON 响应，防止内部错误细节泄露。
    """
    logger.exception("Unhandled exception")
    return JSONResponse({"detail": "Internal Server Error"}, status_code=500)
