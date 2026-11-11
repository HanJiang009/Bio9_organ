"""
药物试验报告数据组装模块
负责整合药物试验报告所需的所有数据
"""
from typing import Dict, Any, Optional
import json
import logging
from datetime import datetime
from app.db.factory import get_adapter

logger = logging.getLogger(__name__)


def assemble_drug_test_report(syjh_id: int) -> Dict[str, Any]:
    """
    组装药物试验报告数据
    
    Args:
        syjh_id: 实验计划ID (T_SYJH.F_ID)
        
    Returns:
        包含完整报告数据的字典，包括：
        - 实验计划信息（核心）
        - 实验记录基本信息（如果有）
        - 培养工单信息
        - 质控记录（质检数据，来自T_ZKJL表）
        - 观察记录（培养过程数据，来自T_GCJL表）
        - 所有JSON字段原样返回
        
    Raises:
        ValueError: 当实验计划不存在时
        
    Note:
        - 实验记录可能还未创建，此时返回的实验记录部分为None
        - 报告生成应该在实验记录提交复核后触发，此时必然有实验记录
        - 质控记录包含关键质检指标：细胞活率、污染情况、支原体检测等
        - 观察记录包含培养过程的日常观察数据
    """
    adapter = get_adapter()
    
    with adapter.connect() as conn:
        cur = conn.cursor()
        
        # ==================== 1. 获取实验计划详细信息 ====================
        logger.info(f"[药物试验报告] 开始组装报告数据，实验计划ID: {syjh_id}")
        
        # 使用现有的 get_test_plan_detail 函数获取完整的计划信息
        try:
            from app.crud.drug_test import get_test_plan_detail
            plan_detail = get_test_plan_detail(syjh_id)
            if not plan_detail:
                raise ValueError(f"未找到实验计划: F_ID={syjh_id}")
            logger.info(f"[药物试验报告] 获取实验计划详情成功")
        except Exception as e:
            logger.error(f"[药物试验报告] 获取实验计划详情失败: {e}", exc_info=True)
            raise ValueError(f"获取实验计划详情失败: {e}")
        
        gdid = plan_detail.get("F_GDID")
        
        # ==================== 2. 获取实验记录基本信息 ====================
        # 根据实验计划ID查询对应的实验记录
        cur.execute("""
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
                r.F_GXSJ
            FROM T_SYJL r
            WHERE r.F_SYJH_ID = ?
        """, (syjh_id,))
        
        record_row = cur.fetchone()
        
        # 实验记录可能还未创建，这是正常的
        record_data = None
        if record_row:
            record_cols = [c[0] for c in cur.description]
            record_data = dict(zip(record_cols, record_row))
            logger.info(f"[药物试验报告] 获取实验记录成功，记录ID: {record_data.get('F_ID')}")
        else:
            logger.warning(f"[药物试验报告] 该实验计划尚未填写实验记录，实验计划ID: {syjh_id}")
        
        # ==================== 3. 获取培养工单信息 ====================
        order_data = None
        zzid = None
        if gdid:
            try:
                from app.crud.organoid_culture_tissue import get_culture_order_detail
                order_data = get_culture_order_detail(gdid)
                zzid = order_data.get('F_ZZID') if order_data else None
                logger.info(f"[药物试验报告] 获取培养工单信息成功，工单编号: {order_data.get('F_GDBH')}, 标本号: {zzid}")
            except Exception as e:
                logger.warning(f"[药物试验报告] 获取培养工单信息失败: {e}")
                order_data = None
        else:
            logger.warning(f"[药物试验报告] 未关联工单，跳过工单信息获取")
        
        # ==================== 3.5. 补充样本信息（取样部位、接收时间等） ====================
        sample_info = None
        if zzid:
            try:
                cur.execute("""
                    SELECT F_CJBW, F_JSSJ
                    FROM T_YBZJ
                    WHERE F_ZZID = ?
                """, (zzid,))
                sample_row = cur.fetchone()
                if sample_row:
                    sample_cols = [c[0] for c in cur.description]
                    sample_info = dict(zip(sample_cols, sample_row))
                    
                    # 将取样部位添加到工单信息中
                    if order_data:
                        order_data["F_CJBW"] = sample_info.get("F_CJBW")
                    
                    logger.info(f"[药物试验报告] 获取样本信息成功，标本号: {zzid}, 取样部位: {sample_info.get('F_CJBW')}")
                else:
                    logger.warning(f"[药物试验报告] 未找到样本信息，标本号: {zzid}")
            except Exception as e:
                logger.warning(f"[药物试验报告] 获取样本信息失败: {e}")
                sample_info = None
        else:
            logger.warning(f"[药物试验报告] 未关联标本号，跳过样本信息获取")
        
        # ==================== 3.6. 补充预处理信息 ====================
        preprocess_info = None
        if gdid:
            try:
                cur.execute("""
                    SELECT F_KSCLSJ, F_YBYCL, F_YBYCLQK
                    FROM T_YCL
                    WHERE F_GDID = ?
                """, (gdid,))
                preprocess_row = cur.fetchone()
                if preprocess_row:
                    preprocess_cols = [c[0] for c in cur.description]
                    preprocess_info = dict(zip(preprocess_cols, preprocess_row))
                    
                    # 转换预处理图片文件ID为URL（支持单 ID 或 ID 列表）
                    from app.crud.release_inspection_report import _resolve_first_file_url
                    preprocess_info["F_YBYCL_URL"] = _resolve_first_file_url(
                        preprocess_info.get("F_YBYCL")
                    )
                    
                    # 格式化处理时间
                    if preprocess_info.get("F_KSCLSJ"):
                        preprocess_info["F_KSCLSJ"] = _format_datetime(preprocess_info.get("F_KSCLSJ"))
                    
                    logger.info(f"[药物试验报告] 获取预处理信息成功，工单ID: {gdid}")
                else:
                    logger.warning(f"[药物试验报告] 未找到预处理信息，工单ID: {gdid}")
            except Exception as e:
                logger.warning(f"[药物试验报告] 获取预处理信息失败: {e}")
                preprocess_info = None
        else:
            logger.warning(f"[药物试验报告] 未关联工单，跳过预处理信息获取")
        
        # ==================== 4. 获取质控记录（质检数据） ====================
        qc_record = None
        if gdid:
            try:
                from app.crud.qc_record import get_qc_record_by_gdid
                from app.crud.release_inspection_report import format_zkjl_data
                
                qc_record_raw = get_qc_record_by_gdid(gdid)
                if qc_record_raw:
                    # 格式化质控记录（转换图片URL等）
                    qc_record = format_zkjl_data(qc_record_raw)
                    logger.info(f"[药物试验报告] 获取质控记录成功，记录ID: {qc_record.get('F_ID')}")
                else:
                    logger.warning(f"[药物试验报告] 未找到质控记录，工单ID: {gdid}")
            except Exception as e:
                logger.warning(f"[药物试验报告] 获取质控记录失败: {e}")
                qc_record = None
        else:
            logger.warning(f"[药物试验报告] 未关联工单，跳过质控记录获取")
        
        # ==================== 5. 获取观察记录（培养过程数据，作为补充） ====================
        observations = []
        if gdid:
            try:
                from app.crud.organoid_observation import list_observations_by_gdid
                observations = list_observations_by_gdid(gdid)
                logger.info(f"[药物试验报告] 获取观察记录成功，共 {len(observations)} 条")
            except Exception as e:
                logger.warning(f"[药物试验报告] 获取观察记录失败: {e}")
                observations = []
        else:
            logger.warning(f"[药物试验报告] 未关联工单，跳过观察记录获取")
        
        # ==================== 6. 组装返回数据 ====================
        # 注意：JSON字段在这里原样返回，不进行解析
        # 解析工作由后续的PDF生成模块负责
        
        # 处理实验记录数据（可能为None）
        experiment_record = None
        if record_data:
            experiment_record = {
                "F_ID": record_data.get("F_ID"),
                "F_SYJH_ID": record_data.get("F_SYJH_ID"),
                "F_JLR": record_data.get("F_JLR"),
                "F_JLRQ": _format_datetime(record_data.get("F_JLRQ")),
                "F_CJSJ": _format_datetime(record_data.get("F_CJSJ")),
                "F_GXSJ": _format_datetime(record_data.get("F_GXSJ")),
                
                # JSON字段（原样返回，字符串形式）
                "F_KBDS_ZBQ": record_data.get("F_KBDS_ZBQ"),
                "F_KBDS_ZBH": record_data.get("F_KBDS_ZBH"),
                "F_XTXBH": record_data.get("F_XTXBH"),
                "F_XBHL_PJ": record_data.get("F_XBHL_PJ"),
                "F_XBHL": record_data.get("F_XBHL"),
                "F_XBHL_WC": record_data.get("F_XBHL_WC"),
                "F_GMD": record_data.get("F_GMD"),
                "F_IC50": record_data.get("F_IC50"),
                "F_JGSJ": record_data.get("F_JGSJ"),
                "F_SYJL": record_data.get("F_SYJL"),
                "F_JLFJ": record_data.get("F_JLFJ"),
            }
        
        # 组装增强的质控记录（包含样本和预处理信息）
        enhanced_qc_record = qc_record.copy() if qc_record else {}
        
        # 补充样本接收时间和取样部位
        if sample_info:
            enhanced_qc_record["F_JSSJ"] = _format_datetime(sample_info.get("F_JSSJ"))
            enhanced_qc_record["F_CJBW"] = sample_info.get("F_CJBW")
        
        # 补充预处理信息
        if preprocess_info:
            enhanced_qc_record["F_KSCLSJ"] = preprocess_info.get("F_KSCLSJ")
            enhanced_qc_record["F_YBYCL_URL"] = preprocess_info.get("F_YBYCL_URL")
            enhanced_qc_record["F_YBYCL"] = preprocess_info.get("F_YBYCL")
            enhanced_qc_record["F_YBYCLQK"] = preprocess_info.get("F_YBYCLQK")
        
        result = {
            # ===== 元信息 =====
            "报告类型": "药物实验报告",
            "生成时间": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            
            # ===== 实验计划完整信息（核心） =====
            "实验计划": plan_detail,
            
            # ===== 实验记录基本信息（可能为None） =====
            "实验记录": experiment_record,
            
            # ===== 培养工单信息 =====
            "培养工单": order_data,
            
            # ===== 质控记录（质检数据，核心，已增强） =====
            "质控记录": enhanced_qc_record if enhanced_qc_record else None,
            
            # ===== 观察记录（培养过程数据，补充） =====
            "观察记录": observations,
            
            # ===== 快捷访问字段（方便前端使用） =====
            "快捷信息": {
                "实验名称": plan_detail.get("F_SYMC"),
                "工单编号": plan_detail.get("F_GDBH") or (order_data.get("F_GDBH") if order_data else None),
                "标本号": zzid,
                "取样部位": sample_info.get("F_CJBW") if sample_info else None,
                "实验开始日期": _format_date(plan_detail.get("F_SYKSRQ")),
                "实验人": plan_detail.get("F_SYR"),
                "记录人": record_data.get("F_JLR") if record_data else None,
                "记录日期": _format_datetime(record_data.get("F_JLRQ")) if record_data else None,
                "孔板类型": plan_detail.get("F_KBLX"),
                "实验状态": plan_detail.get("F_SYZT"),
                "是否已填写记录": record_data is not None,
                "是否有质控记录": qc_record is not None,
            }
        }
        
        logger.info(f"[药物试验报告] 报告数据组装完成，实验计划ID: {syjh_id}, 是否有实验记录: {record_data is not None}, 是否有质控记录: {qc_record is not None}, 是否有样本信息: {sample_info is not None}, 是否有预处理信息: {preprocess_info is not None}")
        
        return result


