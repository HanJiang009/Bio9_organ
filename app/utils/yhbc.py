"""用户保存标记 F_YHBC：0=流水线模板预填，1=用户真实保存。"""
from __future__ import annotations

from typing import Any, Optional


def yhbc_flag(from_pipeline_template: bool = False) -> int:
    """模板初始化 → 0；用户保存 → 1。"""
    return 0 if from_pipeline_template else 1


def as_bool(v: Any) -> Optional[bool]:
    """将库中的 0/1/True/False/None 转为 bool；无法识别则 None。"""
    if v is None:
        return None
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return bool(int(v))
    s = str(v).strip().lower()
    if s in ("1", "true", "yes", "y"):
        return True
    if s in ("0", "false", "no", "n", ""):
        return False
    return None
