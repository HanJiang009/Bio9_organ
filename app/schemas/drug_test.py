from pydantic import BaseModel, Field
from typing import Optional, List, Any, Union
from datetime import datetime


# ==================== 用药信息 ====================
class DrugUsageItem(BaseModel):
    """单个用药信息"""
    F_SJID: int = Field(..., description="试剂ID")
    F_PCH: str = Field(..., description="批次号")
    F_YL: str = Field(..., description="用量")
    F_XSFA: Optional[str] = Field(None, description="稀释方案")


class DrugUsageDetail(DrugUsageItem):
    """用药信息详情（包含ID和出库单号）"""
    F_ID: int
    F_CKDH: Optional[str] = Field(None, description="出库单号")


# ==================== 组别信息 ====================
class GroupItem(BaseModel):
    """实验组别"""
    F_ZBMC: str = Field(..., description="组别名称")
    F_ZBLX: str = Field(..., description="组别类型")
    F_SORT: Optional[int] = Field(None, description="排序（可选，不传则按数组顺序自动生成1,2,3...）")
    F_FKSSZ: int = Field(..., description="复孔数设置（组别级）")
    F_ND: List[Union[int, float, str]] = Field(..., description="浓度数组（组别级），支持整数、浮点数或字符串")
    F_YY: List[DrugUsageItem] = Field(default_factory=list, description="用药信息列表")


class GroupDetail(BaseModel):
    """组别详情"""
    F_ID: int
    F_ZBMC: str
    F_ZBLX: str
    F_SORT: Optional[int] = None
    F_FKSSZ: int
    F_ND: List[Union[int, float, str]] = Field(default_factory=list, description="浓度数组，支持整数、浮点数或字符串")
    F_YY: List[DrugUsageDetail] = Field(default_factory=list, description="用药信息列表")


# ==================== 孔位信息 ====================
class WellItem(BaseModel):
    """孔位信息"""
    F_WZ: str = Field(..., description="位置（A1, A2...）")
    F_ZBMC: str = Field(..., description="组别名称")
    F_ND: str = Field(..., description="浓度")
    F_JYTJ: Optional[str] = Field(None, description="加药体积")
    F_XSFA: Optional[str] = Field(None, description="稀释方案")
    F_YS: Optional[str] = Field(None, description="颜色")


class WellDetail(WellItem):
    """孔位详情（包含ID）"""
    F_ID: int


# ==================== 实验计划 - 创建 ====================
class TestPlanCreateIn(BaseModel):
    """创建实验计划"""
    F_GDID: int = Field(..., description="关联工单ID")
    F_SYMC: Optional[str] = Field(None, description="实验名称")
    F_SYBH: Optional[str] = Field(None, description="实验编号（系统自动生成）")
    F_SYKSRQ: Optional[str] = Field(None, description="试验开始日期")
    F_SYR: Optional[str] = Field(None, description="实验人")
    F_SYJHYMD: Optional[str] = Field(None, description="实验计划与目的")
    F_XBSL: Optional[str] = Field(None, description="细胞数量")
    F_JZJND: Optional[str] = Field(None, description="基质胶浓度")
    F_JZJTJ: Optional[str] = Field(None, description="基质胶体积")
    F_FJ: Optional[int] = Field(None, description="附件ID")
    F_KBLX: Optional[str] = Field(None, description="孔板类型")
    F_KBBH: Optional[str] = Field(None, description="孔板编号")
    F_BZ: Optional[str] = Field(None, description="备注")
    
    ZBXX: List[GroupItem] = Field(..., description="实验组别列表")
    KWXX: List[WellItem] = Field(..., description="孔位信息列表")


class TestPlanCreateOut(BaseModel):
    """创建实验计划返回"""
    F_ID: Optional[int] = None
    F_GDID: Optional[int] = None
    F_SYMC: Optional[str] = None
    F_SYBH: str = Field(..., description="实验编号")
    auto_outbound_count: int = Field(default=0, description="自动生成的出库单数量")
    auto_outbound_codes: List[str] = Field(default_factory=list, description="自动生成的出库单号列表")


