"""
药物管理模块 Schemas
"""
from typing import Optional, List, Dict, Any
from pydantic import BaseModel, Field
from datetime import datetime


# ==================== 药物验证 ====================

class DrugCheckIn(BaseModel):
    """验证药物是否存在入参"""
    F_YWMC: str = Field(description="药物名称")


class DrugCheckOut(BaseModel):
    """验证药物是否存在出参"""
    exists: bool = Field(description="是否存在")
    message: str = Field(description="提示消息")
    drug_id: Optional[int] = Field(default=None, description="药物ID（如果存在）")
    drug_code: Optional[str] = Field(default=None, description="药物编号（如果存在）")


# ==================== 药物主表 ====================

class DrugCreateIn(BaseModel):
    """创建药物入参"""
    F_YWLX: str = Field(description="药物类型（必填）")
    F_YWMC: str = Field(description="药物名称（必填）")
    F_YWSPM: Optional[str] = Field(default=None, description="药物商品名")
    F_CAS: Optional[str] = Field(default=None, description="CAS号")
    F_YWDM: Optional[str] = Field(default=None, description="药物代名")
    F_HH: Optional[str] = Field(default=None, description="货号")
    F_GSMC: Optional[str] = Field(default=None, description="公司名称（必填）")
    F_YWPZQK: Optional[str] = Field(default=None, description="药物批准情况")
    F_YWFZL: Optional[str] = Field(default=None, description="药物分子量")
    F_YWSYZ: Optional[str] = Field(default=None, description="药物适用症")
    F_ZYBD: Optional[str] = Field(default=None, description="作用靶点")
    F_YWCCND: Optional[str] = Field(default=None, description="药物存储浓度")
    F_ZYTL: Optional[str] = Field(default=None, description="作用通路")
    F_CCLX: Optional[str] = Field(default=None, description="存储类型")
    F_CCBX: Optional[str] = Field(default=None, description="存储冰箱")
    F_CD: Optional[str] = Field(default=None, description="纯度")
    F_YJKC: Optional[float] = Field(default=None, description="预警库存")
    F_CCHZ: Optional[str] = Field(default=None, description="存储盒子")
    F_SJPF: Optional[List[int]] = Field(default=None, description="试剂配方（原药ID数组，仅当F_YWLX为'试剂'时需要）")
    F_DJR: Optional[str] = Field(default=None, description="登记人")
    F_DJRQ: Optional[datetime] = Field(default=None, description="登记日期")
    F_BZ: Optional[str] = Field(default=None, description="备注")


class DrugCreateOut(BaseModel):
    """创建药物出参"""
    F_ID: int
    F_YWBH: str
    F_YWMC: str


class DrugUpdateIn(BaseModel):
    """更新药物入参"""
    F_ID: int = Field(description="药物ID")
    F_YWLX: Optional[str] = Field(default=None, description="药物类型")
    F_YWMC: Optional[str] = Field(default=None, description="药物名称")
    F_YWSPM: Optional[str] = Field(default=None, description="药物商品名")
    F_CAS: Optional[str] = Field(default=None, description="CAS号")
    F_YWDM: Optional[str] = Field(default=None, description="药物代名")
    F_HH: Optional[str] = Field(default=None, description="货号")
    F_GSMC: Optional[str] = Field(default=None, description="公司名称")
    F_YWPZQK: Optional[str] = Field(default=None, description="药物批准情况")
    F_YWFZL: Optional[str] = Field(default=None, description="药物分子量")
    F_YWSYZ: Optional[str] = Field(default=None, description="药物适用症")
    F_ZYBD: Optional[str] = Field(default=None, description="作用靶点")
    F_YWCCND: Optional[str] = Field(default=None, description="药物存储浓度")
    F_ZYTL: Optional[str] = Field(default=None, description="作用通路")
    F_CCLX: Optional[str] = Field(default=None, description="存储类型")
    F_CCBX: Optional[str] = Field(default=None, description="存储冰箱")
    F_CD: Optional[str] = Field(default=None, description="纯度")
    F_YJKC: Optional[float] = Field(default=None, description="预警库存")
    F_CCHZ: Optional[str] = Field(default=None, description="存储盒子")
    F_SJPF: Optional[List[int]] = Field(default=None, description="试剂配方（原药ID数组，会覆盖原有配方）")
    F_SJPF_DEL: Optional[List[int]] = Field(default=None, description="要删除的配方成分（原药ID数组，从现有配方中删除）")
    F_BZ: Optional[str] = Field(default=None, description="备注")


