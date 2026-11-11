"""
采集标准模块路由

子模块：
- 项目信息维护（占位符模块）：/sampling-standard/project/*
- 采集模板：/sampling-standard/template/*
"""
from fastapi import APIRouter, Depends
from app.schemas.common import ApiResponse
from app.schemas.sampling_standard import (
    ProjectCreate,
    ProjectUpdate,
    ProjectOut,
    ProjectListQuery,
    ProjectListOut,
    ProjectDeleteRequest,
    TemplateCreate,
    TemplateUpdate,
    TemplateOut,
    TemplateListQuery,
    TemplateListOut,
    TemplateGetRequest,
    TemplateDeleteRequest,
    TemplateItemAddRequest,
    TemplateItemUpdateRequest,
    TemplateItemDeleteRequest,
)
from app.core.response import ok, err
from app.core.auth import require_roles, get_current_user
from app.crud import sampling_standard as crud


router = APIRouter(prefix="/sampling-standard", tags=["sampling_standard"])


# ============================================
# 项目信息维护相关接口
# ============================================

@router.post(
    "/project/list",
    response_model=ApiResponse[ProjectListOut],
    response_model_exclude_none=True,
)
async def list_projects(query: ProjectListQuery):
    """查询项目信息列表（所有用户可访问）"""
    try:
        result = crud.list_projects(query.model_dump())
        return ok(result)
    except Exception as e:
        return err(str(e))


@router.post(
    "/project/create",
    response_model=ApiResponse[ProjectOut],
    response_model_exclude_none=True,
    dependencies=[Depends(require_roles(["sampling_standard"]))],
)
async def create_project(
    data: ProjectCreate,
    current_user: dict = Depends(get_current_user),
):
    """创建项目信息（需鉴权）"""
    try:
        project_id = crud.create_project(data.model_dump())
        project = crud.get_project_by_id(project_id)
        return ok(project, "创建成功")
    except ValueError as e:
        return err(str(e))
    except Exception as e:
        return err(f"创建失败: {str(e)}")


@router.post(
    "/project/update",
    response_model=ApiResponse[ProjectOut],
    response_model_exclude_none=True,
    dependencies=[Depends(require_roles(["sampling_standard"]))],
)
async def update_project(
    data: ProjectUpdate,
    current_user: dict = Depends(get_current_user),
):
    """更新项目信息（需鉴权）"""
    try:
        project_id = data.F_ID
        update_data = {k: v for k, v in data.model_dump().items() if v is not None and k != "F_ID"}
        crud.update_project(project_id, update_data)
        project = crud.get_project_by_id(project_id)
        return ok(project, "更新成功")
    except ValueError as e:
        return err(str(e))
    except Exception as e:
        return err(f"更新失败: {str(e)}")


@router.post(
    "/project/delete",
    response_model=ApiResponse[dict],
    response_model_exclude_none=True,
    dependencies=[Depends(require_roles(["sampling_standard"]))],
)
async def delete_project(
    request: ProjectDeleteRequest,
    current_user: dict = Depends(get_current_user),
):
    """删除项目信息（需鉴权）"""
    try:
        crud.delete_project(request.F_ID)
        return ok({"F_ID": request.F_ID}, "删除成功")
    except ValueError as e:
        return err(str(e))
    except Exception as e:
        return err(f"删除失败: {str(e)}")


# ============================================
# 采集模板相关接口
# ============================================

@router.post(
    "/template/list",
    response_model=ApiResponse[TemplateListOut],
    response_model_exclude_none=True,
)
async def list_templates(query: TemplateListQuery):
    """查询采集模板列表（所有用户可访问）"""
    try:
        result = crud.list_templates(query.model_dump())
        return ok(result)
    except Exception as e:
        return err(str(e))


@router.post(
    "/template/get",
    response_model=ApiResponse[TemplateOut],
    response_model_exclude_none=True,
)
async def get_template(request: TemplateGetRequest):
    """按ID查询单条采集模板及其明细（所有用户可访问）"""
    try:
        template = crud.get_template_by_id(request.F_ID)
        if not template:
            return err(f"模板ID {request.F_ID} 不存在")
        return ok(template, "查询成功")
    except Exception as e:
        return err(str(e))


