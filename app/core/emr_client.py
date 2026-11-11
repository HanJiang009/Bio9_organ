"""电子病历调阅：按配置模板把患者号替换进 URL。

占位符清单在 emr_config.json 的 placeholders 中维护。
一条模板只允许出现其中一种；出现多种则拦截。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import quote


DEFAULT_PLACEHOLDERS: List[Dict[str, str]] = [
    {"token": "%zyh", "field": "F_ZYH", "label": "住院号", "example": "http://emr.example.com/view?zyh=%zyh"},
    {"token": "%brbh", "field": "F_BRBH", "label": "病人编号", "example": "http://emr.example.com/view?brbh=%brbh"},
    {"token": "%sfzh", "field": "F_SFZH", "label": "身份证号", "example": "http://www.baidu.com/%sfzh"},
    {"token": "%mzh", "field": "F_MZH", "label": "门诊号", "example": "http://emr.example.com/view?mzh=%mzh"},
]


def _get_config_path() -> Path:
    if getattr(sys, "frozen", False):
        base_dir = Path(sys.executable).parent
    else:
        base_dir = Path(__file__).resolve().parents[1]
    return base_dir / "config" / "emr_config.json"


def load_emr_config() -> dict:
    path = _get_config_path()
    if not path.exists():
        return {"enabled": False, "url_template": "", "placeholders": list(DEFAULT_PLACEHOLDERS)}
    with path.open("r", encoding="utf-8-sig") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        return {"enabled": False, "url_template": "", "placeholders": list(DEFAULT_PLACEHOLDERS)}
    if not data.get("placeholders"):
        data["placeholders"] = list(DEFAULT_PLACEHOLDERS)
    return data


def save_emr_config(config: dict) -> None:
    path = _get_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    if not config.get("placeholders"):
        config["placeholders"] = list(DEFAULT_PLACEHOLDERS)
    path.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")


def placeholder_items(config: Optional[dict] = None) -> List[Dict[str, str]]:
    cfg = config if config is not None else load_emr_config()
    items = cfg.get("placeholders") or DEFAULT_PLACEHOLDERS
    out: List[Dict[str, str]] = []
    for it in items:
        token = str(it.get("token") or "").strip()
        field = str(it.get("field") or "").strip()
        if not token or not field:
            continue
        out.append({
            "token": token,
            "field": field,
            "label": str(it.get("label") or field),
            "example": str(it.get("example") or ""),
        })
    return out or list(DEFAULT_PLACEHOLDERS)


def _placeholder_map(config: Optional[dict] = None) -> Dict[str, Dict[str, str]]:
    return {it["token"]: it for it in placeholder_items(config)}


def find_placeholders(template: str, config: Optional[dict] = None) -> List[str]:
    """返回模板中出现过的占位符（去重，长 token 优先）。"""
    mapping = _placeholder_map(config)
    tokens = sorted(mapping.keys(), key=len, reverse=True)
    found: List[str] = []
    for token in tokens:
        if token in template and token not in found:
            found.append(token)
    return found


def _clean(v: Any) -> Optional[str]:
    if v is None:
        return None
    s = str(v).strip()
    return s if s else None


def resolve_template(template: str, config: Optional[dict] = None) -> Tuple[str, str, str]:
    """
    校验模板并返回 (token, field, label)。
    未配占位符、或同时出现多种字段时抛 ValueError。
    """
    cfg = config if config is not None else load_emr_config()
    mapping = _placeholder_map(cfg)
    tpl = (template or "").strip()
    if not tpl:
        raise ValueError("未配置电子病历地址")
    found = find_placeholders(tpl, cfg)
    if not found:
        allowed = " / ".join(mapping.keys())
        raise ValueError(f"电子病历地址未配置有效占位符（{allowed}）")
    if len(found) > 1:
        labels = [mapping[t]["label"] for t in found]
        raise ValueError(f"电子病历地址只能配置一种查询字段，当前同时包含：{'、'.join(labels)}")
    token = found[0]
    meta = mapping[token]
    return token, meta["field"], meta["label"]


def build_emr_url(patient: Dict[str, Any], template: Optional[str] = None) -> Dict[str, str]:
    """
    用患者字段替换模板中的唯一占位符，生成可打开的 URL。
    """
    config = load_emr_config()
    if not config.get("enabled", False):
        raise RuntimeError("电子病历调阅未启用")
    tpl = template if template is not None else (config.get("url_template") or "")
    token, field, label = resolve_template(tpl, config)
    value = _clean(patient.get(field))
    if not value:
        raise ValueError(f"缺少{label}，无法打开电子病历")
    url = tpl.replace(token, quote(value, safe=""))
    return {
        "url": url,
        "placeholder": token,
        "field": field,
        "label": label,
    }


def try_build_emr_url(patient: Dict[str, Any], config: Optional[dict] = None) -> Optional[str]:
    """登记接收出参用：拼不出地址时返回 None，不打断主查询。"""
    try:
        cfg = config if config is not None else load_emr_config()
        if not cfg.get("enabled", False):
            return None
        tpl = cfg.get("url_template") or ""
        token, field, _label = resolve_template(tpl, cfg)
        value = _clean(patient.get(field))
        if not value:
            return None
        return tpl.replace(token, quote(value, safe=""))
    except Exception:
        return None
