"""
流水线管理模块路由
"""
from fastapi import APIRouter, Depends

from app.schemas.common import ApiResponse
from app.schemas.pipeline import (
    PipelineCreateIn,
    PipelineCreateOut,
    PipelineUpdateIn,
    PipelineUpdateOut,
    PipelineDetailIn,
    PipelineDetailOut,
    PipelineListQuery,
    PipelineListOut,
    PipelineCopyIn,
    PipelineCopyOut,
    PipelineDeleteIn,
    PipelineDeleteOut,
    PipelineToggleStatusIn,
    PipelineToggleStatusOut,
    NodeAddIn,
    NodeAddOut,
    NodeUpdateIn,
    NodeUpdateOut,
    NodeDeleteIn,
    NodeDeleteOut,
)
from app.core.response import ok, err
from app.core.auth import require_roles, get_current_user
from app.crud import pipeline as crud

router = APIRouter(prefix="/pipeline", tags=["pipeline"])


@router.post(
    "/create",
    response_model=ApiResponse[PipelineCreateOut],
    dependencies=[Depends(require_roles(["pipeline_mgmt"]))],
)
def create_pipeline(payload: PipelineCreateIn, current_user=Depends(get_current_user)):
    """
    创建流水线模板

    **功能说明：**
    - 创建一套流水线模板，包含基础信息和节点配置
    - 登记人由当前登录用户自动填入
    - nodes 为可选，不传则创建空模板（后续可通过 update 接口补充节点）

    **请求示例：**
    ```json
    {
        "F_MC": "标准建模流水线",
        "F_AZ": "肺癌",
        "F_JD": "建模",
        "F_ZT": 1,
        "F_BZ": "适用于标准肺癌类器官建模流程",
        "nodes": [
            {
                "F_JD_LX": "预处理",
                "F_PRESET": {"F_XHSJ": 30, "F_XHWD": 37}
            },
            {
                "F_JD_LX": "原代-培养",
                "F_PRESET": {"F_PYJZ": "基质胶A", "F_PYQD": "100%"}
            }
        ]
    }
    ```

    **响应示例：**
    ```json
    {
        "code": 1,
        "msg": "创建成功",
        "data": {"F_ID": 1, "F_MC": "标准建模流水线"}
    }
    ```
    """
    try:
        data = payload.model_dump()
        data["F_DJR"] = current_user.get("username") or current_user.get("F_YHM")
        result = crud.create_pipeline(data)
        return ok(PipelineCreateOut(**result), "创建成功")
    except Exception as e:
        return err(str(e))


@router.post(
    "/update",
    response_model=ApiResponse[PipelineUpdateOut],
    dependencies=[Depends(require_roles(["pipeline_mgmt"]))],
)
def update_pipeline(payload: PipelineUpdateIn):
    """
    更新流水线（基础信息 + 节点内容，一次提交）

    **功能说明：**
    - 基础信息字段（名称/癌种/阶段/状态/备注）：只更新传入的非 null 字段，未传字段保持原值
    - `nodes`（可选）：节点操作列表，每项规则如下：
        - **带 `F_ID`**：更新对应节点的 `F_JD_LX`（不传则保持原值）和 `F_PRESET`；未出现的节点不受影响
        - **不带 `F_ID`（或传 null）**：视为**新增节点**，`F_JD_LX` 此时必填
    - `F_NODE_DEL`（可选）：传入节点 F_ID 数组，批量删除指定节点
    - 执行顺序：先删除（`F_NODE_DEL`），再更新，最后新增

    **引用限制：** 若该模板已被培养工单引用，则禁止新增或删除节点（结构不可变），
    但仍可修改名称、状态、备注及已有节点内容；响应 `warning` 字段会携带相应提示。

    **请求示例（只改基础信息）：**
    ```json
    {
        "F_ID": 1,
        "F_MC": "修改后的名称",
        "F_ZT": 0
    }
    ```

    **请求示例（更新已有节点 + 新增节点）：**
    ```json
    {
        "F_ID": 1,
        "nodes": [
            {
                "F_ID": 3,
                "F_JD_LX": "预处理",
                "F_PRESET": {"F_XHSJ": 45, "F_XHWD": 37}
            },
            {
                "F_JD_LX": "复苏-培养",
                "F_PRESET": {"F_PYJZ": "基质胶B"}
            }
        ]
    }
    ```

    **请求示例（删除节点）：**
    ```json
    {
        "F_ID": 1,
        "F_NODE_DEL": [4, 6]
    }
    ```
    """
    try:
        data = payload.model_dump()
        if "F_YBLY" not in payload.model_fields_set:
            data.pop("F_YBLY", None)
        result = crud.update_pipeline(data)
        return ok(PipelineUpdateOut(**result), "更新成功")
    except ValueError as e:
        return err(str(e))
    except Exception as e:
        return err(str(e))


