import json
from datetime import datetime
from typing import Any, Dict, List, Optional

from app.db.factory import get_adapter
from app.crud.sample_linkage import _resolve_operator, _parse_datetime
from app.crud.box_mgmt import export_wz_as_int
from app.crud.op_log import insert_op_log
from app.crud.file_center import get_files_by_ids


def _format_datetime(val: Any) -> str | None:
    if val is None:
        return None
    if isinstance(val, datetime):
        return val.strftime("%Y-%m-%d %H:%M:%S")
    try:
        dv = datetime.fromisoformat(str(val))
        return dv.strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return str(val)


# 出库单状态
CKDZT_AUDIT = "待审核"
CKDZT_REJECTED = "审核驳回"
CKDZT_PENDING = "待出库"
CKDZT_PARTIAL = "部分出库"
CKDZT_DONE = "已出库"

# 明细状态
MXZT_PENDING = "待出库"
MXZT_DONE = "已出库"


def _format_sample_for_export(sample: Dict[str, Any]) -> None:
    """出库样本展示：F_WZ 转 int（与运输模块保持一致的库位展示格式）。"""
    if "F_WZ" not in sample:
        return
    original = sample.get("F_WZ")
    converted = export_wz_as_int(
        original,
        sample.get("F_LS"),
        hzh=sample.get("F_HZH"),
    )
    if converted is not None:
        sample["F_WZ"] = converted


def _fetch_outbound_sample_details(ybfz_ids: List[int]) -> List[Dict[str, Any]]:
    """查询出库单关联样本的对外展示字段（发起/详情接口返回用）。"""
    if not ybfz_ids:
        return []
    from app.crud.direct_inbound_schema import ensure_schema
    ensure_schema()
    adapter = get_adapter()
    placeholders = ",".join(["?"] * len(ybfz_ids))
    sql = f"""
    SELECT
        f.F_ID,
        f.F_SGBH,
        f.F_ZZID,
        f.F_YBLX,
        f.F_WZ,
        f.F_HZH,
        f.F_HS,
        f.F_LS,
        f.F_KCZT,
        f.F_CKSJ,
        f.F_DC,
        f.F_LCZD,
        f.F_TJ,
        f.F_RKSJ,
        f.F_CCWZ,
        COALESCE(y.F_BBLX, p.F_BBLX) AS F_BBLX,
        COALESCE(y.F_AZ, p.F_AZ) AS F_AZ
    FROM T_YBFZ f
    LEFT JOIN T_YBZJ y ON y.F_ID = f.F_YBZJ_ID
    LEFT JOIN T_PYGD p ON p.F_ID = f.F_PYGD_ID
    WHERE f.F_ID IN ({placeholders})
    """
    rows, cols = adapter.fetch_all(sql, ybfz_ids)
    idx = {c: i for i, c in enumerate(cols)}
    tmp: Dict[int, Dict[str, Any]] = {}
    for row in rows:
        sample = {c: row[idx[c]] for c in cols}
        sample["F_CKSJ"] = _format_datetime(sample.get("F_CKSJ"))
        sample["F_RKSJ"] = _format_datetime(sample.get("F_RKSJ"))
        if not sample.get("F_CCWZ"):
            sample["F_CCWZ"] = ""
        _format_sample_for_export(sample)
        tmp[int(sample["F_ID"])] = sample
    return [tmp[fid] for fid in ybfz_ids if fid in tmp]


def _resolve_fj_info(fj_raw: Any) -> List[Dict[str, Any]]:
    """将 T_CKD.F_FJ（JSON文件ID数组）转为文件信息列表。"""
    if not fj_raw:
        return []
    try:
        ids = json.loads(fj_raw) if isinstance(fj_raw, str) else fj_raw
        if not isinstance(ids, list):
            return []
        int_ids = [int(x) for x in ids if str(x).strip()]
    except Exception:
        return []
    if not int_ids:
        return []
    try:
        files = get_files_by_ids(int_ids)
        files_map = {f["F_ID"]: f for f in files}
        result = []
        for fid in int_ids:
            info = files_map.get(fid)
            if info:
                result.append({
                    "id": info.get("F_ID"),
                    "path": info.get("F_PATH"),
                    "name": info.get("F_ORIGINAL_NAME"),
                })
            else:
                result.append({"id": fid})
        return result
    except Exception:
        return [{"id": i} for i in int_ids]


