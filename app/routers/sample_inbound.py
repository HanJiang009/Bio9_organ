from fastapi import APIRouter, Depends

from app.core.auth import require_roles, get_current_user_optional
from app.core.response import ok, err
from app.schemas.common import ApiResponse
from app.schemas.sample_inbound import SampleInboundStore
from app.crud import sample_inbound as crud


_auth = [Depends(require_roles(["sample_inbound"]))]

router = APIRouter(prefix="/sample-inbound", tags=["sample_inbound"])


@router.post("/store", response_model=ApiResponse[dict], response_model_exclude_none=True,
             dependencies=_auth)
def store(payload: SampleInboundStore, current_user: dict = Depends(get_current_user_optional)):
    """直接入库。写入容器位置和运输单。

    入库审核关闭时运输单为运送中。开启时为待审核，通过后才变为运送中。
    接收仍走运输管理。接收完成并在库后，综合查询才能按在库看到，存储位置在 F_CCWZ。
    """
    try:
        data = crud.store_samples(payload.model_dump(), current_user)
        return ok(data, "入库成功" if data.get("F_YSDZT") == "运送中" else "已提交入库审核")
    except ValueError as exc:
        return err(str(exc))
    except Exception as exc:
        return err(f"入库失败: {exc}")
