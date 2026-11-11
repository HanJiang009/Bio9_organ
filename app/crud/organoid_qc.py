"""
类器官质控模块 - CRUD操作
"""
from typing import Dict, Any, List
from datetime import datetime
import json

from app.db.factory import get_adapter


def _has_consent_files(raw) -> bool:
    """判断 T_YBZJ.F_ZQTYS_ID 是否包含有效文件"""
    if raw is None:
        return False
    if isinstance(raw, list):
        return len(raw) > 0
    if isinstance(raw, int):
        return True
    if isinstance(raw, str):
        s = raw.strip()
        if not s or s in ("[]", "null", "NULL"):
            return False
        try:
            parsed = json.loads(s)
            if isinstance(parsed, list):
                return len(parsed) > 0
            return bool(parsed)
        except Exception:
            return True
    return bool(raw)


def _format_pdf_urls(pdf_json_str: str | None) -> Dict[str, Any]:
    """
    将F_BG_PDF的JSON字符串转换为包含URL的结构（不带uploads前缀）
    
    Args:
        pdf_json_str: PDF的JSON字符串
        
    Returns:
        {
            "generated": {"id": 123, "name": "xxx.pdf", "path": "drug_test_report/xxx.pdf"},
            "uploaded": [{"id": 456, "name": "yyy.pdf", "path": "xxx/yyy.pdf"}]
        }
    """
    from app.crud.file_center import get_file_by_id
    import os
    
    if not pdf_json_str:
        return {"generated": None, "uploaded": []}
    
    try:
        pdf_data = json.loads(pdf_json_str) if isinstance(pdf_json_str, str) else pdf_json_str
        result = {"generated": None, "uploaded": []}
        
        # 处理系统生成的PDF
        if pdf_data.get("generated"):
            file_info = get_file_by_id(pdf_data["generated"])
            if file_info:
                # 获取F_PATH并清理为相对路径
                path = file_info.get("F_PATH", "")
                
                # 如果是Windows绝对路径，提取相对路径部分
                if "\\" in path or ":" in path:
                    # 尝试提取 uploads/ 之后的路径
                    if "uploads" in path:
                        parts = path.split("uploads")
                        if len(parts) > 1:
                            path = parts[-1].lstrip("\\/")
                    else:
                        # 如果没有uploads，使用文件名
                        path = os.path.basename(path)
                
                # 移除开头的斜杠和 uploads/ 前缀
                path = path.lstrip("/\\")
                if path.startswith("uploads/") or path.startswith("uploads\\"):
                    path = path[8:]
                
                # 统一使用正斜杠
                path = path.replace("\\", "/")
                
                result["generated"] = {
                    "id": file_info.get("F_ID"),
                    "name": file_info.get("F_ORIGINAL_NAME") or "",
                    "path": path  # 相对路径，如 "drug_test_report/xxx.pdf"
                }
        
        # 处理用户上传的PDF
        for file_id in pdf_data.get("uploaded", []):
            file_info = get_file_by_id(file_id)
            if file_info:
                # 获取F_PATH并清理为相对路径
                path = file_info.get("F_PATH", "")
                
                # 如果是Windows绝对路径，提取相对路径部分
                if "\\" in path or ":" in path:
                    # 尝试提取 uploads/ 之后的路径
                    if "uploads" in path:
                        parts = path.split("uploads")
                        if len(parts) > 1:
                            path = parts[-1].lstrip("\\/")
                    else:
                        # 如果没有uploads，使用文件名
                        path = os.path.basename(path)
                
                # 移除开头的斜杠和 uploads/ 前缀
                path = path.lstrip("/\\")
                if path.startswith("uploads/") or path.startswith("uploads\\"):
                    path = path[8:]
                
                # 统一使用正斜杠
                path = path.replace("\\", "/")
                
                result["uploaded"].append({
                    "id": file_info.get("F_ID"),
                    "name": file_info.get("F_ORIGINAL_NAME") or "",
                    "path": path
                })
        
        return result
    except Exception:
        return {"generated": None, "uploaded": []}


