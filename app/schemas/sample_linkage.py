from datetime import datetime
from typing import Optional, Dict, Any, List, Union, Literal
from pydantic import BaseModel, Field, field_validator, model_validator


class BoxInput(BaseModel):
    """分装时指定盒子信息（用于创建盒子记录或校验容量）"""
    F_HZH: Optional[str] = Field(
        None,
        description="盒号。可先调 /box-mgmt/preview-hzh 预览后填入；"
                    "保存时后端按标本号同样逻辑判断占号/手填。"
                    "未配置编码规则时须手填；仅预建盒且走规则时可省略",
    )
    F_TEMPLATE_ID: Optional[int] = Field(None, description="模板ID，新盒子必填；已有盒子可省略。前端空串按未传处理")
    F_KSWZ: Optional[str] = Field(
        None,
        description="本次开始填充的起始格子，字母数字格式如 A4、B2，可选，默认从 A1 开始。"
                    "用于跳过盒子前段已有样本的格子，精确控制本次分配区间。"
    )

    @field_validator("F_TEMPLATE_ID", mode="before")
    @classmethod
    def _coerce_template_id(cls, v: Any) -> Optional[int]:
        """兼容前端未选模板时传 "" / null，统一为 None。"""
        if v is None:
            return None
        if isinstance(v, str):
            s = v.strip()
            if not s:
                return None
            try:
                return int(s)
            except ValueError as e:
                raise ValueError("F_TEMPLATE_ID 必须是整数") from e
        if isinstance(v, bool):
            raise ValueError("F_TEMPLATE_ID 必须是整数")
        if isinstance(v, float):
            if v.is_integer():
                return int(v)
            raise ValueError("F_TEMPLATE_ID 必须是整数")
        return v

    @field_validator("F_HZH", "F_KSWZ", mode="before")
    @classmethod
    def _empty_str_to_none(cls, v: Any) -> Optional[str]:
        if v is None:
            return None
        if isinstance(v, str):
            s = v.strip()
            return s if s else None
        return v


class SampleLinkageQuery(BaseModel):
    F_ZZID: Optional[str] = Field(default=None, description="标本号 模糊匹配")
    F_BRBH: Optional[str] = Field(default=None, description="病人编号/住院号 模糊匹配")
    F_GDBH: Optional[str] = Field(default=None, description="培养工单编号 模糊匹配（仅匹配工单记录）")
    F_DJRQ_START: Optional[str] = Field(default=None, description="登记日期起始，格式 YYYY-MM-DD")
    F_DJRQ_END: Optional[str] = Field(default=None, description="登记日期结束，格式 YYYY-MM-DD")
    F_CXSJ_START: Optional[str] = Field(default=None, description="采集时间起始，格式 YYYY-MM-DD 或 YYYY-MM-DD HH:MM:SS")
    F_CXSJ_END: Optional[str] = Field(default=None, description="采集时间结束，格式 YYYY-MM-DD 或 YYYY-MM-DD HH:MM:SS")
    F_ZCSJ_START: Optional[str] = Field(default=None, description="暂存时间起始。匹配已装入盒子、且盒子创建时间在此区间的标本/工单")
    F_ZCSJ_END: Optional[str] = Field(default=None, description="暂存时间结束，格式同起始")
    skip: int = Field(default=0, ge=0)
    limit: int = Field(default=50, ge=1)


class SampleLinkageItem(BaseModel):
    F_YBZJ_ID: Optional[int] = None
    F_PYGD_ID: Optional[int] = None
    F_ZZID: Optional[str] = None
    F_GDID: Optional[int] = None
    F_GDBH: Optional[str] = None
    F_BBLX: Optional[str] = None
    F_XM: Optional[str] = None
    F_BBLY: Optional[str] = None
    F_XB: Optional[str] = None
    F_NL: Optional[int] = None
    F_BRBH: Optional[str] = None
    F_ZYH: Optional[str] = None
    F_CXSJ: Optional[str] = None
    F_DJRQ: Optional[str] = None
    F_ZZID_YBS: int = Field(default=0, description="该行标本号下已保存的样本管数量。大于 0 时标本号可点")
    F_GDBH_YBS: int = Field(default=0, description="该行培养工单下已保存的样本管数量。标本行为 0；大于 0 时工单号可点")
    F_YGL: int = Field(
        default=0,
        description="标本行：标本号下有样本管则为 1。工单行：只表示该工单下有样本管，不表示同一行标本号有没有管子",
    )

    class Config:
        extra = "allow"  # Allow additional fields


