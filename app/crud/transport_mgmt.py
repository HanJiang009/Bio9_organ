from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from app.db.factory import get_adapter
from app.crud.sample_linkage import _resolve_operator, _parse_datetime
from app.crud.box_mgmt import export_wz_as_int, get_box_by_hzh


def _format_datetime(val: Any) -> str | None:
    """格式化日期时间为字符串"""
    if val is None:
        return None
    if isinstance(val, datetime):
        return val.strftime("%Y-%m-%d %H:%M:%S")
    try:
        dv = datetime.fromisoformat(str(val))
        return dv.strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return str(val)


def _unique_hzh_from_items(items: List[Dict[str, Any]]) -> List[str]:
    """按 items 出现顺序去重盒号。"""
    hzhs: List[str] = []
    seen: set[str] = set()
    for it in items:
        hzh = str(it.get("F_HZH") or "").strip()
        if hzh and hzh not in seen:
            seen.add(hzh)
            hzhs.append(hzh)
    return hzhs


def _box_spec_text(hzh: str, items: List[Dict[str, Any]]) -> Optional[str]:
    """
    盒子规格：优先盒子模板名称，其次 行×列。
    模板查不到时回退该盒号下样本管上的 F_HS/F_LS。
    """
    box = get_box_by_hzh(hzh) if hzh else None
    if box:
        name = str(box.get("F_TPLT_NAME") or "").strip()
        if name:
            return name
        hs, ls = box.get("F_TPLT_HS"), box.get("F_TPLT_LS")
        if hs and ls:
            return f"{hs}×{ls}"
    for it in items:
        if str(it.get("F_HZH") or "").strip() != hzh:
            continue
        hs, ls = it.get("F_HS"), it.get("F_LS")
        if hs and ls:
            return f"{hs}×{ls}"
        break
    return None


def _box_header_from_items(items: List[Dict[str, Any]]) -> Tuple[Optional[str], Optional[str]]:
    """运输单 data 层：盒号、盒子规格（多盒时逗号拼接，顺序与样本一致）。"""
    hzhs = _unique_hzh_from_items(items)
    if not hzhs:
        return None, None
    specs: List[str] = []
    spec_seen: set[str] = set()
    for hzh in hzhs:
        spec = _box_spec_text(hzh, items)
        if spec and spec not in spec_seen:
            spec_seen.add(spec)
            specs.append(spec)
    return ",".join(hzhs), (",".join(specs) if specs else None)


# 对外运输样本 payload：样本来源标识（不落库）
EXTERNAL_SAMPLE_HHID = "类器官系统样本"

# 外部状态同步事件
EXTERNAL_STATUS_EVENTS = frozenset({"received", "stored", "outbound_done"})

# 生物样本库系统实际推送报文中 data.task 取值 → 内部事件的映射
# 报文示例：{"code":0,"msg":"...","pushTime":"...","data":{"task":"样本入库"/"样本出库","ybmx":[...]}}
EXTERNAL_PUSH_TASK_EVENT_MAP = {
    "样本入库": "stored",
    "样本出库": "outbound_done",
}


def adapt_external_push(payload: Dict[str, Any]) -> Dict[str, Any]:
    """将生物样本库系统实际推送报文转换为内部 sync_external_sample_status 所需的
    {F_EVENT, ITEMS:[...]} 结构。

    入参报文格式（由外部系统实际提供）：
    {
        "code": 0, "msg": "查询成功", "pushTime": "2026-07-07 15:31:10.135",
        "data": {
            "task": "样本入库" | "样本出库",
            "ybmx": [
                # 样本入库：
                {"f_sgbh": "...", "f_rksj": "...", "f_rky": "...", "f_rkzt": "已入库"},
                # 样本出库：
                {"f_sgbh": "...", "f_cksj": "...", "f_cky": "...", "f_sybz": "全部用完",
                 "f_ckdh": "CKD_260702001", "f_ckzt": "已出库"},
            ],
        },
    }
    """
    data = payload.get("data")
    if not isinstance(data, dict):
        raise ValueError("请提供 data 字段（{task, ybmx}）")

    task = (data.get("task") or "").strip()
    event = EXTERNAL_PUSH_TASK_EVENT_MAP.get(task)
    if not event:
        raise ValueError(
            f"未知的 task: '{task}'，目前仅支持: {list(EXTERNAL_PUSH_TASK_EVENT_MAP.keys())}"
        )

    raw_items = data.get("ybmx")
    if not isinstance(raw_items, list) or not raw_items:
        raise ValueError("data.ybmx 不能为空")

    items: List[Dict[str, Any]] = []
    for raw in raw_items:
        if not isinstance(raw, dict):
            raise ValueError("data.ybmx 元素必须为对象")
        sgbh = (raw.get("f_sgbh") or "").strip()
        if not sgbh:
            raise ValueError("data.ybmx 中 f_sgbh 不能为空")

        if event == "stored":
            items.append({
                "F_SGBH": sgbh,
                "F_SJ": raw.get("f_rksj"),
                "F_CZR": raw.get("f_rky"),
            })
        else:  # outbound_done
            items.append({
                "F_SGBH": sgbh,
                "F_SJ": raw.get("f_cksj"),
                "F_CZR": raw.get("f_cky"),
                "F_SYBZ": raw.get("f_sybz"),
                "F_EXT_CKDH": raw.get("f_ckdh"),
            })

    return {"F_EVENT": event, "ITEMS": items}


def _normalize_sgbh_list(raw: Any) -> List[str]:
    if raw is None:
        return []
    if isinstance(raw, str):
        text = raw.strip()
        return [text] if text else []
    if isinstance(raw, list):
        return [str(x).strip() for x in raw if str(x).strip()]
    raise ValueError("F_SGBH 必须是字符串或字符串数组")


def _is_unstocked(kczt: Any) -> bool:
    text = (str(kczt).strip() if kczt is not None else "")
    return text in ("", "未入库")


def _item_event_time(item: Dict[str, Any]) -> str:
    parsed = _parse_datetime(item.get("F_SJ"))
    if parsed:
        return parsed
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _item_operator(item: Dict[str, Any], user: Dict[str, Any] | None, adapter) -> str:
    czr = (item.get("F_CZR") or "").strip()
    if czr:
        return czr
    return _resolve_operator(user, adapter)


