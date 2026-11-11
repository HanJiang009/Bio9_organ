"""
pipeline_init.py
流水线模板初始化 —— 根据工单已关联的 JM/LX 流水线模板节点，自动创建各业务节点的初始记录。

主入口：apply_pipeline_to_order(gdid, operator)
  - 若工单已有业务数据则跳过（幂等保护）
  - JM（建模）流水线：预处理、培养、观察、验证
  - LX（流向）流水线：动物/药物实验计划+记录、代次管理、冻存
  - 任一节点失败 → 清理已创建的数据，抛出 ValueError 并注明失败节点

辅助：check_order_has_data(gdid) → (bool, str)
"""
from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 跳过初始化的节点类型（附件节点、下游检查点）
# ---------------------------------------------------------------------------
_SKIP_NODE_TYPES = {"病理", "测序", "其他", "入库"}

# ---------------------------------------------------------------------------
# 各业务表 —— 用于幂等检查
# ---------------------------------------------------------------------------
_EXISTENCE_QUERIES: List[Tuple[str, str]] = [
    ("T_YCL",      "预处理"),
    ("T_PYMX_ZZ",  "培养明细"),
    ("T_GCJL",     "观察记录"),
    ("T_ZKJL",     "验证记录"),
    ("T_DWSY_JH",  "动物实验计划"),
    ("T_SYJH",     "药物实验计划"),
    ("T_DCJL",     "冻存记录"),
]


# ===========================================================================
# 工具函数
# ===========================================================================

def _extract_preset_data(raw_preset: Any) -> Any:
    """
    解析 F_PRESET JSON 字符串，返回实际业务数据。
    - 新格式：{id, x, y, connections, data: {...}} → 返回 data 部分
    - 旧格式：直接是 fields dict → 原样返回
    """
    if raw_preset is None:
        return {}
    if isinstance(raw_preset, dict):
        preset = raw_preset
    else:
        try:
            preset = json.loads(str(raw_preset))
        except Exception:
            return {}
    # 新格式：有 data 字段
    if "data" in preset and isinstance(preset["data"], (dict, list)):
        return preset["data"]
    # 旧格式：直接是业务字段 dict
    return preset


def _clean_empty(val: Any) -> Any:
    """空字符串 → None，方便 CRUD 函数中的 None 检查。"""
    if isinstance(val, str) and val.strip() == "":
        return None
    return val


def _clean_num(val: Any) -> Optional[float]:
    if val is None:
        return None
    s = str(val).strip()
    if not s:
        return None
    try:
        return float(s)
    except Exception:
        return None


def _clean_str(val: Any) -> Optional[str]:
    if val is None:
        return None
    s = str(val).strip()
    return s or None


# ===========================================================================
# 幂等检查
# ===========================================================================

def check_order_has_data(gdid: int) -> Tuple[bool, str]:
    """
    检查工单是否已有任何业务数据。
    返回 (has_data: bool, description: str)。
    """
    from app.db.factory import get_adapter
    adapter = get_adapter()
    try:
        with adapter.connect() as conn:
            cur = conn.cursor()
            for table, label in _EXISTENCE_QUERIES:
                try:
                    cur.execute(f"SELECT COUNT(1) FROM {table} WHERE F_GDID = ?", (gdid,))
                    row = cur.fetchone()
                    if row and int(row[0]) > 0:
                        return True, f"{label}（{table}）"
                except Exception:
                    pass  # 表不存在或查询失败则跳过
    except Exception as e:
        logger.warning(f"[pipeline_init] 检查业务数据存在性时出错（gdid={gdid}）: {e}")
    return False, ""


# ===========================================================================
# preset → create_mx 参数转换
# ===========================================================================

