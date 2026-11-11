"""四层容器：层、架、抽屉、盒。盒位在保存时一次展开。"""
from datetime import datetime
from typing import Any, Dict, List, Optional, Set, Tuple

from app.db.factory import get_adapter
from app.crud.direct_inbound_schema import (
    assert_slot_count,
    ensure_schema,
    parse_dim,
)
from app.crud.op_log import insert_op_log


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _clean(value: Any, limit: int) -> str:
    text = "" if value is None else str(value).strip()
    if len(text) > limit:
        raise ValueError(f"内容超过 {limit} 个字符")
    return text


def _status(value: Any, default: str = "启用") -> str:
    text = _clean(value, 16) or default
    if text not in ("启用", "禁用"):
        raise ValueError("状态只能是启用或禁用")
    return text


def _spec(payload: Dict[str, Any]) -> Dict[str, Any]:
    code = _clean(payload.get("F_RQBH"), 64)
    name = _clean(payload.get("F_RQMC"), 200)
    if not code:
        raise ValueError("容器编号不能为空")
    if not name:
        raise ValueError("容器名称不能为空")
    layers = parse_dim(payload.get("F_CS"), "层数")
    racks = parse_dim(payload.get("F_JS"), "每层架数")
    drawers = parse_dim(payload.get("F_CTS"), "每架抽屉数")
    boxes = parse_dim(payload.get("F_HXS"), "每抽屉盒数")
    total = assert_slot_count(layers, racks, drawers, boxes)
    return {
        "F_RQBH": code,
        "F_RQMC": name,
        "F_CCWD": _clean(payload.get("F_CCWD"), 64) or None,
        "F_CS": layers,
        "F_JS": racks,
        "F_CTS": drawers,
        "F_HXS": boxes,
        "F_ZT": _status(payload.get("F_ZT")),
        "F_BZ": _clean(payload.get("F_BZ"), 500) or None,
        "F_ZHW": total,
    }


def _record(cols, row) -> Dict[str, Any]:
    return dict(zip(cols, row))


def _get_row(adapter, rq_id: int) -> Dict[str, Any]:
    rows, cols = adapter.fetch_all(
        """
        SELECT F_ID, F_RQBH, F_RQMC, F_CCWD, F_CS, F_JS, F_CTS, F_HXS, F_ZT, F_CJSJ, F_BZ
        FROM T_RQ WHERE F_ID = ?
        """,
        [rq_id],
    )
    if not rows:
        raise ValueError(f"未找到容器ID: {rq_id}")
    return _record(cols, rows[0])


def _code_taken(adapter, code: str, exclude_id: Optional[int] = None) -> bool:
    sql = "SELECT F_ID FROM T_RQ WHERE F_RQBH = ?"
    params: List[Any] = [code]
    if exclude_id is not None:
        sql += " AND F_ID <> ?"
        params.append(exclude_id)
    rows, _ = adapter.fetch_all(sql, params)
    return bool(rows)


def occupied_keys(adapter, rq_id: int) -> Set[Tuple[int, int, int, int]]:
    """仍占用盒位的管子。已出库的管子不再占位。"""
    rows, cols = adapter.fetch_all(
        """
        SELECT F_RQC, F_RQJ, F_RQCT, F_RQH
        FROM T_YBFZ
        WHERE F_RQ_ID = ? AND (F_KCZT IS NULL OR F_KCZT <> ?)
        """,
        [rq_id, "已出库"],
    )
    keys: Set[Tuple[int, int, int, int]] = set()
    for row in rows:
        item = _record(cols, row)
        key = (item.get("F_RQC"), item.get("F_RQJ"), item.get("F_RQCT"), item.get("F_RQH"))
        if any(part is None or str(part).strip() == "" for part in key):
            continue
        keys.add(tuple(int(part) for part in key))
    return keys


def _usage_map(adapter, rq_ids: List[int]) -> Dict[int, int]:
    if not rq_ids:
        return {}
    placeholders = ",".join(["?"] * len(rq_ids))
    rows, cols = adapter.fetch_all(
        f"""
        SELECT F_RQ_ID, F_RQC, F_RQJ, F_RQCT, F_RQH
        FROM T_YBFZ
        WHERE F_RQ_ID IN ({placeholders}) AND (F_KCZT IS NULL OR F_KCZT <> ?)
        """,
        list(rq_ids) + ["已出库"],
    )
    grouped: Dict[int, Set[Tuple[int, int, int, int]]] = {}
    for row in rows:
        item = _record(cols, row)
        rq_id = item.get("F_RQ_ID")
        if rq_id is None:
            continue
        key = (item.get("F_RQC"), item.get("F_RQJ"), item.get("F_RQCT"), item.get("F_RQH"))
        if any(part is None or str(part).strip() == "" for part in key):
            continue
        grouped.setdefault(int(rq_id), set()).add(tuple(int(part) for part in key))
    return {rq_id: len(keys) for rq_id, keys in grouped.items()}


