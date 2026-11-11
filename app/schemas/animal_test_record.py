from pydantic import BaseModel, Field, model_validator
from typing import Optional, List, Any


# ==================== 组别观察记录 ====================

class GroupObsAttachment(BaseModel):
    """组别观察附件（含倍数）"""
    F_FJ: Optional[Any]  = Field(None, description="文件ID（保存后）或文件信息对象（查询时）")
    F_BS: Optional[str]  = Field(None, description="倍数，如 40x / 100x")


class GroupObsItem(BaseModel):
    """单个组别的观察数据（保存入参）"""
    F_ZB:       Optional[str]  = Field(None, description="组别名称（对应接种信息中的F_ZB）")
    F_XSYS:     Optional[str]  = Field(None, description="小鼠饮食")
    F_HDD:      Optional[str]  = Field(None, description="活动度")
    F_YCBX:     Optional[str]  = Field(None, description="小鼠异常表现")
    F_XSTZ:     Optional[str]  = Field(None, description="小鼠体重")
    F_ZLDX:     Optional[str]  = Field(None, description="肿瘤大小")
    F_TJJS:     Optional[str]  = Field(None, description="体积计算")
    F_YZWZSFYC: Optional[str]  = Field(None, description="移植部位是否异常（是/否）")
    F_FJ:       Optional[List[Any]] = Field(
        default_factory=list,
        description="附件列表，含倍数。保存时传 [{\"F_FJ_INDEX\": 0, \"F_BS\": \"40x\"}]，"
                    "已有附件传 [{\"F_FJ\": 101, \"F_BS\": \"40x\"}]"
    )
    F_BZ:       Optional[str]  = Field(None, description="备注")
    F_SORT:     Optional[int]  = Field(None, description="排序")


class GroupObsDetail(GroupObsItem):
    """组别观察记录详情（含ID，附件已转为文件信息）"""
    F_ID: int


# ==================== 观察记录（时间点）====================

class ObsHeaderItem(BaseModel):
    """观察记录头信息（保存入参）"""
    F_GCSJ:  Optional[str] = Field(None, description="观察时间（YYYY-MM-DD HH:MM:SS）")
    F_PYDS:  Optional[int] = Field(None, description="培养天数（整数）")
    F_JLRQ:  Optional[str] = Field(None, description="记录日期")
    F_JLR:   Optional[str] = Field(None, description="记录人")


class ObservationDetail(ObsHeaderItem):
    """观察记录详情（查询返回）"""
    F_ID:      int
    F_SYJH_ID: int
    F_SORT:    Optional[int] = None
    F_CJSJ:    Optional[str] = None
    ZBXX:      List[GroupObsDetail] = Field(default_factory=list, description="各组别观察数据")


class ObservationListOut(BaseModel):
    """观察记录列表返回"""
    items: List[ObservationDetail]
    total: int


# ==================== 终点记录 ====================

class EndpointRecordSave(BaseModel):
    """终点记录保存入参（JSON 部分）"""
    F_SYZQ:   Optional[str] = Field(None, description="实验周期（默认取最后一条观察记录的培养天数）")
    F_YZZZQK: Optional[str] = Field(None, description="移植生长情况")
    F_FJ:     Optional[List[Any]] = Field(
        default_factory=list,
        description="已有附件ID列表，如 [101, 102]；新上传文件通过 multipart F_FJ 字段传入"
    )
    F_SYJL:   Optional[str] = Field(None, description="实验结论")
    F_JLRQ:   Optional[str] = Field(None, description="实验记录日期（默认今日，支持修改）")


class EndpointRecordDetail(BaseModel):
    """终点记录详情（查询返回）"""
    F_ID:      int
    F_SYJH_ID: int
    F_SYZQ:    Optional[str] = None
    F_YZZZQK:  Optional[str] = None
    F_FJ:      Optional[List[Any]] = Field(None, description="附件信息列表（含id/path/name）")
    F_SYJL:    Optional[str] = None
    F_JLR:     Optional[str] = None
    F_JLRQ:    Optional[str] = None
    F_CJSJ:    Optional[str] = None
    F_GXSJ:    Optional[str] = None


# ==================== 通用操作 ====================

class ObservationDeleteIn(BaseModel):
    """删除观察记录"""
    F_GCJL_ID: int           = Field(..., description="观察记录ID")
    F_SYJH_ID: Optional[int] = Field(None, description="实验计划ID（可不传，后端自动反查）")


class ObservationListQuery(BaseModel):
    """观察记录列表查询"""
    F_SYJH_ID: int = Field(..., description="实验计划ID")

    @model_validator(mode="before")
    @classmethod
    def _empty_str_to_none(cls, values):
        return {k: (None if v == "" else v) for k, v in values.items()}


class EndpointDetailQuery(BaseModel):
    """终点记录查询"""
    F_SYJH_ID: int = Field(..., description="实验计划ID")


class ExperimentRecordDetailQuery(BaseModel):
    """统一详情查询（实验记录整页）"""
    F_SYJH_ID: int = Field(..., description="实验计划ID")
