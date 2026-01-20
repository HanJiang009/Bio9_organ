from typing import Dict, Any, List, Tuple
from app.db.factory import get_adapter
from datetime import datetime
import json
import logging

logger = logging.getLogger(__name__)


def list_pending_orders(q: Dict[str, Any], user_id: int | None = None) -> Dict[str, Any]:
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

    where_sql = (" WHERE " + " AND ".join(where)) if where else ""

    # 用户过滤（仅看与自己相关的分发记录）
    if user_id is not None:
        where_sql = (where_sql + (" AND " if where_sql else " WHERE ")) + "ff.F_PYRY = ?"
        params.append(user_id)

    base_core = f"""
SELECT
  ff.F_ID, ff.F_ZZID, ff.F_GDZT, ff.F_GDLX, ff.F_FFSJ, ff.F_PYRY,
  y.F_XM, y.F_XB, y.F_BRBH, y.F_BBLX, y.F_ZQTYS, y.F_QYFS, y.F_CXSJ,
  y.F_DJRQ,
  y.F_CJBW
FROM T_FFJL ff
LEFT JOIN T_ZZXX z ON z.F_ZZID = ff.F_ZZID
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
                f"SELECT p.F_ID, p.F_GDBH, p.F_ZZID, p.F_XM, p.F_BRBH, p.F_BBLX, p.F_DC, p.F_DCID, p.F_ZZBW, p.F_XYZL, p.F_QYFS, p.F_YBLY, p.F_YBGG, p.F_SQR, p.F_PYSJ, p.F_FJ, p.F_CJSJ, p.F_GDLX, p.F_GDZT FROM T_PYGD p WHERE p.F_ID IN ({q_marks})",
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


def create_order_from_ffjl(
    ffjl_id: int,
    zzbw: str | None,
    xyzl: str | None,
    qyfs: str | None,
    ybgg: str | None,
    pysj_str: str | None,
    file_ids: List[int] | None,
    creator_code: str = "",
) -> Dict[str, Any]:
    """根据分发记录ID创建培养工单(T_PYGD)。

    规则：
    - 代次 F_DC 固定为 'P0'
    - 代次ID F_DCID = F_ZZID + '-T-' + F_DC
    - 工单编号 F_GDBH = F_ZZID + F_DC + YYYYMMDDHHMMSS
    - 申请人 F_SQR: 用 T_FFJL.F_PYRY 去 T_USER 查询 F_Ktype，未找到时降级使用 F_FFCJR
    - 创建成功后将 T_FFJL.F_GDZT 更新为 '已创建'
    """
    adapter = get_adapter()
    with adapter.connect() as conn:
        cur = conn.cursor()
        # 取待创建工单及样本信息（包含 F_AZ 癌种字段和 F_PYRY）
        cur.execute(
            """
            SELECT ff.F_ZZID, ff.F_FFCJR, ff.F_AZ, ff.F_PYRY,
                   z.F_XM, z.F_XB, z.F_BRBH, z.F_BBLX,
                   g.F_QYFS AS F_SG_QYFS,
                   ff.F_GDLX,
                   yj.F_XM AS F_YJ_XM,
                   yj.F_BRBH AS F_YJ_BRBH,
                   yj.F_BBLX AS F_YJ_BBLX,
                   yj.F_QYFS AS F_YJ_QYFS
            FROM T_FFJL ff
            LEFT JOIN T_ZZXX z ON z.F_ZZID = ff.F_ZZID
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
        dcid = f"{zzid}-T-{dc}"
        ts = datetime.now().strftime("%Y%m%d%H%M%S")
        gdbh = f"{zzid}{dc}{ts}"

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
                F_SQR, F_PYSJ, F_FJ, F_GDBH, F_GDLX, F_GDZT, F_AZ
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
    
    return result


