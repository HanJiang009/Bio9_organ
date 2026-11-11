"""出库单推送客户端

负责在本系统发起（或再次发起）出库单成功后，把出库单信息主动推送给生物样本库系统。

设计与 app/core/his_client.py 的 mock_mode 思路保持一致：
- 加载并缓存 outbound_push_config.json（兼容打包环境与开发环境）
- enabled=false：完全跳过推送（不发请求），仅记录"本应发送的报文"到日志
- mock_mode=true：不发起真实 HTTP 请求，仅记录请求报文并返回固定模拟响应
  （用于本地联调阶段，在没有对方真实接口地址时，先把请求格式定下来给工程师参考）
- mock_mode=false：向 url 发起真实 POST 请求

无论哪种模式，完整的请求报文都会以 INFO 级别记录到 outbound_push 日志（logs/app.log），
方便直接从日志复制报文内容提供给外部系统工程师。
"""
import json
import logging
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict

import httpx

logger = logging.getLogger("outbound_push")

_DEFAULT_CONFIG = {"enabled": False, "mock_mode": True, "timeout": 8, "url": ""}


def _get_config_path() -> Path:
    """获取 outbound_push_config.json 的绝对路径，兼容打包环境与开发环境。"""
    if getattr(sys, "frozen", False):
        base_dir = Path(sys.executable).parent
    else:
        # app/core/outbound_push_client.py -> parents[1] 是 app 目录
        base_dir = Path(__file__).resolve().parents[1]
    return base_dir / "config" / "outbound_push_config.json"


def load_outbound_push_config() -> dict:
    """读取 outbound_push_config.json，返回原始字典。文件不存在则返回默认关闭配置。"""
    path = _get_config_path()
    if not path.exists():
        return dict(_DEFAULT_CONFIG)
    with path.open("r", encoding="utf-8-sig") as f:
        return json.load(f)


def save_outbound_push_config(config: dict) -> None:
    """将配置字典写回 outbound_push_config.json。"""
    path = _get_config_path()
    path.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")


def build_outbound_push_payload(order: Dict[str, Any]) -> Dict[str, Any]:
    """根据 create_outbound/resubmit_outbound 的返回结果，组装推送给生物样本库系统的出库单通知报文。

    报文风格与生物样本库系统自身推送给本系统的入库/出库回执保持一致
    （{code,msg,pushTime,data:{task,...}}，字段小写 f_ 前缀），便于对方复用同一套解析逻辑。
    """
    items = order.get("items") or []
    ybmx = [{"f_sgbh": it.get("F_SGBH")} for it in items if it.get("F_SGBH")]
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
    return {
        "code": 0,
        "msg": "新出库单通知",
        "pushTime": now_str,
        "data": {
            "task": "样本出库申请",
            "f_ckdh": order.get("F_CKDH"),
            "f_sqr": order.get("F_SQR"),
            "f_sqsj": order.get("F_SQSJ"),
            "f_lxfx": order.get("F_LXFX"),
            "f_bz": order.get("F_BZ"),
            "ybmx": ybmx,
        },
    }


def push_outbound_order(order: Dict[str, Any]) -> Dict[str, Any]:
    """推送出库单信息给生物样本库系统（best-effort，不抛异常，不影响出库单创建主流程）。

    返回结构：
    {
        "attempted": bool,   # 是否实际尝试了推送请求（enabled=false 时为 False）
        "mock": bool,        # 是否为模拟推送（未真实发起网络请求）
        "success": bool,
        "url": str,
        "request": dict,     # 完整请求报文，无论成功/模拟/关闭均返回，便于核对格式
        "response": dict | None,
        "error": str | None,
    }
    """
    try:
        config = load_outbound_push_config()
    except Exception as exc:
        logger.warning("[outbound_push] 读取配置失败: %s", exc)
        config = dict(_DEFAULT_CONFIG)

    payload = build_outbound_push_payload(order)
    request_text = json.dumps(payload, ensure_ascii=False)

    if not config.get("enabled", False):
        logger.info(
            "[outbound_push] 推送未启用（enabled=false），跳过真实/模拟请求。"
            "若需推送请在 app/config/outbound_push_config.json 中设置 enabled=true。"
            "本应发送的报文: %s",
            request_text,
        )
        return {
            "attempted": False, "mock": False, "success": False,
            "url": config.get("url"), "request": payload, "response": None,
            "error": "推送未启用",
        }

    url = config.get("url") or ""
    mock_mode = bool(config.get("mock_mode", True))

    logger.info(
        "[outbound_push] 准备推送出库单 F_CKDH=%s url=%s mock_mode=%s request=%s",
        order.get("F_CKDH"), url, mock_mode, request_text,
    )

    if mock_mode:
        mock_response = {
            "code": 0,
            "msg": "请求已接收（MOCK，未发起真实请求）",
            "recvTime": datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3],
        }
        logger.info(
            "[outbound_push] MOCK 模式，不发起真实请求。模拟响应: %s",
            json.dumps(mock_response, ensure_ascii=False),
        )
        return {
            "attempted": True, "mock": True, "success": True,
            "url": url, "request": payload, "response": mock_response, "error": None,
        }

    if not url:
        logger.warning("[outbound_push] mock_mode=false 但 url 为空，无法发起真实请求")
        return {
            "attempted": True, "mock": False, "success": False,
            "url": url, "request": payload, "response": None, "error": "未配置 url",
        }

    timeout = config.get("timeout", 8)
    try:
        resp = httpx.post(url, json=payload, timeout=timeout)
        resp.raise_for_status()
        try:
            resp_json = resp.json()
        except Exception:
            resp_json = {"raw_text": resp.text}
        logger.info(
            "[outbound_push] 推送成功 F_CKDH=%s status=%s response=%s",
            order.get("F_CKDH"), resp.status_code, json.dumps(resp_json, ensure_ascii=False),
        )
        return {
            "attempted": True, "mock": False, "success": True,
            "url": url, "request": payload, "response": resp_json, "error": None,
        }
    except Exception as exc:
        logger.warning("[outbound_push] 推送失败 F_CKDH=%s url=%s error=%s", order.get("F_CKDH"), url, exc)
        return {
            "attempted": True, "mock": False, "success": False,
            "url": url, "request": payload, "response": None, "error": str(exc),
        }
