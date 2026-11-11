from fastapi import APIRouter, Body, Depends
from typing import Any, Dict, Optional
from datetime import date, datetime

from ..schemas.auth import LoginIn, LoginResponse, RoleOptionsQuery, RefreshTokenIn
from ..core.token import issue_token
from ..crud import user as crud_user
from ..core.response import ok, err
from ..core.auth import get_current_user, get_current_payload
from ..crud.role import get_permission_keys_for_role_names, permission_names_for_keys
from ..core.session_role import (
    SessionRoleError,
    account_role_names,
    pick_session_role,
    decorate_session_user,
)

router = APIRouter(prefix="/auth", tags=["auth"])


def _role_options(user: Dict[str, Any]) -> list:
    return [{"value": r, "label": r} for r in account_role_names(user)]


def _issue_session_token(user: Dict[str, Any], session_role: str, perm_keys: Optional[list] = None) -> str:
    if perm_keys is None:
        perm_keys = get_permission_keys_for_role_names([session_role])
    return issue_token({
        "F_ID": user.get("F_ID"),
        "F_YSM": user.get("F_YSM"),
        "F_NAME": user.get("F_Ktype"),
        "roles": perm_keys,
        "role_name": session_role,
    })


@router.post("/roles/options")
def role_options(query: RoleOptionsQuery):
    """登录前按工号或用户 ID 返回可选角色，无需 Token。"""
    user = None
    if query.F_YSM:
        user = crud_user.get_user_by_code(query.F_YSM)
    elif query.F_ID:
        user = crud_user.get_user(query.F_ID)
    else:
        return err("请提供 F_YSM 或 F_ID")
    if not user:
        return err("用户不存在")
    roles = account_role_names(user)
    data = [{"value": r, "label": r} for r in roles]
    return ok(data, "角色选项获取成功")


@router.post("/login", response_model=LoginResponse, response_model_exclude_none=True)
def login(payload: LoginIn):
    if not payload.F_MM2 and not payload.F_MM:
        return {"code": 0, "msg": "请提供 F_MM 或 F_MM2", "token": ""}

    u = crud_user.get_user_by_code(payload.F_YSM)
    if not u:
        return {"code": 0, "msg": "用户不存在", "token": ""}

    auth = crud_user.get_user_for_auth(payload.F_YSM)
    if not auth:
        return {"code": 0, "msg": "认证信息缺失", "token": ""}

    # 启用状态校验
    zt = str(auth.get("F_ZT") or "").strip().lower()
    enabled_vals = {"true", "启用", "1", "yes", "y", "ys"}
    if zt and zt not in enabled_vals:
        return {"code": 0, "msg": "账号已停用", "token": ""}

    # 有效期校验：到期日包含当天，过期判定为 F_YXQ < 今天
    fyxq_raw = str(auth.get("F_YXQ") or "").strip()
    if fyxq_raw:
        try:
            fyxq = datetime.strptime(fyxq_raw, "%Y-%m-%d").date()
            if fyxq < date.today():
                return {"code": 0, "msg": "账号已过期", "token": ""}
        except Exception:
            # 无法解析则不阻断登录，但建议后续清洗数据
            pass

    ok_pwd = False
    if payload.F_MM2:
        ok_pwd = payload.F_MM2.lower() == str(auth.get("F_MM2", "")).lower()
    elif payload.F_MM:
        ok_pwd = crud_user.verify_password(payload.F_MM, auth.get("F_MM2"))

    if not ok_pwd:
        return {"code": 0, "msg": "账号或密码错误", "token": ""}

    role_names = account_role_names(auth)
    if not role_names:
        return {"code": 0, "msg": "当前账号未绑定任何角色", "token": ""}

    session_role = role_names[0]
    from ..core.pseudonym import bind_role_pseudonym
    bind_role_pseudonym(session_role)
    perm_keys = get_permission_keys_for_role_names([session_role])
    token = _issue_session_token(auth, session_role, perm_keys)
    safe_user = crud_user.get_user(auth.get("F_ID"))
    if safe_user:
        decorate_session_user(safe_user, session_role)
    return {
        "code": 1,
        "msg": "登录成功",
        "token": token,
        "user": safe_user,
        "F_JS_SELECTED": session_role,
        "role_options": _role_options(auth),
        "roles": permission_names_for_keys(perm_keys),
    }


@router.post("/refresh_token")
def refresh_token(
    payload: Dict[str, Any] = Depends(get_current_payload),
    body: Optional[RefreshTokenIn] = Body(default=None),
):
    """切换身份，或按库中最新权限重签 JWT。

    登录已按第一个可选角色签发可用 token。首页换角色时传 F_JS_SELECTED；
    不传则沿用 token 中的当前角色并重读权限。
    """
    uid = payload.get("sub")
    if not uid:
        return err("无效登录态")
    try:
        user_id = int(uid)
    except (TypeError, ValueError):
        return err("无效用户标识")

    user_info = crud_user.get_user(user_id)
    if not user_info:
        return err("用户不存在")

    requested = body.F_JS_SELECTED if body else None
    try:
        session_role = pick_session_role(
            account_role_names(user_info),
            requested,
            fallback=payload.get("role_name"),
        )
    except SessionRoleError as e:
        return err(str(e))

    from ..core.pseudonym import bind_role_pseudonym
    bind_role_pseudonym(session_role)
    token = _issue_session_token(user_info, session_role)
    decorate_session_user(user_info, session_role)
    perm_keys = get_permission_keys_for_role_names([session_role])
    return {
        "code": 1,
        "msg": "token 刷新成功",
        "token": token,
        "F_JS_SELECTED": session_role,
        "roles": permission_names_for_keys(perm_keys),
        "user": user_info,
    }


@router.get("/me")
def me(user: dict = Depends(get_current_user), payload: dict = Depends(get_current_payload)):
    session_role = str(user.get("F_JS_SELECTED") or payload.get("role_name") or "").strip()
    if session_role:
        decorate_session_user(user, session_role)
    else:
        from app.core.data_scope import attach_data_scope
        attach_data_scope(user)
    return {"code": 1, "msg": "ok", "user": user}
