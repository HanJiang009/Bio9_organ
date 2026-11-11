"""
流水线进度展示模块路由

POST /pipeline-progress/orders/list
  以培养工单为维度，返回各阶段（登记/分发/培养/质控/传代/入库）完成状态。
  支持按培养人(SQR)、癌种(AZ)、病人编号(BRBH)三种维度聚合分组展示。
"""
from fastapi import APIRouter, Depends
from app.schemas.common import ApiResponse
from app.schemas.pipeline_progress import PipelineProgressQuery
from app.core.response import ok, err
from app.core.auth import require_roles, get_current_user_optional
from app.crud import pipeline_progress as crud

router = APIRouter(prefix="/pipeline-progress", tags=["pipeline_progress"])


@router.post(
    "/orders/list",
    response_model=ApiResponse[dict],
    response_model_exclude_none=False,
    dependencies=[Depends(require_roles(["organoid_culture_tissue"]))],
)
def pipeline_progress_list(
    payload: PipelineProgressQuery,
    current_user: dict = Depends(get_current_user_optional),
):
    """
    培养工单流水线进度列表。

    **过滤参数**（可组合）：
    - F_SQR：培养人用户 ID（`T_USER.F_ID` 数组多选，如 `[7, 12]`）
    - F_AZ：癌种（模糊匹配）
    - F_BRBH：病人编号（模糊匹配）
    - F_ZZID：标本号（模糊匹配）
    - F_GDBH：工单编号（模糊匹配）
    - F_YBLY：样本来源（数组多选精确匹配，如 `["原代制备", "传代"]`）
    - F_PYSJ_START / F_PYSJ_END：开始培养日期范围（含端点）

    **分组参数** `group_by`（可选）：
    - `"SQR"` → 按培养人分组
    - `"AZ"` → 按癌种分组
    - `"BRBH"` → 按病人编号分组
    - 不传 → 返回 `{ total, items }`，`skip`/`limit` 作用于行
    - 传入时 → 返回 `{ total, groups }`，`skip`/`limit` 仍作用于行；SQL 按分组键排序使同组记录连续，同一分组不会被跨页截断（单组超过 `limit` 行除外，那是合理截断）

    **各阶段状态字段**（`stage_djjs` / `stage_ff` / `stage_py` / `stage_zk` / `stage_cd` / `stage_rk`）：
    ```
    {
      "status": "已完成|进行中|不合格|审核通过|审核不通过|null",
      "status_display": "已完成|进行中|未开始|异常",
      "time": "YYYY-MM-DD HH:MM:SS|null"
    }
    ```
    `status_display` 映射规则：审核通过→已完成；审核不通过/不合格→异常；null→未开始。

    **入库阶段 `stage_rk` 统计范围**：仅统计 `T_YBFZ.F_PYGD_ID` 关联到该培养工单的分装入库（-GD- 类）；
    标本直接分装入库（`F_PYGD_ID` 为空、-BB- 类）不参与统计，对应工单 `stage_rk` 保持未开始。
    """
    try:
        from app.core.data_scope import resolve_data_scope
        data = crud.list_pipeline_progress(
            payload.model_dump(),
            user=current_user,
            scope=resolve_data_scope(current_user, "organoid_culture_tissue"),
        )

        return ok(data, f"查询成功，共 {data.get('total', 0)} 条")
    except Exception as e:
        return err(f"查询失败: {e}")
