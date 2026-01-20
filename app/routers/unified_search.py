from fastapi import APIRouter, Depends

from app.core.auth import require_roles
from app.core.response import ok, err
from app.schemas.common import ApiResponse
from app.schemas.unified_search import UnifiedSearchQuery, UnifiedSearchList, SampleDetailQuery
from app.crud import unified_search as crud


router = APIRouter(
    prefix="/unified-search",
    tags=["unified_search"],
    dependencies=[Depends(require_roles(["unified_search"]))],
)


@router.post("/list", response_model=ApiResponse[UnifiedSearchList], response_model_exclude_none=False)
def list_unified(payload: UnifiedSearchQuery):
    data = crud.list_ybfz(payload.model_dump())
    return ok(data, f"查询成功，共 {data.get('total', 0)} 条")


@router.post("/sample/detail", response_model=ApiResponse[dict], response_model_exclude_none=False)
def get_sample_detail(payload: SampleDetailQuery):
    """
    通过 F_ID 获取样本详情
    """
    try:
        data = crud.get_sample_detail_by_id(payload.model_dump())
        return ok(data, "查询成功")
    except ValueError as exc:
        return err(str(exc))
    except Exception as exc:
        return err(f"查询失败: {exc}")

