"""医院级字段配置。库里只存显示名、是否显示、是否必填。"""
from typing import Any, Dict, Iterable, List, Optional

from app.config.field_catalog import FIELDS, PAGES, field_map, page_map
from app.crud.direct_inbound_schema import _create_sql, dialect_name
from app.db.factory import get_adapter


_ready = False
_YES = "是"
_NO = "否"


def ensure_schema() -> None:
    global _ready
    if _ready:
        return
    adapter = get_adapter()
    kind = dialect_name(adapter)
    with adapter.connect() as conn:
        cur = conn.cursor()
        if not adapter.column_exists(cur, "T_ZDPZ", "F_ID"):
            cur.execute(_create_sql(kind, "T_ZDPZ", """
            F_YM VARCHAR(64),
            F_ZDBM VARCHAR(64),
            F_XSM VARCHAR(100),
            F_XS VARCHAR(8),
            F_BT VARCHAR(8)
        """))
        conn.commit()
    _ready = True


def _yes(value: Any, default: bool) -> bool:
    if value is None or (isinstance(value, str) and value.strip() == ""):
        return default
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in {"是", "1", "true", "y", "yes"}:
        return True
    if text in {"否", "0", "false", "n", "no"}:
        return False
    raise ValueError("是否显示、是否必填只接受是或否")


def _flag(value: bool) -> str:
    return _YES if value else _NO


def _clean_name(value: Any) -> Optional[str]:
    text = str(value or "").strip()
    if not text:
        return None
    return text[:100]