# ==================== 实验计划 - 更新 ====================
class GroupItemUpdate(BaseModel):
    """更新时的组别信息（有F_ID=更新已有组别，无F_ID=新增组别）"""
    F_ID: Optional[int] = Field(None, description="组别ID：有则更新，无则新增")
    F_ZBMC: str = Field(..., description="组别名称")
    F_ZBLX: str = Field(..., description="组别类型")
    F_SORT: Optional[int] = Field(None, description="排序")
    F_FKSSZ: int = Field(..., description="复孔数设置")
    F_ND: List[Union[int, float, str]] = Field(..., description="浓度数组")
    F_YY: List[DrugUsageItem] = Field(default_factory=list, description="用药信息列表")


class TestPlanUpdateIn(BaseModel):
    """更新实验计划"""
    F_ID: int = Field(..., description="实验计划ID")
    F_SYMC: Optional[str] = Field(None, description="实验名称")
    F_SYKSRQ: Optional[str] = Field(None, description="试验开始日期")
    F_SYR: Optional[str] = Field(None, description="实验人")
    F_SYJHYMD: Optional[str] = Field(None, description="实验计划与目的")
    F_XBSL: Optional[str] = Field(None, description="细胞数量")
    F_JZJND: Optional[str] = Field(None, description="基质胶浓度")
    F_JZJTJ: Optional[str] = Field(None, description="基质胶体积")
    F_FJ: Optional[int] = Field(None, description="附件ID")
    F_KBLX: Optional[str] = Field(None, description="孔板类型")
    F_KBBH: Optional[str] = Field(None, description="孔板编号")
    F_ZT: Optional[str] = Field(None, description="状态")
    F_BZ: Optional[str] = Field(None, description="备注")

    ZBXX: Optional[List[GroupItemUpdate]] = Field(
        None,
        description="组别列表：有F_ID=更新，无F_ID=新增"
    )
    ZBXX_DEL_IDS: Optional[List[int]] = Field(
        None,
        description="要删除的组别ID列表，如 [2, 3]"
    )
    KWXX: Optional[List[WellItem]] = Field(None, description="孔位信息列表（更新则覆盖）")


# ==================== 工单样本信息 ====================
class WorkOrderInfo(BaseModel):
    """工单样本信息"""
    F_ZZID: Optional[str] = Field(None, description="标本号")
    F_DCID: Optional[str] = Field(None, description="代次ID")
    F_DC: Optional[str] = Field(None, description="代次")
    F_GDBH: Optional[str] = Field(None, description="培养工单编号")
    F_YBLY: Optional[str] = Field(None, description="样本来源")
    F_SQR: Optional[str] = Field(None, description="培养人")
    F_PYSJ: Optional[str] = Field(None, description="开始培养日期")


# ==================== 实验计划 - 详情 ====================
class TestPlanDetailOut(BaseModel):
    """实验计划详情"""
    F_ID: int
    F_GDID: Optional[int] = None
    F_SYBH: Optional[str] = Field(None, description="实验编号")
    F_SYMC: Optional[str] = Field(None, description="实验名称")
    F_SYKSRQ: Optional[str] = None
    F_SYR: Optional[str] = None
    F_SYJHYMD: Optional[str] = None
    F_XBSL: Optional[str] = None
    F_JZJND: Optional[str] = None
    F_JZJTJ: Optional[str] = None
    F_FJ: Optional[str] = None
    F_KBLX: Optional[str] = None
    F_KBBH: Optional[str] = None
    F_ZT: str
    F_CJSJ: str
    F_BZ: Optional[str] = None
    
    GDXX: Optional[WorkOrderInfo] = Field(None, description="工单样本信息")
    ZBXX: List[GroupDetail] = Field(default_factory=list, description="组别信息")
    KWXX: List[WellDetail] = Field(default_factory=list, description="孔位信息")


# ==================== 实验计划 - 列表 ====================
class TestPlanListQuery(BaseModel):
    """实验计划列表查询"""
    F_GDID: Optional[int] = Field(None, description="工单ID")
    F_SYBH: Optional[str] = Field(None, description="实验编号（模糊）")
    F_SYMC: Optional[str] = Field(None, description="实验名称（模糊）")
    F_SYR: Optional[str] = Field(None, description="实验人（模糊）")
    F_ZT: Optional[str] = Field(None, description="状态")
    F_ZZID: Optional[str] = Field(None, description="标本号（模糊）")
    F_GDBH: Optional[str] = Field(None, description="培养工单编号（模糊）")
    F_DCID: Optional[str] = Field(None, description="代次ID（模糊）")
    F_ZZBW: Optional[str] = Field(None, description="组织部位（模糊）")
    F_SQR: Optional[str] = Field(None, description="培养人（模糊）")
    skip: int = Field(0, ge=0, description="跳过记录数")
    limit: int = Field(20, ge=1, le=100, description="返回记录数")


