from __future__ import annotations

from typing import Any, Dict, List, Optional
from datetime import datetime
import json

from app.db.factory import get_adapter


def _now() -> datetime:
    return datetime.now()


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


def _clean_bool(v: Any) -> Optional[int]:
    if v is None:
        return None
    if isinstance(v, bool):
        return 1 if v else 0
    s = str(v).strip().lower()
    if s in ("1", "true", "yes", "y", "on"):  # 归一为 1/0
        return 1
    if s in ("0", "false", "no", "n", "off"):
        return 0
    try:
        return 1 if int(s) != 0 else 0
    except Exception:
        return None


def _json_dumps(obj: Any) -> Optional[str]:
    try:
        return json.dumps(obj, ensure_ascii=False)
    except Exception:
        return None


def create_observation(gcxx: Dict[str, Any], attachments: List[Dict[str, Any]] | None, operator: Optional[str]) -> Dict[str, Any]:
    """插入一条观察记录到 T_GCJL。

    attachments: 形如 [{"F_BS": 倍数, "F_WJ_ID": 文件ID}, ...]
    返回：{"F_ID": 新ID, "F_GDID": ..., "F_FJ": [ ... ]}
    """
    adapter = get_adapter()
    with adapter.connect() as conn:
        cur = conn.cursor()

        row: Dict[str, Any] = {}
        # 必填
        row["F_GDID"] = int(gcxx.get("F_GDID"))

        # 可选字段
        # 时间/日期字段按字符串入库，交由驱动处理；数值字段做清洗
        row["F_GCSJ"] = _clean_str(gcxx.get("F_GCSJ"))
        row["F_PYTS"] = _clean_num(gcxx.get("F_PYTS"))
        row["F_PYJZT"] = _clean_str(gcxx.get("F_PYJZT"))
        row["F_SFWR"] = _clean_bool(gcxx.get("F_SFWR"))
        row["F_SFJY"] = _clean_bool(gcxx.get("F_SFJY"))
        row["F_PJZJ"] = _clean_num(gcxx.get("F_PJZJ"))
        row["F_XBSL"] = _clean_num(gcxx.get("F_XBSL"))
        row["F_SFHX"] = _clean_str(gcxx.get("F_SFHX"))  # 改为字符串类型
        row["F_SFSK"] = _clean_str(gcxx.get("F_SFSK"))  # 改为字符串类型
        row["F_SFHY"] = _clean_bool(gcxx.get("F_SFHY"))
        row["F_HYJL"] = _clean_str(gcxx.get("F_HYJL"))
        row["F_SQXQL"] = _clean_num(gcxx.get("F_SQXQL"))
        row["F_SQBCL"] = _clean_num(gcxx.get("F_SQBCL"))
        row["F_BZ"] = _clean_str(gcxx.get("F_BZ"))
        # 记录日期：始终使用系统当前日期（YYYY-MM-DD）
        row["F_JLRQ"] = datetime.now().strftime("%Y-%m-%d")
        row["F_JLR"] = _clean_str(gcxx.get("F_JLR"))

        # 附件 JSON
        fj_items: List[Dict[str, Any]] = []
        for it in (attachments or []):
            try:
                fj_items.append({
                    "F_BS": _clean_num(it.get("F_BS")),
                    "F_WJ_ID": int(it.get("F_WJ_ID")),
                })
            except Exception:
                continue
        row["F_FJ"] = _json_dumps(fj_items)

        # 计算列与参数，只写非 None 的列
        cols = [k for k, v in row.items() if v is not None]
        placeholders = ", ".join(["?" for _ in cols])
        cur.execute(
            f"INSERT INTO T_GCJL ({', '.join(cols)}) VALUES ({placeholders})",
            tuple(row[c] for c in cols),
        )

        # 获取新ID
        try:
            cur.execute("SELECT MAX(F_ID) FROM T_GCJL WHERE F_GDID = ?", (row["F_GDID"],))
            new_id = int(cur.fetchone()[0])
        except Exception:
            # 退而求其次
            cur.execute("SELECT MAX(F_ID) FROM T_GCJL")
            new_id = int(cur.fetchone()[0])

        conn.commit()
    
    # ========== 查询工单信息（用于质控记录） ==========
    gdid = row["F_GDID"]
    zzid = None
    order_info = {}
    try:
        with adapter.connect() as conn_order:
            cur_order = conn_order.cursor()
            cur_order.execute("SELECT F_ZZID, F_GDBH, F_DCID, F_DC, F_PYSJ, F_SQR, F_XM, F_BRBH FROM T_PYGD WHERE F_ID = ?", (gdid,))
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
                    "F_BRBH": row_order[7]
                }
    except Exception as e:
        import logging
        logger = logging.getLogger(__name__)
        logger.error(f"查询工单信息失败: {e}", exc_info=True)
    
    # ========== 创建质控审核记录（新增观察记录） ==========
    qc_audit_id = None
    import logging
    logger = logging.getLogger(__name__)

    if zzid and operator:
        try:
            from app.crud.qc_audit import create_qc_audit_record, get_sample_basic_info
            logger.info(f"[观察记录质控] 开始创建质控记录（新增观察记录），工单ID: {gdid}")
            
            # 获取样本基本信息
            sample_info = get_sample_basic_info(zzid)
            
            # ✅ 获取完整的新详情数据（新增后的状态，包含新添加的观察记录）
            new_detail_data = None
            try:
                new_detail_data = get_observations_by_gdid(gdid)
                logger.info(f"[观察记录质控] 成功获取新详情数据用于备份，工单ID: {gdid}")
            except Exception as e:
                logger.warning(f"[观察记录质控] 获取新详情数据失败: {e}")
            
            # 新增观察记录的变更信息
            changes = {
                "操作": "新增观察记录",
                "关键信息": {
                    "观察记录ID": new_id,
                    "工单ID": gdid,
                    "标本号": zzid,
                    "观察时间": gcxx.get("F_GCSJ"),
                    "培养天数": gcxx.get("F_PYTS"),
                    "平均直径": gcxx.get("F_PJZJ"),
                    "细胞数量": gcxx.get("F_XBSL"),
                    "是否污染": "是" if row.get("F_SFWR") == 1 else "否" if row.get("F_SFWR") == 0 else None,
                    "是否换液": "是" if row.get("F_SFHY") == 1 else "否" if row.get("F_SFHY") == 0 else None,
                }
            }
            
            # ✅ 创建质控审核记录，F_OLD_DATA记录新增后的完整数据
            qc_audit_id = create_qc_audit_record(
                F_ZKJD="观察",
                F_BGLX="新增",
                F_ZZID=zzid,
                F_CHANGES=changes,
                F_JLR=operator or "系统",
                F_GDID=gdid,
                F_XM=sample_info.get("F_XM"),
                F_BRBH=sample_info.get("F_BRBH"),
                F_GDBH=order_info.get("F_GDBH"),
                F_DCID=order_info.get("F_DCID"),
                F_DC=order_info.get("F_DC"),
                F_PYSJ=order_info.get("F_PYSJ"),
                F_SQR=order_info.get("F_SQR"),
                F_OLD_DATA=new_detail_data  # ✅ 新增操作记录新增后的完整数据
            )
            logger.info(f"[观察记录质控] 质控记录创建成功，记录ID: {qc_audit_id}")
        except Exception as e:
            logger.error(f"[观察记录质控] 创建质控审核记录失败（工单ID: {gdid}）: {e}", exc_info=True)
    
    result = {
        "F_ID": new_id,
        "F_GDID": row["F_GDID"],
        "F_FJ": fj_items,
    }
    
    if qc_audit_id:
        result["qc_audit_id"] = qc_audit_id
    
    return result


