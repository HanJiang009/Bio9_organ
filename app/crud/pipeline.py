"""
流水线管理模块 CRUD
"""
from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Dict, List, Optional

from app.db.factory import get_adapter


# ---------- 内部工具 ----------

def _fmt_dt(val) -> Optional[str]:
    """统一格式化 datetime 字段为字符串"""
    if val is None:
        return None
    if isinstance(val, str):
        return val
    try:
        return val.strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return str(val)


def _serialize_preset(preset: Any) -> Optional[str]:
    """将预设值序列化为 JSON 字符串存入数据库"""
    if preset is None:
        return None
    if isinstance(preset, str):
        return preset
    return json.dumps(preset, ensure_ascii=False)


def _deserialize_preset(raw: Optional[str]) -> Any:
    """将数据库中的 JSON 字符串反序列化为对象"""
    if not raw:
        return None
    try:
        return json.loads(raw)
    except Exception:
        return raw


_ybly_column_ready = False
_MODELING_STAGE = "建模"


def ensure_pipeline_ybly_column() -> None:
    """T_PIPELINE.F_YBLY 不存在时补上。JSON 数组文本，仅建模阶段使用。"""
    global _ybly_column_ready
    if _ybly_column_ready:
        return
    adapter = get_adapter()
    with adapter.connect() as conn:
        cur = conn.cursor()
        if adapter.column_exists(cur, "T_PIPELINE", "F_YBLY"):
            _ybly_column_ready = True
            return
        name = type(adapter).__name__.lower()
        if "dameng" in name:
            ddl = "ALTER TABLE T_PIPELINE ADD F_YBLY VARCHAR(500)"
        elif "sqlserver" in name:
            ddl = "ALTER TABLE T_PIPELINE ADD F_YBLY NVARCHAR(500) NULL"
        else:
            ddl = "ALTER TABLE T_PIPELINE ADD COLUMN F_YBLY VARCHAR(500) NULL COMMENT '样本来源JSON数组，仅建模阶段'"
        cur.execute(ddl)
        conn.commit()
    _ybly_column_ready = True


