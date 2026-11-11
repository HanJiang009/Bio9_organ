"""
流水线进度展示模块 CRUD

以培养工单为维度，批量查询各阶段（登记/分发/培养/质控/传代/入库）状态。
采用「主分页查询 + 8条批量子查询」模式，避免 N+1 问题。
"""
from typing import Dict, Any, List, Optional
from datetime import datetime, date
from app.db.factory import get_adapter
import json
import logging

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 辅助函数
# ---------------------------------------------------------------------------

def _fmt_dt(v: Any) -> Optional[str]:
    """将数据库返回的日期/时间值统一格式化为字符串。"""
    if v is None:
        return None
    if hasattr(v, "strftime"):
        return v.strftime("%Y-%m-%d %H:%M:%S")
    s = str(v)
    return s if s.strip() else None


def _fmt_date(v: Any) -> Optional[str]:
    """将日期值格式化为 YYYY-MM-DD。"""
    if v is None:
        return None
    if hasattr(v, "strftime"):
        return v.strftime("%Y-%m-%d")
    s = str(v)
    return s[:10] if len(s) >= 10 else s


def _parse_dt(v: Any) -> Optional[datetime]:
    """将字符串或 datetime 对象解析为 datetime。"""
    if v is None:
        return None
    if isinstance(v, datetime):
        return v
    if isinstance(v, date):
        return datetime(v.year, v.month, v.day)
    s = str(v).strip()
    if not s:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            pass
    return None


def _calc_pyts(pysj: Any, gdzt: str, wcsj: Any, bhgsj: Any = None) -> Optional[int]:
    """计算培养天数 F_PYTS。
    - 已完成：F_WCSJ - F_PYSJ（天）
    - 不合格：F_BHGSJ - F_PYSJ（天），不足 1 天按 1 天计
    - 已创建：今天 - F_PYSJ（天）
    - 其他：None
    """
    pysj_dt = _parse_dt(pysj)
    if not pysj_dt:
        return None
    gdzt = (gdzt or "").strip()
    if gdzt == "已完成":
        wcsj_dt = _parse_dt(wcsj)
        if wcsj_dt:
            return (wcsj_dt.date() - pysj_dt.date()).days
        return None
    elif gdzt == "不合格":
        bhgsj_dt = _parse_dt(bhgsj)
        if bhgsj_dt:
            days = (bhgsj_dt.date() - pysj_dt.date()).days
            return 1 if days == 0 else days
        return None
    elif gdzt == "已创建":
        return (date.today() - pysj_dt.date()).days
    return None


def _in_clause(n: int):
    """生成 IN 占位符列表字符串。"""
    return ",".join(["?"] * n)


def _fetch_latest_zksh_by_gdbh(cur, gdbh_list: List[str], zkjd: str = "验证单") -> Dict[str, Dict]:
    """批量查询每个工单在指定质控阶段的最新一条 T_ZKSH 记录（按 F_ID 最大）。"""
    from app.crud.qc_audit import zkjd_variants, _PIPELINE_INIT_CHANGES_MARKER
    result: Dict[str, Dict] = {}
    if not gdbh_list:
        return result
    zkjd_base, zkjd_tpl = zkjd_variants(zkjd)
    gdbh_ph = _in_clause(len(gdbh_list))
    pipeline_like = f"%{_PIPELINE_INIT_CHANGES_MARKER}%"
    try:
        cur.execute(
            f"""
            SELECT z.F_GDBH, z.F_SHZT, z.F_SHSJ
            FROM T_ZKSH z
            INNER JOIN (
                SELECT F_GDBH, MAX(F_ID) AS max_id
                FROM T_ZKSH
                WHERE F_GDBH IN ({gdbh_ph}) AND F_ZKJD IN (?, ?)
                  AND (F_CHANGES IS NULL OR F_CHANGES NOT LIKE ?)
                GROUP BY F_GDBH
            ) t ON z.F_ID = t.max_id
            """,
            tuple(gdbh_list) + (zkjd_base, zkjd_tpl, pipeline_like),
        )
        for row in cur.fetchall():
            gdbh = row[0]
            if gdbh:
                result[gdbh] = {"shzt": row[1], "shsj": row[2]}
    except Exception as e:
        logger.warning(f"[pipeline_progress] 最新验证单状态查询失败: {e}")
    return result


