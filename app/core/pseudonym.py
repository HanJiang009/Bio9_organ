"""按当前会话角色的伪名化配置改响应，不改库。配置存在 T_JS.F_WSMH。"""
from __future__ import annotations

import json
import logging
import re
from contextvars import ContextVar
from typing import Any, Dict, List, Optional

NAME_KEYS = {
    "F_XM", "F_KTYPE", "F_SQR", "F_DJR", "F_CZR", "F_PYRY_NAME", "F_HZXM",
    "F_YHXM", "F_HZ",
}
ZYH_KEYS = {"F_ZYH"}
MZH_KEYS = {"F_MZH"}
BRBH_KEYS = {"F_BRBH"}
PHONE_KEYS = {"F_DH", "F_LXDH"}
ADDRESS_KEYS = {"F_DZ"}
ID_KEYS = {"F_SFZH"}

# 勾选项目。旧配置没有 fields 时，姓名/地址/电话/身份证保持开启，新增三项默认不勾。
FIELD_SPECS = (
    ("name", "姓名", NAME_KEYS, True),
    ("zyh", "住院号", ZYH_KEYS, False),
    ("mzh", "门诊号", MZH_KEYS, False),
    ("brbh", "病人编号", BRBH_KEYS, False),
    ("address", "地址", ADDRESS_KEYS, True),
    ("phone", "电话", PHONE_KEYS, True),
    ("id_card", "身份证号", ID_KEYS, True),
)
FIELD_KEYS = tuple(item[0] for item in FIELD_SPECS)
_FIELD_BY_KEY = {item[0]: item for item in FIELD_SPECS}
_KEY_TO_FIELD = {col: item[0] for item in FIELD_SPECS for col in item[2]}
_BOOL_TRUE = {"1", "true", "yes", "y", "on", "是", "开", "开启"}

logger = logging.getLogger(__name__)

_COMPOUND_SURNAMES = (
    "欧阳", "太史", "端木", "上官", "司马", "东方", "独孤", "南宫",
    "万俟", "闻人", "夏侯", "诸葛", "尉迟", "公羊", "赫连", "澹台",
    "皇甫", "宗政", "濮阳", "公冶", "太叔", "申屠", "公孙", "慕容",
    "仲孙", "钟离", "长孙", "宇文", "司徒", "鲜于", "司空", "闾丘",
    "子车", "亓官", "司寇", "巫马", "公西", "颛孙", "壤驷", "公良",
    "漆雕", "乐正", "宰父", "谷梁", "拓跋", "夹谷", "轩辕", "令狐",
    "段干", "百里", "呼延", "东郭", "南门", "羊舌", "微生", "公户",
    "公玉", "公仪", "梁丘", "公仲", "公上", "公门", "公山", "公坚",
    "左丘", "公伯", "西门", "公祖", "第五", "公乘", "贯丘", "公皙",
    "南荣", "东里", "东宫", "仲长", "子书", "子桑", "即墨", "达奚",
    "褚师", "吴铭",
)

# 请求内生效的角色配置。未绑定（脚本、无登录）时不脱敏。
_ctx: ContextVar[Optional[Dict[str, Any]]] = ContextVar("role_pseudonym", default=None)

try:
    from pypinyin import Style, lazy_pinyin
except Exception:  # pragma: no cover
    Style = None  # type: ignore
    lazy_pinyin = None
    logger.warning("pypinyin 未安装或导入失败，姓名将用内置首字母表脱敏")


