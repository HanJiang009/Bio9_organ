from typing import Dict, Any, List, Optional, Tuple
import copy
from datetime import datetime
from app.db.factory import get_adapter


# ==================== 工具函数（复用自 drug_test）====================

def _safe_encode_for_db(value: Any) -> Any:
    """将字符串中的特殊 Unicode 字符转换为 GBK 兼容字符"""
    if not isinstance(value, str):
        return value
    replacements = {
        '×': 'x', '⁰': '0', '¹': '1', '²': '2', '³': '3', '⁴': '4',
        '⁵': '5', '⁶': '6', '⁷': '7', '⁸': '8', '⁹': '9',
        '₀': '0', '₁': '1', '₂': '2', '₃': '3', '₄': '4',
        '₅': '5', '₆': '6', '₇': '7', '₈': '8', '₉': '9',
        'μ': 'u', '±': '+/-', '°': 'deg', '≤': '<=', '≥': '>=', '≈': '~',
    }
    result = value
    for old, new in replacements.items():
        result = result.replace(old, new)
    try:
        result.encode('gbk')
        return result
    except UnicodeEncodeError:
        return result.encode('gbk', errors='ignore').decode('gbk')


def _clean_data_for_db(data: Dict[str, Any]) -> Dict[str, Any]:
    """递归清理数据中的特殊字符"""
    cleaned = {}
    for key, value in data.items():
        if isinstance(value, str):
            cleaned[key] = _safe_encode_for_db(value)
        elif isinstance(value, dict):
            cleaned[key] = _clean_data_for_db(value)
        elif isinstance(value, list):
            cleaned[key] = [
                _clean_data_for_db(item) if isinstance(item, dict)
                else _safe_encode_for_db(item) if isinstance(item, str)
                else item
                for item in value
            ]
        else:
            cleaned[key] = value
    return cleaned


def _parse_datetime(value: Any) -> Optional[str]:
    """解析时间字段为字符串"""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d %H:%M:%S")
    if isinstance(value, str):
        return value
    return str(value)


def _generate_animal_test_plan_code() -> str:
    """
    生成动物实验编号
    格式：DWSY + YYYYMMDDHHMMSS + 毫秒(3位)
    例如：DWSY20251120153045123
    """
    return "DWSY" + datetime.now().strftime("%Y%m%d%H%M%S%f")[:-3]


# ==================== 接种信息写入辅助 ====================