def _status_display(status: Optional[str]) -> str:
    """将细粒度 status 映射为前端展示用的四态。"""
    if not status:
        return "未开始"
    if status in ("已完成", "审核通过"):
        return "已完成"
    if status == "进行中":
        return "进行中"
    if status in ("审核不通过", "不合格"):
        return "异常"
    return "未开始"


def _make_stage(status: Optional[str], time: Optional[str] = None, **extra) -> Dict[str, Any]:
    """构造阶段状态对象（含 status_display）。"""
    stage = {
        "status": status,
        "status_display": _status_display(status),
        "time": time,
    }
    stage.update(extra)
    return stage


# ---------------------------------------------------------------------------
# 各阶段批量查询函数
# ---------------------------------------------------------------------------

def _query_djjs(cur, zzids: List[str]) -> Dict[str, Dict]:
    """登记阶段：固定已完成，时间取 T_YBZJ.F_DJRQ。"""
    result: Dict[str, Dict] = {}
    if not zzids:
        return result
    ph = _in_clause(len(zzids))
    try:
        cur.execute(
            f"SELECT F_ZZID, F_DJRQ FROM T_YBZJ WHERE F_ZZID IN ({ph})",
            tuple(zzids),
        )
        for row in cur.fetchall():
            zzid = row[0]
            if zzid and zzid not in result:
                result[zzid] = {"status": "已完成", "time": _fmt_date(row[1])}
    except Exception as e:
        logger.warning(f"[pipeline_progress] 登记阶段查询失败: {e}")
    return result


def _query_ff(cur, zzids: List[str]) -> Dict[str, Dict]:
    """分发阶段：有分发记录→进行中；T_YBZJ.F_QYWC=1→已完成；时间=MAX(F_FFSJ)。"""
    result: Dict[str, Dict] = {}
    if not zzids:
        return result
    ph = _in_clause(len(zzids))
    # 最后一次分发时间
    ffsj_map: Dict[str, Any] = {}
    try:
        cur.execute(
            f"SELECT F_ZZID, MAX(F_FFSJ) FROM T_FFJL WHERE F_ZZID IN ({ph}) GROUP BY F_ZZID",
            tuple(zzids),
        )
        for row in cur.fetchall():
            if row[0]:
                ffsj_map[row[0]] = row[1]
    except Exception as e:
        logger.warning(f"[pipeline_progress] 分发时间查询失败: {e}")

    # 取用完成状态
    qywc_map: Dict[str, int] = {}
    try:
        cur.execute(
            f"SELECT F_ZZID, F_QYWC FROM T_YBZJ WHERE F_ZZID IN ({ph})",
            tuple(zzids),
        )
        for row in cur.fetchall():
            if row[0]:
                qywc_map[row[0]] = int(row[1]) if row[1] is not None else 0
    except Exception as e:
        logger.warning(f"[pipeline_progress] 取用完成状态查询失败: {e}")

    for zzid in zzids:
        if zzid not in ffsj_map:
            # 没有分发记录
            continue
        qywc = qywc_map.get(zzid, 0)
        status = "已完成" if qywc == 1 else "进行中"
        result[zzid] = {"status": status, "time": _fmt_dt(ffsj_map[zzid])}
    return result


