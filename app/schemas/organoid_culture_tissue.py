from pydantic import BaseModel, Field, field_validator
from typing import Any, Optional, List


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
    F_ZZBW: Optional[str] = None
    F_AZ: Optional[str] = Field(default=None, description="病种/癌种（来自标本 T_YBZJ.F_AZ，便于调用 /pipeline/list）")


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
    F_YBLY: Optional[List[str]] = Field(default=None, description="样本来源 多选精确匹配，如 [\"原代制备\", \"传代\"]")
    F_PYSJ_START: Optional[str] = Field(default=None, description="培养时间-起")
    F_PYSJ_END: Optional[str] = Field(default=None, description="培养时间-止")
    F_HSSL_MIN: Optional[float] = Field(default=None, description="细胞数量-最小值")
    F_HSSL_MAX: Optional[float] = Field(default=None, description="细胞数量-最大值")
    skip: int = Field(default=0, ge=0)
    limit: Optional[int] = Field(default=20, ge=1)
    asTree: Optional[bool] = Field(default=False, description="是否按父子聚合返回")

    @field_validator(
        "F_XM", "F_BRBH", "F_ZZID", "F_GDBH", "F_GDLX", "F_DC", "F_DCID",
        "F_GDZT", "F_ZKZT", "F_ZZBW", "F_XYZL", "F_PYSJ_START", "F_PYSJ_END",
        mode="before",
    )
    @classmethod
    def _empty_str_to_none(cls, v: Any) -> Optional[str]:
        if v is None:
            return None
        if isinstance(v, str):
            s = v.strip()
            return s if s else None
        return v

    @field_validator("F_YBLY", mode="before")
    @classmethod
    def _empty_ybly_to_none(cls, v: Any) -> Any:
        if v is None or v == "":
            return None
        if isinstance(v, str) and not v.strip():
            return None
        return v

    @field_validator("F_HSSL_MIN", "F_HSSL_MAX", mode="before")
    @classmethod
    def _empty_hssl_to_none(cls, v: Any) -> Any:
        if v is None or v == "":
            return None
        if isinstance(v, str) and not v.strip():
            return None
        return v


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
    F_BHGSJ: Optional[str] = Field(default=None, description="不合格标记时间")
    F_AZ: Optional[str] = None
    F_JZKBS: Optional[int] = None
    F_BL: Optional[dict] = None   # {"bz": str, "files": [{id, path, name}]}
    F_CX: Optional[dict] = None   # {"bz": str, "files": [{id, path, name}]}
    F_QT: Optional[dict] = None   # {"bz": str, "files": [{id, path, name}]}
    F_SFDC: Optional[int] = None  # 冻存记录数量
    F_SFFS: Optional[int] = None  # 复苏记录数量
    F_ZDYXH: Optional[str] = None
    F_SYJL: Optional[str] = None
    F_BHGLX: Optional[str] = None
    F_PIPELINE_JM: Optional[int] = None
    F_PIPELINE_LX: Optional[List[int]] = None

    @field_validator("F_PIPELINE_JM", mode="before")
    @classmethod
    def _coerce_pipeline_jm(cls, v: Any) -> Optional[int]:
        if v is None or v == "":
            return None
        if isinstance(v, str):
            s = v.strip()
            if not s:
                return None
            try:
                return int(s)
            except ValueError:
                return None
        return v

    @field_validator("F_PIPELINE_LX", mode="before")
    @classmethod
    def _coerce_pipeline_lx(cls, v: Any) -> Any:
        if v is None or v == "":
            return None
        if isinstance(v, str) and not v.strip():
            return None
        return v
    F_JHCDCS: Optional[int] = None
    F_CDQXBZS: Optional[str] = Field(default=None, description="传代前细胞总数")
    F_FSQXBZS: Optional[str] = Field(default=None, description="复苏前细胞总数")
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
    F_PIPELINE_JM: Optional[int] = Field(default=None, description="建模阶段流水线ID")
    F_PIPELINE_LX: Optional[List[int]] = Field(default=None, description="流向阶段流水线ID列表（支持多选）")
    F_CDQXBZS: Optional[str] = Field(default=None, description="传代前细胞总数")
    F_FSQXBZS: Optional[str] = Field(default=None, description="复苏前细胞总数")


class CultureOrderAttachmentDeleteIn(BaseModel):
    F_ID: int = Field(description="培养工单ID")
    F_FILE_ID: int = Field(description="中心文件ID")


class MarkCultureOrdersUnqualifiedIn(BaseModel):
    """批量标记培养工单为不合格"""
    F_GDIDS:  List[int]      = Field(min_length=1, description="工单ID数组")
    F_BHGYY:  Optional[str]  = Field(default=None, description="不合格原因")
    F_BHGLX:  Optional[str]  = Field(default=None, description="不合格类型")


class MarkCultureOrdersUnqualifiedOut(BaseModel):
    """批量标记结果"""
    updated_count: int
    updated_gdids: List[int]


class SaveCustomFieldsRequest(BaseModel):
    """保存自定义字段（序号和实验记录）"""
    F_ID: int = Field(description="工单ID")
    F_ZDYXH: Optional[str] = Field(default=None, description="自定义序号（可选；传空字符串 '' 或 '-1' 清除序号值）")
    F_SYJL: Optional[str] = Field(default=None, description="实验记录内容（可选）")


class UpdateOrderStatusRequest(BaseModel):
    """更新培养工单状态"""
    F_ID: int = Field(description="工单ID")
    F_GDZT: str = Field(description="新状态（用户自由输入）")


class UpdateOrderStatusResponse(BaseModel):
    """更新工单状态响应"""
    F_ID: int
    F_GDZT: str
    old_status: str
    qc_audit_id: Optional[int] = None
    message: Optional[str] = None


class UploadAttachmentResponse(BaseModel):
    """附件上传响应。
    F_BL / F_CX / F_QT 格式：{"bz": "共用备注", "files": [{id, path, name}, ...]}
    """
    F_ID: int
    F_BL: Optional[dict] = None
    F_CX: Optional[dict] = None
    F_QT: Optional[dict] = None
    added_count: int
    deleted_count: int
    qc_audit_id: Optional[int] = None

    class Config:
        extra = "allow"


class CultureOrderExportQuery(CultureOrderListQuery):
    """培养工单导出入参，在列表查询参数基础上额外支持勾选导出"""
    F_GDIDS: Optional[List[int]] = Field(
        default=None,
        description="需要导出的工单ID数组；传入时仅导出这些工单，不传则按其他过滤条件全量导出"
    )


class UpdateJHCDCSRequest(BaseModel):
    """更新计划传代次数入参"""
    F_ID: int = Field(description="培养工单ID")
    F_JHCDCS: Optional[int] = Field(default=None, description="计划传代次数，传 null 表示清空")


class UpdateJHCDCSResponse(BaseModel):
    """更新计划传代次数出参"""
    F_ID: int
    F_JHCDCS: Optional[int] = None
