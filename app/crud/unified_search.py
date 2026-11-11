from typing import Any, Dict, List
from app.db.factory import get_adapter
from datetime import datetime


def _format_datetime(val: Any) -> str | None:
    if val is None:
        return None
    if isinstance(val, datetime):
        return val.strftime("%Y-%m-%d %H:%M:%S")
    try:
        dv = datetime.fromisoformat(str(val))
        return dv.strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return str(val)


# 库存状态 → SQL 条件（OR 组合）
# F_YSZT 管运输；F_KCZT 管样本库库存
_STOCK_STATUS_SQL = {
    "在库": "(f.F_KCZT = '在库' OR (f.F_KCZT IS NULL AND f.F_YSZT = '已接收'))",
    "未入库": (
        "(f.F_KCZT IS NULL OR f.F_KCZT = '' OR f.F_KCZT = '未入库') "
        "AND (f.F_YSZT IS NULL OR f.F_YSZT = '' OR f.F_YSZT = '未运送' OR f.F_YSZT = '运送中' OR f.F_YSZT = '已接收')"
    ),
    "已出库": "f.F_KCZT = '已出库'",
}

# 兼容旧版 F_YSZT 运输状态标签 → 库存状态
_LEGACY_STOCK_LABEL_MAP = {
    "已接收": "在库",
    "未运送": "未入库",
    "运送中": "未入库",
}


def _normalize_stock_status_labels(raw: Any) -> List[str]:
    """将 F_KCZT / F_YSZT 入参归一化为库存状态标签列表；空表示不过滤。"""
    if raw is None:
        return []
    labels: List[str] = []
    if isinstance(raw, str):
        s = raw.strip()
        if not s or s == "全部":
            return []
        labels = [s]
    elif isinstance(raw, list):
        labels = [
            str(x).strip()
            for x in raw
            if x is not None and str(x).strip() and str(x).strip() != "全部"
        ]
    else:
        return []

    mapped: List[str] = []
    for label in labels:
        canonical = _LEGACY_STOCK_LABEL_MAP.get(label, label)
        if canonical in _STOCK_STATUS_SQL and canonical not in mapped:
            mapped.append(canonical)
    return mapped


def _resolve_stock_status_filter(payload: Dict[str, Any]) -> List[str]:
    """优先 F_KCZT，其次兼容 F_YSZT；均未传则不过滤。"""
    kczt_raw = payload.get("F_KCZT")
    if kczt_raw not in (None, "", []):
        labels = _normalize_stock_status_labels(kczt_raw)
        if labels:
            return labels
    yszt_raw = payload.get("F_YSZT")
    if yszt_raw not in (None, "", []):
        return _normalize_stock_status_labels(yszt_raw)
    return []


def _append_stock_status_filter(where: List[str], labels: List[str]) -> None:
    """按库存状态标签构建多选 OR 条件。"""
    if not labels:
        return
    clauses: List[str] = []
    seen: set[str] = set()
    for label in labels:
        sql = _STOCK_STATUS_SQL.get(label)
        if sql and sql not in seen:
            seen.add(sql)
            clauses.append(sql)
    if clauses:
        where.append("(" + " OR ".join(clauses) + ")")


def _stock_status_label(kczt: Any, yszt: Any) -> str:
    """与库存筛选同一套规则，算出页面使用的 F_YBZT。在库优先于未入库。"""
    kc = "" if kczt is None else str(kczt).strip()
    ys = "" if yszt is None else str(yszt).strip()
    if kc == "已出库":
        return "已出库"
    if kc == "在库" or (kczt is None and ys == "已接收"):
        return "在库"
    if kc in ("", "未入库") and ys in ("", "未运送", "运送中", "已接收"):
        return "未入库"
    return kc or ys


_YBZJ_JOIN = """
LEFT JOIN (
    SELECT F_ZZID,
           MAX(F_BBLX) AS F_BBLX,
           MAX(F_BBLY) AS F_BBLY,
           MAX(F_AZ) AS F_AZ
    FROM T_YBZJ
    GROUP BY F_ZZID
) y ON y.F_ZZID = f.F_ZZID
"""

# 病种、来源科室：工单或样本管为空时回退到标本
_AZ_EXPR = "COALESCE(NULLIF(TRIM(p.F_AZ), ''), NULLIF(TRIM(y.F_AZ), ''))"
_LYKS_EXPR = "COALESCE(NULLIF(TRIM(f.F_LYKS), ''), NULLIF(TRIM(y.F_BBLY), ''))"