def _query_py(cur, ids: List[int], id_to_gdbh: Dict[int, str], id_to_gdzt: Dict[int, str]) -> Dict[int, Dict]:
    """培养阶段：
    - 工单 F_GDZT='不合格' → 不合格
    - T_ZKSH 有验证单提交记录 → 已完成
    - T_PYMX_ZZ 有记录 → 进行中
    时间：MAX(T_PYMX_ZZ.F_CZSJ)
    """
    result: Dict[int, Dict] = {}
    if not ids:
        return result
    ph = _in_clause(len(ids))

    # 培养明细最后时间
    pymx_time_map: Dict[int, Any] = {}
    pymx_exists: set = set()
    try:
        cur.execute(
            f"SELECT F_GDID, MAX(F_CZSJ) FROM T_PYMX_ZZ WHERE F_GDID IN ({ph}) GROUP BY F_GDID",
            tuple(ids),
        )
        for row in cur.fetchall():
            if row[0] is not None:
                gdid = int(row[0])
                pymx_exists.add(gdid)
                pymx_time_map[gdid] = row[1]
    except Exception as e:
        logger.warning(f"[pipeline_progress] 培养明细查询失败: {e}")

    # 验证单提交状态（通过 F_GDBH 关联，取最新一条审核记录）
    gdbh_list = [v for v in id_to_gdbh.values() if v]
    zksh_map: Dict[int, Dict] = {}  # gdid → {shzt, shsj}
    if gdbh_list:
        gdbh_to_zksh = _fetch_latest_zksh_by_gdbh(cur, gdbh_list, "验证单")
        for gdid, gdbh in id_to_gdbh.items():
            if gdbh and gdbh in gdbh_to_zksh:
                zksh_map[gdid] = gdbh_to_zksh[gdbh]

    for gdid in ids:
        gdzt = (id_to_gdzt.get(gdid) or "").strip()
        last_time = _fmt_dt(pymx_time_map.get(gdid))

        if gdzt == "不合格":
            result[gdid] = {"status": "不合格", "time": last_time}
        elif gdid in zksh_map:
            result[gdid] = {"status": "已完成", "time": last_time}
        elif gdid in pymx_exists:
            result[gdid] = {"status": "进行中", "time": last_time}
        # 否则无状态，不加入 result
    return result


def _query_zk(cur, ids: List[int], id_to_gdbh: Dict[int, str]) -> Dict[int, Dict]:
    """质控阶段：
    - T_ZKJL 有用户暂存（F_GXSJ 非空，排除流水线模板占位）且无 T_ZKSH → 进行中
    - T_ZKSH 验证单：待审核 → 进行中；审核通过/不通过 → 对应状态（时间=F_SHSJ）
    """
    result: Dict[int, Dict] = {}
    if not ids:
        return result
    ph = _in_clause(len(ids))

    # 检查 T_ZKJL 是否有用户暂存（流水线初始化占位不写 F_GXSJ）
    zkjl_exists: set = set()
    try:
        cur.execute(
            f"SELECT DISTINCT F_GDID FROM T_ZKJL WHERE F_GDID IN ({ph}) AND F_GXSJ IS NOT NULL",
            tuple(ids),
        )
        for row in cur.fetchall():
            if row[0] is not None:
                zkjl_exists.add(int(row[0]))
    except Exception as e:
        logger.warning(f"[pipeline_progress] 质控草稿查询失败: {e}")

    # 查 T_ZKSH 验证单最新状态（含 F_SHSJ）
    gdbh_list = [v for v in id_to_gdbh.values() if v]
    gdbh_to_zksh = _fetch_latest_zksh_by_gdbh(cur, gdbh_list, "验证单") if gdbh_list else {}

    for gdid in ids:
        gdbh = id_to_gdbh.get(gdid)
        zksh = gdbh_to_zksh.get(gdbh) if gdbh else None

        if zksh:
            shzt = (zksh.get("shzt") or "").strip()
            shsj = _fmt_dt(zksh.get("shsj"))
            if shzt == "待审核":
                result[gdid] = {"status": "进行中", "time": None}
            elif shzt in ("审核通过", "审核不通过"):
                result[gdid] = {"status": shzt, "time": shsj}
            else:
                result[gdid] = {"status": "进行中", "time": None}
        elif gdid in zkjl_exists:
            result[gdid] = {"status": "进行中", "time": None}
        # 否则无状态
    return result