def _preset_to_create_mx_args(jd_lx: str, data: Dict[str, Any]) -> Dict[str, Any]:
    """
    将 preset data 字段转为 create_mx / save_mx 的参数。
    兼容旧格式（PYJTJJL/PYXX_XH 等键）和新格式（扁平字段 + digestionTableConfig）。
    """
    # 旧格式：顶层有 PYJTJJL 等结构化键
    if "PYJTJJL" in data or "PYXX_XH" in data:
        return {
            "pyjtjjl":    data.get("PYJTJJL"),
            "pyxx_xh":    data.get("PYXX_XH"),
            "xhmsy_list": data.get("XHMSYJL"),
            "pyxx_pb":    data.get("PYXX_PB"),
            "pyxx_py":    data.get("PYXX_PY"),
            "cdqxx":      data.get("CDQXX"),
            "qxycf":      data.get("QXYCF"),
        }

    # 新格式：扁平字段
    pyjtjjl = {
        "F_PYJ_WD": _clean_num(data.get("F_PYJ_WD")),
        "F_PYJ_SD": _clean_num(data.get("F_PYJ_SD")),
    }

    pyxx_xh = {
        "F_MJFA":     _clean_str(data.get("F_MJFA")),
        "F_ZZMJFA":   _clean_str(data.get("F_ZZMJFA")),
        "F_ZLXBZB":   _clean_str(data.get("F_ZLXBZB")),
        "F_CLHXBHL":  _clean_str(data.get("F_CLHXBHL")),
        "F_SWKJ":     _clean_str(data.get("F_SWKJ")),
        "F_XBSL":     _clean_num(data.get("F_XBSL")),
        "F_XBHL":     _clean_num(data.get("F_XBHL")),
        # 形态观察记录：此前遗漏未从 preset 提取，导致通过流水线模板创建的培养节点
        # 该字段始终为空（即使 create_mx/save_mx 及放行报告都已支持该字段）
        "F_XTGCJL":   _clean_str(data.get("F_XTGCJL")),
    }

    # digestionTableConfig → xhmsy_list
    dig_cfg = data.get("digestionTableConfig") or []
    xhmsy_list = [
        {
            "F_XHM":  item.get("F_XHM"),
            "F_YL":   _clean_num(item.get("F_YL")),
            "F_ND":   _clean_str(item.get("F_ND")),
            "F_XHWD": _clean_num(item.get("F_XHWD")),
            "F_CZFS": item.get("F_CZFS"),
            "F_LXSL": _clean_num(item.get("F_LXSL")),
            "F_XHSJ": _clean_num(item.get("F_XHSJ")),
            "F_BZ":   item.get("F_BZ"),
            "F_SYCS": item.get("F_SYCS"),
        }
        for item in dig_cfg
    ] or None

    # baseGelTableConfig → pyxx_pb.F_JZJ
    gel_cfg = data.get("baseGelTableConfig") or []
    pyxx_pb = {
        "F_JZJ": [
            {"F_JZJ": g.get("F_JZJ"), "F_YL": _clean_num(g.get("F_YL")), "F_ND": _clean_str(g.get("F_ND"))}
            for g in gel_cfg
        ] or None,
        "F_PYBBH":  _clean_str(data.get("F_PYBBH")),
        "F_JZKBS":  _clean_num(data.get("F_JZKBS")),
        "F_DKJZTJ": _clean_num(data.get("F_DKJZTJ")),
        "F_CJWD":   _clean_num(data.get("F_CJWD")),
        "F_CJSJ":   _clean_num(data.get("F_CJSJ")),
    }

    # 培养箱条件：优先 cultureFormData，其次 incubatorConditions
    culture_form = data.get("cultureFormData") or {}
    incubator    = data.get("incubatorConditions") or {}
    pyxx_py = {
        "F_PYX_TEMP": _clean_num(culture_form.get("F_PYX_TEMP") or incubator.get("F_PYX_TEMP")),
        "F_PYX_CO2":  _clean_num(culture_form.get("F_PYX_CO2")  or incubator.get("F_PYX_CO2")),
        "F_PYX_HUM":  _clean_num(culture_form.get("F_PYX_HUM")  or incubator.get("F_PYX_HUM")),
        "F_TJPYJ":    _clean_num(data.get("F_TJPYJ")),
        "F_PYX_FRSJ": _clean_str(data.get("F_PYX_FRSJ")),
    }

    # 传代前/复苏前处理
    cdqxx = None
    if jd_lx == "复苏-培养":
        cdqxx = {
            "F_FSWD":  _clean_num(data.get("F_FSWD")),
            "F_FSSJ":  _clean_num(data.get("F_FSSJ")),
            "F_PYJ":   _clean_str(data.get("F_PYJ")),
            "F_LXSL":  _clean_num(data.get("F_LXSL")),
            "F_LXSJ":  _clean_num(data.get("F_LXSJ")),
            "F_HXHL":  _clean_num(data.get("F_HXHL")),
            "F_HZXS":  _clean_num(data.get("F_HZXS")),
            "F_FSXL":  _clean_num(data.get("F_FSXL")),
            "F_FSQXBZS": _clean_str(data.get("F_FSQXBZS")),
        }
    elif jd_lx == "传代-培养":
        cdqxx = {
            "F_CDQXBZS":  _clean_str(data.get("F_CDQXBZS") or data.get("F_CDQ_XBMD")),
            "F_CDQ_XBHL": _clean_num(data.get("F_CDQ_XBHL")),
            "F_JZMD":     _clean_str(data.get("F_JZMD")),
        }
        # 复苏前细胞总数也可能出现在传代场景模板里，有则一并带上
        if data.get("F_FSQXBZS") is not None:
            cdqxx["F_FSQXBZS"] = _clean_str(data.get("F_FSQXBZS"))

    qxycf = _clean_str(data.get("F_QXYCF")) if jd_lx in ("传代-培养", "复苏-培养") else None

    return {
        "pyjtjjl":    pyjtjjl,
        "pyxx_xh":    pyxx_xh,
        "xhmsy_list": xhmsy_list,
        "pyxx_pb":    pyxx_pb,
        "pyxx_py":    pyxx_py,
        "cdqxx":      cdqxx,
        "qxycf":      qxycf,
    }


