from datetime import datetime
from typing import Any, Dict, List

from app.db.factory import get_adapter
from app.crud.sample_linkage import _resolve_operator, _parse_datetime


def _format_datetime(val: Any) -> str | None:
    """格式化日期时间为字符串"""
    if val is None:
        return None
    if isinstance(val, datetime):
        return val.strftime("%Y-%m-%d %H:%M:%S")
    try:
        dv = datetime.fromisoformat(str(val))
        return dv.strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return str(val)


def create_transport(payload: Dict[str, Any], user: Dict[str, Any] | None = None) -> Dict[str, Any]:
    ids_raw = payload.get("YBFZ") or []
    if isinstance(ids_raw, (int, str)):
        ids_raw = [ids_raw]
    ybfz_ids: List[int] = [int(x) for x in ids_raw if str(x).strip()]

    if not ybfz_ids:
        raise ValueError("请提供至少一个分装记录ID")

    adapter = get_adapter()
    placeholders = ",".join(["?"] * len(ybfz_ids))

    # 校验分装记录存在且未处于运输中
    select_sql = f"SELECT F_ID, F_YSZT FROM T_YBFZ WHERE F_ID IN ({placeholders})"
    rows, cols = adapter.fetch_all(select_sql, ybfz_ids)
    found_ids = {int(r[0]) for r in rows}
    missing = sorted(set(ybfz_ids) - found_ids)
    if missing:
        raise ValueError(f"找不到以下分装记录ID: {missing}")

    status_idx = cols.index("F_YSZT") if "F_YSZT" in cols else None
    blocked: List[int] = []
    if status_idx is not None:
        for r in rows:
            yszt = r[status_idx]
            if yszt and str(yszt) not in ("未运送", ""):
                blocked.append(int(r[0]))
    if blocked:
        raise ValueError(f"以下分装记录已存在运输状态，无法重复发起: {blocked}")

    # 组装运输单信息
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    ysdh = f"YSDH{datetime.now().strftime('%Y%m%d%H%M%S%f')}"
    ysr = payload.get("F_FQR") or _resolve_operator(user, adapter)
    yssj = _parse_datetime(payload.get("F_FQSJ")) or now_str
    ysbz = payload.get("F_BZ")
    ysmdd = payload.get("F_YSMDD")  # 运输目的地
    ysdzt = "运送中"

    import json

    ysxx_json = json.dumps(ybfz_ids, ensure_ascii=False)

    with adapter.connect() as conn:
        cur = conn.cursor()
        # 插入运输单
        cur.execute(
            """
            INSERT INTO T_YSXX (
                F_YSDH, F_YSXX, F_FQR, F_FQSJ, F_YSDZT,
                F_YBSL, F_BZ, F_JSR, F_JSSJ, F_JSBZ, F_YSMDD
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                ysdh,
                ysxx_json,
                ysr,
                yssj,
                ysdzt,
                len(ybfz_ids),
                ysbz,
                None,
                None,
                None,
                ysmdd,
            ),
        )

        # 更新分装记录运输状态和运输目的地
        update_sql = f"UPDATE T_YBFZ SET F_YSZT = ?, F_YSMDD = ? WHERE F_ID IN ({placeholders})"
        cur.execute(update_sql, tuple([ysdzt, ysmdd] + ybfz_ids))

        conn.commit()

    return {
        "F_YSDH": ysdh,
        "F_YSXX": ybfz_ids,
        "F_YSDZT": ysdzt,
        "F_YBSL": len(ybfz_ids),
    }


def list_transport(payload: Dict[str, Any]) -> Dict[str, Any]:
    adapter = get_adapter()
    skip = int(payload.get("skip") or 0)
    limit = int(payload.get("limit") or 50)
    ysdh_kw = (payload.get("F_YSDH") or "").strip()
    ysdzt = (payload.get("F_YSDZT") or "").strip()
    fqsj_start = (payload.get("F_FQSJ_START") or "").strip()
    fqsj_end = (payload.get("F_FQSJ_END") or "").strip()
    sgbh_kw = (payload.get("F_SGBH") or "").strip()

    where: List[str] = []
    params: List[Any] = []

    if ysdh_kw:
        pred, p = adapter.like("t.F_YSDH", ysdh_kw)
        where.append(pred)
        params.extend(p)

    if ysdzt:
        where.append("t.F_YSDZT = ?")
        params.append(ysdzt)

    if fqsj_start:
        where.append("t.F_FQSJ >= ?")
        params.append(fqsj_start)
    if fqsj_end:
        where.append("t.F_FQSJ <= ?")
        params.append(fqsj_end)

    where_sql = f" WHERE {' AND '.join(where)}" if where else ""

    base_sql = f"""
    SELECT
        t.F_ID,
        t.F_YSDH,
        t.F_YSXX,
        t.F_FQR,
        t.F_FQSJ,
        t.F_YSDZT,
        t.F_YBSL,
        t.F_BZ,
        t.F_JSR,
        t.F_JSSJ,
        t.F_JSBZ,
        t.F_YSMDD
    FROM T_YSXX t
    {where_sql}
    ORDER BY t.F_ID DESC
    """

    items_raw: List[Dict[str, Any]] = []

    with adapter.connect() as conn:
        cur = conn.cursor()
        # 不分页先取出，后续按 SGBH 过滤再分页
        cur.execute(base_sql, tuple(params))
        cols = [c[0] for c in cur.description]
        for row in cur.fetchall():
            data = dict(zip(cols, row))
            for key in ("F_FQSJ", "F_JSSJ"):
                if data.get(key):
                    data[key] = _parse_datetime(data[key])
            items_raw.append(data)

    # 若需按样本编号过滤，基于 F_YSXX 中 ID 去查 T_YBFZ 的 F_SGBH
    filtered: List[Dict[str, Any]] = []
    if sgbh_kw:
        import json
        all_ids: List[int] = []
        for it in items_raw:
            try:
                ids = json.loads(it.get("F_YSXX") or "[]")
                if isinstance(ids, list):
                    all_ids.extend(int(x) for x in ids if str(x).strip())
            except Exception:
                continue
        all_ids = list({i for i in all_ids})

        sgbh_map: Dict[int, str] = {}
        if all_ids:
            placeholders = ",".join(["?"] * len(all_ids))
            sql = f"SELECT F_ID, F_SGBH FROM T_YBFZ WHERE F_ID IN ({placeholders})"
            rows, cols2 = adapter.fetch_all(sql, all_ids)
            idx_id = cols2.index("F_ID")
            idx_sgbh = cols2.index("F_SGBH")
            for r in rows:
                sgbh_map[int(r[idx_id])] = str(r[idx_sgbh]) if r[idx_sgbh] is not None else ""

        sgbh_kw_lower = sgbh_kw.lower()
        for it in items_raw:
            try:
                ids = json.loads(it.get("F_YSXX") or "[]")
                if not isinstance(ids, list):
                    continue
                matched = False
                for _id in ids:
                    sid = int(_id)
                    val = sgbh_map.get(sid, "")
                    if val and sgbh_kw_lower in val.lower():
                        matched = True
                        break
                if matched:
                    filtered.append(it)
            except Exception:
                continue
    else:
        filtered = items_raw

    total = len(filtered)
    paged = filtered[skip: skip + limit] if skip < total else []

    return {"items": paged, "total": total}


def get_transport_detail(payload: Dict[str, Any]) -> Dict[str, Any]:
    ysid = payload.get("F_ID")
    ysdh = (payload.get("F_YSDH") or "").strip()
    if not ysid and not ysdh:
        raise ValueError("F_ID 或 F_YSDH 至少提供一个")

    adapter = get_adapter()
    if ysid:
        header_sql = """
        SELECT
            F_ID, F_YSDH, F_YSXX, F_FQR, F_FQSJ, F_YSDZT, F_BZ, F_YSMDD
        FROM T_YSXX
        WHERE F_ID = ?
        """
        rows, cols = adapter.fetch_all(header_sql, [ysid])
        if not rows:
            raise ValueError(f"未找到运输单ID: {ysid}")
    else:
        header_sql = """
        SELECT
            F_ID, F_YSDH, F_YSXX, F_FQR, F_FQSJ, F_YSDZT, F_BZ, F_YSMDD
        FROM T_YSXX
        WHERE F_YSDH = ?
        """
        rows, cols = adapter.fetch_all(header_sql, [ysdh])
        if not rows:
            raise ValueError(f"未找到运输单号: {ysdh}")
    idx = {c: i for i, c in enumerate(cols)}
    header_row = rows[0]
    ysxx_raw = header_row[idx["F_YSXX"]]
    import json
    try:
        ybfz_ids = json.loads(ysxx_raw or "[]")
        if not isinstance(ybfz_ids, list):
            ybfz_ids = []
        ybfz_ids = [int(x) for x in ybfz_ids if str(x).strip()]
    except Exception:
        ybfz_ids = []

    items: List[Dict[str, Any]] = []
    if ybfz_ids:
        placeholders = ",".join(["?"] * len(ybfz_ids))
        # 先查分装与标本
        sql_ybfz = f"""
        SELECT
            f.F_ID,
            f.F_SGBH,
            f.F_ZZID,
            f.F_DC,
            f.F_PYGD_ID,
            f.F_YBLX,
            y.F_BBLX
        FROM T_YBFZ f
        LEFT JOIN T_YBZJ y ON y.F_ID = f.F_YBZJ_ID
        WHERE f.F_ID IN ({placeholders})
        """
        rows_f, cols_f = adapter.fetch_all(sql_ybfz, ybfz_ids)
        idx_f = {c: i for i, c in enumerate(cols_f)}
        pygd_ids: List[int] = []
        tmp_items: Dict[int, Dict[str, Any]] = {}
        for r in rows_f:
            fid = int(r[idx_f["F_ID"]])
            entry = {
                "F_ID": fid,
                "F_SGBH": r[idx_f.get("F_SGBH")],
                "F_ZZID": r[idx_f.get("F_ZZID")],
                "F_DC": r[idx_f.get("F_DC")],
                "F_PYGD_ID": r[idx_f.get("F_PYGD_ID")],
                "F_YBLX": r[idx_f.get("F_YBLX")],
                "F_BBLX": r[idx_f.get("F_BBLX")] if "F_BBLX" in idx_f else None,
                "F_GDBH": None,
                "F_PYR": None,
            }
            pid = entry.get("F_PYGD_ID")
            if pid is not None:
                pygd_ids.append(int(pid))
            tmp_items[fid] = entry

        pygd_ids = list({p for p in pygd_ids})
        if pygd_ids:
            placeholders2 = ",".join(["?"] * len(pygd_ids))
            # 优先取培养工单的申请人/培养人：使用 F_SQR 作为培养人返回
            try:
                sql_pygd = f"""
                SELECT F_ID, F_GDBH, F_SQR AS F_PYR FROM T_PYGD WHERE F_ID IN ({placeholders2})
                """
                rows_p, cols_p = adapter.fetch_all(sql_pygd, pygd_ids)
            except Exception:
                sql_pygd = f"""
                SELECT F_ID, F_GDBH, NULL AS F_PYR FROM T_PYGD WHERE F_ID IN ({placeholders2})
                """
                rows_p, cols_p = adapter.fetch_all(sql_pygd, pygd_ids)
            idx_p = {c: i for i, c in enumerate(cols_p)}
            for r in rows_p:
                pid = int(r[idx_p["F_ID"]])
                gdbh = r[idx_p.get("F_GDBH")]
                pyr = r[idx_p.get("F_PYR")] if "F_PYR" in idx_p else None
                for entry in tmp_items.values():
                    if entry.get("F_PYGD_ID") == pid:
                        entry["F_GDBH"] = gdbh
                        entry["F_PYR"] = pyr

        # 保持与 F_YSXX 顺序一致
        for fid in ybfz_ids:
            if fid in tmp_items:
                items.append(tmp_items[fid])

    return {
        "F_ID": header_row[idx.get("F_ID")] if "F_ID" in idx else None,
        "F_YSDH": header_row[idx["F_YSDH"]],
        "F_YSDZT": header_row[idx.get("F_YSDZT")],
        "F_FQR": header_row[idx.get("F_FQR")],
        "F_FQSJ": _parse_datetime(header_row[idx.get("F_FQSJ")]) if header_row[idx.get("F_FQSJ")] else None,
        "F_BZ": header_row[idx.get("F_BZ")] if "F_BZ" in idx else None,
        "F_YSMDD": header_row[idx.get("F_YSMDD")] if "F_YSMDD" in idx else None,
        "items": items,
    }


def receive_transport(payload: Dict[str, Any], user: Dict[str, Any] | None = None) -> Dict[str, Any]:
    ysdh = (payload.get("F_YSDH") or "").strip()
    if not ysdh:
        raise ValueError("F_YSDH 不能为空")
    adapter = get_adapter()
    jsr = (payload.get("F_JSR") or "").strip() or _resolve_operator(user, adapter)
    jssj = _parse_datetime(payload.get("F_JSSJ")) or datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    jsbz = payload.get("F_JSBZ")

    # 查运输单
    sql_header = """
    SELECT F_ID, F_YSXX, F_YSDZT
    FROM T_YSXX
    WHERE F_YSDH = ?
    """
    rows, cols = adapter.fetch_all(sql_header, [ysdh])
    if not rows:
        raise ValueError(f"未找到运输单号: {ysdh}")
    idx = {c: i for i, c in enumerate(cols)}
    header = rows[0]
    ysid = header[idx["F_ID"]] if "F_ID" in idx else None
    ysdzt_old = header[idx.get("F_YSDZT")] if "F_YSDZT" in idx else None
    if ysdzt_old and str(ysdzt_old) == "已接收":
        raise ValueError("该运输单已接收，无需重复操作")

    import json

    try:
        ybfz_ids = json.loads(header[idx["F_YSXX"]] or "[]")
        if not isinstance(ybfz_ids, list):
            ybfz_ids = []
        ybfz_ids = [int(x) for x in ybfz_ids if str(x).strip()]
    except Exception:
        ybfz_ids = []

    new_status = "已接收"
    with adapter.connect() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            UPDATE T_YSXX
            SET F_JSR = ?, F_JSSJ = ?, F_JSBZ = ?, F_YSDZT = ?
            WHERE F_YSDH = ?
            """,
            (jsr, jssj, jsbz, new_status, ysdh),
        )

        if ybfz_ids:
            placeholders = ",".join(["?"] * len(ybfz_ids))
            sql_update_ybfz = f"UPDATE T_YBFZ SET F_YSZT = ? WHERE F_ID IN ({placeholders})"
            cur.execute(sql_update_ybfz, tuple([new_status] + ybfz_ids))

        conn.commit()

    return {
        "F_YSDH": ysdh,
        "F_YSDZT": new_status,
        "F_JSR": jsr,
        "F_JSSJ": jssj,
        "F_JSBZ": jsbz,
        "F_YSXX": ybfz_ids,
        "F_ID": ysid,
    }


