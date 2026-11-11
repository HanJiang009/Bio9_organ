import logging
from collections import defaultdict
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from app.db.factory import get_adapter
from app.crud import box_mgmt as _crud_box

logger = logging.getLogger(__name__)


_SGBH_RULE_CODE = "F_SGBH"


def _sgbh_dict_rule_available() -> bool:
    """检查 T_DICT_CODE_RULE 中是否存在已启用、模板非空的 F_SGBH 编码规则。"""
    try:
        from app.crud.code_rule import get_code_rule_by_code
        rule = get_code_rule_by_code(_SGBH_RULE_CODE)
        if not rule:
            return False
        if rule.get("F_ENABLED") != 1:
            return False
        template = rule.get("F_RULE_TEMPLATE")
        return bool(template and str(template).strip())
    except Exception as _e:
        logger.warning(f"[_sgbh_dict_rule_available] 检查F_SGBH字典编码规则失败({_e})，降级硬编码兜底")
        return False


def _generate_sgbh_via_dict_rule(
    *, zzid: str, source_flag: str, dc: Optional[str], yblx: Optional[str],
) -> str:
    """通过字典编码规则生成样本管编号（F_SGBH）。

    source_flag: "BB"（标本直接分装）/ "GD"（培养工单来源分装），供规则模板中
    通过 {F_LYBS} 占位符引用；找不到对应字典项时按原值输出。
    """
    from app.crud.code_rule import generate_code
    ctx = {
        "F_ZZID": zzid,
        "F_LYBS": source_flag,
        "F_DC": dc,
        "F_YBLX": yblx,
    }
    sgbh = generate_code(_SGBH_RULE_CODE, ctx)
    if not sgbh:
        raise ValueError("字典编码规则生成的样本管编号为空")
    return sgbh


def _resolve_zzbw(*, pygd_zzbw=None, ybzj_cjbw=None) -> Optional[str]:
    """组织部位：工单 F_ZZBW 优先，否则取标本 F_CJBW。"""
    for val in (pygd_zzbw, ybzj_cjbw):
        if val is not None and str(val).strip():
            return str(val).strip()
    return None


def _coerce_ybglx_if_present(data: Dict[str, Any], *, label: str = "F_YBGLX（样本管类型）") -> None:
    """
    样本管类型：允许不传该字段；若传入则不得为 null 或空字符串。

    经 Pydantic 解析后，未传的 Optional 字段会变成 None 并出现在 model_dump 中；
    此类情况视为「未传」。显式传 null 由 Schema 的 model_validator 在入参阶段拦截。
    """
    if "F_YBGLX" not in data:
        return
    value = data.get("F_YBGLX")
    if value is None:
        data.pop("F_YBGLX", None)
        return
    text = str(value).strip()
    if not text:
        raise ValueError(f"{label}不能为空字符串")
    data["F_YBGLX"] = text


def _validate_fzxx_ybglx(infos: List[Dict[str, Any]]) -> None:
    for idx, info in enumerate(infos, start=1):
        _coerce_ybglx_if_present(
            info,
            label=f"第{idx}组分装信息的 F_YBGLX（样本管类型）",
        )


def _validate_items_ybglx(items: List[Dict[str, Any]]) -> None:
    """新写入分装记录时校验 F_YBGLX；暂存后仅发起运输（含 F_ID）的条目跳过。"""
    for idx, item in enumerate(items, start=1):
        if item.get("F_ID"):
            continue
        _coerce_ybglx_if_present(
            item,
            label=f"第{idx}条分装记录的 F_YBGLX（样本管类型）",
        )


def _row_label(row_idx: int) -> str:
    """将 0-based 行索引转换为字母标签（0→A, 1→B, …, 25→Z, 26→AA, …）"""
    label = ""
    n = row_idx
    while True:
        label = chr(ord("A") + n % 26) + label
        n = n // 26 - 1
        if n < 0:
            break
    return label


def _parse_wz_to_index(wz: str, ls: int) -> int:
    """
    将字母数字位置（如 A1、B4、AA3）转换为 0-based 绝对序号（行优先）。
    与 _row_label 互为逆函数。

    ls: 盒子列数（每行格子数）

    示例（ls=9）：
        A1 → 0,  A4 → 3,  B1 → 9,  B4 → 12
    """
    if not wz or not wz.strip():
        return 0
    wz = wz.strip().upper()
    alpha = "".join(c for c in wz if c.isalpha())
    digit = "".join(c for c in wz if c.isdigit())
    if not alpha or not digit:
        return 0
    # 字母 → 0-based 行索引（与 _row_label 互逆）
    row_idx = 0
    for c in alpha:
        row_idx = row_idx * 26 + (ord(c) - ord("A")) + 1
    row_idx -= 1
    col_idx = max(0, int(digit) - 1)
    return row_idx * ls + col_idx


def _index_to_wz(abs_index: int, ls: int) -> str:
    """0-based 绝对索引 → 字母数字位置（如 A1、D4）。"""
    return f"{_row_label(abs_index // ls)}{abs_index % ls + 1}"


def _validate_position_conflicts(items: List[Dict[str, Any]]) -> None:
    """
    校验分装条目的盒子格子是否冲突（批次内互斥 + 与库内已有占用互斥）。
    已有 F_ID 且位置不变的 UPDATE 条目不在此函数处理范围内（save 路径不更新 F_WZ）。
    """
    if not items:
        return

    batch_map: Dict[Tuple[str, int], str] = {}
    by_hzh: Dict[str, List[Dict[str, Any]]] = defaultdict(list)

    for item in items:
        hzh = (item.get("F_HZH") or "").strip()
        wz = item.get("F_WZ")
        if not hzh or wz is None or str(wz).strip() == "":
            continue

        ls = _to_pos_int(item.get("F_LS"))
        if ls is None:
            _, ls_val = resolve_box_template_dims(hzh, None)
            ls = ls_val
        if not ls:
            continue

        idx = _parse_wz_to_index(str(wz).strip(), int(ls))
        sgbh = str(item.get("F_SGBH") or "?").strip()
        key = (hzh, idx)
        if key in batch_map:
            wz_label = _index_to_wz(idx, int(ls))
            raise ValueError(
                f"本次分装存在位置冲突：盒子 {hzh} 格子 {wz_label} "
                f"被多条记录占用（{batch_map[key]}、{sgbh}）"
            )
        batch_map[key] = sgbh
        by_hzh[hzh].append(item)

    for hzh, hitems in by_hzh.items():
        try:
            occ = _crud_box.get_box_occupancy(hzh)
        except ValueError:
            continue
        owner = occ.get("wz_index_to_fid") or {}
        ls = _to_pos_int(hitems[0].get("F_LS"))
        if ls is None:
            _, ls_val = resolve_box_template_dims(hzh, None)
            ls = ls_val
        if not ls:
            continue

        for item in hitems:
            fid = item.get("F_ID")
            idx = _parse_wz_to_index(str(item.get("F_WZ")).strip(), int(ls))
            existing_fid = owner.get(idx)
            if existing_fid is None:
                continue
            if fid is not None and int(fid) == int(existing_fid):
                continue
            wz_label = _index_to_wz(idx, int(ls))
            raise ValueError(
                f"盒子 {hzh} 格子 {wz_label} 已被样本管 ID={existing_fid} 占用，"
                f"无法写入 {item.get('F_SGBH') or '新记录'}"
            )