class SampleLinkageResponse(BaseModel):
    items: List[SampleLinkageItem] = Field(default_factory=list)
    total: int = 0


class LinkedSampleQuery(BaseModel):
    """已关联样本查询。标本号与培养工单号二选一。"""
    F_ZZID: Optional[str] = Field(default=None, description="标本号。传入后返回该标本下全部已保存关联样本")
    F_GDBH: Optional[str] = Field(default=None, description="培养工单号。传入后只返回该工单的已保存关联样本")

    @model_validator(mode="after")
    def _exactly_one(self):
        zzid = (self.F_ZZID or "").strip()
        gdbh = (self.F_GDBH or "").strip()
        if bool(zzid) == bool(gdbh):
            raise ValueError("F_ZZID 与 F_GDBH 必须且只能填写一个")
        self.F_ZZID = zzid or None
        self.F_GDBH = gdbh or None
        return self


class LinkedSampleItem(BaseModel):
    """已保存的关联样本。"""
    F_XH: int = Field(description="序号")
    F_SGBH: Optional[str] = Field(default=None, description="样本编号")
    F_YBLX: Optional[str] = Field(default=None, description="样本类型")
    F_ZZID: Optional[str] = Field(default=None, description="标本号")
    F_GDBH: Optional[str] = Field(default=None, description="培养工单号")
    F_DC: Optional[str] = Field(default=None, description="代次")
    F_CCXS: Optional[str] = Field(default=None, description="储藏形式")
    F_FGS: Optional[int] = Field(default=None, description="分管数")
    F_TJ: Optional[str] = Field(default=None, description="体积")
    F_XBSL: Optional[str] = Field(default=None, description="细胞数量")
    F_YBGLX: Optional[str] = Field(default=None, description="样本管类型")
    F_BZ: Optional[str] = Field(default=None, description="备注")


class LinkedSampleResponse(BaseModel):
    items: List[LinkedSampleItem] = Field(default_factory=list)


class SampleAllocationInfo(BaseModel):
    """
    描述一组分装规格（几管、什么类型、是否运输等）。
    盒子分配由外层 BOXES 参数统一管理，此处不再指定盒号或位置。
    """
    F_YBLX: Optional[str] = None
    F_YBGLX: Optional[str] = Field(
        default=None,
        description="样本管类型（可选；若传则不得为 null 或空字符串）",
    )
    F_CCXS: Optional[str] = None
    F_FGS: int = Field(description="分管数", ge=1)
    F_TJ: Optional[str] = None
    F_XBSL: Optional[str] = None
    F_SPREC: Optional[str] = None
    F_BZ: Optional[str] = None
    F_SFYS: Optional[bool] = Field(default=None)  # 是否运输，未提供则预览默认 True

    @field_validator("F_XBSL", mode="before")
    @classmethod
    def _coerce_xbsl(cls, v: Any) -> Optional[str]:
        if v is None:
            return None
        if isinstance(v, bool):
            return str(v)
        if isinstance(v, int):
            return str(v)
        if isinstance(v, float):
            return str(int(v)) if v.is_integer() else str(v)
        if isinstance(v, str):
            s = v.strip()
            return s if s else None
        s = str(v).strip()
        return s if s else None

    @model_validator(mode="before")
    @classmethod
    def _validate_ybglx_if_present(cls, data: Any):
        if isinstance(data, dict) and "F_YBGLX" in data:
            v = data.get("F_YBGLX")
            if v is None:
                raise ValueError("F_YBGLX（样本管类型）不能为 null")
            text = str(v).strip()
            if not text:
                raise ValueError("F_YBGLX（样本管类型）不能为空字符串")
            data["F_YBGLX"] = text
        return data


class SampleAllocationRequest(BaseModel):
    YBZJ_IDS: List[int] = Field(default_factory=list)
    PYGD_IDS: List[int] = Field(default_factory=list)
    FZXX: List[SampleAllocationInfo] = Field(default_factory=list)
    # 本次使用的盒子列表（按顺序贪心填充；新盒子必须提供 F_TEMPLATE_ID）
    BOXES: Optional[List[BoxInput]] = Field(default_factory=list, description="本次使用的盒子，按顺序贪心填充，新盒子必须提供F_TEMPLATE_ID")

    @field_validator("FZXX", mode="before")
    @classmethod
    def _ensure_list(cls, v):
        if v is None:
            return []
        if isinstance(v, dict):
            return [v]
        return v


class SampleTubeItem(BaseModel):
    F_SGBH: str
    F_ZZID: str
    F_YBZJ_ID: Optional[int] = None
    F_PYGD_ID: Optional[int] = None
    F_DC: Optional[str] = None
    F_YBLX: Optional[str] = None
    F_YBGLX: Optional[str] = Field(
        default=None,
        description="样本管类型（可选；若传则不得为 null 或空字符串）",
    )
    F_CCXS: Optional[str] = None
    F_FGS: int
    F_TJ: Optional[str] = None
    F_XBSL: Optional[str] = None
    F_SPREC: Optional[str] = None
    F_BZ: Optional[str] = None
    F_WZ: Optional[str] = None
    F_SFYS: bool = True

    # 盒号与位置坐标
    F_HZH: Optional[str] = None

    # 从 T_YBZJ 带出的患者信息
    F_BRBH: Optional[str] = None
    F_ZYH: Optional[str] = None
    F_MZH: Optional[str] = None
    F_XM: Optional[str] = None
    F_XB: Optional[str] = None
    F_NL: Optional[int] = None
    F_NLDW: Optional[str] = None
    F_DZ: Optional[str] = None
    F_DH: Optional[str] = None
    F_CHH: Optional[str] = None
    F_SFZH: Optional[str] = None
    F_LCZD: Optional[str] = None
    F_LYKS: Optional[str] = None
    F_KS: Optional[str] = None
    F_CXSJ: Optional[str] = None
    F_XMBH: Optional[str] = None
    F_MZ: Optional[str] = None
    F_HY: Optional[str] = None
    F_ZY: Optional[str] = None
    F_ZZBW: Optional[str] = Field(default=None, description="组织部位/采集部位")

    @model_validator(mode="before")
    @classmethod
    def _validate_ybglx_if_present(cls, data: Any):
        if isinstance(data, dict) and "F_YBGLX" in data:
            v = data.get("F_YBGLX")
            if v is None:
                raise ValueError("F_YBGLX（样本管类型）不能为 null")
            text = str(v).strip()
            if not text:
                raise ValueError("F_YBGLX（样本管类型）不能为空字符串")
            data["F_YBGLX"] = text
        return data

    @field_validator(
        "F_CXSJ", "F_DC", "F_YBLX", "F_CCXS", "F_TJ", "F_XBSL", "F_SPREC",
        "F_BZ", "F_WZ", "F_HZH", "F_BRBH", "F_ZYH", "F_MZH", "F_XM",
        "F_XB", "F_NLDW", "F_DZ", "F_DH", "F_CHH", "F_SFZH", "F_LCZD",
        "F_LYKS", "F_KS", "F_XMBH", "F_MZ", "F_HY", "F_ZY", "F_ZZBW",
        mode="before",
    )
    @classmethod
    def _coerce_str(cls, v):
        if v is None:
            return None
        if isinstance(v, datetime):
            return v.strftime("%Y-%m-%d %H:%M:%S")
        return str(v) if not isinstance(v, str) else v

    class Config:
        extra = "allow"


class SampleAllocationPreview(BaseModel):
    items: List[SampleTubeItem] = Field(default_factory=list)
    total: int = 0
    capacity_warnings: List[str] = Field(default_factory=list, description="非空盒子警告：已占格子会跳过，按空闲格分配")