def _lock_value(value: Any) -> int | None:
    from app.crud.direct_inbound_schema import lock_id
    return lock_id(value)


def _tube_can_outbound(kczt: str, locked_to: int | None, source_ckd_id: Optional[int]) -> bool:
    """新单只收未锁定的在库管。再次申请还接受本单锁定的在库管，以及历史单上仍为出库中的管。"""
    if source_ckd_id:
        if kczt == "出库中" and locked_to in (None, int(source_ckd_id)):
            return True
        return kczt == "在库" and locked_to == int(source_ckd_id)
    return kczt == "在库" and locked_to is None


def create_outbound(
    payload: Dict[str, Any],
    user: Dict[str, Any] | None = None,
    source_ckd_id: Optional[int] = None,
) -> Dict[str, Any]:
    """发起出库单。管子保持在库，并用 F_LOCK_CKD_ID 锁到本单。

    出库审核开启时出库单为待审核，通过后才是待出库并推送外部系统。
    审核关闭时直接待出库。真正出库完成仍由外部确认写成已出库。
    """
    from app.crud.direct_inbound_schema import ensure_schema
    from app.crud.stock_audit import AUDIT_PENDING, DOC_OUT, insert_bill, outbound_needs_audit

    ensure_schema()
    ids_raw = payload.get("YBFZ") or []
    if isinstance(ids_raw, (int, str)):
        ids_raw = [ids_raw]
    ybfz_ids: List[int] = list(dict.fromkeys(int(x) for x in ids_raw if str(x).strip()))

    if not ybfz_ids:
        raise ValueError("请提供至少一个分装记录ID")

    adapter = get_adapter()
    placeholders = ",".join(["?"] * len(ybfz_ids))

    select_sql = (
        "SELECT F_ID, F_KCZT, F_LOCK_CKD_ID, F_HZH, F_SGBH, F_ZZID, F_CCWZ "
        f"FROM T_YBFZ WHERE F_ID IN ({placeholders})"
    )
    rows, cols = adapter.fetch_all(select_sql, ybfz_ids)
    found = {int(dict(zip(cols, row))["F_ID"]): dict(zip(cols, row)) for row in rows}
    missing = sorted(set(ybfz_ids) - set(found))
    if missing:
        raise ValueError(f"找不到以下分装记录ID: {missing}")

    blocked: List[int] = []
    for fid in ybfz_ids:
        tube = found[fid]
        kczt = str(tube.get("F_KCZT") or "").strip()
        if not _tube_can_outbound(kczt, _lock_value(tube.get("F_LOCK_CKD_ID")), source_ckd_id):
            blocked.append(fid)
    if blocked:
        if source_ckd_id:
            raise ValueError(f"以下样本状态已变更，无法重新申请出库: {blocked}")
        raise ValueError(f"以下样本不在库或已被其他出库单锁定，无法发起出库: {blocked}")

    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    ckdh = f"CKDH{datetime.now().strftime('%Y%m%d%H%M%S%f')}"
    sqr = payload.get("F_SQR") or _resolve_operator(user, adapter)
    sqsj = _parse_datetime(payload.get("F_SQSJ")) or now_str
    lxfx = payload.get("F_LXFX")
    bz = payload.get("F_BZ")
    need_audit = outbound_needs_audit()
    order_status = AUDIT_PENDING if need_audit else CKDZT_PENDING

    fj_ids_raw = payload.get("F_FJ_IDS") or []
    if isinstance(fj_ids_raw, (int, str)):
        fj_ids_raw = [fj_ids_raw]
    fj_ids = [int(x) for x in fj_ids_raw if str(x).strip()]
    fj_json = json.dumps(fj_ids, ensure_ascii=False) if fj_ids else None
    box_nos = []
    for fid in ybfz_ids:
        box_no = str(found[fid].get("F_HZH") or "").strip()
        if box_no and box_no not in box_nos:
            box_nos.append(box_no)

    with adapter.connect() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO T_CKD (
                F_CKDH, F_SQR, F_SQSJ, F_LXFX, F_BZ, F_FJ,
                F_YBSL, F_CKDZT, F_CJSJ, F_SOURCE_CKD_ID
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                ckdh, sqr, sqsj, lxfx, bz, fj_json,
                len(ybfz_ids), order_status, now_str, source_ckd_id,
            ),
        )
        ckd_id = int(adapter.last_insert_id(cur))

        for fid in ybfz_ids:
            cur.execute(
                """
                INSERT INTO T_CKD_MX (F_CKD_ID, F_YBFZ_ID, F_SGBH, F_MXZT, F_CKSJ)
                SELECT ?, F_ID, F_SGBH, ?, NULL FROM T_YBFZ WHERE F_ID = ?
                """,
                (ckd_id, MXZT_PENDING, fid),
            )

        update_sql = f"UPDATE T_YBFZ SET F_LOCK_CKD_ID = ? WHERE F_ID IN ({placeholders})"
        cur.execute(update_sql, tuple([ckd_id] + ybfz_ids))
        if need_audit:
            insert_bill(
                cur, adapter,
                doc_type=DOC_OUT,
                doc_id=ckd_id,
                doc_no=ckdh,
                box_no=",".join(box_nos),
                sample_count=len(ybfz_ids),
                applicant=sqr,
                applied_at=str(sqsj),
                detail={
                    "F_DJLX": DOC_OUT,
                    "F_CKDH": ckdh,
                    "F_SQR": sqr,
                    "F_SQSJ": _format_datetime(sqsj),
                    "F_LXFX": lxfx,
                    "F_BZ": bz,
                    "items": [
                        {
                            "F_ID": fid,
                            "F_SGBH": found[fid].get("F_SGBH"),
                            "F_ZZID": found[fid].get("F_ZZID"),
                            "F_HZH": found[fid].get("F_HZH"),
                            "F_CCWZ": found[fid].get("F_CCWZ") or "",
                        }
                        for fid in ybfz_ids
                    ],
                },
            )

        conn.commit()

    try:
        insert_op_log(
            module="outbound_mgmt",
            op_type="发起出库单",
            operator=sqr,
            status="success",
            detail={"F_CKDH": ckdh, "F_ID": ckd_id, "YBFZ": ybfz_ids, "F_LXFX": lxfx, "F_CKDZT": order_status},
        )
    except Exception:
        pass

    result = {
        "F_ID": ckd_id,
        "F_CKDH": ckdh,
        "F_SQR": sqr,
        "F_SQSJ": _format_datetime(sqsj),
        "F_LXFX": lxfx,
        "F_BZ": bz,
        "F_YBSL": len(ybfz_ids),
        "F_CKDZT": order_status,
        "F_SOURCE_CKD_ID": source_ckd_id,
        "items": _fetch_outbound_sample_details(ybfz_ids),
    }

    # 待审核先不推送。审核通过后再推。审核关闭时仍在创建成功后推送。
    if need_audit:
        result["external_push"] = {"attempted": False, "success": False, "skipped": "待审核"}
        return result
    try:
        from app.core.outbound_push_client import push_outbound_order
        result["external_push"] = push_outbound_order(result)
    except Exception as exc:
        result["external_push"] = {"attempted": False, "success": False, "error": str(exc)}

    return result


