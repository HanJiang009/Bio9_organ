"""
质控文件统一上传模块
"""
import json
from typing import Dict, Any, Optional, List
from datetime import datetime

from app.db.factory import get_adapter


def upload_qc_file(
    zkjd: str,
    file_ids: List[int],
    uploader: str,
    gdid: Optional[int] = None,
    gdbh: Optional[str] = None,
    zzid: Optional[str] = None
) -> Dict[str, Any]:
    """
    质控文件统一上传接口
    
    Args:
        zkjd: 质控阶段（知情同意书、接收质控、样本质量评分表、类器官质控、类器官放行报告、药物试验报告）
        file_ids: 上传的文件ID列表
        uploader: 上传人
        gdid: 工单ID（可选，与gdbh/zzid三选一）
        gdbh: 工单编号（可选，与gdid/zzid三选一）
        zzid: 标本号（可选，与gdid/gdbh三选一，仅支持"知情同意书"和"接收质控"）
    
    Returns:
        Dict: {"success": True, "message": "上传成功", "file_ids": [123, 456], "gdid": 93, "zzid": "xxx", "gdbh": "xxx"}
    """
    if not file_ids:
        return {"success": False, "message": "未提供文件"}
    
    # 参数校验：三选一
    if not any([gdid, gdbh, zzid]):
        return {"success": False, "message": "必须提供 F_GDID、F_GDBH 或 F_ZZID 其中之一"}
    
    if sum([bool(gdid), bool(gdbh), bool(zzid)]) > 1:
        return {"success": False, "message": "F_GDID、F_GDBH、F_ZZID 只能提供其中之一"}
    
    # 使用F_ZZID时的限制
    if zzid:
        allowed_zkjd = ["知情同意书", "接收质控"]
        if zkjd not in allowed_zkjd:
            return {
                "success": False, 
                "message": f"使用标本号上传时，仅支持质控阶段：{', '.join(allowed_zkjd)}"
            }
    
    adapter = get_adapter()
    
    # 1. 根据不同入参获取工单信息
    final_gdid = None
    final_zzid = None
    final_gdbh = None
    
    if gdid:
        # 通过工单ID查询
        gd_sql = "SELECT F_ID, F_ZZID, F_GDBH FROM T_PYGD WHERE F_ID = ?"
        gd_rows, gd_cols = adapter.fetch_all(gd_sql, [gdid])
        if not gd_rows:
            return {"success": False, "message": f"工单ID {gdid} 不存在"}
        gd_info = dict(zip(gd_cols, gd_rows[0]))
        final_gdid = gd_info.get("F_ID")
        final_zzid = gd_info.get("F_ZZID")
        final_gdbh = gd_info.get("F_GDBH")
        
    elif gdbh:
        # 通过工单编号查询
        gd_sql = "SELECT F_ID, F_ZZID, F_GDBH FROM T_PYGD WHERE F_GDBH = ?"
        gd_rows, gd_cols = adapter.fetch_all(gd_sql, [gdbh])
        if not gd_rows:
            return {"success": False, "message": f"工单编号 {gdbh} 不存在"}
        gd_info = dict(zip(gd_cols, gd_rows[0]))
        final_gdid = gd_info.get("F_ID")
        final_zzid = gd_info.get("F_ZZID")
        final_gdbh = gd_info.get("F_GDBH")
        
    else:  # zzid
        # 使用标本号，不查询工单（因为知情同意书和接收质控不强关联工单）
        final_zzid = zzid
        final_gdid = None
        final_gdbh = None
    
    if not final_zzid:
        return {"success": False, "message": "无法获取标本号"}
    
    with adapter.connect() as conn:
        cur = conn.cursor()
        
        try:
            # 2. 根据质控阶段，将文件ID写入对应表字段
            if zkjd == "知情同意书":
                # 追加到 T_YBZJ.F_ZQTYS_ID 数组中
                result = _append_to_ybzj_zqtys(cur, final_zzid, file_ids, adapter)
                
            elif zkjd == "接收质控":
                # 追加到 T_YBZJ.F_PDF 的 "uploaded" 数组
                result = _append_to_ybzj_pdf(cur, final_zzid, file_ids, adapter)
                
            elif zkjd == "样本质量评分表":
                # 追加到 T_YCL.F_PDF 的 "uploaded" 数组
                if not final_gdid:
                    result = {"success": False, "message": "样本质量评分表上传必须提供工单ID或工单编号"}
                else:
                    result = _append_to_ycl_pdf(cur, final_gdid, file_ids, adapter)
                
            elif zkjd == "类器官质控":
                # 追加到 T_ZKJL.F_PDF 的 "uploaded" 数组
                if not final_gdid:
                    result = {"success": False, "message": "类器官质控上传必须提供工单ID或工单编号"}
                else:
                    result = _append_to_zkjl_pdf(cur, final_gdid, file_ids, adapter)
                
            elif zkjd == "类器官放行报告":
                # 追加到 T_ZKJL.F_PDF_FXJC 的 "uploaded" 数组
                if not final_gdid:
                    result = {"success": False, "message": "类器官放行报告上传必须提供工单ID或工单编号"}
                else:
                    result = _append_to_zkjl_pdf_fxjc(cur, final_gdid, file_ids, adapter)
                
            elif zkjd == "药物试验报告":
                # 留白（模块未开发）
                result = {"success": False, "message": "药物试验模块尚未开发"}
                
            else:
                result = {"success": False, "message": f"未知的质控阶段: {zkjd}"}
            
            if not result.get("success"):
                conn.rollback()
                return result
            
            # 3. 记录上传日志到 T_ZKSC（包含F_GDID）
            log_sql = """
                INSERT INTO T_ZKSC (F_ZZID, F_GDBH, F_GDID, F_ZKJD, F_FJ, F_SCR, F_SCSJ)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """
            file_ids_json = json.dumps(file_ids, ensure_ascii=False)
            upload_time = datetime.now()
            
            cur.execute(log_sql, [final_zzid, final_gdbh, final_gdid, zkjd, file_ids_json, uploader, upload_time])
            
            conn.commit()
            
            return {
                "success": True,
                "message": f"{zkjd}上传成功",
                "file_ids": file_ids,
                "gdid": final_gdid,
                "zzid": final_zzid,
                "gdbh": final_gdbh
            }
            
        except Exception as e:
            conn.rollback()
            return {"success": False, "message": f"上传失败: {str(e)}"}


