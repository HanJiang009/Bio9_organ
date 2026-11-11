"""
样本评分表报告数据组装模块
职责：整合预处理评分数据，提供完整的样本评分表报告数据（JSON格式）
"""
from typing import Dict, Any, Optional
from datetime import datetime
import json

from app.crud.preprocess import fetch_preprocess_by_gdid
from app.crud.organoid_culture_tissue import get_culture_order_detail
from app.crud.sample_receive import query_by_ids
from app.crud.file_center import get_file_by_id
from app.utils.field_coerce import parse_str_list


def _format_bztz(v) -> str:
    arr = v if isinstance(v, list) else parse_str_list(v)
    return ", ".join(str(x) for x in arr) if arr else (str(v) if v else "")


def format_datetime_field(dt, fmt="%Y.%m.%d %H:%M") -> str:
    """格式化日期时间字段"""
    if dt and hasattr(dt, "strftime"):
        return dt.strftime(fmt)
    if dt and isinstance(dt, str):
        try:
            parsed = datetime.fromisoformat(dt.replace(" ", "T"))
            return parsed.strftime(fmt)
        except Exception:
            pass
    return str(dt) if dt else ""


def format_date_field(dt, fmt="%Y.%m.%d") -> str:
    """格式化日期字段"""
    if dt and hasattr(dt, "strftime"):
        return dt.strftime(fmt)
    if dt and isinstance(dt, str):
        try:
            if "T" in dt or " " in dt:
                parsed = datetime.fromisoformat(dt.replace(" ", "T"))
            else:
                parsed = datetime.strptime(dt, "%Y-%m-%d")
            return parsed.strftime(fmt)
        except Exception:
            pass
    return str(dt) if dt else ""


def assemble_preprocess_score_report(gdid: int) -> Dict[str, Any]:
    """
    组装样本评分表报告数据
    
    Args:
        gdid: 工单ID
        
    Returns:
        完整的报告数据结构
        
    Raises:
        ValueError: 工单不存在或数据不完整
    """
    # 1. 查询工单信息
    order = get_culture_order_detail(gdid)
    if not order:
        raise ValueError(f"工单 {gdid} 不存在")
    
    zzid = order.get("F_ZZID")
    if not zzid:
        raise ValueError(f"工单 {gdid} 缺少标本号")
    
    # 2. 查询预处理信息（包含评分数据）
    preprocess = fetch_preprocess_by_gdid(gdid)
    if not preprocess:
        raise ValueError(f"工单 {gdid} 未完成预处理")
    
    # 3. 解析评分数据
    score_data = preprocess.get("F_YBPFB", {})
    if isinstance(score_data, str):
        try:
            score_data = json.loads(score_data)
        except Exception:
            score_data = {}
    
    # 4. 查询样本接收数据（用于获取患者和标本信息）
    sample_data = query_by_ids(F_ZZID=zzid, F_ZYH=None, F_SJTMH=None)
    if not sample_data:
        sample_data = {"JZXX": {}, "BBXX": {}, "YBZJ": {}}
    
    jzxx = sample_data.get("JZXX", {})
    bbxx = sample_data.get("BBXX", {})
    
    # 5. 获取评分模板信息
    template_name = score_data.get("name", "样本情况质量控制表")
    template_code = score_data.get("template_code", "default")
    template_version = score_data.get("version", "1.0.0")
    total_score = score_data.get("total", 0)
    calculated_at = score_data.get("calculated_at", "")
    operator = score_data.get("operator", "")
    
    # 6. 组装评分详情列表（details字段包含完整的评分表信息）
    details = score_data.get("details", [])
    score_details = []
    for detail in details:
        score_details.append({
            "key": detail.get("key", ""),
            "label": detail.get("label", ""),
            "value": detail.get("value", ""),
            "score": detail.get("score", 0),
            "options": detail.get("options", [])  # 包含所有选项及对应分值
        })
    
    # 7. 组装报告数据结构
    report_data = {
        "报告标题": template_name,
        "生成时间": datetime.now().strftime("%Y年%m月%d日 %H:%M:%S"),
        
        # 工单信息
        "工单信息": {
            "F_GDID": gdid,
            "F_GDBH": order.get("F_GDBH") or "",
            "F_ZZID": zzid or "",
            "F_MXLX": order.get("F_MXLX") or "",
        },
        
        # 患者信息
        "患者信息": {
            "F_XM": jzxx.get("F_XM") or "",
            "F_BRBH": jzxx.get("F_BRBH") or "",
            "F_XB": jzxx.get("F_XB") or "",
            "F_NL": jzxx.get("F_NL") or "",
            "F_LCZD": jzxx.get("F_LCZD") or "",
            "F_BZTZ": _format_bztz(jzxx.get("F_BZTZ")),
        },
        
        # 标本信息
        "标本信息": {
            "F_BBLX": bbxx.get("F_BBLX") or "",
            "F_CJBW": bbxx.get("F_CJBW") or "",
            "F_QYFS": bbxx.get("F_QYFS") or "",
            "F_LTSJ": format_datetime_field(bbxx.get("F_LTSJ")),
            "F_JSSJ": format_datetime_field(bbxx.get("F_JSSJ")),
        },
        
        # 预处理信息
        "预处理信息": {
            "F_KSCLSJ": format_datetime_field(preprocess.get("F_KSCLSJ")),
            "F_QXYCF": preprocess.get("F_QXYCF") or "",
            "F_ZZSPCC": preprocess.get("F_ZZSPCC") or "",
            "F_YBYCLQK": preprocess.get("F_YBYCLQK") or "",
            "F_YBZLTJ": preprocess.get("F_YBZLTJ") or "",
            "F_ZLXBZB": preprocess.get("F_ZLXBZB") or "",
            "F_XBHL": preprocess.get("F_XBHL") or "",
        },
        
        # 评分信息
        "评分信息": {
            "template_code": template_code,
            "template_name": template_name,
            "template_version": template_version,
            "total_score": total_score,
            "calculated_at": calculated_at,
            "operator": operator,
            "details": score_details,  # 评分详情列表（包含所有选项）
        },
    }
    
    return report_data

