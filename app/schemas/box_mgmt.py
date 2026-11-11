"""
盒子管理模块 Schemas
"""
from pydantic import BaseModel, Field
from typing import Optional, List
from datetime import datetime


# ============================================
# 盒子模板相关 Schemas
# ============================================

class BoxTpltBase(BaseModel):
    F_CODE: str = Field(..., description="模板代码，如 BOX_81", max_length=50)
    F_NAME: str = Field(..., description="模板名称，如 81孔冻存盒", max_length=100)
    F_HS: int = Field(..., description="行数", ge=1)
    F_LS: int = Field(..., description="列数", ge=1)
    F_ENABLED: int = Field(1, description="是否启用：1启用 0禁用")
    F_SORT: int = Field(0, description="排序号")
    F_BZ: Optional[str] = Field(None, description="备注", max_length=500)


class BoxTpltCreate(BoxTpltBase):
    pass


class BoxTpltUpdate(BaseModel):
    F_ID: int = Field(..., description="模板ID（必填）")
    F_CODE: Optional[str] = Field(None, description="模板代码", max_length=50)
    F_NAME: Optional[str] = Field(None, description="模板名称", max_length=100)
    F_HS: Optional[int] = Field(None, description="行数", ge=1)
    F_LS: Optional[int] = Field(None, description="列数", ge=1)
    F_ENABLED: Optional[int] = Field(None, description="是否启用：1启用 0禁用")
    F_SORT: Optional[int] = Field(None, description="排序号")
    F_BZ: Optional[str] = Field(None, description="备注", max_length=500)


class BoxTpltOut(BoxTpltBase):
    F_ID: int
    F_CAPACITY: Optional[int] = Field(None, description="总容量（行数×列数，查询时计算）")
    F_CREATE_TIME: Optional[datetime] = None
    F_UPDATE_TIME: Optional[datetime] = None

    class Config:
        from_attributes = True


class BoxTpltListQuery(BaseModel):
    F_ENABLED: Optional[int] = Field(None, description="是否启用过滤：1启用 0禁用")
    skip: int = Field(0, ge=0, description="跳过记录数")
    limit: int = Field(100, ge=1, le=1000, description="每页记录数")


class BoxTpltListOut(BaseModel):
    items: List[BoxTpltOut]
    total: int


# ============================================
# 盒子使用相关 Schemas
# ============================================

class BoxCreate(BaseModel):
    F_HZH: str = Field(..., description="盒号", max_length=100)
    F_TEMPLATE_ID: int = Field(..., description="模板ID（必填）")
    F_STATUS: str = Field("未运送", description="状态：未运送 / 运送中 / 已转运 / 已封存", max_length=20)
    F_LOCATION: Optional[str] = Field(None, description="存放位置", max_length=200)
    F_CZR: Optional[str] = Field(None, description="操作人", max_length=100)
    F_BZ: Optional[str] = Field(None, description="备注", max_length=500)


class BoxUpdate(BaseModel):
    F_ID: int = Field(..., description="盒子ID（必填）")
    F_STATUS: Optional[str] = Field(None, description="状态：未运送 / 运送中 / 已转运 / 已封存", max_length=20)
    F_LOCATION: Optional[str] = Field(None, description="存放位置", max_length=200)
    F_BZ: Optional[str] = Field(None, description="备注", max_length=500)


class BoxOut(BaseModel):
    F_ID: int
    F_HZH: str
    F_TEMPLATE_ID: int
    F_STATUS: Optional[str] = None
    F_LOCATION: Optional[str] = None
    F_CZR: Optional[str] = None
    F_BZ: Optional[str] = None
    F_CREATE_TIME: Optional[datetime] = None
    F_UPDATE_TIME: Optional[datetime] = None
    # 关联模板信息（详情接口时填充）
    F_TPLT_NAME: Optional[str] = Field(None, description="模板名称")
    F_TPLT_HS: Optional[int] = Field(None, description="模板行数")
    F_TPLT_LS: Optional[int] = Field(None, description="模板列数")

    class Config:
        from_attributes = True


class BoxListQuery(BaseModel):
    F_HZH: Optional[str] = Field(None, description="盒号，模糊匹配")
    F_STATUS: Optional[str] = Field(None, description="状态过滤")
    F_TEMPLATE_ID: Optional[int] = Field(None, description="模板ID过滤")
    F_ZCSJ_START: Optional[str] = Field(None, description="暂存时间起始，格式 YYYY-MM-DD 或 YYYY-MM-DD HH:MM:SS")
    F_ZCSJ_END: Optional[str] = Field(None, description="暂存时间结束，格式 YYYY-MM-DD 或 YYYY-MM-DD HH:MM:SS")
    F_CREATE_START: Optional[str] = Field(None, description="同 F_ZCSJ_START，兼容旧入参")
    F_CREATE_END: Optional[str] = Field(None, description="同 F_ZCSJ_END，兼容旧入参")
    skip: int = Field(0, ge=0, description="跳过记录数")
    limit: int = Field(50, ge=1, le=500, description="每页记录数")


class BoxListOut(BaseModel):
    items: List[BoxOut]
    total: int


class BoxDeleteRequest(BaseModel):
    F_ID: int = Field(..., description="要删除的盒子ID")


class BoxHzhRequest(BaseModel):
    """以盒号为入参的通用请求体（detail / capacity / layout 接口共用）"""
    F_HZH: str = Field(..., description="盒号")


class BoxDetailRequest(BaseModel):
    """盒子详情请求体，支持按样本管状态过滤"""
    F_HZH: str = Field(..., description="盒号")
    F_YSZT: Optional[str] = Field(
        default=None,
        description="按样本管运输状态过滤：'未运送' / '运送中' / '已接收'；"
                    "不传则返回盒内全部样本管。capacity/used/remaining 始终基于全量计算，不受此过滤影响"
    )


class BoxCapacityOut(BaseModel):
    F_HZH: str = Field(..., description="盒号")
    F_STATUS: Optional[str] = Field(None, description="盒子状态：未运送 / 运送中 / 已转运 / 已封存")
    F_TEMPLATE_ID: Optional[int] = None
    F_TPLT_NAME: Optional[str] = None
    capacity: int = Field(..., description="总容量（行×列）")
    used: int = Field(..., description="已占用格数（唯一格子，与平面图一致）")
    remaining: int = Field(..., description="剩余空间")
    tubes_count: Optional[int] = Field(None, description="盒内样本管记录总数（含同格重复，可选）")


# ============================================
# 盒子平面图相关 Schemas
# ============================================

class BoxCellSample(BaseModel):
    """格子内样本管简要信息（用于前端悬浮/点击弹窗）"""
    F_ID: Optional[int] = None
    F_SGBH: Optional[str] = None
    F_ZZID: Optional[str] = None
    F_YBZJ_ID: Optional[int] = None
    F_PYGD_ID: Optional[int] = None
    F_DC: Optional[str] = None
    F_YBLX: Optional[str] = None
    F_YBGLX: Optional[str] = None
    F_XM: Optional[str] = None
    F_BRBH: Optional[str] = None
    F_ZYH: Optional[str] = None
    F_YSZT: Optional[str] = None
    F_CJSJ: Optional[str] = None

    class Config:
        from_attributes = True


class BoxCellItem(BaseModel):
    """盒子平面图中的单个格子"""
    wz: int = Field(..., description="格子序号（从 1 开始，按行优先排列）")
    row: int = Field(..., description="所在行（从 1 开始）")
    col: int = Field(..., description="所在列（从 1 开始）")
    occupied: bool = Field(..., description="是否已占用")
    sample: Optional[BoxCellSample] = Field(None, description="占用此格的样本管信息，未占用时为 null")


class BoxLayoutOut(BaseModel):
    """盒子平面图完整响应"""
    F_HZH: str = Field(..., description="盒号")
    F_TEMPLATE_ID: Optional[int] = None
    F_TPLT_NAME: Optional[str] = None
    F_STATUS: Optional[str] = None
    F_LOCATION: Optional[str] = None
    rows: int = Field(..., description="总行数")
    cols: int = Field(..., description="总列数")
    capacity: int = Field(..., description="总容量")
    used: int = Field(..., description="已占用格数")
    remaining: int = Field(..., description="剩余格数")
    cells: List[BoxCellItem] = Field(default_factory=list, description="所有格子（含空格子），共 rows×cols 个")


# ============================================
# 盒子维度分装管理 Schemas
# ============================================



class TubePositionItem(BaseModel):
    """单条位置更新记录"""
    F_ID: int = Field(..., description="T_YBFZ 主键（分装记录ID）")
    F_HZH: Optional[str] = Field(None, description="新盒号，为空则保持当前盒不变")
    F_WZ: Optional[str] = Field(None, description="新格子坐标，字母数字格式如 A1、B3；为空则保持不变")


class BoxUpdatePositionsRequest(BaseModel):
    """批量更新暂存分装记录的盒号和格子位置"""
    ITEMS: List[TubePositionItem] = Field(
        ...,
        description="待更新的分装记录列表，每条必须提供 F_ID"
    )
    BOXES: Optional[List] = Field(
        default_factory=list,
        description="若有新盒子需要创建，在此提供 {F_HZH, F_TEMPLATE_ID}；已存在的盒子无需列出"
    )


# ============================================
# 盒号编码预览（正式占号在 create_box / 样本关联保存时内嵌）
# ============================================

class PreviewHzhRequest(BaseModel):
    """预览盒号入参（不消耗序列号）"""
    context: Optional[dict] = Field(
        default=None,
        description="编码规则上下文（可选），如模板相关字段；多数场景传 {} 或不传即可",
    )


class PreviewHzhOut(BaseModel):
    """预览盒号出参"""
    F_HZH: Optional[str] = Field(None, description="预计盒号；未配置规则时为 null，前端应允许手填")
    rule_available: bool = Field(..., description="是否已配置并启用字典编码规则 F_HZH")
    source: str = Field(..., description="code_rule=规则窥探；none=未配置规则")
