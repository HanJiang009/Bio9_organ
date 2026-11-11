from typing import Optional, List
from pydantic import BaseModel, Field


class SampleInboundItem(BaseModel):
    F_YBFZ_ID: int = Field(description="分装记录ID")
    F_C: int = Field(description="层，从 1 开始")
    F_J: int = Field(description="架，从 1 开始")
    F_CT: int = Field(description="抽屉，从 1 开始")
    F_H: int = Field(description="盒，从 1 开始")


class SampleInboundStore(BaseModel):
    F_RQ_ID: int = Field(description="启用中的容器ID")
    ITEMS: List[SampleInboundItem]
    F_FQR: Optional[str] = Field(default=None, description="申请人，不传则取当前用户")
    F_FQSJ: Optional[str] = Field(default=None, description="申请时间")
    F_BZ: Optional[str] = None
    F_YSMDD: Optional[str] = Field(default=None, description="运输目的地")
