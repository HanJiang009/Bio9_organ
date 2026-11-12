from typing import Dict, Any, List, Tuple, Optional
from app.db.factory import get_adapter
from datetime import datetime
import json
import logging

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# F_PIPELINE_LX 序列化/反序列化辅助（支持多个流向流水线 ID）
# ---------------------------------------------------------------------------

def _serialize_lx(ids) -> Optional[str]:
    """List[int] / int / None → JSON 字符串，用于写入 T_PYGD.F_PIPELINE_LX。"""
    if ids is None:
        return None
    if isinstance(ids, int):
        return json.dumps([ids])
    if isinstance(ids, list):
        clean = [int(i) for i in ids if i is not None]
        return json.dumps(clean) if clean else None
    # 兼容已是字符串的情况（不重复序列化）；空串视为未设置
    if isinstance(ids, str):
        return ids if ids.strip() else None
    return None


def _deserialize_lx(raw) -> Optional[List[int]]:
    """JSON 字符串 / 旧版单整数 / None → List[int]，用于从 T_PYGD 读取后转换。"""
    if raw is None:
        return None
    if isinstance(raw, str) and not raw.strip():
        return None
    if isinstance(raw, (int, float)):
        return [int(raw)]
    if isinstance(raw, list):
        result = [int(i) for i in raw if i is not None]
        return result or None
    try:
        parsed = json.loads(str(raw))
        if isinstance(parsed, list):
            result = [int(i) for i in parsed if i is not None]
            return result or None
        return [int(parsed)]
    except Exception:
        try:
            return [int(raw)]
        except Exception:
            return None


def _coerce_pipeline_jm(val) -> Optional[int]:
    """F_PIPELINE_JM → int | None。空串、空白、非法值一律视为未设置。"""
    if val is None:
        return None
    if isinstance(val, bool):
        return None
    if isinstance(val, int):
        return val
    if isinstance(val, float) and val.is_integer():
        return int(val)
    if isinstance(val, str):
        s = val.strip()
        if not s:
            return None
        try:
            return int(s)
        except ValueError:
            return None
    try:
        return int(val)
    except (ValueError, TypeError):
        return None


def _coerce_pipeline_lx(val) -> Optional[List[int]]:
    """入参 F_PIPELINE_LX（int / list / JSON 字符串）→ List[int] | None。"""
    return _deserialize_lx(val)


def _normalize_list_pyts(days: int | None) -> int | None:
    """培养天数展示归一化：日期差不足 1 天时按 1 天计。"""
    if days is None:
        return None
    return 1 if days == 0 else days


def _build_zdyxh_order(adapter, prefix: str = "p") -> str:
    """
    生成 F_ZDYXH 字段（VARCHAR）的多级排序子句（不含前导 ORDER BY 关键字，末尾带逗号）。

    排序规则：
      1. 首字母为 A-Z / a-z 的记录，按字母表升序
      2. 首字母为 0-9 的记录，按字符串长度升序再按值升序（等效于数值升序）
      3. NULL 或空字符串排最后，再按 F_ID 倒序（由调用方追加）
    """
    from app.db.adapters.dameng import DamengAdapter
    from app.db.adapters.mysql import MySQLAdapter
    if isinstance(adapter, DamengAdapter):
        sub1 = lambda col: f"SUBSTR({col}, 1, 1)"
        len_fn = "LENGTH"
    elif isinstance(adapter, MySQLAdapter):
        sub1 = lambda col: f"SUBSTRING({col}, 1, 1)"
        len_fn = "CHAR_LENGTH"
    else:
        sub1 = lambda col: f"SUBSTRING({col}, 1, 1)"
        len_fn = "LEN"

    col = f"{prefix}.F_ZDYXH"
    s1 = sub1(col)
    return (
        f" CASE WHEN {col} IS NULL OR {col} = '' THEN 3"
        f" WHEN UPPER({s1}) BETWEEN 'A' AND 'Z' THEN 1"
        f" ELSE 2 END ASC,"
        f" CASE WHEN UPPER({s1}) BETWEEN 'A' AND 'Z' THEN {col} ELSE NULL END ASC,"
        f" CASE WHEN UPPER({s1}) BETWEEN '0' AND '9' THEN {len_fn}({col}) ELSE NULL END ASC,"
        f" CASE WHEN UPPER({s1}) BETWEEN '0' AND '9' THEN {col} ELSE NULL END ASC,"
    )


def list_pending_orders(q: Dict[str, Any], user_id: int | None = None,
                       user: Dict[str, Any] | None = None,
                       scope: str | None = None) -> Dict[str, Any]:
    adapter = get_adapter()

    where: List[str] = []
    params: List[Any] = []

    # 工单状态
    gdzt = (q.get("F_GDZT") or "").strip()
    if gdzt:
        where.append("ff.F_GDZT = ?")
        params.append(gdzt)

    # 模糊过滤
    if q.get("F_XM"):
        pred, p = adapter.like("y.F_XM", q["F_XM"].strip())
        where.append(pred)
        params.extend(p)
    if q.get("F_BRBH"):
        pred, p = adapter.like("y.F_BRBH", q["F_BRBH"].strip())
        where.append(pred)
        params.extend(p)
    if q.get("F_ZZID"):
        pred, p = adapter.like("ff.F_ZZID", q["F_ZZID"].strip())
        where.append(pred)
        params.extend(p)
    if q.get("F_CJBW"):
        pred, p = adapter.like("y.F_CJBW", q["F_CJBW"].strip())
        where.append(pred)
        params.extend(p)
    if q.get("F_GDLX"):
        pred, p = adapter.like("ff.F_GDLX", q["F_GDLX"].strip())
        where.append(pred)
        params.extend(p)

    # 日期范围
    if q.get("F_DJRQ_START"):
        where.append("y.F_DJRQ >= ?")
        params.append(q["F_DJRQ_START"])
    if q.get("F_DJRQ_END"):
        where.append("y.F_DJRQ <= ?")
        params.append(q["F_DJRQ_END"])
    if q.get("F_CXSJ_START"):
        where.append("y.F_CXSJ >= ?")
        params.append(q["F_CXSJ_START"])
    if q.get("F_CXSJ_END"):
        where.append("y.F_CXSJ <= ?")
        params.append(q["F_CXSJ_END"])

    from app.core.data_scope import apply_owner_filter, SCOPE_SELF
    if user is None and user_id is not None and scope == SCOPE_SELF:
        user = {"F_ID": user_id}
    apply_owner_filter(where, params, scope=scope, user=user, column="ff.F_PYRY", match="id")

    where_sql = (" WHERE " + " AND ".join(where)) if where else ""

    base_core = f"""
SELECT
  ff.F_ID, ff.F_ZZID, ff.F_GDZT, ff.F_GDLX, ff.F_FFSJ, ff.F_PYRY,
  y.F_XM, y.F_XB, y.F_BRBH, y.F_BBLX, y.F_ZQTYS, y.F_QYFS, y.F_CXSJ,
  y.F_DJRQ,
  y.F_CJBW,
  COALESCE(y.F_AZ, ff.F_AZ) AS F_AZ
FROM T_FFJL ff
LEFT JOIN T_SGXX g ON g.F_ZZID = ff.F_ZZID
LEFT JOIN (
  SELECT t1.* FROM T_YBZJ t1
  JOIN (
    SELECT F_ZZID, MAX(F_ID) AS max_id FROM T_YBZJ GROUP BY F_ZZID
  ) t2 ON t1.F_ZZID = t2.F_ZZID AND t1.F_ID = t2.max_id
) y ON y.F_ZZID = ff.F_ZZID
{where_sql}
"""
    # 应用于分页查询的 ORDER BY
    base_select = base_core + " ORDER BY ff.F_FFSJ DESC"

    # 统计总数（子查询中不能包含 ORDER BY）
    count_sql = f"SELECT COUNT(1) FROM ( {base_core} ) t"

    sql, extra = adapter.paginate(base_select, skip=q.get("skip") or 0, limit=q.get("limit"))

    with adapter.connect() as conn:
        cur = conn.cursor()
        # total
        cur.execute(count_sql, tuple(params))
        total = int(cur.fetchone()[0])
        # items
        cur.execute(sql, tuple(params + extra))
        rows = cur.fetchall()
        cols = [c[0] for c in cur.description]
        items: List[Dict[str, Any]] = [dict(zip(cols, r)) for r in rows]

        # map F_PYRY_NAME
        pyry_names: Dict[int, str] = {}
        try:
            ids = list({int(it.get("F_PYRY")) for it in items if it.get("F_PYRY") is not None})
            if ids:
                q_marks = ",".join(["?"] * len(ids))
                from app.crud.user import _name_column
                col = _name_column(adapter, quoted=True)
                cur.execute(f"SELECT F_ID, {col} FROM T_USER WHERE F_ID IN ({q_marks})", tuple(ids))
                for uid, name in cur.fetchall():
                    pyry_names[int(uid)] = name
        except Exception:
            pass

        # format dates and map names
        for it in items:
            # F_FFSJ: 格式 YYYY-MM-DD HH:MM:SS
            v = it.get("F_FFSJ")
            if v is not None:
                try:
                    it["F_FFSJ"] = v.strftime("%Y-%m-%d %H:%M:%S") if hasattr(v, "strftime") else str(v)
                except Exception:
                    it["F_FFSJ"] = str(v)
            # F_DJRQ: 格式 YYYY-MM-DD
            if it.get("F_DJRQ") is not None:
                dv = it["F_DJRQ"]
                try:
                    it["F_DJRQ"] = dv.strftime("%Y-%m-%d") if hasattr(dv, "strftime") else str(dv)
                except Exception:
                    it["F_DJRQ"] = str(dv)
            # F_CXSJ: 格式 YYYY-MM-DD HH:MM:SS
            if it.get("F_CXSJ") is not None:
                cv = it["F_CXSJ"]
                try:
                    it["F_CXSJ"] = cv.strftime("%Y-%m-%d %H:%M:%S") if hasattr(cv, "strftime") else str(cv)
                except Exception:
                    it["F_CXSJ"] = str(cv)
            uid = it.get("F_PYRY")
            it["F_PYRY_NAME"] = pyry_names.get(int(uid)) if uid is not None else None
            # 将 F_CJBW 映射为 F_ZZBW
            it["F_ZZBW"] = it.pop("F_CJBW", None)

        # asTree 开关：将传代/复苏派生工单聚合到父工单 children
        val = q.get("asTree")
        as_tree = False
        if isinstance(val, str):
            as_tree = val.strip().lower() in ("1", "true", "yes")
        else:
            as_tree = bool(val)
        if not as_tree:
            return {"items": items, "total": total}

        # 拉取 parent 关系：若父节点不在当前页，仍需补父节点进集合
        ids = [int(it.get("F_ID")) for it in items]
        id_to_item = {int(it.get("F_ID")): it for it in items}
        parent_map: Dict[int, int | None] = {}
        # 读取当前页记录的 parent
        cur.execute(
            f"SELECT F_ID, F_PARENT_ID FROM T_PYGD WHERE F_ID IN ({','.join(['?']*len(ids))})",
            tuple(ids),
        )
        for rid, pid in cur.fetchall():
            parent_map[int(rid)] = int(pid) if pid is not None else None
        # 找出缺失的父ID，补充读取父节点并合并到集合
        missing_parent_ids = sorted({pid for pid in parent_map.values() if pid and pid not in id_to_item})
        if missing_parent_ids:
            q_marks = ",".join(["?" for _ in missing_parent_ids])
            cur.execute(
                f"SELECT p.F_ID, p.F_GDBH, p.F_ZZID, p.F_XM, p.F_BRBH, p.F_BBLX, p.F_DC, p.F_DCID, p.F_ZZBW, p.F_XYZL, p.F_QYFS, p.F_YBLY, p.F_YBGG, p.F_SQR, p.F_PYSJ, p.F_FJ, p.F_CJSJ, p.F_GDLX, p.F_GDZT, p.F_AZ FROM T_PYGD p WHERE p.F_ID IN ({q_marks})",
                tuple(missing_parent_ids),
            )
            rows2 = cur.fetchall()
            cols2 = [c[0] for c in cur.description]
            for r in rows2:
                it = dict(zip(cols2, r))
                # 补充必要格式化
                for k in ("F_PYSJ", "F_CJSJ"):
                    v = it.get(k)
                    if v is not None:
                        try:
                            it[k] = v.strftime("%Y-%m-%d %H:%M:%S") if hasattr(v, "strftime") else str(v)
                        except Exception:
                            it[k] = str(v)
                # 附件映射最小化：保持为原 JSON 字段（列表需要时可按需加载）
                it["F_FJ"] = []
                it["F_SHZT"] = None
                it["F_DCJL"] = None
                it["F_YCL"] = False
                id_to_item[int(it["F_ID"]) ] = it
                items.append(it)
                # 确保 parent_map 有父的 parent 记录
                if int(it["F_ID"]) not in parent_map:
                    parent_map[int(it["F_ID"]) ] = None

        # 构树：parent 不在集合内则视为根
        for it in items:
            it["children"] = []
        roots: List[Dict[str, Any]] = []
        for it in items:
            rid = int(it.get("F_ID"))
            pid = parent_map.get(rid)
            if pid and pid in id_to_item:
                parent = id_to_item[pid]
                parent.setdefault("children", []).append(it)
            else:
                roots.append(it)

        return {"items": roots, "total": total}


def _generate_culture_gdbh(
    zzid: str,
    dc: str,
    *,
    gdbh_override: str | None = None,
    brbh: str | None = None,
    bblx: str | None = None,
    xm: str | None = None,
) -> str:
    """培养工单编号。P0 与非 P0 共用同一套规则。

    优先顺序：用户指定 > 字典编码规则 F_GDBH（上下文含 F_DC）> {标本号}{代次}{时间戳}。
    """
    import logging as _log

    if gdbh_override:
        gdbh = gdbh_override
    else:
        try:
            from app.crud.code_rule import generate_code as _gen_gdbh
            gdbh = _gen_gdbh("F_GDBH", {
                "F_ZZID": zzid,
                "F_DC": dc,
                "F_BRBH": brbh,
                "F_BBLX": bblx,
                "F_XM": xm,
            })
            if not gdbh:
                raise ValueError("字典编码规则生成的工单编号为空")
        except Exception as _e:
            _log.getLogger(__name__).warning(
                f"[culture-gdbh] 字典编码规则生成工单编号失败({_e})，降级硬编码兜底"
            )
            ts = datetime.now().strftime("%Y%m%d%H%M%S")
            gdbh = f"{zzid}{dc}{ts}"

    try:
        gdbh.encode("gbk")
    except UnicodeEncodeError:
        _log.getLogger(__name__).warning(
            f"[culture-gdbh] 工单编号含GBK不兼容字符，强制降级: {repr(gdbh)}"
        )
        ts = datetime.now().strftime("%Y%m%d%H%M%S")
        gdbh = f"{dc}{ts}"
    return gdbh


def _specimen_type_code(bblx: Any) -> str:
    """标本类型显示值解析为字典编码。未配置字典时沿用原值。"""
    raw = "" if bblx is None else str(bblx).strip()
    if not raw:
        return ""
    try:
        from app.crud.code_rule import (
            _category_code_candidates,
            _dict_items_for_field,
            _gbk_safe,
            _resolve_dict_item_code,
        )
        from app.crud.dict import get_dict_items_batch

        codes = _category_code_candidates("F_BBLX")
        items_map = get_dict_items_batch(codes, enabled_only=True) if codes else {}
        items = _dict_items_for_field("F_BBLX", items_map)
        resolved = _resolve_dict_item_code(raw, items)
        return _gbk_safe(resolved if resolved else raw)
    except Exception:
        return raw


