from typing import Optional, List, Dict, Any
from pydantic import BaseModel, Field


class StockAuditConfig(BaseModel):
    F_CKSH: str = Field(description="出库是否审核：是或否")
    F_RKSH: str = Field(description="入库是否审核：是或否")


class StockAuditListQuery(BaseModel):
    skip: int = Field(default=0, ge=0)
    limit: int = Field(default=50, ge=1, le=200)
    F_TAB: Optional[str] = Field(default=None, description="待审核或全部")
    F_DJLX: Optional[str] = Field(default=None, description="入库或出库")
    F_DJH: Optional[str] = Field(default=None, description="单号，模糊匹配")
    F_SQR: Optional[str] = Field(default=None, description="申请人，模糊匹配")
    F_SQSJ_START: Optional[str] = None
    F_SQSJ_END: Optional[str] = None
    F_HH: Optional[str] = Field(default=None, description="盒号，模糊匹配")


class StockAuditId(BaseModel):
    F_ID: int


class StockAuditReview(BaseModel):
    F_ID: int
    F_JG: str = Field(description="通过或不通过")
    F_SHBZ: Optional[str] = Field(default=None, description="审核备注")


class StockAuditListResponse(BaseModel):
    items: List[Dict[str, Any]] = Field(default_factory=list)
    total: int = 0
