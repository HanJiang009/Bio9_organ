from pydantic import BaseModel, Field
from typing import Optional
from .user import UserOut


class LoginIn(BaseModel):
    F_YSM: str = Field(min_length=1, description="用户识别码/工号")
    F_MM: Optional[str] = Field(default=None, description="明文密码（二选一）")
    F_MM2: Optional[str] = Field(default=None, min_length=32, max_length=32, description="MD5 摘要（二选一）")


class LoginData(BaseModel):
    token: str
    user: UserOut


class LoginResponse(BaseModel):
    code: int
    msg: str = ""
    token: str
    user: Optional[UserOut] = None