class DrugUpdateOut(BaseModel):
    """更新药物出参"""
    F_ID: int
    F_YWMC: str


class DrugDetailIn(BaseModel):
    """药物详情入参"""
    F_ID: int = Field(description="药物ID")


class DrugDetailOut(BaseModel):
    """药物详情出参"""
    F_ID: int
    F_YWBH: Optional[str] = None
    F_YWLX: Optional[str] = None
    F_YWMC: Optional[str] = None
    F_YWSPM: Optional[str] = None
    F_CAS: Optional[str] = None
    F_YWDM: Optional[str] = None
    F_HH: Optional[str] = None
    F_GSMC: Optional[str] = None
    F_YWPZQK: Optional[str] = None
    F_YWFZL: Optional[str] = None
    F_YWSYZ: Optional[str] = None
    F_ZYBD: Optional[str] = None
    F_YWCCND: Optional[str] = None
    F_ZYTL: Optional[str] = None
    F_CCLX: Optional[str] = None
    F_CCBX: Optional[str] = None
    F_CD: Optional[str] = None
    F_YJKC: Optional[float] = None
    F_CCHZ: Optional[str] = None
    F_SJPF: Optional[List[int]] = None
    F_SJPF_DETAILS: Optional[List[Dict[str, Any]]] = Field(default=None, description="试剂配方详情（包含原药ID、编号、名称）")
    F_DJR: Optional[str] = None
    F_DJRQ: Optional[str] = None
    F_BZ: Optional[str] = None


class DrugListQuery(BaseModel):
    """药物列表查询入参"""
    F_YWBH: Optional[str] = Field(default=None, description="药物编号（模糊匹配）")
    F_YWLX: Optional[str] = Field(default=None, description="药物类型（模糊匹配）")
    F_YWMC: Optional[str] = Field(default=None, description="药物名称（模糊匹配）")
    F_GSMC: Optional[str] = Field(default=None, description="公司名称（模糊匹配）")
    skip: int = Field(default=0, ge=0, description="跳过记录数")
    limit: int = Field(default=20, ge=1, le=100, description="返回记录数")


class DrugListItem(BaseModel):
    """药物列表项"""
    F_ID: int
    F_YWBH: Optional[str] = None
    F_YWLX: Optional[str] = None
    F_YWMC: Optional[str] = None
    F_YWSPM: Optional[str] = None
    F_CAS: Optional[str] = None
    F_SJPF: Optional[List[int]] = None
    F_YWDM: Optional[str] = None
    F_HH: Optional[str] = None
    F_GSMC: Optional[str] = None
    F_YWPZQK: Optional[str] = None
    F_YWFZL: Optional[str] = None
    F_YWSYZ: Optional[str] = None
    F_ZYBD: Optional[str] = None
    F_YWCCND: Optional[str] = None
    F_ZYTL: Optional[str] = None
    F_CCLX: Optional[str] = None
    F_CCBX: Optional[str] = None
    F_CD: Optional[str] = None
    F_YJKC: Optional[float] = None
    F_CCHZ: Optional[str] = None
    F_DJR: Optional[str] = None
    F_DJRQ: Optional[str] = None
    F_BZ: Optional[str] = None
    # 库存统计字段
    F_ZKSL: Optional[float] = None  # 在库数量
    F_RKJL: Optional[int] = None  # 入库记录数
    F_CKJL: Optional[int] = None  # 出库记录数


