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
    """直接入库。按分装预览结果写入样本分装表、容器位置和运输单。

    入库审核关闭时运输单为已接收，管子直接在库。
    开启时运输单为待审核，管子仍是未入库；审核通过后运输单为审核通过，管子在库。
    """
    try:
        data = crud.store_samples(payload.model_dump(), current_user)
        return ok(data, "入库成功" if data.get("F_YSDZT") == "已接收" else "已提交入库审核")
    except ValueError as exc:
        return err(str(exc))
    except Exception as exc:
        return err(f"入库失败: {exc}")
