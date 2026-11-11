"""
采集标准模块 CRUD

包含两个子模块：
- 项目信息维护（占位符模块）：T_XMXX
- 采集模板：T_CJMB（一个模板名称占一条记录，明细以 JSON 数组嵌套在 F_MBMX 字段内，
  数组每个元素带应用层自增的 F_ID，增删改采用 merge 模式，参考 organoid_thaw 基质胶明细写法）
"""
from typing import Dict, Any, List, Optional
from app.db.factory import get_adapter
import json
import logging

logger = logging.getLogger(__name__)


def _project_xmbh_exists(cur, xmbh: str, exclude_id: Optional[int] = None) -> bool:
    """检查项目编号是否已存在（精确匹配，忽略首尾空白）。"""
    key = (xmbh or "").strip()
    if not key:
        return False
    sql = "SELECT 1 FROM T_XMXX WHERE F_XMBH = ?"
    params: List[Any] = [key]
    if exclude_id is not None:
        sql += " AND F_ID <> ?"
        params.append(exclude_id)
    cur.execute(sql, tuple(params))
    return cur.fetchone() is not None


def _template_mbmc_exists(cur, mbmc: str, exclude_id: Optional[int] = None) -> bool:
    """检查模板名称是否已存在（精确匹配，忽略首尾空白）。"""
    key = (mbmc or "").strip()
    if not key:
        return False
    sql = "SELECT 1 FROM T_CJMB WHERE F_MBMC = ?"
    params: List[Any] = [key]
    if exclude_id is not None:
        sql += " AND F_ID <> ?"
        params.append(exclude_id)
    cur.execute(sql, tuple(params))
    return cur.fetchone() is not None


# ============================================
# 项目信息维护相关函数
# ============================================

def list_projects(query: Dict[str, Any]) -> Dict[str, Any]:
    """
    查询项目信息列表

    Args:
        query: 查询条件
            - F_XMMC: 项目名称模糊过滤（可选）
            - F_XMBH: 项目编号模糊过滤（可选）
            - skip: 跳过记录数
            - limit: 每页记录数

    Returns:
        {items: List[dict], total: int}
    """
    adapter = get_adapter()

    where: List[str] = []
    params: List[Any] = []

    if query.get("F_XMMC"):
        where.append("F_XMMC LIKE ?")
        params.append(f"%{query['F_XMMC'].strip()}%")

    if query.get("F_XMBH"):
        where.append("F_XMBH LIKE ?")
        params.append(f"%{query['F_XMBH'].strip()}%")

    where_sql = (" WHERE " + " AND ".join(where)) if where else ""

    base_sql = f"""
        SELECT F_ID, F_XMMC, F_XMBH, F_CREATE_TIME
        FROM T_XMXX
        {where_sql}
        ORDER BY F_ID DESC
    """
    count_sql = f"SELECT COUNT(1) FROM T_XMXX{where_sql}"

    sql, extra = adapter.paginate(base_sql, skip=query.get("skip", 0), limit=query.get("limit", 100))

    with adapter.connect() as conn:
        cur = conn.cursor()

        cur.execute(count_sql, tuple(params))
        total = int(cur.fetchone()[0])

        cur.execute(sql, tuple(params + extra))
        rows = cur.fetchall()
        cols = [c[0] for c in cur.description]
        items: List[Dict[str, Any]] = [dict(zip(cols, r)) for r in rows]

        return {"items": items, "total": total}


def get_project_by_id(project_id: int) -> Optional[Dict[str, Any]]:
    """根据ID查询项目信息"""
    adapter = get_adapter()
    sql = "SELECT F_ID, F_XMMC, F_XMBH, F_CREATE_TIME FROM T_XMXX WHERE F_ID = ?"
    with adapter.connect() as conn:
        cur = conn.cursor()
        cur.execute(sql, (project_id,))
        row = cur.fetchone()
        if row:
            cols = [c[0] for c in cur.description]
            return dict(zip(cols, row))
    return None