def _normalize_sync_items(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    """解析 ITEMS；兼容旧版顶层 F_SGBH 列表（内部 receive_transport 使用）。"""
    raw_items = payload.get("ITEMS") or payload.get("items")
    if raw_items:
        if isinstance(raw_items, dict):
            raw_items = [raw_items]
        items: List[Dict[str, Any]] = []
        for raw in raw_items:
            if not isinstance(raw, dict):
                raise ValueError("ITEMS 元素必须为对象")
            sgbh = (raw.get("F_SGBH") or "").strip()
            if not sgbh:
                raise ValueError("ITEMS 中 F_SGBH 不能为空")
            items.append(dict(raw))
        return items

    default_sj = payload.get("F_SJ")
    default_czr = payload.get("F_CZR")
    default_bz = payload.get("F_BZ")
    sgbh_list = _normalize_sgbh_list(payload.get("F_SGBH"))
    if not sgbh_list:
        if (payload.get("F_YSDH") or "").strip() and payload.get("F_EVENT") == "received":
            return []
        raise ValueError("请提供 ITEMS 或 F_SGBH")
    return [
        {
            "F_SGBH": sgbh,
            "F_SJ": default_sj,
            "F_CZR": default_czr,
            "F_BZ": default_bz,
        }
        for sgbh in sgbh_list
    ]


def _fetch_ybfz_by_sgbh_map(adapter, sgbh_list: List[str]) -> Dict[str, Dict[str, Any]]:
    if not sgbh_list:
        return {}
    placeholders = ",".join(["?"] * len(sgbh_list))
    rows, cols = adapter.fetch_all(
        f"""
        SELECT F_ID, F_SGBH, F_YSZT, F_KCZT, F_PYGD_ID, F_ZZID
        FROM T_YBFZ
        WHERE F_SGBH IN ({placeholders})
        """,
        sgbh_list,
    )
    idx = {c: i for i, c in enumerate(cols)}
    result: Dict[str, Dict[str, Any]] = {}
    for row in rows:
        record = {
            "F_ID": int(row[idx["F_ID"]]),
            "F_SGBH": row[idx["F_SGBH"]],
            "F_YSZT": row[idx.get("F_YSZT")] if "F_YSZT" in idx else None,
            "F_KCZT": row[idx.get("F_KCZT")] if "F_KCZT" in idx else None,
            "F_PYGD_ID": row[idx.get("F_PYGD_ID")] if "F_PYGD_ID" in idx else None,
            "F_ZZID": row[idx.get("F_ZZID")] if "F_ZZID" in idx else None,
        }
        result[str(record["F_SGBH"])] = record
    return result


def _apply_stored_update(cur, fid: int, yszt: str, event_time: str) -> None:
    """入库：方案 A，运送中时隐式完成运输接收。"""
    if yszt == "运送中":
        cur.execute(
            """
            UPDATE T_YBFZ
            SET F_YSZT = ?, F_JSSJ = ?, F_KCZT = ?, F_RKSJ = ?
            WHERE F_ID = ?
            """,
            ("已接收", event_time, "在库", event_time, fid),
        )
    else:
        cur.execute(
            """
            UPDATE T_YBFZ
            SET F_KCZT = ?, F_RKSJ = ?
            WHERE F_ID = ?
            """,
            ("在库", event_time, fid),
        )


def _update_ckd_after_outbound(
    cur,
    fid: int,
    event_time: str,
    *,
    adapter=None,
    sybz: str | None = None,
    ext_ckdh: str | None = None,
) -> None:
    """样本确认出库后，联动更新其所属出库单明细(T_CKD_MX)及出库单(T_CKD)的状态。

    若该样本并非由 outbound_mgmt 模块的出库单流程流转出库（如历史/直连场景），
    找不到对应明细行时静默跳过，不影响主流程。

    sybz/ext_ckdh：外部系统（生物样本库）出库回执中的"使用备注"与"外部出库单号"，
    best-effort 写入 T_CKD_MX 对应扩展列（列不存在的旧库环境自动跳过，不影响主流程）。
    """
    cur.execute(
        "SELECT F_ID, F_CKD_ID FROM T_CKD_MX WHERE F_YBFZ_ID = ? AND F_MXZT != ?",
        (fid, "已出库"),
    )
    row = cur.fetchone()
    if not row:
        return
    mx_id, ckd_id = row[0], row[1]

    cur.execute(
        "UPDATE T_CKD_MX SET F_MXZT = ?, F_CKSJ = ? WHERE F_ID = ?",
        ("已出库", event_time, mx_id),
    )

    if (sybz or ext_ckdh) and adapter is not None:
        try:
            if adapter.column_exists(cur, "T_CKD_MX", "F_SYBZ") and adapter.column_exists(cur, "T_CKD_MX", "F_EXT_CKDH"):
                cur.execute(
                    "UPDATE T_CKD_MX SET F_SYBZ = ?, F_EXT_CKDH = ? WHERE F_ID = ?",
                    (sybz, ext_ckdh, mx_id),
                )
        except Exception:
            pass

    cur.execute(
        "SELECT COUNT(1), SUM(CASE WHEN F_MXZT = ? THEN 1 ELSE 0 END) FROM T_CKD_MX WHERE F_CKD_ID = ?",
        ("已出库", ckd_id),
    )
    total, done = cur.fetchone()
    total = int(total or 0)
    done = int(done or 0)
    if total > 0 and done >= total:
        new_status = "已出库"
    elif done > 0:
        new_status = "部分出库"
    else:
        new_status = "待出库"
    cur.execute("UPDATE T_CKD SET F_CKDZT = ? WHERE F_ID = ?", (new_status, ckd_id))


def _try_create_thaw_order_for_outbound(record: Dict[str, Any], item: Dict[str, Any],
                                         user: Dict[str, Any] | None, adapter) -> Dict[str, Any] | None:
    """样本确认出库后，best-effort 自动创建复苏培养工单。

    不抛出异常：调用方仅将结果（成功信息或错误原因）附加到返回体，不影响出库状态同步主流程。

    - 样本管关联了培养工单（T_YBFZ.F_PYGD_ID 不为空，"-GD-" 类样本管）：
      复苏自该工单，样本来源按其代次是否为 P0 判定"原代复苏"/"传代复苏"
    - 样本管未关联任何培养工单（T_YBFZ.F_PYGD_ID 为空，如从原始标本 T_YBZJ 直接分装、
      未走过培养流程的 "-BB-" 类样本管）：没有原工单可复苏，直接以标本登记（T_YBZJ）
      信息新建一张 P0 代次、样本来源"原代复苏"的工单（视为该标本的第一个培养工单）
    - 两种情况均不继承任何流水线配置（复苏工单字段内容有差异，不适合直接继承），
      本次自动触发场景不显式指定流水线，故新建工单流水线留空
    """
    pygd_id = record.get("F_PYGD_ID")
    zzid = record.get("F_ZZID")
    creator_code = _item_operator(item, user, adapter)
    try:
        if pygd_id:
            from app.crud.organoid_culture_tissue import create_thaw_order_from_order
            result = create_thaw_order_from_order(
                orig_gdid=int(pygd_id),
                creator_code=creator_code,
            )
            return {"success": True, **(result or {})}
        if zzid:
            from app.crud.organoid_culture_tissue import create_thaw_order_from_ybzj
            result = create_thaw_order_from_ybzj(
                zzid=str(zzid),
                creator_code=creator_code,
            )
            return {"success": True, **(result or {})}
        return {
            "success": False,
            "skipped": True,
            "reason": "该样本管既未关联培养工单也没有标本号（F_PYGD_ID/F_ZZID均为空），无法自动创建复苏工单",
        }
    except Exception as exc:
        return {"success": False, "error": str(exc)}


def _sync_one_external_item(
    cur,
    *,
    event: str,
    item: Dict[str, Any],
    record: Dict[str, Any] | None,
    user: Dict[str, Any] | None,
    adapter,
) -> tuple[str, str | None, Dict[str, Any] | None]:
    """
    处理单根样本管，返回 (result_type, reason, extra)。
    result_type: success | skipped | failed
    extra: 附加信息（目前仅 outbound_done 成功时携带 thaw_order 复苏工单创建结果）
    """
    sgbh = (item.get("F_SGBH") or "").strip()
    if not record:
        return "failed", "未找到样本编号", None

    fid = record["F_ID"]
    yszt = (str(record.get("F_YSZT")).strip() if record.get("F_YSZT") is not None else "")
    kczt = (str(record.get("F_KCZT")).strip() if record.get("F_KCZT") is not None else "")
    event_time = _item_event_time(item)

    if event == "received":
        if yszt == "已接收":
            return "skipped", "已接收", None
        if yszt != "运送中":
            return "failed", f"当前运输状态={yszt or '未运送'}，不允许接收", None
        cur.execute(
            "UPDATE T_YBFZ SET F_YSZT = ?, F_JSSJ = ? WHERE F_ID = ?",
            ("已接收", event_time, fid),
        )
        return "success", None, None

    if event == "stored":
        if kczt == "在库":
            return "skipped", "已在库", None
        if yszt in ("运送中", "已接收") and _is_unstocked(kczt):
            _apply_stored_update(cur, fid, yszt, event_time)
            return "success", None, None
        if yszt not in ("运送中", "已接收"):
            return "failed", f"当前运输状态={yszt or '未运送'}，不允许入库", None
        return "failed", f"当前库存状态={kczt or '未入库'}，不允许入库", None

    # outbound_done：允许"在库"（未经出库单流程直接出库）或"出库中"（已发起出库单，等待外部确认）
    if kczt == "已出库":
        return "skipped", "已出库", None
    if kczt not in ("在库", "出库中"):
        return "failed", f"当前库存状态={kczt or '未入库'}，不允许出库", None
    from app.crud.direct_inbound_schema import ensure_schema
    from app.crud.stock_audit import clear_tube_outbound_lock, tube_outbound_block_reason
    ensure_schema()
    blocked = tube_outbound_block_reason(cur, fid)
    if blocked:
        return "failed", blocked, None
    cur.execute(
        "UPDATE T_YBFZ SET F_KCZT = ?, F_CKSJ = ? WHERE F_ID = ?",
        ("已出库", event_time, fid),
    )
    clear_tube_outbound_lock(cur, fid)
    _update_ckd_after_outbound(
        cur, fid, event_time,
        adapter=adapter,
        sybz=item.get("F_SYBZ"),
        ext_ckdh=item.get("F_EXT_CKDH"),
    )
    thaw_result = _try_create_thaw_order_for_outbound(record, item, user, adapter)
    extra = {"thaw_order": thaw_result} if thaw_result is not None else None
    return "success", None, extra


def _format_sample_for_external_export(sample: Dict[str, Any]) -> None:
    """对外运输接口：注入 F_HHID，并将 F_WZ 转为 int。"""
    sample["F_HHID"] = EXTERNAL_SAMPLE_HHID
    if "F_WZ" not in sample:
        return
    original = sample.get("F_WZ")
    converted = export_wz_as_int(
        original,
        sample.get("F_LS"),
        hzh=sample.get("F_HZH"),
    )
    # 转换失败时保留库中原始值（如 A1），避免误显示为 null
    if converted is not None:
        sample["F_WZ"] = converted


def create_transport(payload: Dict[str, Any], user: Dict[str, Any] | None = None) -> Dict[str, Any]:
    ids_raw = payload.get("YBFZ") or []
    if isinstance(ids_raw, (int, str)):
        ids_raw = [ids_raw]
    ybfz_ids: List[int] = [int(x) for x in ids_raw if str(x).strip()]

    if not ybfz_ids:
        raise ValueError("请提供至少一个分装记录ID")

    adapter = get_adapter()
    placeholders = ",".join(["?"] * len(ybfz_ids))

    # 校验分装记录存在且未处于运输中
    select_sql = f"SELECT F_ID, F_YSZT FROM T_YBFZ WHERE F_ID IN ({placeholders})"
    rows, cols = adapter.fetch_all(select_sql, ybfz_ids)
    found_ids = {int(r[0]) for r in rows}
    missing = sorted(set(ybfz_ids) - found_ids)
    if missing:
        raise ValueError(f"找不到以下分装记录ID: {missing}")

    status_idx = cols.index("F_YSZT") if "F_YSZT" in cols else None
    blocked: List[int] = []
    if status_idx is not None:
        for r in rows:
            yszt = r[status_idx]
            if yszt and str(yszt) not in ("未运送", ""):
                blocked.append(int(r[0]))
    if blocked:
        raise ValueError(f"以下分装记录已存在运输状态，无法重复发起: {blocked}")

    # 组装运输单信息
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    ysdh = f"YSDH{datetime.now().strftime('%Y%m%d%H%M%S%f')}"
    ysr = payload.get("F_FQR") or _resolve_operator(user, adapter)
    yssj = _parse_datetime(payload.get("F_FQSJ")) or now_str
    ysbz = payload.get("F_BZ")
    ysmdd = payload.get("F_YSMDD")  # 运输目的地
    ysdzt = "运送中"

    import json

    ysxx_json = json.dumps(ybfz_ids, ensure_ascii=False)

    with adapter.connect() as conn:
        cur = conn.cursor()
        # 插入运输单
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
                len(ybfz_ids),
                ysbz,
                None,
                None,
                None,
                ysmdd,
            ),
        )

        # 更新分装记录运输状态和运输目的地
        update_sql = f"UPDATE T_YBFZ SET F_YSZT = ?, F_YSMDD = ? WHERE F_ID IN ({placeholders})"
        cur.execute(update_sql, tuple([ysdzt, ysmdd] + ybfz_ids))

        conn.commit()

    # 运输发起后自动更新盒子状态（未运送→运送中/已转运）
    from app.crud.box_mgmt import try_update_box_status_after_transport
    try_update_box_status_after_transport(ybfz_ids)

    return {
        "F_YSDH": ysdh,
        "F_YSXX": ybfz_ids,
        "F_YSDZT": ysdzt,
        "F_YBSL": len(ybfz_ids),
    }