# GBK 区位粗分拼音首字母，pypinyin 不可用时兜底；绝不能把原汉字再拼回去。
_GBK_INITIALS = (
    (0xB0A1, 0xB0C4, "A"), (0xB0C5, 0xB2C0, "B"), (0xB2C1, 0xB4ED, "C"),
    (0xB4EE, 0xB6E9, "D"), (0xB6EA, 0xB7A1, "E"), (0xB7A2, 0xB8C0, "F"),
    (0xB8C1, 0xB9FD, "G"), (0xB9FE, 0xBBF6, "H"), (0xBBF7, 0xBFA5, "J"),
    (0xBFA6, 0xC0AB, "K"), (0xC0AC, 0xC2E7, "L"), (0xC2E8, 0xC4C2, "M"),
    (0xC4C3, 0xC5B5, "N"), (0xC5B6, 0xC5BD, "O"), (0xC5BE, 0xC6D9, "P"),
    (0xC6DA, 0xC8BA, "Q"), (0xC8BB, 0xC8F5, "R"), (0xC8F6, 0xCBF9, "S"),
    (0xCBFA, 0xCDD9, "T"), (0xCDDA, 0xCEF3, "W"), (0xCEF4, 0xD1B8, "X"),
    (0xD1B9, 0xD4D0, "Y"), (0xD4D1, 0xD7F9, "Z"),
)


def _coerce_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        return value.strip().lower() in _BOOL_TRUE
    return False


def _default_fields() -> Dict[str, bool]:
    """未写过勾选时：姓名/地址/电话/身份证为勾选，住院号/门诊号/病人编号不勾。"""
    return {key: bool(legacy_on) for key, _label, _cols, legacy_on in FIELD_SPECS}


def _normalize_fields(raw: Any) -> Dict[str, bool]:
    if not isinstance(raw, dict):
        return _default_fields()
    out: Dict[str, bool] = {}
    for key, _label, _cols, _legacy in FIELD_SPECS:
        if key in raw:
            out[key] = _coerce_bool(raw.get(key))
        else:
            out[key] = False
    return out


def _empty_config(role_name: str = "") -> Dict[str, Any]:
    return {"enabled": False, "fields": _default_fields(), "role_name": role_name}


def parse_pseudonym_value(raw: Any) -> Dict[str, Any]:
    """把 T_JS.F_WSMH 解析为 {enabled, fields}。空值表示总开关关，勾选用默认项。"""
    data: Any = raw
    if isinstance(raw, str):
        text = raw.strip()
        if not text:
            return {"enabled": False, "fields": _default_fields()}
        try:
            data = json.loads(text)
        except Exception:
            return {"enabled": False, "fields": _default_fields()}
    if not isinstance(data, dict):
        return {"enabled": False, "fields": _default_fields()}
    enabled = None
    for key in ("enabled", "pseudonym_enabled", "pseudonymEnabled"):
        if key in data:
            enabled = _coerce_bool(data.get(key))
            break
    fields_raw = data.get("fields", data.get("pseudonym_fields"))
    return {
        "enabled": bool(enabled),
        "fields": _normalize_fields(fields_raw),
    }


def pseudonym_field_options(fields: Optional[Dict[str, bool]] = None) -> List[Dict[str, Any]]:
    chosen = fields if fields is not None else pseudonym_fields()
    return [
        {"key": key, "label": label, "checked": bool(chosen.get(key))}
        for key, label, _cols, _legacy in FIELD_SPECS
    ]


def public_pseudonym(cfg: Optional[Dict[str, Any]], role_name: str = "") -> Dict[str, Any]:
    parsed = cfg if cfg and "fields" in cfg else parse_pseudonym_value(cfg)
    name = str((cfg or {}).get("role_name") or role_name or "")
    return {
        "enabled": bool(parsed.get("enabled")),
        "role_name": name,
        "fields": pseudonym_field_options(parsed.get("fields") or {}),
    }


def current_pseudonym() -> Dict[str, Any]:
    cfg = _ctx.get()
    if not isinstance(cfg, dict):
        return _empty_config()
    return cfg


def bind_role_pseudonym(role_name: Optional[str]) -> None:
    """按角色名绑定本请求的伪名化。无角色或读库失败时不脱敏。"""
    name = str(role_name or "").strip()
    if not name:
        _ctx.set(_empty_config())
        return
    try:
        from app.crud.role import load_role_pseudonym
        loaded = load_role_pseudonym(name)
    except Exception:
        logger.exception("读取角色伪名化失败 role=%s", name)
        loaded = _empty_config(name)
    loaded["role_name"] = name
    _ctx.set(loaded)


def reset_role_pseudonym(token: Any) -> None:
    _ctx.reset(token)


