"""四层容器：层、架、抽屉、盒。盒位在保存时一次展开。"""
from datetime import datetime
from typing import Any, Dict, List, Optional, Set, Tuple

from app.db.factory import get_adapter
from app.crud.direct_inbound_schema import (
    _SLOT_MAX,
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
    layers = _parse_layers(payload.get("LAYERS") or payload.get("layers"))
    total = sum(item["F_JS"] * item["F_CTS"] * item["F_HXS"] for item in layers)
    if total > _SLOT_MAX:
        raise ValueError(f"盒位总数不能超过 {_SLOT_MAX}，当前为 {total}")
    first = layers[0]
    return {
        "F_RQBH": code,
        "F_RQMC": name,
        "F_RQLB": _clean(payload.get("F_RQLB"), 64) or None,
        "F_CCWD": _clean(payload.get("F_CCWD"), 64) or None,
        "F_CS": len(layers),
        "F_JS": first["F_JS"],
        "F_CTS": first["F_CTS"],
        "F_HXS": first["F_HXS"],
        "F_ZT": _status(payload.get("F_ZT")),
        "F_BZ": _clean(payload.get("F_BZ"), 500) or None,
        "LAYERS": layers,
        "F_ZHW": total,
    }


def _parse_layers(raw: Any) -> List[Dict[str, Any]]:
    if isinstance(raw, dict):
        raw = [raw]
    if not isinstance(raw, list) or not raw:
        raise ValueError("空间结构请传 LAYERS 数组，每一层单独配置架数、抽屉数和盒数")
    if len(raw) > 50:
        raise ValueError("层数不能超过 50")
    layers: List[Dict[str, Any]] = []
    for index, item in enumerate(raw, start=1):
        if not isinstance(item, dict):
            raise ValueError(f"第{index}层格式不正确")
        name = _clean(item.get("F_CMC"), 64) or f"第{index}层"
        racks = parse_dim(item.get("F_JS"), f"第{index}层冻存架数")
        drawers = parse_dim(item.get("F_CTS"), f"第{index}层每架抽屉数")
        boxes = parse_dim(item.get("F_HXS"), f"第{index}层每抽屉盒数")
        rows = parse_dim(item.get("F_HS") or 10, f"第{index}层盒规格行数")
        cols = parse_dim(item.get("F_LS") or 10, f"第{index}层盒规格列数")
        layers.append({
            "F_C": index,
            "F_CMC": name,
            "F_JS": racks,
            "F_CTS": drawers,
            "F_HXS": boxes,
            "F_HS": rows,
            "F_LS": cols,
        })
    return layers


def _record(cols, row) -> Dict[str, Any]:
    return dict(zip(cols, row))


def _get_row(adapter, rq_id: int) -> Dict[str, Any]:
    rows, cols = adapter.fetch_all(
        """
        SELECT F_ID, F_RQBH, F_RQMC, F_RQLB, F_CCWD, F_CS, F_JS, F_CTS, F_HXS, F_ZT, F_CJSJ, F_BZ
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


def _layer_view(item: Dict[str, Any]) -> Dict[str, Any]:
    racks = int(item["F_JS"])
    drawers = int(item["F_CTS"])
    boxes = int(item["F_HXS"])
    rows = int(item.get("F_HS") or 1)
    cols = int(item.get("F_LS") or 1)
    box_total = racks * drawers * boxes
    return {
        "F_C": int(item["F_C"]),
        "F_CMC": item.get("F_CMC") or f"第{item['F_C']}层",
        "F_JS": racks,
        "F_CTS": drawers,
        "F_HXS": boxes,
        "F_HS": rows,
        "F_LS": cols,
        "F_HGG": f"{rows}×{cols}",
        "F_HXW": box_total,
        "F_RL": box_total * rows * cols,
    }


def _summary(layers: List[Dict[str, Any]]) -> str:
    if not layers:
        return ""
    specs = [f"{item['F_JS']}×{item['F_CTS']}×{item['F_HXS']}" for item in layers]
    if len(set(specs)) == 1:
        return f"{len(layers)}×{specs[0]}"
    return "、".join(f"第{item['F_C']}层{item['F_JS']}×{item['F_CTS']}×{item['F_HXS']}" for item in layers)


def _present(row: Dict[str, Any], used: int, layers: List[Dict[str, Any]]) -> Dict[str, Any]:
    shown = [_layer_view(item) for item in layers]
    total = sum(item["F_HXW"] for item in shown)
    return {
        "F_ID": int(row["F_ID"]),
        "F_RQBH": row.get("F_RQBH"),
        "F_RQMC": row.get("F_RQMC"),
        "F_RQLB": row.get("F_RQLB") or "",
        "F_CCWD": row.get("F_CCWD"),
        "F_CS": len(shown),
        "F_GG": _summary(shown),
        "LAYERS": shown,
        "F_YZY": used,
        "F_ZHW": total,
        "F_YSY": f"{used}/{total}",
        "F_ZT": row.get("F_ZT"),
        "F_CJSJ": row.get("F_CJSJ"),
        "F_BZ": row.get("F_BZ"),
        "F_KBJ": used == 0,
        "F_KSC": used == 0,
    }


def _fetch_layers(adapter, rows: List[Dict[str, Any]]) -> Dict[int, List[Dict[str, Any]]]:
    ids = [int(row["F_ID"]) for row in rows]
    grouped: Dict[int, List[Dict[str, Any]]] = {rq_id: [] for rq_id in ids}
    if not ids:
        return grouped
    placeholders = ",".join(["?"] * len(ids))
    stored, cols = adapter.fetch_all(
        f"""
        SELECT F_RQ_ID, F_XH, F_CMC, F_JS, F_CTS, F_HXS, F_HS, F_LS
        FROM T_RQ_C
        WHERE F_RQ_ID IN ({placeholders})
        ORDER BY F_RQ_ID, F_XH
        """,
        ids,
    )
    for raw in stored:
        item = _record(cols, raw)
        rq_id = int(item["F_RQ_ID"])
        grouped.setdefault(rq_id, []).append({
            "F_C": int(item["F_XH"]),
            "F_CMC": item.get("F_CMC"),
            "F_JS": int(item["F_JS"]),
            "F_CTS": int(item["F_CTS"]),
            "F_HXS": int(item["F_HXS"]),
            "F_HS": int(item.get("F_HS") or 1),
            "F_LS": int(item.get("F_LS") or 1),
        })
    headers = {int(row["F_ID"]): row for row in rows}
    for rq_id, layers in grouped.items():
        if layers:
            continue
        header = headers.get(rq_id) or {}
        count = int(header.get("F_CS") or 0)
        racks = int(header.get("F_JS") or 0)
        drawers = int(header.get("F_CTS") or 0)
        boxes = int(header.get("F_HXS") or 0)
        if min(count, racks, drawers, boxes) < 1:
            continue
        grouped[rq_id] = [
            {
                "F_C": index,
                "F_CMC": f"第{index}层",
                "F_JS": racks,
                "F_CTS": drawers,
                "F_HXS": boxes,
                "F_HS": 10,
                "F_LS": 10,
            }
            for index in range(1, count + 1)
        ]
    return grouped


def load_layers(adapter, rq_id: int, header: Dict[str, Any]) -> List[Dict[str, Any]]:
    return _fetch_layers(adapter, [{**header, "F_ID": rq_id}]).get(rq_id, [])


def _save_structure(cur, rq_id: int, layers: List[Dict[str, Any]]) -> None:
    cur.execute("DELETE FROM T_RQ_C WHERE F_RQ_ID = ?", (rq_id,))
    cur.execute("DELETE FROM T_RQ_HW WHERE F_RQ_ID = ?", (rq_id,))
    cur.executemany(
        """
        INSERT INTO T_RQ_C (F_RQ_ID, F_XH, F_CMC, F_JS, F_CTS, F_HXS, F_HS, F_LS)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (rq_id, item["F_C"], item["F_CMC"], item["F_JS"], item["F_CTS"], item["F_HXS"], item["F_HS"], item["F_LS"])
            for item in layers
        ],
    )
    slots = []
    for item in layers:
        for rack in range(1, item["F_JS"] + 1):
            for drawer in range(1, item["F_CTS"] + 1):
                for box in range(1, item["F_HXS"] + 1):
                    slots.append((rq_id, item["F_C"], rack, drawer, box))
    if slots:
        cur.executemany(
            "INSERT INTO T_RQ_HW (F_RQ_ID, F_C, F_J, F_CT, F_H) VALUES (?, ?, ?, ?, ?)",
            slots,
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
    category = (payload.get("F_RQLB") or "").strip()
    if category:
        filters.append("t.F_RQLB = ?")
        params.append(category)
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
    SELECT t.F_ID, t.F_RQBH, t.F_RQMC, t.F_RQLB, t.F_CCWD, t.F_CS, t.F_JS, t.F_CTS, t.F_HXS,
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
    layers = _fetch_layers(adapter, items)
    return {
        "items": [
            _present(item, usage.get(int(item["F_ID"]), 0), layers.get(int(item["F_ID"]), []))
            for item in items
        ],
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
            "F_RQLB": item["F_RQLB"],
            "F_CCWD": item["F_CCWD"],
            "F_GG": item["F_GG"],
            "LAYERS": item["LAYERS"],
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
    return _present(row, len(occupied_keys(adapter, int(rq_id))), load_layers(adapter, int(rq_id), row))


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
                F_RQBH, F_RQMC, F_RQLB, F_CCWD, F_CS, F_JS, F_CTS, F_HXS, F_ZT, F_CJSJ, F_BZ
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                spec["F_RQBH"], spec["F_RQMC"], spec["F_RQLB"], spec["F_CCWD"],
                spec["F_CS"], spec["F_JS"], spec["F_CTS"], spec["F_HXS"],
                spec["F_ZT"], now, spec["F_BZ"],
            ),
        )
        rq_id = int(adapter.last_insert_id(cur))
        _save_structure(cur, rq_id, spec["LAYERS"])
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
            SET F_RQBH = ?, F_RQMC = ?, F_RQLB = ?, F_CCWD = ?, F_CS = ?, F_JS = ?,
                F_CTS = ?, F_HXS = ?, F_ZT = ?, F_BZ = ?
            WHERE F_ID = ?
            """,
            (
                spec["F_RQBH"], spec["F_RQMC"], spec["F_RQLB"], spec["F_CCWD"],
                spec["F_CS"], spec["F_JS"], spec["F_CTS"], spec["F_HXS"],
                spec["F_ZT"], spec["F_BZ"], rq_id,
            ),
        )
        _save_structure(cur, rq_id, spec["LAYERS"])
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
        cur.execute("DELETE FROM T_RQ_C WHERE F_RQ_ID = ?", (rq_id,))
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
    layer_rows = load_layers(adapter, rq_id, row)
    layer_map = {int(item["F_C"]): item for item in layer_rows}
    used = occupied_keys(adapter, rq_id)
    header = {
        "F_ID": rq_id,
        "F_RQBH": row.get("F_RQBH"),
        "F_RQMC": row.get("F_RQMC"),
        "F_RQLB": row.get("F_RQLB") or "",
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
        spec = layer_map.get(layer_no)
        if not spec or rack_no > int(spec["F_JS"]) or drawer_no > int(spec["F_CTS"]):
            raise ValueError("所选层、架或抽屉超出容器规格")
        header.update({
            "F_C": layer_no,
            "F_J": rack_no,
            "F_CT": drawer_no,
            "boxes": _boxes(used, layer_no, rack_no, drawer_no, int(spec["F_HXS"])),
        })
        return header
    header["layers"] = [_tree_layer(item, used) for item in layer_rows]
    return header


def _tree_layer(item: Dict[str, Any], used: Set[Tuple[int, int, int, int]]) -> Dict[str, Any]:
    layer = int(item["F_C"])
    racks = []
    for rack in range(1, int(item["F_JS"]) + 1):
        drawers = []
        occupied = 0
        total = 0
        for drawer in range(1, int(item["F_CTS"]) + 1):
            boxes = _boxes(used, layer, rack, drawer, int(item["F_HXS"]))
            drawers.append({"F_CT": drawer, "boxes": boxes})
            total += len(boxes)
            occupied += sum(1 for box in boxes if box["F_ZY"])
        racks.append({
            "F_J": rack,
            "F_GS": occupied,
            "F_ZYL": round(occupied * 100 / total, 1) if total else 0,
            "drawers": drawers,
        })
    return {
        "F_C": layer,
        "F_CMC": item.get("F_CMC"),
        "F_HS": int(item.get("F_HS") or 1),
        "F_LS": int(item.get("F_LS") or 1),
        "racks": racks,
    }


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