class TestPlanListItem(BaseModel):
    """实验计划列表项"""
    F_ID: int
    F_GDID: Optional[int] = None
    F_SYBH: Optional[str] = Field(None, description="实验编号")
    F_SYMC: Optional[str] = Field(None, description="实验名称")
    F_SYKSRQ: Optional[str] = None
    F_SYR: Optional[str] = None
    F_GROUP_COUNT: int = Field(default=0, description="组别数量")
    F_WELL_COUNT: int = Field(default=0, description="孔位数量")
    F_KBLX: Optional[str] = None
    F_KBBH: Optional[str] = None
    F_ZT: str
    F_CJSJ: str
    
    # 工单信息
    F_ZZID: Optional[str] = Field(None, description="标本号")
    F_DCID: Optional[str] = Field(None, description="代次ID")
    F_GDBH: Optional[str] = Field(None, description="培养工单编号")
    F_ZZBW: Optional[str] = Field(None, description="组织部位")
    F_YBLY: Optional[str] = Field(None, description="样本来源")
    F_SQR: Optional[str] = Field(None, description="培养人")
    F_PYSJ: Optional[str] = Field(None, description="开始培养日期")
    
    # PDF报告
    F_BG_PDF: Optional[dict] = Field(None, description="药物试验报告PDF")


class TestPlanListOut(BaseModel):
    """实验计划列表返回"""
    items: List[TestPlanListItem]
    total: int


# ==================== 实验计划 - 删除 ====================
class TestPlanDeleteIn(BaseModel):
    """删除实验计划"""
    F_ID: int = Field(..., description="实验计划ID")


# ==================== 状态更新 ====================
class TestPlanStatusUpdateIn(BaseModel):
    """更新实验状态"""
    F_ID: int = Field(..., description="实验计划ID")
    F_ZT: str = Field(..., description="状态：已创建/进行中/已完成")


# ==================== 实验记录（单表 + JSON）====================


class ExperimentRecordSave(BaseModel):
    """
    实验记录保存入参
    
    业务规则：一个实验计划只能有一个实验结果记录
    - 如果该实验计划已有记录 → 自动更新
    - 如果该实验计划没有记录 → 自动新增
    """
    F_SYJH_ID: int = Field(..., description="实验计划ID（根据此ID自动判断新增/更新）")
    
    # 孔板读数（JSON字符串）
    KBDS: Optional[str] = Field(None, description="孔板读数JSON: {\"ZBQ\": {\"A1\": 0.523}, \"ZBH\": {\"A1\": 0.412}}")
    
    # 形态学变化（JSON字符串，含附件索引）
    XTXBH: Optional[str] = Field(None, description="形态学变化JSON: [{\"F_ZBID\": 1, \"F_FJ_INDEX\": [0], \"F_XT\": \"描述\"}]")
    
    # 细胞活率（JSON字符串）
    # 平均活率：{组别ID: {浓度: 值}}
    XBHL_PJ: Optional[str] = Field(None, description="平均细胞活率JSON: {\"1\": {\"0.01\": 98, \"0.1\": 96}}")
    # 孔级活率：{组别ID: {浓度: {孔位: 值}}}
    XBHL: Optional[str] = Field(None, description="细胞活率（孔级）JSON: {\"2\": {\"0.1\": {\"A1\": 78}}}")
    # 标准误差：{组别ID: {浓度: 误差值}}
    XBHL_WC: Optional[str] = Field(None, description="细胞活率标准误差JSON: {\"2\": {\"0.1\": 1.5}}")
    
    # 光敏度（JSON字符串）
    GMD: Optional[str] = Field(None, description="光敏度（孔级）JSON: {\"2\": {\"0.1\": {\"A1\": 10}}}")
    
    # IC50（JSON字符串，用户上传的自定义图片，可选）
    IC50: Optional[str] = Field(None, description="IC50自定义图片JSON: [{\"F_ZBID\": 2, \"F_FJ_INDEX\": 5}]")
    
    # 实验结论（JSON字符串，含附件索引）
    # JGSJ结构：{指标名: {组别ID: 值}}
    SYJL: Optional[str] = Field(None, description="实验结论JSON: {\"JGSJ\": {\"相对IC50\": {\"1\": null, \"2\": 0.45}}, \"JLWB\": \"文本\", \"F_FJ_INDEX\": [4]}")