def create_project(data: Dict[str, Any]) -> int:
    """
    创建项目信息

    Args:
        data: 项目数据（F_XMMC, F_XMBH）

    Returns:
        新创建的项目ID
    """
    adapter = get_adapter()

    xmmc = (data.get("F_XMMC") or "").strip()
    xmbh = (data.get("F_XMBH") or "").strip()
    if not xmmc:
        raise ValueError("项目名称不能为空")
    if not xmbh:
        raise ValueError("项目编号不能为空")

    sql = "INSERT INTO T_XMXX (F_XMMC, F_XMBH) VALUES (?, ?)"

    with adapter.connect() as conn:
        cur = conn.cursor()
        if _project_xmbh_exists(cur, xmbh):
            raise ValueError(f"项目编号「{xmbh}」已存在，请勿重复创建")

        cur.execute(sql, (xmmc, xmbh))

        new_id = adapter.last_insert_id(cur)
        if new_id is None:
            cur.execute("SELECT MAX(F_ID) FROM T_XMXX")
            new_id = cur.fetchone()[0]

        conn.commit()
        return int(new_id)


def update_project(project_id: int, data: Dict[str, Any]) -> bool:
    """
    更新项目信息

    Args:
        project_id: 项目ID
        data: 更新数据

    Returns:
        是否更新成功
    """
    adapter = get_adapter()

    existing = get_project_by_id(project_id)
    if not existing:
        raise ValueError(f"项目ID {project_id} 不存在")

    updates: List[str] = []
    params: List[Any] = []

    if "F_XMMC" in data and data["F_XMMC"] is not None:
        xmmc = str(data["F_XMMC"]).strip()
        if not xmmc:
            raise ValueError("项目名称不能为空")
        updates.append("F_XMMC = ?")
        params.append(xmmc)

    if "F_XMBH" in data and data["F_XMBH"] is not None:
        xmbh = str(data["F_XMBH"]).strip()
        if not xmbh:
            raise ValueError("项目编号不能为空")
        updates.append("F_XMBH = ?")
        params.append(xmbh)

    if not updates:
        return True

    params.append(project_id)
    sql = f"UPDATE T_XMXX SET {', '.join(updates)} WHERE F_ID = ?"

    with adapter.connect() as conn:
        cur = conn.cursor()
        if "F_XMBH = ?" in updates:
            xmbh_to_check = params[updates.index("F_XMBH = ?")]
            if _project_xmbh_exists(cur, xmbh_to_check, exclude_id=project_id):
                raise ValueError(f"项目编号「{xmbh_to_check}」已存在，请勿重复使用")
        cur.execute(sql, tuple(params))
        conn.commit()
        return True


def delete_project(project_id: int) -> bool:
    """
    删除项目信息

    Args:
        project_id: 项目ID

    Returns:
        是否删除成功
    """
    adapter = get_adapter()

    existing = get_project_by_id(project_id)
    if not existing:
        raise ValueError(f"项目ID {project_id} 不存在")

    sql = "DELETE FROM T_XMXX WHERE F_ID = ?"

    with adapter.connect() as conn:
        cur = conn.cursor()
        cur.execute(sql, (project_id,))
        conn.commit()
        return True


# ============================================
# 采集模板相关函数
# ============================================

_TEMPLATE_ITEM_FIELDS = ("F_YBLX", "F_CCXS", "F_FGS", "F_TJ", "F_YBGLX", "F_XBSL", "F_BZ")


def _clean_item_field(v: Any) -> Any:
    if v is None:
        return None
    if isinstance(v, str):
        s = v.strip()
        return s if s != "" else None
    return v


def _clean_xbsl_field(v: Any) -> Any:
    """细胞数量统一存为字符串，兼容 int/float 入参及存量 JSON。"""
    if v is None:
        return None
    if isinstance(v, bool):
        return str(v)
    if isinstance(v, int):
        return str(v)
    if isinstance(v, float):
        return str(int(v)) if v.is_integer() else str(v)
    if isinstance(v, str):
        s = v.strip()
        return s if s else None
    s = str(v).strip()
    return s if s else None


