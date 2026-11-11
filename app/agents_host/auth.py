from typing import Any, Dict, List

from fastapi import Header, HTTPException

from app.core.auth import _load_user_from_payload
from app.core.settings import settings
from app.core.token import verify_token


def _license_filter(roles: List[str]) -> List[str]:
    role_set = set(roles)
    try:
        from app.utils.license_ui_modules import get_licensed_module_ids

        allowed = get_licensed_module_ids()
        if allowed is not None:
            role_set &= allowed
    except Exception:
        pass
    return list(role_set)


def current_principal(
    authorization: str = Header(None),
    x_dev_roles: str | None = Header(default=None, alias="X-Dev-Roles"),
    x_token: str | None = Header(default=None, alias="X-Token"),
) -> Dict[str, Any]:
    """返回 {user, roles}。不在此强制 spec 权限，由 Runtime 按 Spec 检查。"""
    if settings.debug and x_dev_roles:
        roles = _license_filter([r.strip() for r in x_dev_roles.split(",") if r and r.strip()])
        return {
            "user": {"F_ID": 0, "F_YSM": "DEV", "F_Ktype": "开发者"},
            "roles": roles,
        }

    token_str = None
    if authorization:
        token_str = authorization[7:].strip() if authorization.startswith("Bearer ") else authorization.strip()
    if not token_str and x_token:
        token_str = x_token.strip()
    if not token_str:
        raise HTTPException(status_code=401, detail="缺少或非法的认证头")
    try:
        payload = verify_token(token_str)
    except Exception:
        raise HTTPException(status_code=401, detail="Token 无效或已过期")
    user = _load_user_from_payload(payload)
    if not user:
        raise HTTPException(status_code=401, detail="无效载荷" if not payload.get("sub") else "用户不存在")
    roles = _license_filter(list(payload.get("roles") or []))
    return {"user": user, "roles": roles}
