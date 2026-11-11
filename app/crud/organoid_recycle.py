from __future__ import annotations

from typing import Any, Dict, List, Optional
from datetime import datetime
import json

from app.db.factory import get_adapter


def _clean_num(v: Any) -> Any:
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return v
    s = str(v).strip()
    if s == "":
        return None
    try:
        if "." in s:
            return float(s)
        return int(s)
    except Exception:
        return None


def _clean_str(v: Any) -> Optional[str]:
    if v is None:
        return None
    s = str(v).strip()
    return s if s != "" else None


def _json_dumps(obj: Any) -> Optional[str]:
    try:
        return json.dumps(obj, ensure_ascii=False)
    except Exception:
        return None


def save_recycle_record(hsxx: Dict[str, Any], file_ids_override: List[int] | None, add_file_ids: List[int] | None, del_file_ids: List[int] | None, operator: Optional[str] = None) -> Dict[str, Any]:
    """按 F_GDID 一对一保存回收记录：存在则更新，不存在则新增。

    F_FJ 字段存 JSON 数组（文件ID列表）。
    """
    adapter = get_adapter()
    
    # 初始化变量（用于质控记录）
    old_data = None  # 用于变更对比的旧记录数据
    updates = None  # 用于变更对比的更新数据
    old_detail_data_before_update = None  # 修改前的完整详情数据（用于F_OLD_DATA）
    
    with adapter.connect() as conn:
        cur = conn.cursor()

        gdid = int(hsxx.get("F_GDID"))

        # 查是否存在
        sql = "SELECT * FROM T_HSJL WHERE F_GDID = ? ORDER BY F_ID DESC"
        paginated_sql, extra = adapter.paginate(sql, 0, 1)
        cur.execute(paginated_sql, (gdid,) + tuple(extra))
        row = cur.fetchone()
        
        is_new_record = not row  # 标志：是否为新增记录

        if not row:
            # 新建
            rec: Dict[str, Any] = {
                "F_GDID": gdid,
                "F_HSSJ": _clean_str(hsxx.get("F_HSSJ")),
                "F_JSFF": _clean_str(hsxx.get("F_JSFF")),
                "F_HSSL": _clean_num(hsxx.get("F_HSSL")),
                "F_XBZT": _clean_str(hsxx.get("F_XBZT")),
            }
            fj_ids: List[int] = []
            if file_ids_override is not None:
                # 如果有覆盖参数，使用覆盖参数
                for v in (file_ids_override or []):
                    try:
                        fj_ids.append(int(v))
                    except Exception:
                        pass
            else:
                # 如果没有覆盖参数，使用 add_file_ids
                if add_file_ids:
                    for v in add_file_ids:
                        try:
                            fj_ids.append(int(v))
                        except Exception:
                            pass
            rec["F_FJ"] = _json_dumps(sorted(list({int(x) for x in fj_ids})))

            cols = [k for k, v in rec.items() if v is not None]
            placeholders = ", ".join(["?" for _ in cols])
            cur.execute(
                f"INSERT INTO T_HSJL ({', '.join(cols)}) VALUES ({placeholders})",
                tuple(rec[c] for c in cols),
            )
            cur.execute("SELECT MAX(F_ID) FROM T_HSJL WHERE F_GDID = ?", (gdid,))
            record_id = int(cur.fetchone()[0])
            conn.commit()

        else:
            # 已存在 → 更新
            cols = [c[0] for c in cur.description]
            current = dict(zip(cols, row))
            record_id = int(current.get("F_ID"))
            
            # 保存旧数据用于变更对比
            old_data = current.copy()
            
            # ✅ 在更新前获取完整的旧详情数据（用于F_OLD_DATA）
            try:
                from app.crud.organoid_observation import get_observations_by_gdid
                import logging
                logger = logging.getLogger(__name__)
                old_detail_data_before_update = get_observations_by_gdid(gdid)
                logger.info(f"[回收记录质控] 成功获取修改前完整详情数据，工单ID: {gdid}")
            except Exception as e:
                if 'logger' not in locals():
                    import logging
                    logger = logging.getLogger(__name__)
                logger.warning(f"[回收记录质控] 获取修改前完整详情数据失败: {e}")

            updates: Dict[str, Any] = {}
            if hsxx.get("F_HSSJ") is not None:
                updates["F_HSSJ"] = _clean_str(hsxx.get("F_HSSJ"))
            if hsxx.get("F_JSFF") is not None:
                updates["F_JSFF"] = _clean_str(hsxx.get("F_JSFF"))
            if hsxx.get("F_HSSL") is not None:
                updates["F_HSSL"] = _clean_num(hsxx.get("F_HSSL"))
            if hsxx.get("F_XBZT") is not None:
                updates["F_XBZT"] = _clean_str(hsxx.get("F_XBZT"))

            # 附件：覆盖优先，否则增删
            try:
                exist_fj = json.loads(current.get("F_FJ") or "[]")
                exist_set = {int(x) for x in exist_fj}
            except Exception:
                exist_set = set()
            if file_ids_override is not None:
                exist_set = {int(x) for x in (file_ids_override or []) if x is not None}
            else:
                if add_file_ids:
                    for fid in add_file_ids:
                        try:
                            exist_set.add(int(fid))
                        except Exception:
                            pass
                if del_file_ids:
                    for fid in del_file_ids:
                        try:
                            exist_set.discard(int(fid))
                        except Exception:
                            pass
            updates["F_FJ"] = _json_dumps(sorted(list(exist_set)))

            set_cols = [k for k, v in updates.items() if v is not None]
            if set_cols:
                sets = ", ".join([f"{c} = ?" for c in set_cols])
                params = [updates[c] for c in set_cols] + [record_id]
                cur.execute(f"UPDATE T_HSJL SET {sets} WHERE F_ID = ?", tuple(params))

            conn.commit()
    
    # ========== 查询工单信息（新增/更新都需要） ==========
    zzid = None
    order_info = {}
    try:
        with adapter.connect() as conn_order:
            cur_order = conn_order.cursor()
            cur_order.execute("SELECT F_ZZID, F_GDBH, F_DCID, F_DC, F_PYSJ, F_SQR, F_XM, F_BRBH, F_YBLY FROM T_PYGD WHERE F_ID = ?", (gdid,))
            row_order = cur_order.fetchone()
            if row_order:
                zzid = row_order[0]
                order_info = {
                    "F_GDBH": row_order[1],
                    "F_DCID": row_order[2],
                    "F_DC": row_order[3],
                    "F_PYSJ": row_order[4],
                    "F_SQR": row_order[5],
                    "F_XM": row_order[6],
                    "F_BRBH": row_order[7],
                    "F_YBLY": row_order[8]
                }
    except Exception as e:
        import logging
        logger = logging.getLogger(__name__)
        logger.error(f"查询工单信息失败: {e}", exc_info=True)
    
    # ========== 创建质控审核记录（新增/更新都需要） ==========
    qc_audit_id = None
    import logging
    logger = logging.getLogger(__name__)

    if zzid and operator:
        try:
            from app.crud.qc_audit import create_qc_audit_record, get_sample_basic_info, calculate_changes
            from app.crud.organoid_observation import get_observations_by_gdid
            logger.info(f"[回收记录质控] 开始创建质控记录，is_new_record: {is_new_record}")
            
            # 获取样本基本信息
            sample_info = get_sample_basic_info(zzid)
            
            # 判断是新增还是更新
            if is_new_record:
                # 新增分支
                bglx = "新增"
                changes = {
                    "操作": "新增回收记录",
                    "关键信息": {
                        "回收记录ID": record_id,
                        "工单ID": gdid,
                        "标本号": zzid,
                        "回收时间": hsxx.get("F_HSSJ"),
                        "接收方法": hsxx.get("F_JSFF"),
                        "回收数量": hsxx.get("F_HSSL"),
                        "细胞状态": hsxx.get("F_XBZT")
                    }
                }
                
                # ✅ 新增操作：获取新增后的完整详情数据（用于F_OLD_DATA字段）
                new_detail_data = None
                try:
                    new_detail_data = get_observations_by_gdid(gdid)
                    logger.info(f"[回收记录质控] 成功获取新增后完整详情数据，工单ID: {gdid}")
                except Exception as e:
                    logger.warning(f"[回收记录质控] 获取新增后完整详情数据失败: {e}")
                
                # 新增操作使用新数据
                data_for_old_data = new_detail_data
                
            else:
                # 更新分支
                bglx = "修改"
                
                # ✅ 修改操作：使用在commit前获取的完整详情数据
                # old_detail_data_before_update 在外层已经初始化，在修改分支中赋值
                
                changes = calculate_changes(old_data, updates)
                if not changes.get("变更字段"):
                    # 没有实际变更，不创建质控记录
                    logger.info(f"[回收记录质控] 回收记录未变更，跳过质控记录创建 (工单ID: {gdid})")
                    qc_audit_id = None
                    changes = None
                else:
                    # 与新增侧对齐：显式标注操作类型，供质控审核前端区分弹窗
                    changes["操作"] = "修改回收记录"
                    changes["关键信息"] = {
                        "回收记录ID": record_id,
                        "工单ID": gdid,
                        "标本号": zzid,
                    }

                # 修改操作使用修改前的旧数据
                data_for_old_data = old_detail_data_before_update
            
            # 只有有变更时才创建记录
            if changes:
                # ✅ 创建质控审核记录，包含F_OLD_DATA
                qc_audit_id = create_qc_audit_record(
                    F_ZKJD="观察",
                    F_BGLX=bglx,
                    F_ZZID=zzid,
                    F_CHANGES=changes,
                    F_JLR=operator or "系统",
                    F_GDID=gdid,
                    F_XM=sample_info.get("F_XM") or order_info.get("F_XM"),
                    F_BRBH=sample_info.get("F_BRBH") or order_info.get("F_BRBH"),
                    F_ZZBW=sample_info.get("F_ZZBW"),
                    F_YBLY=order_info.get("F_YBLY"),  # 从工单信息中获取样本来源
                    # 工单相关字段
                    F_GDBH=order_info.get("F_GDBH"),
                    F_DCID=order_info.get("F_DCID"),
                    F_DC=order_info.get("F_DC"),
                    F_PYSJ=order_info.get("F_PYSJ"),
                    F_SQR=order_info.get("F_SQR"),
                    F_OLD_DATA=data_for_old_data  # ✅ 新增时传入新数据，修改时传入旧数据
                )
                logger.info(f"[回收记录质控] 创建质控审核记录成功，工单ID: {gdid}, 类型: {bglx}, 质控ID: {qc_audit_id}")
        except Exception as e:
            logger.error(f"[回收记录质控] 创建质控审核记录失败 (工单ID: {gdid}): {e}", exc_info=True)
    else:
        logger.warning(f"[回收记录质控] 跳过创建质控记录 - zzid: {zzid}, operator: '{operator}'")
    
    # ========== 返回结果（新增/更新不同） ==========
    if is_new_record:
        # 新增分支
        result = {
            "F_ID": record_id,
            "F_GDID": gdid,
            "F_FJ_IDS": json.loads(rec.get("F_FJ") or "[]")
        }
    else:
        # 更新分支
        result = {
            "F_ID": record_id,
            "F_GDID": gdid,
            "F_FJ_IDS": sorted(list(exist_set))
        }
    
    if qc_audit_id:
        result["qc_audit_id"] = qc_audit_id
    
    return result