def _query_cd(cur, ids: List[int]) -> Dict[int, Dict]:
    """传代阶段：有 F_YBLY='传代' 的子工单 → 已完成；时间=MIN(F_CJSJ)。"""
    result: Dict[int, Dict] = {}
    if not ids:
        return result
    ph = _in_clause(len(ids))
    try:
        cur.execute(
            f"SELECT F_PARENT_ID, MIN(F_CJSJ) FROM T_PYGD WHERE F_PARENT_ID IN ({ph}) AND F_YBLY = '传代' GROUP BY F_PARENT_ID",
            tuple(ids),
        )
        for row in cur.fetchall():
            if row[0] is not None:
                result[int(row[0])] = {"status": "已完成", "time": _fmt_dt(row[1])}
    except Exception as e:
        logger.warning(f"[pipeline_progress] 传代状态查询失败: {e}")
    return result


def _is_pygd_linked_inbound(pygd_id: Any, sgbh: Optional[str] = None) -> bool:
    """是否为培养工单来源的分装入库（非标本直接入库链路）。

    - 培养工单入库：T_YBFZ.F_PYGD_ID 关联到培养工单（样本管编号通常为 -GD-）
    - 标本直接入库：F_PYGD_ID 为空，仅关联 T_YBZJ（样本管编号通常为 -BB-），不计入 stage_rk
    """
    if pygd_id is None:
        return False
    try:
        if int(pygd_id) <= 0:
            return False
    except (TypeError, ValueError):
        return False
    if sgbh and "-BB-" in str(sgbh):
        return False
    return True


