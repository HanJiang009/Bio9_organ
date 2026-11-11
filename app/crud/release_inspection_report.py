"""
类器官放行检测报告数据整合模块

职责：整合培养详情和质控记录数据，提供完整的放行检测报告数据

数据来源：
- 培养详情数据：assemble_culture_detail_report (包含工单、患者、标本、预处理、培养、观察、回收)
- 质控记录数据：get_qc_record_by_gdid (包含质控信息和质控图片)

特点：数据结构拼接，不修改现有结构
"""
from typing import Dict, Any
from datetime import datetime

from app.crud.culture_detail_report import assemble_culture_detail_report
from app.crud.qc_record import get_qc_record_by_gdid
from app.crud.file_center import get_file_url
import json


def _resolve_first_file_url(file_ids_raw: Any) -> str:
    """将文件 ID（单值 / JSON 字符串 / 列表）解析为首个可访问 URL。"""
    if not file_ids_raw:
        return ""
    if isinstance(file_ids_raw, str):
        s = file_ids_raw.strip()
        if s.startswith("[") or s.startswith("{"):
            try:
                file_ids_raw = json.loads(s)
            except Exception:
                return get_file_url(s)
        else:
            return get_file_url(s)
    if isinstance(file_ids_raw, int):
        return get_file_url(file_ids_raw)
    if isinstance(file_ids_raw, list):
        for item in file_ids_raw:
            if isinstance(item, int):
                url = get_file_url(item)
                if url:
                    return url
            elif isinstance(item, dict):
                path = item.get("url") or item.get("path") or item.get("F_PATH")
                if path:
                    return str(path) if str(path).startswith("/") else f"/{path}"
    return ""


def format_zkjl_data(zkjl_raw: Dict[str, Any]) -> Dict[str, Any]:
    """
    格式化质控记录数据
    
    Args:
        zkjl_raw: 原始质控记录数据
        
    Returns:
        格式化后的质控记录数据
    """
    if not zkjl_raw:
        return {}
    
    # 基础字段
    zkjl_formatted = {
        "F_ID": zkjl_raw.get("F_ID"),
        "F_GDID": zkjl_raw.get("F_GDID"),
        "F_QKMS": zkjl_raw.get("F_QKMS") or "",
        "F_PYTS": zkjl_raw.get("F_PYTS"),
        "F_XT": zkjl_raw.get("F_XT") or "",
        "F_SFWR": zkjl_raw.get("F_SFWR") or "",
        "F_XBSL": zkjl_raw.get("F_XBSL") or "",
        "F_XBHL": zkjl_raw.get("F_XBHL"),
        "F_ZYT": zkjl_raw.get("F_ZYT") or "",
        "F_JLR": zkjl_raw.get("F_JLR") or "",
        "F_JLSJ": str(zkjl_raw.get("F_JLSJ")) if zkjl_raw.get("F_JLSJ") else "",
    }
    
    # 转换图片URL（如果还没有URL字段）
    for img_field in ["F_IMG_D1M4", "F_IMG_D1M20", "F_IMG_D3M4", 
                      "F_IMG_D3M20", "F_IMG_D7M4", "F_IMG_D7M20"]:
        # 检查是否已有URL字段
        url_field = f"{img_field}_URL"
        if url_field in zkjl_raw and zkjl_raw[url_field]:
            zkjl_formatted[url_field] = zkjl_raw[url_field]
        else:
            # 如果没有URL字段，从文件ID转换（支持单 ID 或 ID 列表）
            file_id = zkjl_raw.get(img_field)
            zkjl_formatted[url_field] = _resolve_first_file_url(file_id)
    
    return zkjl_formatted


def assemble_release_inspection_report(gdid: int) -> Dict[str, Any]:
    """
    整合放行检测报告数据
    
    原理：培养详情数据 + 质控记录数据
    特点：数据结构拼接，不修改现有结构
    
    Args:
        gdid: 工单ID
        
    Returns:
        完整的放行检测报告数据
        
    Raises:
        ValueError: 如果工单不存在或质控状态不符合
    """
    # 1. 获取培养详情数据（包含工单、患者、标本、预处理、培养明细、观察记录、回收记录）
    # 这个函数会自动检查质控状态，如果不符合会抛出 ValueError
    culture_data = assemble_culture_detail_report(gdid)
    
    # 2. 获取质控记录数据
    zkjl_raw = get_qc_record_by_gdid(gdid)
    
    # 3. 格式化质控记录
    zkjl_formatted = format_zkjl_data(zkjl_raw) if zkjl_raw else {}
    
    # 4. 拼接数据（在顶层添加"质控记录"键）
    report_data = {
        **culture_data,  # 保留原有所有数据: 工单信息、患者信息、标本信息、预处理信息、培养详情、观察记录、回收记录
        "质控记录": zkjl_formatted  # 新增质控记录
    }
    
    # 5. 修改报告标题
    report_data["报告标题"] = "类器官放行检测报告"
    
    return report_data