@router.post(
    "/detail",
    dependencies=[Depends(require_roles(["pipeline_mgmt"]))],
)
def get_pipeline_detail(payload: PipelineDetailIn):
    """
    获取流水线详情

    **功能说明：**
    - 返回流水线基础信息及全部节点配置（含预设值 JSON）
    - `F_ID` 支持单个整数或整数数组：
      - 传单个值（`{"F_ID": 1}`）→ data 为单个对象
      - 传数组（`{"F_ID": [1, 2, 3]}`）→ data 为列表，顺序与入参一致，不存在的 ID 跳过

    **单个示例：**
    ```json
    {"F_ID": 1}
    ```

    **批量示例：**
    ```json
    {"F_ID": [1, 2, 3]}
    ```
    """
    try:
        if isinstance(payload.F_ID, list):
            items = crud.get_pipeline_batch_detail(payload.F_ID)
            return ok([PipelineDetailOut(**it) for it in items])
        else:
            result = crud.get_pipeline_detail(payload.F_ID)
            return ok(PipelineDetailOut(**result))
    except ValueError as e:
        return err(str(e))
    except Exception as e:
        return err(str(e))


@router.post(
    "/list",
    response_model=ApiResponse[PipelineListOut],
    dependencies=[Depends(require_roles(["pipeline_mgmt"]))],
)
def list_pipelines(payload: PipelineListQuery):
    """
    获取流水线列表（分页）

    **功能说明：**
    - 支持按名称（模糊）、阶段（精确）、状态筛选
    - 未传 `F_YBLY` 时，癌种/病种仍按模糊匹配；`include_all` 配合 `F_AZ` 可带出病种为「全部」或空的通用模板
    - 传入 `F_YBLY`（工单的单个样本来源）时改为整值匹配，空病种、空来源都算通用模板：
      病种相等且来源数组包含该值，或两者只有一个为空且另一个命中，或两者都空
    - 未传 `F_AZ` 时 `include_all` 无效；已传 `F_YBLY` 时不再使用 `include_all`
    - 返回结果附带各模板已配置的节点数量 node_count

    **请求示例：**
    ```json
    {
        "F_AZ": "肺癌",
        "include_all": true,
        "F_JD": "建模",
        "F_ZT": 1,
        "skip": 0,
        "limit": 20
    }
    ```

    **响应示例：**
    ```json
    {
        "code": 1,
        "msg": "",
        "data": {
            "total": 5,
            "data": [
                {
                    "F_ID": 1,
                    "F_MC": "标准建模流水线",
                    "F_AZ": "肺癌",
                    "F_JD": "建模",
                    "F_ZT": 1,
                    "F_DJR": "admin",
                    "F_DJRQ": "2026-03-01 10:00:00",
                    "F_GXSJ": "2026-03-01 10:00:00",
                    "F_BZ": null,
                    "node_count": 3
                }
            ]
        }
    }
    ```
    """
    try:
        result = crud.list_pipelines(
            mc=payload.F_MC,
            az=payload.F_AZ,
            jd=payload.F_JD,
            zt=payload.F_ZT,
            skip=payload.skip,
            limit=payload.limit,
            include_all=payload.include_all,
            ybly=payload.F_YBLY,
        )
        return ok(PipelineListOut(**result))
    except Exception as e:
        return err(str(e))


@router.post(
    "/copy",
    response_model=ApiResponse[PipelineCopyOut],
    dependencies=[Depends(require_roles(["pipeline_mgmt"]))],
)
def copy_pipeline(payload: PipelineCopyIn, current_user=Depends(get_current_user)):
    """
    复制流水线模板

    **功能说明：**
    - 将指定流水线模板（含全部节点及预设值）完整复制为一份新模板
    - 新模板名称自动追加 `-{操作人}-{年月日}` 后缀，例如 `标准建模流水线-admin-20260303`
    - 新模板登记人由当前登录用户自动填入，登记日期为当前时间
    - 新模板状态与原模板保持一致

    **请求示例：**
    ```json
    {"F_ID": 1}
    ```

    **响应示例：**
    ```json
    {
        "code": 1,
        "msg": "复制成功",
        "data": {"F_ID": 8, "F_MC": "标准建模流水线-admin-20260303"}
    }
    ```
    """
    try:
        operator = current_user.get("username") or current_user.get("F_YHM")
        result = crud.copy_pipeline(payload.F_ID, operator)
        return ok(PipelineCopyOut(**result), "复制成功")
    except ValueError as e:
        return err(str(e))
    except Exception as e:
        return err(str(e))


@router.post(
    "/delete",
    response_model=ApiResponse[PipelineDeleteOut],
    dependencies=[Depends(require_roles(["pipeline_mgmt"]))],
)
def delete_pipeline(payload: PipelineDeleteIn):
    """
    删除流水线模板

    **功能说明：**
    - 删除指定流水线模板，同时级联删除其所有节点配置

    **请求示例：**
    ```json
    {"F_ID": 1}
    ```
    """
    try:
        result = crud.delete_pipeline(payload.F_ID)
        return ok(PipelineDeleteOut(**result), "删除成功")
    except ValueError as e:
        return err(str(e))
    except Exception as e:
        return err(str(e))