def list_transport(payload: Dict[str, Any], user: Dict[str, Any] | None = None,
                  scope: str | None = None) -> Dict[str, Any]:
    adapter = get_adapter()
    skip = int(payload.get("skip") or 0)
    limit = int(payload.get("limit") or 50)
    ysdh_kw = (payload.get("F_YSDH") or "").strip()
    ysdzt = (payload.get("F_YSDZT") or "").strip()
    fqsj_start = (payload.get("F_FQSJ_START") or "").strip()
    fqsj_end = (payload.get("F_FQSJ_END") or "").strip()
    sgbh_kw = (payload.get("F_SGBH") or "").strip()

    where: List[str] = []
    params: List[Any] = []

    if ysdh_kw:
        pred, p = adapter.like("t.F_YSDH", ysdh_kw)
        where.append(pred)
        params.extend(p)

    if ysdzt:
        where.append("t.F_YSDZT = ?")
        params.append(ysdzt)

    if fqsj_start:
        where.append("t.F_FQSJ >= ?")
        params.append(fqsj_start)
    if fqsj_end:
        where.append("t.F_FQSJ <= ?")
        params.append(fqsj_end)

    from app.core.data_scope import apply_owner_filter
    apply_owner_filter(where, params, scope=scope, user=user, column="t.F_FQR", match="name")

    where_sql = f" WHERE {' AND '.join(where)}" if where else ""

    base_sql = f"""
    SELECT
        t.F_ID,
        t.F_YSDH,
        t.F_YSXX,
        t.F_FQR,
        t.F_FQSJ,
        t.F_YSDZT,
        t.F_YBSL,
        t.F_BZ,
        t.F_JSR,
        t.F_JSSJ,
        t.F_JSBZ,
        t.F_YSMDD
    FROM T_YSXX t
    {where_sql}
    ORDER BY t.F_ID DESC
    """

    items_raw: List[Dict[str, Any]] = []

    with adapter.connect() as conn:
        cur = conn.cursor()
        # 不分页先取出，后续按 SGBH 过滤再分页
        cur.execute(base_sql, tuple(params))
        cols = [c[0] for c in cur.description]
        for row in cur.fetchall():
            data = dict(zip(cols, row))
            for key in ("F_FQSJ", "F_JSSJ"):
                if data.get(key):
                    data[key] = _parse_datetime(data[key])
            items_raw.append(data)

    # 若需按样本编号过滤，基于 F_YSXX 中 ID 去查 T_YBFZ 的 F_SGBH
    filtered: List[Dict[str, Any]] = []
    if sgbh_kw:
        import json
        all_ids: List[int] = []
        for it in items_raw:
            try:
                ids = json.loads(it.get("F_YSXX") or "[]")
                if isinstance(ids, list):
                    all_ids.extend(int(x) for x in ids if str(x).strip())
            except Exception:
                continue
        all_ids = list({i for i in all_ids})

        sgbh_map: Dict[int, str] = {}
        if all_ids:
            placeholders = ",".join(["?"] * len(all_ids))
            sql = f"SELECT F_ID, F_SGBH FROM T_YBFZ WHERE F_ID IN ({placeholders})"
            rows, cols2 = adapter.fetch_all(sql, all_ids)
            idx_id = cols2.index("F_ID")
            idx_sgbh = cols2.index("F_SGBH")
            for r in rows:
                sgbh_map[int(r[idx_id])] = str(r[idx_sgbh]) if r[idx_sgbh] is not None else ""

        sgbh_kw_lower = sgbh_kw.lower()
        for it in items_raw:
            try:
                ids = json.loads(it.get("F_YSXX") or "[]")
                if not isinstance(ids, list):
                    continue
                matched = False
                for _id in ids:
                    sid = int(_id)
                    val = sgbh_map.get(sid, "")
                    if val and sgbh_kw_lower in val.lower():
                        matched = True
                        break
                if matched:
                    filtered.append(it)
            except Exception:
                continue
    else:
        filtered = items_raw

    total = len(filtered)
    paged = filtered[skip: skip + limit] if skip < total else []

    # 按运输单样本分装 ID 聚合盒号（T_YBFZ.F_HZH）
    import json as _json
    all_fz_ids: List[int] = []
    id_lists: List[List[int]] = []
    for it in paged:
        ids: List[int] = []
        try:
            raw = _json.loads(it.get("F_YSXX") or "[]")
            if isinstance(raw, list):
                ids = [int(x) for x in raw if str(x).strip()]
        except Exception:
            ids = []
        id_lists.append(ids)
        all_fz_ids.extend(ids)

    hzh_map: Dict[int, str] = {}
    uniq_ids = list({i for i in all_fz_ids})
    if uniq_ids:
        placeholders = ",".join(["?"] * len(uniq_ids))
        rows, cols2 = adapter.fetch_all(
            f"SELECT F_ID, F_HZH FROM T_YBFZ WHERE F_ID IN ({placeholders})",
            uniq_ids,
        )
        idx_id = cols2.index("F_ID")
        idx_hzh = cols2.index("F_HZH")
        for r in rows:
            hzh = r[idx_hzh]
            if hzh is not None and str(hzh).strip():
                hzh_map[int(r[idx_id])] = str(hzh).strip()

    for it, ids in zip(paged, id_lists):
        boxes: List[str] = []
        seen = set()
        for fid in ids:
            hzh = hzh_map.get(int(fid))
            if hzh and hzh not in seen:
                seen.add(hzh)
                boxes.append(hzh)
        it["F_HZH"] = ",".join(boxes) if boxes else None
        it["F_HZH_LIST"] = boxes

    return {"items": paged, "total": total}


