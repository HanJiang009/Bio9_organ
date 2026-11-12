"""出入库审核开关和审核单。入库只覆盖直接入库，出库覆盖出库单。"""
import json
from datetime import datetime
from typing import Any, Dict, List, Optional

from app.db.factory import get_adapter
from app.crud.direct_inbound_schema import ensure_schema, lock_id
from app.crud.op_log import insert_op_log


DOC_IN = "入库"
DOC_OUT = "出库"
AUDIT_PENDING = "待审核"
AUDIT_PASS = "审核通过"
AUDIT_REJECT = "审核驳回"
OUTBOUND_READY = "待出库"


class StockAuditPermissionError(PermissionError):
    """当前账号不是超级管理员。"""


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _format_datetime(val: Any) -> str | None:
    if val is None:
        return None
    if isinstance(val, datetime):
        return val.strftime("%Y-%m-%d %H:%M:%S")
    try:
        return datetime.fromisoformat(str(val)).strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return str(val)


def _flag(value: Any) -> str:
    text = "" if value is None else str(value).strip().lower()
    if text in ("是", "1", "true", "yes", "y"):
        return "是"
    if text in ("否", "0", "false", "no", "n", ""):
        return "否"
    raise ValueError("审核开关只能填是或否")


def _record(cols, row) -> Dict[str, Any]:
    return dict(zip(cols, row))


def assert_super_admin(user: Dict[str, Any] | None) -> None:
    from app.core.session_role import account_role_names
    if "超级管理员" not in account_role_names(user):
        raise StockAuditPermissionError("仅超级管理员可配置出入库审核开关")


def audit_flags() -> Dict[str, str]:
    ensure_schema()
    adapter = get_adapter()
    rows, cols = adapter.fetch_all(
        "SELECT F_ID, F_CKSH, F_RKSH FROM T_CRKSH_CFG ORDER BY F_ID",
        [],
    )
    if not rows:
        return {"F_CKSH": "否", "F_RKSH": "否"}
    record = _record(cols, rows[0])
    return {
        "F_CKSH": "是" if str(record.get("F_CKSH") or "").strip() == "是" else "否",
        "F_RKSH": "是" if str(record.get("F_RKSH") or "").strip() == "是" else "否",
    }


def outbound_needs_audit() -> bool:
    return audit_flags()["F_CKSH"] == "是"


def inbound_needs_audit() -> bool:
    return audit_flags()["F_RKSH"] == "是"


def save_flags(payload: Dict[str, Any], user: Dict[str, Any] | None = None) -> Dict[str, str]:
    assert_super_admin(user)
    flags = {"F_CKSH": _flag(payload.get("F_CKSH")), "F_RKSH": _flag(payload.get("F_RKSH"))}
    ensure_schema()
    adapter = get_adapter()
    with adapter.connect() as conn:
        cur = conn.cursor()
        cur.execute("DELETE FROM T_CRKSH_CFG")
        cur.execute(
            "INSERT INTO T_CRKSH_CFG (F_CKSH, F_RKSH) VALUES (?, ?)",
            (flags["F_CKSH"], flags["F_RKSH"]),
        )
        conn.commit()
    _log("配置审核开关", user, flags)
    return flags


