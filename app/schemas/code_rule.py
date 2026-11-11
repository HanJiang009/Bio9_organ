"""
编码规则管理模块 Schemas
"""
from pydantic import BaseModel, Field
from typing import Optional, List, Dict, Any
from datetime import datetime


# ============================================
# 编码规则相关 Schemas
# ============================================

class CodeRuleBase(BaseModel):
    """编码规则基础模型"""
    F_CODE: str = Field(..., description="规则代码", max_length=50)
    F_NAME: str = Field(..., description="规则名称", max_length=100)
    F_RULE_TEMPLATE: Optional[str] = Field(None, description="规则模板", max_length=500)
    F_DESC: Optional[str] = Field(None, description="描述说明", max_length=500)
    F_SAMPLE: Optional[str] = Field(None, description="示例值", max_length=100)
    F_RULE_REC: Optional[Any] = Field(None, description="编码配置段。出参为对象数组；入参兼容数组或 JSON 字符串")
    F_ENABLED: int = Field(1, description="是否启用：1启用 0禁用")


class CodeRuleCreate(CodeRuleBase):
    """创建编码规则"""
    pass


class CodeRuleUpdate(BaseModel):
    """更新编码规则"""
    F_ID: int = Field(..., description="规则ID（必填）")
    F_NAME: Optional[str] = Field(None, description="规则名称", max_length=100)
    F_RULE_TEMPLATE: Optional[str] = Field(None, description="规则模板", max_length=500)
    F_DESC: Optional[str] = Field(None, description="描述说明", max_length=500)
    F_SAMPLE: Optional[str] = Field(None, description="示例值", max_length=100)
    F_RULE_REC: Optional[Any] = Field(None, description="编码配置段。出参为对象数组；入参兼容数组或 JSON 字符串")
    F_ENABLED: Optional[int] = Field(None, description="是否启用：1启用 0禁用")


class CodeRuleOut(CodeRuleBase):
    """编码规则输出"""
    F_ID: int
    F_CREATE_TIME: Optional[datetime] = None
    F_UPDATE_TIME: Optional[datetime] = None

    class Config:
        from_attributes = True


class CodeRuleListQuery(BaseModel):
    """编码规则列表查询"""
    F_ENABLED: Optional[int] = Field(None, description="是否启用过滤：1启用 0禁用")
    skip: int = Field(0, ge=0, description="跳过记录数")
    limit: int = Field(100, ge=1, le=1000, description="每页记录数")


class CodeRuleListOut(BaseModel):
    """编码规则列表输出"""
    items: List[CodeRuleOut]
    total: int


class CodeRefField(BaseModel):
    """可引用字段"""
    label: str = Field(..., description="显示名")
    value: str = Field(..., description="字段代码，写入引用类型段的 value")


class CodeRuleRefFieldsQuery(BaseModel):
    """按规则代码查询可引用字段"""
    F_CODE: str = Field(..., description="规则代码", max_length=50)


class CodeRuleRefFieldsOut(BaseModel):
    """可引用字段出参"""
    F_CODE: str
    fields: List[CodeRefField]


# ============================================
# 编码生成相关 Schemas
# ============================================

class CodeGenerateRequest(BaseModel):
    """编码生成请求"""
    rule_code: str = Field(..., description="规则代码")
    context: Dict[str, Any] = Field(default_factory=dict, description="上下文数据")


class CodeGenerateOut(BaseModel):
    """编码生成输出"""
    code: str = Field(..., description="生成的编码")
    rule_code: str = Field(..., description="使用的规则代码")
    rule_name: str = Field(..., description="规则名称")


class CodePreviewRequest(BaseModel):
    """编码预览请求"""
    rule_template: str = Field(..., description="规则模板")
    context: Dict[str, Any] = Field(default_factory=dict, description="上下文数据")


class CodePreviewOut(BaseModel):
    """编码预览输出"""
    preview_code: str = Field(..., description="预览编码")
    explanation: str = Field(..., description="规则说明")


# ============================================
# 编码序列相关 Schemas
# ============================================

class CodeSequenceOut(BaseModel):
    """编码序列输出"""
    F_ID: int
    F_RULE_CODE: str
    F_PREFIX: str
    F_CURRENT_VALUE: int
    F_RESET_TYPE: str
    F_LAST_UPDATE: Optional[datetime] = None

    class Config:
        from_attributes = True


class CodeSequenceListQuery(BaseModel):
    """编码序列列表查询"""
    F_RULE_CODE: Optional[str] = Field(None, description="规则代码过滤")
    skip: int = Field(0, ge=0, description="跳过记录数")
    limit: int = Field(100, ge=1, le=1000, description="每页记录数")


class CodeSequenceListOut(BaseModel):
    """编码序列列表输出"""
    items: List[CodeSequenceOut]
    total: int


# ============================================
# 通用响应 Schemas
# ============================================

class CodeRuleDeleteRequest(BaseModel):
    """删除编码规则请求"""
    F_ID: int = Field(..., description="要删除的规则ID")