def get_transport_detail(payload: Dict[str, Any]) -> Dict[str, Any]:
    ysid = payload.get("F_ID")
    ysdh = (payload.get("F_YSDH") or "").strip()
    if not ysid and not ysdh:
        raise ValueError("F_ID 或 F_YSDH 至少提供一个")

    adapter = get_adapter()
    if ysid:
        header_sql = """
        SELECT
            F_ID, F_YSDH, F_YSXX, F_FQR, F_FQSJ, F_YSDZT, F_BZ, F_YSMDD
        FROM T_YSXX
        WHERE F_ID = ?
        """
        rows, cols = adapter.fetch_all(header_sql, [ysid])
        if not rows:
            raise ValueError(f"未找到运输单ID: {ysid}")
    else:
        header_sql = """
        SELECT
            F_ID, F_YSDH, F_YSXX, F_FQR, F_FQSJ, F_YSDZT, F_BZ, F_YSMDD
        FROM T_YSXX
        WHERE F_YSDH = ?
        """
        rows, cols = adapter.fetch_all(header_sql, [ysdh])
        if not rows:
            raise ValueError(f"未找到运输单号: {ysdh}")
    idx = {c: i for i, c in enumerate(cols)}
    header_row = rows[0]
    ysxx_raw = header_row[idx["F_YSXX"]]
    import json
    try:
        ybfz_ids = json.loads(ysxx_raw or "[]")
        if not isinstance(ybfz_ids, list):
            ybfz_ids = []
        ybfz_ids = [int(x) for x in ybfz_ids if str(x).strip()]
    except Exception:
        ybfz_ids = []

    items: List[Dict[str, Any]] = []
    if ybfz_ids:
        placeholders = ",".join(["?"] * len(ybfz_ids))
        # 查询样本所有字段（参考 list_transporting_samples 实现）
        sql_ybfz = f"""
        SELECT
            f.*,
            COALESCE(y.F_BBLX, p.F_BBLX) AS F_BBLX,
            p.F_GDBH,
            p.F_DC AS F_DC,
            p.F_SQR AS F_PYR
        FROM T_YBFZ f
        LEFT JOIN T_YBZJ y ON y.F_ID = f.F_YBZJ_ID
        LEFT JOIN T_PYGD p ON p.F_ID = f.F_PYGD_ID
        WHERE f.F_ID IN ({placeholders})
        """
        
        with adapter.connect() as conn:
            cur = conn.cursor()
            try:
                cur.execute(sql_ybfz, tuple(ybfz_ids))
            except Exception:
                # 如果 F_SQR 不存在，重试不带该字段的查询
                sql_ybfz = f"""
                SELECT
                    f.*,
                    COALESCE(y.F_BBLX, p.F_BBLX) AS F_BBLX,
                    p.F_GDBH,
                    p.F_DC AS F_DC,
                    NULL AS F_PYR
                FROM T_YBFZ f
                LEFT JOIN T_YBZJ y ON y.F_ID = f.F_YBZJ_ID
                LEFT JOIN T_PYGD p ON p.F_ID = f.F_PYGD_ID
                WHERE f.F_ID IN ({placeholders})
                """
                cur.execute(sql_ybfz, tuple(ybfz_ids))
            
            cols_f = [c[0] for c in cur.description]
            rows_f = cur.fetchall()
        
        # 构建样本字典，保持与 F_YSXX 顺序一致
        tmp_items: Dict[int, Dict[str, Any]] = {}
        for row in rows_f:
            sample = dict(zip(cols_f, row))
            sample_id = sample["F_ID"]
            
            # 格式化时间字段
            sample["F_CJSJ"] = _format_datetime(sample.get("F_CJSJ"))
            for time_field in ["F_XGSJ", "F_JYSJ", "F_RKSJ", "F_CKSJ", "F_JSSJ"]:
                if time_field in sample and sample.get(time_field):
                    sample[time_field] = _format_datetime(sample[time_field])
            
            _format_sample_for_external_export(sample)
            tmp_items[sample_id] = sample

        # 保持与 F_YSXX 顺序一致
        for fid in ybfz_ids:
            if fid in tmp_items:
                items.append(tmp_items[fid])

    f_hzh, f_hzgg = _box_header_from_items(items)

    return {
        "F_ID": header_row[idx.get("F_ID")] if "F_ID" in idx else None,
        "F_YSDH": header_row[idx["F_YSDH"]],
        "F_YSDZT": header_row[idx.get("F_YSDZT")],
        "F_FQR": header_row[idx.get("F_FQR")],
        "F_FQSJ": _parse_datetime(header_row[idx.get("F_FQSJ")]) if header_row[idx.get("F_FQSJ")] else None,
        "F_BZ": header_row[idx.get("F_BZ")] if "F_BZ" in idx else None,
        "F_YSMDD": header_row[idx.get("F_YSMDD")] if "F_YSMDD" in idx else None,
        "F_HZH": f_hzh,
        "F_HZGG": f_hzgg,
        "items": items,
    }