def list_outbound(payload: Dict[str, Any], user: Dict[str, Any] | None = None,
                 scope: str | None = None) -> Dict[str, Any]:
    adapter = get_adapter()
    skip = int(payload.get("skip") or 0)
    limit = int(payload.get("limit") or 50)

    filters: List[str] = []
    params: List[Any] = []

    ckdh_kw = (payload.get("F_CKDH") or "").strip()
    if ckdh_kw:
        pred, p = adapter.like("t.F_CKDH", ckdh_kw)
        filters.append(pred)
        params.extend(p)

    sqr_kw = (payload.get("F_SQR") or "").strip()
    if sqr_kw:
        pred, p = adapter.like("t.F_SQR", sqr_kw)
        filters.append(pred)
        params.extend(p)

    ckdzt = (payload.get("F_CKDZT") or "").strip()
    if ckdzt:
        filters.append("t.F_CKDZT = ?")
        params.append(ckdzt)

    sqsj_start = (payload.get("F_SQSJ_START") or "").strip()
    sqsj_end = (payload.get("F_SQSJ_END") or "").strip()
    if sqsj_start:
        filters.append("t.F_SQSJ >= ?")
        params.append(sqsj_start)
    if sqsj_end:
        filters.append("t.F_SQSJ <= ?")
        params.append(sqsj_end)

    sgbh_kw = (payload.get("F_SGBH") or "").strip()
    if sgbh_kw:
        pred, p = adapter.like("mx.F_SGBH", sgbh_kw)
        filters.append(
            f"EXISTS (SELECT 1 FROM T_CKD_MX mx WHERE mx.F_CKD_ID = t.F_ID AND {pred})"
        )
        params.extend(p)

    from app.core.data_scope import apply_owner_filter
    apply_owner_filter(filters, params, scope=scope, user=user, column="t.F_SQR", match="name")

    where_sql = (" WHERE " + " AND ".join(filters)) if filters else ""

    base_sql = f"""
    SELECT
        t.F_ID, t.F_CKDH, t.F_SQR, t.F_SQSJ, t.F_LXFX, t.F_BZ,
        t.F_YBSL, t.F_CKDZT, t.F_CJSJ, t.F_SOURCE_CKD_ID
    FROM T_CKD t
    {where_sql}
    """
    count_sql = f"SELECT COUNT(1) FROM T_CKD t {where_sql}"
    list_sql = base_sql + " ORDER BY t.F_ID DESC"
    list_sql, extra = adapter.paginate(list_sql, skip=skip, limit=limit)

    with adapter.connect() as conn:
        cur = conn.cursor()
        cur.execute(count_sql, tuple(params))
        total = int(cur.fetchone()[0])

        cur.execute(list_sql, tuple(params + extra))
        cols = [c[0] for c in cur.description]
        rows = cur.fetchall()

    items: List[Dict[str, Any]] = []
    for row in rows:
        record = dict(zip(cols, row))
        record["F_SQSJ"] = _format_datetime(record.get("F_SQSJ"))
        record["F_CJSJ"] = _format_datetime(record.get("F_CJSJ"))
        items.append(record)

    return {"items": items, "total": total}


