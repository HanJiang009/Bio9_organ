from __future__ import annotations

import json
import math
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional
from datetime import datetime


def _get_config_dir() -> Path:
	"""
	获取配置目录路径，支持 PyInstaller 打包后的环境
	
	统一规则：所有配置文件都从程序根目录的 config/ 读取
	- 开发环境：app/config/scoring/
	- 打包环境：Bio9_organ.exe 同级的 config/scoring/
	"""
	if getattr(sys, 'frozen', False):
		# 打包后：从可执行文件同级的 config/ 目录读取
		base_path = Path(sys.executable).parent
	else:
		# 开发环境
		base_path = Path(__file__).resolve().parents[1]
	
	return base_path / "config" / "scoring"


CFG_DIR = _get_config_dir()


def _get_by_path(root: Dict[str, Any] | None, path: str) -> Any:
	if not root:
		return None
	cur: Any = root
	for p in path.split("."):
		if cur is None:
			return None
		if isinstance(cur, dict):
			cur = cur.get(p)
		else:
			return None
	return cur


def _to_number(v: Any) -> Optional[float]:
	if v is None:
		return None
	if isinstance(v, (int, float)):
		return float(v)
	s = str(v).strip().replace("%", "")
	try:
		return float(s)
	except Exception:
		return None


def _match_enum(val: Any, rule: Dict[str, Any]) -> bool:
	m = rule.get("match")
	if m is None:
		return False
	val_s = str(val).strip().lower() if val is not None else ""
	if isinstance(m, list):
		return any(val_s == str(x).strip().lower() for x in m)
	return val_s == str(m).strip().lower()


def _cmp_num(x: float, r: Dict[str, Any]) -> bool:
	for op in ("lt", "lte", "gt", "gte", "eq"):
		if op in r:
			y = float(r[op])
			if op == "lt" and not (x < y):
				return False
			if op == "lte" and not (x <= y):
				return False
			if op == "gt" and not (x > y):
				return False
			if op == "gte" and not (x >= y):
				return False
			if op == "eq" and not math.isclose(x, y, rel_tol=1e-9, abs_tol=1e-9):
				return False
	return True


def _parse_dt(s: Any) -> Optional[datetime]:
	if s is None:
		return None
	if isinstance(s, datetime):
		return s
	ss = str(s).strip()
	for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%Y/%m/%d %H:%M:%S", "%Y/%m/%d"):
		try:
			return datetime.strptime(ss, fmt)
		except Exception:
			continue
	return None


def _format_range_label(r: Dict[str, Any]) -> str:
	# build human text like "> 50%", "10%-40%", "< 10%"
	low = None
	high = None
	if "gt" in r:
		low = (">", r["gt"])
	elif "gte" in r:
		low = (">=", r["gte"])
	if "lt" in r:
		high = ("<", r["lt"])
	elif "lte" in r:
		high = ("<=", r["lte"])
	if low and high:
		return f"{low[1]}-{high[1]}" if low[0] in (">", ">=") and high[0] in ("<", "<=") else f"{low[0]} {low[1]} & {high[0]} {high[1]}"
	if low and not high:
		return f"{low[0]} {low[1]}"
	if high and not low:
		return f"{high[0]} {high[1]}"
	if "eq" in r:
		return f"= {r['eq']}"
	return ""


def _rules_to_options(typ: str, rules: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
	opts: List[Dict[str, Any]] = []
	for r in rules or []:
		if typ == "enum":
			m = r.get("label") or r.get("match")
			if isinstance(m, list):
				label = str(m[0]) if m else ""
			else:
				label = str(m) if m is not None else ""
			opts.append({"category": label, "score": float(r.get("score") or 0)})
		elif typ == "range":
			label = r.get("label") or _format_range_label(r)
			opts.append({"category": label, "score": float(r.get("score") or 0)})
		elif typ == "expr":
			label = r.get("label") or (r.get("when") or "")
			opts.append({"category": label, "score": float(r.get("score") or 0)})
	return opts


def load_template(code_or_filename: str) -> Dict[str, Any]:
	p = CFG_DIR / (f"SCORE_{code_or_filename}.json" if not code_or_filename.lower().endswith(".json") else code_or_filename)
	with open(p, "r", encoding="utf-8") as f:
		return json.load(f)


def calc_score(template: Dict[str, Any], data: Dict[str, Any]) -> Dict[str, Any]:
	items = template.get("items", [])
	details: List[Dict[str, Any]] = []
	missing: List[str] = []

	for it in items:
		src = it.get("source")
		label = it.get("label") or it.get("key")
		typ = it.get("type")
		# weight 仅作为内部乘数，输出中不再返回
		weight = float(it.get("weight") or 1)
		default_score = float(it.get("default_score") or 0)

		raw = _get_by_path(data, src) if src else None
		hit_score: Optional[float] = None
		hit_rule: Optional[Dict[str, Any]] = None

		if typ == "enum":
			for r in it.get("rules", []):
				if _match_enum(raw, r):
					hit_score = float(r.get("score") or 0)
					hit_rule = r
					break
		elif typ == "range":
			x = _to_number(raw)
			if x is not None:
				for r in it.get("rules", []):
					if _cmp_num(x, r):
						hit_score = float(r.get("score") or 0)
						hit_rule = r
						break
		elif typ == "expr":
			for r in it.get("rules", []):
				expr = r.get("when")
				if not expr:
					continue
				try:
					env = {"data": data, "val": raw, "parse_dt": _parse_dt}
					if bool(eval(expr, {"__builtins__": {}}, env)):
						hit_score = float(r.get("score") or 0)
						hit_rule = r
						break
				except Exception:
					continue

		score = (hit_score if hit_score is not None else default_score) * weight
		if raw in (None, "", []):
			if it.get("required") is True:
				missing.append(it.get("key") or label)

		# 对于离体时间特殊处理：计算并显示小时差
		display_value = raw
		if it.get("key") == "ex_vivo_time" and typ == "expr":
			try:
				ltsj = _parse_dt(_get_by_path(data, "YCL.F_LTSJ"))
				ksclsj = _parse_dt(_get_by_path(data, "YCL.F_KSCLSJ"))
				if ltsj and ksclsj:
					hours = (ksclsj - ltsj).total_seconds() / 3600
					display_value = round(hours, 2)
					# 如果匹配到了规则，使用规则的 label
					if hit_rule:
						display_value = f"{display_value} 小时 ({hit_rule.get('label', '')})"
			except Exception:
				pass

		details.append({
			"key": it.get("key"),
			"label": label,
			"value": display_value,
			"score": score,
			"options": _rules_to_options(typ, it.get("rules", []))
		})

	total_mode = (template.get("total") or "sum").lower()
	if total_mode == "min":
		total = min((d["score"] for d in details), default=0)
	elif total_mode == "max":
		total = max((d["score"] for d in details), default=0)
	else:
		total = sum(d["score"] for d in details)

	return {
		"template_code": template.get("template_code"),
		"name": template.get("name"),
		"version": template.get("version"),
		"total": total,
		"details": details,
	}


def calc_score_by_code(template_code: str, data: Dict[str, Any]) -> Dict[str, Any]:
	tpl = load_template(template_code)
	return calc_score(tpl, data)