def delete_observation(fid: int, operator: Optional[str] = None) -> bool:
    adapter = get_adapter()
    
    # 先查询要删除的记录信息
    gdid = None
    zzid = None
    old_record = None
    
    with adapter.connect() as conn:
        cur = conn.cursor()
        try:
            # 获取记录详情（删除前）
            cur.execute("SELECT * FROM T_GCJL WHERE F_ID = ?", (int(fid),))
            row = cur.fetchone()
            if row:
                cols = [c[0] for c in cur.description]
                old_record = dict(zip(cols, row))
                gdid = old_record.get("F_GDID")
                
                # 获取工单的ZZID
                if gdid:
                    cur.execute("SELECT F_ZZID FROM T_PYGD WHERE F_ID = ?", (gdid,))
                    zzid_row = cur.fetchone()
                    if zzid_row:
                        zzid = zzid_row[0]
            
            # 删除记录
            cur.execute("DELETE FROM T_GCJL WHERE F_ID = ?", (int(fid),))
            affected = cur.rowcount
            conn.commit()
            
            if affected == 0:
                return False
                
        except Exception:
            return False
    
    # ========== 创建质控审核记录（删除观察记录） ==========
    if gdid and zzid and operator and old_record:
        try:
            import logging
            logger = logging.getLogger(__name__)
            
            from app.crud.qc_audit import create_qc_audit_record, get_sample_basic_info
            logger.info(f"[观察记录质控] 开始创建质控记录（删除观察记录），工单ID: {gdid}, 记录ID: {fid}")
            
            # 获取样本基本信息和工单信息
            sample_info = get_sample_basic_info(zzid)
            
            with adapter.connect() as conn_order:
                cur_order = conn_order.cursor()
                cur_order.execute("SELECT F_GDBH, F_DCID, F_DC, F_PYSJ, F_SQR FROM T_PYGD WHERE F_ID = ?", (gdid,))
                row_order = cur_order.fetchone()
                order_info = {}
                if row_order:
                    order_info = {
                        "F_GDBH": row_order[0],
                        "F_DCID": row_order[1],
                        "F_DC": row_order[2],
                        "F_PYSJ": row_order[3],
                        "F_SQR": row_order[4],
                    }
            
            # ✅ 获取删除后的完整详情数据
            old_detail_data = None
            try:
                old_detail_data = get_observations_by_gdid(gdid)
                logger.info(f"[观察记录质控] 成功获取删除后详情数据用于备份，工单ID: {gdid}")
            except Exception as e:
                logger.warning(f"[观察记录质控] 获取删除后详情数据失败: {e}")
            
            # 删除观察记录的变更信息
            changes = {
                "操作": "删除观察记录",
                "关键信息": {
                    "观察记录ID": fid,
                    "工单ID": gdid,
                    "标本号": zzid,
                    "观察时间": old_record.get("F_GCSJ"),
                    "培养天数": old_record.get("F_PYTS"),
                    "平均直径": old_record.get("F_PJZJ"),
                    "细胞数量": old_record.get("F_XBSL"),
                }
            }
            
            # 创建质控审核记录
            qc_audit_id = create_qc_audit_record(
                F_ZKJD="观察",
                F_BGLX="删除",
                F_ZZID=zzid,
                F_CHANGES=changes,
                F_JLR=operator or "系统",
                F_GDID=gdid,
                F_XM=sample_info.get("F_XM"),
                F_BRBH=sample_info.get("F_BRBH"),
                F_GDBH=order_info.get("F_GDBH"),
                F_DCID=order_info.get("F_DCID"),
                F_DC=order_info.get("F_DC"),
                F_PYSJ=order_info.get("F_PYSJ"),
                F_SQR=order_info.get("F_SQR"),
                F_OLD_DATA=old_detail_data
            )
            logger.info(f"[观察记录质控] 质控记录创建成功（删除），记录ID: {qc_audit_id}")
        except Exception as e:
            logger.error(f"[观察记录质控] 创建质控审核记录失败（删除，工单ID: {gdid}）: {e}", exc_info=True)
    
    return True


