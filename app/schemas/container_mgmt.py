from typing import Optional, List, Dict, Any
from pydantic import BaseModel, Field


class ContainerListQuery(BaseModel):
    skip: int = Field(default=0, ge=0)
    limit: int = Field(default=50, ge=1, le=200)
    F_GJZ: Optional[str] = Field(default=None, description="编号或名称，模糊匹配")
    F_RQBH: Optional[str] = Field(default=None, description="容器编号，模糊匹配")
    F_RQMC: Optional[str] = Field(default=None, description="容器名称，模糊匹配")
    F_RQLB: Optional[str] = Field(default=None, description="容器类别，精确匹配")
    F_CCWD: Optional[str] = Field(default=None, description="存储温度，模糊匹配")
    F_ZT: Optional[str] = Field(default=None, description="启用或禁用")


class ContainerLayer(BaseModel):
    F_CMC: Optional[str] = Field(default=None, description="层名称，不传则按第N层")
    F_JS: int = Field(description="该层冻存架数")
    F_CTS: int = Field(description="该层每架抽屉数")
    F_HXS: int = Field(description="该层每抽屉盒数")
    F_HS: Optional[int] = Field(default=10, description="盒规格行数")
    F_LS: Optional[int] = Field(default=10, description="盒规格列数")


class ContainerSave(BaseModel):
    F_RQBH: str = Field(description="容器编号")
    F_RQMC: str = Field(description="容器名称")
    F_RQLB: Optional[str] = Field(default=None, description="容器类别")
    F_CCWD: Optional[str] = Field(default=None, description="存储温度")
    LAYERS: List[ContainerLayer] = Field(description="按层配置。数组顺序就是第1层、第2层")
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