def _format_datetime(value: Any) -> Optional[str]:
    """
    格式化日期时间为字符串
    
    Args:
        value: 日期时间值（可能是datetime对象或字符串）
        
    Returns:
        格式化后的字符串，如果无法解析则返回原值的字符串形式
    """
    if value is None:
        return None
    
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d %H:%M:%S")
    
    if isinstance(value, str):
        # 尝试解析并重新格式化
        try:
            dt = datetime.fromisoformat(value)
            return dt.strftime("%Y-%m-%d %H:%M:%S")
        except Exception:
            try:
                dt = datetime.strptime(value, "%Y-%m-%d %H:%M:%S")
                return dt.strftime("%Y-%m-%d %H:%M:%S")
            except Exception:
                return value
    
    return str(value)


def _format_date(value: Any) -> Optional[str]:
    """
    格式化日期为字符串（只保留日期部分）
    
    Args:
        value: 日期值（可能是datetime对象或字符串）
        
    Returns:
        格式化后的日期字符串（YYYY-MM-DD）
    """
    if value is None:
        return None
    
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d")
    
    if isinstance(value, str):
        # 尝试解析并重新格式化
        try:
            dt = datetime.fromisoformat(value)
            return dt.strftime("%Y-%m-%d")
        except Exception:
            try:
                dt = datetime.strptime(value, "%Y-%m-%d %H:%M:%S")
                return dt.strftime("%Y-%m-%d")
            except Exception:
                try:
                    dt = datetime.strptime(value, "%Y-%m-%d")
                    return dt.strftime("%Y-%m-%d")
                except Exception:
                    # 如果已经是YYYY-MM-DD格式或无法解析，直接返回
                    return value.split(" ")[0] if " " in value else value
    
    return str(value)
