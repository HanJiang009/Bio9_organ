"""直接入库：把已有分装管放进容器，并写入现有运输单。"""
import json
from datetime import datetime
from typing import Any, Dict, List

from app.db.factory import get_adapter
from app.crud.container_mgmt import load_enabled_container, occupied_keys
from app.crud.direct_inbound_schema import ensure_schema, format_ccwz, lock_id
from app.crud.op_log import insert_op_log
from app.crud.sample_linkage import _parse_datetime, _resolve_operator
from app.crud.stock_audit import DOC_IN, AUDIT_PENDING, inbound_needs_audit, insert_bill


def store_samples(payload: Dict[str, Any], user: Dict[str, Any] | None = None) -> Dict[str, Any]:
    ensure_schema()
    rq_id = payload.get("F_RQ_ID")
    if not rq_id:
        raise ValueError("F_RQ_ID 不能为空")
    rq_id = int(rq_id)
    items = _items(payload.get("ITEMS") or payload.get("items"))
    container = load_enabled_container(rq_id)
    layers = int(container.get("F_CS") or 0)
    racks = int(container.get("F_JS") or 0)
    drawers = int(container.get("F_CTS") or 0)
    boxes = int(container.get("F_HXS") or 0)
    code = str(container.get("F_RQBH") or "")

    seen_slots = set()
    tube_ids: List[int] = []
    for item in items:
        slot = (item["F_C"], item["F_J"], item["F_CT"], item["F_H"])
        if slot in seen_slots:
            raise ValueError(f"盒位重复: {_slot_text(slot)}")
        seen_slots.add(slot)
        if item["F_C"] > layers or item["F_J"] > racks or item["F_CT"] > drawers or item["F_H"] > boxes:
            raise ValueError(f"盒位超出容器规格: {_slot_text(slot)}")
        tube_ids.append(item["F_YBFZ_ID"])

    adapter = get_adapter()
    tubes = _load_tubes(adapter, tube_ids)
    for item in items:
        tube = tubes.get(item["F_YBFZ_ID"])
        if not tube:
            raise ValueError(f"找不到分装记录ID: {item['F_YBFZ_ID']}")
        _assert_tube_free(tube)

    occupied = occupied_keys(adapter, rq_id)
    for item in items:
        slot = (item["F_C"], item["F_J"], item["F_CT"], item["F_H"])
        if slot in occupied:
            raise ValueError(f"盒位已被占用: {_slot_text(slot)}")

    need_audit = inbound_needs_audit()
    tube_status = AUDIT_PENDING if need_audit else "运送中"
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    applicant = payload.get("F_FQR") or _resolve_operator(user, adapter)
    applied_at = _parse_datetime(payload.get("F_FQSJ")) or now
    remark = payload.get("F_BZ")
    destination = payload.get("F_YSMDD")
    order_no = f"YSDH{datetime.now().strftime('%Y%m%d%H%M%S%f')}"

    placed = []
    for item in items:
        slot = (item["F_C"], item["F_J"], item["F_CT"], item["F_H"])
        tube = tubes[item["F_YBFZ_ID"]]
        placed.append({
            **item,
            "F_SGBH": tube.get("F_SGBH"),
            "F_HZH": tube.get("F_HZH"),
            "F_CCWZ": format_ccwz(code, *slot),
        })

    with adapter.connect() as conn:
        cur = conn.cursor()
        for item in placed:
            cur.execute(
                """
                SELECT F_ID FROM T_YBFZ
                WHERE F_RQ_ID = ? AND F_RQC = ? AND F_RQJ = ? AND F_RQCT = ? AND F_RQH = ?
                  AND (F_KCZT IS NULL OR F_KCZT <> ?)
                """,
                (rq_id, item["F_C"], item["F_J"], item["F_CT"], item["F_H"], "已出库"),
            )
            found = cur.fetchone()
            if found and int(found[0]) not in tube_ids:
                raise ValueError(f"盒位已被占用: {_slot_text((item['F_C'], item['F_J'], item['F_CT'], item['F_H']))}")
            values = [
                rq_id, item["F_C"], item["F_J"], item["F_CT"], item["F_H"],
                item["F_CCWZ"], tube_status,
            ]
            sets = "F_RQ_ID = ?, F_RQC = ?, F_RQJ = ?, F_RQCT = ?, F_RQH = ?, F_CCWZ = ?, F_YSZT = ?"
            if destination:
                sets += ", F_YSMDD = ?"
                values.append(destination)
            values.append(item["F_YBFZ_ID"])
            cur.execute(
                f"""
                UPDATE T_YBFZ SET {sets}
                WHERE F_ID = ? AND (F_YSZT IS NULL OR F_YSZT = '' OR F_YSZT = ?)
                """,
                tuple(values + ["未运送"]),
            )
            if cur.rowcount == 0:
                raise ValueError(f"分装记录 {item['F_YBFZ_ID']} 状态已变化，不能直接入库")

        cur.execute(
            """
            INSERT INTO T_YSXX (
                F_YSDH, F_YSXX, F_FQR, F_FQSJ, F_YSDZT,
                F_YBSL, F_BZ, F_JSR, F_JSSJ, F_JSBZ, F_YSMDD
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                order_no, json.dumps(tube_ids, ensure_ascii=False), applicant, applied_at,
                tube_status, len(tube_ids), remark, None, None, None, destination,
            ),
        )
        order_id = int(adapter.last_insert_id(cur))
        audit_id = None
        if need_audit:
            audit_id = insert_bill(
                cur, adapter,
                doc_type=DOC_IN,
                doc_id=order_id,
                doc_no=order_no,
                box_no=_box_text(placed),
                sample_count=len(tube_ids),
                applicant=applicant,
                applied_at=applied_at,
                detail={
                    "F_DJLX": DOC_IN,
                    "F_YSDH": order_no,
                    "F_RQ_ID": rq_id,
                    "F_RQBH": code,
                    "F_RQMC": container.get("F_RQMC"),
                    "F_CCWD": container.get("F_CCWD"),
                    "F_YSMDD": destination,
                    "F_BZ": remark,
                    "items": placed,
                },
            )
        conn.commit()

    _log(user, {"F_YSDH": order_no, "F_ID": order_id, "YBFZ": tube_ids, "F_YSDZT": tube_status})
    return {
        "F_ID": order_id,
        "F_YSDH": order_no,
        "F_YSDZT": tube_status,
        "F_YBSL": len(tube_ids),
        "F_RQ_ID": rq_id,
        "F_RQBH": code,
        "F_SHID": audit_id,
        "items": placed,
    }


def _items(raw: Any) -> List[Dict[str, int]]:
    if isinstance(raw, dict):
        raw = [raw]
    if not isinstance(raw, list) or not raw:
        raise ValueError("请提供至少一个入库样本")
    items: List[Dict[str, int]] = []
    seen_ids = set()
    for index, raw_item in enumerate(raw, start=1):
        if not isinstance(raw_item, dict):
            raise ValueError(f"第{index}条样本格式不正确")
        try:
            tube_id = int(raw_item.get("F_YBFZ_ID"))
            slot = {
                "F_YBFZ_ID": tube_id,
                "F_C": int(raw_item.get("F_C")),
                "F_J": int(raw_item.get("F_J")),
                "F_CT": int(raw_item.get("F_CT")),
                "F_H": int(raw_item.get("F_H")),
            }
        except (TypeError, ValueError):
            raise ValueError(f"第{index}条样本的分装ID和层架抽屉盒必须是整数")
        if min(slot["F_C"], slot["F_J"], slot["F_CT"], slot["F_H"]) < 1:
            raise ValueError(f"第{index}条样本的层架抽屉盒须从 1 开始")
        if tube_id in seen_ids:
            raise ValueError(f"分装记录重复: {tube_id}")
        seen_ids.add(tube_id)
        items.append(slot)
    return items


def _load_tubes(adapter, tube_ids: List[int]) -> Dict[int, Dict[str, Any]]:
    placeholders = ",".join(["?"] * len(tube_ids))
    rows, cols = adapter.fetch_all(
        f"""
        SELECT F_ID, F_SGBH, F_HZH, F_KCZT, F_YSZT, F_RQ_ID, F_LOCK_CKD_ID
        FROM T_YBFZ WHERE F_ID IN ({placeholders})
        """,
        tube_ids,
    )
    return {int(dict(zip(cols, row))["F_ID"]): dict(zip(cols, row)) for row in rows}


def _assert_tube_free(tube: Dict[str, Any]) -> None:
    tube_id = int(tube["F_ID"])
    stock = str(tube.get("F_KCZT") or "").strip()
    transport = str(tube.get("F_YSZT") or "").strip()
    if stock in ("在库", "已出库", "出库中"):
        raise ValueError(f"分装记录 {tube_id} 当前库存状态为{stock}，不能直接入库")
    if transport not in ("", "未运送"):
        raise ValueError(f"分装记录 {tube_id} 当前运输状态为{transport}，不能直接入库")
    if lock_id(tube.get("F_LOCK_CKD_ID")):
        raise ValueError(f"分装记录 {tube_id} 已被出库单锁定")
    if lock_id(tube.get("F_RQ_ID")):
        raise ValueError(f"分装记录 {tube_id} 已放入容器")


def _slot_text(slot) -> str:
    layer, rack, drawer, box = slot
    return f"{layer}层{rack}架{drawer}抽屉{box}盒"


def _box_text(items: List[Dict[str, Any]]) -> str:
    seen = []
    for item in items:
        text = _slot_text((item["F_C"], item["F_J"], item["F_CT"], item["F_H"]))
        if text not in seen:
            seen.append(text)
    return ",".join(seen)


def _log(user: Dict[str, Any] | None, detail: Dict[str, Any]) -> None:
    try:
        operator = None
        if user:
            operator = user.get("F_Ktype") or user.get("F_KTYPE") or user.get("F_YSM")
        insert_op_log(
            module="sample_inbound",
            op_type="样本入库",
            operator=operator,
            status="success",
            detail=detail,
        )
    except Exception:
        pass
