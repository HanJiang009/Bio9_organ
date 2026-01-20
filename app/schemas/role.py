from pydantic import BaseModel, Field
from typing import Optional, Any, Dict


class RoleCreate(BaseModel):
    F_JS: str = Field(min_length=1, description="角色名称")
    F_QSS: Optional[Any] = Field(default=None, description="权限树(JSON或对象)")
    F_ZT: Optional[str] = Field(default="true", description="状态,true/false")


class RoleUpdate(BaseModel):
    F_ID: int
    F_JS: str
    F_QSS: Optional[Any] = None
    F_ZT: Optional[str] = None


class RoleQuery(BaseModel):
    F_ID: Optional[int] = None
    F_JS: Optional[str] = None


class RoleDelete(BaseModel):
    F_ID: int


class RoleListQuery(BaseModel):
    skip: int = 0
    limit: int = 20
    F_JS: Optional[str] = None      # 角色名称（模糊）
    F_JSBH: Optional[str] = None    # 角色编号（模糊）
    F_ZT: Optional[str] = None      # 状态筛选：是/否 或 true/false/启用/停用/1/0/yes/no/y/n


class RoleUsersQuery(BaseModel):
    F_ID: int = Field(description="角色ID")


