from fastapi import APIRouter, Depends

from app.core.auth import require_roles, get_current_user_optional
from app.core.response import ok, err
from app.schemas.common import ApiResponse
from app.schemas.stock_audit import (
    StockAuditConfig,
    StockAuditListQuery,
    StockAuditListResponse,
    StockAuditId,
    StockAuditReview,
)
from app.crud import stock_audit as crud


_auth = [Depends(require_roles(["stock_audit"]))]

router = APIRouter(prefix="/stock-audit", tags=["stock_audit"])


@router.post("/config/get", response_model=ApiResponse[dict], response_model_exclude_none=True,
             dependencies=_auth)
def get_config(current_user: dict = Depends(get_current_user_optional)):
    """读取出库是否审核、入库是否审核。只有超级管理员可以调用。"""
    try:
        crud.assert_super_admin(current_user)
        return ok(crud.audit_flags(), "查询成功")
    except crud.StockAuditPermissionError as exc:
        return err(str(exc))
    except ValueError as exc:
        return err(str(exc))
    except Exception as exc:
        return err(f"查询失败: {exc}")


@router.post("/config/save", response_model=ApiResponse[dict], response_model_exclude_none=True,
             dependencies=_auth)
def save_config(payload: StockAuditConfig, current_user: dict = Depends(get_current_user_optional)):
    """保存两个开关。只有角色名为超级管理员的账号可以修改。默认都是否。"""
    try:
        data = crud.save_flags(payload.model_dump(), current_user)
        return ok(data, "保存成功")
    except crud.StockAuditPermissionError as exc:
        return err(str(exc))
    except ValueError as exc:
        return err(str(exc))
    except Exception as exc:
        return err(f"保存失败: {exc}")


@router.post("/list", response_model=ApiResponse[StockAuditListResponse], response_model_exclude_none=True,
             dependencies=_auth)
def list_audits(payload: StockAuditListQuery):
    """F_TAB 传待审核时只看未审单据，传全部或不传则不限状态。F_DJLX 可筛入库或出库。"""
    try:
        data = crud.list_audits(payload.model_dump())
        return ok(data, f"查询成功，共 {data.get('total', 0)} 条")
    except ValueError as exc:
        return err(str(exc))
    except Exception as exc:
        return err(f"查询失败: {exc}")


@router.post("/detail", response_model=ApiResponse[dict], response_model_exclude_none=True,
             dependencies=_auth)
def detail(payload: StockAuditId):
    """打开的是提交时的快照。F_KSH 为 true 时底部才能审核，已审核只能查看。"""
    try:
        return ok(crud.get_audit(payload.model_dump()), "查询成功")
    except ValueError as exc:
        return err(str(exc))
    except Exception as exc:
        return err(f"查询失败: {exc}")


@router.post("/review", response_model=ApiResponse[dict], response_model_exclude_none=True,
             dependencies=_auth)
def review(payload: StockAuditReview, current_user: dict = Depends(get_current_user_optional)):
    """通过或不通过，可填备注。已审核的单据不能再次审核。"""
    try:
        data = crud.review_audit(payload.model_dump(), current_user)
        return ok(data, "审核完成")
    except ValueError as exc:
        return err(str(exc))
    except Exception as exc:
        return err(f"审核失败: {exc}")
