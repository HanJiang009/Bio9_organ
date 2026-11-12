"""
药物试验——实验记录（结果录入）CRUD
单表 + JSON 字段存储，使用新设计的表结构
"""
import sys
import json
import logging
import numpy as np
import matplotlib
matplotlib.use('Agg')  # 使用非GUI后端
import matplotlib.pyplot as plt
from datetime import datetime
from typing import Any, Dict, Optional, List
from pathlib import Path
from app.db.factory import get_adapter
from app.utils.ic50_analyzer import IC50Analyzer

logger = logging.getLogger(__name__)


_JSON_FIELDS = [
    "F_KBDS_ZBQ",
    "F_KBDS_ZBH",
    "F_XTXBH",
    "F_XBHL_PJ",
    "F_XBHL",
    "F_XBHL_WC",
    "F_GMD",
    "F_IC50",
    "F_JGSJ",
    "F_JLFJ",
]


def _dumps_json(val: Any) -> Optional[str]:
    """序列化为JSON字符串"""
    if val is None:
        return None
    try:
        return json.dumps(val, ensure_ascii=False)
    except Exception:
        return None


def _loads_json(val: Any):
    """反序列化JSON字符串"""
    if val is None:
        return None
    if isinstance(val, (dict, list)):
        return val
    try:
        return json.loads(val)
    except Exception:
        return val


def _replace_attachment_indices(obj: Any, file_id_map: List[int]) -> Any:
    """
    递归替换 F_FJ_INDEX 为实际的文件ID，并支持与 F_FJ 合并
    
    Args:
        obj: 待处理的对象（dict/list/其他）
        file_id_map: 文件ID映射数组 [101, 102, 103...]，索引对应上传顺序
    
    Returns:
        替换后的对象
    
    说明：
        - 支持方案1：混合使用 F_FJ（保留的附件ID）和 F_FJ_INDEX（新上传的索引）
        - 最终的 F_FJ = 原有的 F_FJ + 根据 F_FJ_INDEX 转换的新ID
        - 处理完成后会删除 F_FJ_INDEX 字段
    
    示例：
        输入: {"F_FJ": [101, 102], "F_FJ_INDEX": [0, 1]}
        file_id_map: [201, 202]
        输出: {"F_FJ": [101, 102, 201, 202]}
    """
    import logging
    logger = logging.getLogger(__name__)
    
    if isinstance(obj, dict):
        new_dict = {}
        has_fj_index = "F_FJ_INDEX" in obj
        has_fj = "F_FJ" in obj
        
        for key, value in obj.items():
            if key == "F_FJ_INDEX":
                # ✅ 跳过 F_FJ_INDEX 字段，不直接写入新字典
                # 会在后面处理合并逻辑
                continue
            elif key == "F_FJ" and has_fj_index:
                # ✅ 如果同时存在 F_FJ 和 F_FJ_INDEX，需要合并
                logger.debug(f"[合并] 发现 F_FJ={value} 和 F_FJ_INDEX={obj['F_FJ_INDEX']}")
                
                # 1. 保留原有的附件ID（F_FJ）
                kept_ids = []
                if isinstance(value, list):
                    kept_ids = [int(fid) for fid in value if fid]
                elif isinstance(value, int) and value > 0:
                    kept_ids = [value]
                
                # 2. 转换新上传的附件索引（F_FJ_INDEX）
                new_ids = []
                fj_index_value = obj["F_FJ_INDEX"]
                if isinstance(fj_index_value, list):
                    new_ids = [
                        file_id_map[idx] for idx in fj_index_value
                        if isinstance(idx, int) and 0 <= idx < len(file_id_map)
                    ]
                elif isinstance(fj_index_value, int) and 0 <= fj_index_value < len(file_id_map):
                    new_ids = [file_id_map[fj_index_value]]
                
                # 3. 合并：保留的 + 新上传的
                final_ids = kept_ids + new_ids
                new_dict["F_FJ"] = final_ids
                
                logger.debug(f"[合并] 保留ID={kept_ids}, 新ID={new_ids}, 最终={final_ids}")
            else:
                # 递归处理其他字段
                new_dict[key] = _replace_attachment_indices(value, file_id_map)
        
        # 如果只有 F_FJ_INDEX 没有 F_FJ（旧逻辑兼容）
        if has_fj_index and not has_fj:
            fj_index_value = obj["F_FJ_INDEX"]
            logger.debug(f"[替换] 只有 F_FJ_INDEX={fj_index_value}, file_id_map长度={len(file_id_map)}")
            
            if isinstance(fj_index_value, list):
                new_dict["F_FJ"] = [
                    file_id_map[idx] for idx in fj_index_value
                    if isinstance(idx, int) and 0 <= idx < len(file_id_map)
                ]
                logger.debug(f"[替换] 数组索引 {fj_index_value} -> 文件ID {new_dict['F_FJ']}")
            elif isinstance(fj_index_value, int) and 0 <= fj_index_value < len(file_id_map):
                new_dict["F_FJ"] = file_id_map[fj_index_value]
                logger.debug(f"[替换] 单个索引 {fj_index_value} -> 文件ID {new_dict['F_FJ']}")
            else:
                new_dict["F_FJ"] = [] if isinstance(fj_index_value, list) else None
                logger.warning(f"[替换] 无效索引 {fj_index_value}，设置 F_FJ 为空")
        
        return new_dict
    elif isinstance(obj, list):
        return [_replace_attachment_indices(item, file_id_map) for item in obj]
    else:
        return obj