def _boxes_input_map(boxes_raw: List[Dict[str, Any]] | None) -> Dict[str, Dict[str, Any]]:
    """将 BOXES 列表转为 {F_HZH: box_dict}。"""
    return {b["F_HZH"]: b for b in (boxes_raw or []) if b.get("F_HZH")}


def _to_pos_int(val: Any) -> Optional[int]:
    try:
        n = int(val)
        return n if n > 0 else None
    except (TypeError, ValueError):
        return None


def resolve_box_template_dims(
    hzh: str,
    boxes_input: Optional[Dict[str, Dict[str, Any]]] = None,
) -> Tuple[Optional[int], Optional[int]]:
    """
    解析盒子的模板行数/列数（F_HS / F_LS）。
    优先 BOXES 中的 F_TEMPLATE_ID，其次 T_BOX 关联模板。
    """
    hzh_val = (hzh or "").strip()
    if not hzh_val:
        return None, None

    hs: Optional[int] = None
    ls: Optional[int] = None
    bx = (boxes_input or {}).get(hzh_val) or {}
    tpl_id = bx.get("F_TEMPLATE_ID")
    if tpl_id:
        tpl = _crud_box.get_box_template_by_id(int(tpl_id))
        if tpl:
            hs = _to_pos_int(tpl.get("F_HS"))
            ls = _to_pos_int(tpl.get("F_LS"))

    if hs is None or ls is None:
        existing = _crud_box.get_box_by_hzh(hzh_val)
        if existing:
            if hs is None:
                hs = _to_pos_int(existing.get("F_TPLT_HS"))
            if ls is None:
                ls = _to_pos_int(existing.get("F_TPLT_LS"))

    return hs, ls


def enrich_items_box_dims(
    items: List[Dict[str, Any]],
    boxes_raw: List[Dict[str, Any]] | None = None,
) -> None:
    """为分装条目补全 F_HS/F_LS（来自盒子模板，条目上已有值则不覆盖）。"""
    boxes_map = _boxes_input_map(boxes_raw)
    for item in items:
        hzh = (item.get("F_HZH") or "").strip()
        if not hzh:
            continue
        hs, ls = resolve_box_template_dims(hzh, boxes_map)
        if hs is not None and item.get("F_HS") is None:
            item["F_HS"] = hs
        if ls is not None and item.get("F_LS") is None:
            item["F_LS"] = ls


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


def get_sample_linkage_lists(q: Dict[str, Any], user: Dict[str, Any] | None = None,
                            scope: str | None = None) -> Dict[str, Any]:
    """
    查询标本和工单的统一列表。
    返回扁平化结构，每条记录来自 T_YBZJ 或 T_PYGD。
    """
    adapter = get_adapter()
    skip = int(q.get("skip") or 0)
    limit = int(q.get("limit") or 50)

    zzid = (q.get("F_ZZID") or "").strip()
    brbh = (q.get("F_BRBH") or "").strip()
    gdbh = (q.get("F_GDBH") or "").strip()
    djrq_start = (q.get("F_DJRQ_START") or "").strip()
    djrq_end = (q.get("F_DJRQ_END") or "").strip()
    cxsj_start = (q.get("F_CXSJ_START") or "").strip()
    cxsj_end = (q.get("F_CXSJ_END") or "").strip()
    zcsj_start = (q.get("F_ZCSJ_START") or "").strip()
    zcsj_end = (q.get("F_ZCSJ_END") or "").strip()

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
    
    # 培养工单编号过滤（仅工单，标本子查询直接排除）
    if gdbh:
        where_ybzj.append("1=0")  # 按工单编号筛选时不返回标本记录
        pred_p, p_p = adapter.like("p.F_GDBH", gdbh)
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

    if zcsj_start or zcsj_end:
        from app.crud.box_mgmt import _range_bound, ensure_box_create_time
        ensure_box_create_time()
        zcsj_sql = """
            EXISTS (
                SELECT 1 FROM T_YBFZ fz
                INNER JOIN T_BOX bx ON bx.F_HZH = fz.F_HZH
                WHERE {match_sql}
        """
        zcsj_params: List[Any] = []
        if zcsj_start:
            zcsj_sql += " AND bx.F_CREATE_TIME >= ?"
            zcsj_params.append(_range_bound(zcsj_start, end=False))
        if zcsj_end:
            zcsj_sql += " AND bx.F_CREATE_TIME <= ?"
            zcsj_params.append(_range_bound(zcsj_end, end=True))
        zcsj_sql += ")"
        where_ybzj.append(zcsj_sql.format(match_sql="fz.F_ZZID = y.F_ZZID"))
        params_ybzj.extend(zcsj_params)
        where_pygd.append(zcsj_sql.format(match_sql="fz.F_PYGD_ID = p.F_ID"))
        params_pygd.extend(zcsj_params)

    from app.core.data_scope import apply_owner_filter
    apply_owner_filter(where_ybzj, params_ybzj, scope=scope, user=user, column="y.F_DJR", match="name")
    apply_owner_filter(where_pygd, params_pygd, scope=scope, user=user, column="p.F_SQR", match="name")

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

        _mark_linked_sample_rows(cur, items)

    return {"items": items, "total": total}


def _mark_linked_sample_rows(cur, items: List[Dict[str, Any]]) -> None:
    """给每行标出标本号、工单号各自的已保存分装条数。一条 T_YBFZ 算一根样本管。"""
    zzids: List[str] = []
    pygd_ids: List[int] = []
    for item in items:
        if item.get("F_ZZID"):
            zzids.append(str(item["F_ZZID"]))
        if item.get("F_PYGD_ID"):
            try:
                pygd_ids.append(int(item["F_PYGD_ID"]))
            except (TypeError, ValueError):
                pass

    zzid_count: Dict[str, int] = {}
    pygd_count: Dict[int, int] = {}
    if zzids:
        unique = list(dict.fromkeys(zzids))
        placeholders = ",".join(["?"] * len(unique))
        cur.execute(
            f"SELECT F_ZZID, COUNT(1) FROM T_YBFZ WHERE F_ZZID IN ({placeholders}) GROUP BY F_ZZID",
            tuple(unique),
        )
        zzid_count = {row[0]: int(row[1]) for row in cur.fetchall() if row and row[0]}
    if pygd_ids:
        unique_ids = list(dict.fromkeys(pygd_ids))
        placeholders = ",".join(["?"] * len(unique_ids))
        cur.execute(
            f"SELECT F_PYGD_ID, COUNT(1) FROM T_YBFZ WHERE F_PYGD_ID IN ({placeholders}) GROUP BY F_PYGD_ID",
            tuple(unique_ids),
        )
        pygd_count = {int(row[0]): int(row[1]) for row in cur.fetchall() if row and row[0] is not None}

    for item in items:
        zzid_n = zzid_count.get(item.get("F_ZZID"), 0)
        try:
            gdb_n = pygd_count.get(int(item["F_PYGD_ID"]), 0) if item.get("F_PYGD_ID") else 0
        except (TypeError, ValueError):
            gdb_n = 0
        item["F_ZZID_YBS"] = zzid_n
        item["F_GDBH_YBS"] = gdb_n
        # 标本行看标本号，工单行只看该工单，不代表同一行标本号有没有管子
        item["F_YGL"] = 1 if (gdb_n if item.get("F_PYGD_ID") else zzid_n) > 0 else 0