class DrugListOut(BaseModel):
    """药物列表出参"""
    total: int
    data: list[DrugListItem]


class DrugSimpleItem(BaseModel):
    """药物简化信息（用于下拉选择，仅原药）"""
    F_ID: int = Field(description="药物ID")
    F_YWBH: str = Field(description="药物编号")
    F_YWMC: str = Field(description="药物名称")


class DrugSimpleListOut(BaseModel):
    """药物简化列表出参"""
    total: int = Field(description="总数")
    data: List[DrugSimpleItem] = Field(description="药物简化列表")


class ReagentListQuery(BaseModel):
    """试剂列表查询入参（用于药物实验）"""
    F_YWBH: Optional[str] = Field(default=None, description="药物编号（模糊匹配）")
    F_YWMC: Optional[str] = Field(default=None, description="药物名称（模糊匹配）")
    skip: int = Field(default=0, ge=0, description="跳过记录数")
    limit: int = Field(default=20, ge=1, le=100, description="返回记录数")


class ReagentInboundBatch(BaseModel):
    """试剂入库批次信息"""
    F_RKDH: str = Field(description="入库单号")
    F_PCH: Optional[str] = Field(None, description="批次号")
    F_YXQ: Optional[str] = Field(None, description="有效期")
    F_ZKSL: float = Field(description="该批次在库数量")
    F_RKSL: float = Field(description="入库数量")
    F_CKSL: float = Field(description="已出库数量")


class ReagentListItem(BaseModel):
    """试剂列表项（用于药物实验，含批次信息）"""
    F_ID: int = Field(description="药物ID")
    F_YWBH: Optional[str] = Field(None, description="药物编号")
    F_YWMC: Optional[str] = Field(None, description="药物名称")
    F_YWSPM: Optional[str] = Field(None, description="药物俗品名")
    F_CAS: Optional[str] = Field(None, description="CAS号")
    F_GSMC: Optional[str] = Field(None, description="公司名称")
    F_ZKSL: float = Field(description="总在库数量")
    batches: List[ReagentInboundBatch] = Field(description="入库批次列表（仅包含有库存的批次）")


class ReagentListOut(BaseModel):
    """试剂列表出参"""
    total: int = Field(description="总数")
    data: List[ReagentListItem] = Field(description="试剂列表")


class DrugDeleteIn(BaseModel):
    """删除药物入参"""
    F_ID: int = Field(description="药物ID")


class DrugDeleteOut(BaseModel):
    """删除药物出参"""
    F_ID: int


# ==================== 入库单 ====================

class DrugUsageItem(BaseModel):
    """原药使用信息（试剂入库时使用）"""
    F_YWID: int = Field(description="原药ID")
    F_RKDH: str = Field(description="原药入库单号")
    F_SYSL: float = Field(gt=0, description="使用数量（必须大于0）")


class InboundCreateIn(BaseModel):
    """创建入库单入参"""
    F_YWID: int = Field(description="药物ID")
    F_RKSL: float = Field(gt=0, description="入库数量（必须大于0）")
    F_PCH: Optional[str] = Field(default=None, description="批次号")
    F_YXQ: Optional[datetime] = Field(default=None, description="有效期")
    F_RKCZR: Optional[str] = Field(default=None, description="入库操作人")
    F_RKRQ: Optional[datetime] = Field(default=None, description="入库日期")
    F_BZ: Optional[str] = Field(default=None, description="备注")
    F_YYSY: Optional[List[DrugUsageItem]] = Field(default=None, description="原药使用信息（仅试剂入库时需要，数组包含使用的原药ID、入库单号和使用数量）")