def _normalize_item(it: Dict[str, Any], fid: int) -> Dict[str, Any]:
    """按固定字段顺序整理单条模板明细，附加指定的 F_ID。"""
    obj: Dict[str, Any] = {"F_ID": fid}
    for f in _TEMPLATE_ITEM_FIELDS:
        v = it.get(f)
        obj[f] = _clean_xbsl_field(v) if f == "F_XBSL" else _clean_item_field(v)
    return obj


def _loads_mbmx(raw: Any) -> List[Dict[str, Any]]:
    if raw is None:
        return []
    try:
        arr = json.loads(raw) if isinstance(raw, str) else raw
        if isinstance(arr, dict):
            arr = [arr]
        return arr if isinstance(arr, list) else []
    except Exception:
        return []


def _dumps_mbmx(items: List[Dict[str, Any]]) -> str:
    return json.dumps(items, ensure_ascii=False)


def _build_mbmx_full_replace(items: Any) -> List[Dict[str, Any]]:
    """整体全量替换明细：按入参顺序重建 JSON 数组，F_ID 从 1 递增（忽略入参中的 F_ID）。"""
    result: List[Dict[str, Any]] = []
    next_id = 1
    for it in (items or []):
        if not isinstance(it, dict):
            continue
        result.append(_normalize_item(it, next_id))
        next_id += 1
    return result


def _row_to_out(row: Dict[str, Any]) -> Dict[str, Any]:
    row = dict(row)
    row["MBMX"] = _loads_mbmx(row.get("F_MBMX"))
    row.pop("F_MBMX", None)
    return row


def list_templates(query: Dict[str, Any]) -> Dict[str, Any]:
    """
    查询采集模板列表

    Args:
        query: 查询条件
            - F_MBMC: 模板名称模糊过滤（可选）
            - skip: 跳过记录数
            - limit: 每页记录数

    Returns:
        {items: List[dict]（含反序列化后的 MBMX）, total: int}
    """
    adapter = get_adapter()

    where: List[str] = []
    params: List[Any] = []

    if query.get("F_MBMC"):
        where.append("F_MBMC LIKE ?")
        params.append(f"%{query['F_MBMC'].strip()}%")

    where_sql = (" WHERE " + " AND ".join(where)) if where else ""

    base_sql = f"""
        SELECT F_ID, F_MBMC, F_MBMX, F_CREATE_TIME, F_UPDATE_TIME
        FROM T_CJMB
        {where_sql}
        ORDER BY F_ID DESC
    """
    count_sql = f"SELECT COUNT(1) FROM T_CJMB{where_sql}"

    sql, extra = adapter.paginate(base_sql, skip=query.get("skip", 0), limit=query.get("limit", 100))

    with adapter.connect() as conn:
        cur = conn.cursor()

        cur.execute(count_sql, tuple(params))
        total = int(cur.fetchone()[0])

        cur.execute(sql, tuple(params + extra))
        rows = cur.fetchall()
        cols = [c[0] for c in cur.description]
        items = [_row_to_out(dict(zip(cols, r))) for r in rows]

        return {"items": items, "total": total}


def get_template_by_id(template_id: int) -> Optional[Dict[str, Any]]:
    """根据ID查询采集模板（含反序列化后的 MBMX）"""
    adapter = get_adapter()
    sql = "SELECT F_ID, F_MBMC, F_MBMX, F_CREATE_TIME, F_UPDATE_TIME FROM T_CJMB WHERE F_ID = ?"
    with adapter.connect() as conn:
        cur = conn.cursor()
        cur.execute(sql, (template_id,))
        row = cur.fetchone()
        if row:
            cols = [c[0] for c in cur.description]
            return _row_to_out(dict(zip(cols, row)))
    return None


