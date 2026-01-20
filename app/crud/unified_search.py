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


def list_ybfz(payload: Dict[str, Any]) -> Dict[str, Any]:
    adapter = get_adapter()
    skip = int(payload.get("skip") or 0)
    limit = int(payload.get("limit") or 50)

    filters: List[str] = []
    params: List[Any] = []

    # 固定条件：只查询已接收的样本
    filters.append("f.F_YSZT = ?")
    params.append("已接收")

    for key, column in (
        ("F_SGBH", "f.F_SGBH"),
        ("F_ZZID", "f.F_ZZID"),
        ("F_GDBH", "p.F_GDBH"),
        ("F_DC", "COALESCE(p.F_DC, f.F_DC)"),
    ):
        val = (payload.get(key) or "").strip()
        if val:
            pred, like_params = adapter.like(column, val)
            filters.append(pred)
            params.extend(like_params)

    where_sql = " WHERE " + " AND ".join(filters)

    base_sql = f"""
SELECT
    f.*, p.F_GDBH, p.F_DC AS P_F_DC
FROM T_YBFZ f
LEFT JOIN T_PYGD p ON p.F_ID = f.F_PYGD_ID
{where_sql}
"""

    count_sql = f"SELECT COUNT(1) FROM ( {base_sql} ) t"
    list_sql = base_sql + " ORDER BY f.F_ID DESC"
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

    adapter = get_adapter()
    
    # 查询样本分装信息，包含所有字段
    sql = """
    SELECT
        f.*, 
        p.F_GDBH, 
        p.F_DC AS P_F_DC,
        p.F_SQR AS F_PYR,
        y.F_BBLX
    FROM T_YBFZ f
    LEFT JOIN T_PYGD p ON p.F_ID = f.F_PYGD_ID
    LEFT JOIN T_YBZJ y ON y.F_ID = f.F_YBZJ_ID
    WHERE f.F_ID = ?
    """
    
    rows, cols = adapter.fetch_all(sql, [f_id])
    if not rows:
        raise ValueError(f"未找到样本ID: {f_id}")
    
    idx = {c: i for i, c in enumerate(cols)}
    row = rows[0]
    
    # 构建结果字典
    result = {}
    for col in cols:
        result[col] = row[idx[col]]
    
    # 格式化日期时间字段
    result["F_CJSJ"] = _format_datetime(result.get("F_CJSJ"))
    
    return result