def _append_to_ybzj_zqtys(cur, zzid: str, file_ids: List[int], adapter) -> Dict[str, Any]:
    """追加文件ID到 T_YBZJ.F_ZQTYS_ID 数组"""
    try:
        # 查询现有记录
        select_sql = "SELECT F_ZQTYS_ID FROM T_YBZJ WHERE F_ZZID = ?"
        cur.execute(select_sql, [zzid])
        row = cur.fetchone()
        
        if not row:
            return {"success": False, "message": f"未找到标本号 {zzid} 的样本质检记录"}
        
        # 解析现有ID数组
        existing_ids = []
        raw_value = row[0]
        if raw_value:
            try:
                if isinstance(raw_value, str):
                    parsed = json.loads(raw_value)
                    # 确保解析结果是列表
                    if isinstance(parsed, list):
                        existing_ids = parsed
                    elif isinstance(parsed, int):
                        existing_ids = [parsed]
                    else:
                        existing_ids = []
                elif isinstance(raw_value, list):
                    existing_ids = raw_value
                elif isinstance(raw_value, int):
                    existing_ids = [raw_value]
            except:
                # 解析失败时，尝试作为单个整数处理
                try:
                    existing_ids = [int(raw_value)]
                except:
                    existing_ids = []
        
        # 追加新ID（去重）
        for fid in file_ids:
            if fid not in existing_ids:
                existing_ids.append(fid)
        
        # 更新数据库
        new_value = json.dumps(existing_ids, ensure_ascii=False)
        update_sql = "UPDATE T_YBZJ SET F_ZQTYS_ID = ? WHERE F_ZZID = ?"
        cur.execute(update_sql, [new_value, zzid])
        
        return {"success": True}
    except Exception as e:
        return {"success": False, "message": f"更新知情同意书失败: {str(e)}"}


