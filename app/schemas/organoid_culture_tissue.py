from pydantic import BaseModel, Field
from typing import Optional, List


class PendingOrderListQuery(BaseModel):
    F_XM: Optional[str] = Field(default=None, description="姓名 模糊")
    F_BRBH: Optional[str] = Field(default=None, description="病人编号 模糊")
    F_ZZID: Optional[str] = Field(default=None, description="标本号 模糊")
    F_CJBW: Optional[str] = Field(default=None, description="采集部位 模糊")
    F_GDLX: Optional[str] = Field(default=None, description="工单类型 模糊")
    F_DJRQ_START: Optional[str] = Field(default=None, description="登记日期-起 YYYY-MM-DD")
    F_DJRQ_END: Optional[str] = Field(default=None, description="登记日期-止 YYYY-MM-DD")
    F_CXSJ_START: Optional[str] = Field(default=None, description="取样日期-起 YYYY-MM-DD 或 YYYY-MM-DD HH:MM:SS")
    F_CXSJ_END: Optional[str] = Field(default=None, description="取样日期-止 YYYY-MM-DD 或 YYYY-MM-DD HH:MM:SS")
    F_GDZT: Optional[str] = Field(default="待创建", description="工单状态，默认待创建")
    skip: int = Field(default=0, ge=0, description="跳过条数")
    limit: Optional[int] = Field(default=20, ge=1, description="返回条数")


class PendingOrderItem(BaseModel):
    # FFJL
    F_ID: int
    F_ZZID: Optional[str] = None
    F_GDZT: Optional[str] = None
    F_GDLX: Optional[str] = None
    F_FFSJ: Optional[str] = None
    F_PYRY: Optional[int] = None
    F_PYRY_NAME: Optional[str] = None
    # Z/患者与样本
    F_XM: Optional[str] = None
    F_XB: Optional[str] = None
    F_BRBH: Optional[str] = None
    F_BBLX: Optional[str] = None
    F_ZQTYS: Optional[str] = None
    F_QYFS: Optional[str] = None
    F_DJRQ: Optional[str] = None
    F_CXSJ: Optional[str] = None
    F_CJBW: Optional[str] = None


class PendingOrderListOut(BaseModel):
    items: List[PendingOrderItem]
    total: int


# 培养工单列表/详情/更新
class CultureOrderListQuery(BaseModel):
    F_XM: Optional[str] = Field(default=None, description="姓名 模糊")
    F_BRBH: Optional[str] = Field(default=None, description="病人编号 模糊")
    F_ZZID: Optional[str] = Field(default=None, description="标本号 模糊")
    F_GDBH: Optional[str] = Field(default=None, description="工单编号 模糊")
    F_GDLX: Optional[str] = Field(default=None, description="工单类型 模糊")
    F_DC: Optional[str] = Field(default=None, description="代次 精确或模糊")
    F_DCID: Optional[str] = Field(default=None, description="代次ID 模糊")
    F_GDZT: Optional[str] = Field(default=None, description="工单状态（当前固定为 已创建）")
    F_ZKZT: Optional[str] = Field(default=None, description="质控状态（精确匹配，如：审核通过）")
    F_ZZBW: Optional[str] = Field(default=None, description="组织部位 模糊")
    F_XYZL: Optional[str] = Field(default=None, description="血液种类 模糊")
    F_PYSJ_START: Optional[str] = Field(default=None, description="培养时间-起")
    F_PYSJ_END: Optional[str] = Field(default=None, description="培养时间-止")
    F_HSSL_MIN: Optional[float] = Field(default=None, description="细胞数量-最小值")
    F_HSSL_MAX: Optional[float] = Field(default=None, description="细胞数量-最大值")
    skip: int = Field(default=0, ge=0)
    limit: Optional[int] = Field(default=20, ge=1)
    asTree: Optional[bool] = Field(default=False, description="是否按父子聚合返回")


class CultureOrderItem(BaseModel):
    F_ID: int
    F_GDBH: Optional[str] = None
    F_ZZID: Optional[str] = None
    F_XM: Optional[str] = None
    F_BRBH: Optional[str] = None
    F_BBLX: Optional[str] = None
    F_DC: Optional[str] = None
    F_DCID: Optional[str] = None
    F_ZZBW: Optional[str] = None
    F_XYZL: Optional[str] = None
    F_QYFS: Optional[str] = None
    F_YBLY: Optional[str] = None
    F_YBGG: Optional[str] = None
    F_GDLX: Optional[str] = None
    F_GDZT: Optional[str] = None
    F_SHZT: Optional[str] = None
    F_DCJL: Optional[int] = None
    F_YCL: Optional[bool] = None
    F_SQR: Optional[str] = None
    F_PYSJ: Optional[str] = None
    F_FJ: Optional[List[dict]] = None
    F_CJSJ: Optional[str] = None
    F_HSSJ: Optional[str] = None
    F_HSSL: Optional[float] = None
    F_PYTS: Optional[int] = None
    children: Optional[List[dict]] = None


class CultureOrderListOut(BaseModel):
    items: List[CultureOrderItem]
    total: int


class CultureOrderDetailQuery(BaseModel):
    F_ID: int


class CultureOrderUpdateIn(BaseModel):
    F_ID: int = Field(description="培养工单ID")
    F_ZZBW: Optional[str] = None
    F_XYZL: Optional[str] = None
    F_YBLY: Optional[str] = None
    F_YBGG: Optional[str] = None
    F_PYSJ: Optional[str] = None


class CultureOrderAttachmentDeleteIn(BaseModel):
    F_ID: int = Field(description="培养工单ID")
    F_FILE_ID: int = Field(description="中心文件ID")


class MarkCultureOrdersUnqualifiedIn(BaseModel):
    """批量标记培养工单为不合格"""
    F_GDIDS: List[int] = Field(min_length=1, description="工单ID数组")
    F_BHGYY: Optional[str] = Field(default=None, description="不合格原因")


class MarkCultureOrdersUnqualifiedOut(BaseModel):
    """批量标记结果"""
    updated_count: int
    updated_gdids: List[int]
