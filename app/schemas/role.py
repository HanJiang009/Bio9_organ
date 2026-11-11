from pydantic import BaseModel, Field
from typing import Optional, Any, Dict, Union


class RoleCreate(BaseModel):
    F_JS: str = Field(min_length=1, description="角色名称")
    F_QSS: Optional[Any] = Field(default=None, description="权限树(JSON或对象)")
    F_ZT: Optional[str] = Field(default="true", description="状态,true/false")
    F_SJQX: Optional[Union[str, Dict[str, str]]] = Field(
        default=None,
        description="模块数据权限映射 {菜单id: all|self}；也可传旧版标量",
    )


class RoleUpdate(BaseModel):
    F_ID: int
    F_JS: str
    F_QSS: Optional[Any] = None
    F_ZT: Optional[str] = None
    F_SJQX: Optional[Union[str, Dict[str, str]]] = Field(
        default=None,
        description="整表覆盖模块数据权限；不传不改",
    )


class RolePseudonymSave(BaseModel):
    F_ID: int = Field(description="角色ID")
    enabled: bool = Field(description="该角色是否开启伪名化")
    fields: Optional[Any] = Field(
        default=None,
        description="勾选字段。对象按 key 更新；数组为完整勾选集合。不传则只改该角色的总开关",
    )


class RoleDataScopeToggle(BaseModel):
    F_ID: int
    node_id: str = Field(min_length=1, description="菜单权限键，如 sample_receive")
    F_SJQX: str = Field(description="all / self，也接受全部数据 / 个人数据 / 本人数据")


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


