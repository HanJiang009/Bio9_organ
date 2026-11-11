from pydantic import BaseModel, Field
from typing import Optional, List, Any
from datetime import datetime


# ==================== 动物接种信息 ====================

class AnimalInoculationItem(BaseModel):
    """动物接种信息（创建时）"""
    F_DWBH:    Optional[str] = Field(None, description="动物编号")
    F_XB:      Optional[str] = Field(None, description="性别（雄/雌）")
    F_NL:      Optional[str] = Field(None, description="年龄（如：6周龄）")
    F_LH:      Optional[str] = Field(None, description="笼号")
    F_ZB:      Optional[str] = Field(None, description="组别")
    F_JHYZSJ:  Optional[str] = Field(None, description="计划移植时间")
    F_JHJZXBS: Optional[str] = Field(None, description="计划接种细胞数（如：5×10⁶）")
    F_JHYZBW:  Optional[str] = Field(None, description="计划移植部位")
    F_JHCZR:   Optional[str] = Field(None, description="计划操作人")
    F_SORT:    Optional[int] = Field(None, description="排序（不传则按数组顺序自动生成）")


class AnimalInoculationDetail(AnimalInoculationItem):
    """动物接种信息详情（含ID）"""
    F_ID: int


class AnimalInoculationItemUpdate(AnimalInoculationItem):
    """更新时的动物接种信息（有F_ID=更新已有记录，无F_ID=新增记录）"""
    F_ID: Optional[int] = Field(None, description="接种记录ID：有则更新，无则新增")


# ==================== 动物实验计划 - 创建 ====================

class AnimalTestPlanCreateIn(BaseModel):
    """创建动物实验计划"""
    F_GDID:    int            = Field(..., description="关联工单ID")
    F_SYMC:    str            = Field(..., description="实验名称")
    F_SYKSRQ:  Optional[str]  = Field(None, description="实验开始日期")
    F_SYR:     Optional[str]  = Field(None, description="实验人")
    F_SYJHYMD: Optional[str]  = Field(None, description="实验计划与目的")
    F_XBSL:   Optional[str]  = Field(None, description="细胞数量")
    F_JZJND:   Optional[str]  = Field(None, description="基质胶浓度")
    F_JZJTJ:   Optional[str]  = Field(None, description="基质胶体积")
    F_FJ:      Optional[int]  = Field(None, description="附件ID")
    F_BZ:      Optional[str]  = Field(None, description="备注")
    JZXX: List[AnimalInoculationItem] = Field(default_factory=list, description="动物接种信息列表")


class AnimalTestPlanCreateOut(BaseModel):
    """创建动物实验计划返回"""
    F_ID:   Optional[int] = None
    F_GDID: Optional[int] = None
    F_SYBH: Optional[str] = None
    F_SYMC: Optional[str] = None


# ==================== 动物实验计划 - 更新 ====================

class AnimalTestPlanUpdateIn(BaseModel):
    """更新动物实验计划"""
    F_ID:      int            = Field(..., description="实验计划ID")
    F_SYMC:    Optional[str]  = Field(None, description="实验名称")
    F_SYKSRQ:  Optional[str]  = Field(None, description="实验开始日期")
    F_SYR:     Optional[str]  = Field(None, description="实验人")
    F_SYJHYMD: Optional[str]  = Field(None, description="实验计划与目的")
    F_XBSL:   Optional[str]  = Field(None, description="细胞数量")
    F_JZJND:   Optional[str]  = Field(None, description="基质胶浓度")
    F_JZJTJ:   Optional[str]  = Field(None, description="基质胶体积")
    F_FJ:      Optional[int]  = Field(None, description="附件ID")
    F_ZT:      Optional[str]  = Field(None, description="状态")
    F_BZ:      Optional[str]  = Field(None, description="备注")

    JZXX: Optional[List[AnimalInoculationItemUpdate]] = Field(
        None,
        description="接种信息增量更新：有F_ID=更新，无F_ID=新增"
    )
    JZXX_DEL_IDS: Optional[List[int]] = Field(
        None,
        description="要删除的接种记录ID列表，如 [2, 3]"
    )


# ==================== 工单样本信息 ====================