def list_culture_orders(q: Dict[str, Any], user_ktype: str | None = None) -> Dict[str, Any]:
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

    # 工单状态：如果需要，可与 T_FFJL 关联过滤；当前已创建的记录通常在 T_PYGD 中
    if q.get("F_GDZT"):
        # 为兼容当前设计，简单按 '已创建' 匹配；若后续需要更复杂状态流，可扩展
        pred, p = adapter.like("'已创建'", q["F_GDZT"].strip())
        where.append(pred)
        params.extend(p)
    
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

    where_sql = (" WHERE " + " AND ".join(where)) if where else ""
    
    # 用户过滤（仅看自己申请的工单）
    if user_ktype is not None:
        where_sql = (where_sql + (" AND " if where_sql else " WHERE ")) + "p.F_SQR = ?"
        params.append(user_ktype)

    # 使用适配器方法生成跨数据库兼容的子查询
    subq_dcjl = adapter.subquery_limit1("d.F_ID", "T_DCJL d", "d.F_GDID = p.F_ID", "d.F_ID DESC")

    base_core = f"""
SELECT p.F_ID, p.F_GDBH, p.F_ZZID, p.F_XM, p.F_BRBH, p.F_BBLX,
       p.F_DC, p.F_DCID, p.F_ZZBW, p.F_XYZL, p.F_QYFS, p.F_YBLY, p.F_YBGG,
       p.F_SQR, p.F_PYSJ, p.F_FJ, p.F_CJSJ,
       p.F_GDLX,
       p.F_GDZT,
       p.F_ZKZT AS F_SHZT,
       ({subq_dcjl}) AS F_DCJL,
       CASE WHEN EXISTS (SELECT 1 FROM T_YCL y WHERE y.F_GDID = p.F_ID) THEN 1 ELSE 0 END AS F_YCL,
       p.F_PARENT_ID,
       h.F_HSSJ,
       h.F_HSSL
FROM T_PYGD p
LEFT JOIN T_HSJL h ON h.F_GDID = p.F_ID
{where_sql}
"""
    base_select = base_core + " ORDER BY p.F_ID DESC"
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
            for k in ("F_PYSJ", "F_CJSJ", "F_HSSJ"):
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
            
            # 计算培养天数：F_PYSJ 到 F_HSSJ 的日期差
            pyts = None
            pysj = it.get("F_PYSJ")
            hssj = it.get("F_HSSJ")
            if pysj and hssj:
                try:
                    # 解析日期
                    pysj_dt = None
                    hssj_dt = None
                    
                    if isinstance(pysj, str):
                        try:
                            pysj_dt = datetime.fromisoformat(pysj)
                        except Exception:
                            try:
                                pysj_dt = datetime.strptime(pysj, "%Y-%m-%d %H:%M:%S")
                            except Exception:
                                pass
                    elif hasattr(pysj, "date"):
                        pysj_dt = pysj
                    
                    if isinstance(hssj, str):
                        try:
                            hssj_dt = datetime.fromisoformat(hssj)
                        except Exception:
                            try:
                                hssj_dt = datetime.strptime(hssj, "%Y-%m-%d %H:%M:%S")
                            except Exception:
                                pass
                    elif hasattr(hssj, "date"):
                        hssj_dt = hssj
                    
                    if pysj_dt and hssj_dt:
                        pyts = (hssj_dt.date() - pysj_dt.date()).days
                except Exception:
                    pyts = None
            it["F_PYTS"] = pyts
            
            if "F_YCL" in it:
                try:
                    it["F_YCL"] = bool(int(it.get("F_YCL"))) if it.get("F_YCL") is not None else False
                except Exception:
                    it["F_YCL"] = False
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
            parent_query = f"SELECT p.F_ID, p.F_GDBH, p.F_ZZID, p.F_XM, p.F_BRBH, p.F_BBLX, p.F_DC, p.F_DCID, p.F_ZZBW, p.F_XYZL, p.F_QYFS, p.F_YBLY, p.F_YBGG, p.F_SQR, p.F_PYSJ, p.F_FJ, p.F_CJSJ, p.F_GDLX, p.F_GDZT, p.F_ZKZT AS F_SHZT, ({subq_dcjl}) AS F_DCJL, CASE WHEN EXISTS (SELECT 1 FROM T_YCL y WHERE y.F_GDID = p.F_ID) THEN 1 ELSE 0 END AS F_YCL, p.F_PARENT_ID, h.F_HSSJ, h.F_HSSL FROM T_PYGD p LEFT JOIN T_HSJL h ON h.F_GDID = p.F_ID WHERE {parent_where_sql}"
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
) -> Dict[str, Any]:
    adapter = get_adapter()
    with adapter.connect() as conn:
        cur = conn.cursor()
        sql = "SELECT F_ZZID, F_XM, F_BRBH, F_BBLX, F_DC, F_DCID, F_ZZBW, F_XYZL, F_YBGG, F_SQR, F_GDLX, F_FJ, F_AZ FROM T_PYGD WHERE F_ID = ? ORDER BY F_ID DESC"
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
        # F_DCID: 代次标识（不含时间戳，多次传代会重复）
        new_dcid = f"{zzid}-T-{new_dc}-CD"
        
        # F_GDBH: 工单编号（含时间戳，每次传代唯一）
        ts = datetime.now().strftime("%Y%m%d%H%M%S")
        gdbh = f"{zzid}{new_dc}{ts}"
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
                F_SQR, F_PYSJ, F_FJ, F_GDBH, F_GDLX, F_PARENT_ID, F_GDZT, F_AZ
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                base.get("F_XM"), base.get("F_BRBH"), zzid, base.get("F_BBLX"),
                new_dc, new_dcid, target_zzbw, base.get("F_XYZL"), target_ybgg, "传代",
                base.get("F_SQR"), target_pysj, fj_json, gdbh, base.get("F_GDLX"), orig_gdid, "已创建",
                base.get("F_AZ"),  # 继承父工单的癌种
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
    
    return result


