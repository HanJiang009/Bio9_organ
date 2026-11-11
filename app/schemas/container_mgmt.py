from typing import Optional, List, Dict, Any
from pydantic import BaseModel, Field


class ContainerListQuery(BaseModel):
    skip: int = Field(default=0, ge=0)
    limit: int = Field(default=50, ge=1, le=200)
    F_GJZ: Optional[str] = Field(default=None, description="编号或名称，模糊匹配")
    F_RQBH: Optional[str] = Field(default=None, description="容器编号，模糊匹配")
    F_RQMC: Optional[str] = Field(default=None, description="容器名称，模糊匹配")
    F_CCWD: Optional[str] = Field(default=None, description="存储温度，模糊匹配")
    F_ZT: Optional[str] = Field(default=None, description="启用或禁用")


class ContainerSave(BaseModel):
    F_RQBH: str = Field(description="容器编号")
    F_RQMC: str = Field(description="容器名称")
    F_CCWD: Optional[str] = Field(default=None, description="存储温度")
    F_CS: int = Field(description="层数")
    F_JS: int = Field(description="每层架数")
    F_CTS: int = Field(description="每架抽屉数")
    F_HXS: int = Field(description="每抽屉盒数")
    F_ZT: Optional[str] = Field(default="启用", description="启用或禁用")
    F_BZ: Optional[str] = None


class ContainerUpdate(ContainerSave):
    F_ID: int


class ContainerId(BaseModel):
    F_ID: int


class ContainerTreeQuery(BaseModel):
    F_ID: int
    F_C: Optional[int] = Field(default=None, description="层。与架、抽屉一起传时只返回该抽屉盒位")
    F_J: Optional[int] = Field(default=None, description="架")
    F_CT: Optional[int] = Field(default=None, description="抽屉")


class ContainerListResponse(BaseModel):
    items: List[Dict[str, Any]] = Field(default_factory=list)
    total: int = 0