@router.post(
    "/toggle-status",
    response_model=ApiResponse[PipelineToggleStatusOut],
    dependencies=[Depends(require_roles(["pipeline_mgmt"]))],
)
def toggle_pipeline_status(payload: PipelineToggleStatusIn):
    """
    切换流水线启用/禁用状态

    **功能说明：**
    - 将指定流水线的状态更新为目标值（1=启用，0=禁用）

    **请求示例（禁用）：**
    ```json
    {"F_ID": 1, "F_ZT": 0}
    ```

    **请求示例（启用）：**
    ```json
    {"F_ID": 1, "F_ZT": 1}
    ```
    """
    try:
        result = crud.toggle_pipeline_status(payload.F_ID, payload.F_ZT)
        msg = "已启用" if payload.F_ZT == 1 else "已禁用"
        return ok(PipelineToggleStatusOut(**result), msg)
    except ValueError as e:
        return err(str(e))
    except Exception as e:
        return err(str(e))


# ==================== 节点单独操作接口 ====================

@router.post(
    "/node/add",
    response_model=ApiResponse[NodeAddOut],
    dependencies=[Depends(require_roles(["pipeline_mgmt"]))],
)
def add_node(payload: NodeAddIn):
    """
    向流水线追加单个节点

    **功能说明：**
    - 在指定流水线下新增一个节点及其预设值
    - 节点顺序由前端根据业务规则自行维护，后端按 F_ID 升序返回

    **请求示例：**
    ```json
    {
        "F_PIPELINE_ID": 1,
        "F_JD_LX": "复苏-培养",
        "F_PRESET": {"F_PYJZ": "基质胶B", "F_TRSJ": 20}
    }
    ```

    **响应示例：**
    ```json
    {
        "code": 1,
        "msg": "节点添加成功",
        "data": {"F_ID": 5, "F_PIPELINE_ID": 1, "F_JD_LX": "复苏-培养"}
    }
    ```
    """
    try:
        result = crud.add_node(payload.model_dump())
        return ok(NodeAddOut(**result), "节点添加成功")
    except ValueError as e:
        return err(str(e))
    except Exception as e:
        return err(str(e))


@router.post(
    "/node/update",
    response_model=ApiResponse[NodeUpdateOut],
    dependencies=[Depends(require_roles(["pipeline_mgmt"]))],
)
def update_node(payload: NodeUpdateIn):
    """
    更新单个节点内容

    **功能说明：**
    - 按节点 `F_ID` 精确更新指定节点的类型（`F_JD_LX`）和/或预设值（`F_PRESET`）
    - `F_JD_LX` 不传则保持原节点类型不变
    - `F_PRESET` 传入（包括传 `null`）则整体覆盖该节点的预设值；不传该字段则保持原预设值不变
    - 返回更新后的完整节点信息

    **请求示例（只修改预设值中的部分字段，需前端合并后整体传入）：**
    ```json
    {
        "F_ID": 3,
        "F_PRESET": {
            "F_XHSJ": 45,
            "F_XHWD": 37,
            "F_YJFS": "胰蛋白酶"
        }
    }
    ```

    **请求示例（清空预设值）：**
    ```json
    {
        "F_ID": 3,
        "F_PRESET": null
    }
    ```

    **响应示例：**
    ```json
    {
        "code": 1,
        "msg": "节点更新成功",
        "data": {
            "F_ID": 3,
            "F_PIPELINE_ID": 1,
            "F_JD_LX": "预处理",
            "F_PRESET": {"F_XHSJ": 45, "F_XHWD": 37, "F_YJFS": "胰蛋白酶"},
            "F_GXSJ": "2026-03-03 14:00:00"
        }
    }
    ```
    """
    try:
        # 使用 model_dump 并保留显式传入的 null（exclude_unset=True 区分"未传"与"传了null"）
        data = payload.model_dump(exclude_unset=False)
        data["F_ID"] = payload.F_ID
        # 只将实际传入的字段写入 data，未传字段不覆盖
        submitted = payload.model_dump(exclude_unset=True)
        submitted["F_ID"] = payload.F_ID
        result = crud.update_node(submitted)
        return ok(NodeUpdateOut(**result), "节点更新成功")
    except ValueError as e:
        return err(str(e))
    except Exception as e:
        return err(str(e))


@router.post(
    "/node/delete",
    response_model=ApiResponse[NodeDeleteOut],
    dependencies=[Depends(require_roles(["pipeline_mgmt"]))],
)
def delete_node(payload: NodeDeleteIn):
    """
    删除单个节点

    **功能说明：**
    - 按节点 `F_ID` 删除指定节点，不影响同一流水线的其他节点

    **请求示例：**
    ```json
    {"F_ID": 3}
    ```

    **响应示例：**
    ```json
    {
        "code": 1,
        "msg": "节点删除成功",
        "data": {"F_ID": 3, "F_PIPELINE_ID": 1}
    }
    ```
    """
    try:
        result = crud.delete_node(payload.F_ID)
        return ok(NodeDeleteOut(**result), "节点删除成功")
    except ValueError as e:
        return err(str(e))
    except Exception as e:
        return err(str(e))
