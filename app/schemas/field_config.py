from typing import Any, List, Optional

from pydantic import BaseModel, Field


class FieldConfigPageQuery(BaseModel):
    F_YM: str = Field(description="页面编码")


class FieldConfigItemIn(BaseModel):
    F_ZDBM: str = Field(description="字段编码")
    F_XSM: Optional[str] = Field(default=None, description="显示名，空则用系统名称")
    F_XS: Optional[Any] = Field(default="是", description="是否显示，是或否")
    F_BT: Optional[Any] = Field(default="否", description="是否必填，是或否")


class FieldConfigSave(BaseModel):
    F_YM: str
    items: List[FieldConfigItemIn]


class FieldConfigRuntimeQuery(BaseModel):
    F_YM: Optional[str] = Field(default=None, description="单个页面编码")
    PAGES: Optional[List[str]] = Field(default=None, description="页面编码列表，不传则返回全部")
