from fastapi import APIRouter, Depends

from app.core.auth import get_current_user, require_roles
from app.core.response import err, ok
from app.crud import field_config as crud
from app.schemas.common import ApiResponse
from app.schemas.field_config import FieldConfigPageQuery, FieldConfigRuntimeQuery, FieldConfigSave


_auth = [Depends(require_roles(["field_config"]))]

router = APIRouter(prefix="/field-config", tags=["field_config"])


@router.post("/pages", response_model=ApiResponse[dict], response_model_exclude_none=True, dependencies=_auth)
def pages():
    """配置页可以管理的页面。"""
    return ok(crud.list_pages(), "查询成功")


@router.post("/detail", response_model=ApiResponse[dict], response_model_exclude_none=True, dependencies=_auth)
def detail(payload: FieldConfigPageQuery):
    """某一页的系统字段，带上已保存的显示名、显隐和必填。"""
    try:
        return ok(crud.page_detail(payload.F_YM), "查询成功")
    except ValueError as exc:
        return err(str(exc))


@router.post("/save", response_model=ApiResponse[dict], response_model_exclude_none=True, dependencies=_auth)
def save(payload: FieldConfigSave):
    """只接受目录里已有的编码。锁定项的必填不能改成否。隐藏后按不必填保存。"""
    try:
        data = crud.save_page(payload.F_YM, [item.model_dump() for item in payload.items])
        return ok(data, "保存成功")
    except ValueError as exc:
        return err(str(exc))


@router.post("/runtime", response_model=ApiResponse[dict], response_model_exclude_none=True)
def runtime(payload: FieldConfigRuntimeQuery, current_user: dict = Depends(get_current_user)):
    """业务表单读取生效配置。登录即可，不要求字段配置权限。"""
    del current_user
    try:
        pages = payload.PAGES or ([payload.F_YM] if payload.F_YM else [])
        return ok(crud.runtime(pages), "查询成功")
    except ValueError as exc:
        return err(str(exc))
