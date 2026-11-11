from pydantic import BaseModel, Field, field_validator
from typing import Optional, List, Union, Any
from app.utils.field_coerce import parse_str_list


class SampleReceiveQuery(BaseModel):
    F_ZZID: Optional[str] = Field(default=None, description="标本号")
    F_ZYH: Optional[str] = Field(default=None, description="住院号")
    F_SJTMH: Optional[str] = Field(default=None, description="送检条码号")


class FileInfo(BaseModel):
    id: int
    path: Optional[str] = None
    name: Optional[str] = None


#就诊/患者信息
class PatientInfo(BaseModel):
    F_XM: Optional[str] = None
    F_BRBH: Optional[str] = None
    F_ZYH: Optional[str] = None
    F_XB: Optional[str] = None
    F_NL: Optional[str] = None
    F_DH: Optional[str] = None
    F_SFZH: Optional[str] = None
    F_DZ: Optional[str] = None
    F_BBLY: Optional[str] = None
    F_LCZD: Optional[str] = None
    F_ZQTYS: Optional[str] = None
    F_ZQTYSLB: Optional[str] = None
    F_ZQTYS_FILES: Optional[List[FileInfo]] = Field(default_factory=list, description="知情同意书文件数组")
    F_MZH: Optional[str] = None
    F_CHH: Optional[str] = None
    F_BZTZ: Optional[List[str]] = Field(default=None, description="病灶特征（字符串数组）")
    F_SJTMH: Optional[str] = None  # 送检条码号
    F_CSNY: Optional[str] = None  # 出生年月
    F_XMMC: Optional[str] = None  # 项目名称（快照，来自项目信息维护，不做存在性校验）
    F_XMBH: Optional[str] = None  # 项目编号（快照，同上；沿链路传递到运输单查询）
    F_DZBL: Optional[str] = Field(default=None, description="电子病历调阅地址（按 emr_config 拼出；未启用或缺少对应号时为 null）")

    @field_validator("F_BZTZ", mode="before")
    @classmethod
    def _coerce_bztz(cls, v: Any) -> Optional[List[str]]:
        return parse_str_list(v)

#标本信息
class SpecimenInfo(BaseModel):
    F_ZZID: Optional[str] = None
    F_BBLX: Optional[str] = None
    F_SSMC: Optional[str] = None
    F_SSRQ: Optional[str] = None
    F_SSS: Optional[str] = None
    F_SSYS: Optional[str] = None
    F_CXSJ: Optional[str] = None
    F_LTSJ: Optional[str] = None
    F_JSSJ: Optional[str] = None
    F_CRB: Optional[str] = None
    F_CJBW: Optional[str] = None
    F_QYFS: Optional[str] = None
    F_BZTZ: Optional[List[str]] = Field(default=None, description="病灶特征（字符串数组，可与 JZXX 二选一传入）")

    @field_validator("F_BZTZ", mode="before")
    @classmethod
    def _coerce_bztz(cls, v: Any) -> Optional[List[str]]:
        return parse_str_list(v)

#质检信息
class QualityInfo(BaseModel):
    F_SYTP_FILES: Optional[List[FileInfo]] = Field(default_factory=list, description="收样图片文件数组")
    F_SYMS: Optional[str] = None
    F_YSWD: Optional[float] = None
    F_YSSJ: Optional[float] = None
    F_SFDSF: Optional[str] = None
    F_AZ: Optional[str] = None
    F_ABL: Optional[str] = None
    F_KI67: Optional[str] = None
    F_SFWH: Optional[str] = None
    F_SFJY: Optional[str] = None
    F_SFCQ: Optional[str] = None
    F_SFQB: Optional[str] = None
    F_SFLY: Optional[str] = None
    F_PBMCDC: Optional[str] = None
    F_QXDC: Optional[str] = None
    F_DJR: Optional[str] = None
    F_DJRQ: Optional[str] = None
    F_BHG: Optional[str] = None
    F_BHGYY: Optional[str] = None
    F_SJJG: Optional[str] = None
    F_QYWC: Optional[int] = None

#样本接收返回信息
class DistributeSummary(BaseModel):
    F_ID: int
    F_ZZID: str
    F_PYRY: Optional[str | int] = None  # 可以是用户ID(int)或用户名(str)
    F_MXLX: Optional[str] = None
    F_GDLX: Optional[str] = None
    F_FFSJ: Optional[str] = None
    F_FFCJR: Optional[str] = None
    F_GDZT: Optional[str] = None
    F_QYWC: Optional[int] = None


class SampleReceiveOut(BaseModel):
    JZXX: PatientInfo
    BBXX: SpecimenInfo
    YBZJ: Optional[QualityInfo] = None
    FFJL: List[DistributeSummary] = Field(default_factory=list)



class MarkUnqualifiedIn(BaseModel):
    F_ZZID: str = Field(min_length=1, description="标本号")
    F_BHGYY: Optional[str] = Field(default=None, description="不合格原因")


