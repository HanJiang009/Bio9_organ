"""
盒子管理模块 CRUD
"""
from collections import defaultdict
from datetime import datetime
from typing import Dict, Any, List, Optional, Set, Tuple
from app.db.factory import get_adapter
import logging

logger = logging.getLogger(__name__)


def _absolute_index_from_wz(wz_val: Any, ls: int, *, capacity: int = 0) -> Optional[int]:
    """
    将 F_WZ 转为 0-based 绝对格子索引（与 sample_linkage._parse_wz_to_index 一致）。
    """
    if wz_val is None or ls <= 0:
        return None
    grid_pos = _wz_to_grid_pos(wz_val, ls)
    if grid_pos is None:
        return None
    idx = grid_pos - 1
    if capacity > 0 and not (0 <= idx < capacity):
        return None
    return idx


def _index_to_wz_label(abs_index: int, ls: int) -> str:
    """0-based 绝对索引 → 字母数字位置（如 A1、D4）。"""
    row_idx = abs_index // ls
    col_num = abs_index % ls + 1
    label = ""
    n = row_idx
    while True:
        label = chr(ord("A") + n % 26) + label
        n = n // 26 - 1
        if n < 0:
            break
    return f"{label}{col_num}"


def get_box_occupancy(hzh: str) -> Dict[str, Any]:
    """
    查询盒子占用情况（按唯一格子统计，与平面图一致）。

    Returns:
        occupied_indices: 0-based 已占格子索引集合
        wz_index_to_fid: 格子索引 → 最新一条 T_YBFZ.F_ID（按 F_ID 较大者优先）
        tubes_count: 盒内样本管记录总数（含同格重复）
        used: 已占格子数（唯一位置）
        capacity / remaining
    """
    box = get_box_by_hzh(hzh)
    if not box:
        raise ValueError(f"盒号 {hzh} 不存在")

    hs = int(box.get("F_TPLT_HS") or 0)
    ls = int(box.get("F_TPLT_LS") or 0)
    capacity = hs * ls

    adapter = get_adapter()
    with adapter.connect() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT F_ID, F_WZ FROM T_YBFZ WHERE F_HZH = ? ORDER BY F_ID ASC",
            (hzh,),
        )
        rows = cur.fetchall()

    occupied: Set[int] = set()
    wz_index_to_fid: Dict[int, int] = {}
    for fid, wz in rows:
        idx = _absolute_index_from_wz(wz, ls, capacity=capacity) if ls > 0 else None
        if idx is None:
            continue
        occupied.add(idx)
        wz_index_to_fid[idx] = int(fid)

    used_slots = len(occupied)
    tubes_count = len(rows)
    return {
        "F_HZH": hzh,
        "F_STATUS": box.get("F_STATUS"),
        "F_TEMPLATE_ID": box.get("F_TEMPLATE_ID"),
        "F_TPLT_NAME": box.get("F_TPLT_NAME"),
        "occupied_indices": occupied,
        "wz_index_to_fid": wz_index_to_fid,
        "tubes_count": tubes_count,
        "used": used_slots,
        "capacity": capacity,
        "remaining": capacity - used_slots,
    }


def _wz_to_grid_pos(wz_val: Any, cols: int) -> Optional[int]:
    """
    将 T_YBFZ.F_WZ 转换为 1-based 格子序号（行优先），用于盒子平面图渲染。
    兼容两种存储格式：
    - 字母数字（如 A1、B3）→ 解析后计算
    - 纯数字（如 1、5、10） → 直接使用（历史数据兼容）
    """
    if wz_val is None:
        return None
    try:
        return int(wz_val)
    except (TypeError, ValueError):
        pass
    wz_str = str(wz_val).strip().upper()
    alpha = "".join(c for c in wz_str if c.isalpha())
    digit = "".join(c for c in wz_str if c.isdigit())
    if not alpha or not digit:
        return None
    row_idx = 0
    for c in alpha:
        row_idx = row_idx * 26 + (ord(c) - ord("A")) + 1
    row_idx -= 1
    col_idx = int(digit) - 1
    return row_idx * cols + col_idx + 1  # 转为 1-based 序号


def export_wz_as_int(
    wz_val: Any,
    cols: Optional[int] = None,
    *,
    hzh: Optional[str] = None,
) -> Optional[int]:
    """
    对外接口：将 T_YBFZ.F_WZ 转为 int（1-based 格子序号，行优先）。

    与 sample_linkage 写入 A1/A2 的逻辑互逆：
      写入：absolute_pos → row/col → f"{_row_label(row)}{col}"
      读出：A1/A2 + F_LS(列数) → _wz_to_grid_pos → 1-based int
    纯数字存储（如 "1"）则直接 int()。

    若 T_YBFZ.F_LS 为空但提供了 F_HZH，则从 T_BOX 模板补列数后再解析 A1 格式。
    """
    if wz_val is None:
        return None
    if isinstance(wz_val, str) and not wz_val.strip():
        return None
    try:
        cols_int = int(cols) if cols is not None else 0
    except (TypeError, ValueError):
        cols_int = 0
    if cols_int <= 0 and hzh:
        box = get_box_by_hzh(str(hzh).strip())
        if box and box.get("F_TPLT_LS"):
            try:
                cols_int = int(box["F_TPLT_LS"])
            except (TypeError, ValueError):
                cols_int = 0
    if cols_int > 0:
        return _wz_to_grid_pos(wz_val, cols_int)
    try:
        return int(wz_val)
    except (TypeError, ValueError):
        return None