def create_thaw_order_from_order(
    orig_gdid: int,
    zzbw: str | None = None,
    ybgg: str | None = None,
    pysj_str: str | None = None,
    file_ids: List[int] | None = None,
    creator_code: str = "",
) -> Dict[str, Any]:
    adapter = get_adapter()
    with adapter.connect() as conn:
        cur = conn.cursor()
        sql = "SELECT F_ZZID, F_XM, F_BRBH, F_BBLX, F_DC, F_DCID, F_ZZBW, F_XYZL, F_YBGG, F_SQR, F_GDLX, F_AZ FROM T_PYGD WHERE F_ID = ? ORDER BY F_ID DESC"
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
        # F_DCID: 代次标识（不含时间戳，多次复苏会重复）
        new_dcid = f"{zzid}-T-{new_dc}-FS"
        
        # F_GDBH: 工单编号（含时间戳，每次复苏唯一）
        ts = datetime.now().strftime("%Y%m%d%H%M%S")
        gdbh = f"{zzid}{new_dc}{ts}"
        pysj = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        
        # 判断样本来源：如果被复苏的原样本是P0（原代），则标记为"原代复苏"
        ybly = "原代复苏" if old_dc.upper() == "P0" else "复苏"

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
                F_SQR, F_PYSJ, F_FJ, F_GDBH, F_GDLX, F_PARENT_ID, F_GDZT, F_AZ
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                base.get("F_XM"), base.get("F_BRBH"), zzid, base.get("F_BBLX"),
                new_dc, new_dcid, target_zzbw, base.get("F_XYZL"), target_ybgg, ybly,
                base.get("F_SQR"), target_pysj, fj_json, gdbh, base.get("F_GDLX"), orig_gdid, "已创建",
                base.get("F_AZ"),  # 继承父工单的癌种
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
                    "样本来源": "复苏"
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
    
    return result


