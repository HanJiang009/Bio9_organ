"""
培养工单详情 PDF 生成模块
"""
from typing import Dict, Any, List, Optional
from datetime import datetime
from pathlib import Path
import json

from app.db.factory import get_adapter
from app.core.settings import settings
from app.crud.organoid_culture_tissue import get_culture_order_detail
from app.crud.preprocess import fetch_preprocess_by_gdid, fetch_ltsj_for_gdid
from app.crud.organoid_culture_detail import get_mx_by_gdid
from app.crud.organoid_observation import list_observations_by_gdid
from app.crud.sample_receive import query_by_ids
from app.crud.file_center import insert_file_record
from app.core.docx_pdf import render_pdf, convert_docx_to_pdf


def check_zkzt_status(gdid: int) -> tuple[bool, str]:
    """
    检查工单的质控状态
    
    Args:
        gdid: 工单ID
        
    Returns:
        (是否允许生成, 状态信息)
    """
    adapter = get_adapter()
    with adapter.connect() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT F_ZKZT FROM T_PYGD WHERE F_ID = ?",
            (gdid,)
        )
        row = cur.fetchone()
        if not row:
            return False, "工单不存在"
        
        zkzt = str(row[0]).strip() if row[0] else ""
        
        # 允许的状态
        allowed_statuses = ["待审核", "审核通过", "审核不通过"]
        
        if zkzt in allowed_statuses:
            return True, zkzt
        else:
            return False, f"工单质控状态为'{zkzt}',需要先提交质控单"