class WorkOrderInfo(BaseModel):
    """工单样本信息"""
    F_ZZID: Optional[str] = Field(None, description="标本号")
    F_DCID: Optional[str] = Field(None, description="代次ID")
    F_DC:   Optional[str] = Field(None, description="代次")
    F_GDBH: Optional[str] = Field(None, description="培养工单编号")
    F_YBLY: Optional[str] = Field(None, description="样本来源")
    F_SQR:  Optional[str] = Field(None, description="培养人")
    F_PYSJ: Optional[str] = Field(None, description="开始培养日期")
    F_ZZBW: Optional[str] = Field(None, description="组织部位")


# ==================== 动物实验计划 - 详情 ====================

class AnimalTestPlanDetailOut(BaseModel):
    """动物实验计划详情"""
    F_ID:      int
    F_GDID:    Optional[int] = None
    F_SYBH:    Optional[str] = Field(None, description="实验编号")
    F_SYMC:    str
    F_ZT:      str
    F_SYKSRQ:  Optional[str] = None
    F_SYR:     Optional[str] = None
    F_SYJHYMD: Optional[str] = None
    F_XBSL:   Optional[str] = None
    F_JZJND:   Optional[str] = None
    F_JZJTJ:   Optional[str] = None
    F_FJ:      Optional[str] = None   # 返回静态路径
    F_BHG:     Optional[str] = None
    F_BHGYY:   Optional[str] = None
    F_CJSJ:    str
    F_BZ:      Optional[str] = None

    GDXX: Optional[WorkOrderInfo]                    = Field(None, description="工单样本信息")
    JZXX: List[AnimalInoculationDetail]              = Field(default_factory=list, description="动物接种信息列表")


# ==================== 动物实验计划 - 列表 ====================

class AnimalTestPlanListQuery(BaseModel):
    """动物实验计划列表查询"""
    F_GDID:  Optional[int] = Field(None, description="工单ID")
    F_SYBH:  Optional[str] = Field(None, description="实验编号（模糊）")
    F_SYMC:  Optional[str] = Field(None, description="实验名称（模糊）")
    F_SYR:   Optional[str] = Field(None, description="实验人（模糊）")
    F_ZT:    Optional[str] = Field(None, description="状态")
    F_ZZID:  Optional[str] = Field(None, description="标本号（模糊）")
    F_GDBH:  Optional[str] = Field(None, description="培养工单编号（模糊）")
    F_DCID:  Optional[str] = Field(None, description="代次ID（模糊）")
    F_ZZBW:  Optional[str] = Field(None, description="组织部位（模糊）")
    F_SQR:   Optional[str] = Field(None, description="培养人（模糊）")
    skip:  int = Field(0, ge=0, description="跳过记录数")
    limit: int = Field(20, ge=1, le=100, description="返回记录数")


class AnimalTestPlanListItem(BaseModel):
    """动物实验计划列表项"""
    F_ID:      int
    F_GDID:    Optional[int] = None
    F_SYBH:    Optional[str] = Field(None, description="实验编号")
    F_SYMC:    str
    F_ZT:      str
    F_SYKSRQ:  Optional[str] = None
    F_SYR:     Optional[str] = None
    F_CJSJ:    str
    F_ANIMAL_COUNT: int = Field(default=0, description="动物接种数量")

    # 工单信息
    F_ZZID: Optional[str] = Field(None, description="标本号")
    F_DCID: Optional[str] = Field(None, description="代次ID")
    F_GDBH: Optional[str] = Field(None, description="培养工单编号")
    F_ZZBW: Optional[str] = Field(None, description="组织部位")
    F_YBLY: Optional[str] = Field(None, description="样本来源")
    F_SQR:  Optional[str] = Field(None, description="培养人")
    F_PYSJ: Optional[str] = Field(None, description="开始培养日期")

    # 报告PDF（预留）
    F_BG_PDF: Optional[Any] = Field(None, description="报告PDF")


class AnimalTestPlanListOut(BaseModel):
    """动物实验计划列表返回"""
    items: List[AnimalTestPlanListItem]
    total: int


# ==================== 其他操作 ====================

class AnimalTestPlanDeleteIn(BaseModel):
    """删除动物实验计划"""
    F_ID: int = Field(..., description="实验计划ID")


class AnimalTestPlanStatusUpdateIn(BaseModel):
    """更新动物实验计划状态"""
    F_ID: int = Field(..., description="实验计划ID")
    F_ZT: str = Field(..., description="状态：已创建/进行中/已完成")


class MarkAnimalTestUnqualifiedIn(BaseModel):
    """标记动物实验计划为不合格"""
    F_SYJH_ID: int            = Field(..., gt=0, description="实验计划ID")
    F_BHGYY:   Optional[str]  = Field(None, description="不合格原因")