def sync_external_sample_status(payload: Dict[str, Any], user: Dict[str, Any] | None = None) -> Dict[str, Any]:
    """
    外部系统样本状态同步（逐管处理，部分成功/失败）。

    F_EVENT:
    - stored        → F_KCZT=在库, F_RKSJ；运送中时隐式写 F_YSZT=已接收, F_JSSJ（方案 A）
    - outbound_done → F_KCZT=已出库, F_CKSJ
    - received      → 兼容保留，F_YSZT=已接收, F_JSSJ
    """
    import json

    event = (payload.get("F_EVENT") or "").strip()
    if event not in EXTERNAL_STATUS_EVENTS:
        raise ValueError("F_EVENT 必须为 received、stored 或 outbound_done")

    items = _normalize_sync_items(payload)
    seen: set[str] = set()
    for item in items:
        sgbh = (item.get("F_SGBH") or "").strip()
        if sgbh in seen:
            raise ValueError(f"ITEMS 中 F_SGBH 重复: {sgbh}")
        seen.add(sgbh)

    adapter = get_adapter()

    # 兼容旧版 received + F_YSDH：展开运输单内样本
    ysdh = (payload.get("F_YSDH") or "").strip()
    ysid = None
    ysdzt_old = None
    if ysdh and event == "received":
        rows, cols = adapter.fetch_all(
            """
            SELECT F_ID, F_YSXX, F_YSDZT
            FROM T_YSXX
            WHERE F_YSDH = ?
            """,
            [ysdh],
        )
        if not rows:
            raise ValueError(f"未找到运输单号: {ysdh}")
        idx = {c: i for i, c in enumerate(cols)}
        header = rows[0]
        ysid = header[idx["F_ID"]] if "F_ID" in idx else None
        ysdzt_old = header[idx.get("F_YSDZT")] if "F_YSDZT" in idx else None
        try:
            transport_ids = json.loads(header[idx["F_YSXX"]] or "[]")
            if not isinstance(transport_ids, list):
                transport_ids = []
            transport_ids = [int(x) for x in transport_ids if str(x).strip()]
        except Exception:
            transport_ids = []
        if transport_ids:
            placeholders = ",".join(["?"] * len(transport_ids))
            t_rows, t_cols = adapter.fetch_all(
                f"SELECT F_SGBH FROM T_YBFZ WHERE F_ID IN ({placeholders})",
                transport_ids,
            )
            t_idx = {c: i for i, c in enumerate(t_cols)}
            existing = {(it.get("F_SGBH") or "").strip() for it in items}
            default_sj = payload.get("F_SJ")
            default_czr = payload.get("F_CZR")
            default_bz = payload.get("F_BZ")
            for row in t_rows:
                sgbh = (row[t_idx["F_SGBH"]] or "").strip()
                if sgbh and sgbh not in existing:
                    items.append(
                        {
                            "F_SGBH": sgbh,
                            "F_SJ": default_sj,
                            "F_CZR": default_czr,
                            "F_BZ": default_bz,
                        }
                    )
                    existing.add(sgbh)

    if not items:
        raise ValueError("没有可同步的样本")

    sgbh_list = [(it.get("F_SGBH") or "").strip() for it in items]
    record_map = _fetch_ybfz_by_sgbh_map(adapter, sgbh_list)

    success: List[str] = []
    skipped: List[Dict[str, str]] = []
    failed: List[Dict[str, str]] = []
    thaw_orders: List[Dict[str, Any]] = []

    with adapter.connect() as conn:
        cur = conn.cursor()
        for item in items:
            sgbh = (item.get("F_SGBH") or "").strip()
            record = record_map.get(sgbh)
            try:
                result_type, reason, extra = _sync_one_external_item(
                    cur,
                    event=event,
                    item=item,
                    record=record,
                    user=user,
                    adapter=adapter,
                )
                if result_type == "success":
                    conn.commit()
                    success.append(sgbh)
                    if extra and extra.get("thaw_order") is not None:
                        thaw_orders.append({"F_SGBH": sgbh, **extra["thaw_order"]})
                elif result_type == "skipped":
                    skipped.append({"F_SGBH": sgbh, "reason": reason or "已处于目标状态"})
                else:
                    failed.append({"F_SGBH": sgbh, "reason": reason or "状态不允许本次同步"})
            except Exception as exc:
                try:
                    conn.rollback()
                except Exception:
                    pass
                failed.append({"F_SGBH": sgbh, "reason": f"更新失败: {exc}"})

        # 旧版 received + 运输单：若本批有成功接收，尝试更新运输单头
        if event == "received" and ysdh and success and str(ysdzt_old) != "已接收":
            first_ok = next((it for it in items if (it.get("F_SGBH") or "").strip() in success), items[0])
            header_time = _item_event_time(first_ok)
            header_czr = _item_operator(first_ok, user, adapter)
            header_bz = first_ok.get("F_BZ") or payload.get("F_BZ")
            cur.execute(
                """
                UPDATE T_YSXX
                SET F_JSR = ?, F_JSSJ = ?, F_JSBZ = ?, F_YSDZT = ?
                WHERE F_YSDH = ?
                """,
                (header_czr, header_time, header_bz, "已接收", ysdh),
            )
            conn.commit()

    total = len(items)
    result: Dict[str, Any] = {
        "F_EVENT": event,
        "total": total,
        "success_count": len(success),
        "skipped_count": len(skipped),
        "failed_count": len(failed),
        "success": success,
    }
    if skipped:
        result["skipped"] = skipped
    if failed:
        result["failed"] = failed
    if thaw_orders:
        result["thaw_orders"] = thaw_orders
    if ysdh:
        result["F_YSDH"] = ysdh
        result["F_YSDZT"] = "已接收" if success else ysdzt_old
        result["F_ID"] = ysid
    return result


