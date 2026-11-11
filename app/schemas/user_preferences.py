from typing import Any
from pydantic import BaseModel, Field


class PreferenceGetIn(BaseModel):
    page_key: str = Field(..., min_length=1, description="页面标识符，由前端自定义，如 culture_order_list")


class PreferenceSaveIn(BaseModel):
    page_key: str = Field(..., min_length=1, description="页面标识符，由前端自定义，如 culture_order_list")
    config: Any = Field(..., description="字段配置内容，后端透明存储，结构由前端自定义")


class PreferenceOut(BaseModel):
    page_key: str
    config: Any