class SampleAllocationSaveRequest(BaseModel):
    YBZJ_IDS: List[int] = Field(default_factory=list)
    PYGD_IDS: List[int] = Field(default_factory=list)
    FZXX: List[SampleAllocationInfo] = Field(default_factory=list)
    ITEMS: Optional[List[SampleTubeItem]] = Field(
        default=None, 
        description="可选：条目级分装列表（含精确运输标识）",
        alias="items"  # 支持小写 "items" 作为别名
    )
    
    # 本次使用的盒子列表（按顺序贪心填充；新盒子必须提供 F_TEMPLATE_ID）
    BOXES: Optional[List[BoxInput]] = Field(default_factory=list, description="本次使用的盒子，按顺序贪心填充，新盒子必须提供F_TEMPLATE_ID")

    # 运输单信息
    F_FQR: Optional[str] = Field(default=None, description="运输发起人")
    F_FQSJ: Optional[str] = Field(default=None, description="运输发起时间，ISO或yyyy-MM-dd HH:mm:ss")
    F_BZ: Optional[str] = Field(default=None, description="运输备注")
    F_YSMDD: Optional[str] = Field(default=None, description="运输目的地")
    
    model_config = {"populate_by_name": True}  # 允许使用字段名或别名

    @field_validator("FZXX", mode="before")
    @classmethod
    def _ensure_list(cls, v):
        if v is None:
            return []
        if isinstance(v, dict):
            return [v]
        return v

    @field_validator("ITEMS", mode="before")
    @classmethod
    def _ensure_items_list(cls, v):
        if v is None:
            return None
        if isinstance(v, dict):
            return [v]
        return v


class SampleAllocationScanRequest(SampleAllocationRequest):
    """手动扫描预制试管编码的分装预览请求，在原版基础上新增外部试管编码数组"""
    TUBE_CODES: List[str] = Field(
        default_factory=list,
        description="外部扫描获取的试管编码数组，按顺序分配给生成的样本关联条目"
    )


class SampleAllocationScanSaveRequest(SampleAllocationSaveRequest):
    """手动扫描预制试管编码的分装保存请求，在原版基础上新增外部试管编码数组"""
    TUBE_CODES: List[str] = Field(
        default_factory=list,
        description="外部扫描获取的试管编码数组，按顺序分配给保存的样本分装条目；缺少编码的条目将被跳过不保存"
    )


class SampleAllocationDraftRequest(BaseModel):
    """
    暂存分装请求。

    与 SampleAllocationSaveRequest 功能相同，但无论 F_SFYS 取值，
    所有分装记录均以 F_YSZT='未运送' 写入 T_YBFZ，且不创建运输单（T_YSXX）。
    用于需要先保存盒位信息、后续再发起运输的场景。
    """
    YBZJ_IDS: List[int] = Field(default_factory=list)
    PYGD_IDS: List[int] = Field(default_factory=list)
    FZXX: List[SampleAllocationInfo] = Field(default_factory=list)
    ITEMS: Optional[List[SampleTubeItem]] = Field(
        default=None,
        description="可选：条目级分装列表（含盒号和格子坐标），优先于 FZXX 使用",
        alias="items"
    )
    BOXES: Optional[List[BoxInput]] = Field(
        default_factory=list,
        description="本次使用的盒子，按顺序贪心填充，新盒子必须提供 F_TEMPLATE_ID"
    )

    model_config = {"populate_by_name": True}

    @field_validator("FZXX", mode="before")
    @classmethod
    def _ensure_list(cls, v):
        if v is None:
            return []
        if isinstance(v, dict):
            return [v]
        return v

    @field_validator("ITEMS", mode="before")
    @classmethod
    def _ensure_items_list(cls, v):
        if v is None:
            return None
        if isinstance(v, dict):
            return [v]
        return v


class SampleAllocationDraftScanRequest(SampleAllocationDraftRequest):
    """手动扫描预制试管编码的分装暂存请求，在暂存版基础上新增外部试管编码数组"""
    TUBE_CODES: List[str] = Field(
        default_factory=list,
        description="外部扫描获取的试管编码数组，按顺序分配给暂存的样本分装条目；缺少编码的条目将被跳过不保存"
    )


class TubeScanRequest(BaseModel):
    F_SGBH: str = Field(..., description="样本管编号")


class TransportCreateRequest(BaseModel):
    YBFZ: List[int] = Field(default_factory=list, description="待运输的分装记录ID列表")
    F_FQR: Optional[str] = Field(default=None, description="运输发起人")
    F_FQSJ: Optional[str] = Field(default=None, description="运输发起时间，ISO或yyyy-MM-dd HH:mm:ss")
    F_BZ: Optional[str] = Field(default=None, description="运输备注")
    F_YSMDD: Optional[str] = Field(default=None, description="运输目的地")
    
    @field_validator("YBFZ", mode="before")
    @classmethod
    def _ensure_list(cls, v):
        if v is None:
            return []
        if isinstance(v, (int, str)):
            return [v]
        return v