class InboundCreateOut(BaseModel):
    """创建入库单出参"""
    F_ID: int
    F_RKDH: str
    F_YWID: int
    F_RKSL: float
    F_PCH: Optional[str] = None
    F_YXQ: Optional[str] = None
    F_RKCZR: Optional[str] = None
    F_RKRQ: Optional[str] = None
    F_BZ: Optional[str] = None
    F_CJSJ: Optional[str] = None
    # 试剂入库时自动创建的原药出库单信息
    auto_outbound_count: Optional[int] = Field(default=None, description="自动创建的原药出库单数量")
    auto_outbound_codes: Optional[List[str]] = Field(default=None, description="自动创建的原药出库单号列表")


class InboundListQuery(BaseModel):
    """入库单列表查询入参"""
    F_YWID: Optional[int] = Field(default=None, description="药物ID")
    F_YWBH: Optional[List[str]] = Field(default=None, description="药物编号数组（多选过滤）")
    F_PCH: Optional[str] = Field(default=None, description="批次号（模糊匹配）")
    skip: int = Field(default=0, ge=0, description="跳过记录数")
    limit: int = Field(default=20, ge=1, le=100, description="返回记录数")


class InboundListItem(BaseModel):
    """入库单列表项"""
    F_ID: int
    F_RKDH: str
    F_YWID: int
    F_RKSL: float
    F_PCH: Optional[str] = None
    F_YXQ: Optional[str] = None
    F_RKCZR: Optional[str] = None
    F_RKRQ: Optional[str] = None
    F_BZ: Optional[str] = None
    F_CJSJ: Optional[str] = None
    # 药物信息
    F_YWBH: Optional[str] = None
    F_YWMC: Optional[str] = None
    F_YWLX: Optional[str] = None
    F_GSMC: Optional[str] = None
    # 剩余在库数量
    F_ZKSL: Optional[float] = Field(default=None, description="剩余在库数量（本入库单的入库数量 - 基于本入库单的出库数量）")


class InboundListOut(BaseModel):
    """入库单列表出参"""
    total: int
    data: list[InboundListItem]


class InboundOptionsQuery(BaseModel):
    """入库单选项查询入参（用于试剂配制时选择原药批次）"""
    F_YWID: int = Field(description="药物ID")


class InboundOptionItem(BaseModel):
    """入库单选项项（用于下拉选择）"""
    F_RKDH: str = Field(description="入库单号")
    F_PCH: Optional[str] = Field(default=None, description="批次号")
    F_YXQ: Optional[str] = Field(default=None, description="有效期")
    F_ZKSL: float = Field(description="剩余在库数量")


class InboundOptionsOut(BaseModel):
    """入库单选项列表出参"""
    data: List[InboundOptionItem] = Field(description="入库单选项列表")


class InboundListForOutboundQuery(BaseModel):
    """出库用入库单列表查询入参"""
    F_YWBH: Optional[str] = Field(default=None, description="药物编号（模糊匹配）")
    F_YWMC: Optional[str] = Field(default=None, description="药物名称（模糊匹配）")
    F_YWLX: Optional[str] = Field(default=None, description="药物类型（精确匹配）")
    F_RKDH: Optional[str] = Field(default=None, description="入库单号（模糊匹配）")
    skip: int = Field(default=0, ge=0, description="跳过记录数")
    limit: int = Field(default=20, ge=1, le=100, description="返回记录数")


class InboundForOutboundItem(BaseModel):
    """出库用入库单列表项"""
    F_ID: int = Field(description="入库单ID")
    F_RKDH: str = Field(description="入库单号")
    F_YWID: int = Field(description="药物ID")
    F_YWBH: Optional[str] = Field(default=None, description="药物编号")
    F_YWMC: Optional[str] = Field(default=None, description="药物名称")
    F_YWLX: Optional[str] = Field(default=None, description="药物类型")
    F_GSMC: Optional[str] = Field(default=None, description="供应商名称")
    F_RKSL: float = Field(description="入库数量")
    F_ZKSL: float = Field(description="剩余在库数量")
    F_PCH: Optional[str] = Field(default=None, description="批次号")
    F_YXQ: Optional[str] = Field(default=None, description="有效期")
    F_RKRQ: Optional[str] = Field(default=None, description="入库日期")
    F_RKCZR: Optional[str] = Field(default=None, description="入库操作人")


