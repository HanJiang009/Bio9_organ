import logging
from typing import Any

from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)


class PseudonymJSONResponse(JSONResponse):
    """所有 JSON 响应在序列化前走伪名化，不依赖中间件是否读到 body。"""

    def __init__(self, content: Any = None, status_code: int = 200, headers=None, **kwargs):
        from app.core.pseudonym import apply_pseudonym, is_pseudonym_enabled

        enabled = False
        try:
            enabled = is_pseudonym_enabled()
            if enabled:
                content = apply_pseudonym(content, True)
        except Exception:
            logger.exception("pseudonym JSON 序列化失败，返回原始内容")
        merged = dict(headers or {})
        merged["X-Pseudonym"] = "1" if enabled else "0"
        super().__init__(content, status_code=status_code, headers=merged, **kwargs)


def ok(data=None, msg: str = ""):
    from app.core.pseudonym import apply_pseudonym
    return {"code": 1, "msg": msg, "data": apply_pseudonym(data)}

def err(msg: str, code: int = 0, data=None):
    return {"code": code, "msg": msg, "data": data}