def _fill_display_fields(record: Dict[str, Any]) -> None:
    record["F_YBZT"] = _stock_status_label(record.get("F_KCZT"), record.get("F_YSZT"))
    record["F_CCWZ"] = "" if not record.get("F_CCWZ") else str(record.get("F_CCWZ"))


def _as_filter_values(raw: Any) -> List[str]:
    """查询条件归一化为非空字符串列表；空串/空数组视为不过滤。"""
    if raw is None or raw == "":
        return []
    if isinstance(raw, str):
        s = raw.strip()
        return [s] if s else []
    if isinstance(raw, list):
        return [
            str(x).strip()
            for x in raw
            if x is not None and str(x).strip()
        ]
    s = str(raw).strip()
    return [s] if s else []


def list_ybfz(payload: Dict[str, Any], user: Dict[str, Any] | None = None,
             scope: str | None = None) -> Dict[str, Any]:
    from app.crud.direct_inbound_schema import ensure_schema
    ensure_schema()
    adapter = get_adapter()
    skip = int(payload.get("skip") or 0)
    limit = int(payload.get("limit") or 50)

    filters: List[str] = []
    params: List[Any] = []

    _append_stock_status_filter(filters, _resolve_stock_status_filter(payload))

    # 模糊匹配条件（字段名 -> SQL 列名）
    like_fields = [
        ("F_SGBH", "f.F_SGBH"),
        ("F_ZZID",  "f.F_ZZID"),
        ("F_GDBH",  "p.F_GDBH"),
        ("F_DC",    "COALESCE(p.F_DC, f.F_DC)"),
        ("F_YBLX",  "f.F_YBLX"),
        ("F_BRBH",  "f.F_BRBH"),
        ("F_AZ",    _AZ_EXPR),
        ("F_LYKS",  _LYKS_EXPR),
        ("F_BBLY",  _LYKS_EXPR),
        ("F_CJBW",  "p.F_ZZBW"),
        ("F_PYR",   "p.F_SQR"),
    ]
    for key, column in like_fields:
        vals = _as_filter_values(payload.get(key))
        if not vals:
            continue
        ors: List[str] = []
        for val in vals:
            pred, like_params = adapter.like(column, val)
            ors.append(pred)
            params.extend(like_params)
        filters.append(ors[0] if len(ors) == 1 else "(" + " OR ".join(ors) + ")")

    # 入库时间范围过滤
    rksj_start = (payload.get("F_RKSJ_START") or "").strip()
    rksj_end = (payload.get("F_RKSJ_END") or "").strip()
    if rksj_start:
        filters.append("f.F_RKSJ >= ?")
        params.append(rksj_start)
    if rksj_end:
        filters.append("f.F_RKSJ < ?")
        params.append(rksj_end + " 23:59:59" if len(rksj_end) == 10 else rksj_end)

    zcsj_start = (payload.get("F_ZCSJ_START") or "").strip()
    zcsj_end = (payload.get("F_ZCSJ_END") or "").strip()
    if zcsj_start:
        filters.append("f.F_CJSJ >= ?")
        params.append(zcsj_start)
    if zcsj_end:
        filters.append("f.F_CJSJ < ?")
        params.append(zcsj_end + " 23:59:59" if len(zcsj_end) == 10 else zcsj_end)

    from app.core.data_scope import is_self_scope, owner_predicate
    if is_self_scope(scope):
        pred_sqr, p_sqr = owner_predicate("p.F_SQR", user, match="name")
        pred_djr, p_djr = owner_predicate("y2.F_DJR", user, match="name")
        filters.append(
            f"({pred_sqr} OR EXISTS (SELECT 1 FROM T_YBZJ y2 WHERE y2.F_ZZID = f.F_ZZID AND {pred_djr}))"
        )
        params.extend(p_sqr)
        params.extend(p_djr)

    where_sql = (" WHERE " + " AND ".join(filters)) if filters else ""

    base_sql = f"""
SELECT
    f.*, p.F_GDBH, p.F_DC AS F_DC,
    p.F_SQR AS F_PYR,
    p.F_ZZBW AS F_CJBW,
    COALESCE(f.F_YBLX, y.F_BBLX) AS F_BBLX,
    NULL AS F_KLX,
    f.F_CJSJ AS F_ZCSJ,
    {_AZ_EXPR} AS F_AZ,
    {_LYKS_EXPR} AS F_BBLY
FROM T_YBFZ f
LEFT JOIN T_PYGD p ON p.F_ID = f.F_PYGD_ID
{_YBZJ_JOIN}
{where_sql}
"""

    count_sql = f"""
SELECT COUNT(1)
FROM T_YBFZ f
LEFT JOIN T_PYGD p ON p.F_ID = f.F_PYGD_ID
{_YBZJ_JOIN}
{where_sql}
"""
    # 自定义序号排序：字母开头 → 数字开头（按数值大小） → NULL/空 → 最后按 F_ID 倒序
    from app.crud.organoid_culture_tissue import _build_zdyxh_order
    order_clause = "ORDER BY" + _build_zdyxh_order(adapter, "f") + " f.F_ID DESC"
    list_sql = base_sql + " " + order_clause
    list_sql, extra = adapter.paginate(list_sql, skip=skip, limit=limit)

    with adapter.connect() as conn:
        cur = conn.cursor()
        cur.execute(count_sql, tuple(params))
        total = int(cur.fetchone()[0])

        cur.execute(list_sql, tuple(params + extra))
        cols = [c[0] for c in cur.description]
        rows = cur.fetchall()

    items: List[Dict[str, Any]] = []
    for row in rows:
        record = dict(zip(cols, row))
        record["F_CJSJ"] = _format_datetime(record.get("F_CJSJ"))
        record["F_ZCSJ"] = _format_datetime(record.get("F_ZCSJ"))
        _fill_display_fields(record)
        items.append(record)

    return {"items": items, "total": total}