def _append_to_ybzj_pdf(cur, zzid: str, file_ids: List[int], adapter) -> Dict[str, Any]:
    """追加文件ID到 T_YBZJ.F_PDF 的 uploaded 数组"""
    try:
        select_sql = "SELECT F_PDF FROM T_YBZJ WHERE F_ZZID = ?"
        cur.execute(select_sql, [zzid])
        row = cur.fetchone()
        
        if not row:
            return {"success": False, "message": f"未找到标本号 {zzid} 的样本质检记录"}
        
        # 解析PDF字段
        pdf_data = {"generated": None, "uploaded": []}
        raw_value = row[0]
        if raw_value:
            try:
                if isinstance(raw_value, str):
                    parsed = json.loads(raw_value)
                    if isinstance(parsed, dict):
                        pdf_data = parsed
                elif isinstance(raw_value, dict):
                    pdf_data = raw_value
            except:
                pass
        
        # 确保有uploaded字段且为列表
        if "uploaded" not in pdf_data or not isinstance(pdf_data.get("uploaded"), list):
            pdf_data["uploaded"] = []
        
        # 追加新ID（去重）
        for fid in file_ids:
            if fid not in pdf_data["uploaded"]:
                pdf_data["uploaded"].append(fid)
        
        # 更新数据库
        new_value = json.dumps(pdf_data, ensure_ascii=False)
        update_sql = "UPDATE T_YBZJ SET F_PDF = ? WHERE F_ZZID = ?"
        cur.execute(update_sql, [new_value, zzid])
        
        return {"success": True}
    except Exception as e:
        return {"success": False, "message": f"更新接收质控PDF失败: {str(e)}"}


def _append_to_ycl_pdf(cur, gdid: int, file_ids: List[int], adapter) -> Dict[str, Any]:
    """追加文件ID到 T_YCL.F_PDF 的 uploaded 数组"""
    try:
        select_sql = "SELECT F_PDF FROM T_YCL WHERE F_GDID = ?"
        cur.execute(select_sql, [gdid])
        row = cur.fetchone()
        
        if not row:
            return {"success": False, "message": f"未找到工单 {gdid} 的预处理记录"}
        
        # 解析PDF字段
        pdf_data = {"generated": None, "uploaded": []}
        raw_value = row[0]
        if raw_value:
            try:
                if isinstance(raw_value, str):
                    parsed = json.loads(raw_value)
                    if isinstance(parsed, dict):
                        pdf_data = parsed
                elif isinstance(raw_value, dict):
                    pdf_data = raw_value
            except:
                pass
        
        # 确保有uploaded字段且为列表
        if "uploaded" not in pdf_data or not isinstance(pdf_data.get("uploaded"), list):
            pdf_data["uploaded"] = []
        
        # 追加新ID（去重）
        for fid in file_ids:
            if fid not in pdf_data["uploaded"]:
                pdf_data["uploaded"].append(fid)
        
        # 更新数据库
        new_value = json.dumps(pdf_data, ensure_ascii=False)
        update_sql = "UPDATE T_YCL SET F_PDF = ? WHERE F_GDID = ?"
        cur.execute(update_sql, [new_value, gdid])
        
        return {"success": True}
    except Exception as e:
        return {"success": False, "message": f"更新样本质量评分表PDF失败: {str(e)}"}


