from typing import Any, Dict, List, Optional, Union

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from app.core.auth import get_current_user, require_roles
from app.core.data_scope import session_role_names
from app.core.pseudonym import (
    current_pseudonym,
    mask_name,
    pseudonym_field_options,
    pypinyin_available,
)
from app.core.response import err, ok
from app.crud.role import get_permission_keys_for_role_names, save_role_pseudonym
from app.schemas.common import ApiResponse

router = APIRouter(prefix="/sys-setting", tags=["sys_setting"])


class PseudonymToggleIn(BaseModel):
    F_ID: int = Field(description="要设置的角色ID")
    enabled: bool = Field(description="该角色是否开启伪名化")
    fields: Optional[Union[Dict[str, Any], List[str]]] = Field(
        default=None,
        description="勾选字段。对象按 key 更新；数组为完整勾选集合。不传则只改该角色的总开关",
    )


def _can_edit(user: dict) -> bool:
    names = session_role_names(user)
    if not names:
        return False
    return "role_mgmt" in set(get_permission_keys_for_role_names(names))


@router.get("/pseudonym", response_model=ApiResponse[dict])
def get_pseudonym_setting(current_user: dict = Depends(get_current_user)):
    """当前登录角色实际生效的伪名化，只读。改配置走角色管理接口。"""
    cfg = current_pseudonym()
    return ok(
        {
            "enabled": bool(cfg.get("enabled")),
            "role_name": cfg.get("role_name") or "",
            "can_edit": _can_edit(current_user),
            "fields": pseudonym_field_options(cfg.get("fields") or {}),
            "pypinyin_ok": pypinyin_available(),
            "preview": mask_name("张铭"),
        },
        "查询成功",
    )


@router.post("/pseudonym/toggle", response_model=ApiResponse[dict],
             dependencies=[Depends(require_roles(["role_mgmt"]))])
def toggle_pseudonym(payload: PseudonymToggleIn):
    ok_flag, message, data = save_role_pseudonym(payload.F_ID, payload.enabled, payload.fields)
    if not ok_flag:
        return err(message or "保存失败")
    return ok(data, "设置成功")