def receive_transport(payload: Dict[str, Any], user: Dict[str, Any] | None = None) -> Dict[str, Any]:
    """兼容旧调用，映射到 sync_external_sample_status(received)。"""
    sgbh_list = _normalize_sgbh_list(payload.get("F_SGBH"))
    items = [
        {
            "F_SGBH": sgbh,
            "F_SJ": payload.get("F_JSSJ"),
            "F_CZR": payload.get("F_JSR"),
            "F_BZ": payload.get("F_JSBZ"),
        }
        for sgbh in sgbh_list
    ]
    return sync_external_sample_status(
        {
            "F_EVENT": "received",
            "F_YSDH": payload.get("F_YSDH"),
            "ITEMS": items,
        },
        user,
    )


def get_sample_by_sgbh(payload: Dict[str, Any]) -> Dict[str, Any]:
    sgbh = (payload.get("F_SGBH") or "").strip()
    if not sgbh:
        raise ValueError("F_SGBH 不能为空")

    adapter = get_adapter()
    # 查分装 + 标本类型
    sql = """
    SELECT
        f.F_ID,
        f.F_SGBH,
        f.F_ZZID,
        f.F_DC,
        f.F_PYGD_ID,
        f.F_YBLX,
        f.F_XMBH,
        y.F_BBLX
    FROM T_YBFZ f
    LEFT JOIN T_YBZJ y ON y.F_ID = f.F_YBZJ_ID
    WHERE f.F_SGBH = ?
    ORDER BY f.F_ID DESC
    """
    rows, cols = adapter.fetch_all(sql, [sgbh])
    if not rows:
        raise ValueError(f"未找到样本编号: {sgbh}")
    idx = {c: i for i, c in enumerate(cols)}
    r = rows[0]  # 取最新一条
    result = {
        "F_ID": r[idx["F_ID"]],
        "F_SGBH": r[idx["F_SGBH"]],
        "F_ZZID": r[idx.get("F_ZZID")],
        "F_DC": r[idx.get("F_DC")],
        "F_PYGD_ID": r[idx.get("F_PYGD_ID")],
        "F_YBLX": r[idx.get("F_YBLX")],
        "F_XMBH": r[idx.get("F_XMBH")] if "F_XMBH" in idx else None,
        "F_BBLX": r[idx.get("F_BBLX")] if "F_BBLX" in idx else None,
        "F_GDBH": None,
        "F_PYR": None,
    }

    pygd_id = result.get("F_PYGD_ID")
    if pygd_id is not None:
        try:
            sql_p = "SELECT F_GDBH, F_SQR AS F_PYR FROM T_PYGD WHERE F_ID = ?"
            rows_p, cols_p = adapter.fetch_all(sql_p, [pygd_id])
        except Exception:
            sql_p = "SELECT F_GDBH, NULL AS F_PYR FROM T_PYGD WHERE F_ID = ?"
            rows_p, cols_p = adapter.fetch_all(sql_p, [pygd_id])
        if rows_p:
            idx_p = {c: i for i, c in enumerate(cols_p)}
            result["F_GDBH"] = rows_p[0][idx_p.get("F_GDBH")]
            result["F_PYR"] = rows_p[0][idx_p.get("F_PYR")] if "F_PYR" in idx_p else None

    return result


