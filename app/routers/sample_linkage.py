from fastapi import APIRouter, Depends

from typing import Optional

from app.core.auth import require_roles, get_current_user_optional
from app.core.response import ok, err
from app.schemas.common import ApiResponse
from app.schemas.sample_linkage import (
    SampleLinkageQuery,
    SampleLinkageResponse,
    LinkedSampleQuery,
    LinkedSampleResponse,
    SampleAllocationRequest,
    SampleAllocationScanRequest,
    SampleAllocationPreview,
    SampleAllocationSaveRequest,
    SampleAllocationScanSaveRequest,
    SampleAllocationDraftRequest,
    SampleAllocationDraftScanRequest,
    TubeScanRequest,
)
from app.crud import sample_linkage as crud


router = APIRouter(prefix="/sample-linkage", tags=["sample_linkage"], dependencies=[Depends(require_roles(["sample_linkage"]))])


@router.post("/list", response_model=ApiResponse[SampleLinkageResponse], response_model_exclude_none=False)
def sample_linkage_list(payload: SampleLinkageQuery, current_user: dict = Depends(get_current_user_optional)):
    from app.core.data_scope import resolve_data_scope
    data = crud.get_sample_linkage_lists(
        payload.model_dump(),
        user=current_user,
        scope=resolve_data_scope(current_user, "sample_linkage"),
    )
    return ok(data, "查询成功")


@router.post("/linked-samples", response_model=ApiResponse[LinkedSampleResponse], response_model_exclude_none=False)
def linked_samples(payload: LinkedSampleQuery):
    """
    查询已保存的关联样本。

    - 只传 F_ZZID：该标本下全部关联样本
    - 只传 F_GDBH：该培养工单的关联样本
    """
    try:
        items = crud.list_linked_samples(payload.F_ZZID, payload.F_GDBH)
        return ok({"items": items}, "查询成功")
    except ValueError as exc:
        return err(str(exc))
    except Exception as exc:
        return err(f"查询失败: {exc}")


@router.post("/assign/preview", response_model=ApiResponse[SampleAllocationPreview], response_model_exclude_none=True)
def sample_allocation_preview(payload: SampleAllocationRequest):
    try:
        data = crud.preview_sample_allocation(payload.model_dump())
        warnings = data.get("capacity_warnings") or []
        msg = "；".join(warnings) if warnings else "预览成功"
        return ok(data, msg)
    except ValueError as exc:
        return err(str(exc))
    except Exception as exc:
        return err(f"预览失败: {exc}")


@router.post("/assign/preview/scan", response_model=ApiResponse[SampleAllocationPreview], response_model_exclude_none=True)
def sample_allocation_scan_preview(payload: SampleAllocationScanRequest):
    """
    手动扫描预制试管编码的分装预览。

    与 /assign/preview 功能相同，额外接收用户扫描的试管编码数组（TUBE_CODES），
    按顺序将其作为 F_SGBH 填入预览结果。

    - 编码数量 == 样本数：正常返回
    - 编码数量 != 样本数：返回提示 "样本管数量与样本数不一致（x/x）"，
      多余编码忽略，不足时对应条目的 F_SGBH 留空
    - TUBE_CODES 批内重复或库内已占用（非「未运送」）时返回错误
    """
    try:
        data = crud.preview_sample_allocation_with_scan(payload.model_dump())
        tube_warning = data.pop("warning", None)
        cap_warnings = data.get("capacity_warnings") or []
        all_warnings = ([tube_warning] if tube_warning else []) + cap_warnings
        msg = "；".join(all_warnings) if all_warnings else "预览成功"
        return ok(data, msg)
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
        skipped = data.get("skipped") or []
        msg = "保存成功"
        if skipped:
            msg = f"保存成功（跳过 {len(skipped)} 条已存在记录）"
        return ok(data, msg)
    except ValueError as exc:
        return err(str(exc))
    except Exception as exc:
        return err(f"保存失败: {exc}")