def _generate_culture_dcid(zzid: str, bblx: Any, dc: str) -> str:
    """代次ID：标本号-标本类型编码-工单代次。"""
    code = _specimen_type_code(bblx)
    parts = [str(zzid or "").strip(), code, str(dc or "").strip()]
    return "-".join(part for part in parts if part)


def create_order_from_ffjl(
    ffjl_id: int,
    zzbw: str | None,
    xyzl: str | None,
    qyfs: str | None,
    ybgg: str | None,
    pysj_str: str | None,
    file_ids: List[int] | None,
    creator_code: str = "",
    gdbh_override: str | None = None,
    pipeline_jm: int | None = None,
    pipeline_lx: List[int] | int | None = None,
) -> Dict[str, Any]:
    """根据分发记录ID创建培养工单(T_PYGD)。

    规则：
    - 代次 F_DC 固定为 'P0'
    - 代次ID F_DCID = 标本号-标本类型编码-工单代次
    - 工单编号 F_GDBH 与传代、复苏共用字典编码规则 F_GDBH
    - 申请人 F_SQR: 用 T_FFJL.F_PYRY 去 T_USER 查询 F_Ktype，未找到时降级使用 F_FFCJR
    - 创建成功后将 T_FFJL.F_GDZT 更新为 '已创建'
    """
    from app.crud.field_config import assert_page
    assert_page("culture_order", {
        "F_QYFS": qyfs,
        "F_YBGG": ybgg,
        "F_ZZBW": zzbw,
        "F_PYSJ": pysj_str,
        "F_PIPELINE_JM": pipeline_jm,
        "F_PIPELINE_LX": pipeline_lx,
    }, limit=["F_QYFS", "F_YBGG", "F_ZZBW", "F_PYSJ", "F_PIPELINE_JM", "F_PIPELINE_LX"])
    adapter = get_adapter()
    with adapter.connect() as conn:
        cur = conn.cursor()
        # 取待创建工单及样本信息（包含 F_AZ 癌种字段和 F_PYRY）
        cur.execute(
            """
            SELECT ff.F_ZZID, ff.F_FFCJR, ff.F_AZ, ff.F_PYRY,
                   yj.F_XM, yj.F_XB, yj.F_BRBH, yj.F_BBLX,
                   g.F_QYFS AS F_SG_QYFS,
                   ff.F_GDLX,
                   yj.F_XM AS F_YJ_XM,
                   yj.F_BRBH AS F_YJ_BRBH,
                   yj.F_BBLX AS F_YJ_BBLX,
                   yj.F_QYFS AS F_YJ_QYFS,
                   yj.F_XMBH
            FROM T_FFJL ff
            LEFT JOIN T_SGXX g ON g.F_ZZID = ff.F_ZZID
            LEFT JOIN (
                SELECT t1.*
                FROM T_YBZJ t1
                INNER JOIN (
                    SELECT F_ZZID, MAX(F_ID) AS max_id
                    FROM T_YBZJ
                    GROUP BY F_ZZID
                ) t2 ON t1.F_ZZID = t2.F_ZZID AND t1.F_ID = t2.max_id
            ) yj ON yj.F_ZZID = ff.F_ZZID
            WHERE ff.F_ID = ?
            """,
            (ffjl_id,),
        )
        row = cur.fetchone()
        if not row:
            raise ValueError("分发记录不存在")
        cols = [c[0] for c in cur.description]
        base = dict(zip(cols, row))

        zzid = base.get("F_ZZID")
        if not zzid:
            raise ValueError("分发记录缺少标本号")

        # 从 T_USER 查询培养人员的 F_Ktype 作为申请人
        pyry_id = base.get("F_PYRY")
        sqr_name = None
        if pyry_id:
            try:
                cur.execute("SELECT F_Ktype FROM T_USER WHERE F_ID = ?", (pyry_id,))
                user_row = cur.fetchone()
                if user_row and user_row[0]:
                    sqr_name = user_row[0]
            except Exception:
                pass
        
        # 如果未找到，降级使用 F_FFCJR
        if not sqr_name:
            sqr_name = base.get("F_FFCJR")

        # 尝试补充最新制剂记录中的 F_XYZL（列可能不存在，需检测）
        try:
            has_xyzl_col = adapter.column_exists(cur, "T_YBZJ", "F_XYZL")
        except Exception:
            has_xyzl_col = False
        if has_xyzl_col:
            try:
                sql = "SELECT F_XYZL FROM T_YBZJ WHERE F_ZZID = ? ORDER BY F_ID DESC"
                paginated_sql, extra = adapter.paginate(sql, 0, 1)
                cur.execute(paginated_sql, (zzid,) + tuple(extra))
                xyzl_row = cur.fetchone()
                if xyzl_row:
                    base["F_YJ_XYZL"] = xyzl_row[0]
            except Exception:
                pass

        dc = "P0"
        dcid = _generate_culture_dcid(zzid, base.get("F_YJ_BBLX") or base.get("F_BBLX"), dc)
        gdbh = _generate_culture_gdbh(
            zzid,
            dc,
            gdbh_override=gdbh_override,
            brbh=base.get("F_YJ_BRBH") or base.get("F_BRBH"),
            bblx=base.get("F_YJ_BBLX") or base.get("F_BBLX"),
            xm=base.get("F_YJ_XM") or base.get("F_XM"),
        )

        # 解析培养时间
        pysj = None
        if pysj_str:
            try:
                # 兼容 "YYYY-MM-DD HH:MM:SS" 或 ISO 格式
                try:
                    pysj = datetime.fromisoformat(pysj_str)
                except Exception:
                    pysj = datetime.strptime(pysj_str, "%Y-%m-%d %H:%M:%S")
            except Exception:
                pysj = None

        # 插入 T_PYGD：F_FJ 改为保存文件ID数组JSON；响应返回同时给出 id/path/name
        fj_json = None
        attachments: List[Dict[str, Any]] = []
        if file_ids:
            try:
                from app.crud.file_center import get_files_by_ids
                import os
                ids = [int(i) for i in file_ids]
                files = get_files_by_ids(ids)
                for f in files:
                    fid = f.get("F_ID")
                    fpath = f.get("F_PATH")
                    fname = f.get("F_ORIGINAL_NAME") or (os.path.basename(fpath) if fpath else None)
                    if fid is not None:
                        attachments.append({"id": int(fid), "path": fpath, "name": fname})
                import json as _json
                fj_json = _json.dumps(ids, ensure_ascii=False)
            except Exception:
                attachments = []
                fj_json = None
        cur.execute(
            """
            INSERT INTO T_PYGD (
                F_XM, F_BRBH, F_ZZID, F_BBLX,
                F_DC, F_DCID, F_ZZBW, F_XYZL, F_QYFS, F_YBGG, F_YBLY,
                F_SQR, F_PYSJ, F_FJ, F_GDBH, F_GDLX, F_GDZT, F_AZ,
                F_PIPELINE_JM, F_PIPELINE_LX, F_CJSJ, F_XMBH
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                base.get("F_YJ_XM") or base.get("F_XM"),
                base.get("F_YJ_BRBH") or base.get("F_BRBH"),
                zzid,
                base.get("F_YJ_BBLX") or base.get("F_BBLX"),
                dc,
                dcid,
                zzbw,
                xyzl or base.get("F_YJ_XYZL") or base.get("F_XYZL"),
                qyfs or base.get("F_YJ_QYFS") or base.get("F_SG_QYFS"),
                ybgg,
                "原代制备",
                sqr_name,  # 使用从T_USER查询到的F_Ktype
                pysj,
                fj_json,
                gdbh,
                base.get("F_GDLX"),
                "已创建",
                base.get("F_AZ"),  # 癌种字段
                pipeline_jm,
                _serialize_lx(pipeline_lx),
                datetime.now(),
                base.get("F_XMBH"),  # 项目编号（快照，来自标本登记）
            ),
        )
        # 获取新ID
        try:
            cur.execute("SELECT MAX(F_ID) FROM T_PYGD WHERE F_ZZID = ?", (zzid,))
            new_id = int(cur.fetchone()[0])
        except Exception:
            new_id = None

        # 更新分发记录状态
        try:
            cur.execute("UPDATE T_FFJL SET F_GDZT = '已创建' WHERE F_ID = ?", (ffjl_id,))
        except Exception:
            pass

        conn.commit()
    
    # ========== 创建质控审核记录（在事务外执行） ==========
    import logging
    logger = logging.getLogger(__name__)
    
    # 调试日志
    logger.info(f"[工单创建质控] 准备创建质控记录 - new_id: {new_id}, zzid: {zzid}, creator_code: '{creator_code}'")
    
    qc_audit_id = None
    if new_id and zzid and creator_code is not None:
        try:
            from app.crud.qc_audit import create_qc_audit_record, get_sample_basic_info
            
            # 获取样本基本信息
            sample_info = get_sample_basic_info(zzid)
            
            # 工单创建的变更信息
            changes = {
                "操作": "创建培养工单",
                "关键信息": {
                    "工单ID": new_id,
                    "工单编号": gdbh,
                    "标本号": zzid,
                    "代次": dc,
                    "代次ID": dcid,
                    "组织部位": zzbw,
                    "样本规格": ybgg,
                    "培养人": sqr_name,
                    "工单状态": "已创建"
                }
            }
            
            # 如果有培养时间，添加到变更信息
            if pysj:
                changes["关键信息"]["开始培养日期"] = pysj.strftime("%Y-%m-%d %H:%M:%S")
            
            # ✅ 获取新创建的工单完整详情数据
            new_detail_data = None
            try:
                new_detail_data = get_culture_order_detail(new_id)
                logger.info(f"[工单创建质控] 成功获取新工单详情数据用于备份，工单ID: {new_id}")
            except Exception as e:
                logger.warning(f"[工单创建质控] 获取新工单详情数据失败: {e}")
            
            # 创建质控审核记录
            qc_audit_id = create_qc_audit_record(
                F_ZKJD="工单创建",
                F_BGLX="新增",
                F_ZZID=zzid,
                F_CHANGES=changes,
                F_JLR=creator_code or "系统",  # 如果为空字符串，使用"系统"
                F_GDID=new_id,
                F_XM=sample_info.get("F_XM"),
                F_BRBH=sample_info.get("F_BRBH"),
                F_ZZBW=sample_info.get("F_ZZBW"),
                F_YBLY="原代制备",  # 使用工单中设置的 F_YBLY 值（来自 T_PYGD 表）
                # 工单相关字段（只传 T_ZKSH 表实际存在的字段）
                F_GDBH=gdbh,
                F_DCID=dcid,
                F_DC=dc,
                F_PYSJ=pysj,
                F_SQR=sqr_name,
                # F_GDZT 不存在于 T_ZKSH 表，通过 F_GDBH 关联 T_PYGD 查询
                F_OLD_DATA=new_detail_data  # ✅ 新增操作记录新增后的完整数据
            )
            logger.info(f"[工单创建质控] 创建质控审核记录成功，工单ID: {new_id}, 工单编号: {gdbh}, 质控ID: {qc_audit_id}")
        except Exception as e:
            logger.error(f"[工单创建质控] 创建质控审核记录失败 (工单ID: {new_id}): {e}", exc_info=True)
    else:
        logger.warning(f"[工单创建质控] 跳过创建质控记录 - new_id: {new_id}, zzid: {zzid}, creator_code: '{creator_code}'")
    
    result = {
        "F_ID": new_id,
        "F_GDBH": gdbh,
        "F_ZZID": zzid,
        "F_FJ_IDS": file_ids or [],
        "F_FJ": attachments
    }
    
    # 添加质控审核记录ID
    if qc_audit_id:
        result["qc_audit_id"] = qc_audit_id

    # ========== 流水线模板初始化 ==========
    if new_id and (pipeline_jm or pipeline_lx):
        try:
            from app.crud.pipeline_init import apply_pipeline_to_order
            init_result = apply_pipeline_to_order(new_id, creator_code or "")
            result["pipeline_init"] = init_result
            logger.info(f"[工单创建流水线] 工单 {new_id} 流水线初始化完成: {init_result.get('initialized')}")
        except Exception as init_err:
            # 初始化失败：删除刚创建的工单（补偿回滚），然后抛出带详细说明的异常
            logger.error(f"[工单创建流水线] 工单 {new_id} 流水线初始化失败，正在回滚工单: {init_err}", exc_info=True)
            try:
                from app.db.factory import get_adapter as _get_adapter
                with _get_adapter().connect() as _conn:
                    _conn.cursor().execute("DELETE FROM T_PYGD WHERE F_ID = ?", (new_id,))
                    _conn.commit()
                logger.info(f"[工单创建流水线] 工单 {new_id} 已回滚删除")
            except Exception as del_err:
                logger.error(f"[工单创建流水线] 回滚删除工单 {new_id} 失败: {del_err}")
            raise ValueError(f"工单创建成功但流水线初始化失败，操作已回滚。原因：{init_err}") from init_err

    return result


def _parse_attachment_field(raw) -> Dict[str, Any]:
    """将附件字段原始值解析为 {"bz": str, "ids": [int, ...]} 格式。

    兼容三种历史格式（向下兼容）：
    - 新格式：{"bz": "备注", "ids": [1, 2, 3]}
    - 中间格式：[{"id": 1, "bz": "备注"}, ...]（每文件独立备注，取第一个非空 bz 作为共用备注）
    - 旧格式：[1, 2, 3]（纯 ID 数组）
    """
    import json as _json
    empty: Dict[str, Any] = {"bz": "", "ids": []}
    if isinstance(raw, str):
        try:
            raw = _json.loads(raw)
        except Exception:
            return empty
    if raw is None:
        return empty
    # ---- 新格式：顶层 dict ----
    if isinstance(raw, dict) and "ids" in raw:
        ids: List[int] = []
        for i in (raw.get("ids") or []):
            try:
                fid = int(i)
                if fid:
                    ids.append(fid)
            except Exception:
                pass
        return {"bz": raw.get("bz") or "", "ids": ids}
    # ---- 兼容旧列表格式 ----
    if isinstance(raw, list):
        ids = []
        bz = ""
        for item in raw:
            try:
                if isinstance(item, dict):
                    fid = int(item.get("id") or item.get("F_ID") or 0)
                    if fid:
                        ids.append(fid)
                    if not bz:
                        bz = item.get("bz") or ""
                else:
                    fid = int(item)
                    if fid:
                        ids.append(fid)
            except Exception:
                pass
        return {"bz": bz, "ids": ids}
    return empty


# 向下兼容别名（代码中其他地方如有调用旧函数名不会报错）
_parse_attachment_items = _parse_attachment_field


def _build_attachment_files(ids: List[int]) -> List[Dict[str, Any]]:
    """将 ID 列表转换为带路径/名称的文件详情列表。"""
    from app.crud.file_center import get_file_by_id
    import os
    result = []
    for file_id_int in ids:
        try:
            file_info = get_file_by_id(int(file_id_int))
            if file_info:
                path = file_info.get("F_PATH", "") or ""
                if "\\" in path or ":" in path:
                    if "uploads" in path:
                        parts = path.split("uploads")
                        path = parts[-1].lstrip("\\/") if len(parts) > 1 else path
                    else:
                        path = os.path.basename(path)
                path = path.lstrip("/\\")
                if path.startswith("uploads/") or path.startswith("uploads\\"):
                    path = path[8:]
                path = path.replace("\\", "/")
                result.append({
                    "id": int(file_id_int),
                    "path": path,
                    "name": file_info.get("F_ORIGINAL_NAME") or "",
                })
        except Exception:
            pass
    return result


def _format_attachment_field(raw) -> Dict[str, Any]:
    """将附件字段原始值转换为 API 响应格式 {"bz": str, "files": [...]}。"""
    parsed = _parse_attachment_field(raw)
    return {"bz": parsed["bz"], "files": _build_attachment_files(parsed["ids"])}


# 向下兼容别名（部分内部调用仍用旧函数名）
def _format_attachment_array(raw):
    """兼容旧调用：返回格式改为 {bz, files}，不再是纯列表。"""
    return _format_attachment_field(raw)


def list_culture_orders(q: Dict[str, Any], user_ktype: str | None = None,
                       user: Dict[str, Any] | None = None,
                       scope: str | None = None) -> Dict[str, Any]:
    adapter = get_adapter()
    where: List[str] = []
    params: List[Any] = []

    def like(col: str, key: str | None):
        if key:
            pred, p = adapter.like(col, key.strip())
            where.append(pred)
            params.extend(p)

    like("p.F_XM", q.get("F_XM"))
    like("p.F_BRBH", q.get("F_BRBH"))
    like("p.F_ZZID", q.get("F_ZZID"))
    like("p.F_GDBH", q.get("F_GDBH"))
    like("p.F_GDLX", q.get("F_GDLX"))
    like("p.F_DC", q.get("F_DC"))
    like("p.F_DCID", q.get("F_DCID"))
    like("p.F_ZZBW", q.get("F_ZZBW"))
    like("p.F_XYZL", q.get("F_XYZL"))

    # 样本来源：多选精确匹配（IN 过滤）
    ybly_list = q.get("F_YBLY")
    if ybly_list and isinstance(ybly_list, list):
        ybly_vals = [v for v in ybly_list if v]
        if ybly_vals:
            ph = ",".join(["?"] * len(ybly_vals))
            where.append(f"p.F_YBLY IN ({ph})")
            params.extend(ybly_vals)

    # 工单状态：精确匹配
    if q.get("F_GDZT"):
        where.append("p.F_GDZT = ?")
        params.append(q["F_GDZT"].strip())
    
    # 质控状态过滤（精确匹配）
    if q.get("F_ZKZT"):
        where.append("p.F_ZKZT = ?")
        params.append(q["F_ZKZT"].strip())

    if q.get("F_PYSJ_START"):
        where.append("p.F_PYSJ >= ?")
        params.append(q["F_PYSJ_START"])
    if q.get("F_PYSJ_END"):
        where.append("p.F_PYSJ <= ?")
        params.append(q["F_PYSJ_END"])
    
    # 细胞数量范围筛选
    if q.get("F_HSSL_MIN") is not None:
        where.append("h.F_HSSL >= ?")
        params.append(q["F_HSSL_MIN"])
    if q.get("F_HSSL_MAX") is not None:
        where.append("h.F_HSSL <= ?")
        params.append(q["F_HSSL_MAX"])

    from app.core.data_scope import apply_owner_filter, SCOPE_SELF
    if user is None and user_ktype is not None and scope == SCOPE_SELF:
        user = {"F_Ktype": user_ktype}
    apply_owner_filter(where, params, scope=scope, user=user, column="p.F_SQR", match="name")

    where_sql = (" WHERE " + " AND ".join(where)) if where else ""

    # 使用COUNT子查询统计冻存和复苏记录数量
    subq_dcjl = "SELECT COUNT(1) FROM T_DCJL d WHERE d.F_GDID = p.F_ID"
    subq_fsjl = "SELECT COUNT(1) FROM T_FSJL f WHERE f.F_GDID = p.F_ID"

    base_core = f"""
SELECT p.F_ID, p.F_GDBH, p.F_ZZID, p.F_XM, p.F_BRBH, p.F_BBLX,
       p.F_DC, p.F_DCID, p.F_ZZBW, p.F_XYZL, p.F_QYFS, p.F_YBLY, p.F_YBGG,
       p.F_SQR, p.F_PYSJ, p.F_FJ, p.F_CJSJ,
       p.F_GDLX,
       p.F_GDZT,
       p.F_ZKZT AS F_SHZT,
       p.F_AZ,
       p.F_BL, p.F_CX, p.F_QT, p.F_ZDYXH, p.F_SYJL, p.F_JHCDCS,
       p.F_CDQXBZS, p.F_FSQXBZS,
       p.F_BHGLX,
       ({subq_dcjl}) AS F_DCJL,
       ({subq_fsjl}) AS F_FSJL,
       CASE WHEN EXISTS (SELECT 1 FROM T_YCL y WHERE y.F_GDID = p.F_ID) THEN 1 ELSE 0 END AS F_YCL,
       p.F_PARENT_ID,
       p.F_WCSJ,
       p.F_BHGSJ,
       p.F_PIPELINE_JM,
       p.F_PIPELINE_LX,
       h.F_HSSJ,
       h.F_HSSL,
       mx.F_JZKBS
FROM T_PYGD p
LEFT JOIN (
  SELECT t1.F_GDID, t1.F_HSSJ, t1.F_HSSL
  FROM T_HSJL t1
  INNER JOIN (SELECT F_GDID, MAX(F_ID) AS max_id FROM T_HSJL GROUP BY F_GDID) t2
    ON t1.F_GDID = t2.F_GDID AND t1.F_ID = t2.max_id
) h ON h.F_GDID = p.F_ID
LEFT JOIN (
  SELECT t1.F_GDID, t1.F_JZKBS
  FROM T_PYMX_ZZ t1
  INNER JOIN (SELECT F_GDID, MAX(F_ID) AS max_id FROM T_PYMX_ZZ GROUP BY F_GDID) t2
    ON t1.F_GDID = t2.F_GDID AND t1.F_ID = t2.max_id
) mx ON mx.F_GDID = p.F_ID
{where_sql}
"""
    base_select = base_core + " ORDER BY" + _build_zdyxh_order(adapter, "p") + " p.F_ID DESC"
    count_sql = f"SELECT COUNT(1) FROM ( {base_core} ) t"
    sql, extra = adapter.paginate(base_select, skip=q.get("skip") or 0, limit=q.get("limit"))

    with adapter.connect() as conn:
        cur = conn.cursor()
        cur.execute(count_sql, tuple(params))
        total = int(cur.fetchone()[0])
        cur.execute(sql, tuple(params + extra))
        rows = cur.fetchall()
        cols = [c[0] for c in cur.description]
        items = [dict(zip(cols, r)) for r in rows]

        def _format(it: Dict[str, Any]) -> None:
            # 格式化时间字段
            for k in ("F_PYSJ", "F_CJSJ", "F_HSSJ", "F_WCSJ", "F_BHGSJ"):
                v = it.get(k)
                if v is not None:
                    try:
                        it[k] = v.strftime("%Y-%m-%d %H:%M:%S") if hasattr(v, "strftime") else str(v)
                    except Exception:
                        it[k] = str(v)
            
            # 转换数值字段为浮点数（处理 DECIMAL 等数据库类型可能返回字符串的情况）
            for k in ("F_HSSL",):
                v = it.get(k)
                if v is not None:
                    try:
                        it[k] = float(v)
                    except (ValueError, TypeError):
                        it[k] = None
            
            # 计算培养天数 F_PYTS
            # - 已完成：F_WCSJ - F_PYSJ（天）
            # - 不合格：F_BHGSJ - F_PYSJ（天），不足 1 天按 1 天计
            # - 已创建：今天 - F_PYSJ（天），不足 1 天按 1 天计
            # - 其他状态：None
            pyts = None
            pysj = it.get("F_PYSJ")
            gdzt = (it.get("F_GDZT") or "").strip()
            if pysj:
                try:
                    from datetime import date as _date
                    pysj_dt = (
                        datetime.fromisoformat(pysj) if isinstance(pysj, str)
                        else (pysj if hasattr(pysj, "date") else None)
                    )
                    if pysj_dt is None and isinstance(pysj, str):
                        try:
                            pysj_dt = datetime.strptime(pysj, "%Y-%m-%d %H:%M:%S")
                        except Exception:
                            pass
                    if pysj_dt:
                        if gdzt == "已完成":
                            wcsj = it.get("F_WCSJ")
                            if wcsj:
                                wcsj_dt = (
                                    datetime.fromisoformat(wcsj) if isinstance(wcsj, str)
                                    else (wcsj if hasattr(wcsj, "date") else None)
                                )
                                if wcsj_dt is None and isinstance(wcsj, str):
                                    try:
                                        wcsj_dt = datetime.strptime(wcsj, "%Y-%m-%d %H:%M:%S")
                                    except Exception:
                                        pass
                                if wcsj_dt:
                                    pyts = (wcsj_dt.date() - pysj_dt.date()).days
                        elif gdzt == "不合格":
                            bhgsj = it.get("F_BHGSJ")
                            if bhgsj:
                                bhgsj_dt = (
                                    datetime.fromisoformat(bhgsj) if isinstance(bhgsj, str)
                                    else (bhgsj if hasattr(bhgsj, "date") else None)
                                )
                                if bhgsj_dt is None and isinstance(bhgsj, str):
                                    try:
                                        bhgsj_dt = datetime.strptime(bhgsj, "%Y-%m-%d %H:%M:%S")
                                    except Exception:
                                        pass
                                if bhgsj_dt:
                                    pyts = _normalize_list_pyts((bhgsj_dt.date() - pysj_dt.date()).days)
                        elif gdzt == "已创建":
                            pyts = _normalize_list_pyts((_date.today() - pysj_dt.date()).days)
                except Exception:
                    pyts = None
            it["F_PYTS"] = pyts

            # 流水线 ID：库里可能是空串，统一成 int / List[int] / None
            it["F_PIPELINE_JM"] = _coerce_pipeline_jm(it.get("F_PIPELINE_JM"))
            it["F_PIPELINE_LX"] = _deserialize_lx(it.get("F_PIPELINE_LX"))
            
            if "F_YCL" in it:
                try:
                    it["F_YCL"] = bool(int(it.get("F_YCL"))) if it.get("F_YCL") is not None else False
                except Exception:
                    it["F_YCL"] = False
            
            # 格式化冻存记录数量（无记录则返回0，有记录则返回数量）
            try:
                it["F_SFDC"] = int(it.get("F_DCJL") or 0)
            except Exception:
                it["F_SFDC"] = 0
            
            # 格式化复苏记录数量（无记录则返回0，有记录则返回数量）
            try:
                it["F_SFFS"] = int(it.get("F_FSJL") or 0)
            except Exception:
                it["F_SFFS"] = 0
            
            # 格式化病理/测序/其他附件（统一为 {bz, files} 格式）
            for _af in ("F_BL", "F_CX", "F_QT"):
                _af_raw = it.get(_af)
                it[_af] = _format_attachment_field(_af_raw) if _af_raw else {"bz": "", "files": []}

            fj_raw = it.get("F_FJ")
            if fj_raw is not None and not isinstance(fj_raw, list):
                try:
                    arr = json.loads(fj_raw) if isinstance(fj_raw, str) else fj_raw
                except Exception:
                    arr = None
                details: List[Dict[str, Any]] = []
                if isinstance(arr, list) and arr:
                    try:
                        from app.crud.file_center import get_files_by_ids
                        ids: List[int] = []
                        for a in arr:
                            try:
                                ids.append(int(a))
                            except Exception:
                                pass
                        if ids:
                            files = get_files_by_ids(ids)
                            id_to_row = {int(f.get("F_ID")): f for f in files if f.get("F_ID") is not None}
                            for a in arr:
                                try:
                                    aid = int(a)
                                    row = id_to_row.get(aid)
                                    if row:
                                        details.append({
                                            "id": aid,
                                            "path": row.get("F_PATH"),
                                            "name": row.get("F_ORIGINAL_NAME"),
                                        })
                                except Exception:
                                    pass
                    except Exception:
                        details = []
                it["F_FJ"] = details
            elif isinstance(fj_raw, list):
                it["F_FJ"] = fj_raw

        for it in items:
            _format(it)

        # 是否需要构建树
        parent_present = any(it.get("F_PARENT_ID") for it in items)
        val = q.get("asTree")
        
        # 判断 asTree 的值
        if val is None:
            # 没传参数，使用智能判断：有父子关系就用树状结构
            as_tree = parent_present
        elif isinstance(val, bool):
            # 传了布尔值，强制使用传入的值
            as_tree = val
        else:
            # 传了字符串，解析为布尔值
            as_tree = str(val).strip().lower() in ("1", "true", "yes")
        
        if not as_tree:
            for it in items:
                if it.get("F_PARENT_ID") is not None:
                    try:
                        it["F_PARENT_ID"] = int(it["F_PARENT_ID"])
                    except Exception:
                        it["F_PARENT_ID"] = None
            return {"items": items, "total": total}

        # 构建 id -> item 映射，并补齐缺失的父节点
        id_to_item: Dict[int, Dict[str, Any]] = {int(it.get("F_ID")): it for it in items}
        parent_map: Dict[int, Optional[int]] = {}
        for it in items:
            pid = it.get("F_PARENT_ID")
            parent_map[int(it.get("F_ID"))] = int(pid) if pid is not None else None

        missing_parent_ids = sorted({pid for pid in parent_map.values() if pid and pid not in id_to_item})
        if missing_parent_ids:
            qmarks = ",".join(["?" for _ in missing_parent_ids])
            # 构建补齐父节点的查询，同样应用 F_HSSL 过滤条件
            parent_where_clauses = [f"p.F_ID IN ({qmarks})"]
            parent_params = list(missing_parent_ids)
            
            # 应用与主查询相同的 F_HSSL 过滤条件
            if q.get("F_HSSL_MIN") is not None:
                parent_where_clauses.append("h.F_HSSL >= ?")
                parent_params.append(q["F_HSSL_MIN"])
            if q.get("F_HSSL_MAX") is not None:
                parent_where_clauses.append("h.F_HSSL <= ?")
                parent_params.append(q["F_HSSL_MAX"])
            
            parent_where_sql = " AND ".join(parent_where_clauses)
            parent_query = (
                f"SELECT p.F_ID, p.F_GDBH, p.F_ZZID, p.F_XM, p.F_BRBH, p.F_BBLX,"
                f" p.F_DC, p.F_DCID, p.F_ZZBW, p.F_XYZL, p.F_QYFS, p.F_YBLY, p.F_YBGG,"
                f" p.F_SQR, p.F_PYSJ, p.F_FJ, p.F_CJSJ, p.F_GDLX, p.F_GDZT,"
                f" p.F_ZKZT AS F_SHZT, ({subq_dcjl}) AS F_DCJL, ({subq_fsjl}) AS F_FSJL,"
                f" CASE WHEN EXISTS (SELECT 1 FROM T_YCL y WHERE y.F_GDID = p.F_ID) THEN 1 ELSE 0 END AS F_YCL,"
                f" p.F_PARENT_ID, p.F_WCSJ, p.F_PIPELINE_JM, p.F_PIPELINE_LX,"
                f" h.F_HSSJ, h.F_HSSL"
                f" FROM T_PYGD p"
                f" LEFT JOIN ("
                f"   SELECT t1.F_GDID, t1.F_HSSJ, t1.F_HSSL FROM T_HSJL t1"
                f"   INNER JOIN (SELECT F_GDID, MAX(F_ID) AS max_id FROM T_HSJL GROUP BY F_GDID) t2"
                f"     ON t1.F_GDID = t2.F_GDID AND t1.F_ID = t2.max_id"
                f" ) h ON h.F_GDID = p.F_ID"
                f" WHERE {parent_where_sql}"
            )
            cur.execute(parent_query, tuple(parent_params))
            cols2 = [c[0] for c in cur.description]
            parent_rows = [dict(zip(cols2, r)) for r in cur.fetchall()]
            for row in parent_rows:
                _format(row)
                pid = row.get("F_PARENT_ID")
                parent_map[int(row.get("F_ID"))] = int(pid) if pid is not None else None
                id_to_item[int(row.get("F_ID"))] = row
                items.append(row)

        # 初始化 children 数组并构建树
        for it in id_to_item.values():
            it.setdefault("children", [])
            if it.get("F_PARENT_ID") is not None:
                try:
                    it["F_PARENT_ID"] = int(it["F_PARENT_ID"])
                except Exception:
                    it["F_PARENT_ID"] = None
        roots: List[Dict[str, Any]] = []
        for rid, it in id_to_item.items():
            pid = parent_map.get(rid)
            if pid and pid in id_to_item:
                id_to_item[pid].setdefault("children", []).append(it)
            else:
                roots.append(it)

        return {"items": roots, "total": total}


def create_passage_order_from_order(
    orig_gdid: int,
    zzbw: str | None = None,
    ybgg: str | None = None,
    pysj_str: str | None = None,
    file_ids: List[int] | None = None,
    creator_code: str = "",
    pipeline_jm: int | None = None,
    pipeline_lx: List[int] | int | None = None,
    cdqxbzs: str | None = None,
) -> Dict[str, Any]:
    from app.crud.field_config import assert_page
    assert_page("culture_passage", {
        "F_ZZBW": zzbw,
        "F_YBGG": ybgg,
        "F_PYSJ": pysj_str,
        "F_PIPELINE_JM": pipeline_jm,
        "F_PIPELINE_LX": pipeline_lx,
    }, limit=["F_ZZBW", "F_YBGG", "F_PYSJ", "F_PIPELINE_JM", "F_PIPELINE_LX"])
    adapter = get_adapter()
    with adapter.connect() as conn:
        cur = conn.cursor()
        sql = "SELECT F_ZZID, F_XM, F_BRBH, F_BBLX, F_DC, F_DCID, F_ZZBW, F_XYZL, F_YBGG, F_SQR, F_GDLX, F_FJ, F_AZ, F_QYFS, F_PIPELINE_JM, F_PIPELINE_LX, F_XMBH FROM T_PYGD WHERE F_ID = ? ORDER BY F_ID DESC"
        paginated_sql, extra = adapter.paginate(sql, 0, 1)
        cur.execute(paginated_sql, (orig_gdid,) + tuple(extra))
        row = cur.fetchone()
        if not row:
            raise ValueError("原工单不存在")
        cols = [c[0] for c in cur.description]
        base = dict(zip(cols, row))

        zzid = base.get("F_ZZID")
        if not zzid:
            raise ValueError("原工单缺少标本号")

        old_dc = (base.get("F_DC") or "P0").strip()
        try:
            num = int(old_dc[1:]) if old_dc.upper().startswith("P") else 0
        except Exception:
            num = 0
        new_dc = f"P{num + 1}"
        new_dcid = _generate_culture_dcid(zzid, base.get("F_BBLX"), new_dc)
        
        gdbh = _generate_culture_gdbh(
            zzid,
            new_dc,
            brbh=base.get("F_BRBH"),
            bblx=base.get("F_BBLX"),
            xm=base.get("F_XM"),
        )
        pysj = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        # 附件：不继承，改为按入参 file_ids 写入；未提供则为空
        fj_json = None
        if file_ids:
            try:
                fj_json = json.dumps([int(i) for i in file_ids], ensure_ascii=False)
            except Exception:
                fj_json = None

        # 字段覆盖：F_ZZBW、F_YBGG 优先用入参（未提供则继承原值）；F_PYSJ 支持入参，否则当前时间
        target_zzbw = zzbw if zzbw is not None else base.get("F_ZZBW")
        target_ybgg = ybgg if ybgg is not None else base.get("F_YBGG")
        target_pysj = pysj
        if pysj_str:
            try:
                try:
                    target_pysj = datetime.fromisoformat(pysj_str)
                except Exception:
                    target_pysj = datetime.strptime(pysj_str, "%Y-%m-%d %H:%M:%S")
            except Exception:
                target_pysj = pysj

        cur.execute(
            """
            INSERT INTO T_PYGD (
                F_XM, F_BRBH, F_ZZID, F_BBLX,
                F_DC, F_DCID, F_ZZBW, F_XYZL, F_YBGG, F_YBLY,
                F_SQR, F_PYSJ, F_FJ, F_GDBH, F_GDLX, F_PARENT_ID, F_GDZT, F_AZ,
                F_QYFS, F_PIPELINE_JM, F_PIPELINE_LX, F_XMBH, F_CJSJ, F_CDQXBZS
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                base.get("F_XM"), base.get("F_BRBH"), zzid, base.get("F_BBLX"),
                new_dc, new_dcid, target_zzbw, base.get("F_XYZL"), target_ybgg, "传代",
                base.get("F_SQR"), target_pysj, fj_json, gdbh, base.get("F_GDLX"), orig_gdid, "已创建",
                base.get("F_AZ"),                                              # 继承父工单的癌种
                base.get("F_QYFS"),                                            # 继承父工单的取样方式
                pipeline_jm if pipeline_jm is not None else base.get("F_PIPELINE_JM"),
                _serialize_lx(pipeline_lx if pipeline_lx is not None else _deserialize_lx(base.get("F_PIPELINE_LX"))),
                base.get("F_XMBH"),  # 项目编号（快照，继承自父工单）
                datetime.now(),
                _coerce_cell_total(cdqxbzs),
            ),
        )
        
        # 查询刚插入的记录ID（用 F_GDBH 唯一标识，避免 F_DCID 重复问题）
        try:
            lookup_sql = "SELECT F_ID FROM T_PYGD WHERE F_GDBH = ?"
            cur.execute(lookup_sql, (gdbh,))
            row_new = cur.fetchone()
            new_id = int(row_new[0]) if row_new else None
        except Exception:
            new_id = None

        conn.commit()
    
    # ========== 创建质控审核记录（在事务外执行） ==========
    import logging
    logger = logging.getLogger(__name__)
    
    logger.info(f"[传代工单创建质控] 准备创建质控记录 - new_id: {new_id}, zzid: {zzid}, creator_code: '{creator_code}'")
    
    qc_audit_id = None
    if new_id and zzid and creator_code is not None:
        try:
            from app.crud.qc_audit import create_qc_audit_record, get_sample_basic_info
            
            # 获取样本基本信息
            sample_info = get_sample_basic_info(zzid)
            
            # 传代工单创建的变更信息
            changes = {
                "操作": "创建传代培养工单",
                "关键信息": {
                    "原工单ID": orig_gdid,
                    "新工单ID": new_id,
                    "工单编号": gdbh,
                    "标本号": zzid,
                    "代次": new_dc,
                    "代次ID": new_dcid,
                    "组织部位": target_zzbw,
                    "样本规格": target_ybgg,
                    "工单状态": "已创建",
                    "样本来源": "传代"
                }
            }
            
            # 如果有培养时间，添加到变更信息
            if target_pysj:
                try:
                    pysj_str_formatted = target_pysj.strftime("%Y-%m-%d %H:%M:%S") if hasattr(target_pysj, 'strftime') else str(target_pysj)
                    changes["关键信息"]["开始培养日期"] = pysj_str_formatted
                except Exception:
                    pass
            
            # ✅ 获取新创建的传代工单完整详情数据
            new_detail_data = None
            try:
                new_detail_data = get_culture_order_detail(new_id)
                logger.info(f"[传代工单创建质控] 成功获取新工单详情数据用于备份，工单ID: {new_id}")
            except Exception as e:
                logger.warning(f"[传代工单创建质控] 获取新工单详情数据失败: {e}")
            
            # 创建质控审核记录
            qc_audit_id = create_qc_audit_record(
                F_ZKJD="传代",
                F_BGLX="新增",
                F_ZZID=zzid,
                F_CHANGES=changes,
                F_JLR=creator_code or "系统",
                F_GDID=new_id,
                F_XM=sample_info.get("F_XM"),
                F_BRBH=sample_info.get("F_BRBH"),
                F_ZZBW=sample_info.get("F_ZZBW"),
                F_YBLY="传代",  # 使用工单中设置的 F_YBLY 值（来自 T_PYGD 表）
                F_GDBH=gdbh,
                F_DCID=new_dcid,
                F_DC=new_dc,
                F_PYSJ=target_pysj if isinstance(target_pysj, datetime) else None,
                F_SQR=base.get("F_SQR"),
                F_OLD_DATA=new_detail_data  # ✅ 新增操作记录新增后的完整数据
            )
            logger.info(f"[传代工单创建质控] 质控记录创建成功 - qc_audit_id: {qc_audit_id}")
        except Exception as e:
            import traceback
            logger.error(f"[传代工单创建质控] 质控记录创建失败: {e}\n{traceback.format_exc()}")
    else:
        logger.warning(f"[传代工单创建质控] 跳过质控记录创建 - new_id: {new_id}, zzid: {zzid}, creator_code: '{creator_code}'")
    
    result = {"F_ID": new_id, "F_GDBH": gdbh, "F_ZZID": zzid, "F_DCID": new_dcid, "F_DC": new_dc}
    if qc_audit_id:
        result["qc_audit_id"] = qc_audit_id

    # ========== 流水线模板初始化 ==========
    _actual_jm = pipeline_jm if pipeline_jm is not None else base.get("F_PIPELINE_JM")
    _actual_lx = pipeline_lx if pipeline_lx is not None else _deserialize_lx(base.get("F_PIPELINE_LX"))
    if new_id and (_actual_jm or _actual_lx):
        try:
            from app.crud.pipeline_init import apply_pipeline_to_order
            init_result = apply_pipeline_to_order(new_id, creator_code or "")
            result["pipeline_init"] = init_result
            logger.info(f"[传代工单流水线] 工单 {new_id} 流水线初始化完成: {init_result.get('initialized')}")
        except Exception as init_err:
            logger.error(f"[传代工单流水线] 工单 {new_id} 初始化失败，正在回滚: {init_err}", exc_info=True)
            try:
                from app.db.factory import get_adapter as _get_adapter
                with _get_adapter().connect() as _conn:
                    _conn.cursor().execute("DELETE FROM T_PYGD WHERE F_ID = ?", (new_id,))
                    _conn.commit()
            except Exception as del_err:
                logger.error(f"[传代工单流水线] 回滚删除工单 {new_id} 失败: {del_err}")
            raise ValueError(f"传代工单创建成功但流水线初始化失败，操作已回滚。原因：{init_err}") from init_err

    _overlay_incoming_cell_total(new_id, "F_CDQXBZS", cdqxbzs)
    return result


