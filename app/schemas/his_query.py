from pydantic import BaseModel, Field
from typing import Optional


class HisQueryIn(BaseModel):
    value: str = Field(..., description="查询值（门诊号/住院号/条码等）")
    field_key: Optional[str] = Field(
        default=None,
        description=(
            "查询字段键，如 f_mzh、f_zyh。"
            "传入时直接调用对应接口（兼容旧调用）；"
            "不传时由配置文件的 default_pipeline / 顺序轮询决定查询策略"
        ),
    )


class HisFieldConfig(BaseModel):
    field_key: str = Field(..., description="系统内部字段标识，全小写，用于 pipeline 引用")
    label: str = Field(..., description="显示名称")
    url: str = Field(..., description="HIS 接口地址")
    enabled: bool = Field(..., description="是否启用")
    priority: Optional[int] = Field(default=None, description="自动轮询时的调用顺序，数字越小越先调用")
    request_key: Optional[str] = Field(
        default=None,
        description="实际发送给 HIS 接口的参数名（如 HIS 要求大写则填 F_MZH）。不填则使用 field_key",
    )


class HisPipelineStep(BaseModel):
    field_key: str = Field(..., description="本步骤使用的接口，引用 field_mappings 中的 field_key")
    extract: Optional[dict[str, str]] = Field(
        default=None,
        description=(
            "仅 chain 模式使用。从本步骤响应中提取下一步的入参。"
            "key=下一步的 field_key，value=本步骤响应 data[0] 中的字段名（大小写不敏感匹配）"
        ),
    )


class HisPipeline(BaseModel):
    id: str = Field(..., description="pipeline 唯一标识，调用时通过 pipeline_id 引用")
    label: str = Field(..., description="pipeline 描述")
    mode: str = Field(..., description="查询模式：parallel_merge（并行合并）/ chain（级联）")
    steps: list[HisPipelineStep] = Field(..., description="查询步骤列表，按顺序执行")


class HisConfigOut(BaseModel):
    enabled: bool = Field(..., description="HIS 查询模块总开关")
    timeout: int = Field(..., description="请求超时时间（秒）")
    query_mode: Optional[str] = Field(default=None, description="备注用途，实际策略由 default_pipeline 决定")
    default_pipeline: Optional[str] = Field(
        default=None,
        description="默认 pipeline ID。不传 field_key 时优先走此 pipeline；留空则使用顺序轮询",
    )
    field_mappings: list[HisFieldConfig] = Field(..., description="字段与接口的映射列表")
    query_pipelines: Optional[list[HisPipeline]] = Field(default=None, description="组合/级联查询 pipeline 列表")


class HisFieldUpdateIn(BaseModel):
    field_key: str = Field(..., description="要修改的字段键，如 f_mzh")
    url: Optional[str] = Field(default=None, description="更新后的 HIS 接口地址")
    enabled: Optional[bool] = Field(default=None, description="是否启用")
    label: Optional[str] = Field(default=None, description="更新后的显示名称")
    priority: Optional[int] = Field(default=None, description="更新后的轮询优先级")
    request_key: Optional[str] = Field(default=None, description="更新后的实际请求参数名")