def _get_template_raw(conn, template_id: int) -> Optional[Dict[str, Any]]:
    """在给定连接内查询模板原始行（F_MBMX 仍为字符串），供写操作复用同一连接/事务。"""
    cur = conn.cursor()
    cur.execute("SELECT F_ID, F_MBMC, F_MBMX FROM T_CJMB WHERE F_ID = ?", (template_id,))
    row = cur.fetchone()
    if not row:
        return None
    cols = [c[0] for c in cur.description]
    return dict(zip(cols, row))


def create_template(data: Dict[str, Any]) -> int:
    """
    创建采集模板（整条记录）

    Args:
        data: {F_MBMC: str, MBMX: List[dict]}（MBMX 中传入的 F_ID 将被忽略，重新从 1 编号）

    Returns:
        新创建的模板ID
    """
    adapter = get_adapter()

    mbmc = (data.get("F_MBMC") or "").strip()
    if not mbmc:
        raise ValueError("模板名称不能为空")

    items = _build_mbmx_full_replace(data.get("MBMX") or [])

    sql = "INSERT INTO T_CJMB (F_MBMC, F_MBMX) VALUES (?, ?)"

    with adapter.connect() as conn:
        cur = conn.cursor()
        if _template_mbmc_exists(cur, mbmc):
            raise ValueError(f"模板名称「{mbmc}」已存在，请勿重复创建")

        cur.execute(sql, (mbmc, _dumps_mbmx(items)))

        new_id = adapter.last_insert_id(cur)
        if new_id is None:
            cur.execute("SELECT MAX(F_ID) FROM T_CJMB")
            new_id = cur.fetchone()[0]

        conn.commit()
        return int(new_id)


def update_template(template_id: int, data: Dict[str, Any]) -> bool:
    """
    更新采集模板（整条记录）。
    传入 F_MBMC 则更新模板名称；传入 MBMX（且非 None）则整体全量替换明细数组。

    Args:
        template_id: 模板ID
        data: {F_MBMC?: str, MBMX?: List[dict]}

    Returns:
        是否更新成功
    """
    adapter = get_adapter()

    with adapter.connect() as conn:
        existing = _get_template_raw(conn, template_id)
        if not existing:
            raise ValueError(f"模板ID {template_id} 不存在")

        cur = conn.cursor()

        updates: List[str] = []
        params: List[Any] = []

        if data.get("F_MBMC") is not None:
            mbmc = str(data["F_MBMC"]).strip()
            if not mbmc:
                raise ValueError("模板名称不能为空")
            if _template_mbmc_exists(cur, mbmc, exclude_id=template_id):
                raise ValueError(f"模板名称「{mbmc}」已存在，请勿重复使用")
            updates.append("F_MBMC = ?")
            params.append(mbmc)

        if "MBMX" in data and data["MBMX"] is not None:
            items = _build_mbmx_full_replace(data["MBMX"])
            updates.append("F_MBMX = ?")
            params.append(_dumps_mbmx(items))

        if not updates:
            return True

        updates.append(f"F_UPDATE_TIME = {adapter.now()}")
        params.append(template_id)

        cur.execute(f"UPDATE T_CJMB SET {', '.join(updates)} WHERE F_ID = ?", tuple(params))
        conn.commit()
        return True


def delete_template(template_id: int) -> bool:
    """删除采集模板（整条记录）"""
    adapter = get_adapter()

    existing = get_template_by_id(template_id)
    if not existing:
        raise ValueError(f"模板ID {template_id} 不存在")

    sql = "DELETE FROM T_CJMB WHERE F_ID = ?"

    with adapter.connect() as conn:
        cur = conn.cursor()
        cur.execute(sql, (template_id,))
        conn.commit()
        return True