def _append_to_zkjl_pdf(cur, gdid: int, file_ids: List[int], adapter) -> Dict[str, Any]:
    """追加文件ID到 T_ZKJL.F_PDF 的 uploaded 数组"""
    try:
        select_sql = "SELECT F_PDF FROM T_ZKJL WHERE F_GDID = ?"
        cur.execute(select_sql, [gdid])
        row = cur.fetchone()
        
        if not row:
            return {"success": False, "message": f"未找到工单 {gdid} 的质控记录"}
        
        # 解析PDF字段
        pdf_data = {"generated": None, "uploaded": []}
        raw_value = row[0]
        if raw_value:
            try:
                if isinstance(raw_value, str):
                    parsed = json.loads(raw_value)
                    if isinstance(parsed, dict):
                        pdf_data = parsed
                elif isinstance(raw_value, dict):
                    pdf_data = raw_value
            except:
                pass
        
        # 确保有uploaded字段且为列表
        if "uploaded" not in pdf_data or not isinstance(pdf_data.get("uploaded"), list):
            pdf_data["uploaded"] = []
        
        # 追加新ID（去重）
        for fid in file_ids:
            if fid not in pdf_data["uploaded"]:
                pdf_data["uploaded"].append(fid)
        
        # 更新数据库
        new_value = json.dumps(pdf_data, ensure_ascii=False)
        update_sql = "UPDATE T_ZKJL SET F_PDF = ? WHERE F_GDID = ?"
        cur.execute(update_sql, [new_value, gdid])
        
        return {"success": True}
    except Exception as e:
        return {"success": False, "message": f"更新类器官质控PDF失败: {str(e)}"}


def _append_to_zkjl_pdf_fxjc(cur, gdid: int, file_ids: List[int], adapter) -> Dict[str, Any]:
    """追加文件ID到 T_ZKJL.F_PDF_FXJC 的 uploaded 数组"""
    try:
        select_sql = "SELECT F_PDF_FXJC FROM T_ZKJL WHERE F_GDID = ?"
        cur.execute(select_sql, [gdid])
        row = cur.fetchone()
        
        if not row:
            return {"success": False, "message": f"未找到工单 {gdid} 的质控记录"}
        
        # 解析PDF字段
        pdf_data = {"generated": None, "uploaded": []}
        raw_value = row[0]
        if raw_value:
            try:
                if isinstance(raw_value, str):
                    parsed = json.loads(raw_value)
                    if isinstance(parsed, dict):
                        pdf_data = parsed
                elif isinstance(raw_value, dict):
                    pdf_data = raw_value
            except:
                pass
        
        # 确保有uploaded字段且为列表
        if "uploaded" not in pdf_data or not isinstance(pdf_data.get("uploaded"), list):
            pdf_data["uploaded"] = []
        
        # 追加新ID（去重）
        for fid in file_ids:
            if fid not in pdf_data["uploaded"]:
                pdf_data["uploaded"].append(fid)
        
        # 更新数据库
        new_value = json.dumps(pdf_data, ensure_ascii=False)
        update_sql = "UPDATE T_ZKJL SET F_PDF_FXJC = ? WHERE F_GDID = ?"
        cur.execute(update_sql, [new_value, gdid])
        
        return {"success": True}
    except Exception as e:
        return {"success": False, "message": f"更新类器官放行报告PDF失败: {str(e)}"}