class TransportListQuery(BaseModel):
    skip: int = Field(default=0, ge=0)
    limit: int = Field(default=50, ge=1, le=200)
    F_YSDH: Optional[str] = Field(default=None, description="模糊匹配运输单号")
    F_YSDZT: Optional[str] = Field(default=None, description="运输单状态过滤")
    F_FQSJ_START: Optional[str] = Field(default=None, description="发起时间起")
    F_FQSJ_END: Optional[str] = Field(default=None, description="发起时间止")
    F_SGBH: Optional[str] = Field(default=None, description="样本编号模糊匹配，需要通过 F_YSXX 的分装ID关联查询")


class TransportListResponse(BaseModel):
    items: List[dict] = Field(default_factory=list)
    total: int = 0


class AvailableSampleQuery(BaseModel):
    """可用样本查询（用于运输单创建前的样本挑选）"""
    F_SGBH: Optional[str] = Field(default=None, description="样本编号，模糊匹配")
    F_GDBH: Optional[str] = Field(default=None, description="培养工单编号，模糊匹配")
    F_ZZID: Optional[str] = Field(default=None, description="标本号，模糊匹配")
    F_DC: Optional[str] = Field(default=None, description="代次，模糊匹配")
    skip: int = Field(default=0, ge=0)
    limit: int = Field(default=50, ge=1)


class AvailableSampleResponse(BaseModel):
    """可用样本查询响应"""
    items: List[Dict[str, Any]] = Field(default_factory=list)
    total: int = 0


class TransportingSampleQuery(BaseModel):
    """运输中样本查询（按运输单聚合，用于接收方查询运输单及样本信息）"""
    # 运输单级别过滤条件
    F_YSDH: Optional[str] = Field(default=None, description="运输单号，模糊匹配")
    F_FQR: Optional[str] = Field(default=None, description="发起人，模糊匹配")
    F_FQSJ_START: Optional[str] = Field(default=None, description="发起时间起始")
    F_FQSJ_END: Optional[str] = Field(default=None, description="发起时间结束")
    F_YSMDD: Optional[str] = Field(default=None, description="运输目的地，模糊匹配")
    
    # 样本级别过滤条件（如果指定，则只返回包含匹配样本的运输单）
    F_SGBH: Optional[str] = Field(default=None, description="样本编号，模糊匹配")
    F_GDBH: Optional[str] = Field(default=None, description="培养工单编号，模糊匹配")
    F_ZZID: Optional[str] = Field(default=None, description="标本号，模糊匹配")
    F_DC: Optional[str] = Field(default=None, description="代次，模糊匹配")
    
    skip: int = Field(default=0, ge=0)
    limit: int = Field(default=50, ge=1, description="分页大小（按运输单数量分页）")


class TransportingSampleResponse(BaseModel):
    """运输中样本查询响应（样本数据在 data 中，运输单数据平级）"""
    items: List[Dict[str, Any]] = Field(default_factory=list, description="样本列表，每条记录包含 F_YSDH 字段")
    total: int = Field(default=0, description="样本总数")


class TransportingSampleFullResponse(BaseModel):
    """运输中样本完整响应（包含运输单和样本数据）"""
    code: int
    msg: str
    transports: List[Dict[str, Any]] = Field(default_factory=list, description="运输单列表")
    transport_total: int = Field(default=0, description="运输单总数")
    data: TransportingSampleResponse


class TransportDetailQuery(BaseModel):
    F_ID: Optional[int] = Field(default=None, description="运输单主键，优先使用")
    F_YSDH: Optional[str] = Field(default=None, description="运输单号，备用")


class TransportDetailResponse(BaseModel):
    F_ID: Optional[int] = None
    F_YSDH: str
    F_YSDZT: Optional[str] = None
    F_FQR: Optional[str] = None
    F_FQSJ: Optional[str] = None
    F_BZ: Optional[str] = None
    F_YSMDD: Optional[str] = None
    F_HZH: Optional[str] = Field(default=None, description="盒号（运输单内样本去重汇总）")
    F_HZGG: Optional[str] = Field(default=None, description="盒子规格（模板名称，缺省则为行×列）")
    items: List[dict] = Field(default_factory=list, description="分装样本详情")