def get_outbound_detail(payload: Dict[str, Any]) -> Dict[str, Any]:
    ckd_id = payload.get("F_ID")
    ckdh = (payload.get("F_CKDH") or "").strip()
    if not ckd_id and not ckdh:
        raise ValueError("F_ID 或 F_CKDH 至少提供一个")

    adapter = get_adapter()
    header_cols = "F_ID, F_CKDH, F_SQR, F_SQSJ, F_LXFX, F_BZ, F_FJ, F_YBSL, F_CKDZT, F_CJSJ, F_SOURCE_CKD_ID"
    if ckd_id:
        rows, cols = adapter.fetch_all(f"SELECT {header_cols} FROM T_CKD WHERE F_ID = ?", [ckd_id])
        if not rows:
            raise ValueError(f"未找到出库单ID: {ckd_id}")
    else:
        rows, cols = adapter.fetch_all(f"SELECT {header_cols} FROM T_CKD WHERE F_CKDH = ?", [ckdh])
        if not rows:
            raise ValueError(f"未找到出库单号: {ckdh}")

    idx = {c: i for i, c in enumerate(cols)}
    row = rows[0]
    header = {c: row[idx[c]] for c in cols}
    resolved_ckd_id = int(header["F_ID"])

    mx_rows, mx_cols = adapter.fetch_all(
        "SELECT F_YBFZ_ID, F_SGBH, F_MXZT, F_CKSJ FROM T_CKD_MX WHERE F_CKD_ID = ? ORDER BY F_ID",
        [resolved_ckd_id],
    )
    mx_idx = {c: i for i, c in enumerate(mx_cols)}
    ybfz_ids = [int(r[mx_idx["F_YBFZ_ID"]]) for r in mx_rows]
    mx_status_map = {
        int(r[mx_idx["F_YBFZ_ID"]]): {
            "F_MXZT": r[mx_idx["F_MXZT"]],
            "F_CKSJ": _format_datetime(r[mx_idx["F_CKSJ"]]),
        }
        for r in mx_rows
    }

    samples = _fetch_outbound_sample_details(ybfz_ids)
    for s in samples:
        mx = mx_status_map.get(int(s["F_ID"])) or {}
        s["F_MXZT"] = mx.get("F_MXZT")
        # 明细表记录的确认出库时间优先于样本表（历史更贴合本单）
        if mx.get("F_CKSJ"):
            s["F_CKSJ"] = mx.get("F_CKSJ")

    return {
        "F_ID": resolved_ckd_id,
        "F_CKDH": header.get("F_CKDH"),
        "F_SQR": header.get("F_SQR"),
        "F_SQSJ": _format_datetime(header.get("F_SQSJ")),
        "F_LXFX": header.get("F_LXFX"),
        "F_BZ": header.get("F_BZ"),
        "F_FJ": _resolve_fj_info(header.get("F_FJ")),
        "F_YBSL": header.get("F_YBSL"),
        "F_CKDZT": header.get("F_CKDZT"),
        "F_CJSJ": _format_datetime(header.get("F_CJSJ")),
        "F_SOURCE_CKD_ID": header.get("F_SOURCE_CKD_ID"),
        "items": samples,
    }