def get_upload_logs(
    zzid: Optional[str] = None,
    gdbh: Optional[str] = None,
    gdid: Optional[int] = None,
    zkjd: Optional[str] = None,
    skip: int = 0,
    limit: int = 50
) -> Dict[str, Any]:
    """
    查询质控上传日志
    
    Args:
        zzid: 标本号（可选）
        gdbh: 工单号（可选）
        gdid: 工单ID（可选）
        zkjd: 质控阶段（可选）
        skip: 跳过记录数
        limit: 返回记录数
    
    Returns:
        Dict: {"items": [...], "total": 100}
    """
    adapter = get_adapter()
    
    where_clauses = []
    params = []
    
    if zzid:
        where_clauses.append("F_ZZID = ?")
        params.append(zzid)
        # 使用标本号查询时，只返回知情同意书和接收质控两个阶段
        if not zkjd:  # 如果没有指定阶段，则自动过滤
            where_clauses.append("F_ZKJD IN (?, ?)")
            params.append("知情同意书")
            params.append("接收质控")
    
    if gdbh:
        where_clauses.append("F_GDBH = ?")
        params.append(gdbh)
    
    if gdid:
        where_clauses.append("F_GDID = ?")
        params.append(gdid)
    
    if zkjd:
        where_clauses.append("F_ZKJD = ?")
        params.append(zkjd)
    
    where_sql = " WHERE " + " AND ".join(where_clauses) if where_clauses else ""
    
    # 查询总数
    count_sql = f"SELECT COUNT(*) FROM T_ZKSC{where_sql}"
    count_rows, _ = adapter.fetch_all(count_sql, params)
    total = int(count_rows[0][0]) if count_rows else 0
    
    # 查询数据
    base_sql = f"""
        SELECT F_ID, F_ZZID, F_GDBH, F_GDID, F_ZKJD, F_FJ, F_SCR, F_SCSJ
        FROM T_ZKSC
        {where_sql}
        ORDER BY F_SCSJ DESC
    """
    
    paginated_sql, extra_params = adapter.paginate(base_sql, skip, limit)
    rows, cols = adapter.fetch_all(paginated_sql, params + extra_params)
    
    items = []
    for row in rows:
        item = dict(zip(cols, row))
        
        # 解析F_FJ（JSON数组）
        file_ids = []
        if item.get("F_FJ"):
            try:
                file_ids = json.loads(item["F_FJ"])
            except:
                file_ids = []
        
        # 查询文件详细信息
        file_details = []
        if file_ids:
            try:
                # 构建IN查询
                placeholders = ", ".join(["?"] * len(file_ids))
                file_sql = f"""
                    SELECT F_ID, F_ORIGINAL_NAME, F_PATH 
                    FROM T_FILES 
                    WHERE F_ID IN ({placeholders})
                """
                file_rows, file_cols = adapter.fetch_all(file_sql, file_ids)
                
                for file_row in file_rows:
                    file_info = dict(zip(file_cols, file_row))
                    file_details.append({
                        "id": file_info.get("F_ID"),
                        "name": file_info.get("F_ORIGINAL_NAME"),
                        "path": file_info.get("F_PATH")
                    })
            except Exception as e:
                # 查询失败时，只返回ID列表
                file_details = [{"id": fid, "name": None, "path": None} for fid in file_ids]
        
        item["F_FJ"] = file_details
        
        # 格式化时间
        if item.get("F_SCSJ"):
            try:
                item["F_SCSJ"] = item["F_SCSJ"].strftime("%Y-%m-%d %H:%M:%S")
            except:
                pass
        
        items.append(item)
    
    return {"items": items, "total": total}


def delete_upload_record(record_id: int) -> Dict[str, Any]:
    """
    删除上传记录
    
    Args:
        record_id: T_ZKSC表的F_ID
    
    Returns:
        Dict: {"success": True, "message": "删除成功"}
    """
    adapter = get_adapter()
    
    # 1. 查询记录详情
    select_sql = "SELECT F_ID, F_ZZID, F_GDBH, F_GDID, F_ZKJD, F_FJ FROM T_ZKSC WHERE F_ID = ?"
    rows, cols = adapter.fetch_all(select_sql, [record_id])
    
    if not rows:
        return {"success": False, "message": f"上传记录 {record_id} 不存在"}
    
    record = dict(zip(cols, rows[0]))
    zkjd = record.get("F_ZKJD")
    fj_json = record.get("F_FJ")
    gdid = record.get("F_GDID")
    zzid = record.get("F_ZZID")
    
    # 解析文件ID列表
    file_ids = []
    if fj_json:
        try:
            file_ids = json.loads(fj_json) if isinstance(fj_json, str) else fj_json
        except:
            pass
    
    if not file_ids:
        # 没有关联文件，直接删除记录
        with adapter.connect() as conn:
            cur = conn.cursor()
            delete_sql = "DELETE FROM T_ZKSC WHERE F_ID = ?"
            cur.execute(delete_sql, [record_id])
            conn.commit()
        return {"success": True, "message": "上传记录已删除（无关联文件）"}
    
    # 2. 从对应表中移除文件ID
    with adapter.connect() as conn:
        cur = conn.cursor()
        
        try:
            if zkjd == "知情同意书":
                result = _remove_from_ybzj_zqtys(cur, zzid, file_ids, adapter)
            elif zkjd == "接收质控":
                result = _remove_from_ybzj_pdf(cur, zzid, file_ids, adapter)
            elif zkjd == "样本质量评分表":
                result = _remove_from_ycl_pdf(cur, gdid, file_ids, adapter)
            elif zkjd == "类器官质控":
                result = _remove_from_zkjl_pdf(cur, gdid, file_ids, adapter)
            elif zkjd == "类器官放行报告":
                result = _remove_from_zkjl_pdf_fxjc(cur, gdid, file_ids, adapter)
            else:
                result = {"success": True}  # 未知阶段，只删除记录
            
            if not result.get("success"):
                conn.rollback()
                return result
            
            # 3. 删除T_ZKSC记录
            delete_sql = "DELETE FROM T_ZKSC WHERE F_ID = ?"
            cur.execute(delete_sql, [record_id])
            
            conn.commit()
            return {"success": True, "message": f"上传记录已删除，已从{zkjd}中移除 {len(file_ids)} 个文件"}
            
        except Exception as e:
            conn.rollback()
            return {"success": False, "message": f"删除失败: {str(e)}"}