def _present(row: Dict[str, Any], used: int) -> Dict[str, Any]:
    layers = int(row.get("F_CS") or 0)
    racks = int(row.get("F_JS") or 0)
    drawers = int(row.get("F_CTS") or 0)
    boxes = int(row.get("F_HXS") or 0)
    total = layers * racks * drawers * boxes
    return {
        "F_ID": int(row["F_ID"]),
        "F_RQBH": row.get("F_RQBH"),
        "F_RQMC": row.get("F_RQMC"),
        "F_CCWD": row.get("F_CCWD"),
        "F_CS": layers,
        "F_JS": racks,
        "F_CTS": drawers,
        "F_HXS": boxes,
        "F_GG": f"{layers}×{racks}×{drawers}×{boxes}",
        "F_YZY": used,
        "F_ZHW": total,
        "F_YSY": f"{used}/{total}",
        "F_ZT": row.get("F_ZT"),
        "F_CJSJ": row.get("F_CJSJ"),
        "F_BZ": row.get("F_BZ"),
        "F_KBJ": used == 0,
        "F_KSC": used == 0,
    }


def _expand_slots(cur, rq_id: int, layers: int, racks: int, drawers: int, boxes: int) -> None:
    cur.execute("DELETE FROM T_RQ_HW WHERE F_RQ_ID = ?", (rq_id,))
    batch = []
    for layer in range(1, layers + 1):
        for rack in range(1, racks + 1):
            for drawer in range(1, drawers + 1):
                for box in range(1, boxes + 1):
                    batch.append((rq_id, layer, rack, drawer, box))
    cur.executemany(
        "INSERT INTO T_RQ_HW (F_RQ_ID, F_C, F_J, F_CT, F_H) VALUES (?, ?, ?, ?, ?)",
        batch,
    )


def _assert_editable(adapter, rq_id: int) -> None:
    if occupied_keys(adapter, rq_id):
        raise ValueError("容器已有样本占用，只能查看")


def list_containers(payload: Dict[str, Any]) -> Dict[str, Any]:
    ensure_schema()
    adapter = get_adapter()
    skip = int(payload.get("skip") or 0)
    limit = int(payload.get("limit") or 50)
    filters: List[str] = []
    params: List[Any] = []

    keyword = (payload.get("F_GJZ") or "").strip()
    if keyword:
        pred_code, p_code = adapter.like("t.F_RQBH", keyword)
        pred_name, p_name = adapter.like("t.F_RQMC", keyword)
        filters.append(f"({pred_code} OR {pred_name})")
        params.extend(p_code)
        params.extend(p_name)
    for field, column in (("F_RQBH", "t.F_RQBH"), ("F_RQMC", "t.F_RQMC"), ("F_CCWD", "t.F_CCWD")):
        text = (payload.get(field) or "").strip()
        if not text:
            continue
        pred, extra = adapter.like(column, text)
        filters.append(pred)
        params.extend(extra)
    status = (payload.get("F_ZT") or "").strip()
    if status:
        filters.append("t.F_ZT = ?")
        params.append(status)

    where_sql = (" WHERE " + " AND ".join(filters)) if filters else ""
    base_sql = f"""
    SELECT t.F_ID, t.F_RQBH, t.F_RQMC, t.F_CCWD, t.F_CS, t.F_JS, t.F_CTS, t.F_HXS,
           t.F_ZT, t.F_CJSJ, t.F_BZ
    FROM T_RQ t
    {where_sql}
    """
    count_sql = f"SELECT COUNT(1) FROM T_RQ t {where_sql}"
    list_sql, extra = adapter.paginate(base_sql + " ORDER BY t.F_ID DESC", skip=skip, limit=limit)

    with adapter.connect() as conn:
        cur = conn.cursor()
        cur.execute(count_sql, tuple(params))
        total = int(cur.fetchone()[0])
        cur.execute(list_sql, tuple(params + extra))
        cols = [c[0] for c in cur.description]
        rows = cur.fetchall()

    items = [_record(cols, row) for row in rows]
    usage = _usage_map(adapter, [int(item["F_ID"]) for item in items])
    return {
        "items": [_present(item, usage.get(int(item["F_ID"]), 0)) for item in items],
        "total": total,
    }