def is_blank(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return value.strip() == ""
    if isinstance(value, (list, tuple)):
        return len(value) == 0 or all(is_blank(item) for item in value)
    if isinstance(value, dict):
        return len(value) == 0 or all(is_blank(item) for item in value.values())
    return False


def _overrides(page: str) -> Dict[str, Dict[str, Any]]:
    ensure_schema()
    rows, cols = get_adapter().fetch_all(
        "SELECT F_ZDBM, F_XSM, F_XS, F_BT FROM T_ZDPZ WHERE F_YM = ?",
        [page],
    )
    found: Dict[str, Dict[str, Any]] = {}
    for raw in rows:
        item = dict(zip(cols, raw))
        code = str(item.get("F_ZDBM") or "").strip()
        if code:
            found[code] = item
    return found


def _rules(page: str) -> List[Dict[str, Any]]:
    specs = FIELDS.get(page)
    if specs is None:
        raise ValueError("未知的配置页面")
    stored = _overrides(page)
    rules: List[Dict[str, Any]] = []
    for code, label, locked, check in specs:
        row = stored.get(code) or {}
        visible = True if locked else _yes(row.get("F_XS"), True)
        required = True if locked else _yes(row.get("F_BT"), False)
        if not visible and not locked:
            required = False
        custom = _clean_name(row.get("F_XSM"))
        rules.append({
            "F_ZDBM": code,
            "F_XTMC": label,
            "F_XSM": custom or "",
            "F_XSMC": custom or label,
            "F_XS": _flag(visible),
            "F_BT": _flag(required),
            "F_SD": _flag(locked),
            "F_JY": _flag(check),
            "_visible": visible,
            "_required": required,
            "_locked": locked,
            "_check": check,
        })
    return rules


def _public(rule: Dict[str, Any]) -> Dict[str, Any]:
    return {key: value for key, value in rule.items() if not key.startswith("_")}


def list_pages() -> Dict[str, Any]:
    return {"items": [
        {"F_YM": item["code"], "F_YMMC": item["name"], "F_FZ": item["group"]}
        for item in PAGES
    ]}


def page_detail(page: str) -> Dict[str, Any]:
    meta = page_map().get(page)
    if not meta:
        raise ValueError("未知的配置页面")
    return {
        "F_YM": page,
        "F_YMMC": meta["name"],
        "F_FZ": meta["group"],
        "items": [_public(item) for item in _rules(page)],
    }


def runtime(pages: Iterable[str]) -> Dict[str, Any]:
    codes = [str(item).strip() for item in pages if str(item).strip()]
    if not codes:
        codes = [item["code"] for item in PAGES]
    unknown = [item for item in codes if item not in page_map()]
    if unknown:
        raise ValueError("未知的配置页面: " + "、".join(unknown))
    return {"items": [page_detail(item) for item in codes]}


def save_page(page: str, items: List[Dict[str, Any]]) -> Dict[str, Any]:
    meta = page_map().get(page)
    if not meta:
        raise ValueError("未知的配置页面")
    catalog = field_map(page)
    if not isinstance(items, list) or not items:
        raise ValueError("请提交该页要保存的字段")
    seen = set()
    rows = []
    for item in items:
        if not isinstance(item, dict):
            raise ValueError("字段格式不正确")
        code = str(item.get("F_ZDBM") or "").strip()
        if code not in catalog:
            raise ValueError(f"不能配置目录之外的字段: {code or '空编码'}")
        if code in seen:
            raise ValueError(f"字段重复: {code}")
        seen.add(code)
        _, label, locked, _check = catalog[code]
        visible = True if locked else _yes(item.get("F_XS"), True)
        required = True if locked else _yes(item.get("F_BT"), False)
        if locked and _yes(item.get("F_XS"), True) is False:
            raise ValueError(f"{label}是系统锁定项，不能隐藏")
        if locked and _yes(item.get("F_BT"), True) is False:
            raise ValueError(f"{label}是系统锁定项，必填不能关掉")
        if not visible and not locked:
            required = False
        rows.append((page, code, _clean_name(item.get("F_XSM")), _flag(visible), _flag(required and visible or locked)))
    adapter = get_adapter()
    ensure_schema()
    with adapter.connect() as conn:
        cur = conn.cursor()
        for row in rows:
            cur.execute("DELETE FROM T_ZDPZ WHERE F_YM = ? AND F_ZDBM = ?", (row[0], row[1]))
            cur.execute(
                "INSERT INTO T_ZDPZ (F_YM, F_ZDBM, F_XSM, F_XS, F_BT) VALUES (?, ?, ?, ?, ?)",
                row,
            )
        conn.commit()
    return page_detail(page)


def _iter_dicts(node: Any):
    if isinstance(node, dict):
        yield node
        for value in node.values():
            yield from _iter_dicts(value)
    elif isinstance(node, list):
        for value in node:
            yield from _iter_dicts(value)


def assert_page(
    page: str,
    payload: Any,
    *,
    updating: bool = False,
    limit: Optional[Iterable[str]] = None,
) -> None:
    """显示并且必填的字段为空就拒绝。更新时没传到的字段不拦。"""
    allowed = {str(item) for item in limit} if limit is not None else None
    required = []
    for rule in _rules(page):
        if not rule["_check"] or not rule["_visible"] or not rule["_required"]:
            continue
        if allowed is not None and rule["F_ZDBM"] not in allowed:
            continue
        required.append(rule)
    if not required or payload is None:
        return
    found: Dict[str, List[Any]] = {rule["F_ZDBM"]: [] for rule in required}
    for node in _iter_dicts(payload):
        for code in found:
            if code in node:
                found[code].append(node[code])
    missing = []
    for rule in required:
        values = found[rule["F_ZDBM"]]
        if not values:
            if updating:
                continue
            missing.append(rule["F_XSMC"])
            continue
        if any(is_blank(value) for value in values):
            missing.append(rule["F_XSMC"])
    if missing:
        shown = "、".join(missing[:8])
        raise ValueError(f"请填写：{shown}")


def scrub_hidden(page: str, *nodes: Any) -> None:
    """隐藏字段如果传的是空值，更新时不要拿它把原值清掉。"""
    hidden = {rule["F_ZDBM"] for rule in _rules(page) if not rule["_visible"]}
    if not hidden:
        return

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            for key in list(node):
                if key in hidden and is_blank(node.get(key)):
                    node.pop(key, None)
                else:
                    walk(node.get(key))
        elif isinstance(node, list):
            for item in node:
                walk(item)

    for node in nodes:
        walk(node)
