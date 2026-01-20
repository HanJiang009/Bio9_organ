from typing import Optional, Dict, Any, List
from pydantic import BaseModel, Field, field_validator


class SampleLinkageQuery(BaseModel):
    F_ZZID: Optional[str] = Field(default=None, description="标本号 模糊匹配")
    F_BRBH: Optional[str] = Field(default=None, description="病人编号/住院号 模糊匹配")
    F_DJRQ_START: Optional[str] = Field(default=None, description="登记日期起始，格式 YYYY-MM-DD")
    F_DJRQ_END: Optional[str] = Field(default=None, description="登记日期结束，格式 YYYY-MM-DD")
    F_CXSJ_START: Optional[str] = Field(default=None, description="采集时间起始，格式 YYYY-MM-DD 或 YYYY-MM-DD HH:MM:SS")
    F_CXSJ_END: Optional[str] = Field(default=None, description="采集时间结束，格式 YYYY-MM-DD 或 YYYY-MM-DD HH:MM:SS")
    skip: int = Field(default=0, ge=0)
    limit: int = Field(default=50, ge=1)


class SampleLinkageItem(BaseModel):
    F_YBZJ_ID: Optional[int] = None
    F_PYGD_ID: Optional[int] = None
    F_ZZID: Optional[str] = None
    F_GDID: Optional[int] = None
    F_GDBH: Optional[str] = None
    F_BBLX: Optional[str] = None
    F_XM: Optional[str] = None
    F_BBLY: Optional[str] = None
    F_XB: Optional[str] = None
    F_NL: Optional[int] = None
    F_BRBH: Optional[str] = None
    F_ZYH: Optional[str] = None
    F_CXSJ: Optional[str] = None
    F_DJRQ: Optional[str] = None

    class Config:
        extra = "allow"  # Allow additional fields


class SampleLinkageResponse(BaseModel):
    items: List[SampleLinkageItem] = Field(default_factory=list)
    total: int = 0


class SampleAllocationInfo(BaseModel):
    F_YBLX: Optional[str] = None
    F_CCXS: Optional[str] = None
    F_FGS: int = Field(description="分管数", ge=1)
    F_TJ: Optional[str] = None
    F_ND: Optional[str] = None
    F_SPREC: Optional[str] = None
    F_BZ: Optional[str] = None
    F_WZ: Optional[str] = None          # 位置（不校验占位，仅记录）
    F_SFYS: Optional[bool] = Field(default=None)  # 是否运输，未提供则预览默认 True
    
    # 盒号和位置（可选，会覆盖外围的默认盒号）
    F_HZH: Optional[str] = Field(default=None, description="盒号")
    F_HS: Optional[int] = Field(default=None, description="行数", ge=1)
    F_LS: Optional[int] = Field(default=None, description="列数", ge=1)


class SampleAllocationRequest(BaseModel):
    YBZJ_IDS: List[int] = Field(default_factory=list)
    PYGD_IDS: List[int] = Field(default_factory=list)
    FZXX: List[SampleAllocationInfo] = Field(default_factory=list)
    
    # 外围默认盒号（可选，FZXX 内的盒号优先）
    F_HZH: Optional[str] = Field(default=None, description="默认盒号，作用于所有未指定盒号的分装信息")

    @field_validator("FZXX", mode="before")
    @classmethod
    def _ensure_list(cls, v):
        if v is None:
            return []
        if isinstance(v, dict):
            return [v]
        return v


class SampleTubeItem(BaseModel):
    F_SGBH: str
    F_ZZID: str
    F_YBZJ_ID: Optional[int] = None
    F_PYGD_ID: Optional[int] = None
    F_DC: Optional[str] = None
    F_YBLX: Optional[str] = None
    F_CCXS: Optional[str] = None
    F_FGS: int
    F_TJ: Optional[str] = None
    F_ND: Optional[str] = None
    F_SPREC: Optional[str] = None
    F_BZ: Optional[str] = None
    F_WZ: Optional[str] = None
    F_SFYS: bool = True
    
    # 盒号和位置
    F_HZH: Optional[str] = None
    F_HS: Optional[int] = None
    F_LS: Optional[int] = None


class SampleAllocationPreview(BaseModel):
    items: List[SampleTubeItem] = Field(default_factory=list)
    total: int = 0


class SampleAllocationSaveRequest(BaseModel):
    YBZJ_IDS: List[int] = Field(default_factory=list)
    PYGD_IDS: List[int] = Field(default_factory=list)
    FZXX: List[SampleAllocationInfo] = Field(default_factory=list)
    ITEMS: Optional[List[SampleTubeItem]] = Field(
        default=None, 
        description="可选：条目级分装列表（含精确运输标识）",
        alias="items"  # 支持小写 "items" 作为别名
    )
    
    # 外围默认盒号（可选，FZXX 内的盒号优先）
    F_HZH: Optional[str] = Field(default=None, description="默认盒号，作用于所有未指定盒号的分装信息")
    
    # 运输单信息
    F_FQR: Optional[str] = Field(default=None, description="运输发起人")
    F_FQSJ: Optional[str] = Field(default=None, description="运输发起时间，ISO或yyyy-MM-dd HH:mm:ss")
    F_BZ: Optional[str] = Field(default=None, description="运输备注")
    F_YSMDD: Optional[str] = Field(default=None, description="运输目的地")
    
    model_config = {"populate_by_name": True}  # 允许使用字段名或别名

    @field_validator("FZXX", mode="before")
    @classmethod
    def _ensure_list(cls, v):
        if v is None:
            return []
        if isinstance(v, dict):
            return [v]
        return v

    @field_validator("ITEMS", mode="before")
    @classmethod
    def _ensure_items_list(cls, v):
        if v is None:
            return None
        if isinstance(v, dict):
            return [v]
        return v