def insert_bill(
    cur,
    adapter,
    *,
    doc_type: str,
    doc_id: int,
    doc_no: str,
    box_no: str,
    sample_count: int,
    applicant: Any,
    applied_at: str,
    detail: Dict[str, Any],
) -> int:
    text = (box_no or "")[:1000]
    cur.execute(
        """
        INSERT INTO T_CRKSH (
            F_DJLX, F_DJID, F_DJH, F_HH, F_YBSL, F_SQR, F_SQSJ, F_SHZT, F_DETAIL
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            doc_type, doc_id, doc_no, text, sample_count, applicant, applied_at,
            AUDIT_PENDING, json.dumps(detail, ensure_ascii=False),
        ),
    )
    return int(adapter.last_insert_id(cur))


def list_audits(payload: Dict[str, Any]) -> Dict[str, Any]:
    ensure_schema()
    adapter = get_adapter()
    skip = int(payload.get("skip") or 0)
    limit = int(payload.get("limit") or 50)
    filters: List[str] = []
    params: List[Any] = []

    tab = (payload.get("F_TAB") or "").strip()
    if tab == "待审核":
        filters.append("t.F_SHZT = ?")
        params.append(AUDIT_PENDING)
    doc_type = (payload.get("F_DJLX") or "").strip()
    if doc_type:
        if doc_type not in (DOC_IN, DOC_OUT):
            raise ValueError("单据类型只能是入库或出库")
        filters.append("t.F_DJLX = ?")
        params.append(doc_type)
    for field, column in (("F_DJH", "t.F_DJH"), ("F_SQR", "t.F_SQR"), ("F_HH", "t.F_HH")):
        text = (payload.get(field) or "").strip()
        if text:
            pred, extra = adapter.like(column, text)
            filters.append(pred)
            params.extend(extra)
    start = (payload.get("F_SQSJ_START") or "").strip()
    end = (payload.get("F_SQSJ_END") or "").strip()
    if start:
        filters.append("t.F_SQSJ >= ?")
        params.append(start)
    if end:
        filters.append("t.F_SQSJ < ?")
        params.append(end + " 23:59:59" if len(end) == 10 else end)

    where_sql = (" WHERE " + " AND ".join(filters)) if filters else ""
    base_sql = f"""
    SELECT t.F_ID, t.F_DJLX, t.F_DJID, t.F_DJH, t.F_HH, t.F_YBSL,
           t.F_SQR, t.F_SQSJ, t.F_SHZT, t.F_SHR, t.F_SHSJ, t.F_SHBZ
    FROM T_CRKSH t
    {where_sql}
    """
    count_sql = f"SELECT COUNT(1) FROM T_CRKSH t {where_sql}"
    list_sql, extra = adapter.paginate(base_sql + " ORDER BY t.F_ID DESC", skip=skip, limit=limit)
    with adapter.connect() as conn:
        cur = conn.cursor()
        cur.execute(count_sql, tuple(params))
        total = int(cur.fetchone()[0])
        cur.execute(list_sql, tuple(params + extra))
        cols = [c[0] for c in cur.description]
        rows = cur.fetchall()
    items = []
    for row in rows:
        record = _record(cols, row)
        record["F_SQSJ"] = _format_datetime(record.get("F_SQSJ"))
        record["F_SHSJ"] = _format_datetime(record.get("F_SHSJ"))
        record["F_KSH"] = str(record.get("F_SHZT") or "") == AUDIT_PENDING
        items.append(record)
    return {"items": items, "total": total}


def get_audit(payload: Dict[str, Any]) -> Dict[str, Any]:
    ensure_schema()
    audit_id = payload.get("F_ID")
    if not audit_id:
        raise ValueError("F_ID 不能为空")
    adapter = get_adapter()
    rows, cols = adapter.fetch_all(
        """
        SELECT F_ID, F_DJLX, F_DJID, F_DJH, F_HH, F_YBSL, F_SQR, F_SQSJ,
               F_SHZT, F_SHR, F_SHSJ, F_SHBZ, F_DETAIL
        FROM T_CRKSH WHERE F_ID = ?
        """,
        [int(audit_id)],
    )
    if not rows:
        raise ValueError(f"未找到审核单ID: {audit_id}")
    record = _record(cols, rows[0])
    detail = _load_detail(record.get("F_DETAIL"))
    record.pop("F_DETAIL", None)
    record["F_SQSJ"] = _format_datetime(record.get("F_SQSJ"))
    record["F_SHSJ"] = _format_datetime(record.get("F_SHSJ"))
    record["F_DETAIL"] = detail
    record["F_KSH"] = str(record.get("F_SHZT") or "") == AUDIT_PENDING
    return record


def review_audit(payload: Dict[str, Any], user: Dict[str, Any] | None = None) -> Dict[str, Any]:
    ensure_schema()
    audit_id = payload.get("F_ID")
    if not audit_id:
        raise ValueError("F_ID 不能为空")
    decision = str(payload.get("F_JG") or "").strip()
    if decision in ("通过", AUDIT_PASS):
        passed = True
    elif decision in ("不通过", "驳回", AUDIT_REJECT):
        passed = False
    else:
        raise ValueError("审核结果只能是通过或不通过")
    remark = payload.get("F_SHBZ")
    if remark is not None:
        remark = str(remark).strip() or None
        if remark and len(remark) > 1000:
            raise ValueError("审核备注超过 1000 个字符")

    adapter = get_adapter()
    from app.crud.sample_linkage import _resolve_operator
    reviewer = _resolve_operator(user, adapter)
    now = _now()
    push_outbound_id = None

    with adapter.connect() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT F_ID, F_DJLX, F_DJID, F_DJH, F_SHZT
            FROM T_CRKSH WHERE F_ID = ?
            """,
            (int(audit_id),),
        )
        row = cur.fetchone()
        if not row:
            raise ValueError(f"未找到审核单ID: {audit_id}")
        cols = [c[0] for c in cur.description]
        bill = dict(zip(cols, row))
        if str(bill.get("F_SHZT") or "") != AUDIT_PENDING:
            raise ValueError("该单据已审核，不能再次审核")
        doc_type = str(bill.get("F_DJLX") or "")
        doc_id = int(bill["F_DJID"])
        if doc_type == DOC_IN:
            _review_inbound(cur, doc_id, passed)
        elif doc_type == DOC_OUT:
            _review_outbound(cur, doc_id, passed)
            if passed:
                push_outbound_id = doc_id
        else:
            raise ValueError(f"未知单据类型: {doc_type}")
        cur.execute(
            """
            UPDATE T_CRKSH
            SET F_SHZT = ?, F_SHR = ?, F_SHSJ = ?, F_SHBZ = ?
            WHERE F_ID = ? AND F_SHZT = ?
            """,
            (
                AUDIT_PASS if passed else AUDIT_REJECT,
                reviewer, now, remark, int(audit_id), AUDIT_PENDING,
            ),
        )
        if cur.rowcount == 0:
            raise ValueError("该单据已审核，不能再次审核")
        conn.commit()

    result = get_audit({"F_ID": int(audit_id)})
    if push_outbound_id:
        result["external_push"] = _push_outbound(push_outbound_id)
    _log("审核" + ("通过" if passed else "驳回"), user, {"F_ID": int(audit_id), "F_DJH": result.get("F_DJH")})
    return result


