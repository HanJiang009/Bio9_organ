from fastapi import APIRouter, Depends

from app.core.auth import require_roles, get_current_user_optional
from app.core.response import ok, err
from app.schemas.common import ApiResponse
from app.schemas.unified_search import (
    UnifiedSearchQuery,
    UnifiedSearchList,
    SampleDetailQuery,
    SetSampleOrderIn,
)
from app.crud import unified_search as crud


router = APIRouter(
    prefix="/unified-search",
    tags=["unified_search"],
    dependencies=[Depends(require_roles(["unified_search"]))],
)


@router.post("/list", response_model=ApiResponse[UnifiedSearchList], response_model_exclude_none=False)
def list_unified(payload: UnifiedSearchQuery, current_user: dict = Depends(get_current_user_optional)):
    from app.core.data_scope import resolve_data_scope
    data = crud.list_ybfz(
        payload.model_dump(),
        user=current_user,
        scope=resolve_data_scope(current_user, "unified_search"),
    )
    return ok(data, f"查询成功，共 {data.get('total', 0)} 条")


@router.post("/sample/detail", response_model=ApiResponse[dict], response_model_exclude_none=False)
def get_sample_detail(payload: SampleDetailQuery, current_user: dict = Depends(get_current_user_optional)):
    """
    通过 F_ID 获取样本详情
    """
    try:
        data = crud.get_sample_detail_by_id(payload.model_dump())
        from app.core.data_scope import (
            is_self_scope, resolve_data_scope, is_owner,
            fetch_ybzj_djr_by_zzid, access_denied_response,
        )
        if is_self_scope(resolve_data_scope(current_user, "unified_search")):
            if not (
                is_owner(current_user, data.get("F_PYR"))
                or is_owner(current_user, fetch_ybzj_djr_by_zzid(data.get("F_ZZID")))
            ):
                return access_denied_response()
        return ok(data, "查询成功")
    except ValueError as exc:
        return err(str(exc))
    except Exception as exc:
        return err(f"查询失败: {exc}")


@router.post("/set-order", response_model=ApiResponse[dict], response_model_exclude_none=False)
def set_sample_order(payload: SetSampleOrderIn):
    """
    设置样本自定义序号 F_ZDYXH。

    - 传入正整数：更新为该序号
    - 传入 -1：清除序号（置为 NULL）
    - 序号影响综合查询列表的排序，有序号的排在前面，无序号的按 F_ID 倒序兜底
    """
    try:
        result = crud.set_sample_order(payload.F_ID, payload.F_ZDYXH)
        return ok(result, "设置成功")
    except ValueError as exc:
        return err(str(exc))
    except Exception as exc:
        return err(f"操作失败: {exc}")
