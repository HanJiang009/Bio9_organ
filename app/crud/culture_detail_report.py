"""
类器官培养工单详情报告数据组装模块
职责：整合多个详情接口的数据，提供完整的工单报告数据（JSON格式）

与干细胞系统差异：
- 无复核步骤
- 部分字段命名不同
- 有基质胶、消化酶使用记录等特有内容
"""
from typing import Dict, Any, List, Optional
from datetime import datetime
import json

from app.crud.organoid_culture_tissue import get_culture_order_detail
from app.crud.preprocess import fetch_preprocess_by_gdid, fetch_ltsj_for_gdid
from app.crud.organoid_culture_detail import get_mx_by_gdid
from app.crud.organoid_observation import list_observations_by_gdid
from app.crud.organoid_recycle import get_recycle_by_gdid
from app.crud.sample_receive import query_by_ids
from app.crud.file_center import get_file_by_id
from app.db.factory import get_adapter


def check_report_conditions(gdid: int, *, allow_unqualified: bool = False) -> tuple[bool, str]:
    """
    检查工单是否满足生成报告的条件

    Args:
        gdid: 工单ID
        allow_unqualified: 培养详情查看时，培养或观察阶段已标为不合格、
            尚未提交验证单的工单也允许生成。放行检测报告不放开。

    Returns:
        (是否允许生成, 状态信息)
    """
    adapter = get_adapter()
    with adapter.connect() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT F_ZKZT, F_GDZT FROM T_PYGD WHERE F_ID = ?",
            (gdid,)
        )
        row = cur.fetchone()
        if not row:
            return False, "工单不存在"
        
        zkzt = str(row[0]).strip() if row[0] else ""
        gdzt = str(row[1]).strip() if row[1] else ""
        
        # 允许的质控状态
        allowed_zkzt = ["待审核", "审核通过", "审核不通过"]
        
        if zkzt in allowed_zkzt:
            return True, f"质控状态: {zkzt}, 工单状态: {gdzt}"

        # 培养/观察阶段标不合格时还没有验证单，质控状态为空，查看详情仍应出 PDF
        if allow_unqualified and gdzt in ("不合格", "不合格审批中"):
            return True, f"工单状态: {gdzt}，质控状态: {zkzt or '未提交'}"

        return False, f"工单质控状态为'{zkzt}'，需要先提交质控单"


def format_datetime_field(dt, fmt="%Y.%m.%d %H:%M") -> str:
    """格式化日期时间字段"""
    if dt and hasattr(dt, "strftime"):
        return dt.strftime(fmt)
    elif dt:
        return str(dt)
    return ""


def format_date_field(dt, fmt="%Y.%m.%d") -> str:
    """格式化日期字段"""
    if dt and hasattr(dt, "strftime"):
        return dt.strftime(fmt)
    elif dt:
        return str(dt)
    return ""


def get_file_url(file_id: Any) -> str:
    """
    根据文件ID获取文件URL路径
    
    Args:
        file_id: 文件ID或文件ID字符串
        
    Returns:
        文件URL路径，如 "/uploads/xxx.jpg"
    """
    if not file_id:
        return ""
    
    try:
        fid = int(file_id)
        file_info = get_file_by_id(fid)
        if file_info and file_info.get("F_PATH"):
            path = file_info.get("F_PATH")
            # 确保路径以/开头
            if not str(path).startswith('/'):
                return f"/{path}"
            return str(path)
    except:
        pass
    
    return ""


