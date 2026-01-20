from pydantic import BaseModel, Field
from typing import Optional, List


class SampleReceiveQuery(BaseModel):
    F_ZZID: Optional[str] = Field(default=None, description="标本号")
    F_ZYH: Optional[str] = Field(default=None, description="住院号")
    F_SJTMH: Optional[str] = Field(default=None, description="送检条码号")

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
    F_ZQTYS_ID: Optional[str] = None
    F_ZQTYS_DZ: Optional[str] = None
    F_MZH: Optional[str] = None
    F_CHH: Optional[str] = None
    F_BZTZ: Optional[str] = None
    F_SJTMH: Optional[str] = None  # 送检条码号
    F_CSNY: Optional[str] = None  # 出生年月

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

#质检信息
class QualityInfo(BaseModel):
    F_SYTP: Optional[str] = None
    F_SYTP_DZ: Optional[str] = None
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


class DistributeOut(BaseModel):
    F_ID: int
    F_ZZID: str
    F_PYRY: int
    F_MXLX: str
    F_GDLX: Optional[str] = None
    F_GDZT: Optional[str] = None
    F_FFSJ: str
    F_FFCJR: str
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
class YBZJListQuery(BaseModel):
    F_ZZID: Optional[str] = None
    F_XM: Optional[str] = None
    F_BRBH: Optional[str] = None
    F_ZYH: Optional[str] = None
    F_BBLX: Optional[str] = None
    F_BHG: Optional[str] = None
    F_SFPY: Optional[str] = None
    F_DJRQ_START: Optional[str] = None
    F_DJRQ_END: Optional[str] = None
    F_CXSJ_START: Optional[str] = None
    F_CXSJ_END: Optional[str] = None
    skip: int = 0
    limit: Optional[int] = 20



class DistributeUpdate(BaseModel):
    F_ID: int = Field(description="分发记录ID")
    F_PYRY: Optional[int] = Field(default=None, description="培养人员用户ID(可选)")
    F_MXLX: Optional[str] = Field(default=None, description="模型类型(可选)")
    F_GDLX: Optional[str] = Field(default=None, description="工单类型(可选)")


class DistributeDelete(BaseModel):
    F_ID: int = Field(description="分发记录ID")


# 合并保存+分发 入参（distributes 为数组项）
class DistributeBatchItem(BaseModel):
    F_PYRY: int = Field(description="培养人员用户ID")
    F_MXLX: str = Field(min_length=1, description="模型类型")
    F_GDLX: Optional[str] = Field(default=None, description="工单类型(可选)")


class SaveAndDistributeOut(BaseModel):
    F_ZZID: str
    ybzj_saved: bool
    distribute_ids: List[int]
    qc_audit_ids: Optional[List[int]] = Field(default_factory=list, description="质控审核记录ID列表")
    pdf_id: Optional[int] = Field(default=None, description="生成的PDF文件ID")
class YBZJListItem(BaseModel):
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
    F_CTC: int = 0
    F_BLSJ: Optional[str] = None
    F_ZXSJ: Optional[str] = None