def update_observation(
    fid: int,
    gcxx: Dict[str, Any],
    attachments: List[Dict[str, Any]] | None,
    operator: Optional[str]
) -> Dict[str, Any]:
    """
    更新观察记录
    
    Args:
        fid: 观察记录ID
        gcxx: 观察记录主体字段（不含附件）
        attachments: 新的附件列表 [{"F_BS": 倍数, "F_WJ_ID": 文件ID}, ...]
        operator: 操作人
    
    Returns:
        {"F_ID": 记录ID, "F_GDID": 工单ID, "F_FJ": 附件列表, "qc_audit_id": 质控记录ID(可选)}
    """
    adapter = get_adapter()
    
    # 先查询原记录（用于质控审核）
    old_record = None
    gdid = None
    zzid = None
    
    with adapter.connect() as conn:
        cur = conn.cursor()
        
        # 获取原记录
        cur.execute("SELECT * FROM T_GCJL WHERE F_ID = ?", (int(fid),))
        row = cur.fetchone()
        if not row:
            raise ValueError("观察记录不存在")
        
        cols = [c[0] for c in cur.description]
        old_record = dict(zip(cols, row))
        gdid = old_record.get("F_GDID")
        
        # 获取工单的ZZID
        if gdid:
            cur.execute("SELECT F_ZZID FROM T_PYGD WHERE F_ID = ?", (gdid,))
            zzid_row = cur.fetchone()
            if zzid_row:
                zzid = zzid_row[0]
        
        # 构建更新数据
        row_update: Dict[str, Any] = {}
        
        # 可选字段（只更新传入的字段）
        if "F_GCSJ" in gcxx:
            row_update["F_GCSJ"] = _clean_str(gcxx.get("F_GCSJ"))
        if "F_PYTS" in gcxx:
            row_update["F_PYTS"] = _clean_num(gcxx.get("F_PYTS"))
        if "F_PYJZT" in gcxx:
            row_update["F_PYJZT"] = _clean_str(gcxx.get("F_PYJZT"))
        if "F_SFWR" in gcxx:
            row_update["F_SFWR"] = _clean_bool(gcxx.get("F_SFWR"))
        if "F_SFJY" in gcxx:
            row_update["F_SFJY"] = _clean_bool(gcxx.get("F_SFJY"))
        if "F_PJZJ" in gcxx:
            row_update["F_PJZJ"] = _clean_num(gcxx.get("F_PJZJ"))
        if "F_XBSL" in gcxx:
            row_update["F_XBSL"] = _clean_num(gcxx.get("F_XBSL"))
        if "F_SFHX" in gcxx:
            row_update["F_SFHX"] = _clean_str(gcxx.get("F_SFHX"))
        if "F_SFSK" in gcxx:
            row_update["F_SFSK"] = _clean_str(gcxx.get("F_SFSK"))
        if "F_SFHY" in gcxx:
            row_update["F_SFHY"] = _clean_bool(gcxx.get("F_SFHY"))
        if "F_HYJL" in gcxx:
            row_update["F_HYJL"] = _clean_str(gcxx.get("F_HYJL"))
        if "F_SQXQL" in gcxx:
            row_update["F_SQXQL"] = _clean_num(gcxx.get("F_SQXQL"))
        if "F_SQBCL" in gcxx:
            row_update["F_SQBCL"] = _clean_num(gcxx.get("F_SQBCL"))
        if "F_BZ" in gcxx:
            row_update["F_BZ"] = _clean_str(gcxx.get("F_BZ"))
        if "F_JLR" in gcxx:
            row_update["F_JLR"] = _clean_str(gcxx.get("F_JLR"))
        
        # 更新记录日期为当前日期
        row_update["F_JLRQ"] = datetime.now().strftime("%Y-%m-%d")
        
        # 附件 JSON（如果提供了 attachments 参数，则更新）
        if attachments is not None:
            fj_items: List[Dict[str, Any]] = []
            for it in attachments:
                try:
                    fj_items.append({
                        "F_BS": _clean_num(it.get("F_BS")),
                        "F_WJ_ID": int(it.get("F_WJ_ID")),
                    })
                except Exception:
                    pass
            row_update["F_FJ"] = _json_dumps(fj_items)
        
        # 执行更新
        if row_update:
            set_clause = ", ".join([f"{k} = ?" for k in row_update.keys()])
            params = list(row_update.values()) + [int(fid)]
            cur.execute(f"UPDATE T_GCJL SET {set_clause} WHERE F_ID = ?", tuple(params))
        
        conn.commit()
        
        # 获取更新后的记录
        cur.execute("SELECT * FROM T_GCJL WHERE F_ID = ?", (int(fid),))
        row = cur.fetchone()
        cols = [c[0] for c in cur.description]
        new_record = dict(zip(cols, row))
    
    # ========== 创建质控审核记录（编辑观察记录） ==========
    qc_audit_id = None
    import logging
    logger = logging.getLogger(__name__)
    
    if gdid and zzid and operator:
        try:
            from app.crud.qc_audit import create_qc_audit_record, get_sample_basic_info, calculate_changes
            logger.info(f"[观察记录质控] 开始创建质控记录（编辑观察记录），工单ID: {gdid}, 记录ID: {fid}")
            
            # 获取样本基本信息和工单信息
            sample_info = get_sample_basic_info(zzid)
            
            with adapter.connect() as conn_order:
                cur_order = conn_order.cursor()
                cur_order.execute("SELECT F_GDBH, F_DCID, F_DC, F_PYSJ, F_SQR FROM T_PYGD WHERE F_ID = ?", (gdid,))
                row_order = cur_order.fetchone()
                order_info = {}
                if row_order:
                    order_info = {
                        "F_GDBH": row_order[0],
                        "F_DCID": row_order[1],
                        "F_DC": row_order[2],
                        "F_PYSJ": row_order[3],
                        "F_SQR": row_order[4],
                    }
            
            # 获取编辑后的完整详情数据
            new_detail_data = None
            try:
                new_detail_data = get_observations_by_gdid(gdid)
                logger.info(f"[观察记录质控] 成功获取编辑后详情数据用于备份，工单ID: {gdid}")
            except Exception as e:
                logger.warning(f"[观察记录质控] 获取编辑后详情数据失败: {e}")
            
            # 计算变更信息（对比修改前后）
            field_mapping = {
                "F_GCSJ": "观察时间",
                "F_PYTS": "培养天数",
                "F_PYJZT": "培养基状态",
                "F_SFWR": "是否污染",
                "F_SFJY": "是否加药",
                "F_PJZJ": "平均直径",
                "F_XBSL": "细胞数量",
                "F_SFHX": "是否换液",
                "F_SFSK": "是否收块",
                "F_SFHY": "是否换液(旧)",
                "F_HYJL": "换液记录",
                "F_SQXQL": "上清消耗量",
                "F_SQBCL": "上清补充量",
                "F_BZ": "备注",
                "F_JLR": "记录人",
                "F_FJ": "附件",
            }
            
            changes = calculate_changes(old_record, new_record, field_mapping)
            
            # 添加关键信息
            changes["关键信息"] = {
                "观察记录ID": fid,
                "工单ID": gdid,
                "标本号": zzid,
            }
            
            # 创建质控审核记录
            qc_audit_id = create_qc_audit_record(
                F_ZKJD="观察",
                F_BGLX="编辑",
                F_ZZID=zzid,
                F_CHANGES=changes,
                F_JLR=operator or "系统",
                F_GDID=gdid,
                F_XM=sample_info.get("F_XM"),
                F_BRBH=sample_info.get("F_BRBH"),
                F_GDBH=order_info.get("F_GDBH"),
                F_DCID=order_info.get("F_DCID"),
                F_DC=order_info.get("F_DC"),
                F_PYSJ=order_info.get("F_PYSJ"),
                F_SQR=order_info.get("F_SQR"),
                F_OLD_DATA=new_detail_data  # 编辑操作记录编辑后的完整数据
            )
            logger.info(f"[观察记录质控] 质控记录创建成功（编辑），记录ID: {qc_audit_id}")
        except Exception as e:
            logger.error(f"[观察记录质控] 创建质控审核记录失败（编辑，工单ID: {gdid}）: {e}", exc_info=True)
    
    # 返回结果
    fj_items_result = []
    try:
        fj_data = json.loads(new_record.get("F_FJ") or "[]")
        if isinstance(fj_data, list):
            fj_items_result = fj_data
        elif isinstance(fj_data, dict):
            fj_items_result = [fj_data]
    except Exception:
        pass
    
    result = {
        "F_ID": fid,
        "F_GDID": gdid,
        "F_FJ": fj_items_result,
    }
    
    if qc_audit_id:
        result["qc_audit_id"] = qc_audit_id
    
    return result


