from datetime import datetime

from fastapi import APIRouter, Depends

from app.core.auth import require_roles, get_current_user_optional, get_current_user_silent
from app.core.response import ok, err
from app.schemas.common import ApiResponse
from app.schemas.sample_linkage import (
    TransportCreateRequest,
    TransportListQuery,
    TransportListResponse,
    TransportDetailQuery,
    TransportDetailResponse,
    ExternalPushSyncRequest,
    ExternalPushAckResponse,
    SampleBySgbhQuery,
    SampleBySgbhResponse,
    AvailableSampleQuery,
    AvailableSampleResponse,
    TransportingSampleQuery,
    TransportingSampleResponse,
    TransportingSampleFullResponse,
)
from app.crud import transport_mgmt as crud


_auth = [Depends(require_roles(["transport_mgmt"]))]

router = APIRouter(
    prefix="/transport-mgmt",
    tags=["transport_mgmt"],
)


@router.post("/transport/create", response_model=ApiResponse[dict], response_model_exclude_none=True,
             dependencies=_auth)
def create_transport(payload: TransportCreateRequest, current_user: dict = Depends(get_current_user_optional)):
    try:
        data = crud.create_transport(payload.model_dump(), current_user)
        return ok(data, "创建运输单成功")
    except ValueError as exc:
        return err(str(exc))
    except Exception as exc:
        return err(f"创建运输单失败: {exc}")


@router.post("/transport/list", response_model=ApiResponse[TransportListResponse], response_model_exclude_none=True,
             dependencies=_auth)
def list_transport(payload: TransportListQuery, current_user: dict = Depends(get_current_user_optional)):
    try:
        from app.core.data_scope import resolve_data_scope
        data = crud.list_transport(
            payload.model_dump(),
            user=current_user,
            scope=resolve_data_scope(current_user, "transport_mgmt"),
        )
        return ok(data, "查询成功")
    except ValueError as exc:
        return err(str(exc))
    except Exception as exc:
        return err(f"查询失败: {exc}")


@router.post("/transport/detail", response_model=ApiResponse[TransportDetailResponse], response_model_exclude_none=True)
def transport_detail(payload: TransportDetailQuery, current_user: dict = Depends(get_current_user_optional)):
    try:
        data = crud.get_transport_detail(payload.model_dump())
        from app.core.data_scope import deny_owner_access
        denied = deny_owner_access(current_user, data.get("F_FQR"), module="transport_mgmt")
        if denied:
            return denied
        return ok(data, "查询成功")
    except ValueError as exc:
        return err(str(exc))
    except Exception as exc:
        return err(f"查询失败: {exc}")


@router.post("/sample/by-sgbh", response_model=ApiResponse[SampleBySgbhResponse], response_model_exclude_none=True,
             dependencies=_auth)
def sample_by_sgbh(payload: SampleBySgbhQuery):
    try:
        data = crud.get_sample_by_sgbh(payload.model_dump())
        return ok(data, "查询成功")
    except ValueError as exc:
        return err(str(exc))
    except Exception as exc:
        return err(f"查询失败: {exc}")


def _recv_time() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]


@router.post("/external/status/sync", response_model=ExternalPushAckResponse, response_model_exclude_none=True)
def external_sample_status_sync(
    payload: ExternalPushSyncRequest,
    current_user: dict = Depends(get_current_user_silent),
):
    """
    生物样本库系统样本状态推送（样本入库 / 样本出库），响应遵循外部系统约定的
    {code, msg, recvTime} 回执契约（不套用通用 ApiResponse 外壳）。

    data.task: "样本入库" | "样本出库"
    """
    try:
        internal_payload = crud.adapt_external_push(payload.model_dump())
        data = crud.sync_external_sample_status(internal_payload)
        failed_count = int(data.get("failed_count") or 0)
        success_count = int(data.get("success_count") or 0)
        skipped_count = int(data.get("skipped_count") or 0)
        if failed_count and not success_count and not skipped_count:
            msg = f"处理失败（{failed_count}条，请重推 detail.failed 列表）"
        else:
            msg = "请求已接收"
        return ExternalPushAckResponse(code=0, msg=msg, recvTime=_recv_time(), detail=data)
    except ValueError as exc:
        return ExternalPushAckResponse(code=1, msg=str(exc), recvTime=_recv_time())
    except Exception as exc:
        return ExternalPushAckResponse(code=1, msg=f"处理失败: {exc}", recvTime=_recv_time())


@router.post("/sample/available", response_model=ApiResponse[AvailableSampleResponse], response_model_exclude_none=False,
             dependencies=_auth)
def list_available_samples(payload: AvailableSampleQuery):
    """
    查询可用的样本（F_YSZT为"未运送"的记录），用于运输单创建前的样本挑选
    类似于 /unified-search/list，但只返回未运送的样本
    """
    try:
        data = crud.list_available_samples(payload.model_dump())
        return ok(data, f"查询成功，共 {data.get('total', 0)} 条")
    except ValueError as exc:
        return err(str(exc))
    except Exception as exc:
        return err(f"查询失败: {exc}")


@router.post("/sample/transporting", response_model=TransportingSampleFullResponse, response_model_exclude_none=False)
def list_transporting_samples(payload: TransportingSampleQuery):
    """
    查询运输中的样本（transports 与 data 平级）
    用于接收方查询运输单及样本信息
    """
    try:
        result = crud.list_transporting_samples(payload.model_dump())
        
        return TransportingSampleFullResponse(
            code=0,
            msg=f"查询成功，共 {result.get('total', 0)} 条",
            transports=result.get('transports', []),
            transport_total=result.get('transport_total', 0),
            data=TransportingSampleResponse(
                items=result.get('items', []),
                total=result.get('total', 0)
            )
        )
    except ValueError as exc:
        return TransportingSampleFullResponse(
            code=1,
            msg=str(exc),
            transports=[],
            transport_total=0,
            data=TransportingSampleResponse(items=[], total=0)
        )
    except Exception as exc:
        return TransportingSampleFullResponse(
            code=1,
            msg=f"查询失败: {exc}",
            transports=[],
            transport_total=0,
            data=TransportingSampleResponse(items=[], total=0)
        )

