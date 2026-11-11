from typing import Optional, List, Dict, Any, Union
from pydantic import BaseModel, Field, field_validator, model_validator

from app.utils.field_coerce import coerce_str_list

_MULTI_FILTER_FIELDS = (
    "F_SGBH", "F_GDBH", "F_ZZID", "F_DC",
    "F_YBLX", "F_BRBH", "F_AZ", "F_LYKS", "F_BBLY", "F_CJBW", "F_PYR",
)


class UnifiedSearchQuery(BaseModel):
    F_SGBH: Optional[List[str]] = Field(default=None, description="样本编号，单值或数组；数组为 OR 模糊匹配")
    F_GDBH: Optional[List[str]] = Field(default=None, description="培养工单编号，单值或数组；数组为 OR 模糊匹配")
    F_ZZID: Optional[List[str]] = Field(default=None, description="标本号，单值或数组；数组为 OR 模糊匹配")
    F_DC: Optional[List[str]] = Field(default=None, description="代次，单值或数组；数组为 OR 模糊匹配")
    F_YBLX: Optional[List[str]] = Field(default=None, description="样本类型，单值或数组（OR）；空数组不过滤")
    F_KCZT: Optional[Union[str, List[str]]] = Field(
        default=None,
        description=(
            "样本库存状态过滤（T_YBFZ.F_KCZT），支持单选/多选（OR）："
            "'在库'/'未入库'/'已出库'；"
            "不传、传空或传'全部'表示不过滤"
        ),
    )
    F_YSZT: Optional[Union[str, List[str]]] = Field(
        default=None,
        description=(
            "【兼容字段，等同 F_KCZT】样本库存状态过滤；"
            "旧值：'已接收'→在库，'未运送'/'运送中'→未入库；"
            "不传、传空或传'全部'表示不过滤"
        ),
    )
    F_BRBH: Optional[List[str]] = Field(default=None, description="病人编号，单值或数组；数组为 OR 模糊匹配")
    F_AZ: Optional[List[str]] = Field(default=None, description="癌种/病种，单值或数组（OR）；空数组不过滤")
    F_LYKS: Optional[List[str]] = Field(default=None, description="来源科室，单值或数组（OR）；同 F_BBLY")
    F_BBLY: Optional[List[str]] = Field(default=None, description="来源科室，单值或数组（OR）。匹配样本管 F_LYKS，为空时匹配标本 F_BBLY")
    F_CJBW: Optional[List[str]] = Field(default=None, description="取材部位，单值或数组（OR，对应 T_PYGD.F_ZZBW）")
    F_PYR: Optional[List[str]] = Field(default=None, description="培养人，单值或数组（OR，对应 T_PYGD.F_SQR）")
    F_RKSJ_START: Optional[str] = Field(default=None, description="入库时间范围起始，格式 YYYY-MM-DD")
    F_RKSJ_END: Optional[str] = Field(default=None, description="入库时间范围截止，格式 YYYY-MM-DD")
    F_ZCSJ: Optional[List[str]] = Field(
        default=None,
        description="每根样本管的暂存时间（T_YBFZ.F_CJSJ），格式同入库时间：[\"YYYY-MM-DD\", \"YYYY-MM-DD\"]",
    )
    F_ZCSJ_START: Optional[str] = Field(default=None, description="暂存时间起始，由 F_ZCSJ 拆出")
    F_ZCSJ_END: Optional[str] = Field(default=None, description="暂存时间截止，由 F_ZCSJ 拆出")
    skip: int = Field(default=0, ge=0)
    limit: int = Field(default=50, ge=1)

    @field_validator(*_MULTI_FILTER_FIELDS, mode="before")
    @classmethod
    def _normalize_multi_filter(cls, v):
        """兼容前端多选：字符串、数组、空数组/空串均收下；空 → 不过滤。"""
        return coerce_str_list(v)

    @field_validator("F_KCZT", "F_YSZT", mode="before")
    @classmethod
    def _normalize_stock_status_filter(cls, v):
        if v is None:
            return None
        if isinstance(v, str):
            s = v.strip()
            if not s or s == "全部":
                return None
            return [s]
        if isinstance(v, list):
            items = [
                str(x).strip()
                for x in v
                if x is not None and str(x).strip() and str(x).strip() != "全部"
            ]
            return list(dict.fromkeys(items)) or None
        return None

    @model_validator(mode="before")
    @classmethod
    def _expand_date_ranges(cls, data: Any) -> Any:
        """入库时间、暂存时间都收 [起始, 截止] 数组，并拆成 START/END。"""
        if not isinstance(data, dict):
            return data
        data = dict(data)
        for key, start_key, end_key in (
            ("F_RKSJ", "F_RKSJ_START", "F_RKSJ_END"),
            ("F_ZCSJ", "F_ZCSJ_START", "F_ZCSJ_END"),
        ):
            start, end = _split_date_range(data.get(key))
            if start and not data.get(start_key):
                data[start_key] = start
            if end and not data.get(end_key):
                data[end_key] = end
            if key == "F_ZCSJ":
                data[key] = [x for x in (start, end) if x] or None
        return data


def _split_date_range(raw: Any):
    if raw is None or raw == "" or raw == []:
        return None, None
    if isinstance(raw, str):
        s = raw.strip()
        return (s or None), None
    if isinstance(raw, (list, tuple)):
        items = [str(x).strip() for x in raw if x is not None and str(x).strip()]
        start = items[0] if items else None
        end = items[1] if len(items) > 1 else None
        return start, end
    s = str(raw).strip()
    return (s or None), None


class UnifiedSearchList(BaseModel):
    items: List[Dict[str, Any]] = Field(default_factory=list)
    total: int = 0


class SampleDetailQuery(BaseModel):
    """样本详情查询"""
    F_ID: int = Field(description="样本分装记录ID")


class SampleDetailResponse(BaseModel):
    """样本详情响应"""
    pass  # 动态字段，使用 Dict[str, Any]


class SetSampleOrderIn(BaseModel):
    """设置样本自定义序号"""
    F_ID: int = Field(description="样本分装记录ID（T_YBFZ.F_ID）")
    F_ZDYXH: Optional[str] = Field(
        default=None,
        description="自定义序号；传空字符串 '' 或 '-1' 表示清除序号（置为 NULL）",
    )