def list_available_samples(payload: Dict[str, Any]) -> Dict[str, Any]:
    """
    查询可用的样本（F_YSZT为"未运送"的记录），用于运输单创建前的样本挑选
    类似于 unified_search/list，但只返回未运送的样本
    """
    adapter = get_adapter()
    skip = int(payload.get("skip") or 0)
    limit = int(payload.get("limit") or 50)

    filters: List[str] = []
    params: List[Any] = []

    # 固定条件：只查询未运送的样本
    # F_YSZT 为 "未运送" 或 NULL 或空字符串
    filters.append("(f.F_YSZT IS NULL OR f.F_YSZT = '' OR f.F_YSZT = ?)")
    params.append("未运送")

    for key, column in (
        ("F_SGBH", "f.F_SGBH"),
        ("F_ZZID", "f.F_ZZID"),
        ("F_GDBH", "p.F_GDBH"),
        ("F_DC", "COALESCE(p.F_DC, f.F_DC)"),
    ):
        val = (payload.get(key) or "").strip()
        if val:
            pred, like_params = adapter.like(column, val)
            filters.append(pred)
            params.extend(like_params)

    where_sql = " WHERE " + " AND ".join(filters)

    base_sql = f"""
SELECT
    f.*, p.F_GDBH, p.F_DC AS F_DC
FROM T_YBFZ f
LEFT JOIN T_PYGD p ON p.F_ID = f.F_PYGD_ID
{where_sql}
"""

    count_sql = f"SELECT COUNT(1) FROM ( {base_sql} ) t"
    list_sql = base_sql + " ORDER BY f.F_ID DESC"
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
        record["F_CJSJ"] = _format_datetime(record.get("F_CJSJ"))
        items.append(record)

    return {"items": items, "total": total}


