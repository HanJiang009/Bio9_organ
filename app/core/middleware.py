import time
import uuid
import logging
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response
from .settings import settings
from app.core.token import verify_token
from app.crud.op_log import insert_op_log

logger = logging.getLogger("request")


class RequestLogMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        if not settings.request_log:
            return await call_next(request)
        trace_id = request.headers.get("X-Trace-Id") or uuid.uuid4().hex
        start = time.time()
        try:
            response: Response = await call_next(request)
            elapsed = round((time.time() - start) * 1000)
            logger.info(
                f"trace={trace_id} method={request.method} path={request.url.path} status={response.status_code} ms={elapsed}"
            )
            response.headers["X-Trace-Id"] = trace_id
            # 写入操作日志（以路径首段作为模块名）
            try:
                seg = request.url.path.strip("/").split("/")
                module = (seg[0] if seg else "root")
                # 解析 token 获取工号与姓名
                operator_code = None
                operator_name = None
                auth_header = request.headers.get("Authorization") or request.headers.get("X-Token")
                if auth_header:
                    token = auth_header.strip()
                    if token.lower().startswith("bearer "):
                        token = token[7:].strip()
                    try:
                        payload = verify_token(token)
                        operator_code = payload.get("code")
                        operator_name = payload.get("name") or payload.get("code")
                    except Exception:
                        pass
                # 构造可读详情
                detail_text = f"用户 {operator_code or '-'}({operator_name or '-'}) 调用 [{module}] {request.method} {request.url.path} => {response.status_code}，耗时 {elapsed}ms，查询参数: {dict(request.query_params)}"
                insert_op_log(
                    module=module,
                    op_type=request.method,
                    operator=operator_name or request.headers.get("X-User-Code") or None,
                    status=str(response.status_code),
                    detail=detail_text,
                    operator_code=operator_code or request.headers.get("X-User-Code") or None,
                )
            except Exception:
                pass
            return response
        except Exception as e:
            elapsed = round((time.time() - start) * 1000)
            logger.exception(f"trace={trace_id} method={request.method} path={request.url.path} error={e} ms={elapsed}")
            try:
                seg = request.url.path.strip("/").split("/")
                module = (seg[0] if seg else "root")
                # 解析 token 获取工号与姓名（异常路径也尽量写）
                operator_code = None
                operator_name = None
                auth_header = request.headers.get("Authorization") or request.headers.get("X-Token")
                if auth_header:
                    token = auth_header.strip()
                    if token.lower().startswith("bearer "):
                        token = token[7:].strip()
                    try:
                        payload = verify_token(token)
                        operator_code = payload.get("code")
                        operator_name = payload.get("name") or payload.get("code")
                    except Exception:
                        pass
                insert_op_log(
                    module,
                    request.method,
                    operator_name or request.headers.get("X-User-Code") or None,
                    "500",
                    {
                        "trace": trace_id,
                        "ms": elapsed,
                        "error": str(e),
                    },
                    operator_code or request.headers.get("X-User-Code") or None,
                )
            except Exception:
                pass
            raise


def _scope_header(scope: dict, name: str) -> str:
    target = name.lower().encode("latin1")
    for key, value in scope.get("headers") or []:
        if key.lower() == target:
            try:
                return value.decode("latin1")
            except Exception:
                return ""
    return ""


def _role_name_from_scope(scope: dict) -> str:
    """从本次请求的 token 取出当前会话角色。无 token 则不脱敏。"""
    auth = _scope_header(scope, "authorization")
    token = _scope_header(scope, "x-token")
    token_str = ""
    if auth:
        token_str = auth[7:].strip() if auth.lower().startswith("bearer ") else auth.strip()
    if not token_str and token:
        token_str = token.strip()
    if not token_str:
        return ""
    try:
        from app.core.token import verify_token
        payload = verify_token(token_str)
    except Exception:
        return ""
    role_name = str(payload.get("role_name") or "").strip()
    if role_name:
        return role_name
    uid = payload.get("sub")
    if not uid:
        return ""
    try:
        from app.core.session_role import apply_session_role
        from app.crud.user import get_user
        user = apply_session_role(get_user(int(uid)), payload)
    except Exception:
        return ""
    return str((user or {}).get("F_JS_SELECTED") or "").strip()


class PseudonymMiddleware:
    """在进入业务前绑定当前角色的伪名化。响应由 PseudonymJSONResponse 按该配置脱敏。"""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return
        from app.core.pseudonym import push_role_pseudonym, reset_role_pseudonym
        token = push_role_pseudonym(_role_name_from_scope(scope))
        try:
            await self.app(scope, receive, send)
        finally:
            reset_role_pseudonym(token)


