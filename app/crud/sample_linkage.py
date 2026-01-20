from datetime import datetime
from typing import Any, Dict, List, Tuple

from app.db.factory import get_adapter


def _parse_datetime(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d %H:%M:%S")
    try:
        return datetime.fromisoformat(str(value)).strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return str(value)


def _parse_date(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d")
    try:
        return datetime.fromisoformat(str(value)).strftime("%Y-%m-%d")
    except Exception:
        return str(value)


def get_sample_linkage_lists(q: Dict[str, Any]) -> Dict[str, Any]:
    """
    查询标本和工单的统一列表。
    返回扁平化结构，每条记录来自 T_YBZJ 或 T_PYGD。
    """
    adapter = get_adapter()
    skip = int(q.get("skip") or 0)
    limit = int(q.get("limit") or 50)

    zzid = (q.get("F_ZZID") or "").strip()
    brbh = (q.get("F_BRBH") or "").strip()
    djrq_start = (q.get("F_DJRQ_START") or "").strip()
    djrq_end = (q.get("F_DJRQ_END") or "").strip()
    cxsj_start = (q.get("F_CXSJ_START") or "").strip()
    cxsj_end = (q.get("F_CXSJ_END") or "").strip()

    # 构建过滤条件（需要分别处理标本和工单）
    where_ybzj: List[str] = []
    params_ybzj: List[Any] = []
    where_pygd: List[str] = []
    params_pygd: List[Any] = []

    # 标本号过滤（两者共有）
    if zzid:
        pred_y, p_y = adapter.like("y.F_ZZID", zzid)
        where_ybzj.append(pred_y)
        params_ybzj.extend(p_y)
        
        pred_p, p_p = adapter.like("p.F_ZZID", zzid)
        where_pygd.append(pred_p)
        params_pygd.extend(p_p)
    
    # 病人编号过滤（两者共有）
    if brbh:
        pred_y, p_y = adapter.like("COALESCE(y.F_BRBH, y.F_ZYH)", brbh)
        where_ybzj.append(pred_y)
        params_ybzj.extend(p_y)
        
        pred_p, p_p = adapter.like("p.F_BRBH", brbh)
        where_pygd.append(pred_p)
        params_pygd.extend(p_p)
    
    # 登记日期过滤（仅标本）
    if djrq_start:
        where_ybzj.append("y.F_DJRQ >= ?")
        params_ybzj.append(djrq_start)
    if djrq_end:
        where_ybzj.append("y.F_DJRQ <= ?")
        params_ybzj.append(djrq_end)
    
    # 采集时间过滤（两者都需要，但工单通过关联查）
    if cxsj_start:
        where_ybzj.append("y.F_CXSJ >= ?")
        params_ybzj.append(cxsj_start)
        where_pygd.append("y.F_CXSJ >= ?")
        params_pygd.append(cxsj_start)
    if cxsj_end:
        where_ybzj.append("y.F_CXSJ <= ?")
        params_ybzj.append(cxsj_end)
        where_pygd.append("y.F_CXSJ <= ?")
        params_pygd.append(cxsj_end)

    where_ybzj_sql = " WHERE " + " AND ".join(where_ybzj) if where_ybzj else ""
    where_pygd_sql = " WHERE " + " AND ".join(where_pygd) if where_pygd else ""

    # 使用 UNION ALL 合并标本和工单查询
    # 标本查询（T_YBZJ）
    query_ybzj = f"""
    SELECT
        y.F_ID AS F_YBZJ_ID,
        NULL AS F_PYGD_ID,
        y.F_ZZID,
        NULL AS F_GDID,
        NULL AS F_GDBH,
        y.F_BBLX,
        y.F_XM,
        y.F_BBLY,
        y.F_XB,
        y.F_NL,
        COALESCE(y.F_BRBH, y.F_ZYH) AS F_BRBH,
        y.F_ZYH,
        y.F_CXSJ,
        y.F_DJRQ,
        'YBZJ' AS SOURCE_TYPE,
        y.F_ID AS SORT_ID
    FROM T_YBZJ y
    {where_ybzj_sql}
    """

    # 工单查询（T_PYGD）- 关联 T_YBZJ 获取采集时间
    query_pygd = f"""
    SELECT
        NULL AS F_YBZJ_ID,
        p.F_ID AS F_PYGD_ID,
        p.F_ZZID,
        p.F_ID AS F_GDID,
        p.F_GDBH,
        p.F_BBLX,
        p.F_XM,
        NULL AS F_BBLY,
        NULL AS F_XB,
        NULL AS F_NL,
        p.F_BRBH,
        NULL AS F_ZYH,
        y.F_CXSJ,
        NULL AS F_DJRQ,
        'PYGD' AS SOURCE_TYPE,
        p.F_ID AS SORT_ID
    FROM T_PYGD p
    LEFT JOIN T_YBZJ y ON y.F_ZZID = p.F_ZZID
    {where_pygd_sql}
    """

    # 合并查询（不带 ORDER BY，用于计数）
    union_query_base = f"""
    SELECT * FROM (
        {query_ybzj}
        UNION ALL
        {query_pygd}
    ) AS combined
    """

    # 计数查询（不包含 ORDER BY）
    count_query = f"SELECT COUNT(1) FROM ({union_query_base}) AS t"

    # 用于分页的查询（包含 ORDER BY）
    union_query_with_order = union_query_base + " ORDER BY SORT_ID DESC"
    
    # 分页查询
    paginated_query, extra_params = adapter.paginate(union_query_with_order, skip=skip, limit=limit)

    items: List[Dict[str, Any]] = []
    total = 0

    with adapter.connect() as conn:
        cur = conn.cursor()
        
        # 执行计数（UNION 中有两个子查询，分别使用各自的参数）
        union_params = params_ybzj + params_pygd
        cur.execute(count_query, tuple(union_params))
        total = int(cur.fetchone()[0])
        
        # 执行查询
        cur.execute(paginated_query, tuple(union_params + extra_params))
        cols = [c[0] for c in cur.description]
        rows = cur.fetchall()

        for row in rows:
            data = dict(zip(cols, row))
            
            # 格式化时间字段
            if data.get("F_CXSJ"):
                data["F_CXSJ"] = _parse_datetime(data["F_CXSJ"])
            if data.get("F_DJRQ"):
                data["F_DJRQ"] = _parse_date(data["F_DJRQ"])
            
            # 移除辅助字段
            data.pop("SOURCE_TYPE", None)
            data.pop("SORT_ID", None)
            
            items.append(data)

    return {"items": items, "total": total}


def _fetch_ybzj_by_ids(ids: List[int]) -> List[Dict[str, Any]]:
    if not ids:
        return []
    adapter = get_adapter()
    placeholders = ",".join(["?"] * len(ids))
    sql = f"""
SELECT F_ID, F_ZZID, F_BBLX,
       F_BRBH, F_ZYH, F_MZH, F_XM, F_XB, F_NL,
       NULL AS F_NLDW,
       NULL AS F_MZ, NULL AS F_HY, NULL AS F_ZY, NULL AS F_DZ, NULL AS F_DH, NULL AS F_CHH, NULL AS F_SFZH,
       NULL AS F_LCZD, NULL AS F_LYKS, NULL AS F_KS, F_CXSJ, NULL AS F_XMBH
FROM T_YBZJ
WHERE F_ID IN ({placeholders})
"""
    rows, cols = adapter.fetch_all(sql, [int(i) for i in ids])
    return [dict(zip(cols, row)) for row in rows]


def _fetch_pygd_by_ids(ids: List[int]) -> List[Dict[str, Any]]:
    if not ids:
        return []
    adapter = get_adapter()
    placeholders = ",".join(["?"] * len(ids))
    sql = f"""
SELECT p.F_ID, p.F_ZZID, p.F_DC, p.F_BBLX,
       p.F_BRBH, NULL AS F_ZYH, NULL AS F_MZH, p.F_XM, NULL AS F_XB, NULL AS F_NL, NULL AS F_NLDW,
       NULL AS F_MZ, NULL AS F_HY, NULL AS F_ZY, NULL AS F_DZ, NULL AS F_DH, NULL AS F_CHH, NULL AS F_SFZH,
       NULL AS F_LCZD, NULL AS F_LYKS, NULL AS F_KS, y.F_CXSJ, NULL AS F_XMBH
FROM T_PYGD p
LEFT JOIN T_YBZJ y ON y.F_ZZID = p.F_ZZID
WHERE p.F_ID IN ({placeholders})
"""
    rows, cols = adapter.fetch_all(sql, [int(i) for i in ids])
    return [dict(zip(cols, row)) for row in rows]


def _resolve_operator(user: Dict[str, Any] | None, adapter) -> str | None:
    if not user:
        return None

    for key in ("F_KTYPE", "F_Ktype"):
        val = user.get(key)
        if val:
            return str(val)

    user_id = user.get("F_ID") if isinstance(user, dict) else None
    if user_id is None:
        return None

    from app.crud.user import _name_column  # reuse helper
    col = _name_column(adapter, quoted=True)

    try:
        sql = f"SELECT {col} FROM T_USER WHERE F_ID = ?"
        paginated_sql, extra = adapter.paginate(sql, 0, 1)
        rows, _ = adapter.fetch_all(paginated_sql, [user_id] + extra)
        if rows:
            val = rows[0][0]
            if val:
                return str(val)
    except Exception:
        pass

    return str(user_id)


def preview_sample_allocation(payload: Dict[str, Any]) -> Dict[str, Any]:
    ybzj_ids: List[int] = payload.get("YBZJ_IDS") or []
    pygd_ids: List[int] = payload.get("PYGD_IDS") or []
    infos_raw = payload.get("FZXX")
    default_hzh = payload.get("F_HZH")  # 外围默认盒号

    if not ybzj_ids and not pygd_ids:
        raise ValueError("请至少选择一个样本或工单")

    if isinstance(infos_raw, dict):
        infos = [infos_raw]
    elif isinstance(infos_raw, list):
        infos = infos_raw
    else:
        infos = []

    if not infos:
        raise ValueError("请至少提供一组分装信息")

    for idx, info in enumerate(infos, start=1):
        fgs = info.get("F_FGS")
        if not isinstance(fgs, int) or fgs <= 0:
            raise ValueError(f"第{idx}组分装信息的分管数必须为正整数")
        
        # 优先使用 FZXX 内的盒号，否则使用外围默认盒号；位置信息不校验
        hzh = info.get("F_HZH") or default_hzh
        info["_computed_hzh"] = hzh

    ybzj_rows = _fetch_ybzj_by_ids(ybzj_ids)
    pygd_rows = _fetch_pygd_by_ids(pygd_ids)

    if len(ybzj_rows) != len(ybzj_ids):
        missing = set(ybzj_ids) - {int(r.get("F_ID")) for r in ybzj_rows}
        if missing:
            raise ValueError(f"找不到以下 YBZJ 记录: {sorted(missing)}")
    if len(pygd_rows) != len(pygd_ids):
        missing = set(pygd_ids) - {int(r.get("F_ID")) for r in pygd_rows}
        if missing:
            raise ValueError(f"找不到以下工单记录: {sorted(missing)}")

    samples: List[Tuple[str, str, Dict[str, Any]]] = []

    for row in ybzj_rows:
        zzid = row.get("F_ZZID")
        if not zzid:
            continue
        base_info = {
            "F_YBZJ_ID": int(row.get("F_ID")),
            "F_ZZID": str(zzid),
            "F_DC": None,
            "F_YBLX": row.get("F_BBLX"),
            "F_BRBH": row.get("F_BRBH"),
            "F_ZYH": row.get("F_ZYH"),
            "F_MZH": row.get("F_MZH"),
            "F_XM": row.get("F_XM"),
            "F_XB": row.get("F_XB"),
            "F_NL": row.get("F_NL"),
            "F_NLDW": row.get("F_NLDW"),
            "F_MZ": row.get("F_MZ"),
            "F_HY": row.get("F_HY"),
            "F_ZY": row.get("F_ZY"),
            "F_DZ": row.get("F_DZ"),
            "F_DH": row.get("F_DH"),
            "F_CHH": row.get("F_CHH"),
            "F_SFZH": row.get("F_SFZH"),
            "F_LCZD": row.get("F_LCZD"),
            "F_LYKS": row.get("F_LYKS"),
            "F_KS": row.get("F_KS"),
            "F_CXSJ": row.get("F_CXSJ"),
            "F_XMBH": row.get("F_XMBH"),
        }
        samples.append((str(zzid), "BB", base_info))

    for row in pygd_rows:
        zzid = row.get("F_ZZID")
        if not zzid:
            continue
        base_info = {
            "F_PYGD_ID": int(row.get("F_ID")),
            "F_ZZID": str(zzid),
            "F_DC": row.get("F_DC"),
            "F_YBLX": row.get("F_BBLX"),
            # 通过 T_PYGD / T_YBZJ(左联) 尽可能带出患者信息
            "F_BRBH": row.get("F_BRBH"),
            "F_ZYH": row.get("F_ZYH"),
            "F_MZH": row.get("F_MZH"),
            "F_XM": row.get("F_XM"),
            "F_XB": row.get("F_XB"),
            "F_NL": row.get("F_NL"),
            "F_NLDW": row.get("F_NLDW"),
            "F_MZ": row.get("F_MZ"),
            "F_HY": row.get("F_HY"),
            "F_ZY": row.get("F_ZY"),
            "F_DZ": row.get("F_DZ"),
            "F_DH": row.get("F_DH"),
            "F_CHH": row.get("F_CHH"),
            "F_SFZH": row.get("F_SFZH"),
            "F_LCZD": row.get("F_LCZD"),
            "F_LYKS": row.get("F_LYKS"),
            "F_KS": row.get("F_KS"),
            "F_CXSJ": row.get("F_CXSJ"),
            "F_XMBH": row.get("F_XMBH"),
        }
        samples.append((str(zzid), "GD", base_info))

    if not samples:
        raise ValueError("未检索到有效的样本数据")

    items: List[Dict[str, Any]] = []
    ts = datetime.now().strftime("%Y%m%d%H%M%S")
    sequence_counters: Dict[Tuple[str, str], int] = {}
    
    for info in infos:
        fgs = int(info.get("F_FGS"))
        # 使用计算后的盒号（已考虑外围默认值和内部覆盖）
        hzh = info.get("_computed_hzh")
        hs = info.get("F_HS")    # 行数
        ls = info.get("F_LS")    # 列数
        wz = info.get("F_WZ")
        sfys_raw = info.get("F_SFYS")
        if sfys_raw is None:
            sfys = True  # 未提供则默认需要运输
        else:
            sfys = bool(sfys_raw)
        
        for zzid, source_flag, base in samples:
            key = (zzid, source_flag)
            start_seq = sequence_counters.get(key, 0)
            for offset in range(1, fgs + 1):
                seq = start_seq + offset
                sg = base.copy()
                sg["F_SGBH"] = f"{zzid}-{source_flag}-{ts}-{seq}"
                sg["F_CCXS"] = info.get("F_CCXS")
                sg["F_FGS"] = fgs
                sg["F_TJ"] = info.get("F_TJ")
                sg["F_ND"] = info.get("F_ND")
                sg["F_SPREC"] = info.get("F_SPREC")
                sg["F_BZ"] = info.get("F_BZ")
                sg["F_WZ"] = wz
                sg["F_SFYS"] = sfys
                
                # 添加盒号、行数、列数
                sg["F_HZH"] = hzh
                sg["F_HS"] = hs
                sg["F_LS"] = ls
                
                if info.get("F_YBLX"):
                    sg["F_YBLX"] = info.get("F_YBLX")
                items.append(sg)
            sequence_counters[key] = start_seq + fgs

    return {"items": items, "total": len(items)}


def get_tube_by_sgbh(sgbh: str) -> Dict[str, Any]:
    """
    根据样本管编号查询样本分装详情
    
    Args:
        sgbh: 样本管编号 (F_SGBH)
    
    Returns:
        包含样本管详情和运输状态的字典
        
    Raises:
        ValueError: 未找到对应的样本管
    """
    if not sgbh or not sgbh.strip():
        raise ValueError("样本管编号不能为空")
    
    sgbh = sgbh.strip()
    adapter = get_adapter()
    
    # 查询样本分装表
    sql = """
    SELECT 
        F_ID, F_SGBH, F_ZZID, F_YBZJ_ID, F_PYGD_ID, F_DC,
        F_YBLX, F_CCXS, F_FGS, F_TJ, F_ND, F_SPREC,
        F_BZ, F_CZR, F_CJSJ, F_HZH, F_HS, F_LS, F_WZ,
        F_BRBH, F_ZYH, F_MZH, F_XM, F_XB, F_NL, F_NLDW,
        F_MZ, F_HY, F_ZY, F_DZ, F_DH, F_CHH, F_SFZH,
        F_LCZD, F_LYKS, F_KS, F_CXSJ, F_XMBH,
        F_YSZT
    FROM T_YBFZ
    WHERE F_SGBH = ?
    """
    
    with adapter.connect() as conn:
        cur = conn.cursor()
        cur.execute(sql, (sgbh,))
        row = cur.fetchone()
        
        if not row:
            raise ValueError(f"未找到样本管编号: {sgbh}")
        
        cols = [c[0] for c in cur.description]
        data = dict(zip(cols, row))
        
        # 格式化时间字段
        if data.get("F_CJSJ"):
            data["F_CJSJ"] = _parse_datetime(data["F_CJSJ"])
        if data.get("F_CXSJ"):
            data["F_CXSJ"] = _parse_datetime(data["F_CXSJ"])
        
        # 检查运输状态
        yszt = data.get("F_YSZT") or "未运送"
        is_transported = (yszt == "运送中")
        
        # 如果已运输，查询关联的运输单信息
        transport_info = None
        if is_transported:
            ybfz_id = data.get("F_ID")
            # 查询包含此分装ID的运输单
            # F_YSXX 是 JSON 数组字符串，需要用 LIKE 查询
            transport_sql = """
            SELECT F_YSDH, F_YSXX, F_FQR, F_FQSJ, F_YSDZT, F_YBSL, F_BZ
            FROM T_YSXX
            WHERE F_YSXX LIKE ?
            """
            # 使用模糊查询匹配 JSON 数组中的 ID
            like_pattern = f'%{ybfz_id}%'
            cur.execute(transport_sql, (like_pattern,))
            transport_rows = cur.fetchall()
            
            # 找到确实包含此ID的运输单（精确匹配JSON数组）
            import json
            for t_row in transport_rows:
                t_cols = [c[0] for c in cur.description]
                t_data = dict(zip(t_cols, t_row))
                try:
                    ysxx_json = t_data.get("F_YSXX")
                    if ysxx_json:
                        ysxx_list = json.loads(ysxx_json)
                        if ybfz_id in ysxx_list:
                            # 找到匹配的运输单
                            transport_info = {
                                "F_YSDH": t_data.get("F_YSDH"),
                                "F_FQR": t_data.get("F_FQR"),
                                "F_FQSJ": _parse_datetime(t_data.get("F_FQSJ")),
                                "F_YSDZT": t_data.get("F_YSDZT"),
                                "F_YBSL": t_data.get("F_YBSL"),
                                "F_BZ": t_data.get("F_BZ"),
                            }
                            break
                except Exception:
                    continue
        
        result = {
            "tube_info": data,
            "is_transported": is_transported,
            "transport_info": transport_info
        }
        
        return result


def save_sample_allocation(payload: Dict[str, Any], user: Dict[str, Any] | None = None) -> Dict[str, Any]:
    # 兼容大小写：ITEMS 或 items
    items_override = payload.get("ITEMS") or payload.get("items") or []
    items: List[Dict[str, Any]] = []

    if items_override:
        if isinstance(items_override, dict):
            items_override = [items_override]
        for idx, it in enumerate(items_override, start=1):
            if not it.get("F_SGBH"):
                raise ValueError(f"第{idx}条ITEMS缺少F_SGBH")
            obj = dict(it)
            obj["F_SFYS"] = bool(obj.get("F_SFYS"))
            items.append(obj)
    else:
        data = preview_sample_allocation(payload)
        items = data.get("items") or []

    if not items:
        raise ValueError("没有可保存的分装数据")

    adapter = get_adapter()
    czr = _resolve_operator(user, adapter)

    inserted_ids: List[int] = []
    transport_ids: List[int] = []

    with adapter.connect() as conn:
        cur = conn.cursor()
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        for item in items:
            yszt = "运送中" if item.get("F_SFYS") else "未运送"
            cur.execute(
                """
                INSERT INTO T_YBFZ (
                    F_SGBH, F_ZZID, F_YBZJ_ID, F_PYGD_ID, F_DC,
                    F_YBLX, F_CCXS, F_FGS, F_TJ, F_ND, F_SPREC,
                    F_BZ, F_CZR, F_CJSJ, F_HZH, F_HS, F_LS,
                    F_BRBH, F_ZYH, F_MZH, F_XM, F_XB, F_NL, F_NLDW,
                    F_MZ, F_HY, F_ZY, F_DZ, F_DH, F_CHH, F_SFZH,
                    F_LCZD, F_LYKS, F_KS, F_CXSJ, F_XMBH,
                    F_WZ, F_YSZT
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    item.get("F_SGBH"),
                    item.get("F_ZZID"),
                    item.get("F_YBZJ_ID"),
                    item.get("F_PYGD_ID"),
                    item.get("F_DC"),
                    item.get("F_YBLX"),
                    item.get("F_CCXS"),
                    item.get("F_FGS"),
                    item.get("F_TJ"),
                    item.get("F_ND"),
                    item.get("F_SPREC"),
                    item.get("F_BZ"),
                    czr,
                    now_str,
                    item.get("F_HZH"),  # 盒号
                    item.get("F_HS"),   # 行数
                    item.get("F_LS"),   # 列数
                    item.get("F_BRBH"),
                    item.get("F_ZYH"),
                    item.get("F_MZH"),
                    item.get("F_XM"),
                    item.get("F_XB"),
                    item.get("F_NL"),
                    item.get("F_NLDW"),
                    item.get("F_MZ"),
                    item.get("F_HY"),
                    item.get("F_ZY"),
                    item.get("F_DZ"),
                    item.get("F_DH"),
                    item.get("F_CHH"),
                    item.get("F_SFZH"),
                    item.get("F_LCZD"),
                    item.get("F_LYKS"),
                    item.get("F_KS"),
                    item.get("F_CXSJ"),
                    item.get("F_XMBH"),
                    item.get("F_WZ"),
                    yszt,
                ),
            )
            lookup_sql = "SELECT F_ID FROM T_YBFZ WHERE F_SGBH = ? ORDER BY F_ID DESC"
            paginated_sql, extra = adapter.paginate(lookup_sql, 0, 1)
            cur.execute(paginated_sql, (item.get("F_SGBH"),) + tuple(extra))
            row = cur.fetchone()
            new_id = row[0] if row else None
            if new_id is not None:
                inserted_ids.append(int(new_id))
                if item.get("F_SFYS"):
                    transport_ids.append(int(new_id))
        conn.commit()

    transport_info = None
    if transport_ids:
        import json
        from datetime import datetime as _dt
        ysdh = f"YSDH{_dt.now().strftime('%Y%m%d%H%M%S%f')}"
        ysxx_json = json.dumps(transport_ids, ensure_ascii=False)
        ysr = payload.get("F_FQR") or czr
        yssj = payload.get("F_FQSJ") or now_str
        ysbz = payload.get("F_BZ")
        ysmdd = payload.get("F_YSMDD")  # 运输目的地
        ysdzt = "运送中"

        with adapter.connect() as conn:
            cur = conn.cursor()
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
                    len(transport_ids),
                    ysbz,
                    None,
                    None,
                    None,
                    ysmdd,
                ),
            )
            # 同步运输目的地到关联的样本
            if ysmdd:
                placeholders = ",".join(["?"] * len(transport_ids))
                update_sql = f"UPDATE T_YBFZ SET F_YSMDD = ? WHERE F_ID IN ({placeholders})"
                cur.execute(update_sql, tuple([ysmdd] + transport_ids))
            conn.commit()
        transport_info = {"F_YSDH": ysdh, "F_YSXX": transport_ids, "F_YSDZT": ysdzt}

    result = {"inserted": inserted_ids, "count": len(inserted_ids)}
    if transport_info:
        result["transport"] = transport_info
    return result

