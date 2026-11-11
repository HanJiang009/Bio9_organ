"""
流水线进度展示模块 Schemas

以培养工单为维度，展示登记→分发→培养→质控→传代→入库各阶段完成进度。
"""
from typing import Optional, List, Any, Dict
from pydantic import BaseModel, Field


class StageStatus(BaseModel):
    """单个流程阶段的状态与时间"""
    status: Optional[str] = Field(
        default=None,
        description="阶段状态：已完成 / 进行中 / 不合格 / 审核通过 / 审核不通过"
    )
    status_display: Optional[str] = Field(
        default=None,
        description="前端展示状态：已完成 / 进行中 / 未开始 / 异常"
    )
    time: Optional[str] = Field(
        default=None,
        description="该阶段对应的关键时间（格式 YYYY-MM-DD 或 YYYY-MM-DD HH:MM:SS）"
    )


class StagePyStatus(StageStatus):
    """培养阶段状态（额外携带流向流水线信息）"""
    F_PIPELINE_LX: Optional[List[int]] = Field(default=None, description="流向阶段流水线ID列表（支持多选）")
    F_MC: Optional[str] = Field(default=None, description="流向阶段流水线模板名称（多个时以逗号分隔）")


class PipelineProgressItem(BaseModel):
    """单条培养工单的流水线进度"""
    F_ID: int = Field(description="工单ID")
    F_GDBH: Optional[str] = Field(default=None, description="工单编号")
    F_ZZID: Optional[str] = Field(default=None, description="标本号")
    F_BRBH: Optional[str] = Field(default=None, description="病人编号（聚合维度）")
    F_AZ: Optional[str] = Field(default=None, description="癌种（聚合维度）")
    F_SQR: Optional[str] = Field(default=None, description="培养人（聚合维度）")
    F_YBLY: Optional[str] = Field(default=None, description="样本来源")
    F_ZDYXH: Optional[str] = Field(default=None, description="自定义序号")
    F_PYSJ: Optional[str] = Field(default=None, description="开始培养日期")
    F_PYTS: Optional[int] = Field(default=None, description="总计培养天数")
    stage_djjs: StageStatus = Field(default_factory=StageStatus, description="登记阶段状态")
    stage_ff: StageStatus = Field(default_factory=StageStatus, description="分发阶段状态")
    stage_py: StagePyStatus = Field(default_factory=StagePyStatus, description="培养阶段状态")
    stage_zk: StageStatus = Field(default_factory=StageStatus, description="质控阶段状态")
    stage_cd: StageStatus = Field(default_factory=StageStatus, description="传代阶段状态")
    stage_rk: StageStatus = Field(default_factory=StageStatus, description="入库阶段状态（仅统计培养工单来源分装入库，不含标本直接入库）")


class PipelineProgressGroup(BaseModel):
    """按某个维度聚合的工单分组"""
    key: Optional[str] = Field(description="聚合键值（培养人/癌种/病人编号）")
    total: int = Field(description="本组工单总数")
    items: List[PipelineProgressItem] = Field(default_factory=list)


class PipelineProgressListOut(BaseModel):
    """不分组时的返回结构"""
    total: int
    items: List[PipelineProgressItem]


class PipelineProgressGroupOut(BaseModel):
    """分组时的返回结构"""
    total: int = Field(description="符合过滤条件的工单总数")
    groups: List[PipelineProgressGroup]


class PipelineProgressQuery(BaseModel):
    """流水线进度列表查询入参"""
    F_SQR: Optional[List[int]] = Field(default=None, description="培养人用户ID（T_USER.F_ID，多选）")
    F_AZ: Optional[str] = Field(default=None, description="癌种（模糊匹配）")
    F_BRBH: Optional[str] = Field(default=None, description="病人编号（模糊匹配）")
    F_ZZID: Optional[str] = Field(default=None, description="标本号（模糊匹配）")
    F_GDBH: Optional[str] = Field(default=None, description="工单编号（模糊匹配）")
    F_YBLY: Optional[List[str]] = Field(default=None, description="样本来源（多选精确匹配，如 [\"原代制备\", \"传代\"]）")
    F_PYSJ_START: Optional[str] = Field(default=None, description="开始培养日期 起点（格式 YYYY-MM-DD 或 YYYY-MM-DD HH:MM:SS，含端点）")
    F_PYSJ_END: Optional[str] = Field(default=None, description="开始培养日期 终点（格式 YYYY-MM-DD 或 YYYY-MM-DD HH:MM:SS，含端点）")
    group_by: Optional[str] = Field(
        default=None,
        description="聚合分组维度：SQR（培养人）| AZ（癌种）| BRBH（病人编号）；不传则返回平铺列表"
    )
    skip: int = Field(default=0, ge=0, description="跳过记录数（分页）")
    limit: int = Field(default=20, gt=0, description="每页条数")