def save_experiment_record(
    data: Dict[str, Any],
    file_ids: List[int],
    operator: Optional[str] = None,
    skip_qc: bool = False,
) -> Dict[str, Any]:
    """
    保存实验记录（一个实验计划只能有一个实验结果记录）
    
    业务规则：
    - 根据 F_SYJH_ID 查询是否已存在实验记录
    - 如果存在 → 更新现有记录
    - 如果不存在 → 新增记录
    
    Args:
        data: 入参数据字典（各字段为JSON字符串）
        file_ids: 上传文件的ID数组（按上传顺序）
        operator: 操作人
        skip_qc: 为 True 时不写 T_ZKSH 质控（暂存/提交复核前的静默保存）
    
    Returns:
        保存后的完整记录
    """
    if data.get("F_SYJH_ID") is None:
        raise ValueError("缺少 F_SYJH_ID")

    adapter = get_adapter()
    syjh_id = data.get("F_SYJH_ID")
    now = datetime.now()
    
    # 查询是否已存在该实验计划的记录
    rows, _ = adapter.fetch_all(
        "SELECT F_ID FROM T_SYJL WHERE F_SYJH_ID = ?",
        [syjh_id]
    )
    record_id = rows[0][0] if rows else None

    # 解析各JSON字段
    kbds_raw = json.loads(data.get("KBDS") or "{}") if data.get("KBDS") else {}
    xtxbh_raw = json.loads(data.get("XTXBH") or "[]") if data.get("XTXBH") else []
    xbhl_pj_raw = json.loads(data.get("XBHL_PJ") or "{}") if data.get("XBHL_PJ") else {}
    xbhl_raw = json.loads(data.get("XBHL") or "{}") if data.get("XBHL") else {}
    xbhl_wc_raw = json.loads(data.get("XBHL_WC") or "{}") if data.get("XBHL_WC") else {}
    gmd_raw = json.loads(data.get("GMD") or "{}") if data.get("GMD") else {}
    ic50_raw = json.loads(data.get("IC50") or "[]") if data.get("IC50") else []
    syjl_raw = json.loads(data.get("SYJL") or "{}") if data.get("SYJL") else {}
    record_view: Dict[str, Any] = {}
    if data.get("XTXBH") is not None:
        record_view["XTXBH"] = xtxbh_raw
    elif record_id is None:
        record_view["XTXBH"] = []
    if data.get("SYJL") is not None:
        record_view["F_SYJL"] = syjl_raw
    elif record_id is None:
        record_view["F_SYJL"] = None
    from app.crud.field_config import assert_page
    assert_page("drug_record", record_view, updating=record_id is not None)

    # 替换附件索引为实际文件ID
    xtxbh_final = _replace_attachment_indices(xtxbh_raw, file_ids)
    ic50_final = _replace_attachment_indices(ic50_raw, file_ids)
    syjl_final = _replace_attachment_indices(syjl_raw, file_ids)

    # 构建数据库payload（只更新传入的字段，未传入的字段保留原值）
    payload: Dict[str, Any] = {
        "F_GXSJ": now,
    }
    
    # 只有当字段被传入时才更新
    if data.get("KBDS") is not None:
        payload["F_KBDS_ZBQ"] = _dumps_json(kbds_raw.get("ZBQ"))
        payload["F_KBDS_ZBH"] = _dumps_json(kbds_raw.get("ZBH"))
    
    if data.get("XTXBH") is not None:
        payload["F_XTXBH"] = _dumps_json(xtxbh_final)
    
    if data.get("XBHL_PJ") is not None:
        payload["F_XBHL_PJ"] = _dumps_json(xbhl_pj_raw)
    
    if data.get("XBHL") is not None:
        payload["F_XBHL"] = _dumps_json(xbhl_raw)
    
    if data.get("XBHL_WC") is not None:
        payload["F_XBHL_WC"] = _dumps_json(xbhl_wc_raw)
    
    if data.get("GMD") is not None:
        payload["F_GMD"] = _dumps_json(gmd_raw)
    
    if data.get("IC50") is not None:
        payload["F_IC50"] = _dumps_json(ic50_final)
    
    if data.get("SYJL") is not None:
        payload["F_JGSJ"] = _dumps_json(syjl_final.get("JGSJ"))
        payload["F_SYJL"] = _dumps_json(syjl_final)  # ✅ 存储整个对象（已替换索引为文件ID）
        payload["F_JLFJ"] = _dumps_json(syjl_final.get("F_FJ", []))
    
    # 记录人和记录时间
    if operator:
        payload["F_JLR"] = operator
        payload["F_JLRQ"] = now

    # 保存是新增还是修改的标记（用于质控记录）
    is_create = (record_id is None)
    
    # 如果是修改且需要写质控，获取修改前的数据
    old_detail_data = None
    if not skip_qc and not is_create:
        old_detail_data = get_experiment_record(record_id)
    
    with adapter.connect() as conn:
        cur = conn.cursor()

        if record_id:
            # 更新：只更新传入的字段
            if len(payload) > 1:  # 除了 F_GXSJ 之外还有其他字段
                sets = ", ".join([f"{k} = ?" for k in payload.keys()])
                params = list(payload.values()) + [record_id]
                cur.execute(f"UPDATE T_SYJL SET {sets} WHERE F_ID = ?", params)
            else:
                # 如果只有 F_GXSJ，说明没有任何数据更新，直接返回
                pass
        else:
            # 新增：设置初始值
            payload["F_SYJH_ID"] = data.get("F_SYJH_ID")
            payload["F_CJSJ"] = now
            
            # 为未传入的字段设置默认空值
            if "F_KBDS_ZBQ" not in payload:
                payload["F_KBDS_ZBQ"] = None
            if "F_KBDS_ZBH" not in payload:
                payload["F_KBDS_ZBH"] = None
            if "F_XTXBH" not in payload:
                payload["F_XTXBH"] = None
            if "F_XBHL_PJ" not in payload:
                payload["F_XBHL_PJ"] = None
            if "F_XBHL" not in payload:
                payload["F_XBHL"] = None
            if "F_XBHL_WC" not in payload:
                payload["F_XBHL_WC"] = None
            if "F_GMD" not in payload:
                payload["F_GMD"] = None
            if "F_IC50" not in payload:
                payload["F_IC50"] = None
            if "F_JGSJ" not in payload:
                payload["F_JGSJ"] = None
            if "F_SYJL" not in payload:
                payload["F_SYJL"] = None
            if "F_JLFJ" not in payload:
                payload["F_JLFJ"] = None
            
            cols = list(payload.keys())
            placeholders = ", ".join(["?" for _ in cols])
            values = [payload[c] for c in cols]
            cur.execute(
                f"INSERT INTO T_SYJL ({', '.join(cols)}) VALUES ({placeholders})",
                values
            )
            # 获取新ID
            cur.execute(
                "SELECT MAX(F_ID) FROM T_SYJL WHERE F_SYJH_ID = ?",
                (data.get("F_SYJH_ID"),)
            )
            record_id = cur.fetchone()[0]

        # ✅ 自动更新实验计划状态为"进行中"（如果当前状态是"已创建"）
        # 无论是新增还是更新实验记录，都要检查并更新状态
        cur.execute("SELECT F_ZT FROM T_SYJH WHERE F_ID = ?", (syjh_id,))
        plan_row = cur.fetchone()
        if plan_row:
            current_status = plan_row[0]
            if current_status == "已创建":
                cur.execute("UPDATE T_SYJH SET F_ZT = ? WHERE F_ID = ?", ("进行中", syjh_id))
                logger.info(f"实验计划(ID={syjh_id})状态已更新: {current_status} → 进行中")

        conn.commit()
    
    # ========== 创建质控记录（暂存 skip_qc=True 时跳过）==========
    qc_audit_id = None
    if not skip_qc:
        try:
            from app.crud.drug_test import get_test_plan_detail
            from app.crud.qc_audit import create_qc_audit_record, calculate_changes, get_sample_basic_info

            plan_detail = get_test_plan_detail(syjh_id)
            if plan_detail:
                gdid = plan_detail.get("F_GDID")
                gdxx = plan_detail.get("GDXX") or {}
                zzid = gdxx.get("F_ZZID")

                if zzid and gdid:
                    sample_info = get_sample_basic_info(zzid)
                    new_detail_data = get_experiment_record_by_plan(syjh_id)

                    if is_create:
                        changes = {
                            "变更字段": [
                                {"字段": "操作", "新值": "新增实验记录"},
                                {"字段": "实验计划", "新值": plan_detail.get("F_SYMC")},
                                {"字段": "记录人", "新值": operator or "系统"},
                                {"字段": "记录时间", "新值": str(now)},
                            ]
                        }
                    else:
                        changes = calculate_changes(old_detail_data, new_detail_data)

                    qc_audit_id = create_qc_audit_record(
                        F_ZKJD="药物实验记录",
                        F_BGLX="新增" if is_create else "修改",
                        F_ZZID=zzid,
                        F_CHANGES=changes,
                        F_JLR=operator or "系统",
                        F_GDID=gdid,
                        F_XM=sample_info.get("F_XM"),
                        F_BRBH=sample_info.get("F_BRBH"),
                        F_YBLY=gdxx.get("F_YBLY"),
                        F_GDBH=gdxx.get("F_GDBH"),
                        F_DCID=gdxx.get("F_DCID"),
                        F_DC=gdxx.get("F_DC"),
                        F_PYSJ=gdxx.get("F_PYSJ"),
                        F_SQR=gdxx.get("F_SQR"),
                        F_OLD_DATA=new_detail_data if is_create else old_detail_data,
                    )
                    logger.info(
                        f"[药物实验记录质控] 质控记录创建成功，记录ID: {record_id}, "
                        f"质控ID: {qc_audit_id}, 类型: {'新增' if is_create else '修改'}"
                    )
        except Exception as e:
            logger.error(
                f"[药物实验记录质控] 创建质控审核记录失败（记录ID: {record_id}）: {e}",
                exc_info=True,
            )

    result = get_experiment_record(record_id)
    if qc_audit_id:
        result["qc_audit_id"] = qc_audit_id

    return result


def _convert_file_ids_to_urls(file_ids: List[int]) -> List[Dict[str, Any]]:
    """将文件ID数组转换为文件信息数组（含静态URL）"""
    if not file_ids:
        return []
    
    from app.crud.file_center import get_files_by_ids
    
    files = get_files_by_ids(file_ids)
    result = []
    for f in files:
        result.append({
            "id": f.get("F_ID"),
            "path": f.get("F_PATH"),  # 静态URL路径，如 "uploads/xxx/xxx.jpg"
            "name": f.get("F_ORIGINAL_NAME"),
        })
    return result


def _process_record_detail(row: Dict[str, Any], syjh_id: int, adapter) -> Dict[str, Any]:
    """处理实验记录详情的通用逻辑"""
    # 反序列化JSON字段
    for field in _JSON_FIELDS:
        if field in row:
            row[field] = _loads_json(row.get(field))
    
    # ✅ 特殊处理：F_SYJL 字段需要解析（它不在 _JSON_FIELDS 中，但也是 JSON）
    if "F_SYJL" in row and isinstance(row["F_SYJL"], str):
        try:
            row["F_SYJL"] = json.loads(row["F_SYJL"])
        except Exception:
            pass
    
    # 时间字段转字符串
    for time_field in ["F_JLRQ", "F_CJSJ", "F_GXSJ", "F_SYKSRQ"]:
        if row.get(time_field) is not None:
            row[time_field] = str(row[time_field])
    
    # 转换附件ID为文件信息
    # 1. 形态学变化中的附件
    xtxbh = row.get("F_XTXBH") or []
    if isinstance(xtxbh, list):
        for item in xtxbh:
            if isinstance(item, dict) and "F_FJ" in item:
                file_ids = item["F_FJ"]
                if isinstance(file_ids, list):
                    item["F_FJ"] = _convert_file_ids_to_urls([int(fid) for fid in file_ids if fid])
    
    # 2. 结论附件
    jlfj = row.get("F_JLFJ") or []
    if isinstance(jlfj, list):
        row["F_JLFJ"] = _convert_file_ids_to_urls([int(fid) for fid in jlfj if fid])
    
    # 3. IC50附件
    ic50_data = row.get("F_IC50") or []
    if isinstance(ic50_data, list):
        for item in ic50_data:
            if isinstance(item, dict) and "F_FJ" in item:
                fj_val = item["F_FJ"]
                # F_FJ可能是单个整数或数组
                if isinstance(fj_val, list):
                    item["F_FJ"] = _convert_file_ids_to_urls([int(fid) for fid in fj_val if fid])
                elif isinstance(fj_val, int) and fj_val > 0:
                    # 单个文件ID，转为数组格式
                    item["F_FJ"] = _convert_file_ids_to_urls([fj_val])
                else:
                    item["F_FJ"] = []
    
    # 4. 实验结论中的附件 (F_SYJL.F_FJ)
    syjl = row.get("F_SYJL")
    if isinstance(syjl, dict) and "F_FJ" in syjl:
        fj_val = syjl["F_FJ"]
        if isinstance(fj_val, list):
            syjl["F_FJ"] = _convert_file_ids_to_urls([int(fid) for fid in fj_val if fid])
        elif isinstance(fj_val, int) and fj_val > 0:
            syjl["F_FJ"] = _convert_file_ids_to_urls([fj_val])
        else:
            syjl["F_FJ"] = []
    
    # 查询组别信息（包含浓度梯度）
    rows_zb, cols_zb = adapter.fetch_all(
        """
        SELECT F_ID, F_ZBMC, F_ND, F_SORT, F_FKSSZ
        FROM T_SYJH_ZB
        WHERE F_SYJH_ID = ?
        ORDER BY F_SORT, F_ID
        """,
        [syjh_id]
    )
    
    groups = []
    for r in rows_zb:
        zb_dict = dict(zip(cols_zb, r))
        # 解析浓度梯度JSON
        nd_str = zb_dict.get("F_ND")
        if nd_str:
            try:
                zb_dict["F_ND"] = _loads_json(nd_str) or []
            except:
                zb_dict["F_ND"] = []
        else:
            zb_dict["F_ND"] = []
        zb_dict["F_FKSSZ"] = int(zb_dict["F_FKSSZ"])
        groups.append(zb_dict)
    
    row["ZBXX"] = groups
    
    # 生成IC50参数（用于前端生图）
    ic50_params = []
    xbhl_pj = row.get("F_XBHL_PJ") or {}
    ic50_custom = row.get("F_IC50") or []
    
    for group in groups:
        zbid = str(group["F_ID"])
        zbmc = group["F_ZBMC"]
        nd_list = group["F_ND"]
        
        # 检查是否有用户上传的自定义IC50图
        custom_files = None
        if isinstance(ic50_custom, list):
            for ic50_item in ic50_custom:
                if isinstance(ic50_item, dict) and str(ic50_item.get("F_ZBID")) == zbid:
                    fj_data = ic50_item.get("F_FJ")
                    # F_FJ现在已经是文件信息数组（在前面已转换）
                    if fj_data:
                        custom_files = fj_data
                    break
        
        # 如果有平均活率数据，生成IC50参数
        if zbid in xbhl_pj and nd_list:
            concentrations = []
            responses = []
            for nd in nd_list:
                nd_key = str(nd)
                if nd_key in xbhl_pj[zbid]:
                    try:
                        concentrations.append(float(nd))
                        responses.append(float(xbhl_pj[zbid][nd_key]))
                    except (ValueError, TypeError):
                        continue
            
            if concentrations and responses:
                ic50_params.append({
                    "F_ZBID": int(zbid),
                    "F_ZBMC": zbmc,
                    "concentrations": concentrations,
                    "responses": responses,
                    "custom_files": custom_files  # 现在是文件信息数组，不是ID
                })
    
    row["IC50_PARAMS"] = ic50_params
    
    return row