def tube_outbound_block_reason(cur, tube_id: int) -> str | None:
    """出库单还在待审核时，外部系统不能把这根管子确认出库。"""
    try:
        cur.execute("SELECT F_LOCK_CKD_ID FROM T_YBFZ WHERE F_ID = ?", (tube_id,))
        row = cur.fetchone()
    except Exception:
        return None
    locked = lock_id(row[0] if row else None)
    if not locked:
        return None
    cur.execute("SELECT F_CKDZT FROM T_CKD WHERE F_ID = ?", (locked,))
    order = cur.fetchone()
    if order and str(order[0] or "").strip() == AUDIT_PENDING:
        return "出库单待审核，不允许确认出库"
    return None


def clear_tube_outbound_lock(cur, tube_id: int) -> None:
    cur.execute("UPDATE T_YBFZ SET F_LOCK_CKD_ID = NULL WHERE F_ID = ?", (tube_id,))


def _review_inbound(cur, transport_id: int, passed: bool) -> None:
    cur.execute("SELECT F_ID, F_YSXX, F_YSDZT FROM T_YSXX WHERE F_ID = ?", (transport_id,))
    row = cur.fetchone()
    if not row:
        raise ValueError(f"未找到运输单ID: {transport_id}")
    if str(row[2] or "").strip() != AUDIT_PENDING:
        raise ValueError("运输单状态已变化，不能审核")
    tube_ids = _tube_ids(row[1])
    if passed:
        cur.execute(
            "UPDATE T_YSXX SET F_YSDZT = ? WHERE F_ID = ? AND F_YSDZT = ?",
            ("运送中", transport_id, AUDIT_PENDING),
        )
        _set_tube_transport(cur, tube_ids, "运送中")
        return
    cur.execute(
        "UPDATE T_YSXX SET F_YSDZT = ? WHERE F_ID = ? AND F_YSDZT = ?",
        (AUDIT_REJECT, transport_id, AUDIT_PENDING),
    )
    if not tube_ids:
        return
    placeholders = ",".join(["?"] * len(tube_ids))
    cur.execute(
        f"""
        UPDATE T_YBFZ
        SET F_RQ_ID = NULL, F_RQC = NULL, F_RQJ = NULL, F_RQCT = NULL, F_RQH = NULL,
            F_RQHX = NULL, F_RQHL = NULL, F_CCWZ = NULL, F_YSZT = ?
        WHERE F_ID IN ({placeholders}) AND F_YSZT = ?
        """,
        tuple(["未运送"] + tube_ids + [AUDIT_PENDING]),
    )