class SampleReceiveUpdateIn(BaseModel):
    F_ZZID: str = Field(min_length=1, description="标本号")
    JZXX: Optional[PatientInfo] = None
    BBXX: Optional[SpecimenInfo] = None
    YBZJ: Optional[QualityInfo] = None


# 分发记录 - 新增
class DistributeCreate(BaseModel):
    F_ZZID: str = Field(min_length=1, description="标本号")
    F_PYRY: int = Field(description="培养人员用户ID")
    F_MXLX: str = Field(min_length=1, description="模型类型")
    F_GDLX: Optional[str] = Field(default=None, description="工单类型(可选)")
    F_QYWC: Optional[int] = Field(default=0, description="取用是否完成：1=是（后续不再分发）；0=否（可继续分发）")


class DistributeOut(BaseModel):
    F_ID: int
    F_ZZID: str
    F_PYRY: int
    F_MXLX: str
    F_GDLX: Optional[str] = None
    F_GDZT: Optional[str] = None
    F_FFSJ: str
    F_FFCJR: str
    F_QYWC: Optional[int] = Field(None, description="取用是否完成：1=是；0=否")
    qc_audit_id: Optional[int] = Field(None, description="质控审核记录ID")


class DistributeListQuery(BaseModel):
    F_ZZID: str = Field(min_length=1, description="标本号")
    F_PYRY: Optional[int] = Field(default=None, description="培养人员用户ID，用于过滤")
    F_MXLX: Optional[str] = Field(default=None, description="模型类型，模糊匹配")
    skip: int = Field(default=0, description="跳过条数")
    limit: Optional[int] = Field(default=20, description="返回条数")


class DistributeItemOut(DistributeOut):
    F_PYRY_NAME: Optional[str] = None


class DistributeListOut(BaseModel):
    items: List[DistributeItemOut]
    total: int
    F_QYWC: Optional[int] = None


class DistributeListResponse(BaseModel):
    """分发记录列表接口专用响应，F_QYWC 与 code/msg/data 同级"""
    code: int = 1
    msg: str = ""
    F_QYWC: Optional[int] = None
    data: Optional[List[DistributeItemOut]] = None
class YBZJListQuery(BaseModel):
    F_ZZID: Optional[str] = None
    F_XM: Optional[str] = None
    F_BRBH: Optional[str] = None
    F_ZYH: Optional[str] = None
    F_BBLX: Optional[str] = None
    F_BHG: Optional[Union[str, List[str]]] = Field(
        default=None,
        description="合格状态过滤，支持单选/多选：'合格'/'不合格'/'不合格审批中'；数组为 OR 关系；'全部'或不传→不过滤",
    )
    F_SFPY: Optional[str] = None
    F_ZZPY: Optional[str] = None  # 新增：组织培养状态过滤
    F_XYPY: Optional[str] = None  # 新增：血液培养状态过滤
    F_DJRQ_START: Optional[str] = None
    F_DJRQ_END: Optional[str] = None
    F_CXSJ_START: Optional[str] = None
    F_CXSJ_END: Optional[str] = None
    skip: int = 0
    limit: Optional[int] = 20

    @field_validator("F_BHG", mode="before")
    @classmethod
    def _normalize_bhg_filter(cls, v):
        if v is None:
            return None
        if isinstance(v, str):
            s = v.strip()
            return None if not s or s == "全部" else [s]
        if isinstance(v, list):
            items = [
                str(x).strip()
                for x in v
                if x is not None and str(x).strip() and str(x).strip() != "全部"
            ]
            # 去重并保持顺序
            return list(dict.fromkeys(items)) or None
        return None



class DistributeUpdate(BaseModel):
    F_ID: int = Field(description="分发记录ID")
    F_PYRY: Optional[int] = Field(default=None, description="培养人员用户ID(可选)")
    F_MXLX: Optional[str] = Field(default=None, description="模型类型(可选)")
    F_GDLX: Optional[str] = Field(default=None, description="工单类型(可选)")
    F_QYWC: Optional[int] = Field(default=None, description="取用是否完成：1=是；0=否（可选）")


class DistributeDelete(BaseModel):
    F_ID: int = Field(description="分发记录ID")


# 合并保存+分发 入参（distributes 为数组项）
class DistributeBatchItem(BaseModel):
    F_PYRY: int = Field(description="培养人员用户ID")
    F_MXLX: str = Field(min_length=1, description="模型类型")
    F_GDLX: Optional[str] = Field(default=None, description="工单类型(可选)")
    F_QYWC: Optional[int] = Field(default=0, description="取用是否完成：1=是（后续不再分发）；0=否（可继续分发）")


