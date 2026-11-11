from typing import Optional, List, Dict, Any
from pydantic import BaseModel, Field


class OutboundListQuery(BaseModel):
    """出库单列表查询"""
    skip: int = Field(default=0, ge=0)
    limit: int = Field(default=50, ge=1, le=200)
    F_CKDH: Optional[str] = Field(default=None, description="出库单号，模糊匹配")
    F_SQR: Optional[str] = Field(default=None, description="申请人，模糊匹配")
    F_CKDZT: Optional[str] = Field(default=None, description="出库单状态：待出库/部分出库/已出库")
    F_SQSJ_START: Optional[str] = Field(default=None, description="申请时间起")
    F_SQSJ_END: Optional[str] = Field(default=None, description="申请时间止")
    F_SGBH: Optional[str] = Field(default=None, description="样本管编号，模糊匹配（按明细关联过滤）")


class OutboundListResponse(BaseModel):
    items: List[Dict[str, Any]] = Field(default_factory=list)
    total: int = 0


class OutboundDetailQuery(BaseModel):
    F_ID: Optional[int] = Field(default=None, description="出库单主键，优先使用")
    F_CKDH: Optional[str] = Field(default=None, description="出库单号，备用")


class OutboundDetailResponse(BaseModel):
    F_ID: Optional[int] = None
    F_CKDH: str
    F_SQR: Optional[str] = None
    F_SQSJ: Optional[str] = None
    F_LXFX: Optional[str] = None
    F_BZ: Optional[str] = None
    F_FJ: List[Dict[str, Any]] = Field(default_factory=list, description="附件信息（id/path/name）")
    F_YBSL: Optional[int] = None
    F_CKDZT: Optional[str] = None
    F_CJSJ: Optional[str] = None
    F_SOURCE_CKD_ID: Optional[int] = None
    items: List[Dict[str, Any]] = Field(default_factory=list, description="出库样本明细")


class OutboundResubmitRequest(BaseModel):
    """部分出库单：针对剩余未出库样本再次发起出库单"""
    F_CKD_ID: int = Field(description="原出库单ID")
    F_SQR: Optional[str] = Field(default=None, description="申请人，不传则沿用原单")
    F_LXFX: Optional[str] = Field(default=None, description="出库流向，不传则沿用原单")
    F_BZ: Optional[str] = Field(default=None, description="备注，不传则沿用原单")


class OutboundPendingQuery(BaseModel):
    """外部系统拉取待出库信息查询"""
    skip: int = Field(default=0, ge=0)
    limit: int = Field(default=50, ge=1, le=200)
    F_CKDH: Optional[str] = Field(default=None, description="出库单号，模糊匹配")


class OutboundPendingResponse(BaseModel):
    items: List[Dict[str, Any]] = Field(default_factory=list, description="待出库/部分出库单据（纯文本字段，不含附件）")
    total: int = 0