class ExperimentRecordOut(BaseModel):
    """实验记录输出模型（含实验计划信息）"""
    F_ID: Optional[int] = None
    F_SYJH_ID: Optional[int] = None
    
    # 孔板读数
    F_KBDS_ZBQ: Optional[Any] = None
    F_KBDS_ZBH: Optional[Any] = None
    
    # 形态学变化（附件已转为文件ID）
    F_XTXBH: Optional[Any] = None
    
    # 细胞活率
    F_XBHL_PJ: Optional[Any] = None
    F_XBHL: Optional[Any] = None
    F_XBHL_WC: Optional[Any] = None
    
    # 光敏度
    F_GMD: Optional[Any] = None
    
    # IC50
    F_IC50: Optional[Any] = None
    
    # 实验结论
    F_JGSJ: Optional[Any] = None
    F_SYJL: Optional[Any] = None  # ✅ 修改为 Any，允许返回对象或字符串
    F_JLFJ: Optional[Any] = None
    
    # 记录信息
    F_JLR: Optional[str] = None
    F_JLRQ: Optional[str] = None
    F_CJSJ: Optional[str] = None
    F_GXSJ: Optional[str] = None
    
    # 实验计划信息
    F_SYMC: Optional[str] = Field(None, description="实验名称")
    F_SYKSRQ: Optional[str] = Field(None, description="实验开始日期")
    F_SYR: Optional[str] = Field(None, description="实验人")
    F_SYJHYMD: Optional[str] = Field(None, description="实验计划与目的")
    F_KBLX: Optional[str] = Field(None, description="孔板类型")
    
    # 组别信息（含浓度梯度）
    ZBXX: Optional[List[Any]] = Field(None, description="组别信息列表")
    
    # IC50参数（用于前端生图）
    IC50_PARAMS: Optional[List[Any]] = Field(None, description="IC50生图参数")

    class Config:
        from_attributes = True


class ExperimentRecordDetailQuery(BaseModel):
    """实验记录详情查询"""
    F_SYJH_ID: int = Field(..., description="实验计划ID")


class ExperimentRecordListQuery(BaseModel):
    """实验记录列表查询"""
    F_SYJH_ID: Optional[int] = Field(None, description="实验计划ID过滤")
    F_JLR: Optional[str] = Field(None, description="记录人过滤")
    keyword: Optional[str] = Field(None, description="结论文本模糊搜索")
    skip: int = Field(0, ge=0, description="跳过记录数")
    limit: int = Field(20, ge=1, le=100, description="返回记录数")


class GenerateIC50Request(BaseModel):
    """生成 IC50 曲线图请求"""
    F_SYJH_ID: int = Field(..., description="实验计划ID")
    F_ZBID: int = Field(..., description="组别ID")


class GenerateIC50Response(BaseModel):
    """生成 IC50 曲线图响应"""
    success: bool = Field(..., description="是否生成成功")
    message: str = Field(..., description="提示信息")
    ic50: Optional[float] = Field(None, description="IC50值（μM）")
    r2: Optional[float] = Field(None, description="拟合质量R²")
    slope: Optional[float] = Field(None, description="Hill斜率")
    image_url: Optional[str] = Field(None, description="图像URL")
    image_id: Optional[int] = Field(None, description="图像文件ID")
    group_name: Optional[str] = Field(None, description="组别名称")
    
    class Config:
        from_attributes = True


class PreviewIC50Request(BaseModel):
    """预览 IC50 曲线图请求（基于原始数据）"""
    F_ND: List[Union[int, float]] = Field(..., description="浓度数组（μM）", min_length=3)
    F_XBHL: List[Union[int, float]] = Field(..., description="细胞活率数组（%）", min_length=3)
    F_ZBMC: Optional[str] = Field(default="预览组", description="组别名称（用于图表标题和文件名）")


# ==================== 不合格标记 ====================
class MarkUnqualifiedIn(BaseModel):
    """标记实验计划为不合格"""
    F_SYJH_ID: int = Field(..., gt=0, description="实验计划ID")
    F_BHGYY: Optional[str] = Field(default=None, description="不合格原因")

