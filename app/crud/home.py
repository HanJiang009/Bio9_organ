"""首页标本/工单卡片与列表。

只展示已创建的培养工单。未培养标本、待创建分发，以及不合格/已完成等其他状态不进入首页；
调用方显式传入 F_GDZT 时按该状态筛选。
"""
from typing import Any, Dict, List, Optional, Tuple

from app.core.data_scope import apply_owner_filter, SCOPE_SELF
from app.crud.pipeline_progress import attach_pipeline_stages, _fmt_dt
from app.db.factory import get_adapter

_HOME_GDZT = "已创建"


def _keyword_or_clause(adapter, keyword: Optional[str]) -> Tuple[Optional[str], List[Any]]:
    kw = (keyword or "").strip()
    if not kw:
        return None, []
    cols = ("p.F_XM", "p.F_ZZID", "p.F_GDBH", "p.F_BRBH")
    parts: List[str] = []
    params: List[Any] = []
    for col in cols:
        pred, p = adapter.like(col, kw)
        parts.append(pred)
        params.extend(p)
    return "(" + " OR ".join(parts) + ")", params


def _home_where(
    q: Dict[str, Any],
    user: Optional[Dict[str, Any]],
    scope: Optional[str],
) -> Tuple[str, List[Any]]:
    adapter = get_adapter()
    where: List[str] = []
    params: List[Any] = []

    clause, kw_params = _keyword_or_clause(adapter, q.get("keyword"))
    if clause:
        where.append(clause)
        params.extend(kw_params)

    gdzt = (q.get("F_GDZT") or "").strip() or _HOME_GDZT
    where.append("p.F_GDZT = ?")
    params.append(gdzt)

    apply_user = user
    if apply_user is None and scope == SCOPE_SELF:
        apply_user = {}
    apply_owner_filter(where, params, scope=scope, user=apply_user, column="p.F_SQR", match="name")

    where_sql = (" WHERE " + " AND ".join(where)) if where else ""
    return where_sql, params


def _owner_where(user: Optional[Dict[str, Any]], scope: Optional[str]) -> Tuple[str, List[Any]]:
    where: List[str] = []
    params: List[Any] = []
    apply_owner_filter(where, params, scope=scope, user=user, column="p.F_SQR", match="name")
    where_sql = (" WHERE " + " AND ".join(where)) if where else ""
    return where_sql, params


def _query_order_counts(
    cur,
    zzids: List[str],
    user: Optional[Dict[str, Any]],
    scope: Optional[str],
) -> Dict[str, int]:
    result: Dict[str, int] = {}
    if not zzids:
        return result
    owner_sql, owner_params = _owner_where(user, scope)
    extra = " AND " if owner_sql else " WHERE "
    ph = ",".join(["?"] * len(zzids))
    sql = (
        f"SELECT p.F_ZZID, COUNT(1) FROM T_PYGD p{owner_sql}{extra}"
        f"p.F_GDZT = ? AND p.F_ZZID IN ({ph}) GROUP BY p.F_ZZID"
    )
    cur.execute(sql, tuple(owner_params + [_HOME_GDZT] + zzids))
    for row in cur.fetchall():
        if row[0]:
            result[str(row[0])] = int(row[1] or 0)
    return result


def _query_sample_type_remarks(cur, yblx_list: List[str]) -> Dict[str, Optional[str]]:
    remarks: Dict[str, Optional[str]] = {}
    if not yblx_list:
        return remarks
    ph = ",".join(["?"] * len(yblx_list))
    try:
        cur.execute(
            f"""
            SELECT F_VALUE, F_CODE, F_REMARK
            FROM T_DICT_ITEM
            WHERE F_VALUE IN ({ph}) OR F_CODE IN ({ph})
            """,
            tuple(yblx_list + yblx_list),
        )
        for row in cur.fetchall():
            value = str(row[0]).strip() if row[0] else ""
            code = str(row[1]).strip() if row[1] else ""
            remark = str(row[2]).strip() if row[2] not in (None, "") else None
            if value and value not in remarks:
                remarks[value] = remark
            if code and code not in remarks:
                remarks[code] = remark
            if remark:
                if value:
                    remarks[value] = remark
                if code:
                    remarks[code] = remark
    except Exception:
        pass
    return remarks


def _query_sample_types(cur, zzids: List[str]) -> Dict[str, List[Dict[str, Any]]]:
    grouped: Dict[str, List[Dict[str, Any]]] = {z: [] for z in zzids}
    if not zzids:
        return grouped
    ph = ",".join(["?"] * len(zzids))
    rows: List[Tuple[Any, Any, Any]] = []
    try:
        cur.execute(
            f"""
            SELECT F_ZZID, F_YBLX, COUNT(1)
            FROM T_YBFZ
            WHERE F_ZZID IN ({ph}) AND F_YBLX IS NOT NULL AND F_YBLX <> ''
            GROUP BY F_ZZID, F_YBLX
            ORDER BY COUNT(1) DESC
            """,
            tuple(zzids),
        )
        rows = list(cur.fetchall())
    except Exception:
        return grouped

    yblx_list = list({str(r[1]).strip() for r in rows if r[1]})
    remark_map = _query_sample_type_remarks(cur, yblx_list)
    for zzid, yblx, cnt in rows:
        key = str(zzid) if zzid else ""
        name = str(yblx).strip() if yblx else ""
        if not key or not name:
            continue
        grouped.setdefault(key, []).append({
            "F_YBLX": name,
            "F_REMARK": remark_map.get(name),
            "count": int(cnt or 0),
        })
    return grouped


