"""直接入库：按分装预览结果写入样本分装表，并放进容器。"""
import json
from datetime import datetime
from typing import Any, Dict, List

from app.db.factory import get_adapter
from app.crud.container_mgmt import load_enabled_container, load_layers, occupied_keys
from app.crud.direct_inbound_schema import ensure_schema, format_ccwz, normalize_wz
from app.crud.op_log import insert_op_log
from app.crud.sample_linkage import (
    _fetch_ybfz_by_sgbhs,
    _parse_datetime,
    _resolve_operator,
    _validate_items_ybglx,
)
from app.crud.stock_audit import (
    DOC_IN,
    AUDIT_PENDING,
    SOURCE_INBOUND,
    inbound_needs_audit,
    insert_bill,
)


def store_samples(payload: Dict[str, Any], user: Dict[str, Any] | None = None) -> Dict[str, Any]:
    ensure_schema()
    rq_id = payload.get("F_RQ_ID")
    if not rq_id:
        raise ValueError("F_RQ_ID 不能为空")
    rq_id = int(rq_id)
    items = _items(payload.get("ITEMS") or payload.get("items"))
    container = load_enabled_container(rq_id)
    layer_map = {
        int(item["F_C"]): item
        for item in load_layers(get_adapter(), rq_id, container)
    }
    code = str(container.get("F_RQBH") or "")

    seen_slots = set()
    seen_codes = set()
    for item in items:
        spec = layer_map.get(item["F_C"])
        if (
            not spec
            or item["F_J"] > int(spec["F_JS"])
            or item["F_CT"] > int(spec["F_CTS"])
            or item["F_H"] > int(spec["F_HXS"])
        ):
            raise ValueError(f"管位超出容器规格: {_slot_text(item)}")
        try:
            item["F_WZ"] = normalize_wz(item["F_WZ"], int(spec.get("F_HS") or 1), int(spec.get("F_LS") or 1))
        except ValueError as exc:
            raise ValueError(str(exc))
        item["F_HS"] = int(spec.get("F_HS") or 1)
        item["F_LS"] = int(spec.get("F_LS") or 1)
        slot = (item["F_C"], item["F_J"], item["F_CT"], item["F_H"], item["F_WZ"])
        if slot in seen_slots:
            raise ValueError(f"管位重复: {_slot_text(item)}")
        seen_slots.add(slot)
        if item["F_SGBH"] in seen_codes:
            raise ValueError(f"试管编号重复: {item['F_SGBH']}")
        seen_codes.add(item["F_SGBH"])

    adapter = get_adapter()
    _validate_items_ybglx(items)
    existing = _fetch_ybfz_by_sgbhs([item["F_SGBH"] for item in items])
    if existing:
        raise ValueError("试管编号已存在: " + ", ".join(existing))

    occupied = occupied_keys(adapter, rq_id)
    for item in items:
        slot = (item["F_C"], item["F_J"], item["F_CT"], item["F_H"], item["F_WZ"])
        if slot in occupied:
            raise ValueError(f"管位已被占用: {_slot_text(item)}")

    need_audit = inbound_needs_audit()
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    if need_audit:
        order_status = AUDIT_PENDING
        tube_yszt = "未运送"
        tube_kczt = None
        received_at = None
    else:
        order_status = "已接收"
        tube_yszt = "已接收"
        tube_kczt = "在库"
        received_at = now
    applicant = payload.get("F_FQR") or _resolve_operator(user, adapter)
    applied_at = _parse_datetime(payload.get("F_FQSJ")) or now
    remark = payload.get("F_BZ")
    destination = payload.get("F_YSMDD")
    order_no = f"YSDH{datetime.now().strftime('%Y%m%d%H%M%S%f')}"

    placed = []
    for item in items:
        placed.append({
            **item,
            "F_CCWZ": format_ccwz(code, item["F_C"], item["F_J"], item["F_CT"], item["F_H"], item["F_WZ"]),
        })

    tube_ids: List[int] = []
    with adapter.connect() as conn:
        cur = conn.cursor()
        for item in placed:
            cur.execute(
                """
                SELECT F_ID FROM T_YBFZ
                WHERE F_RQ_ID = ? AND F_RQC = ? AND F_RQJ = ? AND F_RQCT = ? AND F_RQH = ?
                  AND UPPER(F_WZ) = ?
                  AND (F_KCZT IS NULL OR F_KCZT <> ?)
                """,
                (
                    rq_id, item["F_C"], item["F_J"], item["F_CT"], item["F_H"],
                    item["F_WZ"], "已出库",
                ),
            )
            if cur.fetchone():
                raise ValueError(f"管位已被占用: {_slot_text(item)}")
            item["F_RQ_ID"] = rq_id
            item["F_CZR"] = applicant
            tube_id = _insert_tube(
                cur, adapter, item, tube_yszt, tube_kczt, received_at, now, destination,
            )
            item["F_YBFZ_ID"] = tube_id
            tube_ids.append(tube_id)

        cur.execute(
            """
            INSERT INTO T_YSXX (
                F_YSDH, F_YSXX, F_FQR, F_FQSJ, F_YSDZT,
                F_YBSL, F_BZ, F_JSR, F_JSSJ, F_JSBZ, F_YSMDD
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                order_no, json.dumps(tube_ids, ensure_ascii=False), applicant, applied_at,
                order_status, len(tube_ids), remark,
                applicant if received_at else None, received_at, None, destination,
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
                source=SOURCE_INBOUND,
                detail={
                    "F_LY": SOURCE_INBOUND,
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

    _log(user, {"F_YSDH": order_no, "F_ID": order_id, "YBFZ": tube_ids, "F_YSDZT": order_status})
    return {
        "F_ID": order_id,
        "F_YSDH": order_no,
        "F_YSDZT": order_status,
        "F_YBSL": len(tube_ids),
        "F_RQ_ID": rq_id,
        "F_RQBH": code,
        "F_SHID": audit_id,
        "items": placed,
    }


def _items(raw: Any) -> List[Dict[str, Any]]:
    if isinstance(raw, dict):
        raw = [raw]
    if not isinstance(raw, list) or not raw:
        raise ValueError("请提供至少一个入库样本")
    items: List[Dict[str, Any]] = []
    for index, raw_item in enumerate(raw, start=1):
        if not isinstance(raw_item, dict):
            raise ValueError(f"第{index}条样本格式不正确")
        item = dict(raw_item)
        code = str(item.get("F_SGBH") or "").strip()
        zzid = str(item.get("F_ZZID") or "").strip()
        wz = str(item.get("F_WZ") or "").strip()
        if not code or not zzid or not wz:
            raise ValueError(f"第{index}条样本须带试管编号、标本号和盒内位置")
        try:
            item["F_C"] = int(item.get("F_C"))
            item["F_J"] = int(item.get("F_J"))
            item["F_CT"] = int(item.get("F_CT"))
            item["F_H"] = int(item.get("F_H"))
        except (TypeError, ValueError):
            raise ValueError(f"第{index}条样本的层架抽屉盒必须是整数")
        if min(item["F_C"], item["F_J"], item["F_CT"], item["F_H"]) < 1:
            raise ValueError(f"第{index}条样本的层架抽屉盒须从 1 开始")
        item["F_SGBH"] = code
        item["F_ZZID"] = zzid
        item["F_WZ"] = wz
        item.pop("F_ID", None)
        item.pop("F_YBFZ_ID", None)
        items.append(item)
    return items


def _optional_int(value: Any) -> int | None:
    if value is None or str(value).strip() == "":
        return None
    return int(value)


def _insert_tube(
    cur, adapter, item: Dict[str, Any], yszt: str | None, kczt: str | None,
    received_at: str | None, now: str, destination: Any,
) -> int:
    columns = [
        "F_SGBH", "F_ZZID", "F_YBZJ_ID", "F_PYGD_ID", "F_DC",
        "F_YBLX", "F_YBGLX", "F_CCXS", "F_FGS", "F_TJ", "F_XBSL", "F_SPREC",
        "F_BZ", "F_CZR", "F_CJSJ", "F_HZH", "F_HS", "F_LS",
        "F_BRBH", "F_ZYH", "F_MZH", "F_XM", "F_XB", "F_NL", "F_NLDW",
        "F_MZ", "F_HY", "F_ZY", "F_DZ", "F_DH", "F_CHH", "F_SFZH",
        "F_LCZD", "F_LYKS", "F_KS", "F_CXSJ", "F_XMBH",
        "F_WZ", "F_YSZT", "F_KCZT", "F_RKSJ", "F_JSSJ",
        "F_RQ_ID", "F_RQC", "F_RQJ", "F_RQCT", "F_RQH", "F_CCWZ",
    ]
    values = [
        item.get("F_SGBH"), item.get("F_ZZID"),
        _optional_int(item.get("F_YBZJ_ID")), _optional_int(item.get("F_PYGD_ID")),
        item.get("F_DC"), item.get("F_YBLX"), item.get("F_YBGLX"), item.get("F_CCXS"),
        _optional_int(item.get("F_FGS")), item.get("F_TJ"), item.get("F_XBSL"), item.get("F_SPREC"),
        item.get("F_BZ"), item.get("F_CZR"), now, item.get("F_HZH"),
        item.get("F_HS"), item.get("F_LS"),
        item.get("F_BRBH"), item.get("F_ZYH"), item.get("F_MZH"), item.get("F_XM"),
        item.get("F_XB"), _optional_int(item.get("F_NL")), item.get("F_NLDW"),
        item.get("F_MZ"), item.get("F_HY"), item.get("F_ZY"), item.get("F_DZ"),
        item.get("F_DH"), item.get("F_CHH"), item.get("F_SFZH"),
        item.get("F_LCZD"), item.get("F_LYKS"), item.get("F_KS"), item.get("F_CXSJ"),
        item.get("F_XMBH"), item.get("F_WZ"), yszt, kczt, received_at, received_at,
        item["F_RQ_ID"], item["F_C"], item["F_J"], item["F_CT"], item["F_H"], item["F_CCWZ"],
    ]
    if destination:
        columns.append("F_YSMDD")
        values.append(destination)
    holders = ", ".join(["?"] * len(columns))
    cur.execute(
        f"INSERT INTO T_YBFZ ({', '.join(columns)}) VALUES ({holders})",
        tuple(values),
    )
    return int(adapter.last_insert_id(cur))


def _slot_text(item: Dict[str, Any]) -> str:
    return f"{item['F_C']}层{item['F_J']}架{item['F_CT']}抽屉{item['F_H']}盒{item['F_WZ']}"


def _box_text(items: List[Dict[str, Any]]) -> str:
    seen = []
    for item in items:
        text = _slot_text(item)
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
