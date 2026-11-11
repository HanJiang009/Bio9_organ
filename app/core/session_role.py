"""一次只认一种角色身份。

登录默认签发第一个可选角色的可用 token；换角色走 refresh_token，JWT 写入 role_name。
数据范围与模块权限都只按该角色计算，不再并集。
"""
from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional

from .data_scope import parse_role_names


class SessionRoleError(ValueError):
    """所选角色不合法，调用方应返回业务错误码。"""


def account_role_names(user: Optional[Dict[str, Any]]) -> List[str]:
    if not user:
        return []
    return list(dict.fromkeys(parse_role_names(user.get("F_JS"))))


def parse_selected_role(raw: Any) -> Optional[str]:
    """把 F_JS_SELECTED 解析为恰好一个角色名；空则 None；多个则报错。"""
    if raw is None or raw == "" or raw == []:
        return None

    names: List[str] = []
    if isinstance(raw, list):
        names = [str(x).strip() for x in raw if str(x).strip()]
    elif isinstance(raw, str):
        s = raw.strip()
        try:
            parsed = json.loads(s)
            if isinstance(parsed, list):
                names = [str(x).strip() for x in parsed if str(x).strip()]
            elif parsed is not None and str(parsed).strip():
                names = [str(parsed).strip()]
        except Exception:
            names = [p.strip() for p in re.split(r"[,，]", s) if p.strip()]
    else:
        s = str(raw).strip()
        if s:
            names = [s]

    names = list(dict.fromkeys(names))
    if not names:
        return None
    if len(names) > 1:
        raise SessionRoleError("一次只能选择一种角色身份")
    return names[0]


def pick_session_role(
    account_roles: List[str],
    requested: Any = None,
    *,
    fallback: Optional[str] = None,
) -> str:
    """选出本次会话唯一角色。

    - 传了 F_JS_SELECTED：必须是账号已绑定角色之一，且只能一个
    - 未传：优先用 fallback（token 里的 role_name，且仍属于账号）
    - 仍没有：账号只绑一个角色则自动选用；多个则要求显式选择
    """
    account_roles = list(dict.fromkeys([r for r in (account_roles or []) if r]))
    if not account_roles:
        raise SessionRoleError("当前账号未绑定任何角色")

    selected = parse_selected_role(requested)
    if selected:
        if selected not in account_roles:
            raise SessionRoleError(f"所选角色无效或不属于当前账号: {selected}")
        return selected

    fb = str(fallback or "").strip()
    if fb:
        if fb not in account_roles:
            raise SessionRoleError("当前身份已失效，请重新选择角色")
        return fb

    if len(account_roles) == 1:
        return account_roles[0]

    raise SessionRoleError("该账号绑定了多个角色，请选择登录身份")


def apply_session_role(user: Optional[Dict[str, Any]], payload: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """把 JWT 中的当前角色写到用户对象，供 data_scope 使用。

    旧 token 无 role_name 时：仅绑一个角色则自动补上；多个则不补（兼容期内仍按绑定角色并集）。
    """
    if not user:
        return user
    role_name = str((payload or {}).get("role_name") or "").strip()
    if not role_name:
        names = account_role_names(user)
        if len(names) == 1:
            role_name = names[0]
    if role_name:
        user["F_JS_SELECTED"] = role_name
    return user


def decorate_session_user(user: Optional[Dict[str, Any]], session_role: str) -> Optional[Dict[str, Any]]:
    """登录 / 刷新 /me 返回给前端的用户视图。"""
    if not user:
        return user
    user["F_JS_ACCOUNT"] = user.get("F_JS")
    user["F_JS_SELECTED"] = session_role
    from .data_scope import attach_data_scope
    attach_data_scope(user)
    return user
