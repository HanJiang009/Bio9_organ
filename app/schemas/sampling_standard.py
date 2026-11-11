"""
采集标准模块 Schemas

包含两个子模块：
- 项目信息维护（占位符模块）：T_XMXX
- 采集模板：T_CJMB（一个模板名称占一条记录，明细以 JSON 数组嵌套在 F_MBMX 字段内）
"""
from pydantic import BaseModel, Field, field_validator
from typing import Optional, List, Any
from datetime import datetime


def _coerce_xbsl_to_str(v: Any) -> Optional[str]:
    """细胞数量对齐样本关联 FZXX：统一为字符串，兼容 int/float 入参及存量 JSON。"""
    if v is None:
        return None
    if isinstance(v, bool):
        return str(v)
    if isinstance(v, int):
        return str(v)
    if isinstance(v, float):
        return str(int(v)) if v.is_integer() else str(v)
    if isinstance(v, str):
        s = v.strip()
        return s if s else None
    s = str(v).strip()
    return s if s else None


# ============================================
# 项目信息维护相关 Schemas
# ============================================

class ProjectBase(BaseModel):
    """项目信息基础模型"""
    F_XMMC: str = Field(..., description="项目名称", max_length=200)
    F_XMBH: str = Field(..., description="项目编号", max_length=50)


class ProjectCreate(ProjectBase):
    """创建项目信息"""
    pass


class ProjectUpdate(BaseModel):
    """更新项目信息"""
    F_ID: int = Field(..., description="项目ID（必填）")
    F_XMMC: Optional[str] = Field(None, description="项目名称", max_length=200)
    F_XMBH: Optional[str] = Field(None, description="项目编号", max_length=50)


class ProjectOut(ProjectBase):
    """项目信息输出"""
    F_ID: int
    F_CREATE_TIME: Optional[datetime] = None

    class Config:
        from_attributes = True


class ProjectListQuery(BaseModel):
    """项目信息列表查询"""
    F_XMMC: Optional[str] = Field(None, description="项目名称模糊查询（可选）")
    F_XMBH: Optional[str] = Field(None, description="项目编号模糊查询（可选）")
    skip: int = Field(0, ge=0, description="跳过记录数")
    limit: int = Field(100, ge=1, le=1000, description="每页记录数")


class ProjectListOut(BaseModel):
    """项目信息列表输出"""
    items: List[ProjectOut]
    total: int


class ProjectDeleteRequest(BaseModel):
    """删除项目信息请求"""
    F_ID: int = Field(..., description="要删除的项目ID")


# ============================================
# 采集模板相关 Schemas
# ============================================

class TemplateItem(BaseModel):
    """
    采集模板明细（嵌套在 T_CJMB.F_MBMX 的 JSON 数组内的单个元素）。
    F_ID 为应用层自增标识，新增时可不传（由服务端分配）。
    字段命名对齐样本关联模块 FZXX（SampleAllocationInfo），方便直接套用到
    /sample-linkage/assign/save 的分装信息中。
    """
    F_ID: Optional[int] = Field(None, description="模板明细ID（应用层自增，新增时可不传）")
    F_YBLX: Optional[str] = Field(None, description="样本类型")
    F_CCXS: Optional[str] = Field(None, description="储藏形式")
    F_FGS: Optional[int] = Field(None, description="管数", ge=1)
    F_TJ: Optional[str] = Field(None, description="体积")
    F_YBGLX: Optional[str] = Field(None, description="样本管类型")
    F_XBSL: Optional[str] = Field(None, description="细胞数量")
    F_BZ: Optional[str] = Field(None, description="备注")

    @field_validator("F_XBSL", mode="before")
    @classmethod
    def _coerce_xbsl(cls, v: Any) -> Optional[str]:
        return _coerce_xbsl_to_str(v)


class TemplateCreate(BaseModel):
    """创建采集模板（整条记录，含模板名称与明细列表）"""
    F_MBMC: str = Field(..., description="模板名称", max_length=200)
    MBMX: List[TemplateItem] = Field(default_factory=list, description="模板明细列表")


class TemplateUpdate(BaseModel):
    """
    更新采集模板（整条记录）。
    传入 F_MBMC 则更新模板名称；传入 MBMX 则整体全量替换明细数组（F_ID 重新从 1 编号）。
    """
    F_ID: int = Field(..., description="模板ID（必填）")
    F_MBMC: Optional[str] = Field(None, description="模板名称", max_length=200)
    MBMX: Optional[List[TemplateItem]] = Field(None, description="模板明细列表（传入则整体全量替换）")


class TemplateOut(BaseModel):
    """采集模板输出"""
    F_ID: int
    F_MBMC: str
    MBMX: List[TemplateItem] = Field(default_factory=list)
    F_CREATE_TIME: Optional[datetime] = None
    F_UPDATE_TIME: Optional[datetime] = None


class TemplateListQuery(BaseModel):
    """采集模板列表查询"""
    F_MBMC: Optional[str] = Field(None, description="模板名称模糊查询（可选）")
    skip: int = Field(0, ge=0, description="跳过记录数")
    limit: int = Field(100, ge=1, le=1000, description="每页记录数")


class TemplateListOut(BaseModel):
    """采集模板列表输出"""
    items: List[TemplateOut]
    total: int


class TemplateGetRequest(BaseModel):
    """按ID查询单条采集模板请求"""
    F_ID: int = Field(..., description="模板ID")


class TemplateDeleteRequest(BaseModel):
    """删除采集模板（整条记录）请求"""
    F_ID: int = Field(..., description="要删除的模板ID")


class TemplateItemAddRequest(BaseModel):
    """精准新增一条模板明细"""
    F_ID: int = Field(..., description="模板ID（必填）")
    F_YBLX: Optional[str] = Field(None, description="样本类型")
    F_CCXS: Optional[str] = Field(None, description="储藏形式")
    F_FGS: Optional[int] = Field(None, description="管数", ge=1)
    F_TJ: Optional[str] = Field(None, description="体积")
    F_YBGLX: Optional[str] = Field(None, description="样本管类型")
    F_XBSL: Optional[str] = Field(None, description="细胞数量")
    F_BZ: Optional[str] = Field(None, description="备注")

    @field_validator("F_XBSL", mode="before")
    @classmethod
    def _coerce_xbsl(cls, v: Any) -> Optional[str]:
        return _coerce_xbsl_to_str(v)


class TemplateItemUpdateRequest(BaseModel):
    """精准修改一条模板明细（仅覆盖传入的非空字段）"""
    F_ID: int = Field(..., description="模板ID（必填）")
    F_ITEM_ID: int = Field(..., description="模板明细ID（必填）")
    F_YBLX: Optional[str] = Field(None, description="样本类型")
    F_CCXS: Optional[str] = Field(None, description="储藏形式")
    F_FGS: Optional[int] = Field(None, description="管数", ge=1)
    F_TJ: Optional[str] = Field(None, description="体积")
    F_YBGLX: Optional[str] = Field(None, description="样本管类型")
    F_XBSL: Optional[str] = Field(None, description="细胞数量")
    F_BZ: Optional[str] = Field(None, description="备注")

    @field_validator("F_XBSL", mode="before")
    @classmethod
    def _coerce_xbsl(cls, v: Any) -> Optional[str]:
        return _coerce_xbsl_to_str(v)


class TemplateItemDeleteRequest(BaseModel):
    """精准删除一条模板明细"""
    F_ID: int = Field(..., description="模板ID（必填）")
    F_ITEM_ID: int = Field(..., description="模板明细ID（必填）")