def _remove_from_ybzj_zqtys(cur, zzid: str, file_ids: List[int], adapter) -> Dict[str, Any]:
    """从 T_YBZJ.F_ZQTYS_ID 数组中移除文件ID"""
    try:
        select_sql = "SELECT F_ZQTYS_ID FROM T_YBZJ WHERE F_ZZID = ?"
        cur.execute(select_sql, [zzid])
        row = cur.fetchone()
        
        if not row:
            return {"success": True}  # 记录不存在，无需处理
        
        # 解析现有ID数组
        existing_ids = []
        raw_value = row[0]
        if raw_value:
            try:
                if isinstance(raw_value, str):
                    parsed = json.loads(raw_value)
                    if isinstance(parsed, list):
                        existing_ids = parsed
                    elif isinstance(parsed, int):
                        existing_ids = [parsed]
                elif isinstance(raw_value, list):
                    existing_ids = raw_value
                elif isinstance(raw_value, int):
                    existing_ids = [raw_value]
            except:
                try:
                    existing_ids = [int(raw_value)]
                except:
                    pass
        
        # 移除指定的文件ID
        for fid in file_ids:
            if fid in existing_ids:
                existing_ids.remove(fid)
        
        # 更新数据库
        new_value = json.dumps(existing_ids, ensure_ascii=False) if existing_ids else None
        update_sql = "UPDATE T_YBZJ SET F_ZQTYS_ID = ? WHERE F_ZZID = ?"
        cur.execute(update_sql, [new_value, zzid])
        
        return {"success": True}
    except Exception as e:
        return {"success": False, "message": f"从知情同意书移除文件失败: {str(e)}"}


def _remove_from_ybzj_pdf(cur, zzid: str, file_ids: List[int], adapter) -> Dict[str, Any]:
    """从 T_YBZJ.F_PDF 的 uploaded 数组中移除文件ID"""
    try:
        select_sql = "SELECT F_PDF FROM T_YBZJ WHERE F_ZZID = ?"
        cur.execute(select_sql, [zzid])
        row = cur.fetchone()
        
        if not row:
            return {"success": True}
        
        pdf_data = {"generated": None, "uploaded": []}
        raw_value = row[0]
        if raw_value:
            try:
                if isinstance(raw_value, str):
                    parsed = json.loads(raw_value)
                    if isinstance(parsed, dict):
                        pdf_data = parsed
                elif isinstance(raw_value, dict):
                    pdf_data = raw_value
            except:
                pass
        
        if "uploaded" in pdf_data and isinstance(pdf_data.get("uploaded"), list):
            for fid in file_ids:
                if fid in pdf_data["uploaded"]:
                    pdf_data["uploaded"].remove(fid)
        
        new_value = json.dumps(pdf_data, ensure_ascii=False)
        update_sql = "UPDATE T_YBZJ SET F_PDF = ? WHERE F_ZZID = ?"
        cur.execute(update_sql, [new_value, zzid])
        
        return {"success": True}
    except Exception as e:
        return {"success": False, "message": f"从接收质控PDF移除文件失败: {str(e)}"}