# ============================================
# 盒子模板相关函数
# ============================================

def list_box_templates(query: Dict[str, Any]) -> Dict[str, Any]:
    """查询盒子模板列表"""
    adapter = get_adapter()

    where: List[str] = []
    params: List[Any] = []

    if query.get("F_ENABLED") is not None:
        where.append("F_ENABLED = ?")
        params.append(query["F_ENABLED"])

    where_sql = (" WHERE " + " AND ".join(where)) if where else ""

    base_sql = f"""
        SELECT F_ID, F_CODE, F_NAME, F_HS, F_LS, F_ENABLED, F_SORT,
               F_BZ, F_CREATE_TIME, F_UPDATE_TIME
        FROM T_BOX_TPLT
        {where_sql}
        ORDER BY F_SORT ASC, F_ID ASC
    """
    count_sql = f"SELECT COUNT(1) FROM T_BOX_TPLT{where_sql}"

    sql, extra = adapter.paginate(base_sql, skip=query.get("skip", 0), limit=query.get("limit", 100))

    with adapter.connect() as conn:
        cur = conn.cursor()
        cur.execute(count_sql, tuple(params))
        total = int(cur.fetchone()[0])

        cur.execute(sql, tuple(params + extra))
        rows = cur.fetchall()
        cols = [c[0] for c in cur.description]
        items: List[Dict[str, Any]] = []
        for row in rows:
            item = dict(zip(cols, row))
            item["F_CAPACITY"] = (item.get("F_HS") or 0) * (item.get("F_LS") or 0)
            items.append(item)

        return {"items": items, "total": total}


def get_box_template_by_id(template_id: int) -> Optional[Dict[str, Any]]:
    """根据ID查询盒子模板"""
    adapter = get_adapter()
    sql = """
        SELECT F_ID, F_CODE, F_NAME, F_HS, F_LS, F_ENABLED, F_SORT,
               F_BZ, F_CREATE_TIME, F_UPDATE_TIME
        FROM T_BOX_TPLT WHERE F_ID = ?
    """
    with adapter.connect() as conn:
        cur = conn.cursor()
        cur.execute(sql, (template_id,))
        row = cur.fetchone()
        if row:
            cols = [c[0] for c in cur.description]
            item = dict(zip(cols, row))
            item["F_CAPACITY"] = (item.get("F_HS") or 0) * (item.get("F_LS") or 0)
            return item
    return None


def get_box_template_by_code(code: str) -> Optional[Dict[str, Any]]:
    """根据代码查询盒子模板"""
    adapter = get_adapter()
    sql = """
        SELECT F_ID, F_CODE, F_NAME, F_HS, F_LS, F_ENABLED, F_SORT,
               F_BZ, F_CREATE_TIME, F_UPDATE_TIME
        FROM T_BOX_TPLT WHERE F_CODE = ?
    """
    with adapter.connect() as conn:
        cur = conn.cursor()
        cur.execute(sql, (code,))
        row = cur.fetchone()
        if row:
            cols = [c[0] for c in cur.description]
            return dict(zip(cols, row))
    return None


def create_box_template(data: Dict[str, Any]) -> int:
    """创建盒子模板，返回新ID"""
    adapter = get_adapter()

    existing = get_box_template_by_code(data["F_CODE"])
    if existing:
        raise ValueError(f"模板代码 {data['F_CODE']} 已存在")

    sql = """
        INSERT INTO T_BOX_TPLT (F_CODE, F_NAME, F_HS, F_LS, F_ENABLED, F_SORT, F_BZ)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """
    with adapter.connect() as conn:
        cur = conn.cursor()
        cur.execute(sql, (
            data["F_CODE"],
            data["F_NAME"],
            data["F_HS"],
            data["F_LS"],
            data.get("F_ENABLED", 1),
            data.get("F_SORT", 0),
            data.get("F_BZ"),
        ))
        cur.execute("SELECT MAX(F_ID) FROM T_BOX_TPLT")
        new_id = cur.fetchone()[0]
        conn.commit()
        return int(new_id)


def update_box_template(template_id: int, data: Dict[str, Any]) -> bool:
    """更新盒子模板"""
    adapter = get_adapter()

    existing = get_box_template_by_id(template_id)
    if not existing:
        raise ValueError(f"模板ID {template_id} 不存在")

    updates: List[str] = []
    params: List[Any] = []

    for field in ["F_CODE", "F_NAME", "F_HS", "F_LS", "F_ENABLED", "F_SORT", "F_BZ"]:
        if field in data and data[field] is not None:
            updates.append(f"{field} = ?")
            params.append(data[field])

    if not updates:
        return True

    updates.append(f"F_UPDATE_TIME = {adapter.now()}")

    params.append(template_id)
    sql = f"UPDATE T_BOX_TPLT SET {', '.join(updates)} WHERE F_ID = ?"

    with adapter.connect() as conn:
        cur = conn.cursor()
        cur.execute(sql, tuple(params))
        conn.commit()
        return True