class InboundListForOutboundOut(BaseModel):
    """出库用入库单列表出参"""
    total: int = Field(description="总记录数")
    data: List[InboundForOutboundItem] = Field(description="入库单列表")


# ==================== 出库单 ====================

class OutboundCreateIn(BaseModel):
    """创建出库单入参"""
    F_RKDH: str = Field(description="入库单号")
    F_CKSL: float = Field(gt=0, description="出库数量（必须大于0）")
    F_CKRQ: Optional[datetime] = Field(default=None, description="出库日期")
    F_CKR: Optional[str] = Field(default=None, description="出库人")
    F_BZ: Optional[str] = Field(default=None, description="备注")


class OutboundCreateOut(BaseModel):
    """创建出库单出参"""
    F_ID: int
    F_CKDH: str
    F_RKDH: str
    F_YWID: int
    F_CKSL: float
    F_PCH: Optional[str] = None
    F_YXQ: Optional[str] = None
    F_CKRQ: Optional[str] = None
    F_CKR: Optional[str] = None
    F_BZ: Optional[str] = None
    F_CJSJ: Optional[str] = None


class OutboundListQuery(BaseModel):
    """出库单列表查询入参"""
    F_YWID: Optional[int] = Field(default=None, description="药物ID")
    F_YWBH: Optional[str] = Field(default=None, description="药物编号（模糊匹配）")
    F_YWMC: Optional[str] = Field(default=None, description="药物名称（模糊匹配）")
    F_YWLX: Optional[str] = Field(default=None, description="药物类型（精确匹配）")
    F_CKDH: Optional[str] = Field(default=None, description="出库单号（模糊匹配）")
    F_RKDH: Optional[str] = Field(default=None, description="入库单号（模糊匹配）")
    F_PCH: Optional[str] = Field(default=None, description="批次号（模糊匹配）")
    skip: int = Field(default=0, ge=0, description="跳过记录数")
    limit: int = Field(default=20, ge=1, le=100, description="返回记录数")


class OutboundListItem(BaseModel):
    """出库单列表项"""
    F_ID: int
    F_CKDH: str
    F_RKDH: str
    F_YWID: int
    F_CKSL: float
    F_PCH: Optional[str] = None
    F_YXQ: Optional[str] = None
    F_CKRQ: Optional[str] = None
    F_CKR: Optional[str] = None
    F_BZ: Optional[str] = None
    F_CJSJ: Optional[str] = None
    # 药物信息
    F_YWBH: Optional[str] = None
    F_YWMC: Optional[str] = None
    F_YWLX: Optional[str] = None
    F_GSMC: Optional[str] = None


class OutboundListOut(BaseModel):
    """出库单列表出参"""
    total: int
    data: list[OutboundListItem]


# ==================== 批量出库 ====================

class BatchOutboundItem(BaseModel):
    """批量出库单项"""
    F_RKDH: str = Field(description="入库单号（关联入库单）")
    F_CKSL: float = Field(gt=0, description="出库数量（必须大于0）")
    F_BZ: Optional[str] = Field(default=None, description="备注")


class BatchOutboundSuccessItem(BaseModel):
    """批量出库成功项"""
    F_ID: int
    F_CKDH: str
    F_RKDH: str
    F_YWID: int
    F_YWMC: Optional[str] = None
    F_CKSL: float
    F_BZ: Optional[str] = None


class BatchOutboundFailedItem(BaseModel):
    """批量出库失败项"""
    F_RKDH: str
    F_CKSL: float
    error: str


class BatchOutboundCreateOut(BaseModel):
    """批量创建出库单响应"""
    success_count: int = Field(description="成功创建数量")
    failed_count: int = Field(description="失败数量")
    success_items: list[BatchOutboundSuccessItem] = Field(default_factory=list, description="成功的出库单")
    failed_items: list[BatchOutboundFailedItem] = Field(default_factory=list, description="失败的出库单")