def list_qc_culture_orders(q: Dict[str, Any], user_ktype: str | None = None,
                          user: Dict[str, Any] | None = None,
                          scope: str | None = None) -> Dict[str, Any]:
    """
    获取类器官质控培养工单列表
    
    相比普通培养工单列表，额外返回：
    - F_JSZK: 接收质控PDF (T_YBZJ.F_PDF)
    - F_YBZLPFB: 样本质量评分表PDF (T_YCL.F_PDF)
    - F_LQGZK: 类器官质控PDF (T_ZKJL.F_PDF)
    - F_LQGFX: 类器官放行报告PDF (T_ZKJL.F_PDF_FXJC)
    - F_YWSYBG: 药物实验报告PDF (T_SYJH.F_BG_PDF，关联最新实验计划)
    - F_DWSYBG: 动物实验报告PDF (T_DWSY_JH.F_BG_PDF，关联最新实验计划)
    - F_SYCLSX: 剩余处理时效（小时）= F_KSCLSJ - F_LTSJ
    """
    adapter = get_adapter()
    where: List[str] = []
    params: List[Any] = []

    def like(col: str, key: str | None):
        if key:
            pred, p = adapter.like(col, key.strip())
            where.append(pred)
            params.extend(p)

    # 基础筛选条件
    like("p.F_XM", q.get("F_XM"))
    like("p.F_BRBH", q.get("F_BRBH"))
    like("p.F_ZZID", q.get("F_ZZID"))
    like("p.F_GDBH", q.get("F_GDBH"))
    like("p.F_GDLX", q.get("F_GDLX"))
    like("p.F_DC", q.get("F_DC"))
    like("p.F_DCID", q.get("F_DCID"))
    like("p.F_ZZBW", q.get("F_ZZBW"))
    like("p.F_XYZL", q.get("F_XYZL"))
    like("p.F_SQR", q.get("F_SQR"))  # 培养人筛选

    # 工单状态筛选：精确匹配
    if q.get("F_GDZT"):
        where.append("p.F_GDZT = ?")
        params.append(q["F_GDZT"].strip())

    # 日期范围筛选
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

    # 子查询
    subq_dcjl = adapter.subquery_limit1("d.F_ID", "T_DCJL d", "d.F_GDID = p.F_ID", "d.F_ID DESC")

    # 主查询SQL - 添加质控相关字段
    # 子查询获取最新的实验计划ID和F_BG_PDF
    subq_syjh = adapter.subquery_limit1("syjh.F_BG_PDF", "T_SYJH syjh", "syjh.F_GDID = p.F_ID", "syjh.F_CJSJ DESC")
    subq_dwsy_jh = adapter.subquery_limit1("dwsy.F_BG_PDF", "T_DWSY_JH dwsy", "dwsy.F_GDID = p.F_ID", "dwsy.F_CJSJ DESC")
    
    base_core = f"""
SELECT p.F_ID, p.F_GDBH, p.F_ZZID, p.F_XM, p.F_BRBH, p.F_BBLX,
       p.F_DC, p.F_DCID, p.F_ZZBW, p.F_XYZL, p.F_QYFS, p.F_YBLY, p.F_YBGG, p.F_CDQXBZS, p.F_FSQXBZS,
       p.F_SQR, p.F_PYSJ, p.F_FJ, p.F_CJSJ,
       p.F_GDLX,
       p.F_GDZT,
       p.F_ZKZT AS F_SHZT,
       ({subq_dcjl}) AS F_DCJL,
       CASE WHEN EXISTS (SELECT 1 FROM T_YCL y WHERE y.F_GDID = p.F_ID) THEN 1 ELSE 0 END AS F_YCL,
       p.F_PARENT_ID,
       h.F_HSSJ,
       h.F_HSSL,
       ybzj.F_PDF AS F_JSZK,
       ybzj.F_ZQTYS AS F_ZQTYS,
       ybzj.F_ZQTYS_ID AS F_ZQTYS_ID,
       ycl.F_PDF AS F_YBZLPFB,
       zkjl.F_PDF AS F_LQGZK,
       zkjl.F_PDF_FXJC AS F_LQGFX,
       ycl.F_LTSJ,
       ycl.F_KSCLSJ,
       ({subq_syjh}) AS F_YWSYBG_RAW,
       ({subq_dwsy_jh}) AS F_DWSYBG_RAW
FROM T_PYGD p
LEFT JOIN T_HSJL h ON h.F_GDID = p.F_ID
LEFT JOIN T_YBZJ ybzj ON ybzj.F_ZZID = p.F_ZZID
LEFT JOIN T_YCL ycl ON ycl.F_GDID = p.F_ID
LEFT JOIN T_ZKJL zkjl ON zkjl.F_GDID = p.F_ID
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
            """格式化每一条记录"""
            # 格式化时间字段
            for k in ("F_PYSJ", "F_CJSJ", "F_HSSJ", "F_LTSJ", "F_KSCLSJ"):
                v = it.get(k)
                if v is not None:
                    try:
                        it[k] = v.strftime("%Y-%m-%d %H:%M:%S") if hasattr(v, "strftime") else str(v)
                    except Exception:
                        it[k] = str(v)
            
            # 转换数值字段为浮点数
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
            
            # 计算剩余处理时效（小时）: 仅针对代次 P0 的工单，其他留空
            syclsx = None
            if (it.get("F_DC") or "").strip() == "P0":
                ltsj = it.get("F_LTSJ")
                ksclsj = it.get("F_KSCLSJ")
                
                if ltsj:
                    try:
                        ltsj_dt = None
                        reference_dt = None  # 参考时间（开始处理时间或当前时间）
                        
                        # 解析离体时间
                        if isinstance(ltsj, str):
                            try:
                                ltsj_dt = datetime.fromisoformat(ltsj)
                            except Exception:
                                try:
                                    ltsj_dt = datetime.strptime(ltsj, "%Y-%m-%d %H:%M:%S")
                                except Exception:
                                    pass
                        elif hasattr(ltsj, "date"):
                            ltsj_dt = ltsj
                        
                        # 确定参考时间：优先使用开始处理时间，否则使用当前时间
                        if ksclsj:
                            if isinstance(ksclsj, str):
                                try:
                                    reference_dt = datetime.fromisoformat(ksclsj)
                                except Exception:
                                    try:
                                        reference_dt = datetime.strptime(ksclsj, "%Y-%m-%d %H:%M:%S")
                                    except Exception:
                                        pass
                            elif hasattr(ksclsj, "date"):
                                reference_dt = ksclsj
                        
                        # 如果没有开始处理时间，使用当前时间
                        if not reference_dt:
                            reference_dt = datetime.now()
                        
                        if ltsj_dt and reference_dt:
                            # 计算已过时间（小时）
                            time_delta = reference_dt - ltsj_dt
                            elapsed_hours = time_delta.total_seconds() / 3600
                            
                            # 计算剩余时间：48小时 - 已过时间
                            syclsx = round(48 - elapsed_hours, 2)  # 保留2位小数
                    except Exception:
                        syclsx = None
            it["F_SYCLSX"] = syclsx
            
            # F_ZQTYS：登记时填写；若为空但已上传知情同意书文件，则显示「是」
            zqtys_id_raw = it.pop("F_ZQTYS_ID", None)
            zqtys_val = it.get("F_ZQTYS")
            if not (zqtys_val and str(zqtys_val).strip()):
                if _has_consent_files(zqtys_id_raw):
                    it["F_ZQTYS"] = "是"

            # 药物实验报告（从实验计划F_BG_PDF字段获取）
            ywsybg_raw = it.pop("F_YWSYBG_RAW", None)
            it["F_YWSYBG"] = _format_pdf_urls(ywsybg_raw)

            # 动物实验报告（从动物实验计划F_BG_PDF字段获取）
            dwsybg_raw = it.pop("F_DWSYBG_RAW", None)
            it["F_DWSYBG"] = _format_pdf_urls(dwsybg_raw)
            
            # 解析PDF字段（JSON格式）
            for pdf_field in ["F_JSZK", "F_YBZLPFB", "F_LQGZK", "F_LQGFX"]:
                pdf_raw = it.get(pdf_field)
                if pdf_raw:
                    try:
                        it[pdf_field] = json.loads(pdf_raw) if isinstance(pdf_raw, str) else pdf_raw
                    except Exception:
                        it[pdf_field] = None
                else:
                    it[pdf_field] = None
            
            # 处理F_YCL字段
            if "F_YCL" in it:
                try:
                    it["F_YCL"] = bool(int(it.get("F_YCL"))) if it.get("F_YCL") is not None else False
                except Exception:
                    it["F_YCL"] = False
            
            # 处理附件字段
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

        return {
            "items": items,
            "total": total,
            "skip": q.get("skip") or 0,
            "limit": q.get("limit") or len(items),
        }

