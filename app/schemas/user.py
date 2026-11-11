from pydantic import BaseModel, Field
from typing import Optional, List

class UserCreate(BaseModel):
    F_YSM: str = Field(min_length=1, description="用户识别码") #工号
    F_MM: str = Field(default=None, description="明文密码，可选；不传则默认 123456")
    F_Ktype: str = Field(min_length=1, description="姓名")
    F_JS: List[str] = Field(min_items=1, description="角色列表，至少一个")
    F_YXQ: str = Field(..., pattern=r"^\d{4}-\d{2}-\d{2}$", description="有效期，格式 YYYY-MM-DD")
    F_ZT: Optional[str] = Field(default="true", description="状态,true/false")  
    F_TOKEN: Optional[str] = None #联系电话

class UserOut(BaseModel):
    F_ID: int
    F_YSM: str
    F_Ktype: str
    F_JS: Optional[str] = None
    F_YXQ: str
    F_TOKEN: Optional[str] = None #联系电话
    F_ZT: Optional[str] = None
    data_scope: Optional[str] = None  # 已废弃：不再有角色级一刀切
    data_scopes: Optional[dict] = None  # 当前会话角色的模块数据权限 {菜单id: all|self}
    F_JS_SELECTED: Optional[str] = None  # 当前会话角色名
    F_JS_ACCOUNT: Optional[str] = None  # 账号绑定的全部角色（逗号串）


class UserQuery(BaseModel):
    F_ID: Optional[int] = None
    F_YSM: Optional[str] = None

class UserListQuery(BaseModel):
    skip: int = 0
    limit: int = 20
    F_YSM: Optional[str] = None     # 工号（模糊），传空串视为未传
    F_Ktype: Optional[str] = None   # 姓名（模糊），传空串视为未传
    F_ZT: Optional[str] = None      # 状态：固定传“是/否”或不传；传空串视为未传
    F_JS: Optional[str] = None      # 角色：传空串视为未传

class UserDelete(BaseModel):
    F_ID: int

class UserUpdate(BaseModel):
    F_ID: int
    F_Ktype: Optional[str] = None
    F_JS: Optional[List[str]] = None
    F_YXQ: Optional[str] = None
    F_ZT: Optional[str] = None
    F_TOKEN: Optional[str] = None


class ResetPasswordIn(BaseModel):
    F_ID: int