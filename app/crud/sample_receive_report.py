"""
样本接收质控报告数据组装模块
职责：整合样本接收查询接口数据，提供完整的接收质控报告数据（JSON格式）
"""
from typing import Dict, Any, Optional
from datetime import datetime
import json

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
            # 尝试解析日期字符串
            if "T" in dt or " " in dt:
                parsed = datetime.fromisoformat(dt.replace(" ", "T"))
            else:
                parsed = datetime.strptime(dt, "%Y-%m-%d")
            return parsed.strftime(fmt)
        except Exception:
            pass
    return str(dt) if dt else ""


def get_file_url(file_id: Any) -> str:
    """根据文件ID获取文件URL"""
    if not file_id:
        return ""
    
    try:
        file_id_int = int(file_id)
        file_info = get_file_by_id(file_id_int)
        if file_info and file_info.get("F_PATH"):
            file_path = file_info.get("F_PATH", "")
            if not file_path.startswith("/"):
                file_path = "/" + file_path
            return file_path
    except Exception as e:
        print(f"警告: 获取文件URL失败 (ID: {file_id}): {e}")
    
    return ""


def assemble_sample_receive_report(F_ZZID: str) -> Dict[str, Any]:
    """
    组装样本接收质控报告数据
    
    Args:
        F_ZZID: 标本号
        
    Returns:
        完整的报告数据结构
        
    Raises:
        ValueError: 标本不存在或数据不完整
    """
    # 1. 查询样本接收数据
    sample_data = query_by_ids(F_ZZID=F_ZZID, F_ZYH=None, F_SJTMH=None)
    if not sample_data:
        raise ValueError(f"标本 {F_ZZID} 不存在")
    
    # 2. 提取各部分数据
    jzxx = sample_data.get("JZXX", {})
    bbxx = sample_data.get("BBXX", {})
    ybzj = sample_data.get("YBZJ", {})
    
    # 3. 验证必要数据
    if not ybzj:
        raise ValueError(f"标本 {F_ZZID} 未完成接收质控")
    
    # 4. 处理实样图片
    sytp_url = ""
    sytp_id = ybzj.get("F_SYTP")
    if sytp_id:
        sytp_url = get_file_url(sytp_id)
        print(f"调试: 实样图片 - F_SYTP值: {sytp_id}, URL: {sytp_url}")
    
    # 5. 组装报告数据结构
    report_data = {
        "报告标题": "样本接收质控报告",
        "生成时间": datetime.now().strftime("%Y年%m月%d日 %H:%M:%S"),
        
        # 就诊信息
        "就诊信息": {
            "F_XM": jzxx.get("F_XM") or "",
            "F_BRBH": jzxx.get("F_BRBH") or "",
            "F_ZYH": jzxx.get("F_ZYH") or "",
            "F_MZH": jzxx.get("F_MZH") or "",
            "F_XB": jzxx.get("F_XB") or "",
            "F_NL": jzxx.get("F_NL") or "",
            "F_CSNY": jzxx.get("F_CSNY") or "",
            "F_DH": jzxx.get("F_DH") or "",
            "F_SFZH": jzxx.get("F_SFZH") or "",
            "F_DZ": jzxx.get("F_DZ") or "",
            "F_LCZD": jzxx.get("F_LCZD") or "",
            "F_BZTZ": _format_bztz(jzxx.get("F_BZTZ")),
            "F_SJTMH": jzxx.get("F_SJTMH") or "",
            "F_ZQTYS": jzxx.get("F_ZQTYS") or "",
            "F_ZQTYSLB": jzxx.get("F_ZQTYSLB") or "",
        },
        
        # 标本信息
        "标本信息": {
            "F_ZZID": bbxx.get("F_ZZID") or "",
            "F_BBLX": bbxx.get("F_BBLX") or "",
            "F_CJBW": bbxx.get("F_CJBW") or "",
            "F_QYFS": bbxx.get("F_QYFS") or "",
            "F_SSMC": bbxx.get("F_SSMC") or "",
            "F_SSRQ": format_datetime_field(bbxx.get("F_SSRQ")),
            "F_SSS": bbxx.get("F_SSS") or "",
            "F_SSYS": bbxx.get("F_SSYS") or "",
            "F_CXSJ": format_datetime_field(bbxx.get("F_CXSJ")),
            "F_LTSJ": format_datetime_field(bbxx.get("F_LTSJ")),
            "F_JSSJ": format_datetime_field(bbxx.get("F_JSSJ")),
            "F_CRB": bbxx.get("F_CRB") or "",
        },
        
        # 质控信息
        "质控信息": {
            # 实样图片
            "F_SYTP_URL": sytp_url,
            
            # 外观描述
            "F_SYMS": ybzj.get("F_SYMS") or "",
            
            # 运输条件
            "F_YSWD": ybzj.get("F_YSWD") or "",
            "F_YSSJ": ybzj.get("F_YSSJ") or "",
            "F_SFDSF": ybzj.get("F_SFDSF") or "",
            
            # 病理信息
            "F_AZ": ybzj.get("F_AZ") or "",
            "F_ABL": ybzj.get("F_ABL") or "",
            "F_KI67": ybzj.get("F_KI67") or "",
            
            # 质控结果
            "F_SFWH": ybzj.get("F_SFWH") or "",
            "F_SFJY": ybzj.get("F_SFJY") or "",
            "F_SFCQ": ybzj.get("F_SFCQ") or "",
            "F_SFQB": ybzj.get("F_SFQB") or "",
            "F_SFLY": ybzj.get("F_SFLY") or "",
            
            # 留存记录
            "F_PBMCDC": ybzj.get("F_PBMCDC") or "",
            "F_QXDC": ybzj.get("F_QXDC") or "",
            
            # 分子检测
            "F_SJJG": ybzj.get("F_SJJG") or "",
            
            # 不合格信息
            "F_BHG": ybzj.get("F_BHG") or "",
            "F_BHGYY": ybzj.get("F_BHGYY") or "",
            
            # 登记信息
            "F_DJR": ybzj.get("F_DJR") or "",
            "F_DJRQ": format_date_field(ybzj.get("F_DJRQ")),
        },
    }
    
    return report_data