class ExternalSampleStatusSyncItem(BaseModel):
    """单根样本管的外部状态同步明细。"""
    F_SGBH: str = Field(..., description="样本试管编号")
    F_SJ: Optional[str] = Field(
        default=None,
        description="该管事件时间，ISO 或 yyyy-MM-dd HH:mm:ss；默认当前时间",
    )
    F_CZR: Optional[str] = Field(default=None, description="该管操作人")
    F_BZ: Optional[str] = Field(default=None, description="该管备注")


class ExternalSampleStatusSyncRequest(BaseModel):
    """
    外部系统样本状态同步（按样本管逐条推送）。

    F_EVENT 说明：
    - stored：样本库入库 → F_KCZT=在库，F_RKSJ；若仍为运送中则隐式完成运输接收（方案 A）
    - outbound_done：样本库出库完成 → F_KCZT=已出库，F_CKSJ
    - received：仅保留兼容；外部主流程请使用 stored
    """
    F_EVENT: Literal["received", "stored", "outbound_done"] = Field(
        description="状态事件：stored | outbound_done | received(兼容)",
    )
    ITEMS: List[ExternalSampleStatusSyncItem] = Field(
        ...,
        min_length=1,
        max_length=1000,
        description="样本管明细列表，每次请求仅一种业务类型",
    )

    @model_validator(mode="after")
    def _validate_unique_sgbh(self):
        seen: set[str] = set()
        for item in self.ITEMS:
            sgbh = (item.F_SGBH or "").strip()
            if not sgbh:
                raise ValueError("ITEMS 中 F_SGBH 不能为空")
            if sgbh in seen:
                raise ValueError(f"ITEMS 中 F_SGBH 重复: {sgbh}")
            seen.add(sgbh)
        return self


class ExternalPushSyncRequest(BaseModel):
    """
    生物样本库系统实际推送报文（外部系统 → 类器官系统）。

    示例（样本入库）：
    {
        "code": 0, "msg": "查询成功", "pushTime": "2026-07-07 15:31:10.135",
        "data": {
            "task": "样本入库",
            "ybmx": [
                {"f_sgbh": "YBBH0001", "f_rksj": "2026-05-19 09:21:51.000",
                 "f_rky": "张老师", "f_rkzt": "已入库"}
            ]
        }
    }

    示例（样本出库）：
    {
        "code": 0, "msg": "查询成功", "pushTime": "2026-07-07 15:30:45.480",
        "data": {
            "task": "样本出库",
            "ybmx": [
                {"f_sgbh": "YBBH0001", "f_cksj": "2026-05-19 09:21:51.000",
                 "f_cky": "张老师", "f_sybz": "全部用完",
                 "f_ckdh": "CKD_260702001", "f_ckzt": "已出库"}
            ]
        }
    }
    """
    code: Optional[int] = Field(default=None, description="外部系统请求码，非本系统校验字段")
    msg: Optional[str] = Field(default=None, description="外部系统请求说明，非本系统校验字段")
    pushTime: Optional[str] = Field(default=None, description="外部系统推送时间")
    data: Dict[str, Any] = Field(..., description="推送内容：{task, ybmx: [...]}")


class ExternalPushAckResponse(BaseModel):
    """回执给生物样本库系统的响应报文（固定契约，不套用通用 ApiResponse 外壳）。"""
    code: int = Field(description="0=请求已接收并成功解析处理；非0=请求被拒绝，msg 说明原因")
    msg: str = Field(description="处理说明")
    recvTime: str = Field(description="本系统接收到请求的时间，yyyy-MM-dd HH:mm:ss.SSS")
    detail: Optional[Dict[str, Any]] = Field(
        default=None,
        description="内部处理明细（成功/跳过/失败条数、复苏工单创建结果等），仅供排查参考，外部系统可忽略",
    )


class SampleBySgbhQuery(BaseModel):
    F_SGBH: str = Field(description="样本编号/试管编号")


class SampleBySgbhResponse(BaseModel):
    F_ID: Optional[int] = None
    F_SGBH: Optional[str] = None
    F_ZZID: Optional[str] = None
    F_DC: Optional[str] = None
    F_PYGD_ID: Optional[int] = None
    F_YBLX: Optional[str] = None
    F_BBLX: Optional[str] = None
    F_GDBH: Optional[str] = None
    F_PYR: Optional[str] = None
    F_XMBH: Optional[str] = Field(default=None, description="项目编号（快照）")