def _normalize_source_token(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    if text == "原代培养":
        return "原代制备"
    return text


def _normalize_ybly(raw: Any) -> Optional[List[str]]:
    if raw is None or raw == "":
        return None
    if isinstance(raw, str):
        text = raw.strip()
        if not text or text in ("[]", "null"):
            return None
        try:
            parsed = json.loads(text)
        except Exception:
            parsed = [text]
        raw = parsed
    if not isinstance(raw, list):
        raw = [raw]
    out: List[str] = []
    for item in raw:
        token = _normalize_source_token(item)
        if token and token not in out:
            out.append(token)
    return out or None


def _dump_ybly(raw: Any) -> Optional[str]:
    values = _normalize_ybly(raw)
    if not values:
        return None
    return json.dumps(values, ensure_ascii=False, separators=(",", ":"))


def _parse_ybly(raw: Any) -> Optional[List[str]]:
    return _normalize_ybly(raw)


def _attach_ybly(row: Dict[str, Any]) -> Dict[str, Any]:
    row["F_YBLY"] = _parse_ybly(row.get("F_YBLY"))
    return row


def _ybly_contains_clause(source: str) -> tuple:
    """整值命中 JSON 数组元素。入库为紧凑 JSON，如 [\"传代\",\"传代复苏\"]。"""
    quoted = json.dumps(source, ensure_ascii=False)
    compact_one = json.dumps([source], ensure_ascii=False, separators=(",", ":"))
    sql = "(F_YBLY = ? OR F_YBLY LIKE ? OR F_YBLY LIKE ? OR F_YBLY LIKE ?)"
    params = [compact_one, f"[{quoted},%", f"%,{quoted}]", f"%,{quoted},%"]
    return sql, params


def _ybly_for_stage(stage: Any, raw: Any) -> Optional[str]:
    if str(stage or "") != _MODELING_STAGE:
        return None
    return _dump_ybly(raw)


def _fetch_nodes(cur, pipeline_id: int) -> List[Dict[str, Any]]:
    """查询指定流水线的所有节点"""
    cur.execute(
        """
        SELECT F_ID, F_PIPELINE_ID, F_JD_LX, F_PRESET, F_CJSJ, F_GXSJ
        FROM T_PIPELINE_NODE
        WHERE F_PIPELINE_ID = ?
        ORDER BY F_ID ASC
        """,
        (pipeline_id,),
    )
    rows = cur.fetchall()
    columns = [col[0] for col in cur.description]
    result = []
    for row in rows:
        item = dict(zip(columns, row))
        item["F_PRESET"] = _deserialize_preset(item.get("F_PRESET"))
        item["F_CJSJ"] = _fmt_dt(item.get("F_CJSJ"))
        item["F_GXSJ"] = _fmt_dt(item.get("F_GXSJ"))
        result.append(item)
    return result


def _replace_nodes(cur, pipeline_id: int, nodes: List[Dict[str, Any]]) -> None:
    """全量覆盖节点：先删除旧节点，再批量插入新节点"""
    cur.execute("DELETE FROM T_PIPELINE_NODE WHERE F_PIPELINE_ID = ?", (pipeline_id,))
    now = datetime.now()
    for node in nodes:
        preset_str = _serialize_preset(node.get("F_PRESET"))
        cur.execute(
            """
            INSERT INTO T_PIPELINE_NODE (F_PIPELINE_ID, F_JD_LX, F_PRESET, F_CJSJ, F_GXSJ)
            VALUES (?, ?, ?, ?, ?)
            """,
            (pipeline_id, node["F_JD_LX"], preset_str, now, now),
        )


# ---------- 对外接口 ----------

def create_pipeline(data: Dict[str, Any]) -> Dict[str, Any]:
    """创建流水线模板（含节点）

    Args:
        data: 包含主表字段及 nodes 列表

    Returns:
        {"F_ID": int, "F_MC": str}
    """
    adapter = get_adapter()
    ensure_pipeline_ybly_column()
    now = datetime.now()
    nodes: List[Dict[str, Any]] = data.get("nodes") or []

    with adapter.connect() as conn:
        cur = conn.cursor()

        cur.execute(
            """
            INSERT INTO T_PIPELINE (F_MC, F_AZ, F_JD, F_ZT, F_DJR, F_DJRQ, F_GXSJ, F_BZ, F_YBLY)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                data["F_MC"],
                data.get("F_AZ"),
                data["F_JD"],
                data.get("F_ZT", 1),
                data.get("F_DJR"),
                now,
                now,
                data.get("F_BZ"),
                _ybly_for_stage(data.get("F_JD"), data.get("F_YBLY")),
            ),
        )

        # 获取新插入行的 ID
        new_id = int(adapter.last_insert_id(cur))

        if nodes:
            _replace_nodes(cur, new_id, nodes)

        conn.commit()

    return {"F_ID": new_id, "F_MC": data["F_MC"]}


def update_pipeline(data: Dict[str, Any]) -> Dict[str, Any]:
    """更新流水线基础信息，并可选地批量更新/新增节点。

    引用检测规则（T_PYGD 中有工单通过 F_PIPELINE_JM 或 F_PIPELINE_LX 引用该模板）：
    - 未被引用：允许所有操作，包括新增节点（nodes 项不含 F_ID）和删除节点（F_NODE_DEL）。
    - 已被引用：
        * 允许：修改模板名称(F_MC)、状态(F_ZT)、备注(F_BZ)以及已有节点内容(F_JD_LX/F_PRESET)。
        * 禁止：增加节点（nodes 项无 F_ID）或删除节点（F_NODE_DEL），否则抛 ValueError。
        * 返回 warning 提示修改不影响已关联的工单。

    Args:
        data: 包含 F_ID、可选的基础字段，以及可选的 nodes 列表

    Returns:
        {"F_ID": int, "F_MC": str, "warning": str | None}

    Raises:
        ValueError: 流水线不存在；或被引用时尝试变更节点数量；或节点校验失败
    """
    adapter = get_adapter()
    ensure_pipeline_ybly_column()
    now = datetime.now()
    pipeline_id: int = data["F_ID"]

    nodes: Optional[List[Dict[str, Any]]] = data.get("nodes") or []
    node_del_ids: Optional[List[int]] = data.get("F_NODE_DEL") or []

    # 区分新增节点（无 F_ID）与更新节点（有 F_ID）
    new_nodes = [n for n in nodes if not n.get("F_ID")]
    update_nodes = [n for n in nodes if n.get("F_ID")]

    with adapter.connect() as conn:
        cur = conn.cursor()

        # 确认流水线存在
        cur.execute("SELECT F_MC, F_JD FROM T_PIPELINE WHERE F_ID = ?", (pipeline_id,))
        row = cur.fetchone()
        if not row:
            raise ValueError(f"流水线不存在：F_ID={pipeline_id}")
        current_jd = row[1]

        # 检测是否被工单引用
        # F_PIPELINE_JM 仍为整数，精确匹配；F_PIPELINE_LX 已改为 JSON 数组字符串，用 LIKE 匹配
        cur.execute(
            "SELECT COUNT(1) FROM T_PYGD WHERE F_PIPELINE_JM = ?",
            (pipeline_id,),
        )
        jm_count_row = cur.fetchone()
        jm_ref = int(jm_count_row[0]) if jm_count_row else 0

        cur.execute(
            "SELECT F_ID, F_PIPELINE_LX FROM T_PYGD WHERE F_PIPELINE_LX IS NOT NULL AND F_PIPELINE_LX != ''"
        )
        lx_rows = cur.fetchall()
        lx_ref = 0
        for lr in lx_rows:
            try:
                ids = json.loads(str(lr[1]))
                if isinstance(ids, list) and pipeline_id in [int(i) for i in ids if i is not None]:
                    lx_ref += 1
                elif int(ids) == pipeline_id:
                    lx_ref += 1
            except Exception:
                try:
                    if int(lr[1]) == pipeline_id:
                        lx_ref += 1
                except Exception:
                    pass

        ref_count = jm_ref + lx_ref
        is_referenced = ref_count > 0

        warning: Optional[str] = None
        if is_referenced:
            # 拦截结构变更
            if new_nodes:
                raise ValueError(
                    f"该流水线模板已被 {ref_count} 个培养工单引用，不允许新增节点（如需调整请先解除引用或复制新模板）"
                )
            if node_del_ids:
                raise ValueError(
                    f"该流水线模板已被 {ref_count} 个培养工单引用，不允许删除节点（如需调整请先解除引用或复制新模板）"
                )
            warning = f"注意：该模板当前被 {ref_count} 个培养工单引用，本次修改仅更新模板本身，不影响已关联的工单。"

        # 更新基础信息（引用时同样允许修改名称/状态/备注等）
        updatable = ["F_MC", "F_AZ", "F_JD", "F_ZT", "F_BZ"]
        set_clauses: List[str] = []
        params: List[Any] = []
        for field in updatable:
            if field in data and data[field] is not None:
                set_clauses.append(f"{field} = ?")
                params.append(data[field])

        effective_jd = data["F_JD"] if data.get("F_JD") else current_jd
        if str(effective_jd or "") != _MODELING_STAGE:
            set_clauses.append("F_YBLY = ?")
            params.append(None)
        elif "F_YBLY" in data:
            set_clauses.append("F_YBLY = ?")
            params.append(_dump_ybly(data.get("F_YBLY")))

        set_clauses.append("F_GXSJ = ?")
        params.append(now)
        params.append(pipeline_id)
        cur.execute(
            f"UPDATE T_PIPELINE SET {', '.join(set_clauses)} WHERE F_ID = ?",
            tuple(params),
        )

        # 删除节点（仅未引用时执行，前面已对引用时拦截）
        for del_id in node_del_ids:
            cur.execute(
                "SELECT F_ID FROM T_PIPELINE_NODE WHERE F_ID = ? AND F_PIPELINE_ID = ?",
                (del_id, pipeline_id),
            )
            if not cur.fetchone():
                raise ValueError(
                    f"节点不存在或不属于该流水线：节点F_ID={del_id}，流水线F_ID={pipeline_id}"
                )
            cur.execute("DELETE FROM T_PIPELINE_NODE WHERE F_ID = ?", (del_id,))

        # 更新已有节点内容
        for node in update_nodes:
            node_id = int(node["F_ID"])
            cur.execute(
                "SELECT F_ID, F_JD_LX FROM T_PIPELINE_NODE WHERE F_ID = ? AND F_PIPELINE_ID = ?",
                (node_id, pipeline_id),
            )
            existing = cur.fetchone()
            if not existing:
                raise ValueError(
                    f"节点不存在或不属于该流水线：节点F_ID={node_id}，流水线F_ID={pipeline_id}"
                )
            # F_JD_LX 不传则保持原值
            new_jd_lx = node.get("F_JD_LX") or existing[1]
            preset_str = _serialize_preset(node.get("F_PRESET"))
            cur.execute(
                "UPDATE T_PIPELINE_NODE SET F_JD_LX = ?, F_PRESET = ?, F_GXSJ = ? WHERE F_ID = ?",
                (new_jd_lx, preset_str, now, node_id),
            )

        # 新增节点（仅未引用时执行）
        for node in new_nodes:
            jd_lx = node.get("F_JD_LX")
            if not jd_lx:
                raise ValueError("新增节点时 F_JD_LX（节点类型）不能为空")
            preset_str = _serialize_preset(node.get("F_PRESET"))
            cur.execute(
                """
                INSERT INTO T_PIPELINE_NODE (F_PIPELINE_ID, F_JD_LX, F_PRESET, F_CJSJ, F_GXSJ)
                VALUES (?, ?, ?, ?, ?)
                """,
                (pipeline_id, jd_lx, preset_str, now, now),
            )

        conn.commit()

        cur.execute("SELECT F_MC FROM T_PIPELINE WHERE F_ID = ?", (pipeline_id,))
        return {"F_ID": pipeline_id, "F_MC": cur.fetchone()[0], "warning": warning}


# ---------- 节点单独操作 ----------

def add_node(data: Dict[str, Any]) -> Dict[str, Any]:
    """向已有流水线追加单个节点

    Args:
        data: 包含 F_PIPELINE_ID、F_JD_LX、F_PRESET

    Returns:
        {"F_ID": int, "F_PIPELINE_ID": int, "F_JD_LX": str}

    Raises:
        ValueError: 流水线不存在
    """
    adapter = get_adapter()
    now = datetime.now()
    pipeline_id: int = data["F_PIPELINE_ID"]

    with adapter.connect() as conn:
        cur = conn.cursor()

        cur.execute("SELECT F_ID FROM T_PIPELINE WHERE F_ID = ?", (pipeline_id,))
        if not cur.fetchone():
            raise ValueError(f"流水线不存在：F_ID={pipeline_id}")

        preset_str = _serialize_preset(data.get("F_PRESET"))
        cur.execute(
            """
            INSERT INTO T_PIPELINE_NODE (F_PIPELINE_ID, F_JD_LX, F_PRESET, F_CJSJ, F_GXSJ)
            VALUES (?, ?, ?, ?, ?)
            """,
            (pipeline_id, data["F_JD_LX"], preset_str, now, now),
        )
        new_id = int(adapter.last_insert_id(cur))
        conn.commit()

    return {"F_ID": new_id, "F_PIPELINE_ID": pipeline_id, "F_JD_LX": data["F_JD_LX"]}


def update_node(data: Dict[str, Any]) -> Dict[str, Any]:
    """按节点 F_ID 精确更新节点内容

    Args:
        data: 包含 F_ID，以及需要更新的 F_JD_LX 和/或 F_PRESET

    Returns:
        更新后的节点完整信息字典

    Raises:
        ValueError: 节点不存在
    """
    adapter = get_adapter()
    now = datetime.now()
    node_id: int = data["F_ID"]

    with adapter.connect() as conn:
        cur = conn.cursor()

        cur.execute(
            "SELECT F_ID, F_PIPELINE_ID, F_JD_LX, F_PRESET FROM T_PIPELINE_NODE WHERE F_ID = ?",
            (node_id,),
        )
        row = cur.fetchone()
        if not row:
            raise ValueError(f"节点不存在：F_ID={node_id}")

        columns = [col[0] for col in cur.description]
        current = dict(zip(columns, row))

        set_clauses = ["F_GXSJ = ?"]
        params: List[Any] = [now]

        if "F_JD_LX" in data and data["F_JD_LX"] is not None:
            set_clauses.append("F_JD_LX = ?")
            params.append(data["F_JD_LX"])

        # F_PRESET 允许显式传 None（清空预设值），因此用 key 存在性判断而非 None 判断
        if "F_PRESET" in data:
            set_clauses.append("F_PRESET = ?")
            params.append(_serialize_preset(data["F_PRESET"]))

        params.append(node_id)
        sql = f"UPDATE T_PIPELINE_NODE SET {', '.join(set_clauses)} WHERE F_ID = ?"
        cur.execute(sql, tuple(params))
        conn.commit()

        # 返回更新后的完整节点数据
        cur.execute(
            "SELECT F_ID, F_PIPELINE_ID, F_JD_LX, F_PRESET, F_CJSJ, F_GXSJ FROM T_PIPELINE_NODE WHERE F_ID = ?",
            (node_id,),
        )
        updated_row = cur.fetchone()
        updated_cols = [col[0] for col in cur.description]
        result = dict(zip(updated_cols, updated_row))
        result["F_PRESET"] = _deserialize_preset(result.get("F_PRESET"))
        result["F_CJSJ"] = _fmt_dt(result.get("F_CJSJ"))
        result["F_GXSJ"] = _fmt_dt(result.get("F_GXSJ"))
        return result


def delete_node(node_id: int) -> Dict[str, Any]:
    """删除单个节点

    Args:
        node_id: 节点 F_ID

    Returns:
        {"F_ID": int, "F_PIPELINE_ID": int}

    Raises:
        ValueError: 节点不存在
    """
    adapter = get_adapter()

    with adapter.connect() as conn:
        cur = conn.cursor()

        cur.execute(
            "SELECT F_ID, F_PIPELINE_ID FROM T_PIPELINE_NODE WHERE F_ID = ?",
            (node_id,),
        )
        row = cur.fetchone()
        if not row:
            raise ValueError(f"节点不存在：F_ID={node_id}")

        pipeline_id = row[1]
        cur.execute("DELETE FROM T_PIPELINE_NODE WHERE F_ID = ?", (node_id,))
        conn.commit()

    return {"F_ID": node_id, "F_PIPELINE_ID": pipeline_id}


def get_pipeline_detail(pipeline_id: int) -> Dict[str, Any]:
    """获取流水线详情（含全部节点）

    Args:
        pipeline_id: 流水线 ID

    Returns:
        详情字典，包含 nodes 列表

    Raises:
        ValueError: 流水线不存在
    """
    adapter = get_adapter()
    ensure_pipeline_ybly_column()

    with adapter.connect() as conn:
        cur = conn.cursor()

        cur.execute(
            """
            SELECT F_ID, F_MC, F_AZ, F_JD, F_ZT, F_DJR, F_DJRQ, F_GXSJ, F_BZ, F_YBLY
            FROM T_PIPELINE
            WHERE F_ID = ?
            """,
            (pipeline_id,),
        )
        row = cur.fetchone()
        if not row:
            raise ValueError(f"流水线不存在：F_ID={pipeline_id}")

        columns = [col[0] for col in cur.description]
        detail = dict(zip(columns, row))
        detail["F_DJRQ"] = _fmt_dt(detail.get("F_DJRQ"))
        detail["F_GXSJ"] = _fmt_dt(detail.get("F_GXSJ"))

        detail["nodes"] = _fetch_nodes(cur, pipeline_id)
        _attach_ybly(detail)

    return detail


def get_pipeline_batch_detail(ids: List[int]) -> List[Dict[str, Any]]:
    """批量获取流水线详情（含全部节点），按入参 ids 顺序返回，不存在的 ID 跳过。"""
    if not ids:
        return []

    adapter = get_adapter()
    ensure_pipeline_ybly_column()
    ph = ",".join(["?"] * len(ids))

    with adapter.connect() as conn:
        cur = conn.cursor()
        cur.execute(
            f"""
            SELECT F_ID, F_MC, F_AZ, F_JD, F_ZT, F_DJR, F_DJRQ, F_GXSJ, F_BZ, F_YBLY
            FROM T_PIPELINE
            WHERE F_ID IN ({ph})
            """,
            tuple(ids),
        )
        rows = cur.fetchall()
        columns = [col[0] for col in cur.description]

        details: Dict[int, Dict[str, Any]] = {}
        for row in rows:
            d = dict(zip(columns, row))
            d["F_DJRQ"] = _fmt_dt(d.get("F_DJRQ"))
            d["F_GXSJ"] = _fmt_dt(d.get("F_GXSJ"))
            d["nodes"] = _fetch_nodes(cur, int(d["F_ID"]))
            _attach_ybly(d)
            details[int(d["F_ID"])] = d

    # 按入参顺序输出，跳过不存在的 ID
    return [details[i] for i in ids if i in details]


def list_pipelines(
    mc: Optional[str] = None,
    az: Optional[str] = None,
    jd: Optional[str] = None,
    zt: Optional[int] = None,
    skip: int = 0,
    limit: int = 20,
    include_all: bool = False,
    ybly: Optional[str] = None,
) -> Dict[str, Any]:
    """查询流水线列表（分页）

    Args:
        mc: 流水线名称（模糊匹配）
        az: 癌种/病种。未传 ybly 时模糊匹配；传入 ybly 时改为整值相等，空病种仍作为通用模板
        include_all: 配合 az。False=仅同病种；True=同病种 + F_AZ 为「全部」或空。传入 ybly 时不再使用
        jd: 阶段（精确匹配）
        zt: 状态（1=启用，0=禁用）
        ybly: 样本来源。传入后按病种整值 + 来源数组包含匹配，病种或来源为空都算通用
        skip: 分页偏移
        limit: 每页条数

    Returns:
        {"total": int, "data": [...]}
    """
    adapter = get_adapter()
    ensure_pipeline_ybly_column()

    where_clauses: List[str] = []
    params: List[Any] = []

    if mc:
        mc = str(mc).strip()
    if az:
        az = str(az).strip()
    if jd:
        jd = str(jd).strip()
    source = _normalize_source_token(ybly)

    if mc:
        where_clauses.append("F_MC LIKE ?")
        params.append(f"%{mc}%")
    if source:
        hit_sql, hit_params = _ybly_contains_clause(source)
        az_empty = "(F_AZ IS NULL OR F_AZ = '' OR F_AZ = '全部')"
        ybly_empty = "(F_YBLY IS NULL OR F_YBLY = '' OR F_YBLY = '[]')"
        if az:
            where_clauses.append(f"((F_AZ = ?) OR {az_empty}) AND ({hit_sql} OR {ybly_empty})")
            params.append(az)
            params.extend(hit_params)
        else:
            where_clauses.append(f"({hit_sql} OR {ybly_empty})")
            params.extend(hit_params)
    elif az:
        if include_all:
            where_clauses.append(
                "(F_AZ LIKE ? OR F_AZ IS NULL OR F_AZ = '' OR F_AZ = '全部')"
            )
        else:
            where_clauses.append("F_AZ LIKE ?")
        params.append(f"%{az}%")
    if jd:
        where_clauses.append("F_JD = ?")
        params.append(jd)
    if zt is not None:
        where_clauses.append("F_ZT = ?")
        params.append(zt)

    where_sql = ("WHERE " + " AND ".join(where_clauses)) if where_clauses else ""

    with adapter.connect() as conn:
        cur = conn.cursor()

        # 查询总数
        cur.execute(f"SELECT COUNT(*) FROM T_PIPELINE {where_sql}", tuple(params))
        total = cur.fetchone()[0]

        # 查询列表，附带节点数量
        # ref_count 在 Python 层计算（F_PIPELINE_LX 为 JSON 数组，不能用 SQL UNION 精确匹配）
        base_sql = f"""
            SELECT
                p.F_ID, p.F_MC, p.F_AZ, p.F_JD, p.F_ZT,
                p.F_DJR, p.F_DJRQ, p.F_GXSJ, p.F_BZ, p.F_YBLY,
                COALESCE(n.node_count, 0) AS node_count
            FROM T_PIPELINE p
            LEFT JOIN (
                SELECT F_PIPELINE_ID, COUNT(F_ID) AS node_count
                FROM T_PIPELINE_NODE
                GROUP BY F_PIPELINE_ID
            ) n ON p.F_ID = n.F_PIPELINE_ID
            {where_sql}
            ORDER BY p.F_DJRQ DESC, p.F_ID DESC
        """

        sql, extra = adapter.paginate(base_sql, skip=skip, limit=limit)
        cur.execute(sql, tuple(params + extra))
        rows = cur.fetchall()
        columns = [col[0] for col in cur.description]
        items_raw = [dict(zip(columns, row)) for row in rows]

        # 计算每个模板的 ref_count（F_PIPELINE_JM 精确匹配，F_PIPELINE_LX JSON 数组 Python 层解析）
        page_ids = [int(it["F_ID"]) for it in items_raw]
        pid_to_ref: Dict[int, int] = {pid: 0 for pid in page_ids}

        if page_ids:
            ph = ",".join(["?"] * len(page_ids))
            # JM 精确引用计数
            cur.execute(
                f"SELECT F_PIPELINE_JM, COUNT(1) FROM T_PYGD WHERE F_PIPELINE_JM IN ({ph}) GROUP BY F_PIPELINE_JM",
                tuple(page_ids),
            )
            jm_rows = cur.fetchall()
            for jr in jm_rows:
                try:
                    pid_to_ref[int(jr[0])] = pid_to_ref.get(int(jr[0]), 0) + int(jr[1])
                except Exception:
                    pass

            # LX JSON 数组引用（全表扫描，Python 层匹配）
            cur.execute(
                "SELECT F_PIPELINE_LX FROM T_PYGD WHERE F_PIPELINE_LX IS NOT NULL AND F_PIPELINE_LX != ''"
            )
            lx_all = cur.fetchall()
            page_id_set = set(page_ids)
            for lx_row in lx_all:
                try:
                    parsed = json.loads(str(lx_row[0]))
                    ids_in_row = parsed if isinstance(parsed, list) else [parsed]
                    for i in ids_in_row:
                        pid = int(i)
                        if pid in page_id_set:
                            pid_to_ref[pid] = pid_to_ref.get(pid, 0) + 1
                except Exception:
                    try:
                        pid = int(lx_row[0])
                        if pid in page_id_set:
                            pid_to_ref[pid] = pid_to_ref.get(pid, 0) + 1
                    except Exception:
                        pass

        data = []
        for item in items_raw:
            item["F_DJRQ"] = _fmt_dt(item.get("F_DJRQ"))
            item["F_GXSJ"] = _fmt_dt(item.get("F_GXSJ"))
            item["node_count"] = item.get("node_count") or 0
            item["ref_count"] = pid_to_ref.get(int(item["F_ID"]), 0)
            _attach_ybly(item)
            data.append(item)

    return {"total": total, "data": data}


def copy_pipeline(pipeline_id: int, operator: Optional[str] = None) -> Dict[str, Any]:
    """复制流水线模板（含全部节点）

    Args:
        pipeline_id: 被复制的流水线 ID
        operator: 操作人（登记人），可选

    Returns:
        {"F_ID": int, "F_MC": str}（新模板的 ID 和名称）

    Raises:
        ValueError: 源流水线不存在
    """
    adapter = get_adapter()
    ensure_pipeline_ybly_column()
    now = datetime.now()

    with adapter.connect() as conn:
        cur = conn.cursor()

        cur.execute(
            "SELECT F_MC, F_AZ, F_JD, F_ZT, F_BZ, F_YBLY FROM T_PIPELINE WHERE F_ID = ?",
            (pipeline_id,),
        )
        row = cur.fetchone()
        if not row:
            raise ValueError(f"流水线不存在：F_ID={pipeline_id}")

        src_mc, src_az, src_jd, src_zt, src_bz, src_ybly = row
        date_str = now.strftime("%Y%m%d")
        suffix = f"{operator}-{date_str}" if operator else date_str
        new_mc = f"{src_mc}-{suffix}【复制】"

        cur.execute(
            """
            INSERT INTO T_PIPELINE (F_MC, F_AZ, F_JD, F_ZT, F_DJR, F_DJRQ, F_GXSJ, F_BZ, F_YBLY)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (new_mc, src_az, src_jd, src_zt, operator, now, now, src_bz, _ybly_for_stage(src_jd, src_ybly)),
        )
        new_id = int(adapter.last_insert_id(cur))

        # 复制所有节点
        cur.execute(
            "SELECT F_JD_LX, F_PRESET FROM T_PIPELINE_NODE WHERE F_PIPELINE_ID = ? ORDER BY F_ID ASC",
            (pipeline_id,),
        )
        nodes = cur.fetchall()
        for jd_lx, preset in nodes:
            cur.execute(
                """
                INSERT INTO T_PIPELINE_NODE (F_PIPELINE_ID, F_JD_LX, F_PRESET, F_CJSJ, F_GXSJ)
                VALUES (?, ?, ?, ?, ?)
                """,
                (new_id, jd_lx, preset, now, now),
            )

        conn.commit()

    return {"F_ID": new_id, "F_MC": new_mc}


def delete_pipeline(pipeline_id: int) -> Dict[str, Any]:
    """删除流水线模板（级联删除节点）

    Args:
        pipeline_id: 流水线 ID

    Returns:
        {"F_ID": int}

    Raises:
        ValueError: 流水线不存在
    """
    adapter = get_adapter()

    with adapter.connect() as conn:
        cur = conn.cursor()

        cur.execute("SELECT F_ID FROM T_PIPELINE WHERE F_ID = ?", (pipeline_id,))
        if not cur.fetchone():
            raise ValueError(f"流水线不存在：F_ID={pipeline_id}")

        cur.execute("DELETE FROM T_PIPELINE_NODE WHERE F_PIPELINE_ID = ?", (pipeline_id,))
        cur.execute("DELETE FROM T_PIPELINE WHERE F_ID = ?", (pipeline_id,))
        conn.commit()

    return {"F_ID": pipeline_id}


def toggle_pipeline_status(pipeline_id: int, zt: int) -> Dict[str, Any]:
    """切换流水线启用/禁用状态

    Args:
        pipeline_id: 流水线 ID
        zt: 目标状态（1=启用，0=禁用）

    Returns:
        {"F_ID": int, "F_ZT": int}

    Raises:
        ValueError: 流水线不存在
    """
    adapter = get_adapter()
    now = datetime.now()

    with adapter.connect() as conn:
        cur = conn.cursor()

        cur.execute("SELECT F_ID FROM T_PIPELINE WHERE F_ID = ?", (pipeline_id,))
        if not cur.fetchone():
            raise ValueError(f"流水线不存在：F_ID={pipeline_id}")

        cur.execute(
            "UPDATE T_PIPELINE SET F_ZT = ?, F_GXSJ = ? WHERE F_ID = ?",
            (zt, now, pipeline_id),
        )
        conn.commit()

    return {"F_ID": pipeline_id, "F_ZT": zt}
