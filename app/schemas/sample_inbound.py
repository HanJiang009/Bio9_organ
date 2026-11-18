from typing import Optional, List
from pydantic import BaseModel, ConfigDict, Field


class SampleInboundItem(BaseModel):
    """预览结果的一条，再补上容器位置。其余预览字段原样保留。"""
    model_config = ConfigDict(extra="allow")

    F_SGBH: str = Field(description="试管编号，来自分装预览")
    F_ZZID: str = Field(description="标本号，来自分装预览")
    F_C: int = Field(description="层，从 1 开始")
    F_J: int = Field(description="架，从 1 开始")
    F_CT: int = Field(description="抽屉，从 1 开始")
    F_H: int = Field(description="盒，从 1 开始")
    F_WZ: str = Field(description="盒内位置，如 A1。按该层盒规格校验")


class SampleInboundStore(BaseModel):
    F_RQ_ID: int = Field(description="启用中的容器ID")
    ITEMS: List[SampleInboundItem]
    F_FQR: Optional[str] = Field(default=None, description="申请人，不传则取当前用户")
    F_FQSJ: Optional[str] = Field(default=None, description="申请时间")
    F_BZ: Optional[str] = None
    F_YSMDD: Optional[str] = Field(default=None, description="运输目的地")