def resubmit_outbound(payload: Dict[str, Any], user: Dict[str, Any] | None = None) -> Dict[str, Any]:
    """针对"部分出库"的出库单，过滤已出库样本，为剩余样本重新发起一张出库单。"""
    ckd_id = payload.get("F_CKD_ID")
    if not ckd_id:
        raise ValueError("F_CKD_ID 不能为空")

    adapter = get_adapter()
    rows, cols = adapter.fetch_all(
        "SELECT F_ID, F_CKDH, F_SQR, F_LXFX, F_BZ, F_CKDZT FROM T_CKD WHERE F_ID = ?",
        [ckd_id],
    )
    if not rows:
        raise ValueError(f"未找到出库单ID: {ckd_id}")
    idx = {c: i for i, c in enumerate(cols)}
    header = rows[0]
    ckdzt = header[idx.get("F_CKDZT")]
    if str(ckdzt or "").strip() != CKDZT_PARTIAL:
        raise ValueError(f"仅'部分出库'的出库单可再次申请，当前状态: {ckdzt}")

    mx_rows, mx_cols = adapter.fetch_all(
        "SELECT F_YBFZ_ID, F_MXZT FROM T_CKD_MX WHERE F_CKD_ID = ?",
        [ckd_id],
    )
    mx_idx = {c: i for i, c in enumerate(mx_cols)}
    remaining_ids = [
        int(r[mx_idx["F_YBFZ_ID"]])
        for r in mx_rows
        if str(r[mx_idx["F_MXZT"]] or "").strip() != MXZT_DONE
    ]
    if not remaining_ids:
        raise ValueError("该出库单没有剩余未出库的样本")

    from app.crud.direct_inbound_schema import ensure_schema
    ensure_schema()
    placeholders = ",".join(["?"] * len(remaining_ids))
    kczt_rows, kczt_cols = adapter.fetch_all(
        f"SELECT F_ID, F_KCZT, F_LOCK_CKD_ID FROM T_YBFZ WHERE F_ID IN ({placeholders})",
        remaining_ids,
    )
    kczt_idx = {c: i for i, c in enumerate(kczt_cols)}
    invalid = [
        int(r[kczt_idx["F_ID"]])
        for r in kczt_rows
        if not _tube_can_outbound(
            str(r[kczt_idx["F_KCZT"]] or "").strip(),
            _lock_value(r[kczt_idx["F_LOCK_CKD_ID"]]),
            int(ckd_id),
        )
    ]
    if invalid:
        raise ValueError(f"以下样本状态已变更，无法重新申请出库: {invalid}")

    new_payload = {
        "YBFZ": remaining_ids,
        "F_SQR": payload.get("F_SQR") or header[idx.get("F_SQR")],
        "F_LXFX": payload.get("F_LXFX") or header[idx.get("F_LXFX")],
        "F_BZ": payload.get("F_BZ") or header[idx.get("F_BZ")],
    }
    return create_outbound(new_payload, user, source_ckd_id=int(ckd_id))