def create_thaw_order_from_order(
    orig_gdid: int,
    zzbw: str | None = None,
    ybgg: str | None = None,
    pysj_str: str | None = None,
    file_ids: List[int] | None = None,
    creator_code: str = "",
    pipeline_jm: int | None = None,
    pipeline_lx: List[int] | int | None = None,
    fsqxbzs: str | None = None,
) -> Dict[str, Any]:
    adapter = get_adapter()
    with adapter.connect() as conn:
        cur = conn.cursor()
        sql = "SELECT F_ZZID, F_XM, F_BRBH, F_BBLX, F_DC, F_DCID, F_ZZBW, F_XYZL, F_YBGG, F_SQR, F_GDLX, F_AZ, F_PIPELINE_JM, F_PIPELINE_LX, F_XMBH FROM T_PYGD WHERE F_ID = ? ORDER BY F_ID DESC"
        paginated_sql, extra = adapter.paginate(sql, 0, 1)
        cur.execute(paginated_sql, (orig_gdid,) + tuple(extra))
        row = cur.fetchone()
        if not row:
            raise ValueError("原工单不存在")
        cols = [c[0] for c in cur.description]
        base = dict(zip(cols, row))

        zzid = base.get("F_ZZID")
        if not zzid:
            raise ValueError("原工单缺少标本号")

        old_dc = (base.get("F_DC") or "P0").strip()
        try:
            num = int(old_dc[1:]) if old_dc.upper().startswith("P") else 0
        except Exception:
            num = 0
        new_dc = f"P{num + 1}"
        new_dcid = _generate_culture_dcid(zzid, base.get("F_BBLX"), new_dc)
        
        gdbh = _generate_culture_gdbh(
            zzid,
            new_dc,
            brbh=base.get("F_BRBH"),
            bblx=base.get("F_BBLX"),
            xm=base.get("F_XM"),
        )
        pysj = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        
        # 判断样本来源：P0原代 → "原代复苏"；传代工单 → "传代复苏"
        ybly = "原代复苏" if old_dc.upper() == "P0" else "传代复苏"

        fj_json = None
        if file_ids:
            try:
                fj_json = json.dumps([int(i) for i in file_ids], ensure_ascii=False)
            except Exception:
                fj_json = None

        target_zzbw = zzbw if zzbw is not None else base.get("F_ZZBW")
        target_ybgg = ybgg if ybgg is not None else base.get("F_YBGG")
        target_pysj = pysj
        if pysj_str:
            try:
                try:
                    target_pysj = datetime.fromisoformat(pysj_str)
                except Exception:
                    target_pysj = datetime.strptime(pysj_str, "%Y-%m-%d %H:%M:%S")
            except Exception:
                target_pysj = pysj

        cur.execute(
            """
            INSERT INTO T_PYGD (
                F_XM, F_BRBH, F_ZZID, F_BBLX,
                F_DC, F_DCID, F_ZZBW, F_XYZL, F_YBGG, F_YBLY,
                F_SQR, F_PYSJ, F_FJ, F_GDBH, F_GDLX, F_PARENT_ID, F_GDZT, F_AZ,
                F_PIPELINE_JM, F_PIPELINE_LX, F_XMBH, F_CJSJ, F_FSQXBZS
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                base.get("F_XM"), base.get("F_BRBH"), zzid, base.get("F_BBLX"),
                new_dc, new_dcid, target_zzbw, base.get("F_XYZL"), target_ybgg, ybly,
                base.get("F_SQR"), target_pysj, fj_json, gdbh, base.get("F_GDLX"), orig_gdid, "已创建",
                base.get("F_AZ"),                                              # 继承父工单的癌种
                # 复苏工单不继承父工单的流水线配置（字段内容有差异，不适合直接继承）：
                # 仅使用调用方显式传入的 pipeline_jm/pipeline_lx，不传则留空
                pipeline_jm,
                _serialize_lx(pipeline_lx),
                base.get("F_XMBH"),  # 项目编号（快照，继承自父工单）
                datetime.now(),
                _coerce_cell_total(fsqxbzs),
            ),
        )
        
        # 查询刚插入的记录ID（用 F_GDBH 唯一标识，避免 F_DCID 重复问题）
        try:
            lookup_sql = "SELECT F_ID FROM T_PYGD WHERE F_GDBH = ?"
            cur.execute(lookup_sql, (gdbh,))
            row_new = cur.fetchone()
            new_id = int(row_new[0]) if row_new else None
        except Exception:
            new_id = None

        conn.commit()
    
    # ========== 创建质控审核记录（在事务外执行） ==========
    import logging
    logger = logging.getLogger(__name__)
    
    logger.info(f"[复苏工单创建质控] 准备创建质控记录 - new_id: {new_id}, zzid: {zzid}, creator_code: '{creator_code}'")
    
    qc_audit_id = None
    if new_id and zzid and creator_code is not None:
        try:
            from app.crud.qc_audit import create_qc_audit_record, get_sample_basic_info
            
            # 获取样本基本信息
            sample_info = get_sample_basic_info(zzid)
            
            # 复苏工单创建的变更信息
            changes = {
                "操作": "创建复苏培养工单",
                "关键信息": {
                    "原工单ID": orig_gdid,
                    "新工单ID": new_id,
                    "工单编号": gdbh,
                    "标本号": zzid,
                    "代次": new_dc,
                    "代次ID": new_dcid,
                    "组织部位": target_zzbw,
                    "样本规格": target_ybgg,
                    "工单状态": "已创建",
                    "样本来源": ybly
                }
            }
            
            # 如果有培养时间，添加到变更信息
            if target_pysj:
                try:
                    pysj_str_formatted = target_pysj.strftime("%Y-%m-%d %H:%M:%S") if hasattr(target_pysj, 'strftime') else str(target_pysj)
                    changes["关键信息"]["开始培养日期"] = pysj_str_formatted
                except Exception:
                    pass
            
            # ✅ 获取新创建的复苏工单完整详情数据
            new_detail_data = None
            try:
                new_detail_data = get_culture_order_detail(new_id)
                logger.info(f"[复苏工单创建质控] 成功获取新工单详情数据用于备份，工单ID: {new_id}")
            except Exception as e:
                logger.warning(f"[复苏工单创建质控] 获取新工单详情数据失败: {e}")
            
            # 创建质控审核记录
            qc_audit_id = create_qc_audit_record(
                F_ZKJD="复苏",
                F_BGLX="新增",
                F_ZZID=zzid,
                F_CHANGES=changes,
                F_JLR=creator_code or "系统",
                F_GDID=new_id,
                F_XM=sample_info.get("F_XM"),
                F_BRBH=sample_info.get("F_BRBH"),
                F_ZZBW=sample_info.get("F_ZZBW"),
                F_YBLY=ybly,  # 使用工单中设置的 F_YBLY 值（复苏 或 原代复苏）
                F_GDBH=gdbh,
                F_DCID=new_dcid,
                F_DC=new_dc,
                F_PYSJ=target_pysj if isinstance(target_pysj, datetime) else None,
                F_SQR=base.get("F_SQR"),
                F_OLD_DATA=new_detail_data  # ✅ 新增操作记录新增后的完整数据
            )
            logger.info(f"[复苏工单创建质控] 质控记录创建成功 - qc_audit_id: {qc_audit_id}")
        except Exception as e:
            import traceback
            logger.error(f"[复苏工单创建质控] 质控记录创建失败: {e}\n{traceback.format_exc()}")
    else:
        logger.warning(f"[复苏工单创建质控] 跳过质控记录创建 - new_id: {new_id}, zzid: {zzid}, creator_code: '{creator_code}'")
    
    result = {"F_ID": new_id, "F_GDBH": gdbh, "F_ZZID": zzid, "F_DCID": new_dcid, "F_DC": new_dc}
    if qc_audit_id:
        result["qc_audit_id"] = qc_audit_id

    # ========== 流水线模板初始化 ==========
    # 复苏工单不继承父工单的流水线，仅当调用方显式传入 pipeline_jm/pipeline_lx 时才初始化
    if new_id and (pipeline_jm or pipeline_lx):
        try:
            from app.crud.pipeline_init import apply_pipeline_to_order
            init_result = apply_pipeline_to_order(new_id, creator_code or "")
            result["pipeline_init"] = init_result
            logger.info(f"[复苏工单流水线] 工单 {new_id} 流水线初始化完成: {init_result.get('initialized')}")
        except Exception as init_err:
            logger.error(f"[复苏工单流水线] 工单 {new_id} 初始化失败，正在回滚: {init_err}", exc_info=True)
            try:
                from app.db.factory import get_adapter as _get_adapter
                with _get_adapter().connect() as _conn:
                    _conn.cursor().execute("DELETE FROM T_PYGD WHERE F_ID = ?", (new_id,))
                    _conn.commit()
            except Exception as del_err:
                logger.error(f"[复苏工单流水线] 回滚删除工单 {new_id} 失败: {del_err}")
            raise ValueError(f"复苏工单创建成功但流水线初始化失败，操作已回滚。原因：{init_err}") from init_err

    _overlay_incoming_cell_total(new_id, "F_FSQXBZS", fsqxbzs)
    return result


def create_thaw_order_from_ybzj(
    zzid: str,
    zzbw: str | None = None,
    ybgg: str | None = None,
    pysj_str: str | None = None,
    file_ids: List[int] | None = None,
    creator_code: str = "",
    pipeline_jm: int | None = None,
    pipeline_lx: List[int] | int | None = None,
    fsqxbzs: str | None = None,
) -> Dict[str, Any]:
    """样本管未关联任何培养工单（即直接由标本登记 T_YBZJ 分装冻存、从未走过培养流程）时，
    以标本登记信息为基础直接创建一张复苏培养工单。

    规则：
    - 由于此前不存在任何培养工单，本次创建视为该标本的"第一个"培养工单：
      代次 F_DC 固定为 'P0'，样本来源 F_YBLY 固定为 '原代复苏'
      （与 create_thaw_order_from_order 中"复苏 P0 工单 → 原代复苏"的判定语义一致）
    - F_PARENT_ID 留空（无父工单可关联）
    - 流水线不做任何继承（本来就没有父工单流水线可继承），仅使用调用方显式传入的
      pipeline_jm/pipeline_lx，不传则留空，与 create_thaw_order_from_order 行为一致
    - 不抛出异常给出库同步等 best-effort 调用方之外的场景：标本登记不存在时抛 ValueError
    """
    adapter = get_adapter()
    with adapter.connect() as conn:
        cur = conn.cursor()
        sql = "SELECT * FROM T_YBZJ WHERE F_ZZID = ? ORDER BY F_ID DESC"
        paginated_sql, extra = adapter.paginate(sql, 0, 1)
        cur.execute(paginated_sql, (zzid,) + tuple(extra))
        row = cur.fetchone()
        if not row:
            raise ValueError(f"未找到标本登记记录: {zzid}")
        cols = [c[0] for c in cur.description]
        base = dict(zip(cols, row))

        new_dc = "P0"
        new_dcid = _generate_culture_dcid(zzid, base.get("F_BBLX"), new_dc)

        gdbh = _generate_culture_gdbh(
            zzid,
            new_dc,
            brbh=base.get("F_BRBH"),
            bblx=base.get("F_BBLX"),
            xm=base.get("F_XM"),
        )
        pysj = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        ybly = "原代复苏"

        fj_json = None
        if file_ids:
            try:
                fj_json = json.dumps([int(i) for i in file_ids], ensure_ascii=False)
            except Exception:
                fj_json = None

        target_zzbw = zzbw if zzbw is not None else base.get("F_CJBW")
        target_ybgg = ybgg if ybgg is not None else base.get("F_YBGG")
        target_pysj = pysj
        if pysj_str:
            try:
                try:
                    target_pysj = datetime.fromisoformat(pysj_str)
                except Exception:
                    target_pysj = datetime.strptime(pysj_str, "%Y-%m-%d %H:%M:%S")
            except Exception:
                target_pysj = pysj

        cur.execute(
            """
            INSERT INTO T_PYGD (
                F_XM, F_BRBH, F_ZZID, F_BBLX,
                F_DC, F_DCID, F_ZZBW, F_XYZL, F_YBGG, F_YBLY,
                F_SQR, F_PYSJ, F_FJ, F_GDBH, F_GDZT, F_AZ,
                F_PIPELINE_JM, F_PIPELINE_LX, F_XMBH, F_CJSJ, F_FSQXBZS
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                base.get("F_XM"), base.get("F_BRBH"), zzid, base.get("F_BBLX"),
                new_dc, new_dcid, target_zzbw, base.get("F_XYZL"), target_ybgg, ybly,
                creator_code or None, target_pysj, fj_json, gdbh, "已创建",
                base.get("F_AZ"),
                pipeline_jm,
                _serialize_lx(pipeline_lx),
                base.get("F_XMBH"),  # 项目编号（快照，来自标本登记 T_YBZJ，SELECT * 已包含）
                datetime.now(),
                _coerce_cell_total(fsqxbzs),
            ),
        )

        try:
            lookup_sql = "SELECT F_ID FROM T_PYGD WHERE F_GDBH = ?"
            cur.execute(lookup_sql, (gdbh,))
            row_new = cur.fetchone()
            new_id = int(row_new[0]) if row_new else None
        except Exception:
            new_id = None

        conn.commit()

    logger.info(f"[标本直接复苏] 准备创建质控记录 - new_id: {new_id}, zzid: {zzid}, creator_code: '{creator_code}'")

    qc_audit_id = None
    if new_id and zzid and creator_code is not None:
        try:
            from app.crud.qc_audit import create_qc_audit_record, get_sample_basic_info

            sample_info = get_sample_basic_info(zzid)

            changes = {
                "操作": "创建复苏培养工单（标本直接复苏，无原培养工单）",
                "关键信息": {
                    "标本号": zzid,
                    "新工单ID": new_id,
                    "工单编号": gdbh,
                    "代次": new_dc,
                    "代次ID": new_dcid,
                    "组织部位": target_zzbw,
                    "样本规格": target_ybgg,
                    "工单状态": "已创建",
                    "样本来源": ybly,
                }
            }
            if target_pysj:
                try:
                    pysj_str_formatted = target_pysj.strftime("%Y-%m-%d %H:%M:%S") if hasattr(target_pysj, 'strftime') else str(target_pysj)
                    changes["关键信息"]["开始培养日期"] = pysj_str_formatted
                except Exception:
                    pass

            new_detail_data = None
            try:
                new_detail_data = get_culture_order_detail(new_id)
            except Exception as e:
                logger.warning(f"[标本直接复苏] 获取新工单详情数据失败: {e}")

            qc_audit_id = create_qc_audit_record(
                F_ZKJD="复苏",
                F_BGLX="新增",
                F_ZZID=zzid,
                F_CHANGES=changes,
                F_JLR=creator_code or "系统",
                F_GDID=new_id,
                F_XM=sample_info.get("F_XM"),
                F_BRBH=sample_info.get("F_BRBH"),
                F_ZZBW=sample_info.get("F_ZZBW"),
                F_YBLY=ybly,
                F_GDBH=gdbh,
                F_DCID=new_dcid,
                F_DC=new_dc,
                F_PYSJ=target_pysj if isinstance(target_pysj, datetime) else None,
                F_SQR=creator_code,
                F_OLD_DATA=new_detail_data,
            )
        except Exception as e:
            import traceback
            logger.error(f"[标本直接复苏] 质控记录创建失败: {e}\n{traceback.format_exc()}")
    else:
        logger.warning(f"[标本直接复苏] 跳过质控记录创建 - new_id: {new_id}, zzid: {zzid}, creator_code: '{creator_code}'")

    result = {"F_ID": new_id, "F_GDBH": gdbh, "F_ZZID": zzid, "F_DCID": new_dcid, "F_DC": new_dc}
    if qc_audit_id:
        result["qc_audit_id"] = qc_audit_id

    # 流水线不继承（本身也无父工单可继承），仅使用调用方显式传入的配置
    if new_id and (pipeline_jm or pipeline_lx):
        try:
            from app.crud.pipeline_init import apply_pipeline_to_order
            init_result = apply_pipeline_to_order(new_id, creator_code or "")
            result["pipeline_init"] = init_result
        except Exception as init_err:
            logger.error(f"[标本直接复苏] 工单 {new_id} 流水线初始化失败，正在回滚: {init_err}", exc_info=True)
            try:
                from app.db.factory import get_adapter as _get_adapter
                with _get_adapter().connect() as _conn:
                    _conn.cursor().execute("DELETE FROM T_PYGD WHERE F_ID = ?", (new_id,))
                    _conn.commit()
            except Exception as del_err:
                logger.error(f"[标本直接复苏] 回滚删除工单 {new_id} 失败: {del_err}")
            raise ValueError(f"复苏工单创建成功但流水线初始化失败，操作已回滚。原因：{init_err}") from init_err

    _overlay_incoming_cell_total(new_id, "F_FSQXBZS", fsqxbzs)
    return result


