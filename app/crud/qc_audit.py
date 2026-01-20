"""
质控审核记录模块

用于记录各个流程的质控审核信息，支持变更追踪和审计功能
"""
from typing import Dict, Any, List, Optional
from datetime import datetime
import json
from app.db.factory import get_adapter


def _generate_zkbh() -> str:
    """生成质控编号（带毫秒和随机后缀，确保同一秒内不重复）"""
    import random
    now = datetime.now()
    timestamp = now.strftime('%Y%m%d%H%M%S')
    milliseconds = now.microsecond // 1000  # 微秒转毫秒
    random_suffix = random.randint(10, 99)  # 2位随机数
    return f"ZKSH{timestamp}{milliseconds:03d}{random_suffix}"


def calculate_changes(
    old_data: Optional[Dict[str, Any]], 
    new_data: Dict[str, Any], 
    field_mapping: Optional[Dict[str, str]] = None
) -> Dict[str, Any]:
    """
    计算数据变更差异
    
    Args:
        old_data: 原始数据（修改时提供，新增时为None）
        new_data: 新数据
        field_mapping: 字段映射（字段名->中文名），可选
    
    Returns:
        变更差异JSON
    """
    changes = []
    
    # 新增操作：只记录关键信息
    if old_data is None:
        return {
            "操作": "新增",
            "关键信息": new_data
        }
    
    # ✅ 定义需要简化的复杂JSON字段（避免记录完整内容，防止数据膨胀）
    COMPLEX_JSON_FIELDS = {"F_YBPFB", "F_JZJ", "F_XHMSY"}  # 评分表、基质胶明细、消化酶使用等
    
    def _summarize_complex_value(value, field_name):
        """为复杂JSON字段生成摘要"""
        if value is None or value == "":
            return ""
        # 对于评分表，只记录总分和计算时间
        if field_name == "F_YBPFB":
            if isinstance(value, str):
                import json
                try:
                    value = json.loads(value)
                except:
                    return "[评分表数据]"
            if isinstance(value, dict):
                total = value.get("total", "未知")
                calc_time = value.get("calculated_at", "未知")
                return f"总分: {total}, 计算时间: {calc_time}"
            return "[评分表数据]"
        # 对于数组型字段，只记录数量
        if isinstance(value, list):
            return f"共{len(value)}项"
        if isinstance(value, dict):
            return f"共{len(value)}个字段"
        return str(value)
    
    # 修改操作：记录变更字段
    for field, new_value in new_data.items():
        old_value = old_data.get(field)
        
        # 跳过内部字段
        if field in ["F_ID", "F_DJRQ", "F_DJR"]:
            continue
        
        # 智能比较：尝试数值比较，失败则使用字符串比较
        is_changed = False
        old_value_str = str(old_value) if old_value is not None else ""
        new_value_str = str(new_value) if new_value is not None else ""
        
        # 尝试数值比较（处理 5.0 vs 5 的情况）
        try:
            # 如果两者都能转换为数值，则进行数值比较
            if old_value is not None and new_value is not None:
                old_num = float(old_value)
                new_num = float(new_value)
                is_changed = (old_num != new_num)
            else:
                # 一个为None，一个不为None，则认为有变化
                is_changed = (old_value != new_value)
        except (ValueError, TypeError):
            # 无法转换为数值，使用字符串比较
            is_changed = (old_value_str != new_value_str)
        
        # 跳过未变化的字段
        if not is_changed:
            continue
        
        # ✅ 构建变更记录（对复杂字段使用摘要）
        if field in COMPLEX_JSON_FIELDS:
            change_item = {
                "field": field,
                "old_value": _summarize_complex_value(old_value, field),
                "new_value": _summarize_complex_value(new_value, field)
            }
        else:
            change_item = {
                "field": field,
                "old_value": old_value_str,
                "new_value": new_value_str
            }
        
        # 只在有映射时才添加 label 字段（避免冗余）
        if field_mapping and field in field_mapping:
            change_item["label"] = field_mapping[field]
        
        changes.append(change_item)
    
    return {"变更字段": changes}


def create_qc_audit_record(
    F_ZKJD: str,
    F_BGLX: str,
    F_ZZID: str,
    F_CHANGES: Dict[str, Any],
    F_JLR: str,
    F_PDF: Optional[int] = None,
    F_GDID: Optional[int] = None,
    F_XM: Optional[str] = None,
    F_BRBH: Optional[str] = None,
    F_ZZBW: Optional[str] = None,
    F_YBLY: Optional[str] = None,
    F_SHBZ: Optional[str] = None,
    F_GDBH: Optional[str] = None,
    F_GDZT: Optional[str] = None,
    F_DCID: Optional[str] = None,
    F_DC: Optional[str] = None,
    F_PYSJ: Optional[datetime] = None,
    F_SQR: Optional[str] = None,
    F_PYTS: Optional[int] = None,
    F_OLD_DATA: Optional[Dict[str, Any]] = None  # ✅ 新增参数
) -> int:
    """
    创建质控审核记录
    
    Args:
        F_ZKJD: 质控阶段（登记接收/分发/工单创建/传代培养/铺板/质控检测等）
        F_BGLX: 变更类型（新增/修改/删除）
        F_ZZID: 组织ID（标本号）
        F_CHANGES: 变更内容JSON
        F_JLR: 记录人（操作人员）
        F_PDF: PDF文件ID（可选）
        F_GDID: 工单ID（可选，关联 T_PYGD.F_ID）
        F_XM: 姓名（可选）
        F_BRBH: 病人编号（可选）
        F_ZZBW: 组织部位（可选）
        F_YBLY: 样本来源（可选）
        F_SHBZ: 审核备注（可选）
        F_GDBH: 培养工单编号（可选，从工单创建阶段开始记录）
        F_GDZT: 工单状态（不存储，通过 F_GDBH 关联 T_PYGD 查询）
        F_DCID: 代次ID（可选，从工单创建阶段开始记录）
        F_DC: 代次（可选，从工单创建阶段开始记录）
        F_PYSJ: 开始培养日期（可选，对应表字段 F_KSPYRQ）
        F_SQR: 培养人/申请人（可选，对应表字段 F_PYR）
        F_PYTS: 培养天数（可选，观察阶段通过回收时间计算）
        F_OLD_DATA: 修改前的完整页面详情数据（可选，仅修改操作时有值）
    
    Returns:
        新创建的审核记录ID
    """
    adapter = get_adapter()
    
    # 准备数据（不包含F_OLD_DATA和F_CHANGES，避免行长度超限）
    data = {
        "F_ZKBH": _generate_zkbh(),
        "F_ZKJD": F_ZKJD,
        "F_BGLX": F_BGLX,
        "F_SHZT": "待审核",
        "F_ZZID": F_ZZID,
        "F_JLR": F_JLR,
        "F_JLSJ": datetime.now()
    }
    
    # ✅ 序列化F_CHANGES和F_OLD_DATA（稍后单独UPDATE以避免行长度超限）
    changes_json = json.dumps(F_CHANGES, ensure_ascii=False, default=str)
    old_data_json = None
    if F_OLD_DATA is not None:
        old_data_json = json.dumps(F_OLD_DATA, ensure_ascii=False, default=str)
    
    # 添加可选字段
    if F_PDF is not None:
        data["F_PDF"] = F_PDF
    if F_GDID is not None:
        data["F_GDID"] = F_GDID
    if F_XM:
        data["F_XM"] = F_XM
    if F_BRBH:
        data["F_BRBH"] = F_BRBH
    if F_ZZBW:
        data["F_ZZBW"] = F_ZZBW
    if F_YBLY:
        data["F_YBLY"] = F_YBLY
    if F_SHBZ:
        data["F_SHBZ"] = F_SHBZ
    
    # 添加工单相关字段（从工单创建阶段开始需要记录）
    # 注意：只使用 T_ZKSH 表中实际存在的字段
    if F_GDBH:
        data["F_GDBH"] = F_GDBH  # 工单编号
    if F_DCID:
        data["F_DCID"] = F_DCID  # 代次ID
    if F_DC:
        data["F_DC"] = F_DC  # 代次
    if F_PYSJ is not None:
        data["F_KSPYRQ"] = F_PYSJ  # 开始培养日期
    if F_SQR:
        data["F_PYR"] = F_SQR  # 培养人
    if F_PYTS is not None:
        data["F_PYTS"] = F_PYTS  # 培养天数
    if F_GDID is not None:
        data["F_GDID"] = F_GDID  # 工单ID
    # F_GDZT (工单状态) 不存储在 T_ZKSH 中，而是通过 F_GDBH 关联 T_PYGD 查询
    
    # 插入记录
    with adapter.connect() as conn:
        cur = conn.cursor()
        
        # 构建INSERT语句（不包含F_CHANGES和F_OLD_DATA）
        fields = list(data.keys())
        placeholders = ", ".join(["?" for _ in fields])
        values = [data[f] for f in fields]
        
        sql = f"INSERT INTO T_ZKSH ({', '.join(fields)}) VALUES ({placeholders})"
        cur.execute(sql, tuple(values))
        
        # 获取插入的ID
        cur.execute("SELECT MAX(F_ID) FROM T_ZKSH WHERE F_ZKBH = ?", (data["F_ZKBH"],))
        result = cur.fetchone()
        new_id = result[0] if result else None
        
        # ✅ 单独UPDATE F_CHANGES和F_OLD_DATA（避免行长度超限）
        if new_id is not None:
            try:
                # 先更新F_CHANGES
                cur.execute(
                    "UPDATE T_ZKSH SET F_CHANGES = ? WHERE F_ID = ?",
                    (changes_json, new_id)
                )
                # 再更新F_OLD_DATA（如果有）
                if old_data_json is not None:
                    cur.execute(
                        "UPDATE T_ZKSH SET F_OLD_DATA = ? WHERE F_ID = ?",
                        (old_data_json, new_id)
                    )
            except Exception as e:
                import logging
                logger = logging.getLogger(__name__)
                logger.warning(f"更新质控审核大文本字段失败 (ID={new_id}): {e}")
        
        conn.commit()
    
    return new_id