def _insert_inoculation_records(cur, plan_id: int, jzxx: List[Dict[str, Any]]):
    """批量插入动物接种信息"""
    for idx, item in enumerate(jzxx):
        sort_val = item.get("F_SORT") if item.get("F_SORT") is not None else (idx + 1)
        cur.execute("""
            INSERT INTO T_DWSY_JZ (
                F_SYJH_ID, F_DWBH, F_XB, F_NL, F_LH, F_ZB,
                F_JHYZSJ, F_JHJZXBS, F_JHYZBW, F_JHCZR, F_SORT
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            plan_id,
            item.get("F_DWBH"),
            item.get("F_XB"),
            item.get("F_NL"),
            item.get("F_LH"),
            item.get("F_ZB") or item.get("F_ZBMC"),
            item.get("F_JHYZSJ"),
            item.get("F_JHJZXBS"),
            item.get("F_JHYZBW"),
            item.get("F_JHCZR"),
            sort_val,
        ))


# ==================== 创建实验计划 ====================

def create_animal_test_plan(data: Dict[str, Any], operator: Optional[str] = None, from_pipeline_template: bool = False) -> Dict[str, Any]:
    """创建动物实验计划"""
    import logging
    logger = logging.getLogger(__name__)

    adapter = get_adapter()
    data = _clean_data_for_db(data)

    # ========== 前置参数校验 ==========
    if not data.get("F_GDID"):
        raise ValueError("F_GDID（关联工单ID）不能为空")

    sybh = _generate_animal_test_plan_code()

    with adapter.connect() as conn:
        cur = conn.cursor()

        # 1. 插入主表
        from app.utils.yhbc import yhbc_flag
        main_fields = {
            "F_GDID":    data.get("F_GDID"),
            "F_SYBH":    sybh,
            "F_SYMC":    data.get("F_SYMC"),
            "F_ZT":      "已创建",
            "F_SYKSRQ":  data.get("F_SYKSRQ"),
            "F_SYR":     data.get("F_SYR"),
            "F_SYJHYMD": data.get("F_SYJHYMD"),
            "F_XBSL":   data.get("F_XBSL"),
            "F_JZJND":   data.get("F_JZJND"),
            "F_JZJTJ":   data.get("F_JZJTJ"),
            "F_FJ":      data.get("F_FJ"),
            "F_BZ":      data.get("F_BZ"),
            "F_CJSJ":    datetime.now(),
            "F_YHBC":    yhbc_flag(from_pipeline_template),
        }
        cols = [k for k, v in main_fields.items() if v is not None]
        vals = [main_fields[k] for k in cols]
        insert_sql = f"INSERT INTO T_DWSY_JH ({', '.join(cols)}) VALUES ({', '.join(['?' for _ in cols])})"
        try:
            cur.execute(insert_sql, tuple(vals))
        except Exception:
            main_fields.pop("F_YHBC", None)
            cols = [k for k, v in main_fields.items() if v is not None]
            vals = [main_fields[k] for k in cols]
            insert_sql = f"INSERT INTO T_DWSY_JH ({', '.join(cols)}) VALUES ({', '.join(['?' for _ in cols])})"
            cur.execute(insert_sql, tuple(vals))

        # 获取新增主表 ID（通过唯一编号定位，避免 F_GDID=None 时的 NULL 匹配问题）
        cur.execute("SELECT MAX(F_ID) FROM T_DWSY_JH WHERE F_SYBH = ?", (sybh,))
        plan_id = cur.fetchone()[0]
        if plan_id is None:
            raise ValueError(f"创建动物实验计划失败：无法获取新增记录ID（SYBH={sybh}）")

        # 2. 插入动物接种信息
        jzxx = data.get("JZXX") or []
        _insert_inoculation_records(cur, plan_id, jzxx)

        conn.commit()

    # ========== 创建质控记录（新增）==========
    qc_audit_id = None
    try:
        from app.crud.qc_audit import create_qc_audit_record, get_sample_basic_info

        gdid = data.get("F_GDID")
        with adapter.connect() as conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT F_ZZID, F_DCID, F_DC, F_GDBH, F_SQR, F_PYSJ, F_YBLY FROM T_PYGD WHERE F_ID = ?",
                (gdid,)
            )
            gd_row = cur.fetchone()

        if gd_row:
            zzid, dcid, dc, gdbh, sqr, pysj, ybly = gd_row
            sample_info = get_sample_basic_info(zzid) if zzid else {}
            new_detail_data = get_animal_test_plan_detail(plan_id)

            changes = {
                "变更字段": [
                    {"字段": "实验名称",   "新值": data.get("F_SYMC")},
                    {"字段": "实验编号",   "新值": sybh},
                    {"字段": "实验开始日期", "新值": data.get("F_SYKSRQ")},
                    {"字段": "实验人",     "新值": data.get("F_SYR")},
                    {"字段": "接种动物数量", "新值": len(jzxx)},
                ]
            }

            qc_audit_id = create_qc_audit_record(
                F_ZKJD="动物实验计划",
                F_BGLX="新增",
                F_ZZID=zzid,
                F_CHANGES=changes,
                F_JLR=operator or "系统",
                F_GDID=gdid,
                F_XM=sample_info.get("F_XM"),
                F_BRBH=sample_info.get("F_BRBH"),
                F_YBLY=ybly,
                F_GDBH=gdbh,
                F_DCID=dcid,
                F_DC=dc,
                F_PYSJ=pysj,
                F_SQR=sqr,
                F_OLD_DATA=new_detail_data,
                from_pipeline_template=from_pipeline_template,
            )
            logger.info(f"[动物实验计划质控] 新增质控记录创建成功，计划ID: {plan_id}, 质控ID: {qc_audit_id}")
    except Exception as e:
        logger.error(f"[动物实验计划质控] 创建新增质控记录失败（计划ID: {plan_id}）: {e}", exc_info=True)

    result = {
        "F_ID":   plan_id,
        "F_GDID": data.get("F_GDID"),
        "F_SYBH": sybh,
        "F_SYMC": data.get("F_SYMC"),
    }
    if qc_audit_id:
        result["qc_audit_id"] = qc_audit_id
    return result


# ==================== 获取实验计划详情 ====================

def get_animal_test_plan_detail(plan_id: int) -> Optional[Dict[str, Any]]:
    """获取动物实验计划详情"""
    adapter = get_adapter()

    with adapter.connect() as conn:
        cur = conn.cursor()

        # 1. 查询主表
        cur.execute("""
            SELECT F_ID, F_GDID, F_SYBH, F_SYMC, F_ZT, F_SYKSRQ, F_SYR, F_SYJHYMD,
                   F_XBSL, F_JZJND, F_JZJTJ, F_FJ, F_BHG, F_BHGYY, F_CJSJ, F_BZ
            FROM T_DWSY_JH
            WHERE F_ID = ?
        """, (plan_id,))
        row = cur.fetchone()
        if not row:
            return None

        cols = [c[0] for c in cur.description]
        main_data = dict(zip(cols, row))
        main_data["F_SYKSRQ"] = _parse_datetime(main_data.get("F_SYKSRQ"))
        main_data["F_CJSJ"]   = _parse_datetime(main_data.get("F_CJSJ"))

        # 附件 ID → 静态路径
        if main_data.get("F_FJ"):
            try:
                from app.crud.file_center import get_files_by_ids
                file_id = int(main_data["F_FJ"])
                files = get_files_by_ids([file_id])
                main_data["F_FJ"] = files[0].get("F_PATH") if files else None
            except Exception:
                main_data["F_FJ"] = None

        # 2. 查询动物接种信息
        cur.execute("""
            SELECT F_ID, F_DWBH, F_XB, F_NL, F_LH, F_ZB,
                   F_JHYZSJ, F_JHJZXBS, F_JHYZBW, F_JHCZR, F_SORT
            FROM T_DWSY_JZ
            WHERE F_SYJH_ID = ?
            ORDER BY F_SORT, F_ID
        """, (plan_id,))
        jzxx = []
        for r in cur.fetchall():
            jzxx.append({
                "F_ID":       r[0],
                "F_DWBH":     r[1],
                "F_XB":       r[2],
                "F_NL":       r[3],
                "F_LH":       r[4],
                "F_ZB":       r[5],
                "F_JHYZSJ":   _parse_datetime(r[6]),
                "F_JHJZXBS":  r[7],
                "F_JHYZBW":   r[8],
                "F_JHCZR":    r[9],
                "F_SORT":     r[10],
            })
        main_data["JZXX"] = jzxx

        # 3. 查询工单样本信息
        gdid = main_data.get("F_GDID")
        if gdid:
            try:
                cur.execute("""
                    SELECT F_ZZID, F_DCID, F_DC, F_GDBH, F_YBLY, F_SQR, F_PYSJ, F_ZZBW
                    FROM T_PYGD
                    WHERE F_ID = ?
                """, (gdid,))
                gd_row = cur.fetchone()
                if gd_row:
                    main_data["GDXX"] = {
                        "F_ZZID": gd_row[0],
                        "F_DCID": gd_row[1],
                        "F_DC":   gd_row[2],
                        "F_GDBH": gd_row[3],
                        "F_YBLY": gd_row[4],
                        "F_SQR":  gd_row[5],
                        "F_PYSJ": _parse_datetime(gd_row[6]),
                        "F_ZZBW": gd_row[7],
                    }
                else:
                    main_data["GDXX"] = None
            except Exception:
                main_data["GDXX"] = None
        else:
            main_data["GDXX"] = None

        return main_data


# ==================== 查询列表 ====================

def list_animal_test_plans(query: Dict[str, Any], user: Dict[str, Any] | None = None,
                          scope: str | None = None) -> Tuple[List[Dict[str, Any]], int]:
    """查询动物实验计划列表"""
    adapter = get_adapter()

    skip  = query.get("skip", 0)
    limit = query.get("limit", 20)

    conditions: List[str] = []
    params: List[Any] = []

    if query.get("F_GDID"):
        conditions.append("main.F_GDID = ?")
        params.append(query["F_GDID"])
    if query.get("F_SYBH"):
        conditions.append("main.F_SYBH LIKE ?")
        params.append(f"%{query['F_SYBH']}%")
    if query.get("F_SYMC"):
        conditions.append("main.F_SYMC LIKE ?")
        params.append(f"%{query['F_SYMC']}%")
    if query.get("F_SYR"):
        conditions.append("main.F_SYR LIKE ?")
        params.append(f"%{query['F_SYR']}%")
    if query.get("F_ZT"):
        conditions.append("main.F_ZT = ?")
        params.append(query["F_ZT"])
    if query.get("F_ZZID"):
        conditions.append("gd.F_ZZID LIKE ?")
        params.append(f"%{query['F_ZZID']}%")
    if query.get("F_GDBH"):
        conditions.append("gd.F_GDBH LIKE ?")
        params.append(f"%{query['F_GDBH']}%")
    if query.get("F_DCID"):
        conditions.append("gd.F_DCID LIKE ?")
        params.append(f"%{query['F_DCID']}%")
    if query.get("F_ZZBW"):
        conditions.append("gd.F_ZZBW LIKE ?")
        params.append(f"%{query['F_ZZBW']}%")
    if query.get("F_SQR"):
        conditions.append("gd.F_SQR LIKE ?")
        params.append(f"%{query['F_SQR']}%")

    from app.core.data_scope import apply_owner_filter
    apply_owner_filter(conditions, params, scope=scope, user=user, column="main.F_SYR", match="name")

    where_clause = " AND ".join(conditions) if conditions else "1=1"

    count_sql = f"""
        SELECT COUNT(*)
        FROM T_DWSY_JH main
        LEFT JOIN T_PYGD gd ON main.F_GDID = gd.F_ID
        WHERE {where_clause}
    """
    list_sql = f"""
        SELECT
            main.F_ID, main.F_GDID, main.F_SYBH, main.F_SYMC, main.F_ZT,
            main.F_SYKSRQ, main.F_SYR, main.F_CJSJ, main.F_BG_PDF,
            (SELECT COUNT(*) FROM T_DWSY_JZ WHERE F_SYJH_ID = main.F_ID) AS F_ANIMAL_COUNT,
            gd.F_ZZID, gd.F_DCID, gd.F_GDBH, gd.F_ZZBW, gd.F_YBLY, gd.F_SQR, gd.F_PYSJ
        FROM T_DWSY_JH main
        LEFT JOIN T_PYGD gd ON main.F_GDID = gd.F_ID
        WHERE {where_clause}
        ORDER BY main.F_CJSJ DESC
    """

    with adapter.connect() as conn:
        cur = conn.cursor()

        cur.execute(count_sql, tuple(params))
        total = cur.fetchone()[0]

        paginated_sql, extra = adapter.paginate(list_sql, skip=skip, limit=limit)
        cur.execute(paginated_sql, tuple(params + extra))
        rows = cur.fetchall()

        items = []
        for row in rows:
            item = {
                "F_ID":           row[0],
                "F_GDID":         row[1],
                "F_SYBH":         row[2],
                "F_SYMC":         row[3],
                "F_ZT":           row[4],
                "F_SYKSRQ":       _parse_datetime(row[5]),
                "F_SYR":          row[6],
                "F_CJSJ":         _parse_datetime(row[7]),
                "F_BG_PDF":       None,   # 报告 PDF 预留，后续报告模块填充
                "F_ANIMAL_COUNT": row[9] or 0,
                "F_ZZID":         row[10],
                "F_DCID":         row[11],
                "F_GDBH":         row[12],
                "F_ZZBW":         row[13],
                "F_YBLY":         row[14],
                "F_SQR":          row[15],
                "F_PYSJ":         _parse_datetime(row[16]),
            }
            items.append(item)

        return items, total


# ==================== 更新实验计划 ====================

def update_animal_test_plan(data: Dict[str, Any], operator: Optional[str] = None) -> Dict[str, Any]:
    """
    更新动物实验计划

    状态限制：
    - 已创建：可修改所有字段 + 接种信息
    - 进行中：只能修改记录性字段（实验名称、实验人、实验目的、附件、备注）
    - 已完成/不合格：禁止修改
    """
    import logging
    logger = logging.getLogger(__name__)

    data = _clean_data_for_db(data)
    adapter = get_adapter()
    plan_id = data.get("F_ID")
    if not plan_id:
        raise ValueError("缺少实验计划ID")

    # 记录修改前的完整数据（用于质控变更对比）
    old_detail_data = get_animal_test_plan_detail(plan_id)

    with adapter.connect() as conn:
        cur = conn.cursor()

        cur.execute("SELECT F_ZT FROM T_DWSY_JH WHERE F_ID = ?", (plan_id,))
        row = cur.fetchone()
        if not row:
            raise ValueError("实验计划不存在")

        current_status = row[0]
        if current_status in ("已完成", "不合格"):
            raise ValueError(f"'{current_status}'状态的实验计划不允许修改")

        # 1. 更新主表
        if current_status == "已创建":
            allowed_fields = [
                "F_SYMC", "F_SYKSRQ", "F_SYR", "F_SYJHYMD",
                "F_XBSL", "F_JZJND", "F_JZJTJ", "F_FJ", "F_ZT", "F_BZ",
            ]
        else:  # 进行中
            allowed_fields = ["F_SYMC", "F_SYR", "F_SYJHYMD", "F_FJ", "F_ZT", "F_BZ"]

        update_fields = {f: data[f] for f in allowed_fields if data.get(f) is not None}
        # 用户更新计划 → 标记已保存
        update_fields["F_YHBC"] = 1
        if update_fields:
            try:
                set_clause = ", ".join([f"{k} = ?" for k in update_fields])
                params = list(update_fields.values()) + [plan_id]
                cur.execute(f"UPDATE T_DWSY_JH SET {set_clause} WHERE F_ID = ?", tuple(params))
            except Exception:
                update_fields.pop("F_YHBC", None)
                if update_fields:
                    set_clause = ", ".join([f"{k} = ?" for k in update_fields])
                    params = list(update_fields.values()) + [plan_id]
                    cur.execute(f"UPDATE T_DWSY_JH SET {set_clause} WHERE F_ID = ?", tuple(params))

        # 2. 增量更新接种信息（仅"已创建"状态允许）
        jzxx_changed = data.get("JZXX") is not None or bool(data.get("JZXX_DEL_IDS"))

        if jzxx_changed:
            if current_status == "进行中":
                raise ValueError("进行中的实验计划不允许修改动物接种信息")

            # Step 1：删除指定接种记录
            del_ids = data.get("JZXX_DEL_IDS") or []
            for jz_id in del_ids:
                cur.execute(
                    "SELECT F_ID FROM T_DWSY_JZ WHERE F_ID = ? AND F_SYJH_ID = ?",
                    (jz_id, plan_id)
                )
                if not cur.fetchone():
                    raise ValueError(f"接种记录 ID={jz_id} 不存在或不属于该实验计划，无法删除")
                cur.execute("DELETE FROM T_DWSY_JZ WHERE F_ID = ?", (jz_id,))

            # Step 2：处理 JZXX 中的新增 / 更新
            jzxx = data.get("JZXX") or []
            new_sort_counter = 1
            for item in jzxx:
                if item.get("F_ID") is None and item.get("F_SORT") is None:
                    item["F_SORT"] = new_sort_counter
                new_sort_counter += 1

            for item in jzxx:
                jz_id = item.get("F_ID")
                if jz_id:
                    # 更新已有接种记录
                    cur.execute(
                        "SELECT F_ID FROM T_DWSY_JZ WHERE F_ID = ? AND F_SYJH_ID = ?",
                        (jz_id, plan_id)
                    )
                    if not cur.fetchone():
                        raise ValueError(f"接种记录 ID={jz_id} 不存在或不属于该实验计划")

                    upd_cols = [
                        "F_DWBH=?", "F_XB=?", "F_NL=?", "F_LH=?", "F_ZB=?",
                        "F_JHYZSJ=?", "F_JHJZXBS=?", "F_JHYZBW=?", "F_JHCZR=?",
                    ]
                    upd_vals = [
                        item.get("F_DWBH"), item.get("F_XB"), item.get("F_NL"),
                        item.get("F_LH"),   item.get("F_ZB") or item.get("F_ZBMC"),
                        item.get("F_JHYZSJ"), item.get("F_JHJZXBS"),
                        item.get("F_JHYZBW"), item.get("F_JHCZR"),
                    ]
                    if item.get("F_SORT") is not None:
                        upd_cols.append("F_SORT=?")
                        upd_vals.append(item["F_SORT"])
                    upd_vals.append(jz_id)
                    cur.execute(
                        f"UPDATE T_DWSY_JZ SET {', '.join(upd_cols)} WHERE F_ID=?",
                        tuple(upd_vals)
                    )
                else:
                    # 新增接种记录
                    _insert_inoculation_records(cur, plan_id, [item])

        conn.commit()

    # ========== 创建质控记录（修改）==========
    qc_audit_id = None
    try:
        from app.crud.qc_audit import create_qc_audit_record, calculate_changes, get_sample_basic_info

        new_detail_data = get_animal_test_plan_detail(plan_id)

        old_for_compare = copy.deepcopy(old_detail_data)
        new_for_compare = copy.deepcopy(new_detail_data)

        # 未修改接种信息则不对比，避免 ID 噪音
        if not jzxx_changed:
            old_for_compare.pop("JZXX", None)
            new_for_compare.pop("JZXX", None)
        else:
            # 对比时剥离自动生成的 F_ID，记录时恢复含 F_ID 的完整数据
            def _strip_id(jzxx_list):
                return [{k: v for k, v in r.items() if k != "F_ID"} for r in (jzxx_list or [])]
            old_for_compare["JZXX"] = _strip_id(old_for_compare.get("JZXX"))
            new_for_compare["JZXX"] = _strip_id(new_for_compare.get("JZXX"))

        changes = calculate_changes(old_for_compare, new_for_compare)

        # 将 JZXX 变更条目恢复为含 F_ID 的完整原始数据
        import json as _json
        if isinstance(changes.get("变更字段"), list):
            for change_item in changes["变更字段"]:
                if change_item.get("field") == "JZXX":
                    change_item["old_value"] = _json.dumps(
                        old_detail_data.get("JZXX") or [], ensure_ascii=False, default=str
                    )
                    change_item["new_value"] = _json.dumps(
                        new_detail_data.get("JZXX") or [], ensure_ascii=False, default=str
                    )

        gdxx   = new_detail_data.get("GDXX") or {}
        zzid   = gdxx.get("F_ZZID")
        gdid   = new_detail_data.get("F_GDID")

        if zzid and gdid:
            sample_info = get_sample_basic_info(zzid)
            qc_audit_id = create_qc_audit_record(
                F_ZKJD="动物实验计划",
                F_BGLX="修改",
                F_ZZID=zzid,
                F_CHANGES=changes,
                F_JLR=operator or "系统",
                F_GDID=gdid,
                F_XM=sample_info.get("F_XM"),
                F_BRBH=sample_info.get("F_BRBH"),
                F_YBLY=gdxx.get("F_YBLY"),
                F_GDBH=gdxx.get("F_GDBH"),
                F_DCID=gdxx.get("F_DCID"),
                F_DC=gdxx.get("F_DC"),
                F_PYSJ=gdxx.get("F_PYSJ"),
                F_SQR=gdxx.get("F_SQR"),
                F_OLD_DATA=new_detail_data,
            )
            logger.info(f"[动物实验计划质控] 修改质控记录创建成功，计划ID: {plan_id}, 质控ID: {qc_audit_id}")
    except Exception as e:
        logger.error(f"[动物实验计划质控] 创建修改质控记录失败（计划ID: {plan_id}）: {e}", exc_info=True)

    result: Dict[str, Any] = {"F_ID": plan_id, "updated": True}
    if qc_audit_id:
        result["qc_audit_id"] = qc_audit_id
    return result


# ==================== 删除实验计划 ====================

def delete_animal_test_plan(plan_id: int, operator: Optional[str] = None) -> bool:
    """
    删除动物实验计划

    业务规则：
    - 只能删除"已创建"状态的实验计划
    - 删除时级联删除关联的动物接种信息（通过外键 ON DELETE CASCADE）
    """
    import logging
    logger = logging.getLogger(__name__)

    adapter = get_adapter()
    old_detail_data = get_animal_test_plan_detail(plan_id)

    with adapter.connect() as conn:
        cur = conn.cursor()

        cur.execute("SELECT F_ZT FROM T_DWSY_JH WHERE F_ID = ?", (plan_id,))
        row = cur.fetchone()
        if not row:
            raise ValueError("实验计划不存在")

        current_status = row[0]
        if current_status != "已创建":
            raise ValueError(f"只能删除'已创建'状态的实验计划（当前状态：{current_status}）")

        cur.execute("DELETE FROM T_DWSY_JH WHERE F_ID = ?", (plan_id,))
        conn.commit()

        logger.info(f"动物实验计划删除成功：ID={plan_id}")

    # ========== 创建质控记录（删除）==========
    try:
        from app.crud.qc_audit import create_qc_audit_record, get_sample_basic_info

        gdxx = old_detail_data.get("GDXX") or {}
        zzid = gdxx.get("F_ZZID")
        gdid = old_detail_data.get("F_GDID")

        if zzid and gdid:
            sample_info = get_sample_basic_info(zzid)
            changes = {
                "变更字段": [
                    {"字段": "操作",   "新值": "删除实验计划"},
                    {"字段": "实验名称", "旧值": old_detail_data.get("F_SYMC")},
                    {"字段": "实验编号", "旧值": old_detail_data.get("F_SYBH")},
                    {"字段": "接种动物数量", "旧值": len(old_detail_data.get("JZXX") or [])},
                ]
            }
            qc_audit_id = create_qc_audit_record(
                F_ZKJD="动物实验计划",
                F_BGLX="删除",
                F_ZZID=zzid,
                F_CHANGES=changes,
                F_JLR=operator or "系统",
                F_GDID=gdid,
                F_XM=sample_info.get("F_XM"),
                F_BRBH=sample_info.get("F_BRBH"),
                F_YBLY=gdxx.get("F_YBLY"),
                F_GDBH=gdxx.get("F_GDBH"),
                F_DCID=gdxx.get("F_DCID"),
                F_DC=gdxx.get("F_DC"),
                F_PYSJ=gdxx.get("F_PYSJ"),
                F_SQR=gdxx.get("F_SQR"),
                F_OLD_DATA=old_detail_data,
            )
            logger.info(f"[动物实验计划质控] 删除质控记录创建成功，计划ID: {plan_id}, 质控ID: {qc_audit_id}")
    except Exception as e:
        logger.error(f"[动物实验计划质控] 创建删除质控记录失败（计划ID: {plan_id}）: {e}", exc_info=True)

    return True


# ==================== 更新状态 ====================

def update_animal_test_plan_status(plan_id: int, status: str) -> bool:
    """更新动物实验计划状态"""
    valid_statuses = ["已创建", "进行中", "待审核", "已完成", "不合格"]
    if status not in valid_statuses:
        raise ValueError(f"无效的状态：{status}，有效值：{', '.join(valid_statuses)}")

    adapter = get_adapter()
    with adapter.connect() as conn:
        cur = conn.cursor()
        cur.execute("SELECT F_ZT FROM T_DWSY_JH WHERE F_ID = ?", (plan_id,))
        row = cur.fetchone()
        if not row:
            raise ValueError("实验计划不存在")
        cur.execute("UPDATE T_DWSY_JH SET F_ZT = ? WHERE F_ID = ?", (status, plan_id))
        conn.commit()
    return True


# ==================== 标记不合格 ====================

def mark_animal_test_unqualified(
    plan_id: int,
    reason: Optional[str],
    creator_code: str = "",
) -> Optional[int]:
    """将动物实验计划标记为不合格（进入审批中状态，审核通过后才正式不合格）"""
    import logging
    logger = logging.getLogger(__name__)

    adapter = get_adapter()

    with adapter.connect() as conn:
        cur = conn.cursor()
        cur.execute("SELECT F_ID, F_SYMC, F_ZT FROM T_DWSY_JH WHERE F_ID = ?", (plan_id,))
        plan_row = cur.fetchone()
        if not plan_row:
            raise ValueError(f"实验计划不存在：ID={plan_id}")

        plan_name = plan_row[1]
        original_zt = plan_row[2] or "已创建"
        cur.execute(
            "UPDATE T_DWSY_JH SET F_BHGYY = ?, F_ZT = '不合格审批中' WHERE F_ID = ?",
            (reason or '', plan_id)
        )
        conn.commit()
        logger.info(f"[动物实验计划] 已标记为不合格审批中，计划ID: {plan_id}")

    qc_audit_id = None
    if creator_code:
        try:
            from app.crud.qc_audit import create_qc_audit_record

            changes = {
                "操作": "标为不合格",
                "原状态": original_zt,
                "关键信息": {
                    "实验计划ID": plan_id,
                    "实验名称": plan_name or "未知",
                    "不合格原因": reason or "未填写原因",
                },
            }
            qc_audit_id = create_qc_audit_record(
                F_ZKJD="动物实验-标为不合格",
                F_BGLX="修改",
                F_CHANGES=changes,
                F_JLR=creator_code,
            )
            logger.info(f"[动物实验计划] 不合格质控记录创建成功，计划ID: {plan_id}, 质控ID: {qc_audit_id}")
        except Exception as e:
            logger.error(f"[动物实验计划] 不合格质控记录创建失败（计划ID: {plan_id}）: {e}", exc_info=True)

    return qc_audit_id
