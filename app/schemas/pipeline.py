"""
流水线管理模块 Schemas
"""
from typing import Optional, List, Any, Dict, Union
from pydantic import BaseModel, Field, field_validator
from datetime import datetime


# ==================== 节点 Schema ====================

class PipelineNodeIn(BaseModel):
    """节点入参（创建/更新时嵌套使用）"""
    F_JD_LX: str = Field(description="节点类型（建模阶段：预处理/原代-培养/传代-培养/复苏-培养/观察/验证；流向阶段：实验计划/实验记录/实验报告/代次管理/冻存记录/入库）")
    F_PRESET: Optional[Any] = Field(default=None, description="预设值（JSON对象，后端原样存储，不解析结构）")


class PipelineNodeOut(BaseModel):
    """节点出参"""
    F_ID: int = Field(description="节点ID")
    F_PIPELINE_ID: int = Field(description="所属流水线ID")
    F_JD_LX: str = Field(description="节点类型")
    F_PRESET: Optional[Any] = Field(default=None, description="预设值（JSON对象）")
    F_CJSJ: Optional[str] = Field(default=None, description="创建时间")
    F_GXSJ: Optional[str] = Field(default=None, description="更新时间")


# ==================== 流水线主表 Schema ====================

class PipelineCreateIn(BaseModel):
    """创建流水线入参"""
    F_MC: str = Field(description="流水线名称（必填）")
    F_AZ: Optional[str] = Field(default=None, description="癌种")
    F_YBLY: Optional[List[str]] = Field(default=None, description="样本来源，多选。仅建模阶段写入；取值与工单一致：原代制备、传代、原代复苏、传代复苏。空表示通用")
    F_JD: str = Field(description="阶段（建模 / 流向）（必填）")
    F_ZT: Optional[int] = Field(default=1, description="状态（1=启用，0=禁用），默认启用")
    F_BZ: Optional[str] = Field(default=None, description="备注")
    F_DJR: Optional[str] = Field(default=None, description="登记人（由后端从当前用户自动填入）")
    nodes: Optional[List[PipelineNodeIn]] = Field(default=None, description="节点配置列表")


class PipelineCreateOut(BaseModel):
    """创建流水线出参"""
    F_ID: int = Field(description="新建流水线ID")
    F_MC: str = Field(description="流水线名称")


class PipelineNodePatchItem(BaseModel):
    """update接口中节点更新/新增项
    - 传入 F_ID：按 ID 更新已有节点的内容（F_JD_LX / F_PRESET）
    - 不传 F_ID（或传 null）：视为新增节点，此时 F_JD_LX 必填
    """
    F_ID: Optional[int] = Field(default=None, description="节点ID；不传或传 null 时视为新增节点")
    F_JD_LX: Optional[str] = Field(default=None, description="节点类型；新增节点时必填，更新节点时可选（不传则保持原值）")
    F_PRESET: Optional[Any] = Field(default=None, description="预设值（JSON对象）；传入则整体覆盖，传 null 表示清空")


class PipelineUpdateIn(BaseModel):
    """更新流水线入参：基础信息与节点预设值可在同一请求中提交"""
    F_ID: int = Field(description="流水线ID（必填）")
    F_MC: Optional[str] = Field(default=None, description="流水线名称")
    F_AZ: Optional[str] = Field(default=None, description="癌种")
    F_YBLY: Optional[List[str]] = Field(default=None, description="样本来源，多选。仅建模阶段生效；流向阶段忽略。传空数组表示清空为通用")
    F_JD: Optional[str] = Field(default=None, description="阶段（建模 / 流向）")
    F_ZT: Optional[int] = Field(default=None, description="状态（1=启用，0=禁用）")
    F_BZ: Optional[str] = Field(default=None, description="备注")
    nodes: Optional[List[PipelineNodePatchItem]] = Field(
        default=None,
        description="需要更新预设值的节点列表；每项须带 F_ID，仅更新列表中出现的节点，其余节点不受影响"
    )
    F_NODE_DEL: Optional[List[int]] = Field(
        default=None,
        description="需要删除的节点 F_ID 数组；与 nodes 更新同步执行，先删除再更新"
    )


class PipelineUpdateOut(BaseModel):
    """更新流水线出参"""
    F_ID: int = Field(description="流水线ID")
    F_MC: str = Field(description="流水线名称")
    warning: Optional[str] = Field(default=None, description="提示信息（如模板正被工单引用，修改不影响已关联工单）")


class PipelineDetailIn(BaseModel):
    """获取流水线详情入参（F_ID 支持单个 int 或 int 数组）"""
    F_ID: Union[int, List[int]] = Field(description="流水线ID，传单个值返回对象，传数组返回列表")


class PipelineDetailOut(BaseModel):
    """流水线详情出参（含节点列表）"""
    F_ID: int
    F_MC: str = Field(description="流水线名称")
    F_AZ: Optional[str] = Field(default=None, description="癌种")
    F_YBLY: Optional[List[str]] = Field(default=None, description="样本来源，JSON 数组。流向阶段为空")
    F_JD: str = Field(description="阶段（建模 / 流向）")
    F_ZT: int = Field(description="状态（1=启用，0=禁用）")
    F_DJR: Optional[str] = Field(default=None, description="登记人")
    F_DJRQ: Optional[str] = Field(default=None, description="登记日期")
    F_GXSJ: Optional[str] = Field(default=None, description="最后更新时间")
    F_BZ: Optional[str] = Field(default=None, description="备注")
    nodes: List[PipelineNodeOut] = Field(default_factory=list, description="节点配置列表")