def get_sample_by_sgbh(payload: Dict[str, Any]) -> Dict[str, Any]:
    sgbh = (payload.get("F_SGBH") or "").strip()
    if not sgbh:
        raise ValueError("F_SGBH 不能为空")

    adapter = get_adapter()
    # 查分装 + 标本类型
    sql = """
    SELECT
        f.F_ID,
        f.F_SGBH,
        f.F_ZZID,
        f.F_DC,
        f.F_PYGD_ID,
        f.F_YBLX,
        y.F_BBLX
    FROM T_YBFZ f
    LEFT JOIN T_YBZJ y ON y.F_ID = f.F_YBZJ_ID
    WHERE f.F_SGBH = ?
    ORDER BY f.F_ID DESC
    """
    rows, cols = adapter.fetch_all(sql, [sgbh])
    if not rows:
        raise ValueError(f"未找到样本编号: {sgbh}")
    idx = {c: i for i, c in enumerate(cols)}
    r = rows[0]  # 取最新一条
    result = {
        "F_ID": r[idx["F_ID"]],
        "F_SGBH": r[idx["F_SGBH"]],
        "F_ZZID": r[idx.get("F_ZZID")],
        "F_DC": r[idx.get("F_DC")],
        "F_PYGD_ID": r[idx.get("F_PYGD_ID")],
        "F_YBLX": r[idx.get("F_YBLX")],
        "F_BBLX": r[idx.get("F_BBLX")] if "F_BBLX" in idx else None,
        "F_GDBH": None,
        "F_PYR": None,
    }

    pygd_id = result.get("F_PYGD_ID")
    if pygd_id is not None:
        try:
            sql_p = "SELECT F_GDBH, F_SQR AS F_PYR FROM T_PYGD WHERE F_ID = ?"
            rows_p, cols_p = adapter.fetch_all(sql_p, [pygd_id])
        except Exception:
            sql_p = "SELECT F_GDBH, NULL AS F_PYR FROM T_PYGD WHERE F_ID = ?"
            rows_p, cols_p = adapter.fetch_all(sql_p, [pygd_id])
        if rows_p:
            idx_p = {c: i for i, c in enumerate(cols_p)}
            result["F_GDBH"] = rows_p[0][idx_p.get("F_GDBH")]
            result["F_PYR"] = rows_p[0][idx_p.get("F_PYR")] if "F_PYR" in idx_p else None

    return result


def list_available_samples(payload: Dict[str, Any]) -> Dict[str, Any]:
    """
    查询可用的样本（F_YSZT为"未运送"的记录），用于运输单创建前的样本挑选
    类似于 unified_search/list，但只返回未运送的样本
    """
    adapter = get_adapter()
    skip = int(payload.get("skip") or 0)
    limit = int(payload.get("limit") or 50)

    filters: List[str] = []
    params: List[Any] = []

    # 固定条件：只查询未运送的样本
    # F_YSZT 为 "未运送" 或 NULL 或空字符串
    filters.append("(f.F_YSZT IS NULL OR f.F_YSZT = '' OR f.F_YSZT = ?)")
    params.append("未运送")

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


def list_transporting_samples(payload: Dict[str, Any]) -> Dict[str, Any]:
    """
    查询运输中的样本（F_YSZT为"运输中"的记录），用于接收方查询样本信息
    类似于 list_available_samples，但只返回运输中的样本
    """
    adapter = get_adapter()
    skip = int(payload.get("skip") or 0)
    limit = int(payload.get("limit") or 50)

    filters: List[str] = []
    params: List[Any] = []

    # 固定条件：只查询运输中的样本
    filters.append("f.F_YSZT = ?")
    params.append("运输中")

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