class TubeScanRequest(BaseModel):
    F_SGBH: str = Field(..., description="样本管编号")


class TransportCreateRequest(BaseModel):
    YBFZ: List[int] = Field(default_factory=list, description="待运输的分装记录ID列表")
    F_FQR: Optional[str] = Field(default=None, description="运输发起人")
    F_FQSJ: Optional[str] = Field(default=None, description="运输发起时间，ISO或yyyy-MM-dd HH:mm:ss")
    F_BZ: Optional[str] = Field(default=None, description="运输备注")
    F_YSMDD: Optional[str] = Field(default=None, description="运输目的地")
    
    @field_validator("YBFZ", mode="before")
    @classmethod
    def _ensure_list(cls, v):
        if v is None:
            return []
        if isinstance(v, (int, str)):
            return [v]
        return v


class TransportListQuery(BaseModel):
    skip: int = Field(default=0, ge=0)
    limit: int = Field(default=50, ge=1, le=200)
    F_YSDH: Optional[str] = Field(default=None, description="模糊匹配运输单号")
    F_YSDZT: Optional[str] = Field(default=None, description="运输单状态过滤")
    F_FQSJ_START: Optional[str] = Field(default=None, description="发起时间起")
    F_FQSJ_END: Optional[str] = Field(default=None, description="发起时间止")
    F_SGBH: Optional[str] = Field(default=None, description="样本编号模糊匹配，需要通过 F_YSXX 的分装ID关联查询")


class TransportListResponse(BaseModel):
    items: List[dict] = Field(default_factory=list)
    total: int = 0


class AvailableSampleQuery(BaseModel):
    """可用样本查询（用于运输单创建前的样本挑选）"""
    F_SGBH: Optional[str] = Field(default=None, description="样本编号，模糊匹配")
    F_GDBH: Optional[str] = Field(default=None, description="培养工单编号，模糊匹配")
    F_ZZID: Optional[str] = Field(default=None, description="标本号，模糊匹配")
    F_DC: Optional[str] = Field(default=None, description="代次，模糊匹配")
    skip: int = Field(default=0, ge=0)
    limit: int = Field(default=50, ge=1)


class AvailableSampleResponse(BaseModel):
    """可用样本查询响应"""
    items: List[Dict[str, Any]] = Field(default_factory=list)
    total: int = 0


class TransportingSampleQuery(BaseModel):
    """运输中样本查询（用于接收方查询样本信息）"""
    F_SGBH: Optional[str] = Field(default=None, description="样本编号，模糊匹配")
    F_GDBH: Optional[str] = Field(default=None, description="培养工单编号，模糊匹配")
    F_ZZID: Optional[str] = Field(default=None, description="标本号，模糊匹配")
    F_DC: Optional[str] = Field(default=None, description="代次，模糊匹配")
    skip: int = Field(default=0, ge=0)
    limit: int = Field(default=50, ge=1)


class TransportingSampleResponse(BaseModel):
    """运输中样本查询响应"""
    items: List[Dict[str, Any]] = Field(default_factory=list)
    total: int = 0


class TransportDetailQuery(BaseModel):
    F_ID: Optional[int] = Field(default=None, description="运输单主键，优先使用")
    F_YSDH: Optional[str] = Field(default=None, description="运输单号，备用")


class TransportDetailResponse(BaseModel):
    F_ID: Optional[int] = None
    F_YSDH: str
    F_YSDZT: Optional[str] = None
    F_FQR: Optional[str] = None
    F_FQSJ: Optional[str] = None
    F_BZ: Optional[str] = None
    F_YSMDD: Optional[str] = None
    items: List[dict] = Field(default_factory=list, description="分装样本详情")


class TransportReceiveRequest(BaseModel):
    F_YSDH: str = Field(description="运输单号")
    F_JSR: Optional[str] = Field(default=None, description="接收人")
    F_JSSJ: Optional[str] = Field(default=None, description="接收时间，ISO或yyyy-MM-dd HH:mm:ss")
    F_JSBZ: Optional[str] = Field(default=None, description="接收备注")


class SampleBySgbhQuery(BaseModel):
    F_SGBH: str = Field(description="样本编号/试管编号")


class SampleBySgbhResponse(BaseModel):
    F_ID: Optional[int] = None
    F_SGBH: Optional[str] = None
    F_ZZID: Optional[str] = None
    F_DC: Optional[str] = None
    F_PYGD_ID: Optional[int] = None
    F_YBLX: Optional[str] = None
    F_BBLX: Optional[str] = None
    F_GDBH: Optional[str] = None
    F_PYR: Optional[str] = None