def _text_or_none(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _int_or_none(value: Any) -> Optional[int]:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def list_linked_samples(zzid: Optional[str], gdbh: Optional[str]) -> List[Dict[str, Any]]:
    """按标本号或培养工单号查询已保存的关联样本，二者只能传一个。"""
    zzid_text = (zzid or "").strip()
    gdbh_text = (gdbh or "").strip()
    if bool(zzid_text) == bool(gdbh_text):
        raise ValueError("F_ZZID 与 F_GDBH 必须且只能填写一个")

    if zzid_text:
        where_sql = "f.F_ZZID = ?"
        params: List[Any] = [zzid_text]
    else:
        where_sql = "p.F_GDBH = ?"
        params = [gdbh_text]

    sql = f"""
    SELECT
        f.F_SGBH,
        f.F_YBLX,
        f.F_ZZID,
        p.F_GDBH,
        f.F_DC,
        f.F_CCXS,
        f.F_FGS,
        f.F_TJ,
        f.F_XBSL,
        f.F_YBGLX,
        f.F_BZ
    FROM T_YBFZ f
    LEFT JOIN T_PYGD p ON p.F_ID = f.F_PYGD_ID
    WHERE {where_sql}
    ORDER BY f.F_ID ASC
    """
    adapter = get_adapter()
    rows, cols = adapter.fetch_all(sql, params)
    items: List[Dict[str, Any]] = []
    for index, row in enumerate(rows, start=1):
        data = dict(zip(cols, row))
        items.append({
            "F_XH": index,
            "F_SGBH": _text_or_none(data.get("F_SGBH")),
            "F_YBLX": _text_or_none(data.get("F_YBLX")),
            "F_ZZID": _text_or_none(data.get("F_ZZID")),
            "F_GDBH": _text_or_none(data.get("F_GDBH")),
            "F_DC": _text_or_none(data.get("F_DC")),
            "F_CCXS": _text_or_none(data.get("F_CCXS")),
            "F_FGS": _int_or_none(data.get("F_FGS")),
            "F_TJ": _text_or_none(data.get("F_TJ")),
            "F_XBSL": _text_or_none(data.get("F_XBSL")),
            "F_YBGLX": _text_or_none(data.get("F_YBGLX")),
            "F_BZ": _text_or_none(data.get("F_BZ")),
        })
    return items


def _fetch_ybzj_by_ids(ids: List[int]) -> List[Dict[str, Any]]:
    if not ids:
        return []
    adapter = get_adapter()
    placeholders = ",".join(["?"] * len(ids))
    sql = f"""
SELECT F_ID, F_ZZID, F_BBLX,
       F_BRBH, F_ZYH, F_MZH, F_XM, F_XB, F_NL,
       NULL AS F_NLDW,
       NULL AS F_MZ, NULL AS F_HY, NULL AS F_ZY, F_DZ, F_DH, F_CHH, F_SFZH,
       F_LCZD, F_BBLY AS F_LYKS, NULL AS F_KS, F_CXSJ, F_XMBH,
       F_CJBW
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
       NULL AS F_LCZD, NULL AS F_LYKS, NULL AS F_KS, y.F_CXSJ, p.F_XMBH,
       p.F_ZZBW, y.F_CJBW
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

    _validate_fzxx_ybglx(infos)

    ybzj_rows = _fetch_ybzj_by_ids(ybzj_ids)
    pygd_rows = _fetch_pygd_by_ids(pygd_ids)

    # 对 PYGD 来源的记录，通过 F_ZZID 从 T_YBZJ 补全患者字段（LEFT JOIN 可能遗漏）
    if pygd_rows:
        pygd_zzids = [r.get("F_ZZID") for r in pygd_rows if r.get("F_ZZID")]
        ybzj_sup = _fetch_ybzj_by_zzids(pygd_zzids)
        for row in pygd_rows:
            zj = ybzj_sup.get(row.get("F_ZZID") or "")
            if not zj:
                continue
            for fz_field, zj_field in _YBZJ_FILL_MAP:
                if not row.get(fz_field):
                    row[fz_field] = zj.get(zj_field)

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
            "F_ZZBW": _resolve_zzbw(ybzj_cjbw=row.get("F_CJBW")),
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
            "F_ZZBW": _resolve_zzbw(
                pygd_zzbw=row.get("F_ZZBW"),
                ybzj_cjbw=row.get("F_CJBW"),
            ),
        }
        samples.append((str(zzid), "GD", base_info))

    if not samples:
        raise ValueError("未检索到有效的样本数据")

    items: List[Dict[str, Any]] = []
    ts = datetime.now().strftime("%Y%m%d%H%M%S")
    sequence_counters: Dict[Tuple[str, str], int] = {}

    # F_SGBH 编号：优先使用字典编码规则（T_DICT_CODE_RULE.F_CODE='F_SGBH'）生成，
    # 规则不存在/未启用/模板为空时降级为硬编码规则（{ZZID}-{来源标记}-{时间戳}-{序号}）。
    # 批次内一旦命中异常统一降级，避免同一批分装记录出现两种编号风格。
    use_dict_rule_sgbh = _sgbh_dict_rule_available()

    for info in infos:
        fgs = int(info.get("F_FGS"))
        sfys_raw = info.get("F_SFYS")
        sfys = True if sfys_raw is None else bool(sfys_raw)

        for zzid, source_flag, base in samples:
            key = (zzid, source_flag)
            start_seq = sequence_counters.get(key, 0)
            for offset in range(1, fgs + 1):
                seq = start_seq + offset
                sg = base.copy()
                if use_dict_rule_sgbh:
                    try:
                        sg["F_SGBH"] = _generate_sgbh_via_dict_rule(
                            zzid=zzid, source_flag=source_flag, dc=base.get("F_DC"),
                            yblx=info.get("F_YBLX") or base.get("F_YBLX"),
                        )
                    except Exception as _e:
                        logger.warning(
                            f"[preview_sample_allocation] 字典编码规则生成F_SGBH失败({_e})，"
                            f"本批次剩余记录降级硬编码兜底"
                        )
                        use_dict_rule_sgbh = False
                        sg["F_SGBH"] = f"{zzid}-{source_flag}-{ts}-{seq}"
                else:
                    sg["F_SGBH"] = f"{zzid}-{source_flag}-{ts}-{seq}"

                # 最终 GBK 安全校验：若编号含 GBK 不兼容字符（如字典项配置了生僻字/emoji）则强制降级
                try:
                    sg["F_SGBH"].encode("gbk")
                except UnicodeEncodeError:
                    logger.warning(
                        f"[preview_sample_allocation] F_SGBH含GBK不兼容字符，强制降级: {sg['F_SGBH']!r}"
                    )
                    sg["F_SGBH"] = f"{zzid}-{source_flag}-{ts}-{seq}"

                sg["F_CCXS"] = info.get("F_CCXS")
                sg["F_FGS"] = fgs
                sg["F_TJ"] = info.get("F_TJ")
                sg["F_XBSL"] = info.get("F_XBSL")
                sg["F_SPREC"] = info.get("F_SPREC")
                sg["F_BZ"] = info.get("F_BZ")
                sg["F_WZ"] = None
                sg["F_HZH"] = None
                sg["F_SFYS"] = sfys
                if info.get("F_YBLX"):
                    sg["F_YBLX"] = info.get("F_YBLX")
                if "F_YBGLX" in info:
                    sg["F_YBGLX"] = info.get("F_YBGLX")
                items.append(sg)
            sequence_counters[key] = start_seq + fgs

    # ------------------------------------------------------------------
    # 按 BOXES 顺序贪心分配盒号和格子坐标
    # ------------------------------------------------------------------
    boxes_raw: List[Dict[str, Any]] = payload.get("BOXES") or []
    capacity_warnings: List[str] = []
    boxes_map = _boxes_input_map(boxes_raw)

    if boxes_raw:
        # 构建每个盒子的可用信息（跳过库内已占格子）
        box_slots: List[Dict[str, Any]] = []
        box_occupied_map: Dict[str, set] = {}
        for bx in boxes_raw:
            hzh = (bx.get("F_HZH") or "").strip()
            if not hzh:
                continue
            hs, ls = resolve_box_template_dims(hzh, boxes_map)
            if not hs or not ls:
                continue  # 无法确定容量，跳过该盒子
            tpl_cap = hs * ls
            # 解析起始格子坐标（F_KSWZ），默认从 A1（索引 0）开始
            kswz = bx.get("F_KSWZ")
            start_pos = _parse_wz_to_index(kswz, ls) if kswz else 0
            start_pos = max(0, min(start_pos, tpl_cap - 1))

            occupied: set = set()
            tubes_count = 0
            used_slots = 0
            try:
                occ = _crud_box.get_box_occupancy(hzh)
                occupied = set(occ.get("occupied_indices") or [])
                tubes_count = int(occ.get("tubes_count") or 0)
                used_slots = int(occ.get("used") or 0)
                if used_slots > 0:
                    kswz_hint = f"，本次从 {kswz} 起分配空闲格" if kswz else ""
                    dup_hint = ""
                    if tubes_count > used_slots:
                        dup_hint = (
                            f"（共 {tubes_count} 条记录，"
                            f"{tubes_count - used_slots} 格存在历史重复占用，已自动跳过）"
                        )
                    capacity_warnings.append(
                        f"盒子 {hzh} 已占 {used_slots} 格{dup_hint}{kswz_hint}"
                    )
            except ValueError:
                pass  # 盒子尚未创建，视为空盒

            box_occupied_map[hzh] = set(occupied)
            free_from_start = sum(
                1 for p in range(start_pos, tpl_cap) if p not in occupied
            )
            box_slots.append({
                "hzh": hzh,
                "hs": hs,
                "cap": tpl_cap,
                "effective_cap": free_from_start,
                "ls": ls,
                "start_pos": start_pos,
                "kswz": kswz,
            })

        # 校验有效总容量（从各盒起始位置起算，排除已占格）
        total_needed = len(items)
        total_available = sum(s["effective_cap"] for s in box_slots)
        if box_slots and total_available < total_needed:
            raise ValueError(
                f"所有盒子从指定起始位置起的空闲格子不足"
                f"（共 {total_available} 格，本次需要 {total_needed} 格）"
            )

        # 贪心分配：依序将 items 填入各盒子的空闲格子
        item_idx = 0
        for slot in box_slots:
            if item_idx >= total_needed:
                break
            hzh = slot["hzh"]
            hs = slot["hs"]
            ls = slot["ls"]
            start_pos = slot["start_pos"]
            tpl_cap = slot["cap"]
            occupied = box_occupied_map.setdefault(hzh, set())
            free_positions = [
                p for p in range(start_pos, tpl_cap) if p not in occupied
            ]
            count = min(len(free_positions), total_needed - item_idx)
            for i in range(count):
                item = items[item_idx + i]
                item["F_HZH"] = hzh
                item["F_HS"] = hs
                item["F_LS"] = ls
                absolute_pos = free_positions[i]
                occupied.add(absolute_pos)
                item["F_WZ"] = _index_to_wz(absolute_pos, ls)
            item_idx += count

        if item_idx < total_needed:
            raise ValueError(
                f"盒子空闲格子不足，仍有 {total_needed - item_idx} 根样本管未能分配位置"
            )

    return {"items": items, "total": len(items), "capacity_warnings": capacity_warnings}


def preview_sample_allocation_with_scan(payload: Dict[str, Any]) -> Dict[str, Any]:
    """
    手动扫描预制试管编码的分装预览。

    在 preview_sample_allocation 基础上，将自动生成的 F_SGBH 替换为用户
    外部扫描传入的试管编码（TUBE_CODES），按顺序一一对应。

    数量不一致时的处理规则：
      - 试管编码 > 条目数：忽略多余编码，返回警告
      - 试管编码 < 条目数：超出部分的 F_SGBH 置空，返回警告
      - 二者相等：正常返回，无警告

    校重规则（TUBE_CODES）：
      - 扫描批次内非空编码不可重复
      - 库内已存在且状态非「未运送」的编码不可复用
      - 状态为「未运送」的暂存记录允许复用（save/draft 时走 UPDATE）
    """
    tube_codes = _normalize_tube_codes(payload.pop("TUBE_CODES", None) or [])
    _validate_scan_tube_codes(tube_codes)

    result = preview_sample_allocation(payload)
    items: List[Dict[str, Any]] = result["items"]
    total: int = result["total"]

    tube_count = len(tube_codes)
    warning: Optional[str] = None

    if tube_count != total:
        warning = f"样本管数量与样本数不一致（{tube_count}/{total}）"

    for idx, item in enumerate(items):
        if idx < tube_count:
            item["F_SGBH"] = tube_codes[idx]
        else:
            item["F_SGBH"] = ""

    return {"items": items, "total": total, "warning": warning}


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
        F_YBLX, F_YBGLX, F_CCXS, F_FGS, F_TJ, F_XBSL, F_SPREC,
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


def _fetch_ybzj_by_zzids(zzids: List[str]) -> Dict[str, Dict[str, Any]]:
    """根据标本号批量查询 T_YBZJ 患者信息，返回 {F_ZZID: row_dict}。
    同一标本号可能有多条记录，取第一条（患者信息相同）。"""
    if not zzids:
        return {}
    adapter = get_adapter()
    unique_zzids = list({z for z in zzids if z})
    placeholders = ",".join(["?"] * len(unique_zzids))
    sql = f"""
SELECT F_ZZID, F_XM, F_XB, F_NL, F_BRBH, F_ZYH, F_MZH,
       F_DZ, F_DH, F_CHH, F_SFZH, F_LCZD, F_BBLY, F_CJBW, F_XMBH
FROM T_YBZJ
WHERE F_ZZID IN ({placeholders})
"""
    rows, cols = adapter.fetch_all(sql, unique_zzids)
    result: Dict[str, Dict[str, Any]] = {}
    for row in rows:
        d = dict(zip(cols, row))
        zzid = d.get("F_ZZID")
        if zzid and zzid not in result:
            result[zzid] = d
    return result


# 写入 T_YBFZ 时从 T_YBZJ 补全的字段映射：(T_YBFZ字段, T_YBZJ字段)
_YBZJ_FILL_MAP: List[Tuple[str, str]] = [
    ("F_XM",   "F_XM"),
    ("F_XB",   "F_XB"),
    ("F_NL",   "F_NL"),
    ("F_BRBH", "F_BRBH"),
    ("F_ZYH",  "F_ZYH"),
    ("F_MZH",  "F_MZH"),
    ("F_DZ",   "F_DZ"),
    ("F_DH",   "F_DH"),
    ("F_CHH",  "F_CHH"),
    ("F_SFZH", "F_SFZH"),
    ("F_LCZD", "F_LCZD"),
    ("F_LYKS", "F_BBLY"),  # T_YBFZ.F_LYKS ← T_YBZJ.F_BBLY
    ("F_XMBH", "F_XMBH"),  # 项目编号兜底：ITEMS 直传缺失时按标本号回填
]


def _fetch_transport_sample_details(fz_ids: List[int]) -> List[Dict[str, Any]]:
    """查询运输单关联样本的对外展示字段（发起运输返参用）。"""
    if not fz_ids:
        return []

    from app.crud.box_mgmt import export_wz_as_int

    adapter = get_adapter()
    placeholders = ",".join(["?"] * len(fz_ids))
    sql = f"""
    SELECT
        f.F_ID,
        f.F_SGBH,
        f.F_ZZID,
        f.F_YBLX,
        f.F_WZ,
        f.F_HZH,
        f.F_LS,
        COALESCE(y.F_BBLX, p.F_BBLX) AS F_BBLX
    FROM T_YBFZ f
    LEFT JOIN T_YBZJ y ON y.F_ID = f.F_YBZJ_ID
    LEFT JOIN T_PYGD p ON p.F_ID = f.F_PYGD_ID
    WHERE f.F_ID IN ({placeholders})
    """
    rows, cols = adapter.fetch_all(sql, fz_ids)
    idx = {c: i for i, c in enumerate(cols)}
    by_id: Dict[int, Dict[str, Any]] = {}
    for row in rows:
        fid = int(row[idx["F_ID"]])
        wz_raw = row[idx.get("F_WZ")] if "F_WZ" in idx else None
        hzh = row[idx.get("F_HZH")] if "F_HZH" in idx else None
        ls = row[idx.get("F_LS")] if "F_LS" in idx else None
        wz = export_wz_as_int(wz_raw, ls, hzh=hzh)
        if wz is None:
            wz = wz_raw
        by_id[fid] = {
            "F_ID": fid,
            "F_BBLX": row[idx.get("F_BBLX")] if "F_BBLX" in idx else None,
            "F_ZZID": row[idx.get("F_ZZID")] if "F_ZZID" in idx else None,
            "F_YBLX": row[idx.get("F_YBLX")] if "F_YBLX" in idx else None,
            "F_SGBH": row[idx.get("F_SGBH")] if "F_SGBH" in idx else None,
            "F_HZH": hzh,
            "F_WZ": wz,
        }

    return [by_id[fid] for fid in fz_ids if fid in by_id]


def _fetch_ybfz_by_sgbhs(sgbhs: List[str]) -> Dict[str, Dict[str, Any]]:
    """按试管编号批量查询 T_YBFZ，返回 {F_SGBH: {F_ID, F_YSZT}}。"""
    unique = list(dict.fromkeys(s.strip() for s in sgbhs if s and str(s).strip()))
    if not unique:
        return {}

    adapter = get_adapter()
    placeholders = ",".join(["?"] * len(unique))
    sql = f"SELECT F_ID, F_SGBH, F_YSZT FROM T_YBFZ WHERE F_SGBH IN ({placeholders})"
    rows, cols = adapter.fetch_all(sql, unique)
    idx = {c: i for i, c in enumerate(cols)}
    result: Dict[str, Dict[str, Any]] = {}
    for row in rows:
        sgbh = str(row[idx["F_SGBH"]]).strip()
        result[sgbh] = {
            "F_ID": int(row[idx["F_ID"]]),
            "F_YSZT": (row[idx.get("F_YSZT")] if "F_YSZT" in idx else None) or "未运送",
        }
    return result


def _normalize_tube_codes(tube_codes: List[Any]) -> List[str]:
    """规范化扫描试管编码（去首尾空白）。"""
    return [str(code or "").strip() for code in (tube_codes or [])]


def _validate_scan_tube_codes(tube_codes: List[str]) -> None:
    """
    校验扫描试管编码（TUBE_CODES）：
    - 非空编码在扫描批次内不可重复
    - 库内已存在且状态非「未运送」的编码不可复用（未运送暂存记录允许后续 save/draft 走 UPDATE）
    """
    non_empty = [code for code in tube_codes if code]
    if not non_empty:
        return

    seen: set[str] = set()
    batch_dupes: List[str] = []
    for code in non_empty:
        if code in seen:
            batch_dupes.append(code)
        seen.add(code)
    if batch_dupes:
        unique_dupes = list(dict.fromkeys(batch_dupes))
        raise ValueError(f"扫描试管编码重复: {', '.join(unique_dupes)}")

    existing_map = _fetch_ybfz_by_sgbhs(non_empty)
    conflicts: List[str] = []
    for code in non_empty:
        existing = existing_map.get(code)
        if existing and existing.get("F_YSZT") != "未运送":
            conflicts.append(f"{code}（{existing['F_YSZT']}）")
    if conflicts:
        raise ValueError(f"试管编号已存在: {', '.join(conflicts)}")


def _is_duplicate_sgbh_db_error(exc: Exception) -> bool:
    """判断是否为 T_YBFZ.F_SGBH 唯一索引冲突（MySQL 1062 等）。"""
    msg = str(exc).lower()
    return "1062" in msg or ("duplicate" in msg and "sgbh" in msg)


def _resolve_existing_ybfz_items(
    new_items: List[Dict[str, Any]],
    update_items: List[Dict[str, Any]],
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], List[Dict[str, str]]]:
    """
    将无 F_ID 但试管编号已在 T_YBFZ 中的条目归并到 UPDATE 路径或跳过。

    典型场景：页面暂存分装（/assign/draft）后不关闭页面，直接携带缓存区 ITEMS
    调用 /assign/save 发起运输——此时 ITEMS 往往不含 F_ID，但 SGBH 已写入库。

    - F_YSZT='未运送'：视为暂存记录，绑定 F_ID 后走 UPDATE（发起运输）
    - 其他状态（如运送中）：跳过，避免重复插入或重复发起运输
    """
    if not new_items:
        return new_items, update_items, []

    sgbhs = [str(it.get("F_SGBH", "")).strip() for it in new_items if it.get("F_SGBH")]
    existing_map = _fetch_ybfz_by_sgbhs(sgbhs)

    still_new: List[Dict[str, Any]] = []
    skipped: List[Dict[str, str]] = []
    resolved_updates = list(update_items)

    for item in new_items:
        sgbh = str(item.get("F_SGBH", "")).strip()
        existing = existing_map.get(sgbh)
        if not existing:
            still_new.append(item)
            continue
        if existing["F_YSZT"] == "未运送":
            item["F_ID"] = existing["F_ID"]
            resolved_updates.append(item)
        else:
            skipped.append({
                "F_SGBH": sgbh,
                "reason": f"试管编号已存在且状态为「{existing['F_YSZT']}」，已跳过",
            })

    return still_new, resolved_updates, skipped


def _apply_ybfz_transport_update(
    cur,
    item: Dict[str, Any],
    *,
    inserted_ids: List[int],
    transport_ids: List[int],
) -> None:
    """UPDATE 路径：将暂存分装记录更新为运输状态（或保持未运送）。"""
    fz_id = int(item["F_ID"])
    yszt = "运送中" if item.get("F_SFYS") else "未运送"
    set_parts = ["F_YSZT = ?"]
    set_vals: List[Any] = [yszt]
    if item.get("F_HS") is not None:
        set_parts.append("F_HS = ?")
        set_vals.append(item["F_HS"])
    if item.get("F_LS") is not None:
        set_parts.append("F_LS = ?")
        set_vals.append(item["F_LS"])
    cur.execute(
        f"UPDATE T_YBFZ SET {', '.join(set_parts)} WHERE F_ID = ? AND F_YSZT = '未运送'",
        tuple(set_vals + [fz_id]),
    )
    inserted_ids.append(fz_id)
    if item.get("F_SFYS"):
        transport_ids.append(fz_id)


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

    _validate_items_ybglx(items)

    # 一次性从 T_YBZJ 补全患者字段（按 F_ZZID 批量查询）
    zzids = [item.get("F_ZZID") for item in items if item.get("F_ZZID")]
    ybzj_map = _fetch_ybzj_by_zzids(zzids)
    for item in items:
        zj = ybzj_map.get(item.get("F_ZZID") or "")
        if not zj:
            continue
        for fz_field, zj_field in _YBZJ_FILL_MAP:
            if not item.get(fz_field):
                item[fz_field] = zj.get(zj_field)

    adapter = get_adapter()
    czr = _resolve_operator(user, adapter)

    # ------------------------------------------------------------------
    # 拆分：有 F_ID 的条目走 UPDATE 路径（暂存后发起运输），
    #       无 F_ID 的条目走 INSERT 路径（全新分装）
    # ------------------------------------------------------------------
    new_items = [it for it in items if not it.get("F_ID")]
    update_items = [it for it in items if it.get("F_ID")]

    # 暂存后未带 F_ID 直接发起运输：按 SGBH 识别已有「未运送」记录，转 UPDATE 路径
    skipped_existing: List[Dict[str, str]] = []
    if new_items:
        new_items, update_items, skipped_existing = _resolve_existing_ybfz_items(
            new_items, update_items
        )

    if not new_items and not update_items:
        if skipped_existing:
            raise ValueError(
                "没有可保存的分装数据（所有试管编号均已存在且无法重复处理）"
            )
        raise ValueError("没有可保存的分装数据")

    # ------------------------------------------------------------------
    # 盒子处理：仅针对新 INSERT 的条目检查/创建 T_BOX 并校验容量
    # ------------------------------------------------------------------
    if new_items:
        items_by_hzh: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
        for item in new_items:
            if item.get("F_HZH"):
                items_by_hzh[item["F_HZH"]].append(item)

        _validate_position_conflicts(new_items)

        if items_by_hzh:
            boxes_input: Dict[str, Dict[str, Any]] = {
                b["F_HZH"]: b for b in (payload.get("BOXES") or []) if b.get("F_HZH")
            }
            for hzh, box_items in items_by_hzh.items():
                box = _crud_box.get_box_by_hzh(hzh)
                if not box:
                    bx = boxes_input.get(hzh, {})
                    tpl_id = bx.get("F_TEMPLATE_ID")
                    if not tpl_id:
                        raise ValueError(f"盒子 {hzh} 不存在且未提供模板ID，请在 BOXES 参数中指定 F_TEMPLATE_ID")
                    # 对齐标本号：保存时内嵌 peek/generate 判断，可能因并发调整最终盒号
                    box_data = {"F_HZH": hzh, "F_TEMPLATE_ID": tpl_id, "F_CZR": czr}
                    _crud_box.create_box(box_data)
                    final_hzh = box_data.get("F_HZH") or hzh
                    if final_hzh != hzh:
                        for it in box_items:
                            it["F_HZH"] = final_hzh
                        hzh = final_hzh
                cap = _crud_box.get_box_capacity(hzh)
                if cap["remaining"] < len(box_items):
                    raise ValueError(
                        f"盒子 {hzh} 空间不足（容量 {cap['capacity']}，已用 {cap['used']}，"
                        f"本次需要 {len(box_items)}）"
                    )

    # 从盒子模板补全 F_HS/F_LS（ITEMS 直传时 preview 不会跑；盒子创建后再查一次 T_BOX）
    enrich_items_box_dims(items, payload.get("BOXES") or [])
    # ------------------------------------------------------------------

    inserted_ids: List[int] = []
    transport_ids: List[int] = []
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    with adapter.connect() as conn:
        cur = conn.cursor()

        lookup_sql = "SELECT F_ID, F_YSZT FROM T_YBFZ WHERE F_SGBH = ? ORDER BY F_ID DESC"
        lookup_paginated, lookup_extra = adapter.paginate(lookup_sql, 0, 1)

        # ── INSERT 路径：全新分装记录 ────────────────────────────────
        for item in new_items:
            yszt = "运送中" if item.get("F_SFYS") else "未运送"
            try:
                cur.execute(
                    """
                    INSERT INTO T_YBFZ (
                        F_SGBH, F_ZZID, F_YBZJ_ID, F_PYGD_ID, F_DC,
                        F_YBLX, F_YBGLX, F_CCXS, F_FGS, F_TJ, F_XBSL, F_SPREC,
                        F_BZ, F_CZR, F_CJSJ, F_HZH, F_HS, F_LS,
                        F_BRBH, F_ZYH, F_MZH, F_XM, F_XB, F_NL, F_NLDW,
                        F_MZ, F_HY, F_ZY, F_DZ, F_DH, F_CHH, F_SFZH,
                        F_LCZD, F_LYKS, F_KS, F_CXSJ, F_XMBH,
                        F_WZ, F_YSZT
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        item.get("F_SGBH"),
                        item.get("F_ZZID"),
                        item.get("F_YBZJ_ID"),
                        item.get("F_PYGD_ID"),
                        item.get("F_DC"),
                        item.get("F_YBLX"),
                        item.get("F_YBGLX"),
                        item.get("F_CCXS"),
                        item.get("F_FGS"),
                        item.get("F_TJ"),
                        item.get("F_XBSL"),
                        item.get("F_SPREC"),
                        item.get("F_BZ"),
                        czr,
                        now_str,
                        item.get("F_HZH"),
                        item.get("F_HS"),
                        item.get("F_LS"),
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
            except Exception as exc:
                # 兜底：并发写入或前置查重未覆盖时的 SGBH 唯一索引冲突
                if not _is_duplicate_sgbh_db_error(exc):
                    raise
                sgbh = str(item.get("F_SGBH", "")).strip()
                cur.execute(lookup_paginated, (sgbh,) + tuple(lookup_extra))
                row = cur.fetchone()
                if not row:
                    raise
                existing_id = int(row[0])
                existing_yszt = (row[1] if len(row) > 1 else None) or "未运送"
                if existing_yszt != "未运送":
                    skipped_existing.append({
                        "F_SGBH": sgbh,
                        "reason": f"试管编号已存在且状态为「{existing_yszt}」，已跳过",
                    })
                    continue
                item["F_ID"] = existing_id
                _apply_ybfz_transport_update(
                    cur, item, inserted_ids=inserted_ids, transport_ids=transport_ids
                )
                continue

            cur.execute(lookup_paginated, (item.get("F_SGBH"),) + tuple(lookup_extra))
            row = cur.fetchone()
            new_id = row[0] if row else None
            if new_id is not None:
                inserted_ids.append(int(new_id))
                if item.get("F_SFYS"):
                    transport_ids.append(int(new_id))

        # ── UPDATE 路径：暂存后发起运输（F_ID 已存在于 T_YBFZ） ───────
        for item in update_items:
            _apply_ybfz_transport_update(
                cur, item, inserted_ids=inserted_ids, transport_ids=transport_ids
            )

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
        ysmdd = payload.get("F_YSMDD")
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
            if ysmdd:
                placeholders = ",".join(["?"] * len(transport_ids))
                update_sql = f"UPDATE T_YBFZ SET F_YSMDD = ? WHERE F_ID IN ({placeholders})"
                cur.execute(update_sql, tuple([ysmdd] + transport_ids))
            conn.commit()

        # 运输发起后自动更新盒子状态
        _crud_box.try_update_box_status_after_transport(transport_ids)

        transport_info = {
            "F_YSDH": ysdh,
            "F_YSXX": transport_ids,
            "F_YSDZT": ysdzt,
            "items": _fetch_transport_sample_details(transport_ids),
        }

    result = {"inserted": inserted_ids, "count": len(inserted_ids)}
    if skipped_existing:
        result["skipped"] = skipped_existing
        result["skipped_count"] = len(skipped_existing)
    if transport_info:
        result["transport"] = transport_info
    return result


def _try_draft_position_update(
    payload: Dict[str, Any],
    user: Dict[str, Any] | None,
) -> Optional[Dict[str, Any]]:
    """
    仅调整已暂存样本盒位时，允许不传 YBZJ_IDS / PYGD_IDS / FZXX。

    ITEMS 每条须含 F_ID 或库内已有的 F_SGBH，并至少指定 F_WZ 或 F_HZH。
    若 ITEMS 中存在未入库的新试管编号，返回 None 以走常规暂存写入路径。
    """
    if payload.get("YBZJ_IDS") or payload.get("PYGD_IDS") or payload.get("FZXX"):
        return None

    items_raw = payload.get("ITEMS") or payload.get("items")
    if not items_raw:
        return None
    if isinstance(items_raw, dict):
        items_raw = [items_raw]
    items_list = [dict(it) for it in items_raw]
    if not items_list:
        return None

    sgbhs = [
        str(it.get("F_SGBH") or "").strip()
        for it in items_list
        if not it.get("F_ID") and str(it.get("F_SGBH") or "").strip()
    ]
    existing_map = _fetch_ybfz_by_sgbhs(sgbhs) if sgbhs else {}

    resolved: List[Dict[str, Any]] = []
    for it in items_list:
        if it.get("F_ID"):
            resolved.append(dict(it))
            continue

        sgbh = str(it.get("F_SGBH") or "").strip()
        if not sgbh:
            raise ValueError("仅调整盒位时，ITEMS 每条须携带 F_ID 或 F_SGBH")

        existing = existing_map.get(sgbh)
        if not existing:
            return None

        yszt = existing.get("F_YSZT") or "未运送"
        if yszt != "未运送":
            raise ValueError(
                f"试管编号 {sgbh} 当前状态为「{yszt}」，仅可调整未运送记录的位置"
            )

        obj = dict(it)
        obj["F_ID"] = existing["F_ID"]
        resolved.append(obj)

    for it in resolved:
        if it.get("F_WZ") is None and not it.get("F_HZH"):
            raise ValueError(
                f"分装记录 ID={it.get('F_ID')} 须至少指定 F_WZ 或 F_HZH"
            )

    czr = _resolve_operator(user, get_adapter())
    result = _crud_box.update_box_positions(
        {"ITEMS": resolved, "BOXES": payload.get("BOXES") or []},
        czr,
    )
    updated = result.get("updated") or []
    return {"updated": updated, "inserted": updated, "count": result.get("count", len(updated))}


def _try_draft_boxes_only(
    payload: Dict[str, Any],
    user: Dict[str, Any] | None,
) -> Optional[Dict[str, Any]]:
    """
    仅预创建盒子、暂不放入样本：BOXES 非空且 ITEMS / 样本工单 / FZXX 均为空。

    盒号已存在时幂等跳过，不报错。
    """
    if payload.get("YBZJ_IDS") or payload.get("PYGD_IDS") or payload.get("FZXX"):
        return None

    items_raw = payload.get("ITEMS") if "ITEMS" in payload else payload.get("items")
    if items_raw:
        if isinstance(items_raw, dict):
            items_list = [items_raw]
        else:
            items_list = list(items_raw)
        if items_list:
            return None

    boxes_raw = payload.get("BOXES") or []
    if isinstance(boxes_raw, dict):
        boxes_raw = [boxes_raw]
    if not boxes_raw:
        return None

    czr = _resolve_operator(user, get_adapter())
    boxes_result: List[Dict[str, Any]] = []
    seen_hzh: set[str] = set()

    for bx in boxes_raw:
        user_hzh = str(bx.get("F_HZH") or "").strip() or None
        if user_hzh and user_hzh in seen_hzh:
            raise ValueError(f"BOXES 中盒号重复: {user_hzh}")

        if user_hzh:
            existing = _crud_box.get_box_by_hzh(user_hzh)
            if existing:
                seen_hzh.add(user_hzh)
                boxes_result.append({
                    "F_HZH": user_hzh,
                    "F_ID": int(existing["F_ID"]),
                    "created": False,
                    "F_HZH_SOURCE": "existing",
                })
                continue

        tpl_id = bx.get("F_TEMPLATE_ID")
        if not tpl_id:
            tip = f"盒子 {user_hzh}" if user_hzh else "新盒子"
            raise ValueError(f"{tip} 须提供 F_TEMPLATE_ID")

        # 对齐标本号：空号/预览号走规则占号，手填自定义不占号
        box_data = {
            "F_HZH": user_hzh,
            "F_TEMPLATE_ID": tpl_id,
            "F_CZR": czr,
        }
        new_id = _crud_box.create_box(box_data)
        final_hzh = box_data["F_HZH"]
        if final_hzh in seen_hzh:
            raise ValueError(f"BOXES 中盒号重复: {final_hzh}")
        seen_hzh.add(final_hzh)
        boxes_result.append({
            "F_HZH": final_hzh,
            "F_ID": new_id,
            "created": True,
            "F_HZH_SOURCE": box_data.get("F_HZH_SOURCE"),
        })

    created_count = sum(1 for b in boxes_result if b.get("created"))
    return {
        "boxes": boxes_result,
        "inserted": [],
        "count": 0,
        "boxes_created": created_count,
    }


def draft_sample_allocation(payload: Dict[str, Any], user: Dict[str, Any] | None = None) -> Dict[str, Any]:
    """
    暂存分装：将分装信息写入 T_YBFZ，所有记录 F_YSZT='未运送'，不创建运输单。

    与 save_sample_allocation 的区别：
    - 强制所有条目 F_SFYS=False，即不发起运输
    - 不创建 T_YSXX 记录

    支持三种 ITEMS / BOXES 模式：
    - 新增暂存：ITEMS 含 F_SGBH 等完整字段（可不传 YBZJ_IDS/PYGD_IDS）
    - 仅调盒位：ITEMS 含 F_ID（或已有暂存的 F_SGBH）+ F_WZ/F_HZH，
      可不传 YBZJ_IDS/PYGD_IDS/FZXX
    - 仅预建盒：BOXES 非空且 ITEMS 为空，先创建盒子、暂不放入样本
    """
    position_result = _try_draft_position_update(payload, user)
    if position_result is not None:
        return position_result

    boxes_result = _try_draft_boxes_only(payload, user)
    if boxes_result is not None:
        return boxes_result

    # 强制 FZXX 中各分组 F_SFYS=False
    fzxx = payload.get("FZXX") or []
    for info in fzxx:
        info["F_SFYS"] = False

    # 处理 ITEMS 覆盖模式
    items_raw = payload.get("ITEMS") or payload.get("items")
    if items_raw:
        if isinstance(items_raw, dict):
            items_raw = [items_raw]
        for item in items_raw:
            item["F_SFYS"] = False
        payload["ITEMS"] = items_raw

    return save_sample_allocation(payload, user)


def save_sample_allocation_with_scan(payload: Dict[str, Any], user: Dict[str, Any] | None = None) -> Dict[str, Any]:
    """
    手动扫描预制试管编码的分装保存。

    在 save_sample_allocation 基础上，先通过 preview_sample_allocation 生成条目，
    再将 TUBE_CODES 中的编码按顺序覆盖每条记录的 F_SGBH，最终写库。

    数量不一致时的处理规则：
      - 试管编码 > 条目数：忽略多余编码，返回警告
      - 试管编码 < 条目数：缺少编码的条目跳过不保存，返回警告
      - 二者相等：正常保存，无警告
    """
    tube_codes = _normalize_tube_codes(payload.pop("TUBE_CODES", None) or [])
    _validate_scan_tube_codes(tube_codes)

    # 生成预览条目（使用原版逻辑，不走 ITEMS override 路径）
    preview_result = preview_sample_allocation(payload)
    items: List[Dict[str, Any]] = preview_result["items"]
    total: int = preview_result["total"]

    tube_count = len(tube_codes)
    warning: Optional[str] = None

    if tube_count != total:
        warning = f"样本管数量与样本数不一致（{tube_count}/{total}）"

    # 按顺序替换 F_SGBH；超出试管编码范围的条目标记为空
    for idx, item in enumerate(items):
        item["F_SGBH"] = tube_codes[idx] if idx < tube_count else ""

    # 仅保存拥有有效试管编码的条目，空编码的跳过
    valid_items = [it for it in items if it.get("F_SGBH")]

    if not valid_items:
        raise ValueError("没有有效的试管编码，无法保存")

    # 将准备好的条目注入 payload，委托原版 save 函数执行写库
    payload["ITEMS"] = valid_items
    result = save_sample_allocation(payload, user)

    if warning:
        result["warning"] = warning
    return result


def draft_sample_allocation_with_scan(payload: Dict[str, Any], user: Dict[str, Any] | None = None) -> Dict[str, Any]:
    """
    手动扫描预制试管编码的分装暂存。

    在 draft_sample_allocation 基础上，先通过 preview_sample_allocation 生成条目，
    再将 TUBE_CODES 中的编码按顺序覆盖每条记录的 F_SGBH，最终以 F_YSZT='未运送' 写库，
    不创建运输单（T_YSXX）。

    数量不一致时的处理规则：
      - 试管编码 > 条目数：忽略多余编码，返回警告
      - 试管编码 < 条目数：缺少编码的条目跳过不保存，返回警告
      - 二者相等：正常暂存，无警告
    """
    tube_codes = _normalize_tube_codes(payload.pop("TUBE_CODES", None) or [])
    _validate_scan_tube_codes(tube_codes)

    # 生成预览条目（不走 ITEMS override 路径）
    preview_result = preview_sample_allocation(payload)
    items: List[Dict[str, Any]] = preview_result["items"]
    total: int = preview_result["total"]

    tube_count = len(tube_codes)
    warning: Optional[str] = None

    if tube_count != total:
        warning = f"样本管数量与样本数不一致（{tube_count}/{total}）"

    # 按顺序替换 F_SGBH；超出试管编码范围的条目标记为空
    for idx, item in enumerate(items):
        item["F_SGBH"] = tube_codes[idx] if idx < tube_count else ""

    # 仅保存拥有有效试管编码的条目，强制 F_SFYS=False（不发起运输）
    valid_items = [it for it in items if it.get("F_SGBH")]

    if not valid_items:
        raise ValueError("没有有效的试管编码，无法暂存")

    for item in valid_items:
        item["F_SFYS"] = False

    # 委托 draft 函数执行写库（内部会再次确保不发起运输）
    payload["ITEMS"] = valid_items
    result = draft_sample_allocation(payload, user)

    if warning:
        result["warning"] = warning
    return result