def delete_box_template(template_id: int) -> bool:
    """删除盒子模板（不可删除已被盒子使用的模板）"""
    adapter = get_adapter()

    existing = get_box_template_by_id(template_id)
    if not existing:
        raise ValueError(f"模板ID {template_id} 不存在")

    with adapter.connect() as conn:
        cur = conn.cursor()
        cur.execute("SELECT COUNT(1) FROM T_BOX WHERE F_TEMPLATE_ID = ?", (template_id,))
        ref_count = int(cur.fetchone()[0])
        if ref_count > 0:
            raise ValueError(f"该模板已被 {ref_count} 个盒子使用，无法删除")

        cur.execute("DELETE FROM T_BOX_TPLT WHERE F_ID = ?", (template_id,))
        conn.commit()
        return True


# ============================================
# 盒子使用相关函数
# ============================================

def _range_bound(value: Any, *, end: bool) -> str:
    """日期补全到当天起止，已带时分秒的原样使用。"""
    s = str(value or "").strip()
    if len(s) <= 10:
        return s + (" 23:59:59" if end else " 00:00:00")
    return s


_box_time_backfilled = False


def ensure_box_create_time() -> None:
    """已有盒子若没写创建时间，用盒内最早分装时间补一次。空盒保持为空。"""
    global _box_time_backfilled
    if _box_time_backfilled:
        return
    adapter = get_adapter()
    try:
        rows, _cols = adapter.fetch_all(
            """
            SELECT b.F_ID, MIN(fz.F_CJSJ) AS t
            FROM T_BOX b
            INNER JOIN T_YBFZ fz ON fz.F_HZH = b.F_HZH
            WHERE b.F_CREATE_TIME IS NULL
              AND fz.F_CJSJ IS NOT NULL
              AND fz.F_HZH IS NOT NULL AND fz.F_HZH <> ''
            GROUP BY b.F_ID
            """
        )
        if rows:
            with adapter.connect() as conn:
                cur = conn.cursor()
                for box_id, created_at in rows:
                    if box_id is None or created_at is None:
                        continue
                    cur.execute(
                        "UPDATE T_BOX SET F_CREATE_TIME = ? WHERE F_ID = ? AND F_CREATE_TIME IS NULL",
                        (created_at, box_id),
                    )
                conn.commit()
        _box_time_backfilled = True
    except Exception:
        logger.exception("回填盒子暂存时间失败")


def list_boxes(query: Dict[str, Any]) -> Dict[str, Any]:
    """查询盒子使用列表，关联模板信息"""
    ensure_box_create_time()
    adapter = get_adapter()

    where: List[str] = []
    params: List[Any] = []

    if query.get("F_HZH"):
        where.append("b.F_HZH LIKE ?")
        params.append(f"%{query['F_HZH'].strip()}%")

    if query.get("F_STATUS"):
        where.append("b.F_STATUS = ?")
        params.append(query["F_STATUS"])

    if query.get("F_TEMPLATE_ID") is not None:
        where.append("b.F_TEMPLATE_ID = ?")
        params.append(query["F_TEMPLATE_ID"])

    zcsj_start = query.get("F_ZCSJ_START") or query.get("F_CREATE_START")
    zcsj_end = query.get("F_ZCSJ_END") or query.get("F_CREATE_END")
    if zcsj_start:
        where.append("b.F_CREATE_TIME >= ?")
        params.append(_range_bound(zcsj_start, end=False))
    if zcsj_end:
        where.append("b.F_CREATE_TIME <= ?")
        params.append(_range_bound(zcsj_end, end=True))

    where_sql = (" WHERE " + " AND ".join(where)) if where else ""

    base_sql = f"""
        SELECT b.F_ID, b.F_HZH, b.F_TEMPLATE_ID, b.F_STATUS, b.F_LOCATION,
               b.F_CZR, b.F_BZ, b.F_CREATE_TIME, b.F_UPDATE_TIME,
               t.F_NAME AS F_TPLT_NAME, t.F_HS AS F_TPLT_HS, t.F_LS AS F_TPLT_LS
        FROM T_BOX b
        LEFT JOIN T_BOX_TPLT t ON t.F_ID = b.F_TEMPLATE_ID
        {where_sql}
        ORDER BY b.F_CREATE_TIME DESC, b.F_ID DESC
    """
    count_sql = f"""
        SELECT COUNT(1) FROM T_BOX b
        LEFT JOIN T_BOX_TPLT t ON t.F_ID = b.F_TEMPLATE_ID
        {where_sql}
    """

    sql, extra = adapter.paginate(base_sql, skip=query.get("skip", 0), limit=query.get("limit", 50))

    with adapter.connect() as conn:
        cur = conn.cursor()
        cur.execute(count_sql, tuple(params))
        total = int(cur.fetchone()[0])

        cur.execute(sql, tuple(params + extra))
        rows = cur.fetchall()
        cols = [c[0] for c in cur.description]
        items: List[Dict[str, Any]] = [dict(zip(cols, r)) for r in rows]

        return {"items": items, "total": total}