# ===========================================================================
# 药物实验计划：experimentList → ZBXX / KWXX
# ===========================================================================

def _experiment_list_to_zbxx_kwxx(
    experiment_list: List[Dict[str, Any]],
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """
    将前端 experimentList 格式转为 create_test_plan 需要的 ZBXX/KWXX 格式。
    每个组 × 每个浓度 × F_FKSSZ = N 个孔位。
    """
    zbxx: List[Dict[str, Any]] = []
    kwxx: List[Dict[str, Any]] = []

    for idx, group in enumerate(experiment_list or []):
        zbmc    = group.get("F_ZBMC") or f"组别{idx + 1}"
        zblx    = group.get("F_ZBLX")
        fkssz   = int(group.get("F_FKSSZ") or 1)
        concs   = group.get("concentrations") or []
        drug_list = group.get("F_YY") or []

        nd_strs = [str(c) for c in concs] if concs else []

        # 组别信息
        zbxx.append({
            "F_ZBMC":  zbmc,
            "F_ZBLX":  zblx,
            "F_FKSSZ": fkssz,
            "F_ND":    nd_strs,
            "F_SORT":  idx + 1,
            "F_YY":    [
                {
                    "F_SJID": d.get("F_SJID"),
                    "F_PCH":  d.get("F_PCH"),
                    "F_YL":   d.get("F_YL"),
                    "F_XSFA": d.get("F_XSFA"),
                }
                for d in drug_list
            ],
        })

        # 孔位：每个浓度重复 fkssz 次
        for nd in nd_strs:
            for _ in range(fkssz):
                kwxx.append({"F_ZBMC": zbmc, "F_ND": nd})

    return zbxx, kwxx


# ===========================================================================
# 各节点初始化函数
# ===========================================================================

def _init_preprocess(gdid: int, zzid: Optional[str], data: Dict[str, Any], operator: Optional[str]) -> int:
    """预处理节点：调用 create_or_update_preprocess_by_gdid。返回记录 ID。"""
    from app.crud.preprocess import create_or_update_preprocess_by_gdid
    fields = {
        "F_QXYCF":    _clean_str(data.get("F_QXYCF")),
        "F_ZZSPCC":   _clean_str(str(data["F_ZZSPCC"]) if data.get("F_ZZSPCC") is not None else None),
        "F_YBYCLQK":  _clean_str(data.get("F_YBYCLQK")),
        "F_YBZLTJ":   _clean_str(data.get("F_YBZLTJ")),
        "F_SFDC":     _clean_str(data.get("F_SFDC")),
        "F_DCYCF":    _clean_str(data.get("F_DCYCF")),
        "F_KSCLSJ":   _clean_empty(data.get("F_KSCLSJ")),
    }
    result = create_or_update_preprocess_by_gdid(
        gdid=gdid,
        zzid=zzid,
        fields={k: v for k, v in fields.items() if v is not None},
        template_code="default",
        operator=operator,
        from_pipeline_template=True,
    )
    return result.get("F_ID") or result.get("id") or 0


def _init_culture_mx(gdid: int, jd_lx: str, data: Dict[str, Any], operator: Optional[str]) -> int:
    """培养节点（原代/传代/复苏-培养）：调用 create_mx。返回记录 ID。"""
    from app.crud.organoid_culture_detail import create_mx
    args = _preset_to_create_mx_args(jd_lx, data)
    result = create_mx(
        gdid=gdid,
        pyjtjjl=args["pyjtjjl"],
        pyxx_xh=args["pyxx_xh"],
        xhmsy_list=args["xhmsy_list"],
        pyxx_pb=args["pyxx_pb"],
        pyxx_py=args["pyxx_py"],
        cdqxx=args["cdqxx"],
        qxycf=args["qxycf"],
        file_ids=None,
        operator=operator,
        from_pipeline_template=True,
    )
    return result.get("F_ID") or result.get("id") or 0


def _init_observation(gdid: int, data: Any, operator: Optional[str]) -> List[int]:
    """
    观察节点：data 可以是单个 dict 或 list。
    每个元素调用一次 create_observation。返回新增记录 ID 列表。
    """
    from app.crud.organoid_observation import create_observation

    if isinstance(data, dict):
        items = [data]
    elif isinstance(data, list):
        items = data
    else:
        items = []

    ids: List[int] = []
    for item in items:
        # F_PYJZT：模板可能是 "a, b" 或 ["a","b"]，统一成逗号分隔字符串再落库
        from app.crud.organoid_observation import _normalize_pyjzt
        gcxx = {
            "F_GDID":  gdid,
            "F_PYTS":  _clean_num(item.get("F_PYTS")),
            "F_PYJZT": _normalize_pyjzt(item.get("F_PYJZT")),
            "F_SFWR":  item.get("F_SFWR"),
            "F_SFJY":  item.get("F_SFJY"),
            "F_PJZJ":  _clean_num(item.get("F_PJZJ")),
            "F_XBSL":  _clean_num(item.get("F_XBSL")),
            "F_SFHX":  _clean_str(item.get("F_SFHX")),
            "F_SFSK":  _clean_str(item.get("F_SFSK")),
            "F_SFHY":  item.get("F_SFHY"),
            "F_HYJL":  _clean_str(item.get("F_HYJL")),
            "F_SQXQL": _clean_num(item.get("F_SQXQL")),
            "F_SQBCL": _clean_num(item.get("F_SQBCL")),
            "F_BZ":    _clean_str(item.get("F_BZ")),
            "F_JLR":   operator,
        }
        result = create_observation(gcxx=gcxx, attachments=None, operator=operator, from_pipeline_template=True)
        rid = result.get("F_ID") or result.get("id") or 0
        if rid:
            ids.append(int(rid))
    return ids


def _init_qc(gdid: int, data: Dict[str, Any], operator: Optional[str]) -> int:
    """验证节点：调用 create_or_update_qc_record。返回记录 ID。"""
    from app.crud.qc_record import create_or_update_qc_record
    qc_data = {
        "F_QKMS": _clean_str(data.get("F_SZQK")),
        "F_PYTS": _clean_num(data.get("F_PYTS")),
        "F_XT":   _clean_str(data.get("F_XT")),
        "F_SFWR": data.get("F_SFWR"),
        "F_XBSL": _clean_num(data.get("F_XBSL")),
        "F_XBHL": _clean_num(data.get("F_XBHL")),
        "F_ZYT":  _clean_str(data.get("F_ZYTJCJG")),
    }
    return create_or_update_qc_record(
        gdid=gdid, qc_data=qc_data, operator=operator, from_pipeline_template=True
    )


def _init_animal_plan(gdid: int, data: Dict[str, Any], operator: Optional[str]) -> int:
    """
    动物实验计划节点：
    data 格式：{"plan_info": {"JZXX": [...]}, "plan": {...}, "JLXX": {...}}
    返回新建计划 ID。
    """
    from app.crud.animal_test import create_animal_test_plan

    plan_info = data.get("plan_info") or {}
    plan      = data.get("plan") or {}

    create_data = {
        "F_GDID":    gdid,
        "F_SYMC":    _clean_str(plan.get("F_SYMC")),
        "F_SYJHYMD": _clean_str(plan.get("F_SYJHYMD")),
        "F_LQGMD":   _clean_str(plan.get("F_LQGMD")),
        "F_JZJND":   _clean_str(plan.get("F_JZJND")),
        "F_JZJTJ":   _clean_str(plan.get("F_JZJTJ")),
        "JZXX":      plan_info.get("JZXX") or [],
    }
    result = create_animal_test_plan(data=create_data, operator=operator, from_pipeline_template=True)
    return result.get("plan_id") or result.get("F_ID") or 0


def _init_animal_record(plan_id: int, data: Dict[str, Any], operator: Optional[str]) -> None:
    """
    动物实验记录节点：
    data 格式：{"GCJL_LIST": [...], "JLXX": {...}}
    按 uid 分组创建观察记录，再创建终点记录。
    """
    from app.crud.animal_test_record import save_observation, save_endpoint_record

    gcjl_list = data.get("GCJL_LIST") or []
    jlxx      = data.get("JLXX") or {}

    # 按 uid 分组 → 每个 uid 代表一个时间点
    from collections import defaultdict
    uid_groups: Dict[Any, List[Dict[str, Any]]] = defaultdict(list)
    for item in gcjl_list:
        uid = item.get("uid", 0)
        uid_groups[uid].append(item)

    for uid_key in sorted(uid_groups.keys()):
        zbxx = uid_groups[uid_key]
        header: Dict[str, Any] = {}
        try:
            save_observation(
                plan_id=plan_id,
                gcjl_id=None,
                header=header,
                zbxx=zbxx,
                file_ids=[],
                operator=operator,
                skip_qc=True,
                from_pipeline_template=True,
            )
        except Exception as e:
            logger.warning(f"[pipeline_init] 动物实验观察记录（uid={uid_key}）创建失败: {e}")

    # 终点记录
    if jlxx:
        endpoint_data = {
            "F_SYZQ":   _clean_str(jlxx.get("F_SYZQ")),
            "F_YZZZQK": _clean_str(jlxx.get("F_YZZZQK")),
            "F_SYJL":   _clean_str(jlxx.get("F_SYJL")),
        }
        try:
            save_endpoint_record(
                plan_id=plan_id,
                data=endpoint_data,
                new_file_ids=[],
                operator=operator,
                skip_qc=True,
                from_pipeline_template=True,
            )
        except Exception as e:
            logger.warning(f"[pipeline_init] 动物实验终点记录创建失败: {e}")


def _init_drug_plan(gdid: int, data: Dict[str, Any], operator: Optional[str]) -> int:
    """
    药物实验计划节点：
    data 格式：{"F_SYMC", "experimentList": [...], "F_KBLX", "F_KBBH", ...}
    返回新建计划 ID。
    """
    from app.crud.drug_test import create_test_plan, normalize_kwxx_positions

    experiment_list = data.get("experimentList") or []
    zbxx, kwxx = _experiment_list_to_zbxx_kwxx(experiment_list)
    kwxx = normalize_kwxx_positions(kwxx)

    create_data = {
        "F_GDID":    gdid,
        "F_SYMC":    _clean_str(data.get("F_SYMC")),
        "F_SYJHYMD": _clean_str(data.get("F_SYJHYMD")),
        "F_LQGMD":   _clean_str(data.get("F_LQGMD")),
        "F_JZJND":   _clean_str(data.get("F_JZJND")),
        "F_JZJTJ":   _clean_str(data.get("F_JZJTJ")),
        "F_KBLX":    _clean_str(data.get("F_KBLX")),
        "F_KBBH":    _clean_str(data.get("F_KBBH")),
        "ZBXX":      zbxx,
        "KWXX":      kwxx,
    }
    result = create_test_plan(data=create_data, operator=operator, skip_stock_check=True, from_pipeline_template=True)
    return result.get("plan_id") or result.get("F_ID") or 0


def _init_drug_record(plan_id: int, data: Dict[str, Any], operator: Optional[str]) -> None:
    """
    药物实验记录节点：
    data 格式：{"XTXBH": [...], "F_SYJL": "..."}
    """
    from app.crud.drug_test import save_experiment_record as drug_save_record

    record_data = {
        "F_SYJH_ID": plan_id,
        "XTXBH":     data.get("XTXBH"),
        "SYJL":      _clean_str(data.get("F_SYJL")),
    }
    try:
        drug_save_record(data=record_data)
    except Exception as e:
        logger.warning(f"[pipeline_init] 药物实验记录创建失败: {e}")


def _init_freeze(gdid: int, data: Dict[str, Any], operator: Optional[str]) -> None:
    """冻存记录节点：调用 create_freeze。"""
    from app.crud.organoid_freeze import create_freeze

    # baseGelTableConfig → xhmsy_list（消化酶信息）
    gel_cfg = data.get("baseGelTableConfig") or []
    xhmsy_list = [
        {
            "F_XHM":  g.get("F_XHM"),
            "F_YL":   _clean_num(g.get("F_YL")),
            "F_ND":   _clean_str(g.get("F_ND")),
            "F_LXWD": _clean_num(g.get("F_LXWD")),
            "F_LXSL": _clean_num(g.get("F_LXSL")),
            "F_XHSJ": _clean_num(g.get("F_XHSJ")),
        }
        for g in gel_cfg
    ] or None

    dcjl = {
        "F_DCQ_XBHL":  _clean_num(data.get("F_DCQ_XBHL")),
        "F_DCQXBZS":   _clean_str(data.get("F_DCQXBZS") or data.get("F_DCQ_XTPG")),
        "F_DCQ_HXBSL": _clean_str(data.get("F_DCQ_HXBSL")),
        "F_DCY":       _clean_str(data.get("F_DCY") or data.get("F_DCYCF")),
        "F_DCYND":     _clean_str(data.get("F_DCYND")),
        "F_DCGS":      _clean_str(data.get("F_DCGS")),
        "F_MG_DCSL":   _clean_str(data.get("F_MG_DCSL")),
        "F_SXBS":      _clean_str(data.get("F_SXBS") or data.get("F_HXBMD")),
    }

    create_freeze(
        gdid=gdid,
        xhmsy_list=xhmsy_list,
        dcjl=dcjl,
        operator=operator,
        from_pipeline_template=True,
    )


def _init_passage_count(gdid: int, data: Dict[str, Any]) -> None:
    """代次管理节点：更新 T_PYGD.F_JHCDCS。"""
    from app.db.factory import get_adapter
    adapter = get_adapter()
    count = data.get("planTransferenceTimes")
    if count is None:
        return
    try:
        with adapter.connect() as conn:
            cur = conn.cursor()
            cur.execute("UPDATE T_PYGD SET F_JHCDCS = ? WHERE F_ID = ?", (int(count), gdid))
            conn.commit()
    except Exception as e:
        logger.warning(f"[pipeline_init] 更新代次管理（F_JHCDCS）失败: {e}")


# ===========================================================================
# 清理函数（用于初始化失败时回滚）
# ===========================================================================

def _cleanup_initialized(gdid: int, initialized: List[Dict[str, Any]]) -> None:
    """
    尝试清除已创建的节点数据（补偿操作）。
    initialized 格式：[{"node": "预处理", "ids": [id], "table": "T_YCL"}, ...]
    """
    from app.db.factory import get_adapter
    adapter = get_adapter()

    # 节点类型 → (主表, ID列)
    cleanup_map = {
        "预处理":       ("T_YCL",     "F_GDID"),     # 按 gdid 删
        "培养明细":     ("T_PYMX_ZZ", "F_GDID"),
        "观察":         ("T_GCJL",    "F_ID"),        # 按具体 ID 删
        "验证":         ("T_ZKJL",    "F_GDID"),
        "动物实验计划": ("T_DWSY_JH", "F_ID"),
        "药物实验计划": ("T_SYJH",    "F_ID"),
        "冻存记录":     ("T_DCJL",    "F_GDID"),
        "代次管理":     None,                          # 只需 UPDATE，不删
    }

    for item in initialized:
        node = item.get("node")
        ids  = item.get("ids") or []
        cfg  = cleanup_map.get(node)
        if cfg is None:
            continue
        table, col = cfg
        try:
            with adapter.connect() as conn:
                cur = conn.cursor()
                if col == "F_GDID":
                    cur.execute(f"DELETE FROM {table} WHERE F_GDID = ?", (gdid,))
                else:
                    for rid in ids:
                        cur.execute(f"DELETE FROM {table} WHERE F_ID = ?", (rid,))
                conn.commit()
            logger.info(f"[pipeline_init] 清理节点 {node} 成功（table={table}, ids={ids}）")
        except Exception as e:
            logger.warning(f"[pipeline_init] 清理节点 {node} 失败: {e}")


# ===========================================================================
# 读取流水线模板节点
# ===========================================================================

def _fetch_pipeline_nodes(pipeline_id: int) -> List[Dict[str, Any]]:
    """从 T_PIPELINE_NODE 读取指定流水线的节点列表。"""
    from app.db.factory import get_adapter
    adapter = get_adapter()
    rows, cols = adapter.fetch_all(
        "SELECT F_ID, F_PIPELINE_ID, F_JD_LX, F_PRESET FROM T_PIPELINE_NODE WHERE F_PIPELINE_ID = ? ORDER BY F_ID",
        [pipeline_id],
    )
    return [dict(zip(cols, row)) for row in rows]


# ===========================================================================
# 主入口
# ===========================================================================

def apply_pipeline_to_order(gdid: int, operator: Optional[str]) -> Dict[str, Any]:
    """
    根据工单已关联的 JM/LX 流水线模板，自动创建各节点的初始业务记录。

    规则：
    1. 若工单已有任何业务数据 → 跳过（幂等）
    2. JM 流水线处理建模节点（预处理、培养、观察、验证）
    3. LX 流水线处理流向节点（动物/药物实验计划+记录、代次管理、冻存）
    4. 多个 LX 流水线检查重复节点类型，有重复则报错
    5. 任一节点失败 → 清理已创建数据，抛出 ValueError（注明失败节点）

    Returns: {"initialized": [节点名称列表], "skipped": [...]}
    Raises: ValueError 描述哪个节点失败
    """
    from app.db.factory import get_adapter
    from app.crud.organoid_culture_tissue import _deserialize_lx

    adapter = get_adapter()

    # ── 0. 读取工单信息 ──────────────────────────────────────────────────────
    rows, cols = adapter.fetch_all(
        "SELECT F_PIPELINE_JM, F_PIPELINE_LX, F_ZZID, F_YBLY, "
        "F_GDBH, F_DCID, F_DC, F_PYSJ, F_SQR, F_XM, F_BRBH "
        "FROM T_PYGD WHERE F_ID = ?",
        [gdid],
    )
    if not rows:
        raise ValueError(f"工单 ID={gdid} 不存在")

    order = dict(zip(cols, rows[0]))
    jm_id  = order.get("F_PIPELINE_JM")
    lx_raw = order.get("F_PIPELINE_LX")
    zzid   = order.get("F_ZZID")

    lx_ids: List[int] = _deserialize_lx(lx_raw) or []

    if not jm_id and not lx_ids:
        logger.info(f"[pipeline_init] 工单 {gdid} 未关联任何流水线，跳过初始化")
        return {"initialized": [], "skipped": ["未关联流水线"]}

    # ── 1. 幂等检查 ─────────────────────────────────────────────────────────
    has_data, data_desc = check_order_has_data(gdid)
    if has_data:
        logger.info(f"[pipeline_init] 工单 {gdid} 已有业务数据（{data_desc}），跳过初始化")
        return {"initialized": [], "skipped": [f"已有数据：{data_desc}"]}

    # ── 2. 读取节点 ──────────────────────────────────────────────────────────
    jm_nodes: List[Dict[str, Any]] = []
    if jm_id:
        try:
            jm_nodes = _fetch_pipeline_nodes(int(jm_id))
        except Exception as e:
            raise ValueError(f"读取建模流水线（ID={jm_id}）节点失败: {e}")

    lx_nodes: List[Dict[str, Any]] = []
    for lx_pid in lx_ids:
        try:
            nodes = _fetch_pipeline_nodes(int(lx_pid))
            lx_nodes.extend(nodes)
        except Exception as e:
            raise ValueError(f"读取流向流水线（ID={lx_pid}）节点失败: {e}")

    # ── 3. LX 重复节点检查 ───────────────────────────────────────────────────
    if len(lx_ids) > 1:
        lx_type_counts: Dict[str, int] = {}
        for n in lx_nodes:
            jd_lx = n.get("F_JD_LX") or ""
            if jd_lx in _SKIP_NODE_TYPES:
                continue
            lx_type_counts[jd_lx] = lx_type_counts.get(jd_lx, 0) + 1
        dups = [t for t, cnt in lx_type_counts.items() if cnt > 1]
        if dups:
            raise ValueError(f"多个流向流水线存在重复节点类型：{', '.join(dups)}，请检查模板配置")

    # ── 4. 执行初始化 ─────────────────────────────────────────────────────────
    initialized: List[Dict[str, Any]] = []  # {node, ids}
    initialized_names: List[str] = []

    # 计划节点先执行（记录节点依赖计划 ID）
    animal_plan_id: Optional[int] = None
    drug_plan_id:   Optional[int] = None

    # 供需要手动写质控审核记录的节点使用
    def _write_qc_audit(zkjd: str, detail_data: Optional[Dict[str, Any]] = None) -> None:
        """为当前工单写一条质控审核记录（T_ZKSH）。"""
        try:
            from app.crud.qc_audit import create_qc_audit_record, get_sample_basic_info
            sample_info = get_sample_basic_info(zzid) if zzid else {}
            changes = {
                "操作": f"流水线初始化 - {zkjd}",
                "关键信息": {"工单ID": gdid, "标本号": zzid},
            }
            create_qc_audit_record(
                F_ZKJD=zkjd,
                F_BGLX="新增",
                F_ZZID=zzid,
                F_CHANGES=changes,
                F_JLR=operator or "系统",
                F_GDID=gdid,
                F_XM=sample_info.get("F_XM") or order.get("F_XM"),
                F_BRBH=sample_info.get("F_BRBH") or order.get("F_BRBH"),
                F_ZZBW=sample_info.get("F_ZZBW"),
                F_YBLY=order.get("F_YBLY"),
                F_GDBH=order.get("F_GDBH"),
                F_DCID=order.get("F_DCID"),
                F_DC=order.get("F_DC"),
                F_SQR=order.get("F_SQR"),
                F_OLD_DATA=detail_data,
                from_pipeline_template=True,
            )
        except Exception as e:
            logger.warning(f"[pipeline_init] 节点 {zkjd} 质控审核记录创建失败（忽略）: {e}")

    def _process_node(node: Dict[str, Any]) -> None:
        nonlocal animal_plan_id, drug_plan_id

        jd_lx  = node.get("F_JD_LX") or ""
        preset = _extract_preset_data(node.get("F_PRESET"))

        if jd_lx in _SKIP_NODE_TYPES:
            return

        try:
            if jd_lx == "预处理":
                rid = _init_preprocess(gdid, zzid, preset, operator)
                initialized.append({"node": "预处理", "ids": [rid]})
                initialized_names.append("预处理")

            elif jd_lx in ("原代-培养", "传代-培养", "复苏-培养"):
                rid = _init_culture_mx(gdid, jd_lx, preset, operator)
                initialized.append({"node": "培养明细", "ids": [rid]})
                initialized_names.append(jd_lx)
                # create_mx 不自带质控审核记录，需手动补写
                _write_qc_audit("培养")

            elif jd_lx == "观察":
                ids = _init_observation(gdid, preset, operator)
                initialized.append({"node": "观察", "ids": ids})
                initialized_names.append("观察")

            elif jd_lx == "验证":
                rid = _init_qc(gdid, preset, operator)
                initialized.append({"node": "验证", "ids": [rid]})
                initialized_names.append("验证")
                # 模板初始化等价于验证单暂存，只写 T_ZKJL，不写 T_ZKSH

            elif jd_lx == "动物实验计划":
                plan_id = _init_animal_plan(gdid, preset, operator)
                animal_plan_id = plan_id
                initialized.append({"node": "动物实验计划", "ids": [plan_id]})
                initialized_names.append("动物实验计划")

            elif jd_lx == "药物实验计划":
                plan_id = _init_drug_plan(gdid, preset, operator)
                drug_plan_id = plan_id
                initialized.append({"node": "药物实验计划", "ids": [plan_id]})
                initialized_names.append("药物实验计划")

            elif jd_lx == "代次管理":
                _init_passage_count(gdid, preset)
                initialized_names.append("代次管理")

            elif jd_lx == "冻存记录":
                _init_freeze(gdid, preset, operator)
                initialized.append({"node": "冻存记录", "ids": []})
                initialized_names.append("冻存记录")

        except Exception as exc:
            _cleanup_initialized(gdid, initialized)
            raise ValueError(f"节点【{jd_lx}】初始化失败: {exc}") from exc

    # 所有节点按：先计划节点，再记录节点的顺序处理
    plan_node_types   = {"预处理", "原代-培养", "传代-培养", "复苏-培养", "观察", "验证",
                         "动物实验计划", "药物实验计划", "代次管理", "冻存记录"}
    record_node_types = {"动物实验记录", "药物实验记录"}

    all_nodes = jm_nodes + lx_nodes
    for n in all_nodes:
        if (n.get("F_JD_LX") or "") in plan_node_types:
            _process_node(n)

    # 记录节点（依赖前一步创建的计划 ID）
    for n in all_nodes:
        jd_lx  = n.get("F_JD_LX") or ""
        preset = _extract_preset_data(n.get("F_PRESET"))
        if jd_lx == "动物实验记录":
            if animal_plan_id:
                try:
                    _init_animal_record(animal_plan_id, preset, operator)
                    initialized_names.append("动物实验记录")
                except Exception as exc:
                    _cleanup_initialized(gdid, initialized)
                    raise ValueError(f"节点【动物实验记录】初始化失败: {exc}") from exc
            else:
                logger.warning("[pipeline_init] 动物实验记录节点存在，但未找到对应计划 ID，跳过")

        elif jd_lx == "药物实验记录":
            if drug_plan_id:
                try:
                    _init_drug_record(drug_plan_id, preset, operator)
                    initialized_names.append("药物实验记录")
                    # 模板初始化等价于实验记录暂存，不写 T_ZKSH
                except Exception as exc:
                    _cleanup_initialized(gdid, initialized)
                    raise ValueError(f"节点【药物实验记录】初始化失败: {exc}") from exc
            else:
                logger.warning("[pipeline_init] 药物实验记录节点存在，但未找到对应计划 ID，跳过")

    logger.info(f"[pipeline_init] 工单 {gdid} 流水线初始化完成: {initialized_names}")
    return {"initialized": initialized_names, "skipped": []}