def get_experiment_record(record_id: int) -> Optional[Dict[str, Any]]:
    """根据记录ID获取实验记录详情（内部使用）"""
    adapter = get_adapter()
    
    # 查询实验记录
    rows, cols = adapter.fetch_all(
        """
        SELECT
            r.F_ID,
            r.F_SYJH_ID,
            r.F_KBDS_ZBQ,
            r.F_KBDS_ZBH,
            r.F_XTXBH,
            r.F_XBHL_PJ,
            r.F_XBHL,
            r.F_XBHL_WC,
            r.F_GMD,
            r.F_IC50,
            r.F_JGSJ,
            r.F_SYJL,
            r.F_JLFJ,
            r.F_JLR,
            r.F_JLRQ,
            r.F_CJSJ,
            r.F_GXSJ,
            p.F_SYMC,
            p.F_SYKSRQ,
            p.F_SYR,
            p.F_SYJHYMD,
            p.F_KBLX
        FROM T_SYJL r
        LEFT JOIN T_SYJH p ON r.F_SYJH_ID = p.F_ID
        WHERE r.F_ID = ?
        """,
        [record_id],
    )
    if not rows:
        return None
    
    row = dict(zip(cols, rows[0]))
    syjh_id = row.get("F_SYJH_ID")
    
    # 调用通用的详情处理逻辑
    return _process_record_detail(row, syjh_id, adapter)