def push_role_pseudonym(role_name: Optional[str]) -> Any:
    """返回 ContextVar token，供中间件在请求结束后复位。"""
    name = str(role_name or "").strip()
    if not name:
        return _ctx.set(_empty_config())
    try:
        from app.crud.role import load_role_pseudonym
        loaded = load_role_pseudonym(name)
    except Exception:
        logger.exception("读取角色伪名化失败 role=%s", name)
        loaded = _empty_config(name)
    loaded["role_name"] = name
    return _ctx.set(loaded)


def is_pseudonym_enabled() -> bool:
    """当前请求、当前会话角色是否开启伪名化。"""
    return bool(current_pseudonym().get("enabled"))


def pseudonym_fields() -> Dict[str, bool]:
    fields = current_pseudonym().get("fields") or {}
    return {key: bool(fields.get(key)) for key in FIELD_KEYS}


def _parse_field_selection(raw: Any, current: Dict[str, bool]) -> Dict[str, bool]:
    if raw is None:
        return dict(current)
    if isinstance(raw, (list, tuple)):
        chosen = {str(item).strip() for item in raw if str(item).strip()}
        unknown = chosen - set(FIELD_KEYS)
        if unknown:
            raise ValueError("未知的伪名化字段: " + ", ".join(sorted(unknown)))
        return {key: key in chosen for key in FIELD_KEYS}
    if isinstance(raw, dict):
        unknown = {str(k) for k in raw.keys()} - set(FIELD_KEYS)
        if unknown:
            raise ValueError("未知的伪名化字段: " + ", ".join(sorted(unknown)))
        out = dict(current)
        for key in FIELD_KEYS:
            if key in raw:
                out[key] = _coerce_bool(raw.get(key))
        return out
    raise ValueError("fields 须为勾选对象或字段名数组")


def serialize_pseudonym(enabled: bool, fields: Dict[str, bool]) -> str:
    payload = {
        "enabled": bool(enabled),
        "fields": {key: bool(fields.get(key)) for key in FIELD_KEYS},
    }
    return json.dumps(payload, ensure_ascii=False)


def build_pseudonym_selection(enabled: bool, fields: Any, current: Dict[str, bool]) -> Dict[str, Any]:
    selected = _parse_field_selection(fields, current)
    return {"enabled": bool(enabled), "fields": selected}


def pypinyin_available() -> bool:
    return lazy_pinyin is not None and Style is not None


def _gbk_initial(ch: str) -> str:
    try:
        encoded = ch.encode("gbk")
    except Exception:
        return "*"
    if len(encoded) != 2:
        return "*"
    code = encoded[0] * 256 + encoded[1]
    for start, end, letter in _GBK_INITIALS:
        if start <= code <= end:
            return letter
    return "*"


def _pinyin_initial(ch: str) -> str:
    if lazy_pinyin is not None and Style is not None:
        try:
            letters = lazy_pinyin(ch, style=Style.FIRST_LETTER)
            s = str((letters or [""])[0] or "").strip()
            letter = s[:1].upper()
            if letter.isascii() and letter.isalpha():
                return letter
        except Exception:
            logger.exception("pypinyin 转换失败: %s", ch)
    letter = _gbk_initial(ch)
    if letter.isascii() and letter.isalpha():
        return letter
    return "*"


def mask_name(value: Any) -> Any:
    if value is None:
        return value
    raw = str(value).strip()
    if not raw:
        return value
    surname = raw[0]
    given = raw[1:]
    for compound in _COMPOUND_SURNAMES:
        if raw.startswith(compound) and len(raw) > len(compound):
            surname = compound
            given = raw[len(compound):]
            break
    if not given:
        return raw
    compact = given.replace(" ", "")
    if compact.isascii() and compact.isalpha():
        return surname + compact.upper()
    initials: list[str] = []
    for ch in given:
        if ch.isascii() and ch.isalpha():
            initials.append(ch.upper())
        elif "\u4e00" <= ch <= "\u9fff":
            initials.append(_pinyin_initial(ch))
        elif ch.isdigit() or not ch.isspace():
            initials.append("*")
    if not initials:
        return surname + ("*" * max(len(given), 1))
    return surname + "".join(initials)


