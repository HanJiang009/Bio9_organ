from typing import Any, List, Optional
from pydantic import BaseModel, Field, field_validator


def _normalize_view(v: Any, default: str = "specimen") -> str:
    s = str(v if v is not None and str(v).strip() else default).strip().lower()
    if s in ("order", "工单"):
        return "order"
    if s in ("specimen", "标本", "sample"):
        return "specimen"
    return default


def _empty_str_to_none(v: Any) -> Optional[Any]:
    if v is None:
        return None
    if isinstance(v, str):
        s = v.strip()
        return s if s else None
    return v


class HomeCardQuery(BaseModel):
    view: str = Field(default="specimen", description="specimen 按标本 / order 按工单")
    keyword: Optional[str] = Field(default=None, description="同时模糊匹配姓名/标本号/工单编号/病人编号")
    F_GDZT: Optional[str] = Field(default=None, description="工单状态，不传=只查已创建")
    skip: Optional[int] = Field(default=None, ge=0, description="卡片默认不分页；传入时才生效")
    limit: Optional[int] = Field(default=None, gt=0, description="卡片默认不分页，不传则返回全部")

    @field_validator("view", mode="before")
    @classmethod
    def _view(cls, v: Any) -> str:
        return _normalize_view(v, "specimen")

    @field_validator("keyword", "F_GDZT", mode="before")
    @classmethod
    def _empty_str(cls, v: Any) -> Optional[str]:
        return _empty_str_to_none(v)

    @field_validator("skip", "limit", mode="before")
    @classmethod
    def _empty_page(cls, v: Any) -> Optional[int]:
        if v is None or v == "":
            return None
        return v


class HomeListQuery(BaseModel):
    view: str = Field(default="order", description="specimen 按标本 / order 按工单；不传保持工单列表")
    keyword: Optional[str] = Field(default=None, description="同时模糊匹配姓名/标本号/工单编号/病人编号")
    F_GDZT: Optional[str] = Field(default=None, description="工单状态，不传=只查已创建")
    skip: int = Field(default=0, ge=0)
    limit: int = Field(default=20, gt=0)

    @field_validator("view", mode="before")
    @classmethod
    def _view(cls, v: Any) -> str:
        return _normalize_view(v, "order")

    @field_validator("keyword", "F_GDZT", mode="before")
    @classmethod
    def _empty_str(cls, v: Any) -> Optional[str]:
        return _empty_str_to_none(v)

    @field_validator("skip", mode="before")
    @classmethod
    def _skip(cls, v: Any) -> int:
        if v is None or v == "":
            return 0
        return v

    @field_validator("limit", mode="before")
    @classmethod
    def _limit(cls, v: Any) -> int:
        if v is None or v == "":
            return 20
        return v


class HomeSampleTypeStat(BaseModel):
    F_YBLX: str
    F_REMARK: Optional[str] = None
    count: int = 0


class HomeCardItem(BaseModel):
    F_ID: Optional[int] = None
    F_XM: Optional[str] = None
    F_BRBH: Optional[str] = None
    F_ZZID: Optional[str] = None
    F_GDZT: Optional[str] = None
    F_ZKZT: Optional[str] = None
    F_AZ: Optional[str] = None
    F_DC: Optional[str] = None
    F_GDBH: Optional[str] = None
    F_ZDYXH: Optional[str] = None
    F_PYSJ: Optional[str] = None
    order_count: int = 0
    sample_types: List[HomeSampleTypeStat] = Field(default_factory=list)
