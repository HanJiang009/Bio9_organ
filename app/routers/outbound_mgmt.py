import json
from typing import List, Optional

from fastapi import APIRouter, Depends, Form, File, UploadFile

from app.core.auth import require_roles, get_current_user_optional
from app.core.response import ok, err
from app.core.storage import build_upload_path
from app.crud.file_center import insert_file_record
from app.schemas.common import ApiResponse
from app.schemas.outbound_mgmt import (
    OutboundListQuery,
    OutboundListResponse,
    OutboundDetailQuery,
    OutboundDetailResponse,
    OutboundResubmitRequest,
    OutboundPendingQuery,
    OutboundPendingResponse,
)
from app.crud import outbound_mgmt as crud


_auth = [Depends(require_roles(["outbound_mgmt"]))]

router = APIRouter(
    prefix="/outbound-mgmt",
    tags=["outbound_mgmt"],
)


@router.post("/apply/create", response_model=ApiResponse[dict], response_model_exclude_none=True,
             dependencies=_auth)
async def apply_create(
    CKDXX: str = Form(..., description="JSON：{YBFZ:number[], F_SQR?, F_SQSJ?, F_LXFX?, F_BZ?}"),
    F_FJ_IDS: Optional[str] = Form(default=None, description="JSON数组：已存在文件ID列表（来自 /files/upload，可选）"),
    F_FJ: Optional[List[UploadFile]] = File(default=None, description="附件文件，可多选，直传即时上传"),
    current_user: dict = Depends(get_current_user_optional),
):
    """发起出库单：从综合查询挑选在库样本，搭配出库单信息发起出库申请。

    管子保持在库，并锁定到本单，其他出库单不能再选。出库审核开启时单据为待审核，通过后才变为待出库。

    表单形式（multipart/form-data）：
    - CKDXX：出库单主体信息（JSON字符串）
    - F_FJ：附件文件，可多选，随请求直接上传（即时上传，无需先调用 /files/upload）
    - F_FJ_IDS：也支持传入已通过 /files/upload 预先上传好的文件ID（JSON数组字符串），与 F_FJ 可同时使用
    """
    try:
        payload = json.loads(CKDXX) if CKDXX else {}
    except Exception:
        return err("CKDXX 解析失败，应为JSON对象")
    if not isinstance(payload, dict):
        return err("CKDXX 解析失败，应为JSON对象")

    file_ids: List[int] = []
    if F_FJ_IDS:
        try:
            parsed = json.loads(F_FJ_IDS)
            if not isinstance(parsed, list):
                return err("F_FJ_IDS 解析失败，应为JSON数组")
            file_ids = [int(v) for v in parsed if str(v).strip()]
        except Exception:
            return err("F_FJ_IDS 解析失败，应为JSON数组")

    if F_FJ:
        for f in F_FJ:
            if not f or not f.filename:
                continue
            dst_path, rel = build_upload_path(f.filename)
            content = await f.read()
            dst_path.write_bytes(content)
            fid = insert_file_record(rel, f.filename)
            if fid is not None:
                file_ids.append(int(fid))

    payload["F_FJ_IDS"] = file_ids

    try:
        data = crud.create_outbound(payload, current_user)
        return ok(data, "创建出库单成功")
    except ValueError as exc:
        return err(str(exc))
    except Exception as exc:
        return err(f"创建出库单失败: {exc}")


@router.post("/list", response_model=ApiResponse[OutboundListResponse], response_model_exclude_none=True,
             dependencies=_auth)
def list_outbound(payload: OutboundListQuery, current_user: dict = Depends(get_current_user_optional)):
    try:
        from app.core.data_scope import resolve_data_scope
        data = crud.list_outbound(
            payload.model_dump(),
            user=current_user,
            scope=resolve_data_scope(current_user, "outbound_mgmt"),
        )
        return ok(data, f"查询成功，共 {data.get('total', 0)} 条")
    except ValueError as exc:
        return err(str(exc))
    except Exception as exc:
        return err(f"查询失败: {exc}")


@router.post("/detail", response_model=ApiResponse[OutboundDetailResponse], response_model_exclude_none=True,
             dependencies=_auth)
def outbound_detail(payload: OutboundDetailQuery, current_user: dict = Depends(get_current_user_optional)):
    try:
        data = crud.get_outbound_detail(payload.model_dump())
        from app.core.data_scope import deny_owner_access
        denied = deny_owner_access(current_user, data.get("F_SQR"), module="outbound_mgmt")
        if denied:
            return denied
        return ok(data, "查询成功")
    except ValueError as exc:
        return err(str(exc))
    except Exception as exc:
        return err(f"查询失败: {exc}")


@router.post("/resubmit", response_model=ApiResponse[dict], response_model_exclude_none=True,
             dependencies=_auth)
def resubmit(payload: OutboundResubmitRequest, current_user: dict = Depends(get_current_user_optional)):
    """针对"部分出库"的出库单，过滤已出库样本，为剩余样本重新发起出库单。"""
    try:
        data = crud.resubmit_outbound(payload.model_dump(), current_user)
        return ok(data, "已为剩余样本重新发起出库单")
    except ValueError as exc:
        return err(str(exc))
    except Exception as exc:
        return err(f"重新发起出库单失败: {exc}")


@router.post("/external/pending", response_model=ApiResponse[OutboundPendingResponse], response_model_exclude_none=True)
def external_pending(payload: OutboundPendingQuery):
    """供外部系统拉取待出库/部分出库单据（无鉴权，纯文本字段，不含附件）。"""
    try:
        data = crud.list_pending_for_external(payload.model_dump())
        return ok(data, f"查询成功，共 {data.get('total', 0)} 条")
    except ValueError as exc:
        return err(str(exc))
    except Exception as exc:
        return err(f"查询失败: {exc}")
