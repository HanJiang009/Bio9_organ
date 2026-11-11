"""
快捷工位模块路由

POST /workstation/detail
  根据培养工单 F_ID，结合其关联的建模（F_PIPELINE_JM）和流向（F_PIPELINE_LX）
  流水线模板，返回模板节点配置及工单各阶段实际业务数据。
"""
from fastapi import APIRouter, Depends

from app.schemas.common import ApiResponse
from app.schemas.workstation import WorkstationDetailIn
from app.core.response import ok, err
from app.core.auth import require_roles, get_current_user_optional
from app.crud import workstation as crud

router = APIRouter(prefix="/workstation", tags=["workstation"])


@router.post(
    "/detail",
    response_model=ApiResponse[dict],
    response_model_exclude_none=False,
    dependencies=[Depends(require_roles(["organoid_culture_tissue"]))],
)
def get_workstation_detail(
    payload: WorkstationDetailIn,
    current_user: dict = Depends(get_current_user_optional),
):
    """
    获取培养工单快捷工位详情

    ## 功能说明

    - 入参工单 `F_GDID`，自动读取工单关联的 **建模流水线**（`F_PIPELINE_JM`）和
      **流向流水线**（`F_PIPELINE_LX`）
    - 若工单未关联任何模板，返回提示 `"该工单未使用流水线模板"`
    - 对每条流水线，返回两部分内容：
        1. **模板详情**：`F_MC`、`F_JD`、节点列表（含 `F_JD_LX` 和 `F_PRESET` 预设值）
        2. **阶段业务数据** `stage_data`：仅包含模板中实际配置的节点，未配置节点不返回
        3. **节点元数据**（写在 `nodes[]` 上）：
           - `node_has_data`：是否已有业务行（含流水线模板预填）
           - `node_user_saved`：是否有用户行为下的保存（模板预填为 `false`，用户调保存接口后为 `true`）
           - 定位「最新未填节点」请用 `node_user_saved === false`，不要仅看 `stage_data` 是否为 null

    ## 建模阶段节点（pipeline_jm.stage_data）

    | 节点名称 | 数据来源 |
    |---|---|
    | 预处理 | 预处理记录（T_PYMX_ZZ 预处理段） |
    | 原代-培养 / 传代-培养 | 培养明细（T_PYMX_ZZ 最新一条） |
    | 复苏-培养 | 培养明细 + 冻存摘要 DCXX（T_DCJL，逻辑同 /organoid-thaw/detail） |
    | 观察 | 观察记录列表（T_GCJL） |
    | 验证 | 质控审核记录（T_ZKSH + T_ZKJL） |

    ## 流向阶段节点（pipeline_lx.stage_data）

    | 节点名称 | 数据来源 |
    |---|---|
    | 动物实验计划 | T_DWSY_JH（按 F_GDID 关联） |
    | 动物实验记录 | T_DWSY_GCJL + T_DWSY_SYJL（按计划ID关联） |
    | 药物试验计划 | T_SYJH（按 F_GDID 关联） |
    | 药物试验记录 | T_SYJL（按计划ID关联） |
    | 代次管理 | 子代工单列表（T_PYGD.F_PARENT_ID） |
    | 冻存记录 | 冻存详情（T_DCJL，按 F_GDID 关联） |
    | 入库 | 暂不开发，固定返回 null |

    ## 返回示例

    ```json
    {
      "order": { "F_ID": 84, "F_GDBH": "...", "F_PIPELINE_JM": 3, "F_PIPELINE_LX": 7, ... },
      "pipeline_jm": {
        "F_ID": 3, "F_MC": "标准建模流水线",
        "nodes": [
          { "F_JD_LX": "预处理", "F_PRESET": { ... } },
          { "F_JD_LX": "原代-培养", "F_PRESET": { ... } }
        ],
        "stage_data": {
          "预处理": { ... },
          "原代-培养": { ... }
        }
      },
      "pipeline_lx": {
        "F_ID": 7, "F_MC": "流向A",
        "nodes": [ { "F_JD_LX": "动物实验计划", "F_PRESET": { ... } } ],
        "stage_data": {
          "动物实验计划": { ... }
        }
      }
    }
    ```
    """
    try:
        from app.core.data_scope import deny_owner_access
        data = crud.get_workstation_detail(payload.F_VALUE)
        owner = (data.get("order") or {}).get("F_SQR")
        denied = deny_owner_access(current_user, owner, module="organoid_culture_tissue")
        if denied:
            return denied
        msg = data.pop("message", "查询成功")
        return ok(data, msg)
    except ValueError as e:
        return err(str(e))
    except Exception as e:
        return err(f"查询失败: {e}")


@router.post(
    "/latest",
    response_model=ApiResponse[dict],
    response_model_exclude_none=False,
    dependencies=[Depends(require_roles(["shortcut_workstation"]))],
)
def latest_culturing_order(current_user: dict = Depends(get_current_user_optional)):
    """快捷工作台刷新：无入参，返回当前用户数据权限下最新一条培养中工单。"""
    from app.core.data_scope import resolve_data_scope

    data = crud.get_latest_culturing_order(
        current_user,
        resolve_data_scope(current_user, "organoid_culture_tissue"),
    )
    if not data:
        return err("暂无培养中工单")
    return ok(data, "查询成功")