def format_observation_data(observations: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    格式化观察记录数据
    
    Args:
        observations: 原始观察记录列表
        
    Returns:
        格式化后的观察记录列表
    """
    gcjl_data = []
    
    for obs in observations:
        # 格式化观察时间
        gcsj = obs.get("F_GCSJ")
        if gcsj and hasattr(gcsj, "strftime"):
            gcsj = gcsj.strftime("%Y.%m.%d(%H:%M)")
        elif gcsj:
            gcsj = str(gcsj)
        else:
            gcsj = ""
        
        # 格式化附件
        fj_list = obs.get("F_FJ") or []
        fj_text = "有" if (isinstance(fj_list, list) and len(fj_list) > 0) else ""
        
        # 格式化是否换液
        sfhy = obs.get("F_SFHY") or ""
        if sfhy and sfhy != "否":
            sfhy = "是"
        else:
            sfhy = ""
        
        # 记录人和时间
        jlr = obs.get("F_JLR") or ""
        jlsj = obs.get("F_JLSJ")
        jlsj_formatted = ""
        if jlsj and hasattr(jlsj, "strftime"):
            jlsj_formatted = jlsj.strftime("%Y.%m.%d")
        elif jlsj:
            jlsj_formatted = str(jlsj)
        
        gcjl_data.append({
            "F_GCSJ": gcsj,
            "F_FJ": fj_text,
            "F_PYJZT": (
                ", ".join(str(x) for x in obs.get("F_PYJZT") if x)
                if isinstance(obs.get("F_PYJZT"), list)
                else (obs.get("F_PYJZT") or "完好")
            ),
            "F_XBZT": obs.get("F_XBZT") or "",
            "F_SFHY": sfhy,
            "F_HYJL": obs.get("F_HYJL") or "",
            "F_QXQL": obs.get("F_QXQL") or "",
            "F_QXBL": obs.get("F_QXBL") or "",
            "F_BZ": obs.get("F_BZ") or "无",
            "F_JLR": jlr,
            "F_JLSJ": jlsj_formatted,
        })
    
    return gcjl_data


def format_jzj_data(jzj_raw: List[Dict[str, Any]] | None) -> List[Dict[str, Any]]:
    """
    格式化基质胶数据
    
    基质胶数据是JSON嵌套在T_PYMX_ZZ.F_JZJ字段内的数组
    每个元素结构: {F_ID, F_JZJ, F_YL, F_ND}
    
    Args:
        jzj_raw: 原始基质胶列表
        
    Returns:
        格式化后的基质胶列表
    """
    if not jzj_raw:
        return []
    
    jzj_data = []
    
    # 如果是字符串,尝试解析JSON
    if isinstance(jzj_raw, str):
        try:
            jzj_raw = json.loads(jzj_raw)
        except:
            return []
    
    # 如果是单个字典,转为列表
    if isinstance(jzj_raw, dict):
        jzj_raw = [jzj_raw]
    
    # 如果不是列表,返回空
    if not isinstance(jzj_raw, list):
        return []
    
    for idx, item in enumerate(jzj_raw, start=1):
        if not isinstance(item, dict):
            continue
            
        jzj_data.append({
            "F_ID": item.get("F_ID") or idx,
            "F_JZJ": item.get("F_JZJ") or "",
            "F_YL": item.get("F_YL") or "",
            "F_ND": item.get("F_ND") or "",
        })
    
    return jzj_data


def format_xhmsy_data(xhmsy_raw: List[Dict[str, Any]] | None) -> List[Dict[str, Any]]:
    """
    格式化消化酶使用记录数据
    
    消化酶数据来自独立表T_XHMSY
    完整字段: F_XHM(消化酶), F_YL(用量), F_ND(浓度), F_XHWD(消化温度), 
             F_CZFS(操作方式), F_XHSJ(消化时间), F_BZ(备注)等
    
    Args:
        xhmsy_raw: 原始消化酶使用记录列表
        
    Returns:
        格式化后的消化酶使用记录列表
    """
    if not xhmsy_raw:
        return []
    
    xhmsy_data = []
    
    # 如果不是列表,返回空
    if not isinstance(xhmsy_raw, list):
        return []
    
    for idx, item in enumerate(xhmsy_raw, start=1):
        if not isinstance(item, dict):
            continue
            
        xhmsy_data.append({
            "F_ID": item.get("F_ID") or idx,
            "F_XHM": item.get("F_XHM") or "",
            "F_YL": item.get("F_YL") or "",
            "F_ND": item.get("F_ND") or "",
            "F_XHWD": item.get("F_XHWD") or "",
            "F_CZFS": item.get("F_CZFS") or "",
            "F_XHSJ": item.get("F_XHSJ") or "",
            "F_BZ": item.get("F_BZ") or "",
            "F_SYCS": item.get("F_SYCS") or "",
            "F_LXSL": item.get("F_LXSL") or "",
        })
    
    return xhmsy_data


def assemble_culture_detail_context(gdid: int) -> Dict[str, Any]:
    """
    组装培养工单详情的完整上下文数据
    
    Args:
        gdid: 工单ID
        
    Returns:
        包含所有字段的上下文字典
    """
    # 1. 查询工单基本信息
    gdxx = get_culture_order_detail(gdid)
    if not gdxx:
        raise ValueError(f"工单 {gdid} 不存在")
    
    # 2. 查询样本信息
    zzid = gdxx.get("F_ZZID")
    ybxx = query_by_ids(zzid, None) if zzid else None
    
    # 3. 查询预处理信息
    yclxx = fetch_preprocess_by_gdid(gdid)
    
    # 如果 T_YCL 没有记录,尝试从 T_YBZJ 获取离体时间
    if not yclxx or not yclxx.get("F_LTSJ"):
        ltsj_from_ybzj = fetch_ltsj_for_gdid(gdid)
        if not yclxx:
            yclxx = {}
        if ltsj_from_ybzj:
            yclxx["F_LTSJ"] = ltsj_from_ybzj
    
    # 格式化预处理附件
    yclxx_fj_list = yclxx.get("F_FJ") if yclxx else []
    if isinstance(yclxx_fj_list, str):
        try:
            yclxx_fj_list = json.loads(yclxx_fj_list)
        except:
            yclxx_fj_list = []
    yclxx_fj_text = "有" if (isinstance(yclxx_fj_list, list) and len(yclxx_fj_list) > 0) else ""
    
    # 4. 查询培养详情
    pymx = get_mx_by_gdid(gdid)
    
    # 格式化基质胶数据
    jzj_raw = pymx.get("pyxx_pb", {}).get("F_JZJ") if pymx else None
    jzj_data = format_jzj_data(jzj_raw)
    # 确保至少有空列表
    if not jzj_data:
        jzj_data = []
    
    # 格式化消化酶使用记录数据
    xhmsy_raw = pymx.get("XHMSYJL") if pymx else None
    xhmsy_data = format_xhmsy_data(xhmsy_raw)
    # 确保至少有空列表
    if not xhmsy_data:
        xhmsy_data = []
    
    # 5. 查询观察记录
    gcjl_raw = list_observations_by_gdid(gdid)
    gcjl_data = format_observation_data(gcjl_raw)
    # 确保至少有空列表
    if not gcjl_data:
        gcjl_data = []
    
    # 6. 格式化日期字段
    def format_datetime(dt, fmt="%Y.%m.%d %H:%M"):
        if dt and hasattr(dt, "strftime"):
            return dt.strftime(fmt)
        elif dt:
            return str(dt)
        return ""
    
    # 7. 组装上下文
    context = {
        # ===== 工单基本信息 =====
        "GDXX_F_GDBH": gdxx.get("F_GDBH") or "",
        "GDXX_F_ZZID": gdxx.get("F_ZZID") or "",
        "GDXX_F_XM": gdxx.get("F_XM") or "",
        "GDXX_F_BRBH": gdxx.get("F_BRBH") or "",
        "GDXX_F_BBLX": gdxx.get("F_BBLX") or "",
        "GDXX_F_DC": gdxx.get("F_DC") or "",
        "GDXX_F_DCID": gdxx.get("F_DCID") or "",
        "GDXX_F_ZZBW": ybxx.get("F_CJBW") if ybxx else "",  # 取样部位应该使用T_YBZJ.F_CJBW
        "GDXX_F_XYZL": gdxx.get("F_XYZL") or "",
        "GDXX_F_QYFS": gdxx.get("F_QYFS") or "",
        "GDXX_F_YBLY": gdxx.get("F_YBLY") or "",
        "GDXX_F_YBGG": gdxx.get("F_YBGG") or "",
        "GDXX_F_SQR": gdxx.get("F_SQR") or "",
        "GDXX_F_PYSJ": format_datetime(gdxx.get("F_PYSJ")),
        "GDXX_F_CJSJ": format_datetime(gdxx.get("F_CJSJ")),
        "GDXX_F_AZ": gdxx.get("F_AZ") or "",
        
        # ===== 样本信息 =====
        "YBXX_F_XB": ybxx.get("F_XB") if ybxx else "",
        "YBXX_F_NL": ybxx.get("F_NL") if ybxx else "",
        "YBXX_F_ZYH": ybxx.get("F_ZYH") if ybxx else "",
        "YBXX_F_MZH": ybxx.get("F_MZH") if ybxx else "",
        "YBXX_F_CXSJ": format_datetime(ybxx.get("F_CXSJ")) if ybxx else "",
        "YBXX_F_LTSJ": format_datetime(ybxx.get("F_LTSJ")) if ybxx else "",
        "YBXX_F_JSSJ": format_datetime(ybxx.get("F_JSSJ")) if ybxx else "",
        "YBXX_F_ZZLX": ybxx.get("F_ZZLX") if ybxx else "",
        
        # ===== 预处理信息 =====
        "YCLXX_F_LTSJ": format_datetime(yclxx.get("F_LTSJ")) if yclxx else "",
        "YCLXX_F_KSCLSJ": format_datetime(yclxx.get("F_KSCLSJ")) if yclxx else "",
        "YCLXX_F_QXYCF": yclxx.get("F_QXYCF") if yclxx else "",
        "YCLXX_F_ZZSPCC": yclxx.get("F_ZZSPCC") if yclxx else "",
        "YCLXX_F_YBYCLQK": yclxx.get("F_YBYCLQK") if yclxx else "",
        "YCLXX_F_YBZLTJ": yclxx.get("F_YBZLTJ") if yclxx else "",
        "YCLXX_F_ZLXBZB": yclxx.get("F_ZLXBZB") if yclxx else "",
        "YCLXX_F_XBHL": yclxx.get("F_XBHL") if yclxx else "",
        "YCLXX_F_LWKJ": yclxx.get("F_LWKJ") if yclxx else "",
        "YCLXX_F_TPLJRL": yclxx.get("F_TPLJRL") if yclxx else "",
        "YCLXX_F_MJFA": yclxx.get("F_MJFA") if yclxx else "",
        "YCLXX_F_ZTMJFA": yclxx.get("F_ZTMJFA") if yclxx else "",
        "YCLXX_F_FJ": yclxx_fj_text,
        
        # ===== 培养详情 =====
        "PYMX_CZR": pymx.get("operator") if pymx else "",
        "PYMX_CZSJ": format_datetime(pymx.get("created_at")) if pymx else "",
        
        # 培养基条件
        "PYMX_PYJ_WD": pymx.get("pyjtjjl", {}).get("F_PYJ_WD") if pymx else "",
        "PYMX_PYJ_SD": pymx.get("pyjtjjl", {}).get("F_PYJ_SD") if pymx else "",
        
        # 悬浮相关
        "PYMX_XH_MJFA": pymx.get("pyxx_xh", {}).get("F_MJFA") if pymx else "",
        "PYMX_XH_ZZMJFA": pymx.get("pyxx_xh", {}).get("F_ZZMJFA") if pymx else "",
        "PYMX_XH_SWKJ": pymx.get("pyxx_xh", {}).get("F_SWKJ") if pymx else "",
        "PYMX_XH_XBSL": pymx.get("pyxx_xh", {}).get("F_XBSL") if pymx else "",
        "PYMX_XH_XBHL": pymx.get("pyxx_xh", {}).get("F_XBHL") if pymx else "",
        "PYMX_XH_XBDL": pymx.get("pyxx_xh", {}).get("F_XBDL") if pymx else "",
        "PYMX_XH_ZLXBZB": pymx.get("pyxx_xh", {}).get("F_ZLXBZB") if pymx else "",
        "PYMX_XH_CLHXBHL": pymx.get("pyxx_xh", {}).get("F_CLHXBHL") if pymx else "",
        "PYMX_XH_XTGCJL": pymx.get("pyxx_xh", {}).get("F_XTGCJL") if pymx else "",
        
        # 铺板相关
        "PYMX_PB_PYBBH": pymx.get("pyxx_pb", {}).get("F_PYBBH") if pymx else "",
        "PYMX_PB_JZKBS": pymx.get("pyxx_pb", {}).get("F_JZKBS") if pymx else "",
        "PYMX_PB_DKJZTJ": pymx.get("pyxx_pb", {}).get("F_DKJZTJ") if pymx else "",
        "PYMX_PB_CJWD": pymx.get("pyxx_pb", {}).get("F_CJWD") if pymx else "",
        "PYMX_PB_CJSJ": format_datetime(pymx.get("pyxx_pb", {}).get("F_CJSJ")) if pymx else "",
        "PYMX_PB_JZKS": pymx.get("pyxx_pb", {}).get("F_JZKS") if pymx else "",
        
        # 培养相关
        "PYMX_PY_TJPYJ": pymx.get("pyxx_py", {}).get("F_TJPYJ") if pymx else "",
        "PYMX_PY_PYX_TEMP": pymx.get("pyxx_py", {}).get("F_PYX_TEMP") if pymx else "",
        "PYMX_PY_PYX_CO2": pymx.get("pyxx_py", {}).get("F_PYX_CO2") if pymx else "",
        "PYMX_PY_PYX_HUM": pymx.get("pyxx_py", {}).get("F_PYX_HUM") if pymx else "",
        "PYMX_PY_PYX_FRSJ": format_datetime(pymx.get("pyxx_py", {}).get("F_PYX_FRSJ")) if pymx else "",
        
        # 培养附件
        "PYMX_FJ": "有" if (pymx and pymx.get("F_FJ") and len(pymx.get("F_FJ", [])) > 0) else "",
        
        # 传代前信息
        "PYMX_CDQXBZS": pymx.get("cdqxx", {}).get("F_CDQXBZS") if pymx else "",
        "PYMX_CDQ_XBHL": pymx.get("cdqxx", {}).get("F_CDQ_XBHL") if pymx else "",
        "PYMX_CDQ_JZMD": pymx.get("cdqxx", {}).get("F_JZMD") if pymx else "",
        
        # ===== 基质胶信息(动态列表) =====
        "JZJXX": jzj_data,
        
        # ===== 消化酶使用记录(动态列表) =====
        "XHMSYJL": xhmsy_data,
        "XHMSYJL_SYR": pymx.get("F_CZR") if pymx else "",  # 消化酶使用人(统一字段,不参与循环)
        
        # ===== 观察记录(动态列表) =====
        "GCJL": gcjl_data,
        
        # ===== 其他信息 =====
        "REPORT_TIME": datetime.now().strftime("%Y年%m月%d日 %H:%M:%S"),
    }
    
    return context


def generate_culture_detail_pdf(gdid: int, operator: str = None) -> Dict[str, Any]:
    """
    生成培养工单详情 PDF
    
    Args:
        gdid: 工单ID
        operator: 操作人
        
    Returns:
        包含文件信息的字典
    """
    # 1. 检查质控状态
    can_generate, status_msg = check_zkzt_status(gdid)
    if not can_generate:
        raise ValueError(status_msg)
    
    # 2. 组装上下文数据
    context = assemble_culture_detail_context(gdid)
    
    # 3. 使用特殊的渲染方式 - 通过程序动态创建表格
    template_name = "pywx_detail_template.docx"
    out_basename = f"culture_detail_{context['GDXX_F_GDBH']}_{datetime.now().strftime('%Y%m%d%H%M%S')}"
    
    # 调用特殊的渲染函数
    from app.core.docx_pdf import render_culture_detail_pdf_with_tables
    pdf_path = render_culture_detail_pdf_with_tables(template_name, context, out_basename)
    
    # 4. 计算相对路径
    upload_dir_path = Path(settings.upload_dir)
    rel = str(pdf_path.relative_to(upload_dir_path)).replace("\\", "/")
    
    # 5. 登记到文件中心
    pdf_id = insert_file_record(rel, f"{out_basename}.pdf")
    
    # 6. 返回结果
    return {
        "file_id": pdf_id,
        "filename": f"{out_basename}.pdf",
        "file_path": rel,
        "file_url": f"/{rel}",
        "file_size": pdf_path.stat().st_size,
        "gdid": gdid,
        "gdbh": context["GDXX_F_GDBH"],
        "zkzt": status_msg,
        "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }

