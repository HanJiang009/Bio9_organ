"""字段清洗/归一化工具：细胞数量字符串、多值字符串数组。"""
from __future__ import annotations

import json
import re
from typing import Any, List, Optional


_E_RE = re.compile(r"^([+-]?\d+(?:\.\d+)?)[eE]([+-]?\d+)$")


def coerce_xbsl_to_str(v: Any) -> Optional[str]:
    """细胞数量统一为字符串，兼容 int/float/存量数值。"""
    if v is None:
        return None
    if isinstance(v, bool):
        return str(int(v))
    if isinstance(v, int):
        return str(v)
    if isinstance(v, float):
        return str(int(v)) if v.is_integer() else str(v)
    if isinstance(v, str):
        s = v.strip()
        return s if s else None
    s = str(v).strip()
    return s if s else None


def is_blank_sci_count(v: Any) -> bool:
    """
    科学计数组件未填写：空、0、0×10^0 / 0e0 等。
    空态组件通常提交 0 或 '0'，与「填了 0」无法区分，按未填处理。
    非数字文本（如模板残留）不视为空。
    """
    if v is None or isinstance(v, bool):
        return True
    if isinstance(v, (int, float)):
        return v == 0
    s = str(v).strip()
    if not s:
        return True
    n = parse_sci_count_to_number(s)
    return n is not None and n == 0


def coerce_xbsl_empty_as_null(v: Any) -> Optional[str]:
    """细胞数量写入：未填写（含空态 0）存 NULL，有值则保留字符串。"""
    if is_blank_sci_count(v):
        return None
    return coerce_xbsl_to_str(v)


def parse_sci_count_to_number(v: Any) -> Optional[float]:
    """
    把前端科学计数法细胞数量转成数字，供仍为 DECIMAL 的列写入。
    兼容：5000、"5000"、"5*10^3"、"5 × 10^3"、"5e3"。
    """
    if v is None or isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip().replace(" ", "").replace("×", "*").replace("x", "*").replace("X", "*")
    if not s:
        return None
    m = re.match(r"^([+-]?\d+(?:\.\d+)?)\*10\^([+-]?\d+)$", s)
    if m:
        return float(m.group(1)) * (10 ** int(m.group(2)))
    m = _E_RE.match(s)
    if m:
        return float(m.group(1)) * (10 ** int(m.group(2)))
    try:
        return float(s)
    except (TypeError, ValueError):
        return None


def coerce_str_list(v: Any) -> Optional[List[str]]:
    """
    统一为字符串数组。
    兼容：list、JSON 数组字符串、逗号分隔、单一字符串（存量）。
    """
    if v is None:
        return None
    if isinstance(v, list):
        out = [str(x).strip() for x in v if x is not None and str(x).strip() != ""]
        return out if out else None
    if isinstance(v, (tuple, set)):
        return coerce_str_list(list(v))
    if isinstance(v, str):
        s = v.strip()
        if not s:
            return None
        if s.startswith("["):
            try:
                parsed = json.loads(s)
                if isinstance(parsed, list):
                    return coerce_str_list(parsed)
            except Exception:
                pass
        if "," in s:
            parts = [p.strip() for p in s.split(",") if p.strip()]
            return parts if parts else None
        return [s]
    s = str(v).strip()
    return [s] if s else None


def dump_str_list(v: Any) -> Optional[str]:
    """写入 DB：字符串数组 → JSON 字符串。"""
    arr = coerce_str_list(v)
    if arr is None:
        return None
    return json.dumps(arr, ensure_ascii=False)


def parse_str_list(v: Any) -> Optional[List[str]]:
    """读出 DB：JSON/存量单值 → 字符串数组。"""
    return coerce_str_list(v)