@router.post(
    "/template/create",
    response_model=ApiResponse[TemplateOut],
    response_model_exclude_none=True,
    dependencies=[Depends(require_roles(["sampling_standard"]))],
)
async def create_template(
    data: TemplateCreate,
    current_user: dict = Depends(get_current_user),
):
    """创建采集模板（整条记录，需鉴权）"""
    try:
        template_id = crud.create_template(data.model_dump())
        template = crud.get_template_by_id(template_id)
        return ok(template, "创建成功")
    except ValueError as e:
        return err(str(e))
    except Exception as e:
        return err(f"创建失败: {str(e)}")


@router.post(
    "/template/update",
    response_model=ApiResponse[TemplateOut],
    response_model_exclude_none=True,
    dependencies=[Depends(require_roles(["sampling_standard"]))],
)
async def update_template(
    data: TemplateUpdate,
    current_user: dict = Depends(get_current_user),
):
    """更新采集模板（整条记录：改名和/或整体替换明细，需鉴权）"""
    try:
        template_id = data.F_ID
        update_data = data.model_dump(exclude_none=True, exclude={"F_ID"})
        crud.update_template(template_id, update_data)
        template = crud.get_template_by_id(template_id)
        return ok(template, "更新成功")
    except ValueError as e:
        return err(str(e))
    except Exception as e:
        return err(f"更新失败: {str(e)}")


@router.post(
    "/template/delete",
    response_model=ApiResponse[dict],
    response_model_exclude_none=True,
    dependencies=[Depends(require_roles(["sampling_standard"]))],
)
async def delete_template(
    request: TemplateDeleteRequest,
    current_user: dict = Depends(get_current_user),
):
    """删除采集模板（整条记录，需鉴权）"""
    try:
        crud.delete_template(request.F_ID)
        return ok({"F_ID": request.F_ID}, "删除成功")
    except ValueError as e:
        return err(str(e))
    except Exception as e:
        return err(f"删除失败: {str(e)}")


@router.post(
    "/template/item/add",
    response_model=ApiResponse[dict],
    response_model_exclude_none=True,
    dependencies=[Depends(require_roles(["sampling_standard"]))],
)
async def add_template_item(
    request: TemplateItemAddRequest,
    current_user: dict = Depends(get_current_user),
):
    """精准新增一条模板明细（需鉴权）"""
    try:
        item_data = request.model_dump(exclude={"F_ID"})
        new_item = crud.add_template_item(request.F_ID, item_data)
        return ok(new_item, "新增成功")
    except ValueError as e:
        return err(str(e))
    except Exception as e:
        return err(f"新增失败: {str(e)}")


@router.post(
    "/template/item/update",
    response_model=ApiResponse[dict],
    response_model_exclude_none=True,
    dependencies=[Depends(require_roles(["sampling_standard"]))],
)
async def update_template_item(
    request: TemplateItemUpdateRequest,
    current_user: dict = Depends(get_current_user),
):
    """精准修改一条模板明细（仅覆盖传入的非空字段，需鉴权）"""
    try:
        patch = request.model_dump(exclude={"F_ID", "F_ITEM_ID"})
        updated_item = crud.update_template_item(request.F_ID, request.F_ITEM_ID, patch)
        return ok(updated_item, "更新成功")
    except ValueError as e:
        return err(str(e))
    except Exception as e:
        return err(f"更新失败: {str(e)}")


@router.post(
    "/template/item/delete",
    response_model=ApiResponse[dict],
    response_model_exclude_none=True,
    dependencies=[Depends(require_roles(["sampling_standard"]))],
)
async def delete_template_item(
    request: TemplateItemDeleteRequest,
    current_user: dict = Depends(get_current_user),
):
    """精准删除一条模板明细（需鉴权）"""
    try:
        crud.delete_template_item(request.F_ID, request.F_ITEM_ID)
        return ok({"F_ID": request.F_ID, "F_ITEM_ID": request.F_ITEM_ID}, "删除成功")
    except ValueError as e:
        return err(str(e))
    except Exception as e:
        return err(f"删除失败: {str(e)}")
