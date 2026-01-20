from pydantic import BaseModel, Field
from typing import Optional


class ShortcutCreate(BaseModel):
    F_UID: int = Field(description="用户ID")
    F_KJJS: str = Field(min_length=2, description="快捷键JSON字符串")


class ShortcutUpdate(BaseModel):
    F_ID: int
    F_KJJS: str


class ShortcutQuery(BaseModel):
    F_ID: Optional[int] = None
    F_UID: Optional[int] = None


class ShortcutListQuery(BaseModel):
    skip: int = 0
    limit: int = 20


