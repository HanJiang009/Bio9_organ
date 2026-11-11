"""
字典管理模块路由
"""
from fastapi import APIRouter, Depends
from app.schemas.common import ApiResponse
from app.schemas.dict import (
    DictCategoryCreate,
    DictCategoryUpdate,
    DictCategoryOut,
    DictCategoryListQuery,
    DictCategoryListOut,
    DictItemCreate,
    DictItemUpdate,
    DictItemOut,
    DictItemListQuery,
    DictItemListOut,
    DictItemBatchQuery,
    DictItemBatchOut,
    DictItemSortRequest,
    DictItemBatchCreateRequest,
    DictItemBatchCreateOut,
    DictItemBatchUpdateRequest,
    DictItemBatchUpdateOut,
    DictDeleteRequest,
)
from app.core.response import ok, err
from app.core.auth import require_roles, get_current_user
from app.crud import dict as crud


router = APIRouter(prefix="/dict", tags=["dict"])


# ============================================
# 字典类别相关接口
# ============================================

@router.post(
    "/category/list",
    response_model=ApiResponse[DictCategoryListOut],
    response_model_exclude_none=True,
)
async def list_categories(query: DictCategoryListQuery):
    """查询字典类别列表（所有用户可访问）"""
    try:
        result = crud.list_dict_categories(query.model_dump())
        return ok(result)
    except Exception as e:
        return err(str(e))


@router.post(
    "/category/create",
    response_model=ApiResponse[DictCategoryOut],
    response_model_exclude_none=True,
    dependencies=[Depends(require_roles(["dict_mgmt"]))],
)
async def create_category(
    data: DictCategoryCreate,
    current_user: dict = Depends(get_current_user)
):
    """创建字典类别（需鉴权）"""
    try:
        category_id = crud.create_dict_category(data.model_dump())
        category = crud.get_dict_category_by_id(category_id)
        return ok(category, "创建成功")
    except ValueError as e:
        return err(str(e))
    except Exception as e:
        return err(f"创建失败: {str(e)}")


@router.post(
    "/category/update",
    response_model=ApiResponse[DictCategoryOut],
    response_model_exclude_none=True,
    dependencies=[Depends(require_roles(["dict_mgmt"]))],
)
async def update_category(
    data: DictCategoryUpdate,
    current_user: dict = Depends(get_current_user)
):
    """更新字典类别（需鉴权）"""
    try:
        category_id = data.F_ID
        # 过滤掉 None 值和 F_ID
        update_data = {k: v for k, v in data.model_dump().items() if v is not None and k != 'F_ID'}
        crud.update_dict_category(category_id, update_data)
        category = crud.get_dict_category_by_id(category_id)
        return ok(category, "更新成功")
    except ValueError as e:
        return err(str(e))
    except Exception as e:
        return err(f"更新失败: {str(e)}")


@router.post(
    "/category/delete",
    response_model=ApiResponse[dict],
    response_model_exclude_none=True,
    dependencies=[Depends(require_roles(["dict_mgmt"]))],
)
async def delete_category(
    request: DictDeleteRequest,
    current_user: dict = Depends(get_current_user)
):
    """删除字典类别（需鉴权，会级联删除相关字典项）"""
    try:
        crud.delete_dict_category(request.F_ID)
        return ok({"F_ID": request.F_ID}, "删除成功")
    except ValueError as e:
        return err(str(e))
    except Exception as e:
        return err(f"删除失败: {str(e)}")


# ============================================
# 字典项相关接口
# ============================================

@router.post(
    "/items/list",
    response_model=ApiResponse[DictItemListOut],
    response_model_exclude_none=True,
)
async def list_items(query: DictItemListQuery):
    """查询字典项列表（所有用户可访问）。F_CATEGORY_CODE 和 F_NAME 均可选，均不填时返回全部"""
    try:
        enabled_only = query.F_ENABLED == 1 if query.F_ENABLED is not None else False
        result = crud.list_dict_items(
            category_code=query.F_CATEGORY_CODE or None,
            name=query.F_NAME or None,
            enabled_only=enabled_only,
            skip=query.skip,
            limit=query.limit
        )
        return ok(result)
    except Exception as e:
        return err(str(e))


@router.get(
    "/items/batch",
    response_model=ApiResponse[DictItemBatchOut],
    response_model_exclude_none=True,
)
async def get_items_batch(
    category_codes: str,
    enabled_only: bool = True
):
    """
    批量获取多个类别的字典项（所有用户可访问）
    
    Args:
        category_codes: 类别代码列表，逗号分隔，如 "YWLX,CCLX,XB"
        enabled_only: 是否只返回启用的项，默认 true
    
    Returns:
        字典数据，key为类别代码
    """
    try:
        codes = [c.strip() for c in category_codes.split(",") if c.strip()]
        if not codes:
            return err("category_codes 不能为空")
        
        result = crud.get_dict_items_batch(codes, enabled_only)
        return ok({"data": result})
    except Exception as e:
        return err(str(e))