def get_sample_basic_info(F_ZZID: str) -> Dict[str, Any]:
    """
    获取样本的基本信息（用于填充质控审核记录）
    
    Args:
        F_ZZID: 标本号
    
    Returns:
        样本基本信息字典
        注意：只返回 T_YBZJ 表中实际存在的字段
        - F_XM: 姓名
        - F_BRBH: 病人编号
        - F_ZZBW: 组织部位（来自 F_CJBW 采集部位字段）
        - 不包含 F_YBLY（样本来源字段在 T_PYGD 培养工单表中，而不是 T_YBZJ 表）
    """
    adapter = get_adapter()
    
    with adapter.connect() as conn:
        cur = conn.cursor()
        
        # 查询样本基本信息（只查询 T_YBZJ 表中实际存在的字段）
        # F_CJBW: 采集部位（肝、肺等）
        sql = """
            SELECT F_XM, F_BRBH, F_CJBW
            FROM T_YBZJ
            WHERE F_ZZID = ?
        """
        cur.execute(sql, (F_ZZID,))
        row = cur.fetchone()
        
        if not row:
            return {}
        
        return {
            "F_XM": row[0],
            "F_BRBH": row[1],
            "F_ZZBW": row[2],  # 返回时用 F_ZZBW 作为key，实际查询的是 F_CJBW（采集部位）
        }


def get_old_data_for_comparison(F_ZZID: str, table_name: str = "T_YBZJ") -> Optional[Dict[str, Any]]:
    """
    获取修改前的数据（用于变更对比）
    
    Args:
        F_ZZID: 标本号
        table_name: 表名（默认T_YBZJ）
    
    Returns:
        原始数据字典，如果不存在返回None
    """
    adapter = get_adapter()
    
    with adapter.connect() as conn:
        cur = conn.cursor()
        
        # 查询现有数据
        sql = f"SELECT * FROM {table_name} WHERE F_ZZID = ?"
        cur.execute(sql, (F_ZZID,))
        row = cur.fetchone()
        
        if not row:
            return None
        
        # 获取列名
        columns = [desc[0] for desc in cur.description]
        
        # 转换为字典
        return dict(zip(columns, row))

