from typing import List, Optional

from pydantic import BaseModel, Field


class EmrPlaceholderOut(BaseModel):
    token: str = Field(description="模板占位符，如 %sfzh")
    field: str = Field(description="对应系统字段，如 F_SFZH")
    label: str = Field(description="中文名")
    example: Optional[str] = Field(default=None, description="配置示例")


class EmrConfigOut(BaseModel):
    enabled: bool = Field(description="是否启用电子病历调阅")
    url_template: str = Field(description="地址模板，只能使用 placeholders 中的一种占位符")
    placeholders: List[EmrPlaceholderOut] = Field(description="可选占位符清单，配置页用来展示可选项")


class EmrConfigUpdateIn(BaseModel):
    enabled: Optional[bool] = Field(default=None, description="是否启用")
    url_template: Optional[str] = Field(
        default=None,
        description="地址模板，例如 http://www.baidu.com/%sfzh；只能包含一种占位符",
    )