def _review_outbound(cur, outbound_id: int, passed: bool) -> None:
    cur.execute("SELECT F_CKDZT FROM T_CKD WHERE F_ID = ?", (outbound_id,))
    row = cur.fetchone()
    if not row:
        raise ValueError(f"未找到出库单ID: {outbound_id}")
    if str(row[0] or "").strip() != AUDIT_PENDING:
        raise ValueError("出库单状态已变化，不能审核")
    if passed:
        cur.execute(
            "UPDATE T_CKD SET F_CKDZT = ? WHERE F_ID = ? AND F_CKDZT = ?",
            (OUTBOUND_READY, outbound_id, AUDIT_PENDING),
        )
        return
    cur.execute(
        "UPDATE T_CKD SET F_CKDZT = ? WHERE F_ID = ? AND F_CKDZT = ?",
        (AUDIT_REJECT, outbound_id, AUDIT_PENDING),
    )
    cur.execute(
        """
        UPDATE T_YBFZ
        SET F_LOCK_CKD_ID = NULL,
            F_KCZT = CASE WHEN F_KCZT = ? THEN ? ELSE F_KCZT END
        WHERE F_LOCK_CKD_ID = ? AND (F_KCZT IS NULL OR F_KCZT <> ?)
        """,
        ("出库中", "在库", outbound_id, "已出库"),
    )


def _set_tube_transport(cur, tube_ids: List[int], status: str) -> None:
    if not tube_ids:
        return
    placeholders = ",".join(["?"] * len(tube_ids))
    cur.execute(
        f"UPDATE T_YBFZ SET F_YSZT = ? WHERE F_ID IN ({placeholders}) AND F_YSZT = ?",
        tuple([status] + tube_ids + [AUDIT_PENDING]),
    )


def _tube_ids(raw: Any) -> List[int]:
    try:
        data = json.loads(raw) if isinstance(raw, str) else raw
    except Exception:
        return []
    if not isinstance(data, list):
        return []
    ids: List[int] = []
    for item in data:
        try:
            ids.append(int(item))
        except (TypeError, ValueError):
            continue
    return ids


def _load_detail(raw: Any) -> Dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _push_outbound(outbound_id: int) -> Dict[str, Any]:
    try:
        from app.crud.outbound_mgmt import get_outbound_detail
        from app.core.outbound_push_client import push_outbound_order
        return push_outbound_order(get_outbound_detail({"F_ID": outbound_id}))
    except Exception as exc:
        return {"attempted": False, "success": False, "error": str(exc)}


def _log(op_type: str, user: Dict[str, Any] | None, detail: Dict[str, Any]) -> None:
    try:
        operator = None
        if user:
            operator = user.get("F_Ktype") or user.get("F_KTYPE") or user.get("F_YSM")
        insert_op_log(
            module="stock_audit",
            op_type=op_type,
            operator=operator,
            status="success",
            detail=detail,
        )
    except Exception:
        pass