class SaveAndDistributeOut(BaseModel):
    F_ZZID: str
    F_ID: Optional[int] = Field(default=None, description="T_YBZJ 主键（顶层 Form 入参/响应）；新建成功后返回，编辑时回传")
    F_ZZID_SOURCE: Optional[str] = Field(
        default=None,
        description="标本号来源：auto=系统自动分配；custom=用户自定义；existing=编辑已有标本",
    )
    ybzj_saved: bool
    distribute_ids: List[int]
    qc_audit_ids: Optional[List[int]] = Field(default_factory=list, description="质控审核记录ID列表")
    pdf_id: Optional[int] = Field(default=None, description="生成的PDF文件ID")
    F_FILE_IDS: Optional[List[int]] = Field(default_factory=list, description="新上传的收样图片文件ID数组")
    F_ZQTYS_IDS: Optional[List[int]] = Field(default_factory=list, description="新上传的知情同意书ID数组")
    deleted_sytp_ids: Optional[List[int]] = Field(default_factory=list, description="已删除的收样图片ID数组")
    deleted_zqtys_ids: Optional[List[int]] = Field(default_factory=list, description="已删除的知情同意书ID数组")

class YBZJListItem(BaseModel):
    F_ID: int = Field(description="T_YBZJ 主键，编辑保存时作为顶层 Form 字段 F_ID 回传")
    F_ZZID: str
    F_BBLX: Optional[str] = None
    F_BHG: Optional[str] = None
    F_ZQTYS: Optional[str] = None
    F_SJJG: Optional[str] = None
    F_XM: Optional[str] = None
    F_XB: Optional[str] = None
    F_BRBH: Optional[str] = None
    F_ZYH: Optional[str] = None
    F_DJRQ: Optional[str] = None
    F_CXSJ: Optional[str] = None
    F_ZQTYS_ID: Optional[str] = None
    F_ZQTYS_DZ: Optional[str] = None
    F_ZZPY: Optional[str] = None
    F_XYPY: Optional[str] = None
    F_LK: int = 0
    F_OTC: int = 0
    F_XQ: int = 0
    F_XJ: int = 0
    F_PBMC: int = 0
    F_PDO: int = 0
    F_ZKPDO: int = 0
    F_CTC: int = 0
    F_BLSJ: Optional[str] = None
    F_ZXSJ: Optional[str] = None
    F_DZBL: Optional[str] = Field(default=None, description="电子病历调阅地址（按 emr_config 拼出；未启用或缺少对应号时为 null）")


class PdoDetailQuery(BaseModel):
    """标本接收列表点击某样本类型数量查看详情入参"""
    F_ID: int = Field(..., description="T_YBZJ 主键（标本接收列表行 F_ID）")
    F_YBLX: str = Field(..., description="样本类型。可传列表列名（F_LK/F_OTC/F_XQ/F_XJ/F_PBMC/F_CTC/F_PDO/F_ZKPDO）或类型名称（蜡块/血清/血浆/类器官/在库类器官等）。F_PDO 与类器官对应 F_YBLX=类器官；F_ZKPDO 与在库类器官是其中库存状态为在库的记录")


class PdoDetailItem(BaseModel):
    """PDO 样本详情弹框行"""
    F_DC: Optional[str] = Field(None, description="代次")
    F_SGBH: Optional[str] = Field(None, description="样本编号")
    F_YBZT: Optional[str] = Field(None, description="样本状态（优先库存状态 F_KCZT，否则运输状态 F_YSZT）")
    F_YBLX: Optional[str] = Field(None, description="样本类型")
    F_XBSL: Optional[Any] = Field(None, description="细胞数量")
    F_GDBH: Optional[str] = Field(None, description="培养工单编号")
    F_RKSJ: Optional[str] = Field(None, description="入库时间")


class PdoDetailOut(BaseModel):
    F_ID: int = Field(..., description="T_YBZJ 主键")
    F_ZZID: Optional[str] = Field(None, description="标本号")
    items: List[PdoDetailItem] = Field(default_factory=list)
    total: int = 0


class SampleReceiveDeleteIn(BaseModel):
    F_ID: int = Field(..., description="T_YBZJ 主键，取标本接收列表行的 F_ID")


class SampleReceiveDeleteOut(BaseModel):
    F_ID: int
    F_ZZID: str
    F_ZZID_RECYCLED: bool = Field(description="标本号是否已退回编码流水。true 表示它是当前最后一个自动号，下次预览会再次拿到")


class PreviewZZIDRequest(BaseModel):
    """预览标本号入参。字段语义与 save-and-distribute 的 JZXX/BBXX/YBZJ 一致，合并为一个 JSON body。"""
    JZXX: Optional[dict] = Field(default=None, description="就诊信息（对应 save-and-distribute 的 JZXX 字段）")
    BBXX: Optional[dict] = Field(default=None, description="标本信息（对应 save-and-distribute 的 BBXX 字段）")
    YBZJ: Optional[dict] = Field(default=None, description="样本登记信息（对应 save-and-distribute 的 YBZJ 字段）")


class PreviewZZIDResponse(BaseModel):
    """预览标本号出参"""
    F_ZZID: str = Field(description="预计标本号（窥探值，不消耗序列号；保存时由后端正式分配）")
    source: str = Field(description="生成来源：'code_rule'（字典编码规则）或 'fallback'（硬编码兜底）")


