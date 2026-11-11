"""
动物实验报告数据组装模块
整合实验计划、动物接种、观察记录、终点记录、培养工单等信息
"""
import json
import logging
from datetime import datetime
from typing import Any, Dict, Optional

from app.db.factory import get_adapter

logger = logging.getLogger(__name__)


def _fmt_dt(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d %H:%M:%S")
    if isinstance(value, str):
        return value
    return str(value)


def _fmt_date(value: Any) -> Optional[str]:
    if value is None:
        return None
    s = _fmt_dt(value)
    return s[:10] if s else None


def assemble_animal_test_report(syjh_id: int) -> Dict[str, Any]:
    """
    组装动物实验报告所需的完整数据。

    Returns 结构：
        报告类型       "动物实验报告"
        生成时间       str
        实验计划       dict   T_DWSY_JH 基本信息
        培养工单       dict | None
        动物接种信息   list   T_DWSY_JZ
        观察记录       list   T_DWSY_GCJL + T_DWSY_GCJL_ZB
        终点记录       dict | None   T_DWSY_SYJL
        快捷信息       dict   常用字段的平铺快捷访问
    """
    try:
        from app.crud.animal_test import get_animal_test_plan_detail
    except ImportError as e:
        raise ValueError(f"无法导入 animal_test 模块: {e}")

    # ========== 1. 实验计划 ==========
    plan = get_animal_test_plan_detail(syjh_id)
    if not plan:
        raise ValueError(f"未找到动物实验计划: F_ID={syjh_id}")
    logger.info(f"[动物实验报告] 组装开始，计划ID={syjh_id}，计划名称={plan.get('F_SYMC')}")

    gdid = plan.get("F_GDID")

    # ========== 2. 培养工单 ==========
    order_data = None
    if gdid:
        try:
            from app.crud.organoid_culture_tissue import get_culture_order_detail
            order_data = get_culture_order_detail(gdid)
        except Exception as e:
            logger.warning(f"[动物实验报告] 获取培养工单失败: {e}")

    # ========== 3. 动物接种信息 ==========
    jzxx = plan.get("JZXX") or []

    # ========== 4. 观察记录 ==========
    try:
        from app.crud.animal_test_record import list_observations
        gcjl_list, _ = list_observations(syjh_id)
    except Exception as e:
        logger.warning(f"[动物实验报告] 获取观察记录失败: {e}")
        gcjl_list = []

    # ========== 5. 终点记录 ==========
    try:
        from app.crud.animal_test_record import get_endpoint_record
        endpoint = get_endpoint_record(syjh_id)
    except Exception as e:
        logger.warning(f"[动物实验报告] 获取终点记录失败: {e}")
        endpoint = None

    # ========== 6. 组装 ==========
    gdxx = plan.get("GDXX") or {}

    result = {
        "报告类型": "动物实验报告",
        "生成时间": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),

        "实验计划": {
            "F_ID":      syjh_id,
            "F_SYBH":    plan.get("F_SYBH"),
            "F_SYMC":    plan.get("F_SYMC"),
            "F_SYJHYMD": plan.get("F_SYJHYMD"),
            "F_XBSL":   plan.get("F_XBSL"),
            "F_JZJND":   plan.get("F_JZJND"),
            "F_JZJTJ":   plan.get("F_JZJTJ"),
            "F_SYKSRQ":  _fmt_date(plan.get("F_SYKSRQ")),
            "F_SYR":     plan.get("F_SYR"),
            "F_ZT":      plan.get("F_ZT"),
        },

        "培养工单": {
            "F_GDBH":  gdxx.get("F_GDBH") or (order_data.get("F_GDBH")  if order_data else None),
            "F_ZZID":  gdxx.get("F_ZZID"),
            "F_XM":    gdxx.get("F_XM"),
            "F_BRBH":  gdxx.get("F_BRBH"),
            "F_DC":    gdxx.get("F_DC"),
            "F_ZZBW":  gdxx.get("F_ZZBW") or (order_data.get("F_ZZBW")  if order_data else None),
            "F_YBLY":  gdxx.get("F_YBLY"),
            "F_PYSJ":  _fmt_date(gdxx.get("F_PYSJ")),
        } if (gdxx or order_data) else None,

        "动物接种信息": [
            {
                "F_DWBH":   item.get("F_DWBH"),
                "F_XB":     item.get("F_XB"),
                "F_NL":     item.get("F_NL"),
                "F_LH":     item.get("F_LH"),
                "F_ZB":     item.get("F_ZB"),
                "F_JZRQ":   _fmt_date(item.get("F_JZRQ")),
                "F_JZXBS":  item.get("F_JZXBS"),
                "F_JZBW":   item.get("F_JZBW"),
                "F_JZCZY":  item.get("F_JZCZY"),
            }
            for item in jzxx
        ],

        "观察记录": [
            {
                "F_ID":    gcjl.get("F_ID"),
                "F_GCSJ":  gcjl.get("F_GCSJ"),
                "F_PYDS":  gcjl.get("F_PYDS"),
                "F_JLRQ":  _fmt_date(gcjl.get("F_JLRQ")),
                "F_JLR":   gcjl.get("F_JLR"),
                "ZBXX":    gcjl.get("ZBXX") or [],
            }
            for gcjl in gcjl_list
        ],

        "终点记录": {
            "F_SYZQ":    endpoint.get("F_SYZQ"),
            "F_YZZZQK":  endpoint.get("F_YZZZQK"),
            "F_SYJL":    endpoint.get("F_SYJL"),
            "F_JLR":     endpoint.get("F_JLR"),
            "F_JLRQ":    _fmt_date(endpoint.get("F_JLRQ")),
            "F_FJ":      endpoint.get("F_FJ") or [],
        } if endpoint else None,

        "快捷信息": {
            "实验名称":   plan.get("F_SYMC"),
            "实验编号":   plan.get("F_SYBH"),
            "实验人":    plan.get("F_SYR"),
            "实验开始日期": _fmt_date(plan.get("F_SYKSRQ")),
            "工单编号":   gdxx.get("F_GDBH"),
            "标本号":    gdxx.get("F_ZZID"),
            "接种动物数":  len(jzxx),
            "观察次数":   len(gcjl_list),
            "记录人":    endpoint.get("F_JLR") if endpoint else None,
            "实验周期":   endpoint.get("F_SYZQ") if endpoint else None,
        },
    }

    logger.info(
        f"[动物实验报告] 组装完成 — 接种动物={len(jzxx)}，"
        f"观察记录={len(gcjl_list)}，终点记录={'有' if endpoint else '无'}"
    )
    return result