def get_sample_detail_by_id(payload: Dict[str, Any]) -> Dict[str, Any]:
    """
    通过 F_ID 获取样本详情
    """
    f_id = payload.get("F_ID")
    if not f_id:
        raise ValueError("F_ID 不能为空")

    try:
        f_id = int(f_id)
    except (ValueError, TypeError):
        raise ValueError("F_ID 必须是有效的整数")

    from app.crud.direct_inbound_schema import ensure_schema
    ensure_schema()
    adapter = get_adapter()

    sql = f"""
    SELECT
        f.*,
        p.F_GDBH,
        p.F_DC AS F_DC,
        p.F_SQR AS F_PYR,
        COALESCE(f.F_YBLX, y.F_BBLX) AS F_BBLX,
        NULL AS F_KLX,
        f.F_CJSJ AS F_ZCSJ,
        {_AZ_EXPR} AS F_AZ,
        {_LYKS_EXPR} AS F_BBLY
    FROM T_YBFZ f
    LEFT JOIN T_PYGD p ON p.F_ID = f.F_PYGD_ID
    {_YBZJ_JOIN}
    WHERE f.F_ID = ?
    """

    rows, cols = adapter.fetch_all(sql, [f_id])
    if not rows:
        raise ValueError(f"未找到样本ID: {f_id}")

    idx = {c: i for i, c in enumerate(cols)}
    row = rows[0]

    result = {}
    for col in cols:
        result[col] = row[idx[col]]

    result["F_CJSJ"] = _format_datetime(result.get("F_CJSJ"))
    result["F_ZCSJ"] = _format_datetime(result.get("F_ZCSJ"))
    _fill_display_fields(result)

    return result


def set_sample_order(ybfz_id: int, zdyxh: str | None) -> Dict[str, Any]:
    """
    设置或清除样本的自定义序号 F_ZDYXH。

    Args:
        ybfz_id: T_YBFZ.F_ID
        zdyxh:   新序号（字符串）；传 '' 或 '-1' 表示清除（置为 NULL）；传 None 则不修改（抛出异常）

    Returns:
        {"F_ID": int, "F_ZDYXH": str | None}
    """
    if zdyxh is None:
        raise ValueError("F_ZDYXH 不能为空，传空字符串或 '-1' 可清除序号")

    adapter = get_adapter()
    with adapter.connect() as conn:
        cur = conn.cursor()

        cur.execute("SELECT F_ID FROM T_YBFZ WHERE F_ID = ?", (ybfz_id,))
        if not cur.fetchone():
            raise ValueError(f"样本ID {ybfz_id} 不存在")

        if zdyxh == "" or zdyxh == "-1":
            cur.execute("UPDATE T_YBFZ SET F_ZDYXH = NULL WHERE F_ID = ?", (ybfz_id,))
            final_val = None
        else:
            cur.execute("UPDATE T_YBFZ SET F_ZDYXH = ? WHERE F_ID = ?", (zdyxh, ybfz_id))
            final_val = zdyxh

        conn.commit()

    return {"F_ID": ybfz_id, "F_ZDYXH": final_val}
