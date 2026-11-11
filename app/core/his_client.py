"""HIS 接口调用核心模块

负责：
- 加载并缓存 his_config.json（兼容打包环境与开发环境）
- 根据字段 key 查找对应的 HIS 接口 URL
- 发起 POST 请求并返回原始响应数据
- 提供配置的读取与更新写入能力
- mock_mode 为 true 时返回固定模拟数据，无需真实 HIS 环境

查询模式说明：
- query_his(field_key, value)      : 指定字段查询，向后兼容旧调用
- query_his_auto(value)            : 不指定字段，按 priority 顺序轮询所有接口，查到即返回
- query_his_pipeline(id, value)    : 按 query_pipelines 配置执行组合/级联查询
"""
import json
import logging
import sys
import httpx
from pathlib import Path
from typing import Any

_logger = logging.getLogger(__name__)

_MOCK_RECORD = {
    "F_BBLX": "体液",
    "F_BBLY": "门诊",
    "F_BM": "测试病名",
    "F_BRBH": "P20250001",
    "F_CHH": "12",
    "F_CRB": "否",
    "F_CXSJ": "2025-07-20 08:30:00",
    "F_CYBC": "采样病程描述",
    "F_DH": "13800138000",
    "F_DZ": "测试地址",
    "F_F1": "",
    "F_F2": "",
    "F_F3": "",
    "F_F4": "",
    "F_F5": "",
    "F_HY": "已婚",
    "F_JZRQ": "2025-07-20",
    "F_LCZD": "结肠癌",
    "F_MZ": "汉族",
    "F_MZH": "MZ20250001",
    "F_NL": 45,
    "F_CSRQ": "1980-01-01",
    "F_SFZH": "110101198001010011",
    "F_SGTM": "SG20250001",
    "F_SQXH": "SQ001",
    "F_SYXH": "SY001",
    "F_XB": "男",
    "F_XM": "测试患者",
    "F_XMBH": "XM001",
    "F_ZY": "工人",
    "F_ZYCS": 1,
    "F_ZYH": "ZY20250001",
}

_MOCK_RESPONSE = {
    "code": 200,
    "data": [_MOCK_RECORD],
    "message": "success",
    "success": True,
}


def _get_config_path() -> Path:
    """获取 his_config.json 的绝对路径，兼容打包环境与开发环境。"""
    if getattr(sys, "frozen", False):
        base_dir = Path(sys.executable).parent
    else:
        # app/core/his_client.py -> parents[1] 是 app 目录
        base_dir = Path(__file__).resolve().parents[1]
    return base_dir / "config" / "his_config.json"


def load_his_config() -> dict:
    """读取 his_config.json，返回原始字典。文件不存在则返回默认空配置。"""
    path = _get_config_path()
    if not path.exists():
        return {"enabled": False, "timeout": 10, "field_mappings": [], "query_pipelines": []}
    with path.open('r', encoding='utf-8-sig') as f:
        return json.load(f)


def get_field_mapping(config: dict) -> dict[str, dict]:
    """将 field_mappings 列表转换为以 field_key 为键的字典，便于快速查找。"""
    return {
        item["field_key"]: item
        for item in config.get("field_mappings", [])
        if item.get("enabled", True)
    }