class PipelineListQuery(BaseModel):
    """流水线列表查询入参"""
    F_MC: Optional[str] = Field(default=None, description="流水线名称（模糊匹配）")
    F_AZ: Optional[str] = Field(default=None, description="癌种/病种。未传 F_YBLY 时模糊匹配；传入 F_YBLY 时改为整值相等，空病种仍作为通用模板")
    include_all: bool = Field(
        default=False,
        description="配合 F_AZ：false=仅同病种；true=同病种 + 病种为「全部」或空的通用模板。未传 F_AZ 或已传 F_YBLY 时无效",
    )
    F_YBLY: Optional[str] = Field(default=None, description="样本来源（单个值）。传入后按病种整值与来源数组包含关系筛选，病种或来源为空都算通用模板")
    F_JD: Optional[str] = Field(default=None, description="阶段（精确匹配：建模 / 流向）")
    F_ZT: Optional[int] = Field(default=None, description="状态（1=启用，0=禁用）")
    skip: int = Field(default=0, ge=0, description="跳过记录数")
    limit: int = Field(default=20, gt=0, description="每页条数")

    @field_validator("include_all", mode="before")
    @classmethod
    def _coerce_include_all(cls, v: Any) -> bool:
        if v is None or v == "":
            return False
        if isinstance(v, str):
            return v.strip().lower() in ("1", "true", "yes", "y")
        return bool(v)


class PipelineListItem(BaseModel):
    """流水线列表项"""
    F_ID: int
    F_MC: str = Field(description="流水线名称")
    F_AZ: Optional[str] = Field(default=None, description="癌种")
    F_YBLY: Optional[List[str]] = Field(default=None, description="样本来源。流向阶段为空")
    F_JD: str = Field(description="阶段")
    F_ZT: int = Field(description="状态（1=启用，0=禁用）")
    F_DJR: Optional[str] = Field(default=None, description="登记人")
    F_DJRQ: Optional[str] = Field(default=None, description="登记日期")
    F_GXSJ: Optional[str] = Field(default=None, description="最后更新时间")
    F_BZ: Optional[str] = Field(default=None, description="备注")
    node_count: int = Field(default=0, description="已配置节点数量")
    ref_count: int = Field(default=0, description="被培养工单引用次数（>0 表示被引用，结构变更受限）")


class PipelineListOut(BaseModel):
    """流水线列表出参"""
    total: int = Field(description="总记录数")
    data: List[PipelineListItem] = Field(description="列表数据")


class PipelineCopyIn(BaseModel):
    """复制流水线入参"""
    F_ID: int = Field(description="被复制的流水线ID")


class PipelineCopyOut(BaseModel):
    """复制流水线出参"""
    F_ID: int = Field(description="新流水线ID")
    F_MC: str = Field(description="新流水线名称")


class PipelineDeleteIn(BaseModel):
    """删除流水线入参"""
    F_ID: int = Field(description="流水线ID")


class PipelineDeleteOut(BaseModel):
    """删除流水线出参"""
    F_ID: int = Field(description="被删除的流水线ID")


class PipelineToggleStatusIn(BaseModel):
    """切换启用/禁用状态入参"""
    F_ID: int = Field(description="流水线ID")
    F_ZT: int = Field(description="目标状态（1=启用，0=禁用）")


class PipelineToggleStatusOut(BaseModel):
    """切换状态出参"""
    F_ID: int = Field(description="流水线ID")
    F_ZT: int = Field(description="当前状态")


# ==================== 节点单独操作 Schema ====================

class NodeAddIn(BaseModel):
    """向流水线追加单个节点入参"""
    F_PIPELINE_ID: int = Field(description="所属流水线ID（必填）")
    F_JD_LX: str = Field(description="节点类型（必填）")
    F_PRESET: Optional[Any] = Field(default=None, description="预设值（JSON对象，后端透明存储）")


class NodeAddOut(BaseModel):
    """追加节点出参"""
    F_ID: int = Field(description="新建节点ID")
    F_PIPELINE_ID: int = Field(description="所属流水线ID")
    F_JD_LX: str = Field(description="节点类型")


class NodeUpdateIn(BaseModel):
    """更新单个节点入参（按节点F_ID精确更新）"""
    F_ID: int = Field(description="节点ID（必填）")
    F_JD_LX: Optional[str] = Field(default=None, description="节点类型（不传则保持原值）")
    F_PRESET: Optional[Any] = Field(default=None, description="预设值（JSON对象，传入则整体覆盖该节点的预设值）")


class NodeUpdateOut(BaseModel):
    """更新节点出参"""
    F_ID: int = Field(description="节点ID")
    F_PIPELINE_ID: int = Field(description="所属流水线ID")
    F_JD_LX: str = Field(description="节点类型")
    F_PRESET: Optional[Any] = Field(default=None, description="更新后的预设值")
    F_GXSJ: Optional[str] = Field(default=None, description="更新时间")


class NodeDeleteIn(BaseModel):
    """删除单个节点入参"""
    F_ID: int = Field(description="节点ID（必填）")


class NodeDeleteOut(BaseModel):
    """删除节点出参"""
    F_ID: int = Field(description="被删除的节点ID")
    F_PIPELINE_ID: int = Field(description="所属流水线ID")