def list_enabled() -> Dict[str, Any]:
    ensure_schema()
    data = list_containers({"skip": 0, "limit": 500, "F_ZT": "启用"})
    items = [
        {
            "F_ID": item["F_ID"],
            "F_RQBH": item["F_RQBH"],
            "F_RQMC": item["F_RQMC"],
            "F_CCWD": item["F_CCWD"],
            "F_CS": item["F_CS"],
            "F_JS": item["F_JS"],
            "F_CTS": item["F_CTS"],
            "F_HXS": item["F_HXS"],
            "F_GG": item["F_GG"],
            "F_YSY": item["F_YSY"],
        }
        for item in data["items"]
    ]
    return {"items": items, "total": data["total"]}


def get_container(payload: Dict[str, Any]) -> Dict[str, Any]:
    ensure_schema()
    rq_id = payload.get("F_ID")
    if not rq_id:
        raise ValueError("F_ID 不能为空")
    adapter = get_adapter()
    row = _get_row(adapter, int(rq_id))
    return _present(row, len(occupied_keys(adapter, int(rq_id))))


def create_container(payload: Dict[str, Any], user: Dict[str, Any] | None = None) -> Dict[str, Any]:
    ensure_schema()
    spec = _spec(payload)
    adapter = get_adapter()
    if _code_taken(adapter, spec["F_RQBH"]):
        raise ValueError(f"容器编号已存在: {spec['F_RQBH']}")
    now = _now()
    with adapter.connect() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO T_RQ (
                F_RQBH, F_RQMC, F_CCWD, F_CS, F_JS, F_CTS, F_HXS, F_ZT, F_CJSJ, F_BZ
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                spec["F_RQBH"], spec["F_RQMC"], spec["F_CCWD"],
                spec["F_CS"], spec["F_JS"], spec["F_CTS"], spec["F_HXS"],
                spec["F_ZT"], now, spec["F_BZ"],
            ),
        )
        rq_id = int(adapter.last_insert_id(cur))
        _expand_slots(cur, rq_id, spec["F_CS"], spec["F_JS"], spec["F_CTS"], spec["F_HXS"])
        conn.commit()
    _log("新增容器", spec["F_RQBH"], {"F_ID": rq_id}, user)
    return get_container({"F_ID": rq_id})


def update_container(payload: Dict[str, Any], user: Dict[str, Any] | None = None) -> Dict[str, Any]:
    ensure_schema()
    rq_id = payload.get("F_ID")
    if not rq_id:
        raise ValueError("F_ID 不能为空")
    rq_id = int(rq_id)
    spec = _spec(payload)
    adapter = get_adapter()
    _get_row(adapter, rq_id)
    _assert_editable(adapter, rq_id)
    if _code_taken(adapter, spec["F_RQBH"], exclude_id=rq_id):
        raise ValueError(f"容器编号已存在: {spec['F_RQBH']}")
    with adapter.connect() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT COUNT(1) FROM T_YBFZ
            WHERE F_RQ_ID = ? AND (F_KCZT IS NULL OR F_KCZT <> ?)
            """,
            (rq_id, "已出库"),
        )
        if int(cur.fetchone()[0] or 0) > 0:
            raise ValueError("容器已有样本占用，只能查看")
        cur.execute(
            """
            UPDATE T_RQ
            SET F_RQBH = ?, F_RQMC = ?, F_CCWD = ?, F_CS = ?, F_JS = ?,
                F_CTS = ?, F_HXS = ?, F_ZT = ?, F_BZ = ?
            WHERE F_ID = ?
            """,
            (
                spec["F_RQBH"], spec["F_RQMC"], spec["F_CCWD"],
                spec["F_CS"], spec["F_JS"], spec["F_CTS"], spec["F_HXS"],
                spec["F_ZT"], spec["F_BZ"], rq_id,
            ),
        )
        _expand_slots(cur, rq_id, spec["F_CS"], spec["F_JS"], spec["F_CTS"], spec["F_HXS"])
        conn.commit()
    _log("修改容器", spec["F_RQBH"], {"F_ID": rq_id}, user)
    return get_container({"F_ID": rq_id})


def delete_container(payload: Dict[str, Any], user: Dict[str, Any] | None = None) -> Dict[str, Any]:
    ensure_schema()
    rq_id = payload.get("F_ID")
    if not rq_id:
        raise ValueError("F_ID 不能为空")
    rq_id = int(rq_id)
    adapter = get_adapter()
    row = _get_row(adapter, rq_id)
    _assert_editable(adapter, rq_id)
    with adapter.connect() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT COUNT(1) FROM T_YBFZ
            WHERE F_RQ_ID = ? AND (F_KCZT IS NULL OR F_KCZT <> ?)
            """,
            (rq_id, "已出库"),
        )
        if int(cur.fetchone()[0] or 0) > 0:
            raise ValueError("容器已有样本占用，不能删除")
        cur.execute("DELETE FROM T_RQ_HW WHERE F_RQ_ID = ?", (rq_id,))
        cur.execute("DELETE FROM T_RQ WHERE F_ID = ?", (rq_id,))
        conn.commit()
    _log("删除容器", row.get("F_RQBH"), {"F_ID": rq_id}, user)
    return {"F_ID": rq_id}


