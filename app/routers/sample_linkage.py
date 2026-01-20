from fastapi import APIRouter, Depends

from typing import Optional

from app.core.auth import require_roles, get_current_user_optional
from app.core.response import ok, err
from app.schemas.common import ApiResponse
from app.schemas.sample_linkage import (
    SampleLinkageQuery,
    SampleLinkageResponse,
    SampleAllocationRequest,
    SampleAllocationPreview,
    SampleAllocationSaveRequest,
    TubeScanRequest,
)
from app.crud import sample_linkage as crud


router = APIRouter(prefix="/sample-linkage", tags=["sample_linkage"], dependencies=[Depends(require_roles(["sample_linkage"]))])


@router.post("/list", response_model=ApiResponse[SampleLinkageResponse], response_model_exclude_none=False)
def sample_linkage_list(payload: SampleLinkageQuery):
    data = crud.get_sample_linkage_lists(payload.model_dump())
    return ok(data, "查询成功")


@router.post("/assign/preview", response_model=ApiResponse[SampleAllocationPreview], response_model_exclude_none=True)
def sample_allocation_preview(payload: SampleAllocationRequest):
    try:
        data = crud.preview_sample_allocation(payload.model_dump())
        return ok(data, "预览成功")
    except ValueError as exc:
        return err(str(exc))
    except Exception as exc:
        return err(f"预览失败: {exc}")


@router.post("/assign/save", response_model=ApiResponse[dict], response_model_exclude_none=True)
def sample_allocation_save(
    payload: SampleAllocationSaveRequest,
    current_user: dict = Depends(get_current_user_optional),
):
    try:
        data = crud.save_sample_allocation(payload.model_dump(), current_user)
        return ok(data, "保存成功")
    except ValueError as exc:
        return err(str(exc))
    except Exception as exc:
        return err(f"保存失败: {exc}")


@router.post("/transport/scan", response_model=ApiResponse[dict], response_model_exclude_none=True)
def scan_tube(payload: TubeScanRequest):
    """
    扫码查询样本管详情
    
    根据样本管编号（F_SGBH）查询样本分装详情，并检查是否已关联运输单。
    
    ## 请求示例
    ```json
    {
      "F_SGBH": "S202612131212-BB-20260112154030-5"
    }
    ```
    
    ## 返回值
    ```json
    {
      "tube_info": {
        "F_ID": 123,
        "F_SGBH": "SAMPLE001-BB-20260116-1",
        "F_ZZID": "SAMPLE001",
        "F_YSZT": "运送中",
        ...
      },
      "is_transported": true,
      "transport_info": {
        "F_YSDH": "YSDH20260116...",
        "F_FQR": "张三",
        "F_FQSJ": "2026-01-16 14:30:00",
        "F_YSDZT": "运送中"
      }
    }
    ```
    
    ## 业务逻辑
    - `is_transported = false`: 样本管未关联运输单，可以加入新运输单
    - `is_transported = true`: 样本管已关联运输单，返回运输单信息供用户确认
    """
    try:
        sgbh = payload.F_SGBH
        data = crud.get_tube_by_sgbh(sgbh)
        
        if data.get("is_transported"):
            # 已关联运输单，返回警告信息
            transport_info = data.get("transport_info") or {}
            ysdh = transport_info.get("F_YSDH", "未知")
            msg = f"该样本管已关联运输单 {ysdh}，请确认是否需要操作"
            return ok(data, msg)
        else:
            # 未关联运输单，正常返回
            return ok(data, "查询成功")
            
    except ValueError as exc:
        return err(str(exc))
    except Exception as exc:
        return err(f"查询失败: {exc}")