def _query_rk(cur, ids: List[int]) -> Dict[int, Dict]:
    """入库阶段（仅统计培养工单来源分装，不含标本直接入库）：
    - 仅统计 T_YBFZ.F_PYGD_ID 关联到当前培养工单的分装记录
    - 标本直接分装入库（F_PYGD_ID 为空、-BB- 类样本管）不参与 stage_rk
    - T_YBFZ.F_KCZT='在库' → 已完成
    - 运输已接收但未入库，或运送中 → 进行中
    时间：stored 用 F_RKSJ；运输中用 T_YSXX.F_FQSJ。
    """
    result: Dict[int, Dict] = {}
    if not ids:
        return result
    ph = _in_clause(len(ids))

    # 汇总每个工单的分装状态
    order_kczt: Dict[int, str] = {}     # gdid → 最高优先级库存状态
    order_yszt: Dict[int, str] = {}     # gdid → 运输状态（辅助判断进行中）
    ybfz_ids_shipped: List[int] = []
    gdid_to_ybfz_ids: Dict[int, List[int]] = {}
    gdid_to_rksj: Dict[int, Any] = {}

    try:
        cur.execute(
            f"SELECT F_PYGD_ID, F_ID, F_YSZT, F_KCZT, F_RKSJ, F_SGBH FROM T_YBFZ WHERE F_PYGD_ID IN ({ph}) AND F_PYGD_ID IS NOT NULL",
            tuple(ids),
        )
        for row in cur.fetchall():
            pygd_id = row[0]
            sgbh = row[5] if len(row) > 5 else None
            if not _is_pygd_linked_inbound(pygd_id, sgbh):
                continue
            gdid = int(pygd_id)
            fz_id = int(row[1]) if row[1] is not None else None
            yszt = (row[2] or "").strip()
            kczt = (row[3] or "").strip()
            rksj = row[4]

            prev_kc = order_kczt.get(gdid, "")
            if kczt == "在库":
                order_kczt[gdid] = "在库"
            elif kczt == "已出库" and prev_kc != "在库":
                order_kczt[gdid] = "已出库"
            elif prev_kc == "" and kczt:
                order_kczt[gdid] = kczt

            prev_ys = order_yszt.get(gdid, "")
            if yszt == "已接收":
                order_yszt[gdid] = "已接收"
            elif yszt == "运送中" and prev_ys != "已接收":
                order_yszt[gdid] = "运送中"
            elif prev_ys == "" and yszt:
                order_yszt[gdid] = yszt

            if kczt == "在库" and rksj:
                prev_rksj = gdid_to_rksj.get(gdid)
                parsed = _parse_dt(rksj)
                if parsed and (prev_rksj is None or parsed < prev_rksj):
                    gdid_to_rksj[gdid] = parsed

            if yszt in ("运送中", "已接收") and fz_id is not None:
                ybfz_ids_shipped.append(fz_id)
                gdid_to_ybfz_ids.setdefault(gdid, []).append(fz_id)
    except Exception as e:
        logger.warning(f"[pipeline_progress] 分装状态查询失败: {e}")

    # 查 T_YSXX 获取最早发起运输时间
    # T_YSXX.F_YSXX 字段存储 JSON 数组（T_YBFZ.F_ID 列表），无法标准 JOIN
    # 策略：逐个 YBFZ ID 用 LIKE 匹配，取 MIN(F_FQSJ)
    gdid_to_fqsj: Dict[int, Any] = {}
    if ybfz_ids_shipped:
        try:
            # 构造每个 YBFZ ID 的 LIKE 条件，批量查出所有相关运输单
            # 使用 OR LIKE 方式（性能允许的情况下）
            like_parts = " OR ".join([f"F_YSXX LIKE ?" for _ in ybfz_ids_shipped])
            like_vals = [f"%{fid}%" for fid in ybfz_ids_shipped]
            cur.execute(
                f"SELECT F_YSXX, F_FQSJ FROM T_YSXX WHERE {like_parts}",
                tuple(like_vals),
            )
            ysxx_rows = cur.fetchall()

            # 将运输单时间映射回 gdid
            for gdid, fz_ids in gdid_to_ybfz_ids.items():
                fz_id_set = set(fz_ids)
                earliest_fqsj = None
                for ysxx_row in ysxx_rows:
                    try:
                        ysxx_json = json.loads(ysxx_row[0]) if isinstance(ysxx_row[0], str) else (ysxx_row[0] or [])
                        if not isinstance(ysxx_json, list):
                            continue
                        ysxx_ids = {int(x) for x in ysxx_json if x is not None}
                        if ysxx_ids & fz_id_set:
                            fqsj = _parse_dt(ysxx_row[1])
                            if fqsj:
                                if earliest_fqsj is None or fqsj < earliest_fqsj:
                                    earliest_fqsj = fqsj
                    except Exception:
                        pass
                if earliest_fqsj:
                    gdid_to_fqsj[gdid] = earliest_fqsj
        except Exception as e:
            logger.warning(f"[pipeline_progress] 运输时间查询失败: {e}")

    for gdid in set(order_kczt.keys()) | set(order_yszt.keys()):
        kczt = order_kczt.get(gdid, "")
        yszt = order_yszt.get(gdid, "")
        if kczt == "在库":
            result[gdid] = {"status": "已完成", "time": _fmt_dt(gdid_to_rksj.get(gdid))}
        elif yszt in ("运送中", "已接收"):
            result[gdid] = {"status": "进行中", "time": _fmt_dt(gdid_to_fqsj.get(gdid))}
    return result


# ---------------------------------------------------------------------------
# 培养人筛选：入参为 T_USER.F_ID，工单表 F_SQR 存的是 F_Ktype（姓名）
# ---------------------------------------------------------------------------

def _resolve_sqr_names(adapter, user_ids: Any) -> List[str]:
    """将培养人用户 ID 列表解析为 T_USER.F_Ktype（对应 T_PYGD.F_SQR）。"""
    if not user_ids or not isinstance(user_ids, list):
        return []
    ids: List[int] = []
    for v in user_ids:
        try:
            ids.append(int(v))
        except (TypeError, ValueError):
            continue
    if not ids:
        return []

    from app.crud.user import _name_column

    col = _name_column(adapter, quoted=True)
    ph = ",".join(["?"] * len(ids))
    rows, _cols = adapter.fetch_all(
        f"SELECT DISTINCT {col} FROM T_USER WHERE F_ID IN ({ph})",
        ids,
    )
    names: List[str] = []
    for row in rows:
        if row and row[0] is not None and str(row[0]).strip():
            names.append(str(row[0]).strip())
    return names