def get_box_by_id(box_id: int) -> Optional[Dict[str, Any]]:
    """根据ID查询盒子（含模板信息）"""
    adapter = get_adapter()
    sql = """
        SELECT b.F_ID, b.F_HZH, b.F_TEMPLATE_ID, b.F_STATUS, b.F_LOCATION,
               b.F_CZR, b.F_BZ, b.F_CREATE_TIME, b.F_UPDATE_TIME,
               t.F_NAME AS F_TPLT_NAME, t.F_HS AS F_TPLT_HS, t.F_LS AS F_TPLT_LS
        FROM T_BOX b
        LEFT JOIN T_BOX_TPLT t ON t.F_ID = b.F_TEMPLATE_ID
        WHERE b.F_ID = ?
    """
    with adapter.connect() as conn:
        cur = conn.cursor()
        cur.execute(sql, (box_id,))
        row = cur.fetchone()
        if row:
            cols = [c[0] for c in cur.description]
            return dict(zip(cols, row))
    return None


def get_box_by_hzh(hzh: str) -> Optional[Dict[str, Any]]:
    """根据盒号查询盒子（含模板信息）"""
    adapter = get_adapter()
    sql = """
        SELECT b.F_ID, b.F_HZH, b.F_TEMPLATE_ID, b.F_STATUS, b.F_LOCATION,
               b.F_CZR, b.F_BZ, b.F_CREATE_TIME, b.F_UPDATE_TIME,
               t.F_NAME AS F_TPLT_NAME, t.F_HS AS F_TPLT_HS, t.F_LS AS F_TPLT_LS
        FROM T_BOX b
        LEFT JOIN T_BOX_TPLT t ON t.F_ID = b.F_TEMPLATE_ID
        WHERE b.F_HZH = ?
    """
    with adapter.connect() as conn:
        cur = conn.cursor()
        cur.execute(sql, (hzh,))
        row = cur.fetchone()
        if row:
            cols = [c[0] for c in cur.description]
            return dict(zip(cols, row))
    return None


