from fastapi import Header, HTTPException, Depends
from typing import Any, Dict, List, Optional
from .token import verify_token
from .settings import settings
from .session_role import apply_session_role
from app.crud import user as crud_user


def _load_user_from_payload(payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    uid = payload.get("sub")
    if not uid:
        return None
    u = crud_user.get_user(int(uid))
    if not u:
        return None
    return apply_session_role(u, payload)


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
    u = _load_user_from_payload(payload)
    if not u:
        raise HTTPException(status_code=401, detail="无效载荷" if not payload.get("sub") else "用户不存在")
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
        u = _load_user_from_payload(payload)
        if u:
            return u
    except HTTPException:
        pass

    # 调试旁路：仅提供 X-Dev-Roles 时返回占位用户
    if settings.debug and x_dev_roles:
        return {"F_ID": 0, "F_YSM": "DEV", "F_Ktype": "开发者"}

    # 否则与 get_current_user 保持一致行为
    raise HTTPException(status_code=401, detail="缺少或非法的认证头")


def get_current_user_silent(
    authorization: str = Header(None),
    x_token: str | None = Header(default=None, alias="X-Token"),
):
    """完全免鉴权的用户解析：有合法 Token 则返回真实用户，没有/无效 Token 时返回 None 而不报错。

    用于外部系统对接的接口（如状态推送回调）：外部系统的身份不由本系统管理，
    不能强制要求登录态，但如果调用方恰好带了有效 Token，仍然记录下真实操作人，
    便于审计与排查问题。
    """
    try:
        payload = get_current_payload(authorization=authorization, x_token=x_token)
        u = _load_user_from_payload(payload)
        if u:
            return u
    except Exception:
        pass
    return None


def require_roles(required: List[str], *, match: str = "all"):
    """角色鉴权依赖。

    开发模式(debug=True)时，支持通过请求头 X-Dev-Roles 模拟角色，免登录。
    生产或未提供 X-Dev-Roles 时，走标准 JWT 鉴权。
    match=all 时要具备全部模块；match=any 时具备其中一个即可。
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
        try:
            from app.utils.license_ui_modules import get_licensed_module_ids
            allowed = get_licensed_module_ids()
            if allowed is not None:
                roles &= allowed
                roles |= (set(roles_list) & {"home"})
        except Exception:
            pass
        needed = set(required)
        allowed_by_role = needed.issubset(roles) if match != "any" else bool(needed & roles)
        if not allowed_by_role:
            raise HTTPException(status_code=403, detail="权限不足")
        return payload

    return _dep