def mask_phone(value: Any) -> Any:
    if value is None:
        return value
    raw = str(value).strip()
    if not raw or "*" in raw:
        return raw
    digits = "".join(c for c in raw if c.isdigit())
    if len(digits) < 7:
        return "*" * len(raw)
    return digits[:3] + ("*" * (len(digits) - 7)) + digits[-4:]


def mask_id_card(value: Any) -> Any:
    if value is None:
        return value
    raw = str(value).strip()
    if not raw or "*" in raw:
        return raw
    compact = re.sub(r"\s+", "", raw)
    if len(compact) < 7:
        return "*" * len(raw)
    return compact[:3] + ("*" * (len(compact) - 7)) + compact[-4:]


def mask_code(value: Any) -> Any:
    """住院号、门诊号、病人编号：保留前 2 后 2，中间打码。"""
    if value is None:
        return value
    raw = str(value).strip()
    if not raw or "*" in raw:
        return raw
    n = len(raw)
    if n <= 1:
        return "*"
    if n == 2:
        return raw[:1] + "*"
    if n <= 4:
        return raw[:1] + ("*" * (n - 1))
    return raw[:2] + ("*" * (n - 4)) + raw[-2:]


def mask_address(value: Any) -> Any:
    if value is None:
        return value
    raw = str(value).strip()
    if not raw or raw.endswith("****"):
        return raw
    if len(raw) <= 6:
        return (raw[:1] + "****") if raw else raw
    return raw[:6] + "****"


def _norm_key(key: Any) -> str:
    if isinstance(key, bytes):
        key = key.decode("utf-8", "ignore")
    s = str(key or "").strip()
    if "." in s:
        s = s.rsplit(".", 1)[-1]
    return s.upper()


def _mask_by_key(key: Any, value: Any, selected: set) -> Any:
    uk = _norm_key(key)
    field = _KEY_TO_FIELD.get(uk)
    if not field or field not in selected:
        return value
    if value is None:
        return value
    if not isinstance(value, str):
        value = str(value)
    if field == "name":
        return mask_name(value)
    if field in ("zyh", "mzh", "brbh"):
        return mask_code(value)
    if field == "phone":
        return mask_phone(value)
    if field == "address":
        return mask_address(value)
    if field == "id_card":
        return mask_id_card(value)
    return value


def _to_plain(obj: Any) -> Any:
    if isinstance(obj, (dict, list, tuple, str, bytes, int, float, bool)) or obj is None:
        return obj
    dump = getattr(obj, "model_dump", None)
    if callable(dump):
        try:
            return dump()
        except Exception:
            return obj
    as_dict = getattr(obj, "dict", None)
    if callable(as_dict):
        try:
            return as_dict()
        except Exception:
            return obj
    return obj


def apply_pseudonym(obj: Any, enabled: Optional[bool] = None, fields: Optional[Any] = None) -> Any:
    if enabled is None:
        enabled = is_pseudonym_enabled()
    if not enabled:
        return obj
    if fields is None:
        selected = {key for key, on in pseudonym_fields().items() if on}
    elif isinstance(fields, dict):
        selected = {key for key, on in fields.items() if on and key in _FIELD_BY_KEY}
    else:
        selected = {str(key) for key in fields if str(key) in _FIELD_BY_KEY}
    if not selected:
        return obj
    return _apply_selected(obj, selected)


def _apply_selected(obj: Any, selected: set) -> Any:
    obj = _to_plain(obj)
    if isinstance(obj, dict):
        out: Dict[str, Any] = {}
        for k, v in obj.items():
            plain = _to_plain(v)
            if isinstance(plain, (dict, list, tuple)):
                out[k] = _apply_selected(plain, selected)
            else:
                out[k] = _mask_by_key(k, plain if plain is not v else v, selected)
        return out
    if isinstance(obj, list):
        return [_apply_selected(item, selected) for item in obj]
    if isinstance(obj, tuple):
        return tuple(_apply_selected(item, selected) for item in obj)
    return obj