def get_experiment_record_by_plan(syjh_id: int) -> Optional[Dict[str, Any]]:
    """根据实验计划ID获取实验记录详情（含实验计划信息和IC50参数）
    
    如果该实验计划尚未录入实验结果，则返回实验计划的基础信息和空的记录结构，
    以便前端能正常渲染录入表单。
    """
    adapter = get_adapter()
    
    # 查询实验记录
    rows, cols = adapter.fetch_all(
        """
        SELECT
            r.F_ID,
            r.F_SYJH_ID,
            r.F_KBDS_ZBQ,
            r.F_KBDS_ZBH,
            r.F_XTXBH,
            r.F_XBHL_PJ,
            r.F_XBHL,
            r.F_XBHL_WC,
            r.F_GMD,
            r.F_IC50,
            r.F_JGSJ,
            r.F_SYJL,
            r.F_JLFJ,
            r.F_JLR,
            r.F_JLRQ,
            r.F_CJSJ,
            r.F_GXSJ,
            p.F_SYMC,
            p.F_SYKSRQ,
            p.F_SYR,
            p.F_SYJHYMD,
            p.F_KBLX
        FROM T_SYJL r
        LEFT JOIN T_SYJH p ON r.F_SYJH_ID = p.F_ID
        WHERE r.F_SYJH_ID = ?
        """,
        [syjh_id],
    )
    
    if rows:
        # 找到了实验记录，返回完整信息
        row = dict(zip(cols, rows[0]))
        return _process_record_detail(row, syjh_id, adapter)
    
    # 没有找到实验记录，查询实验计划基础信息
    plan_rows, plan_cols = adapter.fetch_all(
        """
        SELECT
            F_ID,
            F_SYMC,
            F_SYKSRQ,
            F_SYR,
            F_SYJHYMD,
            F_KBLX
        FROM T_SYJH
        WHERE F_ID = ?
        """,
        [syjh_id]
    )
    
    if not plan_rows:
        # 实验计划不存在
        return None
    
    # 构造空的实验记录结构，但包含实验计划基础信息
    plan = dict(zip(plan_cols, plan_rows[0]))
    
    empty_record = {
        "F_ID": None,
        "F_SYJH_ID": syjh_id,
        "F_KBDS_ZBQ": None,
        "F_KBDS_ZBH": None,
        "F_XTXBH": None,
        "F_XBHL_PJ": None,
        "F_XBHL": None,
        "F_XBHL_WC": None,
        "F_GMD": None,
        "F_IC50": None,
        "F_JGSJ": None,
        "F_SYJL": None,
        "F_JLFJ": None,
        "F_JLR": None,
        "F_JLRQ": None,
        "F_CJSJ": None,
        "F_GXSJ": None,
        "F_SYMC": plan.get("F_SYMC"),
        "F_SYKSRQ": str(plan.get("F_SYKSRQ")) if plan.get("F_SYKSRQ") else None,
        "F_SYR": plan.get("F_SYR"),
        "F_SYJHYMD": plan.get("F_SYJHYMD"),
        "F_KBLX": plan.get("F_KBLX"),
    }
    
    # 调用通用的详情处理逻辑（会添加组别信息和IC50参数）
    return _process_record_detail(empty_record, syjh_id, adapter)