def _parse_lx_ids(raw) -> List[int]:
    if raw is None:
        return []
    if isinstance(raw, (int, float)):
        return [int(raw)]
    try:
        parsed = json.loads(str(raw))
        if isinstance(parsed, list):
            return [int(i) for i in parsed if i is not None]
        return [int(parsed)]
    except Exception:
        try:
            return [int(raw)]
        except Exception:
            return []


def attach_pipeline_stages(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """为工单行批量挂上六阶段进度（登记/分发/培养/质控/传代/入库）。

    在原行上补充 stage_*、F_PYTS，并把 F_PYSJ 格式化为字符串。
    首页列表与 /pipeline-progress/orders/list 共用，避免复制批量子查询。
    """
    if not rows:
        return []

    adapter = get_adapter()
    ids: List[int] = []
    for r in rows:
        try:
            ids.append(int(r["F_ID"]))
        except (TypeError, ValueError, KeyError):
            continue
    zzids: List[str] = list({r.get("F_ZZID") for r in rows if r.get("F_ZZID")})
    id_to_gdbh: Dict[int, str] = {}
    id_to_gdzt: Dict[int, str] = {}
    for r in rows:
        try:
            gdid = int(r["F_ID"])
        except (TypeError, ValueError, KeyError):
            continue
        id_to_gdbh[gdid] = r.get("F_GDBH") or ""
        id_to_gdzt[gdid] = r.get("F_GDZT") or ""

    pipeline_ids: List[int] = list({
        pid
        for r in rows
        for pid in _parse_lx_ids(r.get("F_PIPELINE_LX"))
    })

    with adapter.connect() as conn:
        cur = conn.cursor()
        pipeline_id_to_mc: Dict[int, str] = {}
        if pipeline_ids:
            ph = _in_clause(len(pipeline_ids))
            try:
                cur.execute(
                    f"SELECT F_ID, F_MC FROM T_PIPELINE WHERE F_ID IN ({ph})",
                    tuple(pipeline_ids),
                )
                for row in cur.fetchall():
                    if row[0] is not None:
                        pipeline_id_to_mc[int(row[0])] = row[1] or ""
            except Exception as e:
                logger.warning(f"[pipeline_progress] 流水线模板名称查询失败: {e}")

        djjs_map = _query_djjs(cur, zzids)
        ff_map = _query_ff(cur, zzids)
        py_map = _query_py(cur, ids, id_to_gdbh, id_to_gdzt)
        zk_map = _query_zk(cur, ids, id_to_gdbh)
        cd_map = _query_cd(cur, ids)
        rk_map = _query_rk(cur, ids)

    def _stage(m: dict, key) -> dict:
        s = m.get(key) or {}
        return _make_stage(s.get("status"), s.get("time"))

    items: List[Dict[str, Any]] = []
    for r in rows:
        item = dict(r)
        try:
            gdid = int(r["F_ID"])
        except (TypeError, ValueError, KeyError):
            continue
        zzid = r.get("F_ZZID")
        gdzt = (r.get("F_GDZT") or "").strip()
        pysj = r.get("F_PYSJ")
        wcsj = r.get("F_WCSJ")
        bhgsj = r.get("F_BHGSJ")
        pysj_str = _fmt_dt(pysj)

        lx_ids = _parse_lx_ids(r.get("F_PIPELINE_LX"))
        py_stage = _stage(py_map, gdid)
        py_stage["F_PIPELINE_LX"] = lx_ids if lx_ids else None
        if lx_ids:
            names = [pipeline_id_to_mc[i] for i in lx_ids if i in pipeline_id_to_mc]
            py_stage["F_MC"] = "，".join(names) if names else None
        else:
            py_stage["F_MC"] = None

        item["F_ID"] = gdid
        item["F_PYSJ"] = pysj_str
        item["F_PYTS"] = _calc_pyts(pysj, gdzt, wcsj, bhgsj)
        item["F_PIPELINE_LX"] = lx_ids if lx_ids else None
        item["stage_djjs"] = _stage(djjs_map, zzid) if zzid else _make_stage("已完成")
        item["stage_ff"] = _stage(ff_map, zzid) if zzid else _make_stage(None)
        item["stage_py"] = py_stage
        item["stage_zk"] = _stage(zk_map, gdid)
        item["stage_cd"] = _stage(cd_map, gdid)
        item["stage_rk"] = _stage(rk_map, gdid)
        items.append(item)
    return items


# ---------------------------------------------------------------------------
# 主查询函数
# ---------------------------------------------------------------------------

def list_pipeline_progress(q: Dict[str, Any], user_ktype: Optional[str] = None,
                          user: Optional[Dict[str, Any]] = None,
                          scope: Optional[str] = None) -> Dict[str, Any]:
    """
    查询培养工单流水线进度列表。

    Parameters
    ----------
    q : 查询参数（见 PipelineProgressQuery）
    user_ktype : 当前登录用户 F_Ktype（暂未限制仅看自己）

    Returns
    -------
    dict : { "total": int, "items": [...] }
          或当 group_by 不为空时：{ "groups": [...] }
    """
    adapter = get_adapter()

    where: List[str] = []
    params: List[Any] = []

    def like(col: str, key: Optional[str]):
        if key and key.strip():
            pred, p = adapter.like(col, key.strip())
            where.append(pred)
            params.extend(p)

    def in_exact(col: str, values: Any):
        if not values:
            return
        if isinstance(values, str):
            values = [values]
        if not isinstance(values, list):
            return
        vals = [str(v).strip() for v in values if v is not None and str(v).strip()]
        if not vals:
            return
        ph = ",".join(["?"] * len(vals))
        where.append(f"{col} IN ({ph})")
        params.extend(vals)

    sqr_ids = q.get("F_SQR")
    if sqr_ids:
        sqr_names = _resolve_sqr_names(adapter, sqr_ids)
        if sqr_names:
            ph = ",".join(["?"] * len(sqr_names))
            where.append(f"p.F_SQR IN ({ph})")
            params.extend(sqr_names)
        else:
            where.append("1 = 0")

    like("p.F_AZ", q.get("F_AZ"))
    like("p.F_BRBH", q.get("F_BRBH"))
    like("p.F_ZZID", q.get("F_ZZID"))
    like("p.F_GDBH", q.get("F_GDBH"))
    in_exact("p.F_YBLY", q.get("F_YBLY"))

    # 开始培养日期范围过滤（含端点）
    pysj_start = (q.get("F_PYSJ_START") or "").strip()
    pysj_end = (q.get("F_PYSJ_END") or "").strip()
    if pysj_start:
        # 仅传日期时补全为当天 00:00:00，确保 >= 起点当天
        if len(pysj_start) == 10:
            pysj_start = pysj_start + " 00:00:00"
        where.append("p.F_PYSJ >= ?")
        params.append(pysj_start)
    if pysj_end:
        # 仅传日期时补全为当天 23:59:59，确保 <= 终点当天
        if len(pysj_end) == 10:
            pysj_end = pysj_end + " 23:59:59"
        where.append("p.F_PYSJ <= ?")
        params.append(pysj_end)

    from app.core.data_scope import apply_owner_filter, SCOPE_SELF
    if user is None and user_ktype is not None and scope == SCOPE_SELF:
        user = {"F_Ktype": user_ktype}
    apply_owner_filter(where, params, scope=scope, user=user, column="p.F_SQR", match="name")

    where_sql = (" WHERE " + " AND ".join(where)) if where else ""

    # 当 group_by 有效时，用窗口函数让同组记录在结果集中连续排列：
    # 按组内最大 F_ID DESC 排组，再在组内按 F_ID DESC 排行。
    # 这样行级分页不会把同一分组的记录打散到不同页（超出 limit 的分组除外，那是合理截断）。
    group_by_early = (q.get("group_by") or "").strip().upper()
    gb_field_map = {"SQR": "F_SQR", "AZ": "F_AZ", "BRBH": "F_BRBH"}
    gb_field = gb_field_map.get(group_by_early)

    if gb_field:
        base_sql = f"""
SELECT p.F_ID, p.F_GDBH, p.F_ZZID, p.F_BRBH, p.F_AZ, p.F_SQR,
       p.F_YBLY, p.F_ZDYXH, p.F_PYSJ, p.F_GDZT, p.F_BHGLX,
       p.F_WCSJ, p.F_BHGSJ, p.F_PIPELINE_LX, p.F_CJSJ
FROM T_PYGD p
{where_sql}
ORDER BY MAX(p.F_ID) OVER (PARTITION BY COALESCE(p.{gb_field}, '')) DESC, p.F_ID DESC
"""
    else:
        base_sql = f"""
SELECT p.F_ID, p.F_GDBH, p.F_ZZID, p.F_BRBH, p.F_AZ, p.F_SQR,
       p.F_YBLY, p.F_ZDYXH, p.F_PYSJ, p.F_GDZT, p.F_BHGLX,
       p.F_WCSJ, p.F_BHGSJ, p.F_PIPELINE_LX, p.F_CJSJ
FROM T_PYGD p
{where_sql}
ORDER BY p.F_ID DESC
"""
    count_sql = f"SELECT COUNT(1) FROM T_PYGD p {where_sql}"

    skip = q.get("skip") or 0
    limit = q.get("limit") or 20
    paginated_sql, extra_params = adapter.paginate(base_sql, skip=skip, limit=limit)

    with adapter.connect() as conn:
        cur = conn.cursor()

        # 总数
        cur.execute(count_sql, tuple(params))
        total = int(cur.fetchone()[0])

        if total == 0:
            return {"total": 0, "items": []}

        # 分页主数据
        cur.execute(paginated_sql, tuple(params + extra_params))
        cols = [c[0] for c in cur.description]
        rows = [dict(zip(cols, r)) for r in cur.fetchall()]

    items = attach_pipeline_stages(rows)

    # ---- 可选分组 ----
    group_by = (q.get("group_by") or "").strip().upper()
    if group_by:
        group_field_map = {
            "SQR": "F_SQR",
            "AZ": "F_AZ",
            "BRBH": "F_BRBH",
        }
        field = group_field_map.get(group_by)
        if field:
            # 查询每个分组键的全量计数（不受分页影响）
            group_total_map: Dict[str, int] = {}
            try:
                gc_sql = f"SELECT p.{field}, COUNT(1) FROM T_PYGD p {where_sql} GROUP BY p.{field}"
                with adapter.connect() as _gc_conn:
                    _gc_cur = _gc_conn.cursor()
                    _gc_cur.execute(gc_sql, tuple(params))
                    for _row in _gc_cur.fetchall():
                        _key = _row[0] or ""
                        group_total_map[_key] = int(_row[1])
            except Exception as e:
                logger.warning(f"[pipeline_progress] 分组计数查询失败: {e}")

            # 将当前页 items 按分组键归类
            groups_dict: Dict[str, List] = {}
            for item in items:
                key = item.get(field) or ""
                groups_dict.setdefault(key, []).append(item)

            groups = [
                {
                    "key": k,
                    "total": group_total_map.get(k, len(v)),  # 优先用全量计数
                    "items": v,
                }
                for k, v in groups_dict.items()
            ]
            return {"total": total, "groups": groups}

    return {"total": total, "items": items}