def get_recycle_by_gdid(gdid: int) -> Dict[str, Any] | None:
    adapter = get_adapter()
    with adapter.connect() as conn:
        cur = conn.cursor()
        sql = "SELECT * FROM T_HSJL WHERE F_GDID = ? ORDER BY F_ID DESC"
        paginated_sql, extra = adapter.paginate(sql, 0, 1)
        cur.execute(paginated_sql, (int(gdid),) + tuple(extra))
        row = cur.fetchone()
        if not row:
            return None
        cols = [c[0] for c in cur.description]
        it = dict(zip(cols, row))
        
        # F_FJ：将ID数组JSON转换为详情数组（带静态地址）
        try:
            fj_raw = it.get("F_FJ") or "[]"
            arr = json.loads(fj_raw) if isinstance(fj_raw, str) else fj_raw
            if isinstance(arr, dict):
                arr = [arr]
        except Exception:
            arr = []
        
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
                            row_file = id_to_row.get(aid)
                            if row_file:
                                # 获取F_PATH并清理为相对路径
                                path = row_file.get("F_PATH", "")
                                
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
                                    "name": row_file.get("F_ORIGINAL_NAME"),
                                })
                        except Exception:
                            pass
            except Exception:
                details = []
        it["F_FJ"] = details
        return it


