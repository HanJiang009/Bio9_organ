from typing import Optional, List, Dict, Any
from pydantic import BaseModel, Field


class UnifiedSearchQuery(BaseModel):
    F_SGBH: Optional[str] = Field(default=None, description="样本编号，模糊匹配")
    F_GDBH: Optional[str] = Field(default=None, description="培养工单编号，模糊匹配")
    F_ZZID: Optional[str] = Field(default=None, description="标本号，模糊匹配")
    F_DC: Optional[str] = Field(default=None, description="代次，模糊匹配")
    skip: int = Field(default=0, ge=0)
    limit: int = Field(default=50, ge=1)


class UnifiedSearchList(BaseModel):
    items: List[Dict[str, Any]] = Field(default_factory=list)
    total: int = 0


class SampleDetailQuery(BaseModel):
    """样本详情查询"""
    F_ID: int = Field(description="样本分装记录ID")


class SampleDetailResponse(BaseModel):
    """样本详情响应"""
    pass  # 动态字段，使用 Dict[str, Any]
