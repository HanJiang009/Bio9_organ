from fastapi import APIRouter, Depends

from app.core.auth import require_roles, get_current_user_optional
from app.core.response import ok, err
from app.schemas.common import ApiResponse
from app.schemas.sample_linkage import (
    TransportCreateRequest,
    TransportListQuery,
    TransportListResponse,
    TransportDetailQuery,
    TransportDetailResponse,
    TransportReceiveRequest,
    SampleBySgbhQuery,
    SampleBySgbhResponse,
    AvailableSampleQuery,
    AvailableSampleResponse,
    TransportingSampleQuery,
    TransportingSampleResponse,
)
from app.crud import transport_mgmt as crud


router = APIRouter(
    prefix="/transport-mgmt",
    tags=["transport_mgmt"],
    dependencies=[Depends(require_roles(["transport_mgmt"]))],
)


@router.post("/transport/create", response_model=ApiResponse[dict], response_model_exclude_none=True)
def create_transport(payload: TransportCreateRequest, current_user: dict = Depends(get_current_user_optional)):
    try:
        data = crud.create_transport(payload.model_dump(), current_user)
        return ok(data, "创建运输单成功")
    except ValueError as exc:
        return err(str(exc))
    except Exception as exc:
        return err(f"创建运输单失败: {exc}")


@router.post("/transport/list", response_model=ApiResponse[TransportListResponse], response_model_exclude_none=True)
def list_transport(payload: TransportListQuery):
    try:
        data = crud.list_transport(payload.model_dump())
        return ok(data, "查询成功")
    except ValueError as exc:
        return err(str(exc))
    except Exception as exc:
        return err(f"查询失败: {exc}")


@router.post("/transport/detail", response_model=ApiResponse[TransportDetailResponse], response_model_exclude_none=True)
def transport_detail(payload: TransportDetailQuery):
    try:
        data = crud.get_transport_detail(payload.model_dump())
        return ok(data, "查询成功")
    except ValueError as exc:
        return err(str(exc))
    except Exception as exc:
        return err(f"查询失败: {exc}")


@router.post("/sample/by-sgbh", response_model=ApiResponse[SampleBySgbhResponse], response_model_exclude_none=True)
def sample_by_sgbh(payload: SampleBySgbhQuery):
    try:
        data = crud.get_sample_by_sgbh(payload.model_dump())
        return ok(data, "查询成功")
    except ValueError as exc:
        return err(str(exc))
    except Exception as exc:
        return err(f"查询失败: {exc}")


@router.post("/transport/receive", response_model=ApiResponse[dict], response_model_exclude_none=True)
def transport_receive(payload: TransportReceiveRequest, current_user: dict = Depends(get_current_user_optional)):
    try:
        data = crud.receive_transport(payload.model_dump(), current_user)
        return ok(data, "接收成功")
    except ValueError as exc:
        return err(str(exc))
    except Exception as exc:
        return err(f"接收失败: {exc}")


@router.post("/sample/available", response_model=ApiResponse[AvailableSampleResponse], response_model_exclude_none=False)
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


@router.post("/sample/transporting", response_model=ApiResponse[TransportingSampleResponse], response_model_exclude_none=False)
def list_transporting_samples(payload: TransportingSampleQuery):
    """
    查询运输中的样本（F_YSZT为"运输中"的记录），用于接收方查询样本信息
    类似于 /sample/available，但只返回运输中的样本
    """
    try:
        data = crud.list_transporting_samples(payload.model_dump())
        return ok(data, f"查询成功，共 {data.get('total', 0)} 条")
    except ValueError as exc:
        return err(str(exc))
    except Exception as exc:
        return err(f"查询失败: {exc}")

