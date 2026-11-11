"""
字典管理模块 Schemas
"""
from pydantic import BaseModel, Field, field_validator
from typing import Optional, List, Dict, Any
from datetime import datetime


def _empty_str_to_none_int(v, default: Optional[int] = 0):
    """
    兼容前端表单把未填写的整型字段（F_SORT/F_ENABLED/F_IS_DEFAULT 等）传成空字符串 ""
    的情况，避免 Pydantic 报 "int_parsing" 422 错误（如 /dict/category/create 传
    F_SORT: "" 时报错）。

    - 空字符串 / None → default（必填字段默认 0/1，选填字段传 None 表示保持原样/不过滤）
    - 其余值原样交给 Pydantic 自身的 int 转换（如合法数字字符串 "10"）

    NOTE(2026-07-29)：本函数原先只被 DictItemBase/DictItemBatchCreateItem 的
    F_SORT 使用，DictCategory 系列、Update 系列、ListQuery 系列均未覆盖，
    导致 /dict/category/create 等接口仍会因 F_SORT="" 报 422。现已扩展到全部
    同类整型字段，详见下方各处 field_validator。
    """
    if v is None or v == "":
        return default
    return v


# ============================================
# 字典类别相关 Schemas
# ============================================

class DictCategoryBase(BaseModel):
    """字典类别基础模型"""
    F_CODE: str = Field(..., description="类别代码", max_length=50)
    F_NAME: str = Field(..., description="类别名称", max_length=100)
    F_DESC: Optional[str] = Field(None, description="描述说明", max_length=500)
    F_MODULE: Optional[str] = Field(None, description="所属模块", max_length=50)
    F_ENABLED: int = Field(1, description="是否启用：1启用 0禁用")
    F_SORT: int = Field(0, description="排序号")

    @field_validator("F_ENABLED", mode="before")
    @classmethod
    def _coerce_enabled(cls, v):
        return _empty_str_to_none_int(v, default=1)

    @field_validator("F_SORT", mode="before")
    @classmethod
    def _coerce_sort(cls, v):
        return _empty_str_to_none_int(v, default=0)


class DictCategoryCreate(DictCategoryBase):
    """创建字典类别"""
    pass


class DictCategoryUpdate(BaseModel):
    """更新字典类别"""
    F_ID: int = Field(..., description="类别ID（必填）")
    F_CODE: Optional[str] = Field(None, description="类别代码", max_length=50)
    F_NAME: Optional[str] = Field(None, description="类别名称", max_length=100)
    F_DESC: Optional[str] = Field(None, description="描述说明", max_length=500)
    F_MODULE: Optional[str] = Field(None, description="所属模块", max_length=50)
    F_ENABLED: Optional[int] = Field(None, description="是否启用：1启用 0禁用")
    F_SORT: Optional[int] = Field(None, description="排序号")

    @field_validator("F_ENABLED", "F_SORT", mode="before")
    @classmethod
    def _coerce_optional_int(cls, v):
        return _empty_str_to_none_int(v, default=None)


class DictCategoryOut(DictCategoryBase):
    """字典类别输出"""
    F_ID: int
    F_CREATE_TIME: Optional[datetime] = None
    F_UPDATE_TIME: Optional[datetime] = None

    class Config:
        from_attributes = True


class DictCategoryListQuery(BaseModel):
    """字典类别列表查询"""
    F_MODULE: Optional[str] = Field(None, description="所属模块过滤")
    F_ENABLED: Optional[int] = Field(None, description="是否启用过滤：1启用 0禁用")
    skip: int = Field(0, ge=0, description="跳过记录数")
    limit: int = Field(100, ge=1, le=1000, description="每页记录数")

    @field_validator("F_ENABLED", mode="before")
    @classmethod
    def _coerce_enabled_filter(cls, v):
        return _empty_str_to_none_int(v, default=None)


class DictCategoryListOut(BaseModel):
    """字典类别列表输出"""
    items: List[DictCategoryOut]
    total: int


# ============================================
# 字典项相关 Schemas
# ============================================

class DictItemBase(BaseModel):
    """字典项基础模型"""
    F_CATEGORY_CODE: str = Field(..., description="关联类别代码", max_length=50)
    F_CODE: Optional[str] = Field(None, description="项代码", max_length=50)
    F_VALUE: str = Field(..., description="显示值", max_length=200)
    F_SORT: int = Field(0, description="排序号")
    F_ENABLED: int = Field(1, description="是否启用：1启用 0禁用")
    F_IS_DEFAULT: int = Field(0, description="是否默认选项：1是 0否")
    F_REMARK: Optional[str] = Field(None, description="备注", max_length=500)

    @field_validator("F_SORT", "F_IS_DEFAULT", mode="before")
    @classmethod
    def _coerce_zero_default(cls, v):
        return _empty_str_to_none_int(v, default=0)

    @field_validator("F_ENABLED", mode="before")
    @classmethod
    def _coerce_enabled(cls, v):
        return _empty_str_to_none_int(v, default=1)


class DictItemCreate(DictItemBase):
    """创建字典项"""
    pass


class DictItemUpdate(BaseModel):
    """更新字典项"""
    F_ID: int = Field(..., description="字典项ID（必填）")
    F_CODE: Optional[str] = Field(None, description="项代码", max_length=50)
    F_VALUE: Optional[str] = Field(None, description="显示值", max_length=200)
    F_SORT: Optional[int] = Field(None, description="排序号")
    F_ENABLED: Optional[int] = Field(None, description="是否启用：1启用 0禁用")
    F_IS_DEFAULT: Optional[int] = Field(None, description="是否默认选项：1是 0否")
    F_REMARK: Optional[str] = Field(None, description="备注", max_length=500)

    @field_validator("F_SORT", "F_ENABLED", "F_IS_DEFAULT", mode="before")
    @classmethod
    def _coerce_optional_int(cls, v):
        return _empty_str_to_none_int(v, default=None)


