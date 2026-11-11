# app/routers/user.py
from typing import List
from fastapi import APIRouter, Depends
from app.schemas.common import ApiResponse
from app.schemas.user import (
    UserOut, UserCreate, UserListQuery, UserQuery, UserUpdate, UserDelete, ResetPasswordIn
)
from app.crud import user as crud
from app.core.response import ok, err
from app.core.auth import require_roles

router = APIRouter(prefix="/users", tags=["users"])

#获取用户列表
@router.post("/list", response_model=ApiResponse[List[UserOut]], response_model_exclude_none=True)
def list_users(payload: UserListQuery):
    # 兼容新/旧字段名与空入参：
    # - 新：F_YSM, F_Ktype, F_ZT
    # - 旧：keyword, status
    fy = (getattr(payload, "F_YSM", None) or getattr(payload, "keyword", None) or "").strip()
    fk = (getattr(payload, "F_Ktype", None) or "").strip()
    fj = (getattr(payload, "F_JS", None) or "").strip()
    keyword = fk or fy or None
    role = fj or None
    status = (getattr(payload, "F_ZT", None) or getattr(payload, "status", None) or "").strip() or None

    # skip/limit 也可能不存在于旧模型，安全回退
    skip = getattr(payload, "skip", 0) or 0
    limit = getattr(payload, "limit", None)

    data = crud.list_users(skip=skip, limit=limit, keyword=keyword, status=status, role=role)
    return ok(data, f"查询成功，共 {len(data)} 条")

#获取用户详情
@router.post("/detail", response_model=ApiResponse[UserOut], response_model_exclude_none=True,
             dependencies=[Depends(require_roles(["user_mgmt"]))])
def get_user_detail(payload: UserQuery):
    uid = payload.F_ID
    data = None
    if uid is not None:
        data = crud.get_user(uid)
    elif payload.F_YSM:
        data = crud.get_user_by_code(payload.F_YSM)
    if not data:
        return err("用户不存在")
    return ok(data, "查询成功")

#创建用户
@router.post("/create", response_model=ApiResponse[UserOut], response_model_exclude_none=True,
             dependencies=[Depends(require_roles(["user_mgmt"]))])
def create_user(payload: UserCreate):
    try:
        created = crud.create_user(payload)
    except ValueError as e:
        return err(str(e))
    if not created:
        return err("工号已存在")
    return ok(created, "创建成功")

#更新用户
@router.post("/update", response_model=ApiResponse[UserOut], response_model_exclude_none=True,
             dependencies=[Depends(require_roles(["user_mgmt"]))])
def update_user(payload: UserUpdate):
    try:
        updated = crud.update_user(payload.F_ID, payload)
    except ValueError as e:
        return err(str(e))
    if not updated:
        return err("用户不存在")
    return ok(updated, "更新成功")

#删除用户
@router.post("/delete", response_model=ApiResponse[dict], response_model_exclude_none=True,
             dependencies=[Depends(require_roles(["user_mgmt"]))])
def delete_user(payload: UserDelete):
    ok_flag = crud.delete_user(payload.F_ID)
    if not ok_flag:
        return err("用户不存在")
    return ok({"deleted_user_id": payload.F_ID}, "删除成功")

#重置密码
@router.post("/reset-password", response_model=ApiResponse[UserOut], response_model_exclude_none=True,
             dependencies=[Depends(require_roles(["user_mgmt"]))])
def reset_password(payload: ResetPasswordIn):
    updated = crud.reset_password(payload.F_ID)
    if not updated:
        return err("用户不存在")
    return ok(updated, "密码已重置为默认值")