_CARD_COLS = """
p.F_ID, p.F_GDBH, p.F_ZZID, p.F_XM, p.F_BRBH, p.F_AZ, p.F_DC,
p.F_GDZT, p.F_ZKZT, p.F_ZDYXH, p.F_PYSJ
"""

_LIST_COLS = _CARD_COLS + """,
p.F_SQR, p.F_YBLY, p.F_WCSJ, p.F_BHGSJ, p.F_PIPELINE_LX, p.F_CJSJ
"""


def _normalize_view(raw: Any, default: str = "specimen") -> str:
    s = str(raw or default).strip().lower()
    if s in ("order", "工单"):
        return "order"
    if s in ("specimen", "标本", "sample"):
        return "specimen"
    return default


def _build_home_sql(view: str, where_sql: str, select_cols: str) -> Tuple[str, str]:
    if view == "order":
        base_core = f"SELECT {select_cols} FROM T_PYGD p{where_sql}"
        count_sql = f"SELECT COUNT(1) FROM T_PYGD p{where_sql}"
        base_select = base_core + " ORDER BY p.F_PYSJ DESC, p.F_ID DESC"
        return base_select, count_sql
    ranked = f"""
SELECT {select_cols},
       ROW_NUMBER() OVER (PARTITION BY p.F_ZZID ORDER BY p.F_PYSJ DESC, p.F_ID DESC) AS rn
FROM T_PYGD p
{where_sql}
"""
    base_core = f"SELECT * FROM ({ranked}) t WHERE t.rn = 1"
    count_sql = f"SELECT COUNT(1) FROM ({base_core}) c"
    base_select = base_core + " ORDER BY F_PYSJ DESC, F_ID DESC"
    return base_select, count_sql


def _fetch_home_rows(
    q: Dict[str, Any],
    user: Optional[Dict[str, Any]],
    scope: Optional[str],
    *,
    select_cols: str,
    default_view: str,
    paginate: bool,
) -> Dict[str, Any]:
    adapter = get_adapter()
    view = _normalize_view(q.get("view"), default_view)
    where_sql, params = _home_where(q, user, scope)
    base_select, count_sql = _build_home_sql(view, where_sql, select_cols)

    skip = int(q.get("skip") or 0)
    raw_limit = q.get("limit")
    if paginate:
        limit: Optional[int] = int(raw_limit) if raw_limit else 20
    else:
        limit = int(raw_limit) if raw_limit not in (None, "") else None
        if limit is None:
            skip = 0

    sql, extra = adapter.paginate(base_select, skip=skip, limit=limit)
    with adapter.connect() as conn:
        cur = conn.cursor()
        cur.execute(count_sql, tuple(params))
        total = int(cur.fetchone()[0] or 0)
        if total == 0:
            return {"total": 0, "items": [], "view": view}
        cur.execute(sql, tuple(params + extra))
        cols = [c[0] for c in cur.description]
        rows = [dict(zip(cols, r)) for r in cur.fetchall()]

    for row in rows:
        row.pop("rn", None)
        row.pop("RN", None)
    return {"total": total, "items": rows, "view": view}


def _format_card_item(row: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "F_ID": row.get("F_ID"),
        "F_XM": row.get("F_XM"),
        "F_BRBH": row.get("F_BRBH"),
        "F_ZZID": row.get("F_ZZID"),
        "F_GDZT": row.get("F_GDZT"),
        "F_ZKZT": row.get("F_ZKZT"),
        "F_AZ": row.get("F_AZ"),
        "F_DC": row.get("F_DC"),
        "F_GDBH": row.get("F_GDBH"),
        "F_ZDYXH": row.get("F_ZDYXH"),
        "F_PYSJ": _fmt_dt(row.get("F_PYSJ")),
        "order_count": int(row.get("order_count") or 0),
        "sample_types": row.get("sample_types") or [],
    }


def _attach_counts_and_types(
    items: List[Dict[str, Any]],
    user: Optional[Dict[str, Any]],
    scope: Optional[str],
) -> None:
    zzids = [str(it.get("F_ZZID")) for it in items if it.get("F_ZZID")]
    adapter = get_adapter()
    with adapter.connect() as conn:
        cur = conn.cursor()
        count_map = _query_order_counts(cur, zzids, user, scope)
        type_map = _query_sample_types(cur, zzids)
    for it in items:
        zzid = str(it.get("F_ZZID") or "")
        it["order_count"] = count_map.get(zzid, 0)
        it["sample_types"] = type_map.get(zzid, [])


def list_home_cards(
    q: Dict[str, Any],
    user: Optional[Dict[str, Any]] = None,
    scope: Optional[str] = None,
) -> Dict[str, Any]:
    data = _fetch_home_rows(
        q, user, scope,
        select_cols=_CARD_COLS,
        default_view="specimen",
        paginate=False,
    )
    _attach_counts_and_types(data["items"], user, scope)
    data["items"] = [_format_card_item(it) for it in data["items"]]
    return data


def list_home_orders(
    q: Dict[str, Any],
    user: Optional[Dict[str, Any]] = None,
    scope: Optional[str] = None,
) -> Dict[str, Any]:
    data = _fetch_home_rows(
        q, user, scope,
        select_cols=_LIST_COLS,
        default_view="order",
        paginate=True,
    )
    items = attach_pipeline_stages(data["items"])
    _attach_counts_and_types(items, user, scope)
    for it in items:
        it["F_WCSJ"] = _fmt_dt(it.get("F_WCSJ"))
        it["F_BHGSJ"] = _fmt_dt(it.get("F_BHGSJ"))
        it["F_CJSJ"] = _fmt_dt(it.get("F_CJSJ"))
    data["items"] = items
    return data