class DictItemOut(DictItemBase):
    """字典项输出"""
    F_ID: int
    F_CREATE_TIME: Optional[datetime] = None
    F_UPDATE_TIME: Optional[datetime] = None

    class Config:
        from_attributes = True


class DictItemListQuery(BaseModel):
    """字典项列表查询"""
    F_CATEGORY_CODE: Optional[str] = Field(None, description="类别代码（可选，与F_NAME二选一或均不填则返回全部）")
    F_NAME: Optional[str] = Field(None, description="类别名称精确匹配（可选）")
    F_ENABLED: Optional[int] = Field(None, description="是否启用过滤：1启用 0禁用")
    skip: int = Field(0, ge=0, description="跳过记录数")
    limit: int = Field(1000, ge=1, le=10000, description="每页记录数")

    @field_validator("F_ENABLED", mode="before")
    @classmethod
    def _coerce_enabled_filter(cls, v):
        return _empty_str_to_none_int(v, default=None)


class DictItemListOut(BaseModel):
    """字典项列表输出"""
    items: List[DictItemOut]
    total: int


class DictItemBatchQuery(BaseModel):
    """批量查询字典项"""
    category_codes: str = Field(..., description="类别代码列表，逗号分隔，如 'YWLX,CCLX,XB'")
    enabled_only: bool = Field(True, description="是否只返回启用的项")


class DictItemBatchOut(BaseModel):
    """批量查询字典项输出"""
    data: Dict[str, List[DictItemOut]] = Field(..., description="字典数据，key为类别代码")


class DictItemSortItem(BaseModel):
    """字典项排序项"""
    F_ID: int = Field(..., description="字典项ID")
    F_SORT: int = Field(..., description="新的排序号")

    @field_validator("F_SORT", mode="before")
    @classmethod
    def _coerce_sort(cls, v):
        return _empty_str_to_none_int(v, default=0)


class DictItemSortRequest(BaseModel):
    """批量排序请求"""
    F_CATEGORY_CODE: str = Field(..., description="类别代码")
    items: List[DictItemSortItem] = Field(..., description="排序项列表")


class DictItemBatchCreateItem(BaseModel):
    """批量创建字典项的单个项（不包含 F_CATEGORY_CODE）"""
    F_CODE: Optional[str] = Field(None, description="项代码", max_length=50)
    F_VALUE: str = Field(..., description="显示值", max_length=200)
    F_SORT: int = Field(0, description="排序号")
    F_ENABLED: int = Field(1, description="是否启用：1启用 0禁用")
    F_IS_DEFAULT: int = Field(0, description="是否默认选项：1是 0否")
    F_REMARK: Optional[str] = Field(None, description="备注", max_length=500)

    @field_validator("F_SORT", "F_IS_DEFAULT", mode="before")
    @classmethod
    def _coerce_zero_default(cls, v):
        return _empty_str_to_none_int(v, default=0)

    @field_validator("F_ENABLED", mode="before")
    @classmethod
    def _coerce_enabled(cls, v):
        return _empty_str_to_none_int(v, default=1)


class DictItemBatchCreateRequest(BaseModel):
    """批量创建字典项请求"""
    F_CATEGORY_CODE: str = Field(..., description="类别代码（必填）", max_length=50)
    items: List[DictItemBatchCreateItem] = Field(..., description="字典项列表", min_items=1)


class DictItemBatchCreateOut(BaseModel):
    """批量创建字典项输出"""
    success_count: int = Field(..., description="成功创建的数量")
    failed_count: int = Field(..., description="失败的数量")
    created_ids: List[int] = Field(..., description="成功创建的ID列表")
    errors: List[str] = Field(default_factory=list, description="错误信息列表")


class DictItemBatchUpdateItem(BaseModel):
    """批量更新字典项的单个项"""
    F_ID: int = Field(..., description="字典项ID（必填）")
    F_CODE: Optional[str] = Field(None, description="项代码", max_length=50)
    F_VALUE: Optional[str] = Field(None, description="显示值", max_length=200)
    F_SORT: Optional[int] = Field(None, description="排序号")
    F_ENABLED: Optional[int] = Field(None, description="是否启用：1启用 0禁用")
    F_IS_DEFAULT: Optional[int] = Field(None, description="是否默认选项：1是 0否")
    F_REMARK: Optional[str] = Field(None, description="备注", max_length=500)

    @field_validator("F_SORT", "F_ENABLED", "F_IS_DEFAULT", mode="before")
    @classmethod
    def _coerce_optional_int(cls, v):
        return _empty_str_to_none_int(v, default=None)


class DictItemBatchUpdateRequest(BaseModel):
    """批量更新字典项请求"""
    items: List[DictItemBatchUpdateItem] = Field(..., description="字典项列表", min_items=1)


class DictItemBatchUpdateOut(BaseModel):
    """批量更新字典项输出"""
    success_count: int = Field(..., description="成功更新的数量")
    failed_count: int = Field(..., description="失败的数量")
    updated_ids: List[int] = Field(..., description="成功更新的ID列表")
    errors: List[str] = Field(default_factory=list, description="错误信息列表")


# ============================================
# 通用响应 Schemas
# ============================================

class DictDeleteRequest(BaseModel):
    """删除请求"""
    F_ID: int = Field(..., description="要删除的ID")
