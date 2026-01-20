from fastapi import APIRouter, HTTPException
from typing import Optional
from ..schemas.auth import LoginIn, LoginResponse
from ..core.token import issue_token
from ..crud import user as crud_user
from ..core.response import ok, err
from ..core.auth import get_current_user, get_current_payload
from datetime import date, datetime
from ..crud.role import get_permission_keys_for_role_names

router = APIRouter(prefix="/auth", tags=["auth"])


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

    # 方案A：登录时合并权限键并写入 token.roles
    role_names = []
    raw_roles = auth.get("F_JS")
    if isinstance(raw_roles, str):
        role_names = [r.strip() for r in raw_roles.split(",") if r.strip()]
    elif isinstance(raw_roles, list):
        role_names = [str(r).strip() for r in raw_roles if str(r).strip()]
    perm_keys = get_permission_keys_for_role_names(role_names)

    token = issue_token({
        "F_ID": auth.get("F_ID"),
        "F_YSM": auth.get("F_YSM"),
        "F_NAME": auth.get("F_Ktype"),
        "F_JS": auth.get("F_JS"),
        "roles": perm_keys,
    })
    safe_user = crud_user.get_user(auth.get("F_ID"))
    return {"code": 1, "msg": "登录成功", "token": token, "user": safe_user}


from fastapi import Depends

@router.get("/me")
def me(user: dict = Depends(get_current_user)):
    return {"code": 1, "msg": "ok", "user": user}