def get_culture_order_detail(rec_id: int) -> Dict[str, Any] | None:
    adapter = get_adapter()
    with adapter.connect() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT p.F_ID, p.F_GDBH, p.F_ZZID, p.F_XM, p.F_BRBH, p.F_BBLX,
                   p.F_DC, p.F_DCID, p.F_ZZBW, p.F_XYZL, p.F_QYFS, p.F_YBLY, p.F_YBGG,
                   p.F_SQR, p.F_PYSJ, p.F_FJ, p.F_CJSJ, p.F_AZ
            FROM T_PYGD p WHERE p.F_ID = ?
            """,
            (rec_id,),
        )
        row = cur.fetchone()
        if not row:
            return None
        cols = [c[0] for c in cur.description]
        it = dict(zip(cols, row))
        for k in ("F_PYSJ", "F_CJSJ"):
            v = it.get(k)
            if v is not None:
                try:
                    it[k] = v.strftime("%Y-%m-%d %H:%M:%S") if hasattr(v, "strftime") else str(v)
                except Exception:
                    it[k] = str(v)
        # F_FJ：将ID数组JSON转换为详情数组
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
        return it


def update_culture_order(data: Dict[str, Any]) -> bool:
    adapter = get_adapter()
    rec_id = int(data.get("F_ID"))
    allowed_fields = ["F_ZZBW", "F_XYZL", "F_YBLY", "F_YBGG", "F_PYSJ"]
    sets: List[str] = []
    params: List[Any] = []
    for f in allowed_fields:
        if f in data and data[f] is not None:
            sets.append(f"{f} = ?")
            params.append(data[f])
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


def update_culture_order_with_files(data: Dict[str, Any], add_file_ids: List[int] | None, del_file_ids: List[int] | None = None) -> Dict[str, Any] | None:
    """更新基础字段，并对附件ID做增删：
    - 追加 add_file_ids 到 T_PYGD.F_FJ（ID数组JSON）
    - 删除 del_file_ids：从 JSON 中移除，同时物理删除文件并删除 T_FILES 记录
    返回：{"F_ID": id, "F_FJ": [{id,path,name}...]}
    """
    adapter = get_adapter()
    rec_id = int(data.get("F_ID"))
    allowed_fields = ["F_ZZBW", "F_XYZL", "F_YBLY", "F_YBGG", "F_PYSJ"]
    sets: List[str] = []
    params: List[Any] = []
    for f in allowed_fields:
        if f in data and data[f] is not None:
            sets.append(f"{f} = ?")
            params.append(data[f])
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
        # 删除
        if del_file_ids:
            to_del = {int(x) for x in del_file_ids if isinstance(x, (int, str)) and str(x).isdigit()}
            if to_del:
                existing_ids = [i for i in existing_ids if i not in to_del]
                # 物理删除与记录删除
                from app.crud.file_center import get_file_by_id, delete_file_physical, delete_file_record
                for fid in to_del:
                    try:
                        rowf = get_file_by_id(int(fid))
                        fpath = rowf.get("F_PATH") if rowf else None
                        if fpath:
                            delete_file_physical(fpath)
                        delete_file_record(int(fid))
                    except Exception:
                        pass
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
        return {"F_ID": rec_id, "F_FJ": details}

def remove_culture_attachment(order_id: int, file_id: int) -> Dict[str, Any] | None:
    """根据工单ID与文件ID，删除附件：
    - 查 `T_FILES` 获得路径
    - 删除本地物理文件；删除 `T_FILES` 记录
    - 从 `T_PYGD.F_FJ`（路径数组JSON）中移除相应路径并回写
    - 返回被删除项与剩余附件清单
    """
    adapter = get_adapter()
    with adapter.connect() as conn:
        cur = conn.cursor()
        # 查 file 记录
        from app.crud.file_center import get_file_by_id, delete_file_physical, delete_file_record, get_files_by_ids
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
        # 生成最新附件清单
        # 物理删除 + 删记录
        delete_file_physical(fpath)
        delete_file_record(int(file_id))

        # 组合剩余附件清单（通过路径反查 ID/名称）
        from app.crud.file_center import get_files_by_paths
        file_rows = get_files_by_paths(new_paths)
        left_files = [{"id": r.get("F_ID"), "path": r.get("F_PATH"), "name": r.get("F_ORIGINAL_NAME")} for r in file_rows]
        conn.commit()
        return {"F_ID": order_id, "removed": {"id": int(file_id), "path": fpath}, "F_FJ": left_files}


def export_culture_orders(q: Dict[str, Any], operator: str | None = None) -> Dict[str, Any]:
    """
    导出培养工单列表到 Excel,保存到文件中心并返回文件信息
    
    Args:
        q: 查询参数(与 list_culture_orders 相同)
        operator: 操作人
        
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
        pred, p = adapter.like("'已创建'", query_params["F_GDZT"].strip())
        where.append(pred)
        params.extend(p)

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

    where_sql = (" WHERE " + " AND ".join(where)) if where else ""

    # 查询语句(复用 list_culture_orders 的逻辑)
    # 注意: count 查询和数据查询分开,避免子查询中使用 ORDER BY 的限制
    # 使用适配器方法生成跨数据库兼容的子查询
    subq_dcjl = adapter.subquery_limit1("d.F_ID", "T_DCJL d", "d.F_GDID = p.F_ID", "d.F_ID DESC")
    
    base_query_without_order = f"""
SELECT p.F_ID, p.F_GDBH, p.F_ZZID, p.F_XM, p.F_BRBH, p.F_BBLX,
       p.F_DC, p.F_DCID, p.F_ZZBW, p.F_XYZL, p.F_QYFS, p.F_YBLY, p.F_YBGG,
       p.F_SQR, p.F_PYSJ, p.F_FJ, p.F_CJSJ,
       p.F_GDLX,
       p.F_GDZT,
       p.F_ZKZT AS F_SHZT,
       ({subq_dcjl}) AS F_DCJL,
       CASE WHEN EXISTS (SELECT 1 FROM T_YCL y WHERE y.F_GDID = p.F_ID) THEN 1 ELSE 0 END AS F_YCL,
       p.F_PARENT_ID,
       h.F_HSSJ,
       h.F_HSSL
FROM T_PYGD p
LEFT JOIN T_HSJL h ON h.F_GDID = p.F_ID
{where_sql}
"""
    
    # 完整查询(带排序)
    base_core = base_query_without_order + "ORDER BY p.F_ID DESC"
    
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
        for key in ("F_PYSJ", "F_CJSJ", "F_HSSJ"):
            if row.get(key) and isinstance(row[key], datetime):
                row[key] = row[key].strftime("%Y-%m-%d %H:%M:%S")
        
        # 计算培养天数
        if row.get("F_PYSJ") and row.get("F_HSSJ"):
            try:
                pysj = datetime.strptime(str(row["F_PYSJ"]), "%Y-%m-%d %H:%M:%S")
                hssj = datetime.strptime(str(row["F_HSSJ"]), "%Y-%m-%d %H:%M:%S")
                row["F_PYTS"] = (hssj - pysj).days
            except:
                row["F_PYTS"] = None
        else:
            row["F_PYTS"] = None
    
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
    operator: str | None = None
) -> Dict[str, Any]:
    """
    批量将培养工单标记为不合格
    
    Args:
        gdids: 工单ID数组
        reason: 不合格原因
        operator: 操作人姓名
        
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
            # 先获取工单信息（用于质控记录）
            cur.execute(
                """
                SELECT F_ID, F_GDBH, F_ZZID, F_DCID, F_DC, F_PYSJ, F_SQR 
                FROM T_PYGD 
                WHERE F_ID = ?
                """,
                (int(gdid),)
            )
            order_row = cur.fetchone()
            
            if not order_row:
                logger.warning(f"工单不存在: ID={gdid}")
                continue
            
            # 更新不合格标识和原因
            cur.execute(
                "UPDATE T_PYGD SET F_BHG = 'true', F_BHGYY = ?, F_GDZT = '不合格' WHERE F_ID = ?",
                (reason or '', int(gdid))
            )
            
            if cur.rowcount > 0:
                updated_gdids.append(int(gdid))
                # 保存工单信息
                order_info_list.append({
                    "F_ID": order_row[0],
                    "F_GDBH": order_row[1],
                    "F_ZZID": order_row[2],
                    "F_DCID": order_row[3],
                    "F_DC": order_row[4],
                    "F_PYSJ": order_row[5],
                    "F_SQR": order_row[6]
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
                    
                    # 标为不合格的变更信息
                    changes = {
                        "操作": "标为不合格",
                        "关键信息": {
                            "工单ID": gdid,
                            "工单编号": gdbh,
                            "不合格原因": reason or "未填写原因"
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
        "F_BHG": "true",
        "F_BHGYY": reason or ''
    }
    
    if qc_audit_ids:
        result["qc_audit_ids"] = qc_audit_ids
    
    return result