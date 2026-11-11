from fastapi import APIRouter, Depends
from typing import List
from app.schemas.common import ApiResponse
from app.schemas.shortcut import ShortcutCreate, ShortcutUpdate, ShortcutQuery, ShortcutListQuery
from app.crud import shortcut as crud
from app.crud import role as role_crud
from app.core.response import ok, err
from app.core.auth import require_roles

router = APIRouter(prefix="/shortcuts", tags=["shortcuts"])


@router.post("/list", response_model=ApiResponse[List[dict]], response_model_exclude_none=True,
             dependencies=[Depends(require_roles(["role_mgmt"]))])
def list_shortcuts(payload: ShortcutListQuery):
    data = crud.list_shortcuts(skip=payload.skip, limit=payload.limit)
    return ok(data)


@router.post("/save", response_model=ApiResponse[dict], response_model_exclude_none=True,
             dependencies=[Depends(require_roles(["role_mgmt"]))])
def save_shortcut(payload: ShortcutCreate):
    saved = crud.save_shortcut(payload.F_UID, payload.F_KJJS)
    if not saved:
        return err("保存失败：JSON无效或写入失败")
    return ok(saved, "保存成功")


@router.post("/user-enabled", response_model=ApiResponse[list], response_model_exclude_none=True)
def list_user_enabled_shortcuts(payload: ShortcutQuery):
    if not payload.F_UID:
        return err("缺少用户ID: F_UID")
    # 从角色权限树合并快捷键
    data = role_crud.get_shortcuts_for_user_id(payload.F_UID)
    return ok(data, "查询成功")

