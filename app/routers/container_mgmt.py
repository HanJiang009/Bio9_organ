from fastapi import APIRouter, Depends

from app.core.auth import require_roles, get_current_user_optional
from app.core.response import ok, err
from app.schemas.common import ApiResponse
from app.schemas.container_mgmt import (
    ContainerListQuery,
    ContainerListResponse,
    ContainerSave,
    ContainerUpdate,
    ContainerId,
    ContainerTreeQuery,
)
from app.crud import container_mgmt as crud


_auth = [Depends(require_roles(["container_mgmt"]))]
_read = [Depends(require_roles(["container_mgmt", "sample_inbound"], match="any"))]

router = APIRouter(prefix="/container-mgmt", tags=["container_mgmt"])


@router.post("/list", response_model=ApiResponse[ContainerListResponse], response_model_exclude_none=True,
             dependencies=_auth)
def list_containers(payload: ContainerListQuery):
    """容器列表。F_GG 为层×架×抽屉×盒，F_YSY 为已存放管数/总管位。有管子占用时 F_KBJ、F_KSC 为 false。"""
    try:
        data = crud.list_containers(payload.model_dump())
        return ok(data, f"查询成功，共 {data.get('total', 0)} 条")
    except ValueError as exc:
        return err(str(exc))
    except Exception as exc:
        return err(f"查询失败: {exc}")


@router.post("/enabled", response_model=ApiResponse[ContainerListResponse], response_model_exclude_none=True,
             dependencies=_read)
def list_enabled():
    """样本入库设备选择：只返回启用中的容器。"""
    try:
        data = crud.list_enabled()
        return ok(data, f"查询成功，共 {data.get('total', 0)} 条")
    except ValueError as exc:
        return err(str(exc))
    except Exception as exc:
        return err(f"查询失败: {exc}")


@router.post("/detail", response_model=ApiResponse[dict], response_model_exclude_none=True,
             dependencies=_auth)
def detail(payload: ContainerId):
    try:
        return ok(crud.get_container(payload.model_dump()), "查询成功")
    except ValueError as exc:
        return err(str(exc))
    except Exception as exc:
        return err(f"查询失败: {exc}")


@router.post("/tree", response_model=ApiResponse[dict], response_model_exclude_none=True,
             dependencies=_read)
def tree(payload: ContainerTreeQuery):
    """不传层架抽屉时返回整棵树。传 F_C、F_J、F_CT 后只返回该抽屉的盒子，每个盒子的 tubes 是已放的管子。"""
    try:
        return ok(crud.container_tree(payload.model_dump()), "查询成功")
    except ValueError as exc:
        return err(str(exc))
    except Exception as exc:
        return err(f"查询失败: {exc}")


@router.post("/create", response_model=ApiResponse[dict], response_model_exclude_none=True,
             dependencies=_auth)
def create(payload: ContainerSave, current_user: dict = Depends(get_current_user_optional)):
    try:
        return ok(crud.create_container(payload.model_dump(), current_user), "创建成功")
    except ValueError as exc:
        return err(str(exc))
    except Exception as exc:
        return err(f"创建失败: {exc}")


@router.post("/update", response_model=ApiResponse[dict], response_model_exclude_none=True,
             dependencies=_auth)
def update(payload: ContainerUpdate, current_user: dict = Depends(get_current_user_optional)):
    """没有样本占用时才能改规格。已占用的容器只能查看。"""
    try:
        return ok(crud.update_container(payload.model_dump(), current_user), "保存成功")
    except ValueError as exc:
        return err(str(exc))
    except Exception as exc:
        return err(f"保存失败: {exc}")


@router.post("/delete", response_model=ApiResponse[dict], response_model_exclude_none=True,
             dependencies=_auth)
def delete(payload: ContainerId, current_user: dict = Depends(get_current_user_optional)):
    try:
        return ok(crud.delete_container(payload.model_dump(), current_user), "删除成功")
    except ValueError as exc:
        return err(str(exc))
    except Exception as exc:
        return err(f"删除失败: {exc}")