def get_culture_order_detail(rec_id: int) -> Dict[str, Any] | None:
    adapter = get_adapter()
    with adapter.connect() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT p.F_ID, p.F_GDBH, p.F_ZZID, p.F_XM, p.F_BRBH, p.F_BBLX,
                   p.F_DC, p.F_DCID, p.F_ZZBW, p.F_XYZL, p.F_QYFS, p.F_YBLY, p.F_YBGG,
                   p.F_SQR, p.F_PYSJ, p.F_FJ, p.F_CJSJ, p.F_AZ, p.F_GDZT, p.F_WCSJ, p.F_BHGSJ,
                   p.F_PIPELINE_JM, p.F_PIPELINE_LX,
                   p.F_BL, p.F_CX, p.F_QT,
                   p.F_JHCDCS,
                   p.F_CDQXBZS,
                   p.F_FSQXBZS
            FROM T_PYGD p WHERE p.F_ID = ?
            """,
            (rec_id,),
        )
        row = cur.fetchone()
        if not row:
            return None
        cols = [c[0] for c in cur.description]
        it = dict(zip(cols, row))
        for k in ("F_CDQXBZS", "F_FSQXBZS"):
            if k in it:
                it[k] = _coerce_cell_total(it.get(k))
        for k in ("F_PYSJ", "F_CJSJ", "F_WCSJ", "F_BHGSJ"):
            v = it.get(k)
            if v is not None:
                try:
                    it[k] = v.strftime("%Y-%m-%d %H:%M:%S") if hasattr(v, "strftime") else str(v)
                except Exception:
                    it[k] = str(v)
        # 计算培养天数 F_PYTS
        # - 已完成：F_WCSJ - F_PYSJ（天）
        # - 不合格：F_BHGSJ - F_PYSJ（天），不足 1 天按 1 天计
        # - 已创建：今天 - F_PYSJ（天）
        try:
            from datetime import date as _date
            pysj_raw = it.get("F_PYSJ")
            gdzt = (it.get("F_GDZT") or "").strip()
            pyts = None
            if pysj_raw:
                _pysj_dt = None
                try:
                    _pysj_dt = datetime.fromisoformat(pysj_raw) if isinstance(pysj_raw, str) else (pysj_raw if hasattr(pysj_raw, "date") else None)
                    if _pysj_dt is None and isinstance(pysj_raw, str):
                        _pysj_dt = datetime.strptime(pysj_raw, "%Y-%m-%d %H:%M:%S")
                except Exception:
                    pass
                if _pysj_dt:
                    if gdzt == "已完成":
                        wcsj_raw = it.get("F_WCSJ")
                        if wcsj_raw:
                            _wcsj_dt = None
                            try:
                                _wcsj_dt = datetime.fromisoformat(wcsj_raw) if isinstance(wcsj_raw, str) else (wcsj_raw if hasattr(wcsj_raw, "date") else None)
                                if _wcsj_dt is None and isinstance(wcsj_raw, str):
                                    _wcsj_dt = datetime.strptime(wcsj_raw, "%Y-%m-%d %H:%M:%S")
                            except Exception:
                                pass
                            if _wcsj_dt:
                                pyts = (_wcsj_dt.date() - _pysj_dt.date()).days
                    elif gdzt == "不合格":
                        bhgsj_raw = it.get("F_BHGSJ")
                        if bhgsj_raw:
                            _bhgsj_dt = None
                            try:
                                _bhgsj_dt = datetime.fromisoformat(bhgsj_raw) if isinstance(bhgsj_raw, str) else (bhgsj_raw if hasattr(bhgsj_raw, "date") else None)
                                if _bhgsj_dt is None and isinstance(bhgsj_raw, str):
                                    _bhgsj_dt = datetime.strptime(bhgsj_raw, "%Y-%m-%d %H:%M:%S")
                            except Exception:
                                pass
                            if _bhgsj_dt:
                                pyts = _normalize_list_pyts((_bhgsj_dt.date() - _pysj_dt.date()).days)
                    elif gdzt == "已创建":
                        pyts = (_date.today() - _pysj_dt.date()).days
            it["F_PYTS"] = pyts
        except Exception:
            it["F_PYTS"] = None
        it["F_PIPELINE_JM"] = _coerce_pipeline_jm(it.get("F_PIPELINE_JM"))
        it["F_PIPELINE_LX"] = _deserialize_lx(it.get("F_PIPELINE_LX"))
        # F_FJ：将ID数组JSON转换为详情数组（带静态地址）
        fj_raw = it.get("F_FJ")
        if fj_raw is not None:
            try:
                arr = json.loads(fj_raw) if isinstance(fj_raw, str) else fj_raw
            except Exception:
                arr = None
            details: List[Dict[str, Any]] = []
            if isinstance(arr, list) and arr:
                try:
                    from app.crud.file_center import get_files_by_ids
                    import os
                    ids: List[int] = []
                    for a in arr:
                        try:
                            ids.append(int(a))
                        except Exception:
                            pass
                    if ids:
                        files = get_files_by_ids(ids)
                        id_to_row = {int(f.get("F_ID")): f for f in files if f.get("F_ID") is not None}
                        for a in arr:
                            try:
                                aid = int(a)
                                row = id_to_row.get(aid)
                                if row:
                                    # 获取F_PATH并清理为相对路径
                                    path = row.get("F_PATH", "")
                                    
                                    # 如果是Windows绝对路径，提取相对路径部分
                                    if "\\" in path or ":" in path:
                                        if "uploads" in path:
                                            parts = path.split("uploads")
                                            if len(parts) > 1:
                                                path = parts[-1].lstrip("\\/")
                                        else:
                                            path = os.path.basename(path)
                                    
                                    # 移除开头的斜杠和 uploads/ 前缀
                                    path = path.lstrip("/\\")
                                    if path.startswith("uploads/") or path.startswith("uploads\\"):
                                        path = path[8:]
                                    
                                    # 统一使用正斜杠
                                    path = path.replace("\\", "/")
                                    
                                    details.append({
                                        "id": aid,
                                        "path": path,
                                        "name": row.get("F_ORIGINAL_NAME"),
                                    })
                            except Exception:
                                pass
                except Exception:
                    details = []
            it["F_FJ"] = details
        # 格式化病理/测序/其他附件（统一为 {bz, files} 格式）
        for _field in ("F_BL", "F_CX", "F_QT"):
            _raw = it.get(_field)
            it[_field] = _format_attachment_field(_raw) if _raw else {"bz": "", "files": []}
        return it


def _coerce_cell_total(val: Any) -> Optional[str]:
    """传代前/复苏前细胞总数：按字符串原样保存（列已是 TEXT）。"""
    from app.utils.field_coerce import coerce_xbsl_to_str
    return coerce_xbsl_to_str(val)


def _overlay_incoming_cell_total(gdid: int | None, column: str, value: str | None) -> None:
    """流水线模板同步之后，用本次入参覆盖工单上的细胞总数。入参为空则保留模板值。"""
    if not gdid or column not in ("F_CDQXBZS", "F_FSQXBZS"):
        return
    value = _coerce_cell_total(value)
    if not value:
        return
    adapter = get_adapter()
    with adapter.connect() as conn:
        cur = conn.cursor()
        cur.execute(f"UPDATE T_PYGD SET {column} = ? WHERE F_ID = ?", (value, int(gdid)))
        if column == "F_CDQXBZS":
            cur.execute("SELECT MAX(F_ID) FROM T_PYMX_ZZ WHERE F_GDID = ?", (int(gdid),))
            mx = cur.fetchone()
            if mx and mx[0] is not None:
                cur.execute("UPDATE T_PYMX_ZZ SET F_CDQXBZS = ? WHERE F_ID = ?", (value, mx[0]))
        conn.commit()


def update_culture_order(data: Dict[str, Any]) -> bool:
    adapter = get_adapter()
    rec_id = int(data.get("F_ID"))
    allowed_fields = [
        "F_ZZBW", "F_XYZL", "F_YBLY", "F_YBGG", "F_PYSJ",
        "F_PIPELINE_JM", "F_PIPELINE_LX",
        "F_CDQXBZS", "F_FSQXBZS",
    ]
    if "F_PIPELINE_JM" in data:
        data["F_PIPELINE_JM"] = _coerce_pipeline_jm(data.get("F_PIPELINE_JM"))
    if "F_PIPELINE_LX" in data:
        data["F_PIPELINE_LX"] = _coerce_pipeline_lx(data.get("F_PIPELINE_LX"))
    if "F_CDQXBZS" in data:
        data["F_CDQXBZS"] = _coerce_cell_total(data.get("F_CDQXBZS"))
    if "F_FSQXBZS" in data:
        data["F_FSQXBZS"] = _coerce_cell_total(data.get("F_FSQXBZS"))
    sets: List[str] = []
    params: List[Any] = []
    for f in allowed_fields:
        if f in data and data[f] is not None:
            sets.append(f"{f} = ?")
            val = _serialize_lx(data[f]) if f == "F_PIPELINE_LX" else data[f]
            params.append(val)
    if not sets:
        return False
    sql = f"UPDATE T_PYGD SET {', '.join(sets)} WHERE F_ID = ?"
    params.append(rec_id)
    with adapter.connect() as conn:
        cur = conn.cursor()
        cur.execute(sql, tuple(params))
        affected = cur.rowcount
        conn.commit()
        return affected > 0


def update_culture_order_with_files(data: Dict[str, Any], add_file_ids: List[int] | None, del_file_ids: List[int] | None = None, operator: str = "") -> Dict[str, Any] | None:
    """更新基础字段，并对附件ID做增删：
    - 追加 add_file_ids 到 T_PYGD.F_FJ（ID数组JSON）
    - 删除 del_file_ids：只从 JSON 中移除引用关系，保留 T_FILES 记录和物理文件
    - 创建质控审核记录（如果有operator）
    返回：{"F_ID": id, "F_FJ": [{id,path,name}...], "qc_audit_id": ...}
    """
    import logging
    logger = logging.getLogger(__name__)
    from app.crud.field_config import assert_page, scrub_hidden
    order_fields = ["F_ZZBW", "F_XYZL", "F_YBLY", "F_YBGG", "F_PYSJ", "F_PIPELINE_JM", "F_PIPELINE_LX", "F_QYFS"]
    scrub_hidden("culture_order", data)
    assert_page("culture_order", data, updating=True, limit=order_fields)

    adapter = get_adapter()
    rec_id = int(data.get("F_ID"))
    allowed_fields = [
        "F_ZZBW", "F_XYZL", "F_YBLY", "F_YBGG", "F_PYSJ",
        "F_PIPELINE_JM", "F_PIPELINE_LX",
        "F_CDQXBZS", "F_FSQXBZS",
    ]

    # 空串/非法值统一 coerce，避免 F_PIPELINE_JM='' 写入 INT 列导致 500
    if "F_PIPELINE_JM" in data:
        data["F_PIPELINE_JM"] = _coerce_pipeline_jm(data.get("F_PIPELINE_JM"))
    if "F_PIPELINE_LX" in data:
        data["F_PIPELINE_LX"] = _coerce_pipeline_lx(data.get("F_PIPELINE_LX"))
    if "F_CDQXBZS" in data:
        data["F_CDQXBZS"] = _coerce_cell_total(data.get("F_CDQXBZS"))
    if "F_FSQXBZS" in data:
        data["F_FSQXBZS"] = _coerce_cell_total(data.get("F_FSQXBZS"))
    
    # ✅ 获取修改前的完整详情数据（用于F_OLD_DATA）
    old_detail_data = None
    try:
        old_detail_data = get_culture_order_detail(rec_id)
        logger.info(f"[工单编辑质控] 成功获取旧详情数据用于备份，工单ID: {rec_id}")
    except Exception as e:
        logger.warning(f"[工单编辑质控] 获取旧详情数据失败: {e}")
    
    sets: List[str] = []
    params: List[Any] = []
    for f in allowed_fields:
        if f in data and data[f] is not None:
            sets.append(f"{f} = ?")
            val = _serialize_lx(data[f]) if f == "F_PIPELINE_LX" else data[f]
            params.append(val)
    with adapter.connect() as conn:
        cur = conn.cursor()
        if sets:
            sql = f"UPDATE T_PYGD SET {', '.join(sets)} WHERE F_ID = ?"
            params.append(rec_id)
            cur.execute(sql, tuple(params))

        # 处理附件ID增删
        latest_ids: List[int] = []
        cur.execute("SELECT F_FJ FROM T_PYGD WHERE F_ID = ?", (rec_id,))
        row = cur.fetchone()
        cur_json = row[0] if row else None
        try:
            existing = json.loads(cur_json) if cur_json else []
        except Exception:
            existing = []
        existing_ids: List[int] = []
        for v in existing:
            try:
                existing_ids.append(int(v))
            except Exception:
                pass
        # 删除（只解除引用关系，保留T_FILES记录和物理文件）
        if del_file_ids:
            to_del = {int(x) for x in del_file_ids if isinstance(x, (int, str)) and str(x).isdigit()}
            if to_del:
                # ✅ 只从工单附件列表中移除文件ID，不删除T_FILES记录和物理文件
                # 这样质控记录中保存的文件ID仍然可以在前端渲染出来
                existing_ids = [i for i in existing_ids if i not in to_del]
                logger.info(f"[工单附件] 从工单 {rec_id} 中移除附件ID: {to_del}（保留文件记录和物理文件）")
        if add_file_ids:
            for v in add_file_ids:
                try:
                    iv = int(v)
                    if iv not in existing_ids:
                        existing_ids.append(iv)
                except Exception:
                    pass
        # 回写
        new_json = json.dumps(existing_ids, ensure_ascii=False)
        cur.execute("UPDATE T_PYGD SET F_FJ = ? WHERE F_ID = ?", (new_json, rec_id))

        # 返回附件列表
        from app.crud.file_center import get_files_by_ids
        files = get_files_by_ids(existing_ids)
        details = [{
            "id": int(f.get("F_ID")),
            "path": f.get("F_PATH"),
            "name": f.get("F_ORIGINAL_NAME"),
        } for f in files if f.get("F_ID") is not None]
        conn.commit()
    
    # ========== 创建质控审核记录（在事务外执行） ==========
    qc_audit_id = None
    if operator:
        try:
            from app.crud.qc_audit import create_qc_audit_record, calculate_changes, get_sample_basic_info
            
            # 获取修改后的完整详情数据
            new_detail_data = get_culture_order_detail(rec_id)
            
            # 计算变更内容
            changes = calculate_changes(old_detail_data or {}, new_detail_data or {})
            
            # 如果没有实际变更，不创建质控记录
            if not changes.get("变更字段"):
                logger.info(f"[工单编辑质控] 工单信息未变更，跳过质控记录创建 (工单ID: {rec_id})")
            else:
                # 获取工单和样本信息
                zzid = new_detail_data.get("F_ZZID") if new_detail_data else None
                sample_info = get_sample_basic_info(zzid) if zzid else {}
                
                # ✅ 创建质控审核记录（工单编辑属于"工单创建"阶段的修改操作）
                qc_audit_id = create_qc_audit_record(
                    F_ZKJD="工单创建",  # ✅ 修正：工单编辑应该是"工单创建"阶段，而不是"培养"阶段
                    F_BGLX="修改",
                    F_ZZID=zzid,
                    F_CHANGES=changes,
                    F_JLR=operator or "系统",
                    F_GDID=rec_id,
                    F_XM=sample_info.get("F_XM") or new_detail_data.get("F_XM"),
                    F_BRBH=sample_info.get("F_BRBH") or new_detail_data.get("F_BRBH"),
                    F_ZZBW=sample_info.get("F_ZZBW"),
                    F_YBLY=new_detail_data.get("F_YBLY"),
                    F_GDBH=new_detail_data.get("F_GDBH"),
                    F_DCID=new_detail_data.get("F_DCID"),
                    F_DC=new_detail_data.get("F_DC"),
                    F_PYSJ=new_detail_data.get("F_PYSJ"),
                    F_SQR=new_detail_data.get("F_SQR"),
                    F_OLD_DATA=old_detail_data  # ✅ 传入修改前的完整详情数据
                )
                logger.info(f"[工单编辑质控] 创建质控审核记录成功，工单ID: {rec_id}, 质控阶段: 工单创建, 质控ID: {qc_audit_id}")
        except Exception as e:
            logger.error(f"[工单编辑质控] 创建质控审核记录失败 (工单ID: {rec_id}): {e}", exc_info=True)
    else:
        logger.info(f"[工单编辑质控] 跳过创建质控记录 - operator为空")
    
    result = {"F_ID": rec_id, "F_FJ": details}
    if qc_audit_id:
        result["qc_audit_id"] = qc_audit_id
    
    return result

def remove_culture_attachment(order_id: int, file_id: int) -> Dict[str, Any] | None:
    """根据工单ID与文件ID，删除附件：
    - 查 `T_FILES` 获得路径
    - ✅ 只解除引用关系，保留 `T_FILES` 记录和物理文件
    - 从 `T_PYGD.F_FJ`（路径数组JSON）中移除相应路径并回写
    - 返回被删除项与剩余附件清单
    """
    import logging
    logger = logging.getLogger(__name__)
    
    adapter = get_adapter()
    with adapter.connect() as conn:
        cur = conn.cursor()
        # 查 file 记录
        from app.crud.file_center import get_file_by_id, get_files_by_ids
        f = get_file_by_id(int(file_id))
        fpath = f.get("F_PATH") if f else None
        if not fpath:
            return None
        # 取当前 F_FJ
        cur.execute("SELECT F_FJ FROM T_PYGD WHERE F_ID = ?", (order_id,))
        row = cur.fetchone()
        if not row:
            return None
        cur_paths_json = row[0] if row else None
        try:
            paths = json.loads(cur_paths_json) if cur_paths_json else []
        except Exception:
            paths = []
        # 移除路径
        new_paths = [p for p in paths if p != fpath]
        new_json = json.dumps(new_paths, ensure_ascii=False)
        cur.execute("UPDATE T_PYGD SET F_FJ = ? WHERE F_ID = ?", (new_json, order_id))
        
        # ✅ 只解除引用关系，不删除T_FILES记录和物理文件
        # 这样质控记录中保存的文件信息仍然可以在前端渲染出来
        logger.info(f"[工单附件] 从工单 {order_id} 中移除附件 {file_id}（保留文件记录和物理文件）")

        # 组合剩余附件清单（通过路径反查 ID/名称）
        from app.crud.file_center import get_files_by_paths
        file_rows = get_files_by_paths(new_paths)
        left_files = [{"id": r.get("F_ID"), "path": r.get("F_PATH"), "name": r.get("F_ORIGINAL_NAME")} for r in file_rows]
        conn.commit()
        return {"F_ID": order_id, "removed": {"id": int(file_id), "path": fpath}, "F_FJ": left_files}


def export_culture_orders(
    q: Dict[str, Any],
    operator: str | None = None,
    user_ktype: str | None = None,
    order_ids: List[int] | None = None,
    user: Dict[str, Any] | None = None,
    scope: str | None = None,
) -> Dict[str, Any]:
    """
    导出培养工单列表到 Excel,保存到文件中心并返回文件信息
    
    Args:
        q: 查询参数(与 list_culture_orders 相同)
        operator: 操作人
        user_ktype: 当前登录用户的 F_Ktype，非空时仅导出该用户申请的工单（与列表接口逻辑一致）
        order_ids: 勾选的工单ID数组；非空时仅导出这些工单，忽略其他过滤条件
        
    Returns:
        包含文件ID、文件路径、文件名等信息的字典
    """
    from app.utils.excel_export import export_culture_orders_to_excel, generate_export_filename
    from app.crud.file_center import insert_file_record
    from app.core.settings import settings
    import os
    
    # 1. 复用列表查询逻辑,获取所有数据(忽略分页)
    query_params = {**q, "skip": 0, "limit": None}  # 移除分页限制
    
    # 设置最大导出数量限制(防止数据过大)
    MAX_EXPORT_COUNT = 50000
    
    adapter = get_adapter()
    where: List[str] = []
    params: List[Any] = []

    def like(col: str, key: str | None):
        if key:
            pred, p = adapter.like(col, key.strip())
            where.append(pred)
            params.extend(p)

    # 勾选导出：指定ID数组时，直接用 IN 过滤，跳过其他业务条件
    if order_ids:
        q_marks = ",".join(["?" for _ in order_ids])
        where.append(f"p.F_ID IN ({q_marks})")
        params.extend(order_ids)
    else:
        like("p.F_XM", query_params.get("F_XM"))
        like("p.F_BRBH", query_params.get("F_BRBH"))
        like("p.F_ZZID", query_params.get("F_ZZID"))
        like("p.F_GDBH", query_params.get("F_GDBH"))
        like("p.F_GDLX", query_params.get("F_GDLX"))
        like("p.F_DC", query_params.get("F_DC"))
        like("p.F_DCID", query_params.get("F_DCID"))
        like("p.F_ZZBW", query_params.get("F_ZZBW"))
        like("p.F_XYZL", query_params.get("F_XYZL"))

        if query_params.get("F_GDZT"):
            where.append("p.F_GDZT = ?")
            params.append(query_params["F_GDZT"].strip())

        if query_params.get("F_ZKZT"):
            where.append("p.F_ZKZT = ?")
            params.append(query_params["F_ZKZT"].strip())

        if query_params.get("F_PYSJ_START"):
            where.append("p.F_PYSJ >= ?")
            params.append(query_params["F_PYSJ_START"])
        if query_params.get("F_PYSJ_END"):
            where.append("p.F_PYSJ <= ?")
            params.append(query_params["F_PYSJ_END"])

        if query_params.get("F_HSSL_MIN") is not None:
            where.append("h.F_HSSL >= ?")
            params.append(query_params["F_HSSL_MIN"])
        if query_params.get("F_HSSL_MAX") is not None:
            where.append("h.F_HSSL <= ?")
            params.append(query_params["F_HSSL_MAX"])

    from app.core.data_scope import apply_owner_filter, SCOPE_SELF
    if user is None and user_ktype is not None and scope == SCOPE_SELF:
        user = {"F_Ktype": user_ktype}
    apply_owner_filter(where, params, scope=scope, user=user, column="p.F_SQR", match="name")

    where_sql = (" WHERE " + " AND ".join(where)) if where else ""

    # 查询语句(复用 list_culture_orders 的逻辑)
    # 注意: count 查询和数据查询分开,避免子查询中使用 ORDER BY 的限制
    # 使用COUNT子查询统计冻存和复苏记录数量
    subq_dcjl = "SELECT COUNT(1) FROM T_DCJL d WHERE d.F_GDID = p.F_ID"
    subq_fsjl = "SELECT COUNT(1) FROM T_FSJL f WHERE f.F_GDID = p.F_ID"
    
    base_query_without_order = f"""