def container_tree(payload: Dict[str, Any]) -> Dict[str, Any]:
    """未指定抽屉时返回整棵层-架-抽屉-盒树。指定层、架、抽屉后只返回该抽屉的盒位。"""
    ensure_schema()
    rq_id = payload.get("F_ID")
    if not rq_id:
        raise ValueError("F_ID 不能为空")
    rq_id = int(rq_id)
    adapter = get_adapter()
    row = _get_row(adapter, rq_id)
    layers = int(row.get("F_CS") or 0)
    racks = int(row.get("F_JS") or 0)
    drawers = int(row.get("F_CTS") or 0)
    boxes = int(row.get("F_HXS") or 0)
    used = occupied_keys(adapter, rq_id)
    header = {
        "F_ID": rq_id,
        "F_RQBH": row.get("F_RQBH"),
        "F_RQMC": row.get("F_RQMC"),
        "F_CCWD": row.get("F_CCWD"),
        "F_ZT": row.get("F_ZT"),
    }
    layer_no = payload.get("F_C")
    rack_no = payload.get("F_J")
    drawer_no = payload.get("F_CT")
    picked = [layer_no, rack_no, drawer_no]
    if any(item is not None and str(item).strip() != "" for item in picked):
        if any(item is None or str(item).strip() == "" for item in picked):
            raise ValueError("查看某一抽屉的盒位时，层、架、抽屉都要传")
        layer_no = parse_dim(layer_no, "层")
        rack_no = parse_dim(rack_no, "架")
        drawer_no = parse_dim(drawer_no, "抽屉")
        if layer_no > layers or rack_no > racks or drawer_no > drawers:
            raise ValueError("所选层、架或抽屉超出容器规格")
        header.update({
            "F_C": layer_no,
            "F_J": rack_no,
            "F_CT": drawer_no,
            "boxes": _boxes(used, layer_no, rack_no, drawer_no, boxes),
        })
        return header
    header["layers"] = [
        {
            "F_C": layer,
            "racks": [
                {
                    "F_J": rack,
                    "drawers": [
                        {
                            "F_CT": drawer,
                            "boxes": _boxes(used, layer, rack, drawer, boxes),
                        }
                        for drawer in range(1, drawers + 1)
                    ],
                }
                for rack in range(1, racks + 1)
            ],
        }
        for layer in range(1, layers + 1)
    ]
    return header


def _boxes(used: Set[Tuple[int, int, int, int]], layer: int, rack: int, drawer: int, boxes: int) -> List[Dict[str, Any]]:
    return [
        {"F_H": box, "F_ZY": (layer, rack, drawer, box) in used}
        for box in range(1, boxes + 1)
    ]


def load_enabled_container(rq_id: int) -> Dict[str, Any]:
    ensure_schema()
    row = _get_row(get_adapter(), rq_id)
    if str(row.get("F_ZT") or "").strip() != "启用":
        raise ValueError("只能选择启用中的容器")
    return row


def _log(op_type: str, operator_hint: Any, detail: Dict[str, Any], user: Dict[str, Any] | None) -> None:
    try:
        operator = None
        if user:
            operator = user.get("F_Ktype") or user.get("F_KTYPE") or user.get("F_YSM")
        insert_op_log(
            module="container_mgmt",
            op_type=op_type,
            operator=operator or operator_hint,
            status="success",
            detail=detail,
        )
    except Exception:
        pass