def get_box_detail(hzh: str, yszt: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """
    查询盒子详情：含模板信息、完整管列表、容量统计。

    Args:
        hzh:   盒号
        yszt:  可选，按样本管运输状态过滤（如 '未运送'）；
               不传则返回盒内所有状态的样本管。
               过滤时仅影响 tubes 列表，capacity/used/remaining 始终基于盒内全量计算。

    tubes 每条记录包含 T_YBFZ 完整字段（含 F_ID 供后续接口使用）及推导字段 F_SFYS。
    """
    from app.crud.sample_linkage import _parse_datetime

    box = get_box_by_hzh(hzh)
    if not box:
        return None

    adapter = get_adapter()

    # 构建 tubes 查询（完整字段）
    where_clauses: List[str] = ["F_HZH = ?"]
    params: List[Any] = [hzh]
    if yszt:
        where_clauses.append("F_YSZT = ?")
        params.append(yszt)

    tube_sql = f"""
        SELECT F_ID, F_SGBH, F_ZZID, F_YBZJ_ID, F_PYGD_ID, F_DC,
               F_YBLX, F_YBGLX, F_CCXS, F_FGS, F_TJ, F_XBSL, F_SPREC,
               F_BZ, F_HZH, F_WZ, F_YSZT,
               F_BRBH, F_ZYH, F_MZH, F_XM, F_XB, F_NL, F_NLDW,
               F_MZ, F_HY, F_ZY, F_DZ, F_DH, F_CHH, F_SFZH,
               F_LCZD, F_LYKS, F_KS, F_CXSJ, F_XMBH, F_CJSJ
        FROM T_YBFZ
        WHERE {" AND ".join(where_clauses)}
        ORDER BY F_WZ ASC, F_CJSJ ASC
    """

    with adapter.connect() as conn:
        cur = conn.cursor()
        cur.execute(tube_sql, tuple(params))
        rows = cur.fetchall()
        cols = [c[0] for c in cur.description]
        tubes: List[Dict[str, Any]] = []
        for row in rows:
            item = dict(zip(cols, row))
            if item.get("F_CJSJ"):
                item["F_CJSJ"] = _parse_datetime(item["F_CJSJ"])
            if item.get("F_CXSJ"):
                item["F_CXSJ"] = _parse_datetime(item["F_CXSJ"])
            # 推导 F_SFYS：已接收的记录视为不可再发起运输，其余默认可运输
            item["F_SFYS"] = item.get("F_YSZT") not in ("已接收",)
            tubes.append(item)

    occupancy = get_box_occupancy(hzh)

    box["tubes"] = tubes
    box["tubes_total"] = len(tubes)   # 过滤后的管数
    box["tubes_records_total"] = occupancy["tubes_count"]  # 盒内全量记录数（含同格重复）
    box["capacity"] = occupancy["capacity"]
    box["used"] = occupancy["used"]   # 已占唯一格子数
    box["remaining"] = occupancy["remaining"]
    return box


def create_box(data: Dict[str, Any]) -> int:
    """创建盒子使用记录，返回新ID。

    盒号解析对齐标本号 save-and-distribute：
    - 未传 F_HZH / 等于当前 peek → 按规则 generate 占号
    - 手填且不等于 peek → 直接使用，不占序列
    解析后的最终盒号写回 data["F_HZH"]（及 data["F_HZH_SOURCE"]），供调用方同步 ITEMS。
    """
    adapter = get_adapter()

    resolved = resolve_hzh_for_save(
        data.get("F_HZH"),
        data.get("hzh_context") if isinstance(data.get("hzh_context"), dict) else None,
    )
    data["F_HZH"] = resolved["F_HZH"]
    data["F_HZH_SOURCE"] = resolved["source"]

    if hzh_exists(data["F_HZH"]):
        raise ValueError(f"盒号 {data['F_HZH']} 已存在")

    template = get_box_template_by_id(data["F_TEMPLATE_ID"])
    if not template:
        raise ValueError(f"模板ID {data['F_TEMPLATE_ID']} 不存在")

    created_at = data.get("F_CREATE_TIME") or datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    sql = """
        INSERT INTO T_BOX (F_HZH, F_TEMPLATE_ID, F_STATUS, F_LOCATION, F_CZR, F_BZ, F_CREATE_TIME)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """
    with adapter.connect() as conn:
        cur = conn.cursor()
        cur.execute(sql, (
            data["F_HZH"],
            data["F_TEMPLATE_ID"],
            data.get("F_STATUS", "未运送"),
            data.get("F_LOCATION"),
            data.get("F_CZR"),
            data.get("F_BZ"),
            created_at,
        ))
        cur.execute("SELECT MAX(F_ID) FROM T_BOX")
        new_id = cur.fetchone()[0]
        conn.commit()
        return int(new_id)


def update_box(box_id: int, data: Dict[str, Any]) -> bool:
    """更新盒子状态/位置/备注"""
    adapter = get_adapter()

    existing = get_box_by_id(box_id)
    if not existing:
        raise ValueError(f"盒子ID {box_id} 不存在")

    updates: List[str] = []
    params: List[Any] = []

    for field in ["F_STATUS", "F_LOCATION", "F_BZ"]:
        if field in data and data[field] is not None:
            updates.append(f"{field} = ?")
            params.append(data[field])

    if not updates:
        return True

    updates.append(f"F_UPDATE_TIME = {adapter.now()}")

    params.append(box_id)
    sql = f"UPDATE T_BOX SET {', '.join(updates)} WHERE F_ID = ?"

    with adapter.connect() as conn:
        cur = conn.cursor()
        cur.execute(sql, tuple(params))
        conn.commit()
        return True


def delete_box(box_id: int) -> bool:
    """删除盒子记录（不可删除含样本管的盒子）"""
    adapter = get_adapter()

    existing = get_box_by_id(box_id)
    if not existing:
        raise ValueError(f"盒子ID {box_id} 不存在")

    with adapter.connect() as conn:
        cur = conn.cursor()
        cur.execute("SELECT COUNT(1) FROM T_YBFZ WHERE F_HZH = ?", (existing["F_HZH"],))
        tube_count = int(cur.fetchone()[0])
        if tube_count > 0:
            raise ValueError(f"该盒子已有 {tube_count} 根样本管，无法删除")

        cur.execute("DELETE FROM T_BOX WHERE F_ID = ?", (box_id,))
        conn.commit()
        return True


def get_box_layout(hzh: str) -> Dict[str, Any]:
    """
    查询盒子平面图：返回盒子尺寸及每个格子的占用状态与样本信息。

    格子序号 wz 从 1 开始，按行优先（从左到右、从上到下）排列。
    row = (wz - 1) // cols + 1
    col = (wz - 1) % cols + 1
    """
    box = get_box_by_hzh(hzh)
    if not box:
        raise ValueError(f"盒号 {hzh} 未创建，可通过发起运输或暂存进行创建")

    rows = box.get("F_TPLT_HS") or 0
    cols = box.get("F_TPLT_LS") or 0
    capacity = rows * cols

    adapter = get_adapter()
    tube_sql = """
        SELECT F_ID, F_SGBH, F_ZZID, F_YBZJ_ID, F_PYGD_ID,
               F_DC, F_YBLX, F_YBGLX, F_XM, F_BRBH, F_ZYH, F_YSZT, F_CJSJ, F_WZ
        FROM T_YBFZ
        WHERE F_HZH = ?
    """
    with adapter.connect() as conn:
        cur = conn.cursor()
        cur.execute(tube_sql, (hzh,))
        tube_rows = cur.fetchall()
        tube_cols = [c[0] for c in cur.description]
        tubes = [dict(zip(tube_cols, r)) for r in tube_rows]

    # 以 1-based 序号为键建立位置 → 样本映射；F_WZ 为 None 的记录跳过（不在格子上）
    wz_map: Dict[int, Dict[str, Any]] = {}
    for t in tubes:
        wz_val = t.get("F_WZ")
        grid_pos = _wz_to_grid_pos(wz_val, cols)
        if grid_pos is not None:
            wz_map[grid_pos] = t

    cells = []
    for wz in range(1, capacity + 1):
        row = (wz - 1) // cols + 1 if cols > 0 else 1
        col = (wz - 1) % cols + 1 if cols > 0 else wz
        sample = wz_map.get(wz)
        cells.append({
            "wz": wz,
            "row": row,
            "col": col,
            "occupied": sample is not None,
            "sample": sample,
        })

    used = len(wz_map)
    return {
        "F_HZH": hzh,
        "F_TEMPLATE_ID": box.get("F_TEMPLATE_ID"),
        "F_TPLT_NAME": box.get("F_TPLT_NAME"),
        "F_STATUS": box.get("F_STATUS"),
        "F_LOCATION": box.get("F_LOCATION"),
        "rows": rows,
        "cols": cols,
        "capacity": capacity,
        "used": used,
        "remaining": capacity - used,
        "cells": cells,
    }


def get_box_capacity(hzh: str) -> Dict[str, Any]:
    """查询盒子容量信息：used 按唯一格子统计，与平面图一致。"""
    occ = get_box_occupancy(hzh)
    return {
        "F_HZH": occ["F_HZH"],
        "F_STATUS": occ["F_STATUS"],
        "F_TEMPLATE_ID": occ["F_TEMPLATE_ID"],
        "F_TPLT_NAME": occ["F_TPLT_NAME"],
        "capacity": occ["capacity"],
        "used": occ["used"],
        "remaining": occ["remaining"],
        "tubes_count": occ["tubes_count"],
    }


# ============================================
# 盒子维度分装管理
# ============================================

def try_update_box_status_after_transport(affected_ybfz_ids: List[int]) -> None:
    """
    在运输发起后，根据盒子内样本管的聚合状态自动更新 T_BOX.F_STATUS：

    - 盒内所有样本均非'未运送'（全部在途或已接收）→ '已转运'
    - 盒内仍有'未运送'样本，但同时存在在途或已接收样本（部分运输）→ '运送中'
    - 全部为'未运送'时不变（不应出现此情况，防御性保留）
    """
    if not affected_ybfz_ids:
        return

    adapter = get_adapter()
    placeholders = ",".join(["?"] * len(affected_ybfz_ids))

    # 找出本次运输涉及的盒号
    hzh_sql = f"""
        SELECT DISTINCT F_HZH FROM T_YBFZ
        WHERE F_ID IN ({placeholders}) AND F_HZH IS NOT NULL AND F_HZH <> ''
    """
    hzh_rows, _ = adapter.fetch_all(hzh_sql, affected_ybfz_ids)
    hzh_list = [r[0] for r in hzh_rows if r[0]]

    if not hzh_list:
        return

    hzh_phs = ",".join(["?"] * len(hzh_list))

    # 按盒号统计各状态样本数
    stat_sql = f"""
        SELECT F_HZH,
               SUM(CASE WHEN F_YSZT = '未运送' OR F_YSZT IS NULL OR F_YSZT = '' THEN 1 ELSE 0 END) AS cnt_unsent,
               SUM(CASE WHEN F_YSZT = '运送中' THEN 1 ELSE 0 END) AS cnt_transit,
               SUM(CASE WHEN F_YSZT = '已接收' THEN 1 ELSE 0 END) AS cnt_received
        FROM T_YBFZ
        WHERE F_HZH IN ({hzh_phs})
        GROUP BY F_HZH
    """
    stat_rows, stat_cols = adapter.fetch_all(stat_sql, hzh_list)

    to_seal: List[str] = []     # 无未运送样本 → 已转运
    to_transit: List[str] = []  # 有部分在途   → 运送中

    for row in stat_rows:
        d = dict(zip(stat_cols, row))
        hzh = d["F_HZH"]
        cnt_unsent = int(d.get("cnt_unsent") or 0)
        cnt_transit = int(d.get("cnt_transit") or 0)
        cnt_received = int(d.get("cnt_received") or 0)

        if cnt_unsent == 0:
            to_seal.append(hzh)
        elif cnt_transit > 0 or cnt_received > 0:
            to_transit.append(hzh)

    if not to_seal and not to_transit:
        return

    with adapter.connect() as conn:
        cur = conn.cursor()
        if to_seal:
            seal_phs = ",".join(["?"] * len(to_seal))
            cur.execute(
                f"UPDATE T_BOX SET F_STATUS = '已转运' WHERE F_HZH IN ({seal_phs})",
                tuple(to_seal),
            )
        if to_transit:
            transit_phs = ",".join(["?"] * len(to_transit))
            cur.execute(
                f"UPDATE T_BOX SET F_STATUS = '运送中' WHERE F_HZH IN ({transit_phs}) AND F_STATUS = '未运送'",
                tuple(to_transit),
            )
        conn.commit()



def update_box_positions(data: Dict[str, Any], czr: Optional[str] = None) -> Dict[str, Any]:
    """
    批量更新暂存分装记录（F_YSZT='未运送'）的盒号和格子坐标。

    仅允许更新 F_YSZT='未运送' 的记录；已在运途或已接收的记录无法调整位置。
    若 ITEMS 中某条记录需要换盒，会自动校验目标盒子的剩余容量；
    若目标盒子不存在且 BOXES 中提供了 F_TEMPLATE_ID，则自动创建。
    """
    items: List[Dict[str, Any]] = data.get("ITEMS") or []
    boxes_input: Dict[str, Dict[str, Any]] = {
        b["F_HZH"]: b for b in (data.get("BOXES") or [])
    }

    if not items:
        raise ValueError("请提供至少一条位置更新记录")

    adapter = get_adapter()

    # ── 步骤 1：验证所有 F_ID 存在且状态为未运送 ────────────────────
    fz_ids = [int(it["F_ID"]) for it in items]
    placeholders = ",".join(["?"] * len(fz_ids))
    check_sql = f"SELECT F_ID, F_YSZT, F_HZH FROM T_YBFZ WHERE F_ID IN ({placeholders})"
    rows, cols_desc = adapter.fetch_all(check_sql, fz_ids)

    existing: Dict[int, Dict[str, Any]] = {
        int(r[0]): {"F_YSZT": r[1], "F_HZH": r[2]}
        for r in rows
    }
    for it in items:
        fz_id = int(it["F_ID"])
        if fz_id not in existing:
            raise ValueError(f"分装记录 ID={fz_id} 不存在")
        cur_yszt = existing[fz_id]["F_YSZT"] or "未运送"
        if cur_yszt not in ("未运送", "", None):
            raise ValueError(
                f"分装记录 ID={fz_id} 当前状态为'{cur_yszt}'，"
                f"仅允许调整'未运送'状态的记录位置"
            )

    # ── 步骤 2：按目标盒号分组，校验换盒时的容量 ─────────────────────
    # 换盒：原盒 != 目标盒；仅换位不换盒则不影响容量
    new_to_hzh: Dict[str, List[int]] = defaultdict(list)  # 目标盒号 → 换入的 fz_id 列表
    for it in items:
        fz_id = int(it["F_ID"])
        target_hzh = it.get("F_HZH") or existing[fz_id]["F_HZH"]
        origin_hzh = existing[fz_id]["F_HZH"]
        if target_hzh and target_hzh != origin_hzh:
            new_to_hzh[target_hzh].append(fz_id)

    for target_hzh, moving_ids in new_to_hzh.items():
        box = get_box_by_hzh(target_hzh)
        if not box:
            bx = boxes_input.get(target_hzh, {})
            tpl_id = bx.get("F_TEMPLATE_ID")
            if not tpl_id:
                raise ValueError(
                    f"目标盒子 {target_hzh} 不存在且未提供模板ID，"
                    f"请在 BOXES 参数中指定 F_TEMPLATE_ID"
                )
            create_box({"F_HZH": target_hzh, "F_TEMPLATE_ID": tpl_id, "F_CZR": czr})

        cap = get_box_capacity(target_hzh)
        if cap["remaining"] < len(moving_ids):
            raise ValueError(
                f"目标盒子 {target_hzh} 空间不足（剩余 {cap['remaining']} 格，"
                f"本次换入 {len(moving_ids)} 根）"
            )

    # ── 步骤 2.5：校验目标格子不与库内/本批次其它管冲突 ─────────────
    from app.crud.sample_linkage import enrich_items_box_dims, resolve_box_template_dims

    moving_ids = {int(it["F_ID"]) for it in items}
    planned: Dict[Tuple[str, int], int] = {}
    for it in items:
        fz_id = int(it["F_ID"])
        target_hzh = (it.get("F_HZH") or existing[fz_id]["F_HZH"] or "").strip()
        target_wz = it.get("F_WZ")
        if not target_hzh or target_wz is None or str(target_wz).strip() == "":
            continue
        hs, ls = resolve_box_template_dims(target_hzh, boxes_input)
        if not ls:
            raise ValueError(f"无法确定盒子 {target_hzh} 的列数，无法校验格子坐标")
        capacity = (hs or 0) * (ls or 0)
        idx = _absolute_index_from_wz(target_wz, int(ls), capacity=capacity)
        if idx is None:
            raise ValueError(f"分装记录 ID={fz_id} 的格子坐标 {target_wz!r} 无效")
        key = (target_hzh, idx)
        if key in planned and planned[key] != fz_id:
            wz_label = _index_to_wz_label(idx, int(ls))
            raise ValueError(
                f"位置更新冲突：盒子 {target_hzh} 格子 {wz_label} "
                f"在本批次中被多条记录指定"
            )
        planned[key] = fz_id

    for (target_hzh, idx), fz_id in planned.items():
        try:
            occ = get_box_occupancy(target_hzh)
        except ValueError:
            continue
        owner = occ.get("wz_index_to_fid") or {}
        existing_fid = owner.get(idx)
        if existing_fid is not None and existing_fid not in moving_ids:
            _, ls_val = resolve_box_template_dims(target_hzh, boxes_input)
            wz_label = _index_to_wz_label(idx, int(ls_val or 1))
            raise ValueError(
                f"盒子 {target_hzh} 格子 {wz_label} 已被样本管 ID={existing_fid} 占用，"
                f"无法将 ID={fz_id} 移动至该位置"
            )

    # ── 步骤 3：执行更新 ─────────────────────────────────────────────

    enrich_items_box_dims(items, data.get("BOXES") or [])

    updated_ids: List[int] = []
    with adapter.connect() as conn:
        cur = conn.cursor()
        for it in items:
            fz_id = int(it["F_ID"])
            target_hzh = it.get("F_HZH") or existing[fz_id]["F_HZH"]
            target_wz = it.get("F_WZ")

            set_clauses: List[str] = []
            params: List[Any] = []

            if it.get("F_HZH"):
                set_clauses.append("F_HZH = ?")
                params.append(it["F_HZH"])
            if target_wz is not None:
                set_clauses.append("F_WZ = ?")
                params.append(target_wz)
            hs, ls = resolve_box_template_dims(target_hzh or "", boxes_input)
            if hs is not None:
                set_clauses.append("F_HS = ?")
                params.append(hs)
            if ls is not None:
                set_clauses.append("F_LS = ?")
                params.append(ls)

            if set_clauses:
                params.append(fz_id)
                update_sql = (
                    f"UPDATE T_YBFZ SET {', '.join(set_clauses)} "
                    f"WHERE F_ID = ? AND F_YSZT = '未运送'"
                )
                cur.execute(update_sql, tuple(params))
                updated_ids.append(fz_id)

        conn.commit()

    return {"updated": updated_ids, "count": len(updated_ids)}


# ============================================
# 盒号编码规则（对齐标本号 F_ZZID：peek 预览 / generate 占号）
# ============================================

HZH_RULE_CODE = "F_HZH"
_HZH_SKIP_MAX = 200


def hzh_exists(hzh: str) -> bool:
    """盒号是否已被 T_BOX 或分装表占用（含手填/改库留下的号）。"""
    hzh = str(hzh or "").strip()
    if not hzh:
        return False
    adapter = get_adapter()
    with adapter.connect() as conn:
        cur = conn.cursor()
        cur.execute("SELECT 1 FROM T_BOX WHERE F_HZH = ?", (hzh,))
        if cur.fetchone():
            return True
        cur.execute("SELECT 1 FROM T_YBFZ WHERE F_HZH = ?", (hzh,))
        return cur.fetchone() is not None


def is_hzh_rule_available() -> bool:
    """是否已配置并启用字典编码规则 F_HZH。"""
    try:
        from app.crud.code_rule import get_code_rule_by_code
        rule = get_code_rule_by_code(HZH_RULE_CODE)
        if not rule:
            return False
        if int(rule.get("F_ENABLED") or 0) != 1:
            return False
        tmpl = (rule.get("F_RULE_TEMPLATE") or "").strip()
        return bool(tmpl)
    except Exception:
        return False


def peek_hzh(context: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """
    预览下一个盒号（不消耗序列号）。

    Returns:
        {
          F_HZH: str | None,
          rule_available: bool,
          source: "code_rule" | "none",
        }
    """
    context = context or {}
    if not is_hzh_rule_available():
        return {"F_HZH": None, "rule_available": False, "source": "none"}

    from app.crud.code_rule import peek_code
    last = None
    for offset in range(_HZH_SKIP_MAX):
        hzh = peek_code(HZH_RULE_CODE, context, seq_offset=offset)
        if not hzh:
            raise ValueError("字典编码规则窥探的盒号为空")
        last = hzh
        if not hzh_exists(hzh):
            if offset:
                logger.info(f"[peek-hzh] 跳过 {offset} 个已占用盒号，预览 {hzh}")
            return {"F_HZH": hzh, "rule_available": True, "source": "code_rule"}
    raise ValueError(f"连续 {_HZH_SKIP_MAX} 个预览盒号均已被占用（最后一次 {last}），请检查已有盒号或编码规则")


def _generate_hzh_from_rule(context: Optional[Dict[str, Any]] = None) -> str:
    """正式占用序列并生成盒号；已占用的号会继续占号直到空号。"""
    from app.crud.code_rule import generate_code
    last = None
    for i in range(_HZH_SKIP_MAX):
        hzh = generate_code(HZH_RULE_CODE, context or {})
        if not hzh:
            raise ValueError("字典编码规则生成的盒号为空")
        last = hzh
        if not hzh_exists(hzh):
            if i:
                logger.info(f"[generate-hzh] 跳过 {i} 个已占用盒号，分配 {hzh}")
            return hzh
        logger.warning(f"[generate-hzh] 盒号 {hzh} 已存在，继续占号")
    raise ValueError(f"连续 {_HZH_SKIP_MAX} 个生成盒号均已被占用（最后一次 {last}），请检查已有盒号或编码规则")


def resolve_hzh_for_save(
    user_hzh: Optional[str] = None,
    context: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    保存场景下解析最终盒号（对齐标本号 save-and-distribute 逻辑）。

    - 未入参：有规则则 generate 占号；无规则则报错（需手填）
    - 入参 == 当前 peek：视为预览落地 → generate 占号（并发以后端为准）
    - 入参 != peek：视为手填自定义 → 直接使用，不占序列
    """
    context = context or {}
    user_hzh = (str(user_hzh).strip() if user_hzh is not None else "") or None
    rule_ok = is_hzh_rule_available()

    if not user_hzh:
        if not rule_ok:
            raise ValueError("未配置盒号编码规则，请手动输入盒号")
        hzh = _generate_hzh_from_rule(context)
        source = "auto"
        logger.info(f"[resolve-hzh] 未入参，系统自动分配: {hzh}")
    else:
        if rule_ok:
            peek_result = peek_hzh(context)
            peek_val = peek_result.get("F_HZH")
            if peek_val and user_hzh == peek_val:
                hzh = _generate_hzh_from_rule(context)
                source = "auto"
                logger.info(f"[resolve-hzh] 预览号正式分配: {hzh}")
                if hzh != user_hzh:
                    logger.warning(
                        f"[resolve-hzh] 预览盒号 {user_hzh} 与实际分配 {hzh} 不一致（可能并发），以实际分配为准"
                    )
            else:
                hzh = user_hzh
                source = "custom"
                logger.info(f"[resolve-hzh] 使用自定义盒号: {hzh}")
        else:
            hzh = user_hzh
            source = "custom"
            logger.info(f"[resolve-hzh] 无编码规则，使用手填盒号: {hzh}")

    if hzh_exists(hzh):
        raise ValueError(f"盒号 {hzh} 已存在，请重新预览或更换盒号")

    return {
        "F_HZH": hzh,
        "source": source,
        "rule_available": rule_ok,
    }