def save_his_config(config: dict) -> None:
    """将配置字典写回 his_config.json。"""
    path = _get_config_path()
    path.write_text(
        json.dumps(config, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )


def _get_field_ci(record: dict, field_name: str) -> tuple[bool, Any]:
    """
    大小写不敏感地从 record 中查找字段值。
    返回 (found: bool, value: Any)，优先精确匹配，其次按 lower() 对齐。
    """
    if field_name in record:
        return True, record[field_name]
    field_lower = field_name.lower()
    for k, v in record.items():
        if k.lower() == field_lower:
            return True, v
    return False, None


def _has_data(result: Any) -> bool:
    """判断 HIS 响应是否包含有效数据记录。"""
    if not isinstance(result, dict):
        return False
    data = result.get("data")
    return bool(data)


def _call_his(mapping: dict, value: str, timeout: int) -> Any:
    """
    内部统一发起 HIS POST 请求。
    使用 mapping 中的 request_key（若配置）作为请求参数名，否则回退到 field_key。
    """
    url = mapping["url"]
    req_key = mapping.get("request_key") or mapping["field_key"]
    response = httpx.post(url, json={req_key: value}, timeout=timeout)
    response.raise_for_status()
    return response.json()


def query_his(field_key: str, value: str) -> Any:
    """
    根据 field_key 查找对应的 HIS 接口并发起 POST 请求（向后兼容接口）。

    当 his_config.json 中 mock_mode 为 true 时，跳过真实请求，直接返回模拟数据。

    :param field_key: 查询字段键，如 "f_mzh"
    :param value:     查询值，如 "124455"
    :return:          HIS 接口返回的原始 JSON 数据
    :raises ValueError:   字段未配置或已禁用
    :raises RuntimeError: HIS 模块未启用
    :raises httpx.TimeoutException: 请求超时
    :raises httpx.RequestError:     网络连接失败
    """
    config = load_his_config()

    if not config.get("enabled", False):
        raise RuntimeError("HIS 查询模块未启用，请在 his_config.json 中将 enabled 设为 true")

    mapping = get_field_mapping(config)
    if field_key not in mapping:
        raise ValueError(f"字段 '{field_key}' 未在配置中找到或已禁用")

    if config.get("mock_mode", False):
        return _MOCK_RESPONSE

    timeout = config.get("timeout", 10)
    return _call_his(mapping[field_key], value, timeout)


def query_his_auto(value: str) -> Any:
    """
    不指定 field_key 时的统一入口，查询策略完全由配置文件决定：

    - 若配置了非空的 default_pipeline → 委托给 query_his_pipeline 执行组合/级联查询
    - 否则 → 按 priority 顺序循环调用所有已启用接口，第一个返回有效数据的结果立即返回

    前端无需感知当前客户使用哪种策略，切换策略只需修改配置文件。

    :param value: 查询值（门诊号/住院号/条码等，无需区分类型）
    :return:      HIS 响应字典
    :raises RuntimeError: HIS 模块未启用
    """
    config = load_his_config()

    if not config.get("enabled", False):
        raise RuntimeError("HIS 查询模块未启用，请在 his_config.json 中将 enabled 设为 true")

    # 优先走配置指定的 pipeline（组合/级联场景）
    default_pipeline = (config.get("default_pipeline") or "").strip()
    if default_pipeline:
        return query_his_pipeline(default_pipeline, value)

    # 无 pipeline 配置，走顺序轮询
    if config.get("mock_mode", False):
        return _MOCK_RESPONSE

    enabled_mappings = sorted(
        [m for m in config.get("field_mappings", []) if m.get("enabled", True)],
        key=lambda m: m.get("priority", 999)
    )
    timeout = config.get("timeout", 10)

    for m in enabled_mappings:
        try:
            result = _call_his(m, value, timeout)
            if _has_data(result):
                return result
            _logger.info(
                "[HIS auto] %s (%s) 返回空数据，继续下一个接口",
                m["label"], m["url"],
            )
        except httpx.HTTPStatusError as e:
            _logger.warning(
                "[HIS auto] %s (%s) 返回 HTTP %s，请检查 request_key 配置是否与 HIS 接口要求一致",
                m["label"], m["url"], e.response.status_code,
            )
        except httpx.TimeoutException:
            _logger.warning("[HIS auto] %s (%s) 请求超时", m["label"], m["url"])
        except Exception as e:
            _logger.warning("[HIS auto] %s (%s) 请求异常: %s", m["label"], m["url"], e)

    return {
        "code": 404,
        "data": [],
        "message": "所有接口均未查到匹配数据",
        "success": False,
    }


def query_his_pipeline(pipeline_id: str, value: str) -> Any:
    """
    按 query_pipelines 中定义的流程执行组合或级联查询。

    支持两种模式：
    - parallel_merge：并行调用多个接口，结果合并（后步覆盖前步同名字段）
    - chain：级联查询，每步从上一步结果中提取字段值作为下一步入参

    字段提取（chain 模式）采用大小写不敏感匹配，兼容 HIS 返回字段名大小写不一致的情况。

    :param pipeline_id: query_pipelines 中的 id 字段
    :param value:       初始查询值
    :return:            合并/级联后的完整响应
    :raises ValueError:   pipeline 未找到、步骤字段未配置、级联提取失败
    :raises RuntimeError: HIS 模块未启用
    """
    config = load_his_config()

    if not config.get("enabled", False):
        raise RuntimeError("HIS 查询模块未启用，请在 his_config.json 中将 enabled 设为 true")

    pipeline = next(
        (p for p in config.get("query_pipelines", []) if p["id"] == pipeline_id),
        None,
    )
    if not pipeline:
        raise ValueError(f"Pipeline '{pipeline_id}' 未在配置中找到，请检查 his_config.json")

    if config.get("mock_mode", False):
        return _MOCK_RESPONSE

    field_mapping = get_field_mapping(config)
    timeout = config.get("timeout", 10)
    mode = pipeline.get("mode", "parallel_merge")
    steps = pipeline.get("steps", [])

    if not steps:
        raise ValueError(f"Pipeline '{pipeline_id}' 的 steps 为空")

    # ── 并行合并模式 ────────────────────────────────────────────────────────────
    if mode == "parallel_merge":
        merged: dict = {}
        any_success = False
        for step in steps:
            fk = step.get("field_key", "")
            if fk not in field_mapping:
                continue
            try:
                result = _call_his(field_mapping[fk], value, timeout)
                data_list = result.get("data") or []
                if data_list and isinstance(data_list[0], dict):
                    merged.update(data_list[0])
                    any_success = True
            except Exception:
                continue

        if any_success:
            return {"code": 200, "data": [merged], "message": "success", "success": True}
        return {"code": 404, "data": [], "message": "所有接口均未查到匹配数据", "success": False}

    # ── 级联查询模式 ────────────────────────────────────────────────────────────
    if mode == "chain":
        current_value = value
        accumulated: dict = {}

        for i, step in enumerate(steps):
            fk = step.get("field_key", "")
            if fk not in field_mapping:
                raise ValueError(
                    f"Pipeline '{pipeline_id}' 第 {i + 1} 步的 field_key '{fk}' 未在配置中找到或已禁用"
                )

            try:
                result = _call_his(field_mapping[fk], current_value, timeout)
            except httpx.TimeoutException:
                return {
                    "code": 504,
                    "data": [],
                    "message": f"第 {i + 1} 步（{field_mapping[fk]['label']}）接口请求超时",
                    "success": False,
                }
            except httpx.RequestError as e:
                return {
                    "code": 502,
                    "data": [],
                    "message": f"第 {i + 1} 步（{field_mapping[fk]['label']}）接口连接失败：{e}",
                    "success": False,
                }

            data_list = result.get("data") or []
            if not data_list:
                return {
                    "code": 404,
                    "data": [],
                    "message": f"第 {i + 1} 步（{field_mapping[fk]['label']}）未查到数据，级联中断",
                    "success": False,
                }

            record: dict = data_list[0] if isinstance(data_list[0], dict) else {}
            accumulated.update(record)

            # 若有 extract 且还有后续步骤，从当前结果中提取下一步的入参值
            extract: dict = step.get("extract", {})
            if extract and i + 1 < len(steps):
                next_fk = steps[i + 1].get("field_key", "")
                src_field = extract.get(next_fk)
                if not src_field:
                    raise ValueError(
                        f"Pipeline '{pipeline_id}' 第 {i + 1} 步的 extract 中未定义 '{next_fk}' 的来源字段"
                    )
                found, extracted_val = _get_field_ci(record, src_field)
                if not found or extracted_val is None or str(extracted_val).strip() == "":
                    return {
                        "code": 404,
                        "data": [],
                        "message": (
                            f"第 {i + 1} 步结果中未找到字段 '{src_field}'（大小写不敏感匹配均失败），"
                            f"无法继续级联至第 {i + 2} 步"
                        ),
                        "success": False,
                    }
                current_value = str(extracted_val).strip()

        return {"code": 200, "data": [accumulated], "message": "success", "success": True}

    raise ValueError(f"Pipeline '{pipeline_id}' 使用了不支持的 mode: '{mode}'，仅支持 parallel_merge / chain")