def format_observation_records(gcjl_raw: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    格式化观察记录数据（包含附件图片）
    
    Args:
        gcjl_raw: 原始观察记录列表
        
    Returns:
        格式化后的观察记录列表
    """
    gcjl_data = []
    
    for obs in gcjl_raw:
        # 格式化观察时间
        gcsj = obs.get("F_GCSJ")
        if gcsj and hasattr(gcsj, "strftime"):
            gcsj_fmt = gcsj.strftime("%Y.%m.%d %H:%M")
        elif gcsj:
            gcsj_fmt = str(gcsj)
        else:
            gcsj_fmt = ""
        
        # 格式化记录日期
        jlrq = obs.get("F_JLSJ")
        if jlrq and hasattr(jlrq, "strftime"):
            jlrq_fmt = jlrq.strftime("%Y.%m.%d")
        elif jlrq:
            jlrq_fmt = str(jlrq)
        else:
            jlrq_fmt = ""
        
        # 处理附件（F_FJ）
        fj_field = obs.get("F_FJ")
        fj_urls = []
        fj_text = ""
        
        if fj_field:
            # 如果是字符串，尝试解析JSON
            if isinstance(fj_field, str):
                try:
                    fj_list = json.loads(fj_field)
                except:
                    fj_list = []
            elif isinstance(fj_field, list):
                fj_list = fj_field
            else:
                fj_list = []
            
            # 提取文件ID并获取URL
            if isinstance(fj_list, list) and len(fj_list) > 0:
                for fj_item in fj_list:
                    if isinstance(fj_item, dict):
                        file_id = fj_item.get("F_WJ_ID")
                        if file_id:
                            url = get_file_url(file_id)
                            if url:
                                fj_urls.append(url)
                
                fj_text = "有" if len(fj_urls) > 0 else ""
        
        # 格式化是否换液
        sfhy = obs.get("F_SFHY") or ""
        if sfhy and sfhy != "否":
            sfhy = "是"
        else:
            sfhy = ""

        pyjzt = obs.get("F_PYJZT")
        if isinstance(pyjzt, list):
            pyjzt_fmt = ", ".join(str(x) for x in pyjzt if x) or "完好"
        else:
            pyjzt_fmt = pyjzt or "完好"
        
        gcjl_data.append({
            "T_GCJL.F_GCSJ": gcsj,
            "T_GCJL.F_GCSJ_FMT": gcsj_fmt,
            "T_GCJL.F_PYTS": obs.get("F_PYTS") or "",
            "T_GCJL.F_PYJZT": pyjzt_fmt,
            "T_GCJL.F_SFWR": obs.get("F_SFWR") or "",
            "T_GCJL.F_SFJY": obs.get("F_SFJY") or "",
            "T_GCJL.F_PJZJ": obs.get("F_PJZJ") or "",
            "T_GCJL.F_XBSL": obs.get("F_XBSL") or "",
            "T_GCJL.F_SFHX": obs.get("F_SFHX") or "0",
            "T_GCJL.F_SFSK": obs.get("F_SFSK") or "0",
            "T_GCJL.F_SFHY": sfhy,
            "T_GCJL.F_HYJL": obs.get("F_HYJL") or "",
            "T_GCJL.F_SQXQL": obs.get("F_SQXQL") or "",
            "T_GCJL.F_SQBCL": obs.get("F_SQBCL") or "",
            "T_GCJL.F_FJ": fj_urls,  # URL列表
            "T_GCJL.F_FJ_TEXT": fj_text,  # 文本显示
            "T_GCJL.F_BZ": obs.get("F_BZ") or "无",
            "T_GCJL.F_JLRQ": jlrq,
            "T_GCJL.F_JLRQ_FMT": jlrq_fmt,
            "T_GCJL.F_JLR": obs.get("F_JLR") or "",
            "T_GCJL.F_SBID": obs.get("F_SBID") or "",
        })
    
    return gcjl_data


def format_jzj_data(jzj_raw: Any) -> List[Dict[str, Any]]:
    """
    格式化基质胶数据
    
    基质胶数据是JSON嵌套在T_PYMX_ZZ.F_JZJ字段内的数组
    每个元素结构: {F_ID, F_JZJ, F_YL, F_ND}
    
    Args:
        jzj_raw: 原始基质胶数据
        
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


def format_xhmsy_data(xhmsy_raw: Any) -> List[Dict[str, Any]]:
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


def assemble_culture_detail_report(gdid: int, *, allow_unqualified: bool = False) -> Dict[str, Any]:
    """
    组装培养工单详情的完整报告数据（嵌套JSON结构）
    
    Args:
        gdid: 工单ID
        allow_unqualified: 不合格且尚未提交验证单时仍组装培养详情
        
    Returns:
        包含所有字段的报告数据字典（嵌套结构）
        
    Raises:
        ValueError: 工单不存在或状态不符合
    """
    # 0. 检查状态
    can_generate, status_msg = check_report_conditions(gdid, allow_unqualified=allow_unqualified)
    if not can_generate:
        raise ValueError(status_msg)
    
    # 1. 查询工单基本信息
    gdxx = get_culture_order_detail(gdid)
    if not gdxx:
        raise ValueError(f"工单 {gdid} 不存在")
    
    # 2. 查询样本信息
    zzid = gdxx.get("F_ZZID")
    ybxx_raw = query_by_ids(zzid, None) if zzid else None
    ybxx = ybxx_raw.get("BBXX") if ybxx_raw else {}
    
    # 3. 查询预处理信息
    yclxx = fetch_preprocess_by_gdid(gdid)
    if not yclxx:
        yclxx = {}
    
    # 如果 T_YCL 没有记录,尝试从 T_YBZJ 获取离体时间
    if not yclxx.get("F_LTSJ"):
        ltsj_from_ybzj = fetch_ltsj_for_gdid(gdid)
        if ltsj_from_ybzj:
            yclxx["F_LTSJ"] = ltsj_from_ybzj
    
    # 格式化预处理附件URL
    # 注意：fetch_preprocess_by_gdid 实际产出的是 F_YBYCL_FILES（文件详情列表，含 path/name/id），
    # 而不是 F_YBYCL_URL 或 F_FJ —— 之前这里一直读取两个不存在的字段，导致预处理图片URL永远为空，
    # 报告里的样本预处理图片占位符也就始终无法被替换。
    yclxx_fj_url = ""
    ybycl_files = yclxx.get("F_YBYCL_FILES") or []
    if isinstance(ybycl_files, list) and ybycl_files:
        first_file = ybycl_files[0] or {}
        path = first_file.get("path")
        if path:
            yclxx_fj_url = str(path)
    
    # 兜底：如果 F_YBYCL_FILES 未取到（如老数据/异常），直接从原始 F_YBYCL 字段解析文件ID获取URL
    if not yclxx_fj_url:
        raw_ybycl = yclxx.get("F_YBYCL")
        file_id = None
        if isinstance(raw_ybycl, str):
            try:
                parsed = json.loads(raw_ybycl)
                if isinstance(parsed, list) and parsed:
                    file_id = parsed[0]
                elif parsed:
                    file_id = parsed
            except Exception:
                file_id = raw_ybycl
        elif isinstance(raw_ybycl, list) and raw_ybycl:
            file_id = raw_ybycl[0]
        elif raw_ybycl:
            file_id = raw_ybycl
        if file_id:
            yclxx_fj_url = get_file_url(file_id)
    
    # 确保URL以/开头
    if yclxx_fj_url and not yclxx_fj_url.startswith('/'):
        yclxx_fj_url = f"/{yclxx_fj_url}"
    
    print(f"调试: 预处理图片最终URL: {yclxx_fj_url}")
    
    # 4. 查询培养详情
    pymx = get_mx_by_gdid(gdid)
    if not pymx:
        pymx = {}
    
    # 格式化基质胶数据
    jzj_raw = pymx.get("PYXX_PB", {}).get("F_JZJ") if pymx else None
    jzj_data = format_jzj_data(jzj_raw)
    
    # 格式化消化酶使用记录数据
    xhmsy_raw = pymx.get("XHMSYJL") if pymx else None
    xhmsy_data = format_xhmsy_data(xhmsy_raw)
    
    # 格式化培养明细附件（文件名）
    pymx_fj_raw = pymx.get("F_FJ") if pymx else None
    pymx_fj_list = []
    if isinstance(pymx_fj_raw, list):
        for fj in pymx_fj_raw:
            if isinstance(fj, dict):
                pymx_fj_list.append({
                    "F_ID": fj.get("F_ID"),
                    "F_NAME": fj.get("F_NAME") or "",
                })
    
    # 5. 查询观察记录
    gcjl_raw = list_observations_by_gdid(gdid)
    gcjl_data = format_observation_records(gcjl_raw)
    
    # 6. 查询回收记录
    hsjl = get_recycle_by_gdid(gdid)
    hsjl_data = {}
    if hsjl:
        hsjl_data = {
            "F_HSSJ": format_datetime_field(hsjl.get("F_HSSJ")),
            "F_JSFF": hsjl.get("F_JSFF") or "",
            "F_HSSL": hsjl.get("F_HSSL") or "",
            "F_XBZT": hsjl.get("F_XBZT") or "",
        }
    
    # 7. 组装嵌套JSON结构
    report_data = {
        "meta": {
            "gdid": gdid,
            "gdbh": gdxx.get("F_GDBH") or "",
            "zkzt": gdxx.get("F_ZKZT") or "",
            "gdzt": gdxx.get("F_GDZT") or "",
            "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        },
        "工单基本信息": {
            "F_GDBH": gdxx.get("F_GDBH") or "",
            "F_ZZID": gdxx.get("F_ZZID") or "",
            "F_XM": gdxx.get("F_XM") or "",
            "F_BRBH": gdxx.get("F_BRBH") or "",
            "F_BBLX": gdxx.get("F_BBLX") or "",
            "F_DC": gdxx.get("F_DC") or "",
            "F_DCID": gdxx.get("F_DCID") or "",
            "F_ZZBW": ybxx.get("F_CJBW") or "",  # 从样本信息获取
            "F_XYZL": gdxx.get("F_XYZL") or "",
            "F_QYFS": gdxx.get("F_QYFS") or "",
            "F_YBLY": gdxx.get("F_YBLY") or "",
            "F_YBGG": gdxx.get("F_YBGG") or "",
            "F_CDQXBZS": gdxx.get("F_CDQXBZS") or "",
            "F_FSQXBZS": gdxx.get("F_FSQXBZS") or "",
            "F_SQR": gdxx.get("F_SQR") or "",
            "F_PYSJ": format_datetime_field(gdxx.get("F_PYSJ")),
            "F_CJSJ": format_datetime_field(gdxx.get("F_CJSJ")),
            "F_AZ": gdxx.get("F_AZ") or "",
        },
        "样本信息": {
            "F_XB": ybxx.get("F_XB") or "",
            "F_NL": ybxx.get("F_NL") or "",
            "F_ZYH": ybxx.get("F_ZYH") or "",
            "F_MZH": ybxx.get("F_MZH") or "",
            "F_CXSJ": format_datetime_field(ybxx.get("F_CXSJ")),
            "F_LTSJ": format_datetime_field(ybxx.get("F_LTSJ")),
            "F_JSSJ": format_datetime_field(ybxx.get("F_JSSJ")),
            "F_ZZLX": ybxx.get("F_ZZLX") or "",
        },
        "预处理信息": {
            "T_YCL.F_LTSJ": format_datetime_field(yclxx.get("F_LTSJ")),
            "T_YCL.F_KSCLSJ": format_datetime_field(yclxx.get("F_KSCLSJ")),
            "T_YCL.F_QXYCF": yclxx.get("F_QXYCF") or "",
            "T_YCL.F_ZZSPCC": yclxx.get("F_ZZSPCC") or "",
            "T_YCL.F_YBYCLQK": yclxx.get("F_YBYCLQK") or "",
            "T_YCL.F_YBZLTJ": yclxx.get("F_YBZLTJ") or "",
            "T_YCL.F_ZLXBZB": yclxx.get("F_ZLXBZB") or "",
            "T_YCL.F_XBHL": yclxx.get("F_XBHL") or "",
            "T_YCL.F_LWKJ": yclxx.get("F_LWKJ") or "",
            "T_YCL.F_TPLJRL": yclxx.get("F_TPLJRL") or "",
            "T_YCL.F_MJFA": yclxx.get("F_MJFA") or "",
            "T_YCL.F_ZTMJFA": yclxx.get("F_ZTMJFA") or "",
            "T_YCL.F_YBYCL": yclxx_fj_url,  # 预处理图片URL
        },
        "培养详情": {
            "操作信息": {
                "操作人": pymx.get("F_CZR") or "",
                "操作时间": format_datetime_field(pymx.get("F_CZSJ")),
            },
            "培养基条件记录": {
                "F_PYJ_WD": pymx.get("PYJTJJL", {}).get("F_PYJ_WD") or "",
                "F_PYJ_SD": pymx.get("PYJTJJL", {}).get("F_PYJ_SD") or "",
            },
            "消化信息": {
                # 培养明细自身的清洗液成分（仅"传代-培养"/"复苏-培养"阶段有此字段）为空时，
                # 回退使用预处理阶段（T_YCL.F_QXYCF）的清洗液成分，避免"原代-培养"等阶段
                # 报告中该字段始终空白
                "T_PYMX_ZZ.F_QXYCF": pymx.get("F_QXYCF") or yclxx.get("F_QXYCF") or "",
                "F_MJFA": pymx.get("PYXX_XH", {}).get("F_MJFA") or "",
                "F_ZZMJFA": pymx.get("PYXX_XH", {}).get("F_ZZMJFA") or "",
                "F_SWKJ": pymx.get("PYXX_XH", {}).get("F_SWKJ") or "",
                "F_XBSL": pymx.get("PYXX_XH", {}).get("F_XBSL") or "",
                "F_XBHL": pymx.get("PYXX_XH", {}).get("F_XBHL") or "",
                "F_XBDL": pymx.get("PYXX_XH", {}).get("F_XBDL") or "",
                "F_ZLXBZB": pymx.get("PYXX_XH", {}).get("F_ZLXBZB") or "",
                "F_CLHXBHL": pymx.get("PYXX_XH", {}).get("F_CLHXBHL") or "",
                "F_XTGCJL": pymx.get("PYXX_XH", {}).get("F_XTGCJL") or "",
            },
            "铺板信息": {
                "F_PYBBH": pymx.get("PYXX_PB", {}).get("F_PYBBH") or "",
                "F_JZKBS": pymx.get("PYXX_PB", {}).get("F_JZKBS") or "",
                "F_DKJZTJ": pymx.get("PYXX_PB", {}).get("F_DKJZTJ") or "",
                "F_CJWD": pymx.get("PYXX_PB", {}).get("F_CJWD") or "",
                "F_CJSJ": format_datetime_field(pymx.get("PYXX_PB", {}).get("F_CJSJ")),
                "F_JZKS": pymx.get("PYXX_PB", {}).get("F_JZKS") or "",
            },
            "培养箱信息": {
                "F_TJPYJ": pymx.get("PYXX_PY", {}).get("F_TJPYJ") or "",
                "F_PYX_TEMP": pymx.get("PYXX_PY", {}).get("F_PYX_TEMP") or "",
                "F_PYX_CO2": pymx.get("PYXX_PY", {}).get("F_PYX_CO2") or "",
                "F_PYX_HUM": pymx.get("PYXX_PY", {}).get("F_PYX_HUM") or "",
                "F_PYX_FRSJ": format_datetime_field(pymx.get("PYXX_PY", {}).get("F_PYX_FRSJ")),
            },
            "传代前信息": {
                "F_CDQXBZS": pymx.get("CDQXX", {}).get("F_CDQXBZS") or "",
                "F_CDQ_XBHL": pymx.get("CDQXX", {}).get("F_CDQ_XBHL") or "",
                "F_JZMD": pymx.get("CDQXX", {}).get("F_JZMD") or "",
            },
            "基质胶信息": jzj_data,
            "消化酶使用记录": xhmsy_data,
            "附件": pymx_fj_list,
        },
        "观察记录": gcjl_data,
        "回收记录": hsjl_data,
    }
    
    return report_data

