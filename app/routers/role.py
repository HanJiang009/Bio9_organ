from fastapi import APIRouter, Depends
from typing import List
from app.schemas.common import ApiResponse
from app.schemas.role import RoleCreate, RoleUpdate, RoleQuery, RoleDelete, RoleListQuery, RoleUsersQuery
from app.crud import role as crud
from app.core.response import ok, err
from app.core.auth import require_roles

router = APIRouter(prefix="/roles", tags=["roles"])

# 获取角色列表
@router.post("/list", response_model=ApiResponse[List[dict]], response_model_exclude_none=True,
             dependencies=[Depends(require_roles(["role_mgmt"]))])
def list_roles(payload: RoleListQuery | None = None):
    skip = getattr(payload, "skip", 0) if payload else 0
    limit = getattr(payload, "limit", 20) if payload else 20
    name = (getattr(payload, "F_JS", None) or "").strip() if payload else None
    code = (getattr(payload, "F_JSBH", None) or "").strip() if payload else None
    status = (getattr(payload, "F_ZT", None) or "").strip() if payload else None
    name = name or None
    code = code or None
    status = status or None
    data = crud.list_roles(skip=skip, limit=limit, name=name, code=code, status=status)
    return ok(data, f"查询成功，共 {len(data)} 条")

# 获取角色详情
@router.post("/detail", response_model=ApiResponse[dict], response_model_exclude_none=True,
             dependencies=[Depends(require_roles(["role_mgmt"]))])
def role_detail(payload: RoleQuery):
    data = None
    if payload.F_ID is not None:
        data = crud.get_role(payload.F_ID)
    elif payload.F_JS:
        data = crud.get_role_by_name(payload.F_JS)
    if not data:
        return err("角色不存在")
    return ok(data, "查询成功")

# 创建角色
@router.post("/create", response_model=ApiResponse[dict], response_model_exclude_none=True,
             dependencies=[Depends(require_roles(["role_mgmt"]))])
def create_role(payload: RoleCreate):
    created = crud.create_role(payload)
    if not created:
        return err("角色已存在或数据无效")
    return ok(created, "创建成功")

# 更新角色
@router.post("/update", response_model=ApiResponse[dict], response_model_exclude_none=True,
             dependencies=[Depends(require_roles(["role_mgmt"]))])
def update_role(payload: RoleUpdate):
    updated = crud.update_role(payload.F_ID, payload)
    if not updated:
        return err("角色不存在或冲突")
    return ok(updated, "更新成功")

# 删除角色
@router.post("/delete", response_model=ApiResponse[dict], response_model_exclude_none=True,
             dependencies=[Depends(require_roles(["role_mgmt"]))])
def delete_role(payload: RoleDelete):
    ok_flag, message = crud.delete_role(payload.F_ID)
    if not ok_flag:
        return err(message or "角色不存在或被使用")
    return ok({"deleted_role_id": payload.F_ID}, "删除成功")

# 获取角色用户列表
@router.post("/users", response_model=ApiResponse[List[dict]], response_model_exclude_none=True,
             dependencies=[Depends(require_roles(["role_mgmt"]))])
def users_by_role(payload: RoleUsersQuery):
    data = crud.list_users_by_role_id(payload.F_ID)
    return ok(data, "查询成功")

# 获取角色名称列表
@router.post("/names", response_model=ApiResponse[List[str]])
def role_names(_: dict = Depends(require_roles(["role_mgmt"]))):
    names = crud.get_role_names()
    return ok(names, f"获取角色名称成功，共 {len(names)} 条")


# 根据用户ID返回有权限模块列表
@router.post("/permissions/by-user", response_model=ApiResponse[list], response_model_exclude_none=True,
             dependencies=[Depends(require_roles(["role_mgmt"]))])
def list_permissions_by_user(payload: dict):
    uid = int(payload.get("F_UID") or payload.get("user_id") or 0)
    if not uid:
        return err("缺少用户ID: F_UID")
    data = crud.get_permissions_for_user_id(uid)
    return ok(data, "查询成功")

# 获取权限树
@router.post("/permissions/tree", response_model=ApiResponse[dict], response_model_exclude_none=True,
             dependencies=[Depends(require_roles(["role_mgmt"]))])
def get_permission_tree(payload: RoleQuery):
    tree = crud.get_permission_tree(payload.F_ID)
    if tree is None:
        return err("角色不存在")
    return ok(tree, "查询成功")


# 切换权限
@router.post("/permissions/toggle", response_model=ApiResponse[dict], response_model_exclude_none=True,
             dependencies=[Depends(require_roles(["role_mgmt"]))])
def toggle_permission(payload: dict):
    role_id = int(payload.get("F_ID"))
    node_id = str(payload.get("node_id") or "").strip()
    hp_raw = payload.get("hasPermission")
    if isinstance(hp_raw, str):
        has_permission = hp_raw.strip().lower() in ("true", "1", "yes", "y", "是")
    else:
        has_permission = bool(hp_raw)
    if not node_id:
        return err("缺少节点ID")
    ok_flag = crud.set_permission(role_id, node_id, has_permission)
    if not ok_flag:
        return err("角色或节点不存在")
    return ok({"F_ID": role_id, "node_id": node_id, "hasPermission": has_permission}, "更新成功")


# 切换快捷键
@router.post("/shortcuts/toggle", response_model=ApiResponse[dict], response_model_exclude_none=True,
             dependencies=[Depends(require_roles(["role_mgmt"]))])
def toggle_shortcut(payload: dict):
    role_id = int(payload.get("F_ID"))
    node_id = str(payload.get("node_id") or "").strip()
    sc_raw = payload.get("shortcut")
    if isinstance(sc_raw, str):
        shortcut = sc_raw.strip().lower() in ("true", "1", "yes", "y", "是")
    else:
        shortcut = bool(sc_raw)
    if not node_id:
        return err("缺少节点ID")
    ok_flag, message = crud.set_shortcut(role_id, node_id, shortcut)
    if not ok_flag:
        return err(message or "更新失败")
    return ok({"F_ID": role_id, "node_id": node_id, "shortcut": shortcut}, "更新成功")