def list_pending_for_external(payload: Dict[str, Any] | None = None) -> Dict[str, Any]:
    """供外部系统拉取待出库/部分出库单据（纯文本字段，不含附件）。"""
    payload = payload or {}
    adapter = get_adapter()
    skip = int(payload.get("skip") or 0)
    limit = int(payload.get("limit") or 50)

    filters: List[str] = [f"t.F_CKDZT IN ('{CKDZT_PENDING}', '{CKDZT_PARTIAL}')"]
    params: List[Any] = []

    ckdh_kw = (payload.get("F_CKDH") or "").strip()
    if ckdh_kw:
        pred, p = adapter.like("t.F_CKDH", ckdh_kw)
        filters.append(pred)
        params.extend(p)

    where_sql = " WHERE " + " AND ".join(filters)

    base_sql = f"""
    SELECT t.F_ID, t.F_CKDH, t.F_SQR, t.F_SQSJ, t.F_LXFX, t.F_BZ, t.F_CKDZT
    FROM T_CKD t
    {where_sql}
    """
    count_sql = f"SELECT COUNT(1) FROM T_CKD t {where_sql}"
    list_sql = base_sql + " ORDER BY t.F_ID DESC"
    list_sql, extra = adapter.paginate(list_sql, skip=skip, limit=limit)

    with adapter.connect() as conn:
        cur = conn.cursor()
        cur.execute(count_sql, tuple(params))
        total = int(cur.fetchone()[0])

        cur.execute(list_sql, tuple(params + extra))
        cols = [c[0] for c in cur.description]
        rows = cur.fetchall()

    items: List[Dict[str, Any]] = []
    for row in rows:
        record = dict(zip(cols, row))
        ckd_id = int(record["F_ID"])
        mx_rows, mx_cols = adapter.fetch_all(
            "SELECT F_SGBH FROM T_CKD_MX WHERE F_CKD_ID = ? AND F_MXZT != ?",
            [ckd_id, MXZT_DONE],
        )
        sgbh_list = [r[0] for r in mx_rows if r[0]]
        items.append({
            "F_ID": ckd_id,
            "F_CKDH": record.get("F_CKDH"),
            "F_SQR": record.get("F_SQR"),
            "F_SQSJ": _format_datetime(record.get("F_SQSJ")),
            "F_LXFX": record.get("F_LXFX"),
            "F_BZ": record.get("F_BZ"),
            "F_CKDZT": record.get("F_CKDZT"),
            "F_YBQD": ",".join(sgbh_list),  # 样本清单文本（逗号分隔样本管编号）
            "F_SGBH_LIST": sgbh_list,
        })

    return {"items": items, "total": total}
