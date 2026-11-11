from pydantic import BaseModel, Field
from typing import Optional, Union, List
from .user import UserOut, UserQuery


class LoginIn(BaseModel):
    F_YSM: str = Field(min_length=1, description="用户识别码/工号")
    F_MM: Optional[str] = Field(default=None, description="明文密码（二选一）")
    F_MM2: Optional[str] = Field(default=None, min_length=32, max_length=32, description="MD5 摘要（二选一）")


class RoleOption(BaseModel):
    value: str
    label: str


class RoleOptionsQuery(UserQuery):
    """登录前查询可选角色：F_YSM 或 F_ID。"""


class RefreshTokenIn(BaseModel):
    F_JS_SELECTED: Optional[Union[str, List[str]]] = Field(
        default=None,
        description="首页切换身份时传入恰好一个角色；不传则沿用 token 中的当前角色并重读权限",
    )


class LoginData(BaseModel):
    token: str
    user: UserOut


class LoginResponse(BaseModel):
    code: int
    msg: str = ""
    token: str
    user: Optional[UserOut] = None
    F_JS_SELECTED: Optional[str] = None
    role_options: Optional[List[RoleOption]] = None
    roles: Optional[List[str]] = None


