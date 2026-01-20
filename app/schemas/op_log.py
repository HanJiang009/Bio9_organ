from pydantic import BaseModel, Field
from typing import Optional, List
from datetime import datetime

class OpLog(BaseModel):
    F_MK: str = Field(description="模块名称")
    F_CZLX: str = Field(description="操作类型")
    F_CZR: str = Field(description="操作人")
    F_CZSJ: datetime = Field(description="操作时间")
    F_ZT: str = Field(description="操作状态")
    F_CZXQ: Optional[str] = Field(default=None, description="操作详情")
    F_YSM: Optional[str] = Field(default=None, description="操作人工号")

class OpLogQuery(BaseModel):
    skip: int = Field(default=0, description="跳过条数")
    limit: Optional[int] = Field(default=20, description="返回条数，null 表示不限制")
    F_ID: Optional[int] = Field(default=None, description="主键ID")
    F_MK: Optional[str] = Field(default=None, description="模块名称")
    F_CZLX: Optional[str] = Field(default=None, description="操作类型")
    F_CZR: Optional[str] = Field(default=None, description="操作人")
    F_CZSJ: Optional[datetime] = Field(default=None, description="操作时间")
    F_CZSJ_START: Optional[datetime] = Field(default=None, description="开始时间(含)")
    F_CZSJ_END: Optional[datetime] = Field(default=None, description="结束时间(含)")
    F_ZT: Optional[str] = Field(default=None, description="操作状态")
    F_CZXQ: Optional[str] = Field(default=None, description="操作详情")
    F_YSM: Optional[str] = Field(default=None, description="操作人工号")


class OpLogListOut(BaseModel):
    items: List[OpLog]
    total: int