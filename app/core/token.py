"""JWT 工具：基于 HS256 签发与验证。

依赖：PyJWT（安装包名 jwt 或 pyjwt）。如果未安装，使用前请先安装：
    pip install PyJWT
"""
from __future__ import annotations

import time
import uuid
from typing import Any, Dict, List, Optional

import jwt  # type: ignore

from .settings import settings


def issue_token(user: Dict[str, Any], exp_minutes: Optional[int] = None) -> str:
    """签发 JWT。

    user 需要包含：F_ID, F_YSM, 可选 F_NAME/F_JS。
    exp_minutes 不传则使用 settings.jwt_exp_minutes。
    """
    now = int(time.time())
    exp = now + 60 * (exp_minutes or settings.jwt_exp_minutes)
    roles: List[str] = []
    # 支持直接传入合并后的权限键列表 user["roles"]；否则回退为从 F_JS 解析角色名
    if isinstance(user.get("roles"), list):
        roles = [str(r).strip() for r in user.get("roles") if str(r).strip()]
    else:
        raw_roles = user.get("F_JS")
        if isinstance(raw_roles, str):
            roles = [r.strip() for r in raw_roles.split(",") if r.strip()]
        elif isinstance(raw_roles, list):
            roles = [str(r).strip() for r in raw_roles if str(r).strip()]

    payload: Dict[str, Any] = {
        "sub": str(user.get("F_ID")),  # RFC7519 要求 sub 为字符串
        "code": user.get("F_YSM"),
        "name": user.get("F_NAME") or user.get("F_YSM"),
        "roles": roles,
        "iat": now,
        "exp": exp,
        "jti": str(uuid.uuid4()),
    }
    token = jwt.encode(payload, settings.jwt_secret, algorithm="HS256")
    # PyJWT 在新版本返回 str；老版本可能返回 bytes
    return token if isinstance(token, str) else token.decode("utf-8")


def verify_token(token: str) -> Dict[str, Any]:
    """验证 JWT 并返回载荷，过期或签名不合法会抛出异常。"""
    return jwt.decode(token, settings.jwt_secret, algorithms=["HS256"])  # type: ignore