def add_template_item(template_id: int, item: Dict[str, Any]) -> Dict[str, Any]:
    """
    精准新增一条模板明细（merge 模式：读出现有 MBMX，追加新明细后整体写回）。

    Args:
        template_id: 模板ID
        item: 明细字段（F_YBLX/F_CCXS/F_FGS/F_TJ/F_YBGLX/F_XBSL/F_BZ），传入的 F_ID（如有）将被忽略

    Returns:
        新增的明细（含分配的 F_ID）
    """
    adapter = get_adapter()

    with adapter.connect() as conn:
        existing = _get_template_raw(conn, template_id)
        if not existing:
            raise ValueError(f"模板ID {template_id} 不存在")

        current = _loads_mbmx(existing.get("F_MBMX"))
        max_id = 0
        for it in current:
            try:
                max_id = max(max_id, int(it.get("F_ID") or 0))
            except Exception:
                continue

        new_id = max_id + 1
        new_item = _normalize_item(item, new_id)
        current.append(new_item)

        cur = conn.cursor()
        cur.execute(
            f"UPDATE T_CJMB SET F_MBMX = ?, F_UPDATE_TIME = {adapter.now()} WHERE F_ID = ?",
            (_dumps_mbmx(current), template_id),
        )
        conn.commit()
        return new_item


def update_template_item(template_id: int, item_id: int, patch: Dict[str, Any]) -> Dict[str, Any]:
    """
    精准修改一条模板明细（merge 模式：仅覆盖传入的非空字段，其余明细保持不变）。

    Args:
        template_id: 模板ID
        item_id: 明细ID（F_ID）
        patch: 待更新字段（F_YBLX/F_CCXS/F_FGS/F_TJ/F_YBGLX/F_XBSL/F_BZ），None 表示不修改该字段

    Returns:
        更新后的明细
    """
    adapter = get_adapter()

    with adapter.connect() as conn:
        existing = _get_template_raw(conn, template_id)
        if not existing:
            raise ValueError(f"模板ID {template_id} 不存在")

        current = _loads_mbmx(existing.get("F_MBMX"))

        target: Optional[Dict[str, Any]] = None
        for it in current:
            try:
                if int(it.get("F_ID")) == int(item_id):
                    target = it
                    break
            except Exception:
                continue

        if target is None:
            raise ValueError(f"模板ID {template_id} 下不存在明细ID {item_id}")

        for f in _TEMPLATE_ITEM_FIELDS:
            if f in patch and patch[f] is not None:
                v = patch[f]
                target[f] = _clean_xbsl_field(v) if f == "F_XBSL" else _clean_item_field(v)

        cur = conn.cursor()
        cur.execute(
            f"UPDATE T_CJMB SET F_MBMX = ?, F_UPDATE_TIME = {adapter.now()} WHERE F_ID = ?",
            (_dumps_mbmx(current), template_id),
        )
        conn.commit()
        return target


def delete_template_item(template_id: int, item_id: int) -> bool:
    """
    精准删除一条模板明细（merge 模式：仅从 MBMX 数组中剔除该条，其余明细及模板名称保持不变）。

    Args:
        template_id: 模板ID
        item_id: 明细ID（F_ID）

    Returns:
        是否删除成功
    """
    adapter = get_adapter()

    with adapter.connect() as conn:
        existing = _get_template_raw(conn, template_id)
        if not existing:
            raise ValueError(f"模板ID {template_id} 不存在")

        current = _loads_mbmx(existing.get("F_MBMX"))

        new_list = []
        found = False
        for it in current:
            try:
                if int(it.get("F_ID")) == int(item_id):
                    found = True
                    continue
            except Exception:
                pass
            new_list.append(it)

        if not found:
            raise ValueError(f"模板ID {template_id} 下不存在明细ID {item_id}")

        cur = conn.cursor()
        cur.execute(
            f"UPDATE T_CJMB SET F_MBMX = ?, F_UPDATE_TIME = {adapter.now()} WHERE F_ID = ?",
            (_dumps_mbmx(new_list), template_id),
        )
        conn.commit()
        return True
