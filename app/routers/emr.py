from fastapi import APIRouter, Depends

from app.core import emr_client
from app.core.auth import require_roles
from app.core.response import err, ok
from app.schemas.common import ApiResponse
from app.schemas.emr import EmrConfigOut, EmrConfigUpdateIn

router = APIRouter(prefix="/emr", tags=["emr"])

_SAMPLE_RECEIVE_ROLE = Depends(require_roles(["sample_receive"]))


def _config_out(cfg: dict) -> EmrConfigOut:
    return EmrConfigOut(
        enabled=bool(cfg.get("enabled", False)),
        url_template=str(cfg.get("url_template") or ""),
        placeholders=emr_client.placeholder_items(cfg),
    )


@router.get(
    "/config",
    response_model=ApiResponse[EmrConfigOut],
    summary="获取电子病历调阅配置",
    dependencies=[_SAMPLE_RECEIVE_ROLE],
)
def get_config():
    """配置页读取：启用状态、地址模板、可选占位符清单。登记页打开病历仍用查询接口里的 JZXX.F_DZBL。"""
    try:
        cfg = emr_client.load_emr_config()
        return ok(_config_out(cfg), "获取成功")
    except Exception as e:
        return err(f"读取配置失败：{e}")


@router.post(
    "/config/update",
    response_model=ApiResponse[EmrConfigOut],
    summary="更新电子病历调阅配置",
    dependencies=[_SAMPLE_RECEIVE_ROLE],
)
def update_config(payload: EmrConfigUpdateIn):
    """
    更新启用状态和/或地址模板。只更新传入字段。

    模板一次只能包含一种占位符：`%zyh` / `%brbh` / `%sfzh` / `%mzh`。
    `placeholders` 清单只读，不能通过本接口改写。
    """
    try:
        cfg = emr_client.load_emr_config()
        if payload.enabled is not None:
            cfg["enabled"] = payload.enabled
        if payload.url_template is not None:
            tpl = payload.url_template.strip()
            if tpl:
                emr_client.resolve_template(tpl, cfg)
            cfg["url_template"] = tpl
        emr_client.save_emr_config(cfg)
        return ok(_config_out(cfg), "配置已更新")
    except ValueError as e:
        return err(str(e))
    except Exception as e:
        return err(f"更新配置失败：{e}")