def list_experiment_records(
    skip: int = 0,
    limit: int = 20,
    syjh_id: Optional[int] = None,
    jlr: Optional[str] = None,
    keyword: Optional[str] = None,
    user: Optional[Dict[str, Any]] = None,
    scope: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """获取实验记录列表（分页）"""
    adapter = get_adapter()
    where, params = [], []
    
    if syjh_id is not None:
        where.append("F_SYJH_ID = ?")
        params.append(syjh_id)
    
    if jlr:
        where.append("F_JLR = ?")
        params.append(jlr)
    
    if keyword:
        kw = f"%{keyword.strip()}%"
        where.append("F_SYJL LIKE ?")
        params.append(kw)

    from app.core.data_scope import apply_owner_filter
    apply_owner_filter(where, params, scope=scope, user=user, column="F_JLR", match="name")

    where_sql = (" WHERE " + " AND ".join(where)) if where else ""

    base_sql = f"""
    SELECT
        F_ID,
        F_SYJH_ID,
        F_KBDS_ZBQ,
        F_KBDS_ZBH,
        F_XTXBH,
        F_XBHL_PJ,
        F_XBHL,
        F_XBHL_WC,
        F_GMD,
        F_IC50,
        F_JGSJ,
        F_SYJL,
        F_JLFJ,
        F_JLR,
        F_JLRQ,
        F_CJSJ,
        F_GXSJ
    FROM T_SYJL
    {where_sql}
    ORDER BY F_ID DESC
    """
    
    sql, extra = adapter.paginate(base_sql, skip=skip, limit=limit)
    rows, cols = adapter.fetch_all(sql, params + extra)
    
    results: List[Dict[str, Any]] = []
    for r in rows:
        row = dict(zip(cols, r))
        
        # 反序列化JSON字段
        for field in _JSON_FIELDS:
            if field in row:
                row[field] = _loads_json(row.get(field))
        
        # 时间字段转字符串
        for time_field in ["F_JLRQ", "F_CJSJ", "F_GXSJ"]:
            if row.get(time_field) is not None:
                row[time_field] = str(row[time_field])
        
        results.append(row)
    
    return results


def count_experiment_records(
    syjh_id: Optional[int] = None,
    jlr: Optional[str] = None,
    keyword: Optional[str] = None,
    user: Optional[Dict[str, Any]] = None,
    scope: Optional[str] = None,
) -> int:
    """统计实验记录总数"""
    adapter = get_adapter()
    where, params = [], []
    
    if syjh_id is not None:
        where.append("F_SYJH_ID = ?")
        params.append(syjh_id)
    
    if jlr:
        where.append("F_JLR = ?")
        params.append(jlr)
    
    if keyword:
        kw = f"%{keyword.strip()}%"
        where.append("F_SYJL LIKE ?")
        params.append(kw)

    from app.core.data_scope import apply_owner_filter
    apply_owner_filter(where, params, scope=scope, user=user, column="F_JLR", match="name")

    where_sql = (" WHERE " + " AND ".join(where)) if where else ""
    sql = f"SELECT COUNT(*) AS cnt FROM T_SYJL {where_sql}"
    rows, _ = adapter.fetch_all(sql, params)
    return rows[0][0] if rows else 0


def generate_ic50_image(syjh_id: int, zbid: int) -> Dict[str, Any]:
    """
    生成IC50曲线图
    
    Args:
        syjh_id: 实验计划ID
        zbid: 组别ID
    
    Returns:
        {
            "success": bool,
            "ic50": float,
            "slope": float,
            "r2": float,
            "image_path": str,
            "image_url": str,
            "file_id": int,
            "error": str
        }
    """
    import numpy as np
    import matplotlib
    matplotlib.use('Agg')  # 非GUI后端
    import matplotlib.pyplot as plt
    from pathlib import Path
    from datetime import datetime
    from app.utils.ic50_analyzer import IC50Analyzer
    from app.crud.file_center import insert_file_record
    
    adapter = get_adapter()
    
    try:
        # 1. 一次查询获取所有需要的数据（优化）
        rows, cols = adapter.fetch_all(
            """
            SELECT
                r.F_XBHL_PJ,
                z.F_ZBMC,
                z.F_ND
            FROM T_SYJL r
            JOIN T_SYJH_ZB z ON z.F_SYJH_ID = r.F_SYJH_ID AND z.F_ID = ?
            WHERE r.F_SYJH_ID = ?
            """,
            [zbid, syjh_id]
        )
        
        if not rows:
            return {
                "success": False,
                "message": f"未找到实验数据或组别信息（实验计划ID={syjh_id}, 组别ID={zbid}）"
            }
        
        xbhl_pj_str = rows[0][0]
        zbmc = rows[0][1]
        nd_str = rows[0][2]
        
        # 解析平均细胞活率
        xbhl_pj = _loads_json(xbhl_pj_str) if xbhl_pj_str else {}
        zbid_str = str(zbid)
        
        if not xbhl_pj:
            return {
                "success": False,
                "message": "实验记录中没有平均细胞活率数据，请先保存实验结果"
            }
        
        if zbid_str not in xbhl_pj:
            available_groups = list(xbhl_pj.keys())
            return {
                "success": False,
                "message": f"未找到组别 {zbid}（{zbmc}）的平均细胞活率数据。可用组别ID: {available_groups}"
            }
        
        # 解析浓度梯度
        nd_list = _loads_json(nd_str) if nd_str else []
        
        if not nd_list:
            return {
                "success": False,
                "message": f"组别 {zbmc}（ID={zbid}）没有配置浓度梯度，请检查实验计划设置"
            }
        
        # 3. 构建浓度和活率数组
        concentrations = []
        responses = []
        xbhl_data = xbhl_pj[zbid_str]
        
        for nd in nd_list:
            # 先尝试解析浓度值（去除单位）
            try:
                # 去除可能的单位（uM, μM等）
                nd_clean = str(nd).replace("uM", "").replace("μM", "").replace("um", "").strip()
                conc = float(nd_clean)
            except (ValueError, TypeError) as e:
                print(f"[IC50] 跳过无效浓度: nd={nd}, error={e}")
                continue
            
            # 尝试多种可能的key格式匹配活率数据
            nd_key_variants = [
                str(nd),           # 原始格式，如 "0.01uM"
                nd_clean,          # 去单位后，如 "0.01"
                str(conc),         # 转float再转str，如 "0.01"
            ]
            
            matched = False
            for nd_key in nd_key_variants:
                if nd_key in xbhl_data:
                    try:
                        resp = float(xbhl_data[nd_key])
                        concentrations.append(conc)
                        responses.append(resp)
                        matched = True
                        print(f"[IC50] 成功匹配数据点: 浓度={conc}, 活率={resp}, key={nd_key}")
                        break
                    except (ValueError, TypeError) as e:
                        print(f"[IC50] 活率值无效: key={nd_key}, value={xbhl_data[nd_key]}, error={e}")
                        continue
            
            if not matched:
                print(f"[IC50] 未找到匹配的活率数据: nd={nd}, 尝试的keys={nd_key_variants}, 可用keys={list(xbhl_data.keys())}")
        
        if len(concentrations) < 3:
            return {
                "success": False,
                "message": f"有效数据点不足：需要至少3个点用于拟合IC50曲线，当前仅有{len(concentrations)}个。浓度梯度={nd_list}，平均细胞活率数据keys={list(xbhl_data.keys())}"
            }
        
        concentrations = np.array(concentrations)
        responses = np.array(responses)
        
        # 4. 计算IC50
        analyzer = IC50Analyzer()
        result = analyzer.calculate_ic50(concentrations, responses)
        
        if not result["success"]:
            return {
                "success": False,
                "message": f"IC50计算失败: {result.get('error', '未知错误')}"
            }
        
        # 5. 生成图像
        plt.figure(figsize=(8, 6))
        
        # 生成平滑曲线
        x_log = np.log10(concentrations)
        x_smooth_log = np.linspace(min(x_log) - 0.5, max(x_log) + 0.5, 200)
        x_smooth_real = 10 ** x_smooth_log
        y_smooth = result["model_func"](x_smooth_log, *result["params"])
        
        # 绘制
        plt.scatter(concentrations, responses, color="red", s=60, label="实验数据", zorder=5)
        plt.plot(x_smooth_real, y_smooth, color="blue", linewidth=2, label="拟合曲线")
        plt.axvline(result["ic50"], color="green", linestyle="--", 
                   label=f'IC50 = {result["ic50"]:.4f} μM')
        plt.axhline(50, color="gray", linestyle=":", alpha=0.5)
        
        plt.xscale("log")
        plt.xlabel("Concentration (μM, log scale)", fontsize=12)
        plt.ylabel("Cell Viability (%)", fontsize=12)
        plt.title(f'{zbmc} - IC50: {result["ic50"]:.4f} μM | R²: {result["r2"]:.4f}', 
                 fontsize=14, fontweight='bold')
        plt.legend(loc='best')
        plt.grid(True, which="both", ls="-", alpha=0.2)
        plt.tight_layout()
        
        # 6. 保存图像
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"ic50_{syjh_id}_{zbid}_{timestamp}.png"
        
        # 创建保存目录
        upload_dir = Path("uploads/ic50")
        upload_dir.mkdir(parents=True, exist_ok=True)
        
        file_path = upload_dir / filename
        plt.savefig(file_path, dpi=150, bbox_inches='tight')
        plt.close()
        
        # 7. 保存文件记录
        relative_path = str(file_path).replace("\\", "/")
        file_id = insert_file_record(relative_path, filename)
        
        return {
            "success": True,
            "ic50": float(result["ic50"]),
            "slope": float(result["slope"]),
            "r2": float(result["r2"]),
            "image_path": relative_path,
            "image_url": f"/{relative_path}",
            "file_id": file_id
        }
        
    except Exception as e:
        import traceback
        print(f"[IC50] 异常详情: {traceback.format_exc()}")
        return {
            "success": False,
            "message": f"生成IC50图像失败: {str(e)}"
        }


def generate_ic50_image(syjh_id: int, zbid: int) -> Dict[str, Any]:
    """
    生成 IC50 曲线图
    
    Args:
        syjh_id: 实验计划ID
        zbid: 组别ID
    
    Returns:
        Dict 包含：
        - success: bool
        - message: str
        - ic50: float (可选)
        - r2: float (可选)
        - slope: float (可选)
        - image_url: str (可选)
        - image_id: int (可选)
        - group_name: str (可选)
    """
    adapter = get_adapter()
    
    try:
        # 1. 查询实验记录和组别信息
        rows, cols = adapter.fetch_all(
            """
            SELECT 
                r.F_XBHL_PJ,
                z.F_ZBMC
            FROM T_SYJL r
            JOIN T_SYJH_ZB z ON z.F_SYJH_ID = r.F_SYJH_ID AND z.F_ID = ?
            WHERE r.F_SYJH_ID = ?
            """,
            [zbid, syjh_id]
        )
        
        if not rows:
            return {
                "success": False,
                "message": "未找到实验记录或组别信息"
            }
        
        row = dict(zip(cols, rows[0]))
        xbhl_pj = _loads_json(row.get("F_XBHL_PJ")) or {}
        group_name = row.get("F_ZBMC") or f"组别{zbid}"
        
        # 2. 获取该组别的平均细胞活率数据
        zbid_str = str(zbid)
        if zbid_str not in xbhl_pj:
            return {
                "success": False,
                "message": f"组别 {group_name} 没有细胞活率数据"
            }
        
        activity_data = xbhl_pj[zbid_str]
        
        if not activity_data or not isinstance(activity_data, dict):
            return {
                "success": False,
                "message": f"组别 {group_name} 的细胞活率数据格式错误"
            }
        
        # 3. 直接从实验记录的活率数据中提取浓度和活率
        # 而不是依赖实验计划的浓度梯度配置（因为可能不一致）
        concentrations = []
        responses = []
        
        for conc_str, resp_val in activity_data.items():
            try:
                # 去除可能的单位（uM, μM等）
                conc_clean = str(conc_str).replace("uM", "").replace("μM", "").replace("um", "").strip()
                conc = float(conc_clean)
                resp = float(resp_val)
                
                # IC50计算使用对数尺度，必须过滤掉浓度<=0的点
                if conc <= 0:
                    print(f"[IC50] 跳过浓度<=0的数据点: 浓度={conc}uM, 活率={resp}% (IC50计算需要浓度>0)")
                    continue
                
                concentrations.append(conc)
                responses.append(resp)
                print(f"[IC50] 提取数据点: 浓度={conc}uM, 活率={resp}%")
            except (ValueError, TypeError) as e:
                print(f"[IC50] 跳过无效数据: key={conc_str}, value={resp_val}, error={e}")
                continue
        
        if len(concentrations) < 3:
            return {
                "success": False,
                "message": f"组别 {group_name} 的有效数据点不足（至少需要3个点），当前只有 {len(concentrations)} 个。活率数据={activity_data}"
            }
        
        # 5. 调用 IC50Analyzer 计算
        analyzer = IC50Analyzer()
        conc_array = np.array(concentrations)
        resp_array = np.array(responses)
        
        result = analyzer.calculate_ic50(conc_array, resp_array)
        
        if not result["success"]:
            return {
                "success": False,
                "message": f"IC50 计算失败: {result.get('error', '未知错误')}",
                "group_name": group_name
            }
        
        # 6. 生成图像并保存
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"ic50_{syjh_id}_{zbid}_{timestamp}.png"
        
        # 使用 run.py 所在目录作为基准路径
        import os
        import sys
        if getattr(sys, 'frozen', False):
            # 打包后的exe
            base_dir = Path(sys._MEIPASS)
        else:
            # 开发环境：run.py 所在目录
            base_dir = Path(__file__).resolve().parent.parent.parent
        
        # 创建保存目录
        save_dir = base_dir / "uploads" / "ic50" / datetime.now().strftime("%Y%m")
        save_dir.mkdir(parents=True, exist_ok=True)
        file_path = save_dir / filename
        
        # 生成图像
        plt.figure(figsize=(8, 6))
        
        # 原始数据点
        plt.scatter(conc_array, resp_array, color="red", s=60, label="实验数据", zorder=5)
        
        # 拟合曲线
        x_log = np.log10(conc_array)
        x_smooth_log = np.linspace(min(x_log) - 0.5, max(x_log) + 0.5, 200)
        x_smooth_real = 10 ** x_smooth_log
        y_smooth = result["model_func"](x_smooth_log, *result["params"])
        plt.plot(x_smooth_real, y_smooth, color="blue", linewidth=2, label="拟合曲线")
        
        # IC50 标记
        ic50_val = result["ic50"]
        plt.axvline(ic50_val, color="green", linestyle="--", label=f'IC50 = {ic50_val:.4f} μM')
        plt.axhline(50, color="gray", linestyle=":", alpha=0.5)
        
        # 设置
        plt.xscale("log")
        plt.xlabel("药物浓度 (μM, log scale)", fontsize=12)
        plt.ylabel("细胞活率 (%)", fontsize=12)
        plt.title(f'{group_name} - IC50: {ic50_val:.4f} μM | R²: {result["r2"]:.4f}', fontsize=14)
        plt.legend()
        plt.grid(True, which="both", ls="-", alpha=0.2)
        plt.tight_layout()
        
        # 保存图像
        plt.savefig(str(file_path), dpi=150, bbox_inches='tight')
        plt.close()
        
        # 7. 插入文件记录（使用相对于项目根目录的路径）
        from app.crud.file_center import insert_file_record
        rel_path = str(file_path.relative_to(base_dir)).replace("\\", "/")
        file_id = insert_file_record(rel_path, filename)
        
        # 8. 更新实验记录的F_IC50字段，保存IC50数据和图像ID
        try:
            # 读取现有的F_IC50数据
            rows_ic50, cols_ic50 = adapter.fetch_all(
                "SELECT F_IC50 FROM T_SYJL WHERE F_SYJH_ID = ?",
                [syjh_id]
            )
            
            if rows_ic50:
                f_ic50_str = rows_ic50[0][0]
                f_ic50_data = _loads_json(f_ic50_str) if f_ic50_str else []
            else:
                f_ic50_data = []
            
            # 确保是数组
            if not isinstance(f_ic50_data, list):
                f_ic50_data = []
            
            # 检查该组别是否已存在，如果存在则更新，否则追加
            zbid_found = False
            for item in f_ic50_data:
                if item.get("F_ZBID") == zbid:
                    # 更新现有组别的IC50数据
                    item["ic50"] = float(ic50_val)
                    item["r2"] = float(result["r2"])
                    item["slope"] = float(result["slope"])
                    item["F_FJ"] = int(file_id) if file_id else None
                    zbid_found = True
                    print(f"[IC50] 更新组别{zbid}的IC50数据")
                    break
            
            # 如果该组别不存在，追加新记录
            if not zbid_found:
                f_ic50_data.append({
                    "F_ZBID": zbid,
                    "ic50": float(ic50_val),
                    "r2": float(result["r2"]),
                    "slope": float(result["slope"]),
                    "F_FJ": int(file_id) if file_id else None
                })
                print(f"[IC50] 新增组别{zbid}的IC50数据")
            
            # 保存回数据库
            import json
            f_ic50_json = json.dumps(f_ic50_data, ensure_ascii=False)
            
            with adapter.connect() as conn:
                cur = conn.cursor()
                cur.execute(
                    "UPDATE T_SYJL SET F_IC50 = ? WHERE F_SYJH_ID = ?",
                    [f_ic50_json, syjh_id]
                )
                conn.commit()
            
            print(f"[IC50] 已更新实验记录的F_IC50字段: {f_ic50_json}")
        except Exception as update_err:
            import traceback
            print(f"[IC50] 更新F_IC50字段失败: {update_err}")
            print(traceback.format_exc())
            # 不影响主流程，继续返回成功
        
        return {
            "success": True,
            "message": "IC50 曲线图生成成功",
            "ic50": float(ic50_val),
            "r2": float(result["r2"]),
            "slope": float(result["slope"]),
            "image_url": f"/{rel_path}",
            "image_id": int(file_id) if file_id else None,
            "group_name": group_name
        }
        
    except Exception as e:
        return {
            "success": False,
            "message": f"生成IC50图像时发生错误: {str(e)}"
        }


def preview_ic50_image(concentrations: List[float], responses: List[float], group_name: str = "预览组"):
    """
    预览 IC50 曲线图（不保存文件，直接返回图像字节流）
    
    Args:
        concentrations: 浓度数组（μM）
        responses: 细胞活率数组（%）
        group_name: 组别名称
    
    Returns:
        Dict 包含：
        - success: bool
        - message: str
        - image_bytes: bytes (成功时包含图像字节数据)
        - ic50: float (可选)
        - r2: float (可选)
        - slope: float (可选)
    """
    import io
    
    try:
        # 1. 验证数据
        if len(concentrations) != len(responses):
            return {
                "success": False,
                "message": "浓度数组和活率数组长度不一致"
            }
        
        if len(concentrations) < 3:
            return {
                "success": False,
                "message": f"数据点不足（至少需要3个点），当前只有 {len(concentrations)} 个"
            }
        
        # 2. 转换为numpy数组并过滤无效数据
        valid_concs = []
        valid_resps = []
        
        for conc, resp in zip(concentrations, responses):
            try:
                conc_val = float(conc)
                resp_val = float(resp)
                
                # IC50计算使用对数尺度，必须过滤掉浓度<=0的点
                if conc_val <= 0:
                    print(f"[IC50预览] 跳过浓度<=0的数据点: 浓度={conc_val}μM, 活率={resp_val}%")
                    continue
                
                valid_concs.append(conc_val)
                valid_resps.append(resp_val)
            except (ValueError, TypeError) as e:
                print(f"[IC50预览] 跳过无效数据: 浓度={conc}, 活率={resp}, 错误={e}")
                continue
        
        if len(valid_concs) < 3:
            return {
                "success": False,
                "message": f"有效数据点不足（至少需要3个点），当前只有 {len(valid_concs)} 个"
            }
        
        conc_array = np.array(valid_concs)
        resp_array = np.array(valid_resps)
        
        # 3. 调用 IC50Analyzer 计算
        analyzer = IC50Analyzer()
        result = analyzer.calculate_ic50(conc_array, resp_array)
        
        if not result["success"]:
            return {
                "success": False,
                "message": f"IC50 计算失败: {result.get('error', '未知错误')}"
            }
        
        # 4. 生成图像（使用BytesIO，不保存到文件）
        fig = plt.figure(figsize=(8, 6))
        
        # 原始数据点
        plt.scatter(conc_array, resp_array, color="red", s=60, label="实验数据", zorder=5)
        
        # 拟合曲线
        x_log = np.log10(conc_array)
        x_smooth_log = np.linspace(min(x_log) - 0.5, max(x_log) + 0.5, 200)
        x_smooth_real = 10 ** x_smooth_log
        y_smooth = result["model_func"](x_smooth_log, *result["params"])
        plt.plot(x_smooth_real, y_smooth, color="blue", linewidth=2, label="拟合曲线")
        
        # IC50 标记
        ic50_val = result["ic50"]
        plt.axvline(ic50_val, color="green", linestyle="--", label=f'IC50 = {ic50_val:.4f} μM')
        plt.axhline(50, color="gray", linestyle=":", alpha=0.5)
        
        # 设置
        plt.xscale("log")
        plt.xlabel("药物浓度 (μM, log scale)", fontsize=12)
        plt.ylabel("细胞活率 (%)", fontsize=12)
        plt.title(f'{group_name} - IC50: {ic50_val:.4f} μM | R²: {result["r2"]:.4f}', fontsize=14)
        plt.legend()
        plt.grid(True, which="both", ls="-", alpha=0.2)
        plt.tight_layout()
        
        # 保存到BytesIO
        buf = io.BytesIO()
        plt.savefig(buf, format='png', dpi=150, bbox_inches='tight')
        plt.close(fig)
        
        # 获取字节数据
        buf.seek(0)
        image_bytes = buf.read()
        buf.close()
        
        return {
            "success": True,
            "message": "IC50 曲线图预览生成成功",
            "image_bytes": image_bytes,
            "ic50": float(ic50_val),
            "r2": float(result["r2"]),
            "slope": float(result["slope"])
        }
        
    except Exception as e:
        import traceback
        print(f"[IC50预览] 生成失败: {e}")
        print(traceback.format_exc())
        return {
            "success": False,
            "message": f"生成IC50预览图像时发生错误: {str(e)}"
        }