SELECT p.F_ID, p.F_GDBH, p.F_ZZID, p.F_XM, p.F_BRBH, p.F_BBLX,
       p.F_DC, p.F_DCID, p.F_ZZBW, p.F_XYZL, p.F_QYFS, p.F_YBLY, p.F_YBGG,
       p.F_SQR, p.F_PYSJ, p.F_FJ, p.F_CJSJ,
       p.F_GDLX,
       p.F_GDZT,
       p.F_ZKZT AS F_SHZT,
       ({subq_dcjl}) AS F_DCJL,
       ({subq_fsjl}) AS F_FSJL,
       CASE WHEN EXISTS (SELECT 1 FROM T_YCL y WHERE y.F_GDID = p.F_ID) THEN 1 ELSE 0 END AS F_YCL,
       p.F_PARENT_ID,
       p.F_WCSJ,
       p.F_BHGSJ,
       p.F_PIPELINE_JM,
       p.F_PIPELINE_LX,
       h.F_HSSJ,
       h.F_HSSL
FROM T_PYGD p
LEFT JOIN (
  SELECT t1.F_GDID, t1.F_HSSJ, t1.F_HSSL
  FROM T_HSJL t1
  INNER JOIN (SELECT F_GDID, MAX(F_ID) AS max_id FROM T_HSJL GROUP BY F_GDID) t2
    ON t1.F_GDID = t2.F_GDID AND t1.F_ID = t2.max_id
) h ON h.F_GDID = p.F_ID
{where_sql}
"""
    
    # 完整查询(带排序)
    base_core = base_query_without_order + "ORDER BY" + _build_zdyxh_order(adapter, "p") + " p.F_ID DESC"
    
    # 2. 执行查询
    with adapter.connect() as conn:
        cur = conn.cursor()
        
        # 先检查数量(使用不带 ORDER BY 的查询)
        count_sql = f"SELECT COUNT(1) FROM ( {base_query_without_order} ) t"
        cur.execute(count_sql, tuple(params))
        total = int(cur.fetchone()[0])
        
        if total > MAX_EXPORT_COUNT:
            raise ValueError(f"导出数量超过限制({MAX_EXPORT_COUNT}条),请缩小查询范围")
        
        if total == 0:
            raise ValueError("没有符合条件的数据可导出")
        
        # 查询所有数据
        cur.execute(base_core, tuple(params))
        cols = [c[0] for c in cur.description]
        rows = [dict(zip(cols, r)) for r in cur.fetchall()]
        
    # 3. 数据格式化
    def _format(row: Dict[str, Any]):
        """格式化数据(复用 list_culture_orders 的逻辑)"""
        for key in ("F_PYSJ", "F_CJSJ", "F_HSSJ", "F_WCSJ", "F_BHGSJ"):
            if row.get(key) and isinstance(row[key], datetime):
                row[key] = row[key].strftime("%Y-%m-%d %H:%M:%S")
        
        # 计算培养天数 F_PYTS
        # - 已完成：F_WCSJ - F_PYSJ（天）
        # - 不合格：F_BHGSJ - F_PYSJ（天），不足 1 天按 1 天计
        # - 已创建：今天 - F_PYSJ（天）
        # - 其他：None
        try:
            from datetime import date as _date
            pysj_raw = row.get("F_PYSJ")
            gdzt = (row.get("F_GDZT") or "").strip()
            pyts = None
            if pysj_raw:
                _pysj_dt = None
                try:
                    _pysj_dt = datetime.strptime(str(pysj_raw), "%Y-%m-%d %H:%M:%S")
                except Exception:
                    pass
                if _pysj_dt:
                    if gdzt == "已完成":
                        wcsj_raw = row.get("F_WCSJ")
                        if wcsj_raw:
                            try:
                                _wcsj_dt = datetime.strptime(str(wcsj_raw), "%Y-%m-%d %H:%M:%S")
                                pyts = (_wcsj_dt.date() - _pysj_dt.date()).days
                            except Exception:
                                pass
                    elif gdzt == "不合格":
                        bhgsj_raw = row.get("F_BHGSJ")
                        if bhgsj_raw:
                            try:
                                _bhgsj_dt = datetime.strptime(str(bhgsj_raw), "%Y-%m-%d %H:%M:%S")
                                pyts = _normalize_list_pyts((_bhgsj_dt.date() - _pysj_dt.date()).days)
                            except Exception:
                                pass
                    elif gdzt == "已创建":
                        pyts = (_date.today() - _pysj_dt.date()).days
            row["F_PYTS"] = pyts
        except Exception:
            row["F_PYTS"] = None

        row["F_PIPELINE_JM"] = _coerce_pipeline_jm(row.get("F_PIPELINE_JM"))
        row["F_PIPELINE_LX"] = _deserialize_lx(row.get("F_PIPELINE_LX"))

        # 格式化冻存记录数量（无记录则返回0，有记录则返回数量）
        try:
            row["F_SFDC"] = int(row.get("F_DCJL") or 0)
        except Exception:
            row["F_SFDC"] = 0
        
        # 格式化复苏记录数量（无记录则返回0，有记录则返回数量）
        try:
            row["F_SFFS"] = int(row.get("F_FSJL") or 0)
        except Exception:
            row["F_SFFS"] = 0
    
    for row in rows:
        _format(row)
    
    # 4. 处理树形结构(如果需要)
    as_tree = query_params.get("asTree", False)
    if as_tree:
        # 构建树形结构(复用 list_culture_orders 的逻辑)
        parent_map = {int(r.get("F_ID")): int(r.get("F_PARENT_ID")) if r.get("F_PARENT_ID") is not None else None for r in rows}
        id_to_item = {int(r.get("F_ID")): r for r in rows}
        
        for it in id_to_item.values():
            it.setdefault("children", [])
            if it.get("F_PARENT_ID") is not None:
                try:
                    it["F_PARENT_ID"] = int(it["F_PARENT_ID"])
                except:
                    it["F_PARENT_ID"] = None
                    
        roots = []
        for rid, it in id_to_item.items():
            pid = parent_map.get(rid)
            if pid and pid in id_to_item:
                id_to_item[pid].setdefault("children", []).append(it)
            else:
                roots.append(it)
        export_data = roots
    else:
        export_data = rows
    
    # 5. 生成 Excel 文件
    filename = generate_export_filename(prefix="culture_orders")
    # 使用正确的 uploads 目录路径
    from pathlib import Path
    base_dir = Path(__file__).resolve().parents[2]  # Bio9_organ 目录
    upload_dir = base_dir / settings.upload_dir  # Bio9_organ/uploads
    relative_path = f"exports/{filename}"
    absolute_path = upload_dir / relative_path
    
    # 确保目录存在
    absolute_path.parent.mkdir(parents=True, exist_ok=True)
    
    # 导出到 Excel
    export_culture_orders_to_excel(
        data=export_data,
        filepath=str(absolute_path),
        title="培养工单列表"
    )
    
    # 6. 保存到文件中心
    file_id = insert_file_record(
        path=relative_path,
        original_name=filename
    )
    
    # 7. 返回文件信息
    return {
        "file_id": file_id,
        "filename": filename,
        "file_path": relative_path,
        "file_url": f"/{relative_path}",  # 静态文件访问路径（省略 uploads 前缀）
        "file_size": absolute_path.stat().st_size,
        "total": total,
        "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    }


def mark_culture_orders_unqualified(
    gdids: List[int],
    reason: str | None,
    operator: str | None = None,
    unqualified_type: str | None = None,
) -> Dict[str, Any]:
    """
    批量将培养工单标记为不合格
    
    Args:
        gdids: 工单ID数组
        reason: 不合格原因
        operator: 操作人姓名
        unqualified_type: 不合格类型（F_BHGLX）
        
    Returns:
        {
            "updated_count": int,
            "updated_gdids": List[int],
            "qc_audit_ids": List[int]
        }
    """
    if not gdids:
        raise ValueError("工单ID数组不能为空")
    
    adapter = get_adapter()
    updated_gdids = []
    order_info_list = []  # 保存工单信息用于质控记录
    
    with adapter.connect() as conn:
        cur = conn.cursor()
        
        for gdid in gdids:
            # 先获取工单信息（用于质控记录，同时读取原状态以便审批拒绝时恢复）
            cur.execute(
                """
                SELECT F_ID, F_GDBH, F_ZZID, F_DCID, F_DC, F_PYSJ, F_SQR, F_GDZT
                FROM T_PYGD 
                WHERE F_ID = ?
                """,
                (int(gdid),)
            )
            order_row = cur.fetchone()
            
            if not order_row:
                logger.warning(f"工单不存在: ID={gdid}")
                continue
            
            original_gdzt = order_row[7] or "已创建"  # 保存原状态用于审批拒绝时恢复

            # 设置中间状态"不合格审批中"，等待审批通过后才正式标记为不合格
            from datetime import datetime as _dt
            _bhgsj = _dt.now()
            try:
                cur.execute(
                    "UPDATE T_PYGD SET F_BHGYY = ?, F_BHGLX = ?, F_GDZT = '不合格审批中', F_BHGSJ = ? WHERE F_ID = ?",
                    (reason or '', unqualified_type or '', _bhgsj, int(gdid))
                )
            except Exception:
                cur.execute(
                    "UPDATE T_PYGD SET F_BHGYY = ?, F_BHGLX = ?, F_GDZT = '不合格审批中' WHERE F_ID = ?",
                    (reason or '', unqualified_type or '', int(gdid))
                )
            
            if cur.rowcount > 0:
                updated_gdids.append(int(gdid))
                # 保存工单信息（含原状态）
                order_info_list.append({
                    "F_ID": order_row[0],
                    "F_GDBH": order_row[1],
                    "F_ZZID": order_row[2],
                    "F_DCID": order_row[3],
                    "F_DC": order_row[4],
                    "F_PYSJ": order_row[5],
                    "F_SQR": order_row[6],
                    "原状态": original_gdzt,
                })
        
        conn.commit()
    
    # ========== 创建质控记录（在事务外执行） ==========
    qc_audit_ids = []
    if operator and order_info_list:
        try:
            from app.crud.qc_audit import create_qc_audit_record, get_sample_basic_info
            
            for order_info in order_info_list:
                try:
                    zzid = order_info.get("F_ZZID")
                    gdid = order_info.get("F_ID")
                    gdbh = order_info.get("F_GDBH")
                    
                    # 获取样本基本信息
                    sample_info = get_sample_basic_info(zzid) if zzid else {}
                    
                    # 标为不合格的变更信息（含原状态，用于审批拒绝时恢复）
                    changes = {
                        "操作": "标为不合格",
                        "原状态": order_info.get("原状态", "已创建"),
                        "关键信息": {
                            "工单ID":    gdid,
                            "工单编号":  gdbh,
                            "不合格类型": unqualified_type or "",
                            "不合格原因": reason or "未填写原因",
                        }
                    }
                    
                    # 创建质控审核记录
                    qc_audit_id = create_qc_audit_record(
                        F_ZKJD="培养工单-标为不合格",
                        F_BGLX="修改",
                        F_ZZID=zzid,
                        F_CHANGES=changes,
                        F_JLR=operator,
                        F_GDID=gdid,
                        F_XM=sample_info.get("F_XM"),
                        F_BRBH=sample_info.get("F_BRBH"),
                        F_YBLY=order_info.get("F_YBLY"),  # ✅ 从工单信息中获取样本来源
                        F_GDBH=gdbh,
                        F_DCID=order_info.get("F_DCID"),
                        F_DC=order_info.get("F_DC"),
                        F_PYSJ=order_info.get("F_PYSJ"),
                        F_SQR=order_info.get("F_SQR")
                    )
                    
                    if qc_audit_id:
                        qc_audit_ids.append(qc_audit_id)
                    
                    logger.info(f"[培养工单标为不合格] 质控记录创建成功，工单ID: {gdid}, 质控ID: {qc_audit_id}")
                    
                except Exception as e:
                    logger.error(f"[培养工单标为不合格] 创建质控记录失败 (工单ID: {order_info.get('F_ID')}): {e}", exc_info=True)
                    
        except Exception as e:
            logger.error(f"[培养工单标为不合格] 质控模块导入失败: {e}", exc_info=True)
    
    result = {
        "updated_count": len(updated_gdids),
        "updated_gdids": updated_gdids,
        "F_GDZT":  "不合格审批中",
        "F_BHGYY": reason or '',
        "F_BHGLX": unqualified_type or '',
    }
    
    if qc_audit_ids:
        result["qc_audit_ids"] = qc_audit_ids
    
    return result


def update_order_status(
    gdid: int,
    status: str,
    operator: str | None = None
) -> Dict[str, Any]:
    """
    更新培养工单状态
    
    Args:
        gdid: 工单ID
        status: 新状态（用户自由输入）
        operator: 操作人姓名
    
    Returns:
        {
            "F_ID": int,
            "F_GDZT": str,
            "old_status": str,
            "qc_audit_id": int (可选)
        }
    """
    if not status or not status.strip():
        raise ValueError("状态不能为空")
    
    status = status.strip()
    
    adapter = get_adapter()
    with adapter.connect() as conn:
        cur = conn.cursor()
        
        # 查询工单信息
        cur.execute(
            "SELECT F_GDZT, F_GDBH, F_ZZID, F_DC, F_DCID, F_YBLY FROM T_PYGD WHERE F_ID = ?",
            (int(gdid),)
        )
        row = cur.fetchone()
        if not row:
            raise ValueError(f"工单ID {gdid} 不存在")
        
        old_status = row[0] if row[0] else "未知"
        gdbh = row[1]
        zzid = row[2]
        dc = row[3]
        dcid = row[4]
        ybly = row[5]  # ✅ 获取样本来源
        
        # 如果状态未改变，直接返回
        if old_status == status:
            return {
                "F_ID": gdid,
                "F_GDZT": status,
                "old_status": old_status,
                "message": "状态未改变"
            }
        
        # 更新状态；标记为"已完成"时同步记录完成时间 F_WCSJ
        if status == "已完成":
            try:
                cur.execute(
                    "UPDATE T_PYGD SET F_GDZT = ?, F_WCSJ = ? WHERE F_ID = ?",
                    (status, datetime.now(), int(gdid))
                )
            except Exception:
                # F_WCSJ 列不存在时（DDL 未执行）降级为仅更新状态
                cur.execute(
                    "UPDATE T_PYGD SET F_GDZT = ? WHERE F_ID = ?",
                    (status, int(gdid))
                )
        else:
            cur.execute(
                "UPDATE T_PYGD SET F_GDZT = ? WHERE F_ID = ?",
                (status, int(gdid))
            )
        
        if cur.rowcount == 0:
            raise ValueError(f"更新工单状态失败，工单ID: {gdid}")
        
        conn.commit()
    
    result = {
        "F_ID": gdid,
        "F_GDZT": status,
        "old_status": old_status
    }
    
    # 创建质控审核记录
    qc_audit_id = None
    try:
        from app.crud.qc_audit import create_qc_audit_record
        from app.crud.sample_receive import get_sample_basic_info
        
        # 获取样本基本信息
        sample_info = get_sample_basic_info(zzid) if zzid else {}
        
        # 状态变更信息
        changes = {
            "操作": "更新工单状态",
            "关键信息": {
                "工单ID": gdid,
                "工单编号": gdbh,
                "原状态": old_status,
                "新状态": status
            }
        }
        
        # 创建质控审核记录
        qc_audit_id = create_qc_audit_record(
            F_ZKJD="培养工单-状态更新",
            F_BGLX="修改",
            F_ZZID=zzid,
            F_CHANGES=changes,
            F_JLR=operator,
            F_GDID=gdid,
            F_XM=sample_info.get("F_XM"),
            F_BRBH=sample_info.get("F_BRBH"),
            F_YBLY=ybly,  # ✅ 从工单信息中获取样本来源
            F_GDBH=gdbh,
            F_DCID=dcid,
            F_DC=dc
        )
        
        if qc_audit_id:
            result["qc_audit_id"] = qc_audit_id
            logger.info(f"[培养工单状态更新] 质控记录创建成功，工单ID: {gdid}, 质控ID: {qc_audit_id}")
    
    except Exception as e:
        logger.error(f"[培养工单状态更新] 创建质控记录失败 (工单ID: {gdid}): {e}", exc_info=True)
    
    return result


def save_custom_fields(gdid: int, zdyxh: str = None, syjl: str = None, operator: str = None) -> Dict[str, Any]:
    """
    保存自定义字段（自定义序号和/或实验记录）（新增/编辑一体化）
    
    Args:
        gdid: 工单ID
        zdyxh: 自定义序号（字符串，可选）；传 '' 或 '-1' 表示清除序号（置为 NULL），传 None 表示不修改
        syjl: 实验记录内容（可选）
        operator: 操作人
    
    Returns:
        {"F_ID": int, "F_ZDYXH": str|None, "F_SYJL": str}
    """
    if zdyxh is None and not syjl:
        raise ValueError("至少需要提供自定义序号或实验记录中的一个字段")
    
    adapter = get_adapter()
    with adapter.connect() as conn:
        cur = conn.cursor()
        
        # 检查工单是否存在并获取旧值
        cur.execute("SELECT F_ZDYXH, F_SYJL, F_ZZID FROM T_PYGD WHERE F_ID = ?", (gdid,))
        row = cur.fetchone()
        if not row:
            raise ValueError(f"工单ID {gdid} 不存在")
        
        old_zdyxh = row[0]
        old_syjl = row[1]
        zzid = row[2]
        
        # 构建更新语句
        update_fields = []
        update_params = []
        
        if zdyxh is not None:
            # 空字符串或 "-1" 均视为清除序号
            if zdyxh == "" or zdyxh == "-1":
                update_fields.append("F_ZDYXH = NULL")
            else:
                update_fields.append("F_ZDYXH = ?")
                update_params.append(zdyxh)
        
        if syjl is not None:
            update_fields.append("F_SYJL = ?")
            update_params.append(syjl)
        
        update_params.append(gdid)
        
        # 执行更新
        update_sql = f"UPDATE T_PYGD SET {', '.join(update_fields)} WHERE F_ID = ?"
        cur.execute(update_sql, tuple(update_params))
        conn.commit()
    
    # 创建质控记录
    if operator and ((zdyxh and old_zdyxh != zdyxh) or (syjl and old_syjl != syjl)):
        try:
            from app.crud.qc_audit import create_qc_audit_record, calculate_changes
            
            new_detail = get_culture_order_detail(gdid)
            old_detail = new_detail.copy()
            
            if zdyxh and old_zdyxh != zdyxh:
                old_detail["F_ZDYXH"] = old_zdyxh
            if syjl and old_syjl != syjl:
                old_detail["F_SYJL"] = old_syjl
            
            changes = calculate_changes(old_detail, new_detail)
            
            create_qc_audit_record(
                F_ZKJD="工单创建",
                F_BGLX="修改",
                F_ZZID=zzid,
                F_CHANGES=changes,
                F_JLR=operator,
                F_GDID=gdid,
                F_YBLY=new_detail.get("F_YBLY"),  # ✅ 从工单详情中获取样本来源
                F_GDBH=new_detail.get("F_GDBH"),
                F_DCID=new_detail.get("F_DCID"),
                F_DC=new_detail.get("F_DC"),
                F_PYSJ=new_detail.get("F_PYSJ"),
                F_SQR=new_detail.get("F_SQR"),
                F_OLD_DATA=old_detail
            )
        except Exception as e:
            import logging
            logging.getLogger(__name__).error(f"保存自定义字段质控记录失败: {e}", exc_info=True)
    
    result = {"F_ID": gdid}
    if zdyxh is not None:
        result["F_ZDYXH"] = zdyxh
    if syjl is not None:
        result["F_SYJL"] = syjl
    
    return result


def _upload_attachment_field(
    gdid: int,
    db_field: str,
    file_ids: List[int],
    bz: str = None,
    ids_to_delete: List[int] = None,
    operator: str = None,
    qc_label: str = "附件",
    qc_zkjd: Optional[str] = None,
) -> Dict[str, Any]:
    """
    通用附件字段增量更新。

    存储格式：{"bz": "共用备注", "ids": [文件ID, ...]}
    - bz 是整个字段所有附件共用的备注，传 None 时不改变已有备注，传空字符串时清空。
    - ids 增量管理（去重添加 / 按 ID 删除）。

    返回格式：{"F_ID": gdid, db_field: {"bz": ..., "files": [...]}, "added_count": ..., "deleted_count": ...}
    """
    import json
    adapter = get_adapter()

    with adapter.connect() as conn:
        cur = conn.cursor()

        cur.execute(f"SELECT {db_field} FROM T_PYGD WHERE F_ID = ?", (gdid,))
        row = cur.fetchone()
        if not row:
            raise ValueError(f"工单ID {gdid} 不存在")

        old_raw = row[0] or "{}"
        parsed = _parse_attachment_field(old_raw)
        current_bz = parsed["bz"]
        current_ids: List[int] = list(parsed["ids"])
        current_id_set: set = set(current_ids)

        # 更新备注（仅当调用方明确传入时才覆盖，None 表示不改）
        if bz is not None:
            current_bz = bz

        # 删除指定附件
        deleted_count = 0
        if ids_to_delete:
            del_ids = {int(d) for d in ids_to_delete if d is not None}
            before = len(current_ids)
            current_ids = [fid for fid in current_ids if fid not in del_ids]
            deleted_count = before - len(current_ids)
            current_id_set -= del_ids

        # 添加新附件（去重）
        added_count = 0
        for fid in file_ids:
            try:
                fid_int = int(fid)
                if fid_int not in current_id_set:
                    current_ids.append(fid_int)
                    current_id_set.add(fid_int)
                    added_count += 1
            except Exception:
                pass

        new_data = {"bz": current_bz, "ids": current_ids}
        new_raw = json.dumps(new_data, ensure_ascii=False)
        cur.execute(f"UPDATE T_PYGD SET {db_field} = ? WHERE F_ID = ?", (new_raw, gdid))
        conn.commit()

    qc_audit_id = None
    if operator and qc_zkjd and (added_count > 0 or deleted_count > 0 or bz is not None):
        try:
            import logging
            from app.crud.qc_audit import create_qc_audit_record, calculate_changes, get_sample_basic_info

            logger = logging.getLogger(__name__)
            new_detail = get_culture_order_detail(gdid)
            old_detail = dict(new_detail) if new_detail else {}
            old_detail[db_field] = _format_attachment_field(old_raw)

            changes = calculate_changes(old_detail, new_detail or {})
            if not changes.get("变更字段"):
                logger.info(f"[{qc_zkjd}质控] 无实际变更，跳过 (工单ID: {gdid})")
            else:
                zzid = new_detail.get("F_ZZID") if new_detail else None
                sample_info = get_sample_basic_info(zzid) if zzid else {}
                qc_audit_id = create_qc_audit_record(
                    F_ZKJD=qc_zkjd,
                    F_BGLX="修改",
                    F_ZZID=zzid,
                    F_CHANGES=changes,
                    F_JLR=operator or "系统",
                    F_GDID=gdid,
                    F_XM=sample_info.get("F_XM") or (new_detail or {}).get("F_XM"),
                    F_BRBH=sample_info.get("F_BRBH") or (new_detail or {}).get("F_BRBH"),
                    F_YBLY=(new_detail or {}).get("F_YBLY"),
                    F_GDBH=(new_detail or {}).get("F_GDBH"),
                    F_DCID=(new_detail or {}).get("F_DCID"),
                    F_DC=(new_detail or {}).get("F_DC"),
                    F_PYSJ=(new_detail or {}).get("F_PYSJ"),
                    F_SQR=(new_detail or {}).get("F_SQR"),
                    F_OLD_DATA=old_detail,
                )
                logger.info(
                    f"[{qc_zkjd}质控] 创建质控记录成功，工单ID: {gdid}, 质控ID: {qc_audit_id}"
                )
        except Exception as e:
            import logging
            logging.getLogger(__name__).error(
                f"上传{qc_label}质控记录失败: {e}", exc_info=True
            )

    result = {
        "F_ID": gdid,
        db_field: {"bz": current_bz, "files": _build_attachment_files(current_ids)},
        "added_count": added_count,
        "deleted_count": deleted_count,
    }
    if qc_audit_id:
        result["qc_audit_id"] = qc_audit_id
    return result


def upload_pathology_attachment(
    gdid: int,
    file_ids: List[int],
    bz: str = None,
    ids_to_delete: List[int] = None,
    operator: str = None,
) -> Dict[str, Any]:
    """上传病理附件（增量更新，支持删除）。bz 为整个字段共用备注，传 None 不修改。"""
    return _upload_attachment_field(
        gdid, "F_BL", file_ids, bz=bz,
        ids_to_delete=ids_to_delete, operator=operator,
        qc_label="病理附件", qc_zkjd="上传病理",
    )


def upload_sequencing_attachment(
    gdid: int,
    file_ids: List[int],
    bz: str = None,
    ids_to_delete: List[int] = None,
    operator: str = None,
) -> Dict[str, Any]:
    """上传测序附件（增量更新，支持删除）。bz 为整个字段共用备注，传 None 不修改。"""
    return _upload_attachment_field(
        gdid, "F_CX", file_ids, bz=bz,
        ids_to_delete=ids_to_delete, operator=operator,
        qc_label="测序附件", qc_zkjd="上传测序",
    )


def upload_other_attachment(
    gdid: int,
    file_ids: List[int],
    bz: str = None,
    ids_to_delete: List[int] = None,
    operator: str = None,
) -> Dict[str, Any]:
    """上传其他附件（增量更新，支持删除）。bz 为整个字段共用备注，传 None 不修改。"""
    return _upload_attachment_field(
        gdid, "F_QT", file_ids, bz=bz,
        ids_to_delete=ids_to_delete, operator=operator,
        qc_label="其他附件", qc_zkjd="上传其他",
    )


def update_jhcdcs(gdid: int, jhcdcs: Optional[int]) -> Dict[str, Any]:
    """更新培养工单的计划传代次数（F_JHCDCS）。

    Args:
        gdid: 培养工单 ID
        jhcdcs: 计划传代次数，传 None 表示清空

    Returns:
        {"F_ID": gdid, "F_JHCDCS": jhcdcs}
    """
    adapter = get_adapter()
    with adapter.connect() as conn:
        cur = conn.cursor()
        cur.execute("SELECT F_ID FROM T_PYGD WHERE F_ID = ?", (gdid,))
        if not cur.fetchone():
            raise ValueError(f"培养工单 ID {gdid} 不存在")
        cur.execute(
            "UPDATE T_PYGD SET F_JHCDCS = ? WHERE F_ID = ?",
            (jhcdcs, gdid),
        )
        conn.commit()
    return {"F_ID": gdid, "F_JHCDCS": jhcdcs}