@router.post("/assign/save/scan", response_model=ApiResponse[dict], response_model_exclude_none=True)
def sample_allocation_scan_save(
    payload: SampleAllocationScanSaveRequest,
    current_user: dict = Depends(get_current_user_optional),
):
    """
    手动扫描预制试管编码的分装保存。

    与 /assign/save 功能相同，额外接收用户扫描的试管编码数组（TUBE_CODES），
    按顺序将其作为 F_SGBH 写入数据库。

    - 编码数量 == 样本数：正常保存
    - 编码数量 != 样本数：返回提示 "样本管数量与样本数不一致（x/x）"，
      多余编码忽略，缺少编码的条目跳过不保存
    """
    try:
        data = crud.save_sample_allocation_with_scan(payload.model_dump(), current_user)
        warning = data.pop("warning", None)
        msg = warning if warning else "保存成功"
        return ok(data, msg)
    except ValueError as exc:
        return err(str(exc))
    except Exception as exc:
        return err(f"保存失败: {exc}")


@router.post("/assign/draft", response_model=ApiResponse[dict], response_model_exclude_none=True)
def sample_allocation_draft(
    payload: SampleAllocationDraftRequest,
    current_user: dict = Depends(get_current_user_optional),
):
    """
    暂存分装。

    与 /assign/save 功能相同，但所有分装记录均以 F_YSZT='未运送' 写入 T_YBFZ，
    不创建运输单（T_YSXX）。适用于需要先固定盒位、后续再决定运输时机的场景。

    ## 与 /assign/save 的区别
    - 无论 FZXX 或 ITEMS 中 F_SFYS 取何值，均不发起运输

    ## 仅预建盒子（ITEMS 为空或不传）
    先创建盒子、暂不放入样本：
    ```json
    {
      "BOXES": [
        { "F_HZH": "BOX-2024-001", "F_TEMPLATE_ID": 2, "F_KSWZ": "A1" }
      ],
      "ITEMS": []
    }
    ```
    盒号已存在时幂等跳过；`F_KSWZ` 在后续放入样本时生效。

    ## 仅调整盒位（可不传 YBZJ_IDS / PYGD_IDS / FZXX）
    传 ITEMS，每条含 F_ID（或库内已有暂存的 F_SGBH）及 F_WZ / F_HZH：
    ```json
    {
      "ITEMS": [
        { "F_ID": 123, "F_WZ": "B3" },
        { "F_SGBH": "20260721F1072237", "F_WZ": "A1" }
      ],
      "BOXES": [{ "F_HZH": "BOX001", "F_TEMPLATE_ID": 1 }]
    }
    ```

    ## 后续操作
    1. 调用 `POST /box-mgmt/box/allocation` 获取该盒子的暂存分装信息（含 F_ID）
    2. 调用 `POST /assign/save` 发起运输（ITEMS 可含 F_ID，也可仅含 SGBH，
       系统会按试管编号自动识别已有「未运送」暂存记录）
    """
    try:
        data = crud.draft_sample_allocation(payload.model_dump(), current_user)
        return ok(data, "暂存成功")
    except ValueError as exc:
        return err(str(exc))
    except Exception as exc:
        return err(f"暂存失败: {exc}")


@router.post("/assign/draft/scan", response_model=ApiResponse[dict], response_model_exclude_none=True)
def sample_allocation_draft_scan(
    payload: SampleAllocationDraftScanRequest,
    current_user: dict = Depends(get_current_user_optional),
):
    """
    手动扫描预制试管编码的分装暂存。

    与 /assign/draft 功能相同，额外接收用户扫描的试管编码数组（TUBE_CODES），
    按顺序将其作为 F_SGBH 写入数据库，所有记录均以 F_YSZT='未运送' 保存，
    不创建运输单。

    - 编码数量 == 样本数：正常暂存
    - 编码数量 != 样本数：返回提示 "样本管数量与样本数不一致（x/x）"，
      多余编码忽略，缺少编码的条目跳过不保存
    """
    try:
        data = crud.draft_sample_allocation_with_scan(payload.model_dump(), current_user)
        warning = data.pop("warning", None)
        msg = warning if warning else "暂存成功"
        return ok(data, msg)
    except ValueError as exc:
        return err(str(exc))
    except Exception as exc:
        return err(f"暂存失败: {exc}")


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