def _remove_from_ycl_pdf(cur, gdid: int, file_ids: List[int], adapter) -> Dict[str, Any]:
    """从 T_YCL.F_PDF 的 uploaded 数组中移除文件ID"""
    try:
        select_sql = "SELECT F_PDF FROM T_YCL WHERE F_GDID = ?"
        cur.execute(select_sql, [gdid])
        row = cur.fetchone()
        
        if not row:
            return {"success": True}
        
        pdf_data = {"generated": None, "uploaded": []}
        raw_value = row[0]
        if raw_value:
            try:
                if isinstance(raw_value, str):
                    parsed = json.loads(raw_value)
                    if isinstance(parsed, dict):
                        pdf_data = parsed
                elif isinstance(raw_value, dict):
                    pdf_data = raw_value
            except:
                pass
        
        if "uploaded" in pdf_data and isinstance(pdf_data.get("uploaded"), list):
            for fid in file_ids:
                if fid in pdf_data["uploaded"]:
                    pdf_data["uploaded"].remove(fid)
        
        new_value = json.dumps(pdf_data, ensure_ascii=False)
        update_sql = "UPDATE T_YCL SET F_PDF = ? WHERE F_GDID = ?"
        cur.execute(update_sql, [new_value, gdid])
        
        return {"success": True}
    except Exception as e:
        return {"success": False, "message": f"从样本质量评分表PDF移除文件失败: {str(e)}"}


def _remove_from_zkjl_pdf(cur, gdid: int, file_ids: List[int], adapter) -> Dict[str, Any]:
    """从 T_ZKJL.F_PDF 的 uploaded 数组中移除文件ID"""
    try:
        select_sql = "SELECT F_PDF FROM T_ZKJL WHERE F_GDID = ?"
        cur.execute(select_sql, [gdid])
        row = cur.fetchone()
        
        if not row:
            return {"success": True}
        
        pdf_data = {"generated": None, "uploaded": []}
        raw_value = row[0]
        if raw_value:
            try:
                if isinstance(raw_value, str):
                    parsed = json.loads(raw_value)
                    if isinstance(parsed, dict):
                        pdf_data = parsed
                elif isinstance(raw_value, dict):
                    pdf_data = raw_value
            except:
                pass
        
        if "uploaded" in pdf_data and isinstance(pdf_data.get("uploaded"), list):
            for fid in file_ids:
                if fid in pdf_data["uploaded"]:
                    pdf_data["uploaded"].remove(fid)
        
        new_value = json.dumps(pdf_data, ensure_ascii=False)
        update_sql = "UPDATE T_ZKJL SET F_PDF = ? WHERE F_GDID = ?"
        cur.execute(update_sql, [new_value, gdid])
        
        return {"success": True}
    except Exception as e:
        return {"success": False, "message": f"从类器官质控PDF移除文件失败: {str(e)}"}


def _remove_from_zkjl_pdf_fxjc(cur, gdid: int, file_ids: List[int], adapter) -> Dict[str, Any]:
    """从 T_ZKJL.F_PDF_FXJC 的 uploaded 数组中移除文件ID"""
    try:
        select_sql = "SELECT F_PDF_FXJC FROM T_ZKJL WHERE F_GDID = ?"
        cur.execute(select_sql, [gdid])
        row = cur.fetchone()
        
        if not row:
            return {"success": True}
        
        pdf_data = {"generated": None, "uploaded": []}
        raw_value = row[0]
        if raw_value:
            try:
                if isinstance(raw_value, str):
                    parsed = json.loads(raw_value)
                    if isinstance(parsed, dict):
                        pdf_data = parsed
                elif isinstance(raw_value, dict):
                    pdf_data = raw_value
            except:
                pass
        
        if "uploaded" in pdf_data and isinstance(pdf_data.get("uploaded"), list):
            for fid in file_ids:
                if fid in pdf_data["uploaded"]:
                    pdf_data["uploaded"].remove(fid)
        
        new_value = json.dumps(pdf_data, ensure_ascii=False)
        update_sql = "UPDATE T_ZKJL SET F_PDF_FXJC = ? WHERE F_GDID = ?"
        cur.execute(update_sql, [new_value, gdid])
        
        return {"success": True}
    except Exception as e:
        return {"success": False, "message": f"从类器官放行报告PDF移除文件失败: {str(e)}"}