def list_transporting_samples(payload: Dict[str, Any]) -> Dict[str, Any]:
    """
    查询运输中的样本（扁平结构），用于接收方查询运输单及样本信息
    返回格式：运输单列表和样本列表分开，运输单包含 sample_ids，样本包含 F_YSDH
    """
    adapter = get_adapter()
    skip = int(payload.get("skip") or 0)
    limit = int(payload.get("limit") or 50)
    
    import json

    # ========== 第1步：查询运输单（状态为"运送中"） ==========
    transport_filters: List[str] = ["t.F_YSDZT = ?"]
    transport_params: List[Any] = ["运送中"]

    # 运输单级别过滤条件
    for key, column in (
        ("F_YSDH", "t.F_YSDH"),
        ("F_FQR", "t.F_FQR"),
        ("F_YSMDD", "t.F_YSMDD"),
    ):
        val = (payload.get(key) or "").strip()
        if val:
            pred, like_params = adapter.like(column, val)
            transport_filters.append(pred)
            transport_params.extend(like_params)

    # 时间范围过滤
    fqsj_start = (payload.get("F_FQSJ_START") or "").strip()
    fqsj_end = (payload.get("F_FQSJ_END") or "").strip()
    if fqsj_start:
        transport_filters.append("t.F_FQSJ >= ?")
        transport_params.append(fqsj_start)
    if fqsj_end:
        transport_filters.append("t.F_FQSJ <= ?")
        transport_params.append(fqsj_end)

    transport_where_sql = " WHERE " + " AND ".join(transport_filters)

    transport_sql = f"""
    SELECT
        t.F_ID,
        t.F_YSDH,
        t.F_YSXX,
        t.F_FQR,
        t.F_FQSJ,
        t.F_YSDZT,
        t.F_YBSL,
        t.F_BZ,
        t.F_YSMDD
    FROM T_YSXX t
    {transport_where_sql}
    ORDER BY t.F_ID DESC
    """

    with adapter.connect() as conn:
        cur = conn.cursor()
        cur.execute(transport_sql, tuple(transport_params))
        cols = [c[0] for c in cur.description]
        transport_rows = cur.fetchall()

    # 如果没有运输单，直接返回
    if not transport_rows:
        return {
            "transports": [],
            "transport_total": 0,
            "items": [],
            "total": 0
        }

    # 解析所有运输单信息
    all_transports: List[Dict[str, Any]] = []
    all_sample_ids: List[int] = []
    transport_sample_map: Dict[int, List[int]] = {}  # transport_id -> [sample_ids]
    ysdh_map: Dict[int, str] = {}  # sample_id -> F_YSDH

    for row in transport_rows:
        transport = dict(zip(cols, row))
        transport_id = transport["F_ID"]
        ysdh = transport["F_YSDH"]
        
        # 格式化时间字段
        if transport.get("F_FQSJ"):
            transport["F_FQSJ"] = _parse_datetime(transport["F_FQSJ"])
        
        # 解析样本ID列表
        try:
            sample_ids = json.loads(transport.get("F_YSXX") or "[]")
            if not isinstance(sample_ids, list):
                sample_ids = []
            sample_ids = [int(x) for x in sample_ids if str(x).strip()]
        except Exception:
            sample_ids = []
        
        # 记录样本ID与运输单号的映射关系
        for sid in sample_ids:
            ysdh_map[sid] = ysdh
        
        transport_sample_map[transport_id] = sample_ids
        all_sample_ids.extend(sample_ids)
        all_transports.append(transport)

    # 去重样本ID
    all_sample_ids = list(set(all_sample_ids))

    # ========== 第2步：查询所有样本详情 ==========
    sample_details: Dict[int, Dict[str, Any]] = {}
    
    if all_sample_ids:
        placeholders = ",".join(["?"] * len(all_sample_ids))
        
        # 构建样本查询（带样本级别过滤条件）
        sample_filters: List[str] = [f"f.F_ID IN ({placeholders})"]
        sample_params: List[Any] = all_sample_ids.copy()

        for key, column in (
            ("F_SGBH", "f.F_SGBH"),
            ("F_ZZID", "f.F_ZZID"),
            ("F_GDBH", "p.F_GDBH"),
            ("F_DC", "COALESCE(p.F_DC, f.F_DC)"),
        ):
            val = (payload.get(key) or "").strip()
            if val:
                pred, like_params = adapter.like(column, val)
                sample_filters.append(pred)
                sample_params.extend(like_params)

        sample_where_sql = " WHERE " + " AND ".join(sample_filters)

        sample_sql = f"""
        SELECT
            f.*,
            COALESCE(y.F_BBLX, p.F_BBLX) AS F_BBLX,
            p.F_GDBH,
            p.F_DC AS F_DC,
            p.F_SQR AS F_PYR
        FROM T_YBFZ f
        LEFT JOIN T_YBZJ y ON y.F_ID = f.F_YBZJ_ID
        LEFT JOIN T_PYGD p ON p.F_ID = f.F_PYGD_ID
        {sample_where_sql}
        """

        with adapter.connect() as conn:
            cur = conn.cursor()
            try:
                cur.execute(sample_sql, tuple(sample_params))
            except Exception:
                # 如果 F_SQR 不存在，重试不带该字段的查询
                sample_sql = sample_sql.replace("p.F_SQR AS F_PYR", "NULL AS F_PYR")
                cur.execute(sample_sql, tuple(sample_params))
            
            cols_s = [c[0] for c in cur.description]
            sample_rows = cur.fetchall()

        for row in sample_rows:
            sample = dict(zip(cols_s, row))
            sample_id = sample["F_ID"]
            
            # 格式化时间字段
            sample["F_CJSJ"] = _format_datetime(sample.get("F_CJSJ"))
            # 格式化其他可能的时间字段
            for time_field in ["F_XGSJ", "F_JYSJ", "F_RKSJ", "F_CKSJ", "F_JSSJ"]:
                if time_field in sample and sample.get(time_field):
                    sample[time_field] = _format_datetime(sample[time_field])
            
            # 添加运输单号到样本记录
            sample["F_YSDH"] = ysdh_map.get(sample_id)
            
            _format_sample_for_external_export(sample)
            sample_details[sample_id] = sample

    # ========== 第3步：按样本过滤条件筛选运输单 ==========
    has_sample_filter = any([
        payload.get("F_SGBH"),
        payload.get("F_GDBH"),
        payload.get("F_ZZID"),
        payload.get("F_DC"),
    ])

    filtered_transports: List[Dict[str, Any]] = []
    filtered_samples: List[Dict[str, Any]] = []
    
    for transport in all_transports:
        transport_id = transport["F_ID"]
        sample_ids = transport_sample_map.get(transport_id, [])
        
        # 获取该运输单下的匹配样本
        matched_sample_ids = []
        for sid in sample_ids:
            if sid in sample_details:
                matched_sample_ids.append(sid)
                # 避免重复添加样本
                if sample_details[sid] not in filtered_samples:
                    filtered_samples.append(sample_details[sid])
        
        # 如果有样本级别过滤条件，只保留有匹配样本的运输单
        if has_sample_filter and not matched_sample_ids:
            continue
        
        # 构建运输单数据（包含 sample_ids）
        transport_item = {
            "F_ID": transport["F_ID"],
            "F_YSDH": transport["F_YSDH"],
            "F_FQR": transport.get("F_FQR"),
            "F_FQSJ": transport.get("F_FQSJ"),
            "F_YSDZT": transport.get("F_YSDZT"),
            "F_YSMDD": transport.get("F_YSMDD"),
            "F_BZ": transport.get("F_BZ"),
            "F_YBSL": len(matched_sample_ids),  # 实际匹配的样本数量
            "sample_ids": matched_sample_ids,    # 样本ID数组
        }
        filtered_transports.append(transport_item)

    # ========== 第4步：分页返回（按运输单分页） ==========
    transport_total = len(filtered_transports)
    sample_total = len(filtered_samples)
    
    paged_transports = filtered_transports[skip: skip + limit] if skip < transport_total else []
    
    # 获取分页后运输单对应的样本
    paged_sample_ids = set()
    for transport in paged_transports:
        paged_sample_ids.update(transport.get("sample_ids", []))
    
    # 筛选出分页后的样本列表
    paged_samples = [s for s in filtered_samples if s["F_ID"] in paged_sample_ids]

    return {
        "transports": paged_transports,
        "transport_total": transport_total,
        "items": paged_samples,
        "total": sample_total
    }