def list_observations_by_gdid(gdid: int) -> List[Dict[str, Any]]:
    adapter = get_adapter()
    with adapter.connect() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT * FROM T_GCJL WHERE F_GDID = ? ORDER BY COALESCE(F_PYTS,0) ASC, COALESCE(F_GCSJ,'' ) ASC, F_ID ASC",
            (int(gdid),),
        )
        rows = cur.fetchall()
        cols = [c[0] for c in cur.description]
        data = [dict(zip(cols, r)) for r in rows]
        # 解析 F_FJ
        for it in data:
            try:
                fj = json.loads(it.get("F_FJ") or "[]")
                if isinstance(fj, dict):
                    fj = [fj]
            except Exception:
                fj = []
            it["F_FJ"] = fj
        return data


def get_observations_by_gdid(gdid: int) -> Dict[str, Any]:
    """
    获取完整的观察页面详情（包含工单、培养箱参数、观察记录列表、回收记录），
    用于质控审核记录的F_OLD_DATA字段
    模仿 /organoid-observation/detail-by-gdid 接口的返回结构
    """
    from app.crud import organoid_culture_tissue as crud_order
    from app.crud import organoid_culture_detail as crud_detail
    from app.crud import organoid_recycle as crud_recycle
    
    # 工单详情
    order = crud_order.get_culture_order_detail(gdid) if hasattr(crud_order, "get_culture_order_detail") else None
    
    # 培养箱参数：取最新一条培养明细
    mx = crud_detail.get_mx_by_gdid(gdid)
    incubator = None
    if mx:
        py = mx.get("PYXX_PY") or {}
        incubator = {
            "F_TJPYJ": py.get("F_TJPYJ"),
            "F_PYX_TEMP": py.get("F_PYX_TEMP"),
            "F_PYX_CO2": py.get("F_PYX_CO2"),
            "F_PYX_HUM": py.get("F_PYX_HUM"),
            "F_PYX_FRSJ": py.get("F_PYX_FRSJ"),
        }
    
    # 观察记录列表
    observations = list_observations_by_gdid(gdid)
    
    # 回收记录
    recycle = crud_recycle.get_recycle_by_gdid(gdid)
    
    return {
        "order": order or {},
        "incubator": incubator or {},
        "observations": observations or [],
        "recycle": recycle or {},
    }


