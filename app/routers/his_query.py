from fastapi import APIRouter, Depends
from app.schemas.common import ApiResponse
from app.schemas.his_query import HisQueryIn, HisConfigOut, HisFieldUpdateIn
from app.core.response import ok, err
from app.core.auth import require_roles
from app.core import his_client
import httpx

router = APIRouter(prefix="/his", tags=["his_query"])

_SAMPLE_RECEIVE_ROLE = Depends(require_roles(["sample_receive"]))


@router.post(
    "/query",
    response_model=ApiResponse[dict],
    summary="从 HIS 查询患者/标本信息",
    dependencies=[_SAMPLE_RECEIVE_ROLE],
)
def query_his(payload: HisQueryIn):
    """
    从 HIS 接口查询患者或标本信息。

    **传入 `field_key`（兼容旧调用）**
    ```json
    { "value": "MZ20250001", "field_key": "f_mzh" }
    ```
    直接调用该字段对应的接口，行为与旧版一致。

    **不传 `field_key`（推荐，策略由配置文件决定）**
    ```json
    { "value": "MZ20250001" }
    ```
    后端读取 `his_config.json` 中的 `default_pipeline`：
    - 若配置了有效的 pipeline ID → 按 pipeline 执行组合/级联查询
    - 若未配置 → 按 `priority` 顺序依次轮询所有已启用接口，第一个有数据的结果立即返回

    切换查询策略只需修改配置文件，前端调用方式无需变更。
    """
    try:
        if payload.field_key:
            data = his_client.query_his(payload.field_key, payload.value)
        else:
            data = his_client.query_his_auto(payload.value)
        return ok(data, "查询成功")
    except ValueError as e:
        return err(str(e))
    except RuntimeError as e:
        return err(str(e))
    except httpx.TimeoutException:
        return err("HIS 接口请求超时，请检查网络或联系管理员")
    except httpx.HTTPStatusError as e:
        return err(f"HIS 接口返回错误状态码：{e.response.status_code}")
    except httpx.RequestError as e:
        return err(f"HIS 接口连接失败：{e}")
    except Exception as e:
        return err(f"查询失败：{e}")


@router.get(
    "/config",
    response_model=ApiResponse[HisConfigOut],
    summary="获取 HIS 接口配置",
    dependencies=[_SAMPLE_RECEIVE_ROLE],
)
def get_config():
    """读取当前 HIS 接口配置，包含所有字段映射、pipeline 定义及启用状态。"""
    try:
        config = his_client.load_his_config()
        return ok(config, "获取成功")
    except Exception as e:
        return err(f"读取配置失败：{e}")


@router.post(
    "/config/update",
    response_model=ApiResponse[dict],
    summary="更新指定字段的 HIS 接口配置",
    dependencies=[_SAMPLE_RECEIVE_ROLE],
)
def update_field_config(payload: HisFieldUpdateIn):
    """
    更新指定字段的接口 URL、启用状态、显示名称、轮询优先级或请求参数名。

    字段不存在时返回错误，不会自动新增。
    """
    try:
        config = his_client.load_his_config()
        mappings = config.get("field_mappings", [])

        target = next((m for m in mappings if m["field_key"] == payload.field_key), None)
        if target is None:
            return err(f"字段 '{payload.field_key}' 不存在于配置中")

        if payload.url is not None:
            target["url"] = payload.url
        if payload.enabled is not None:
            target["enabled"] = payload.enabled
        if payload.label is not None:
            target["label"] = payload.label
        if payload.priority is not None:
            target["priority"] = payload.priority
        if payload.request_key is not None:
            target["request_key"] = payload.request_key

        his_client.save_his_config(config)
        return ok(target, "配置已更新")
    except Exception as e:
        return err(f"更新配置失败：{e}")