@router.post(
    "/item/create",
    response_model=ApiResponse[DictItemOut],
    response_model_exclude_none=True,
    dependencies=[Depends(require_roles(["dict_mgmt"]))],
)
async def create_item(
    data: DictItemCreate,
    current_user: dict = Depends(get_current_user)
):
    """创建字典项（需鉴权）"""
    try:
        item_id = crud.create_dict_item(data.model_dump())
        item = crud.get_dict_item_by_id(item_id)
        return ok(item, "创建成功")
    except ValueError as e:
        return err(str(e))
    except Exception as e:
        return err(f"创建失败: {str(e)}")


@router.post(
    "/item/update",
    response_model=ApiResponse[DictItemOut],
    response_model_exclude_none=True,
    dependencies=[Depends(require_roles(["dict_mgmt"]))],
)
async def update_item(
    data: DictItemUpdate,
    current_user: dict = Depends(get_current_user)
):
    """更新字典项（需鉴权）"""
    try:
        item_id = data.F_ID
        # 过滤掉 None 值和 F_ID
        update_data = {k: v for k, v in data.model_dump().items() if v is not None and k != 'F_ID'}
        crud.update_dict_item(item_id, update_data)
        item = crud.get_dict_item_by_id(item_id)
        return ok(item, "更新成功")
    except ValueError as e:
        return err(str(e))
    except Exception as e:
        return err(f"更新失败: {str(e)}")


@router.post(
    "/item/delete",
    response_model=ApiResponse[dict],
    response_model_exclude_none=True,
    dependencies=[Depends(require_roles(["dict_mgmt"]))],
)
async def delete_item(
    request: DictDeleteRequest,
    current_user: dict = Depends(get_current_user)
):
    """删除字典项（需鉴权）"""
    try:
        crud.delete_dict_item(request.F_ID)
        return ok({"F_ID": request.F_ID}, "删除成功")
    except ValueError as e:
        return err(str(e))
    except Exception as e:
        return err(f"删除失败: {str(e)}")


@router.post(
    "/items/sort",
    response_model=ApiResponse[dict],
    response_model_exclude_none=True,
    dependencies=[Depends(require_roles(["dict_mgmt"]))],
)
async def sort_items(
    request: DictItemSortRequest,
    current_user: dict = Depends(get_current_user)
):
    """批量更新字典项排序（需鉴权）"""
    try:
        sort_items = [item.model_dump() for item in request.items]
        crud.batch_update_dict_item_sort(request.F_CATEGORY_CODE, sort_items)
        return ok({}, "排序更新成功")
    except ValueError as e:
        return err(str(e))
    except Exception as e:
        return err(f"排序更新失败: {str(e)}")


@router.post(
    "/items/batch-create",
    response_model=ApiResponse[DictItemBatchCreateOut],
    response_model_exclude_none=True,
    dependencies=[Depends(require_roles(["dict_mgmt"]))],
)
async def batch_create_items(
    request: DictItemBatchCreateRequest,
    current_user: dict = Depends(get_current_user)
):
    """
    批量创建字典项（需鉴权）
    
    **功能说明：**
    - 批量创建同一类别下的多个字典项
    - 如果某些项创建失败，其他项仍会继续创建
    - 返回成功和失败的统计信息
    
    **请求示例：**
    ```json
    {
      "F_CATEGORY_CODE": "YWLX",
      "items": [
        {"F_VALUE": "新药物类型1", "F_CODE": "NEW1", "F_SORT": 10},
        {"F_VALUE": "新药物类型2", "F_CODE": "NEW2", "F_SORT": 11}
      ]
    }
    ```
    """
    try:
        items_data = [item.model_dump() for item in request.items]
        result = crud.batch_create_dict_items(request.F_CATEGORY_CODE, items_data)
        
        if result["failed_count"] > 0:
            msg = f"批量创建完成：成功 {result['success_count']} 项，失败 {result['failed_count']} 项"
        else:
            msg = f"批量创建成功：共 {result['success_count']} 项"
        
        return ok(result, msg)
    except ValueError as e:
        return err(str(e))
    except Exception as e:
        return err(f"批量创建失败: {str(e)}")


@router.post(
    "/items/batch-update",
    response_model=ApiResponse[DictItemBatchUpdateOut],
    response_model_exclude_none=True,
    dependencies=[Depends(require_roles(["dict_mgmt"]))],
)
async def batch_update_items(
    request: DictItemBatchUpdateRequest,
    current_user: dict = Depends(get_current_user)
):
    """
    批量更新字典项（需鉴权）
    
    **功能说明：**
    - 批量更新多个字典项的信息
    - 每个项必须包含 F_ID
    - 如果某些项更新失败，其他项仍会继续更新
    - 返回成功和失败的统计信息
    
    **请求示例：**
    ```json
    {
      "items": [
        {"F_ID": 1, "F_VALUE": "更新后的值1", "F_ENABLED": 1},
        {"F_ID": 2, "F_SORT": 20, "F_ENABLED": 0}
      ]
    }
    ```
    """
    try:
        items_data = [item.model_dump() for item in request.items]
        result = crud.batch_update_dict_items(items_data)
        
        if result["failed_count"] > 0:
            msg = f"批量更新完成：成功 {result['success_count']} 项，失败 {result['failed_count']} 项"
        else:
            msg = f"批量更新成功：共 {result['success_count']} 项"
        
        return ok(result, msg)
    except ValueError as e:
        return err(str(e))
    except Exception as e:
        return err(f"批量更新失败: {str(e)}")
