from fastapi import Header, HTTPException, Depends
from typing import Any, Dict, List
from .token import verify_token
from .settings import settings
from app.crud import user as crud_user


def get_current_payload(
    authorization: str = Header(None),
    x_token: str | None = Header(default=None, alias="X-Token"),
) -> Dict[str, Any]:
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
    return payload


def get_current_user(payload: Dict[str, Any] = Depends(get_current_payload)) -> Dict[str, Any]:
    uid = payload.get("sub")
    if not uid:
        raise HTTPException(status_code=401, detail="无效载荷")
    u = crud_user.get_user(int(uid))
    if not u:
        raise HTTPException(status_code=401, detail="用户不存在")
    return u


def get_current_user_optional(
    authorization: str = Header(None),
    x_token: str | None = Header(default=None, alias="X-Token"),
    x_dev_roles: str | None = Header(default=None, alias="X-Dev-Roles"),
):
    """调试旁路下允许无 Token 返回占位用户；正式环境仍需 Token。

    返回包含常用字段：F_ID, F_YSM, F_Ktype。"""
    # 优先标准路径
    try:
        payload = get_current_payload(authorization=authorization, x_token=x_token)
        uid = payload.get("sub")
        if uid:
            u = crud_user.get_user(int(uid))
            if u:
                return u
    except HTTPException:
        pass

    # 调试旁路：仅提供 X-Dev-Roles 时返回占位用户
    if settings.debug and x_dev_roles:
        return {"F_ID": 0, "F_YSM": "DEV", "F_Ktype": "开发者"}

    # 否则与 get_current_user 保持一致行为
    raise HTTPException(status_code=401, detail="缺少或非法的认证头")


def require_roles(required: List[str]):
    """角色鉴权依赖。

    开发模式(debug=True)时，支持通过请求头 X-Dev-Roles 模拟角色，免登录。
    生产或未提供 X-Dev-Roles 时，走标准 JWT 鉴权。
    """

    def _dep(
        authorization: str = Header(None),
        x_dev_roles: str | None = Header(default=None, alias="X-Dev-Roles"),
        x_token: str | None = Header(default=None, alias="X-Token"),
    ) -> Dict[str, Any]:
        roles_list: List[str] = []
        payload: Dict[str, Any] = {}
        # 不使用别名映射，统一以 permission_labels.json 的键为准

        # 开发旁路：允许通过 X-Dev-Roles 直接指定角色
        if settings.debug and x_dev_roles:
            roles_list = [r.strip() for r in x_dev_roles.split(",") if r and r.strip()]
            payload = {"roles": roles_list}
        else:
            # 正常路径：校验 JWT 并读取 roles（兼容 Bearer/无Bearer 与 X-Token）
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
            roles_list = list(payload.get("roles") or [])

        roles = set(roles_list)
        if not set(required).issubset(roles):
            raise HTTPException(status_code=403, detail="权限不足")
        return payload

    return _dep


