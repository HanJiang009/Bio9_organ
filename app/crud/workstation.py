"""
快捷工位模块 CRUD

根据培养工单关联的流水线模板（F_PIPELINE_JM / F_PIPELINE_LX），
聚合返回工单各阶段的模板预设配置及实际业务数据。

建模阶段节点名称：预处理 / 原代-培养 / 传代-培养 / 复苏-培养 / 观察 / 验证
流向阶段节点名称：药物实验计划 / 药物实验记录 / 动物实验计划 / 动物实验记录 /
                  代次管理 / 冻存记录 / 入库（入库暂不开发，返回 null）
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Dict, List, Optional, Set

from app.db.factory import get_adapter
from app.crud.qc_audit import zkjd_matches, TEMPLATE_ZKJD_SUFFIX, normalize_zkjd
from app.utils.yhbc import as_bool

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 节点名称常量
# ---------------------------------------------------------------------------
_JM_NODES: Set[str] = {
    "预处理", "原代-培养", "传代-培养", "复苏-培养", "观察", "验证"
}
_CULTURE_NODES: Set[str] = {"原代-培养", "传代-培养", "复苏-培养"}

_LX_NODES: Set[str] = {
    "药物实验计划", "药物实验记录",
    "动物实验计划", "动物实验记录",
    "代次管理", "冻存记录", "入库",
}

# 节点 → 质控阶段名（用于无 F_YHBC 时的存量兜底）
_NODE_TO_ZKJD: Dict[str, str] = {
    "预处理": "预处理",
    "原代-培养": "培养",
    "传代-培养": "培养",
    "复苏-培养": "培养",
    "观察": "观察",
    "验证": "验证单",
    "动物实验计划": "动物实验计划",
    "动物实验记录": "动物实验观察记录",
    "药物实验计划": "药物实验计划",
    "药物实验记录": "药物实验记录",
    "冻存记录": "冻存",
}


# ---------------------------------------------------------------------------
# 内部工具
# ---------------------------------------------------------------------------

def _safe(fn, *args, label: str = "", **kwargs):
    """安全调用：失败时记录日志并返回 None，不影响整体响应。"""
    try:
        return fn(*args, **kwargs)
    except Exception as e:
        logger.warning(f"[workstation] {label} 查询失败: {e}")
        return None


def _get_pipeline_with_nodes(pipeline_id: int) -> Optional[Dict[str, Any]]:
    """查询流水线模板基础信息及全部节点（复用 pipeline crud 逻辑）。"""
    from app.crud.pipeline import get_pipeline_detail
    return _safe(get_pipeline_detail, pipeline_id, label=f"流水线模板 id={pipeline_id}")


def _get_order_basic(gdbh: str) -> Optional[Dict[str, Any]]:
    """按工单编号查询培养工单基础字段，返回结果含 F_ID（gdid）。"""
    adapter = get_adapter()
    try:
        with adapter.connect() as conn:
            cur = conn.cursor()
            cur.execute(
                """
                SELECT F_ID, F_GDBH, F_ZZID, F_XM, F_BRBH, F_BBLX, F_DC, F_DCID,
                       F_ZZBW, F_QYFS, F_SQR, F_PYSJ, F_GDZT, F_ZKZT AS F_SHZT, F_AZ, F_YBLY,
                       F_PIPELINE_JM, F_PIPELINE_LX, F_PARENT_ID
                FROM T_PYGD WHERE F_GDBH = ?
                """,
                (gdbh,),
            )
            row = cur.fetchone()
            if not row:
                return None
            cols = [c[0] for c in cur.description]
            return dict(zip(cols, row))
    except Exception as e:
        logger.warning(f"[workstation] 工单查询失败 gdbh={gdbh}: {e}")
        return None


# ---------------------------------------------------------------------------
# 建模阶段（JM）各节点数据查询
# ---------------------------------------------------------------------------

def _build_empty_ybpfb(score_tpl: Dict[str, Any]) -> Dict[str, Any]:
    """
    将 fetch_score_template 返回的模板（含 items）转换为与 F_YBPFB 一致的
    空白 details 格式（value=null, score=0），供无预处理记录时前端渲染空表使用。
    """
    details = [
        {
            "key": item.get("key"),
            "label": item.get("label") or item.get("key"),
            "value": None,
            "score": 0,
            "options": item.get("options") or [],
        }
        for item in (score_tpl.get("items") or [])
    ]
    return {
        "template_code": score_tpl.get("template_code"),
        "name": score_tpl.get("name"),
        "version": score_tpl.get("version"),
        "total": 0,
        "details": details,
    }


def _culture_node_for_order(order: Optional[Dict[str, Any]]) -> Optional[str]:
    """
    根据工单 F_YBLY（样本来源）推断该工单对应的培养节点名称。

    映射规则：
      F_YBLY 含 "复苏"              → "复苏-培养"   (原代复苏 / 传代复苏)
      F_YBLY == "传代"              → "传代-培养"
      其余（原代制备 / 未知）       → "原代-培养"

    返回 None 表示无法推断（order 为空时），调用方应保持原有全量赋值行为。
    """
    if not order:
        return None
    ybly = (order.get("F_YBLY") or "").strip()
    if not ybly:
        return None
    if "复苏" in ybly:
        return "复苏-培养"
    if ybly == "传代":
        return "传代-培养"
    return "原代-培养"


def _attach_dcxx_for_revival(
    node_data: Any,
    gdid: int,
    order: Optional[Dict[str, Any]],
) -> Any:
    """为「复苏-培养」节点附加冻存摘要 DCXX（与 /organoid-thaw/detail 一致）。"""
    from app.crud.organoid_thaw import get_dcxx_by_gdid
    parent_id = (order or {}).get("F_PARENT_ID")
    dcxx = _safe(get_dcxx_by_gdid, gdid, parent_id, label="冻存信息(DCXX)")
    if node_data is None:
        return {"DCXX": dcxx} if dcxx else None
    if not isinstance(node_data, dict):
        return {"detail": node_data, "DCXX": dcxx}
    merged = dict(node_data)
    merged["DCXX"] = dcxx
    return merged


def _fetch_jm_stage_data(
    gdid: int,
    node_names: Set[str],
    order: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """按节点名称集合查询 JM 阶段各节点的业务数据。

    Parameters
    ----------
    order : 工单基础信息字典（来自 _get_order_basic），用于判断培养类型。
            传入后三种培养节点中只有与工单类型匹配的节点会携带数据，
            其余节点返回 None；不传则退化为旧行为（全量赋值）。
    """
    stage: Dict[str, Any] = {}

    if "预处理" in node_names:
        from app.crud.preprocess import fetch_preprocess_by_gdid, fetch_score_template, fetch_ltsj_for_gdid
        preprocess_data = _safe(fetch_preprocess_by_gdid, gdid, label="预处理")
        if preprocess_data is not None:
            # 有预处理记录：若 F_YBPFB 缺失，用默认模板补全空结构
            if not isinstance(preprocess_data.get("F_YBPFB"), dict):
                tpl_code = "default"
                score_tpl = _safe(fetch_score_template, tpl_code, label="样本评分表模板")
                if score_tpl:
                    preprocess_data["F_YBPFB"] = _build_empty_ybpfb(score_tpl)
            # T_YCL.F_LTSJ 老记录可能为 null，兜底从 T_YBZJ 补全
            if not preprocess_data.get("F_LTSJ"):
                preprocess_data["F_LTSJ"] = _safe(fetch_ltsj_for_gdid, gdid, label="离体时间(补全)")
        else:
            # 暂无预处理记录：构造骨架，F_LTSJ 从 T_YBZJ 读取真实值
            ltsj = _safe(fetch_ltsj_for_gdid, gdid, label="离体时间(无预处理记录)")
            score_tpl = _safe(fetch_score_template, "default", label="样本评分表模板(空)")
            preprocess_data = {
                "F_LTSJ": ltsj,
                "F_YBPFB": _build_empty_ybpfb(score_tpl) if score_tpl else None,
            }
        stage["预处理"] = preprocess_data

    # 三种培养类型：只为与工单类型匹配的节点填充数据，其余置 None
    culture_intersect = _CULTURE_NODES & node_names
    if culture_intersect:
        from app.crud.organoid_culture_detail import get_mx_by_gdid
        data = _safe(get_mx_by_gdid, gdid, label="培养明细")
        matched_node = _culture_node_for_order(order)
        for name in culture_intersect:
            if matched_node is None:
                node_data = data
            else:
                node_data = data if name == matched_node else None
            if name == "复苏-培养":
                if node_data is not None:
                    node_data = _attach_dcxx_for_revival(node_data, gdid, order)
                elif matched_node == "复苏-培养":
                    node_data = _attach_dcxx_for_revival(None, gdid, order)
            stage[name] = node_data

    if "观察" in node_names:
        # 使用完整详情结构（含 order / incubator / observations / recycle），
        # 与 /organoid-observation/detail-by-gdid 接口保持一致
        from app.crud.organoid_observation import get_observations_by_gdid
        stage["观察"] = _safe(get_observations_by_gdid, gdid, label="观察详情")

    if "验证" in node_names:
        stage["验证"] = _safe(_fetch_qc_by_gdid, gdid, label="验证单")

    # 病理 / 测序 / 其他：从培养工单对应字段读取附件数组
    _attachment_node_fields = {"病理": "F_BL", "测序": "F_CX", "其他": "F_QT"}
    for _node, _field in _attachment_node_fields.items():
        if _node in node_names:
            stage[_node] = _safe(_fetch_order_attachment, gdid, _field, label=_node)

    return stage


def _fetch_order_attachment(gdid: int, field: str) -> Dict[str, Any]:
    """从 T_PYGD 查询指定 JSON 附件字段，返回 {"bz": str, "files": [...]} 格式。

    兼容旧格式（纯 ID 数组 / [{id, bz}] 列表）和新格式（{"bz", "ids"}）。
    """
    adapter = get_adapter()
    with adapter.connect() as conn:
        cur = conn.cursor()
        cur.execute(f"SELECT {field} FROM T_PYGD WHERE F_ID = ?", (gdid,))
        row = cur.fetchone()
        raw = row[0] if row else None

    from app.crud.organoid_culture_tissue import _format_attachment_field
    return _format_attachment_field(raw) if raw else {"bz": "", "files": []}


def _fetch_qc_by_gdid(gdid: int) -> Optional[Dict[str, Any]]:
    """查询验证节点数据：复用 /qc/overview-by-gdid 的完整逻辑。
    未提交验证单时，会从观察记录预填对应天数/倍率的图片，结构与 overview 接口一致。
    """
    from app.crud.qc_record import get_qc_overview
    return get_qc_overview(gdid)


# ---------------------------------------------------------------------------
# 流向阶段（LX）各节点数据查询
# ---------------------------------------------------------------------------

def _fetch_lx_stage_data(
    gdid: int,
    node_names: Set[str],
    order: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """按节点名称集合查询 LX 阶段各节点的业务数据。"""
    stage: Dict[str, Any] = {}
    gdbh: Optional[str] = (order or {}).get("F_GDBH")

    need_animal = "动物实验计划" in node_names or "动物实验记录" in node_names
    need_drug = "药物实验计划" in node_names or "药物实验记录" in node_names

    # ---- 动物实验 ----
    animal_plan_id: Optional[int] = None
    if need_animal:
        animal_plan_id = _get_animal_plan_id_by_gdid(gdid, gdbh=gdbh)

    if "动物实验计划" in node_names:
        if animal_plan_id:
            from app.crud.animal_test import get_animal_test_plan_detail
            stage["动物实验计划"] = _safe(
                get_animal_test_plan_detail, animal_plan_id, label="动物实验计划"
            )
        else:
            stage["动物实验计划"] = None

    if "动物实验记录" in node_names:
        if animal_plan_id:
            from app.crud.animal_test_record import get_experiment_record
            stage["动物实验记录"] = _safe(
                get_experiment_record, animal_plan_id, label="动物实验记录"
            )
        else:
            stage["动物实验记录"] = None

    # ---- 药物实验 ----
    drug_plan_id: Optional[int] = None
    if need_drug:
        drug_plan_id = _get_drug_plan_id_by_gdid(gdid, gdbh=gdbh)

    if "药物实验计划" in node_names:
        if drug_plan_id:
            from app.crud.drug_test import get_test_plan_detail
            stage["药物实验计划"] = _safe(
                get_test_plan_detail, drug_plan_id, label="药物实验计划"
            )
        else:
            stage["药物实验计划"] = None

    if "药物实验记录" in node_names:
        if drug_plan_id:
            from app.crud.drug_test_result import get_experiment_record_by_plan
            stage["药物实验记录"] = _safe(
                get_experiment_record_by_plan, drug_plan_id, label="药物实验记录"
            )
        else:
            stage["药物实验记录"] = None

    # ---- 代次管理 ----
    if "代次管理" in node_names:
        child_orders = _safe(_fetch_child_orders, gdid, label="代次管理")
        jhcdcs = None
        try:
            _adp = get_adapter()
            with _adp.connect() as _conn:
                _cur = _conn.cursor()
                _cur.execute("SELECT F_JHCDCS FROM T_PYGD WHERE F_ID = ?", (gdid,))
                _row = _cur.fetchone()
                if _row:
                    jhcdcs = _row[0]
        except Exception:
            pass
        stage["代次管理"] = {
            "F_JHCDCS": jhcdcs,
            "children": child_orders,
        }

    # ---- 冻存记录 ----
    if "冻存记录" in node_names:
        from app.crud.organoid_freeze import get_freeze_detail_by_gdid
        stage["冻存记录"] = _safe(get_freeze_detail_by_gdid, gdid, label="冻存记录")

    # ---- 入库（暂不开发）----
    if "入库" in node_names:
        stage["入库"] = None

    return stage


def _get_animal_plan_id_by_gdid(
    gdid: int, gdbh: Optional[str] = None
) -> Optional[int]:
    """通过工单 F_ID 查询最新动物实验计划 ID。
    若直接匹配无结果，则通过 T_PYGD.F_GDBH JOIN 兜底（兼容 F_GDID 类型差异）。
    """
    adapter = get_adapter()
    try:
        with adapter.connect() as conn:
            cur = conn.cursor()
            logger.info(f"[workstation] 动物实验计划查询 gdid={gdid} gdbh={gdbh}")
            cur.execute(
                "SELECT F_ID FROM T_DWSY_JH WHERE F_GDID = ? ORDER BY F_ID DESC",
                (gdid,),
            )
            row = cur.fetchone()
            if row:
                logger.info(f"[workstation] 动物实验计划命中 plan_id={row[0]}")
                return int(row[0])
            # 兜底：JOIN T_PYGD 通过 F_GDBH 匹配（应对 F_GDID 类型不一致情况）
            if gdbh:
                logger.info(f"[workstation] 动物实验计划兜底查询 gdbh={gdbh}")
                cur.execute(
                    """SELECT jh.F_ID FROM T_DWSY_JH jh
                    INNER JOIN T_PYGD gd ON jh.F_GDID = gd.F_ID
                    WHERE gd.F_GDBH = ?
                    ORDER BY jh.F_ID DESC""",
                    (gdbh,),
                )
                row = cur.fetchone()
                if row:
                    logger.info(f"[workstation] 动物实验计划兜底命中 plan_id={row[0]}")
                    return int(row[0])
            logger.info(f"[workstation] 未找到动物实验计划 gdid={gdid} gdbh={gdbh}")
            return None
    except Exception as e:
        logger.warning(f"[workstation] 动物实验计划 ID 查询失败 gdid={gdid}: {e}")
        return None


def _get_drug_plan_id_by_gdid(
    gdid: int, gdbh: Optional[str] = None
) -> Optional[int]:
    """通过工单 F_ID 查询最新药物实验计划 ID。
    若直接匹配无结果，则通过 T_PYGD.F_GDBH JOIN 兜底（兼容 F_GDID 类型差异）。
    """
    adapter = get_adapter()
    try:
        with adapter.connect() as conn:
            cur = conn.cursor()
            logger.info(f"[workstation] 药物实验计划查询 gdid={gdid} gdbh={gdbh}")
            cur.execute(
                "SELECT F_ID FROM T_SYJH WHERE F_GDID = ? ORDER BY F_ID DESC",
                (gdid,),
            )
            row = cur.fetchone()
            if row:
                logger.info(f"[workstation] 药物实验计划命中 plan_id={row[0]}")
                return int(row[0])
            # 兜底：JOIN T_PYGD
            if gdbh:
                logger.info(f"[workstation] 药物实验计划兜底查询 gdbh={gdbh}")
                cur.execute(
                    """SELECT jh.F_ID FROM T_SYJH jh
                    INNER JOIN T_PYGD gd ON jh.F_GDID = gd.F_ID
                    WHERE gd.F_GDBH = ?
                    ORDER BY jh.F_ID DESC""",
                    (gdbh,),
                )
                row = cur.fetchone()
                if row:
                    logger.info(f"[workstation] 药物实验计划兜底命中 plan_id={row[0]}")
                    return int(row[0])
            logger.info(f"[workstation] 未找到药物实验计划 gdid={gdid} gdbh={gdbh}")
            return None
    except Exception as e:
        logger.warning(f"[workstation] 药物实验计划 ID 查询失败 gdid={gdid}: {e}")
        return None



def _fetch_child_orders(gdid: int) -> List[Dict[str, Any]]:
    """查询子代工单列表（传代 / 复苏）。"""
    adapter = get_adapter()
    with adapter.connect() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT F_ID, F_GDBH, F_DC, F_DCID, F_YBLY, F_GDZT, F_PYSJ, F_CJSJ, F_SQR, F_JHCDCS
            FROM T_PYGD WHERE F_PARENT_ID = ? ORDER BY F_ID ASC
            """,
            (gdid,),
        )
        rows = cur.fetchall()
        cols = [c[0] for c in cur.description]
        return [dict(zip(cols, r)) for r in rows]


# ---------------------------------------------------------------------------
# 节点 meta 提取：操作人 / 记录时间 / 间隔时间
# ---------------------------------------------------------------------------

def _parse_time(val: Any) -> Optional[datetime]:
    """将字符串或 datetime 解析为 datetime 对象，用于计算间隔。"""
    if val is None:
        return None
    if isinstance(val, datetime):
        return val
    if isinstance(val, str):
        s = val.strip()
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
            try:
                return datetime.strptime(s, fmt)
            except Exception:
                continue
    return None


def _load_qc_user_saved_stages(gdid: int) -> Set[str]:
    """查询该工单下「非模板」质控阶段名集合（已 normalize）。"""
    out: Set[str] = set()
    try:
        adapter = get_adapter()
        with adapter.connect() as conn:
            cur = conn.cursor()
            cur.execute("SELECT F_ZKJD FROM T_ZKSH WHERE F_GDID = ?", (gdid,))
            for row in cur.fetchall() or []:
                zkjd = (row[0] or "").strip()
                if not zkjd:
                    continue
                if zkjd.endswith(TEMPLATE_ZKJD_SUFFIX):
                    continue
                out.add(normalize_zkjd(zkjd))
    except Exception as e:
        logger.warning(f"[workstation] 加载用户质控阶段失败 gdid={gdid}: {e}")
    return out


def _yhbc_from_stage_val(node_name: str, stage_val: Any) -> Optional[bool]:
    """从 stage_data 业务对象上读取 F_YHBC；读不到返回 None。"""
    if stage_val is None:
        return False

    if node_name == "观察":
        # detail-by-gdid 结构：{order, observations/GCJL, ...} 或历史 list
        if isinstance(stage_val, list):
            flags = [as_bool(r.get("F_YHBC")) for r in stage_val if isinstance(r, dict)]
            known = [f for f in flags if f is not None]
            if known:
                return any(known)
            return None
        if isinstance(stage_val, dict):
            obs = stage_val.get("observations") or stage_val.get("GCJL") or stage_val.get("gcjl") or []
            if isinstance(obs, list) and obs:
                flags = [as_bool(r.get("F_YHBC")) for r in obs if isinstance(r, dict)]
                known = [f for f in flags if f is not None]
                if known:
                    return any(known)
            return as_bool(stage_val.get("F_YHBC"))

    if node_name == "验证":
        if isinstance(stage_val, dict):
            zkjl_list = stage_val.get("zkjl_list") or []
            for r in zkjl_list:
                if not isinstance(r, dict):
                    continue
                flag = as_bool(r.get("F_YHBC"))
                if flag is True:
                    return True
                # 兼容旧逻辑：用户暂存会写 F_GXSJ，模板占位不写
                if r.get("F_GXSJ"):
                    return True
            flags = [as_bool(r.get("F_YHBC")) for r in zkjl_list if isinstance(r, dict)]
            known = [f for f in flags if f is not None]
            if known:
                return any(known)
            return False
        return None

    if node_name == "冻存记录":
        if isinstance(stage_val, dict):
            dcjl = stage_val.get("DCJL") or stage_val
            return as_bool(dcjl.get("F_YHBC")) if isinstance(dcjl, dict) else None

    if node_name in ("病理", "测序", "其他"):
        if isinstance(stage_val, dict):
            files = stage_val.get("files") or []
            bz = (stage_val.get("bz") or "").strip()
            return bool(files) or bool(bz)
        return False

    if node_name == "代次管理":
        return bool(stage_val) if isinstance(stage_val, list) else stage_val is not None

    if node_name == "入库":
        return False

    if isinstance(stage_val, dict):
        return as_bool(stage_val.get("F_YHBC"))
    return None


def _resolve_node_user_saved(
    node_name: str,
    stage_val: Any,
    user_saved_zkjds: Set[str],
) -> bool:
    """
    判断节点是否有用户行为下的保存。
    优先业务表 F_YHBC；否则用非模板质控记录兜底；再否则：
    - 仅有模板数据 → False
    - 存量无标记但有数据 → True（兼容历史）
    """
    yhbc = _yhbc_from_stage_val(node_name, stage_val)
    if yhbc is not None:
        return yhbc

    zkjd = _NODE_TO_ZKJD.get(node_name)
    if zkjd and zkjd in user_saved_zkjds:
        return True

    # 动物实验记录还可能写「动物实验终点记录」
    if node_name == "动物实验记录" and "动物实验终点记录" in user_saved_zkjds:
        return True

    if stage_val is None:
        return False

    # 有模板质控但无用户质控 → 视为未用户保存（由调用方已排除模板）
    # 这里 stage 有值但无用户 QC：更可能是模板预填
    if zkjd:
        return False

    # 无映射节点：有数据即 true
    return True


def _extract_node_meta(
    node_name: str,
    stage_val: Any,
    user_saved_zkjds: Optional[Set[str]] = None,
) -> Dict[str, Any]:
    """
    从某节点的 stage_data 值中提取操作人（node_operator）与记录时间（node_time）。

    返回：{
      node_operator, node_time, node_has_data,
      node_user_saved  # True=用户保存过；False=仅模板预填/无数据
    }
    """
    meta: Dict[str, Any] = {
        "node_operator": None,
        "node_time": None,
        "node_has_data": stage_val is not None,
        "node_user_saved": _resolve_node_user_saved(
            node_name, stage_val, user_saved_zkjds or set()
        ),
    }
    if stage_val is None:
        return meta

    if node_name == "预处理":
        # stage_val: T_YCL dict（含 F_CZR/F_CZSJ 若已迁移，否则退化到 F_KSCLSJ）
        meta["node_operator"] = stage_val.get("F_CZR")
        meta["node_time"] = stage_val.get("F_CZSJ") or stage_val.get("F_KSCLSJ")
        # 若 F_CZR 列尚未建好，从评分 JSON 的 operator 字段降级取用
        if not meta["node_operator"]:
            pfb = stage_val.get("F_YBPFB")
            if isinstance(pfb, dict):
                meta["node_operator"] = pfb.get("operator")

    elif node_name in _CULTURE_NODES:
        # stage_val: T_PYMX_ZZ dict（F_CZR / F_CZSJ 已存在）
        meta["node_operator"] = stage_val.get("F_CZR")
        meta["node_time"] = stage_val.get("F_CZSJ")

    elif node_name == "观察":
        # stage_val: list of T_GCJL rows
        # 取 F_GCSJ（实际观察时间）最新的一条；无 F_GCSJ 时以 F_JLRQ 兜底排序
        if isinstance(stage_val, list) and stage_val:
            def _obs_sort_key(rec: Dict[str, Any]):
                t = _parse_time(rec.get("F_GCSJ")) or _parse_time(rec.get("F_JLRQ"))
                return t if t is not None else datetime.min
            latest = max(stage_val, key=_obs_sort_key)
            meta["node_operator"] = latest.get("F_JLR")
            meta["node_time"] = latest.get("F_GCSJ") or latest.get("F_JLRQ")

    elif node_name == "验证":
        # stage_val: {"zksh_list": [...], "zkjl_list": [...]}
        # zksh_list 来自 T_ZKSH（质控审核单），其中混有各阶段记录；
        # 先筛出 F_ZKJD='验证单' 的记录，再优先取审核通过的那条用 F_SHSJ 作为节点时间。
        # 若尚未审核通过则降级取最新提交记录的登记时间。
        if isinstance(stage_val, dict):
            zksh_list = stage_val.get("zksh_list") or []
            zkjl_list = stage_val.get("zkjl_list") or []

            # 仅考虑验证单阶段的审核记录（按 F_ID 降序，最新在前）
            verify_records = sorted(
                [r for r in zksh_list if zkjd_matches(r.get("F_ZKJD") or "", "验证单")],
                key=lambda r: int(r.get("F_ID") or 0),
                reverse=True,
            )

            # 优先：审核通过的记录（取最新一条），时间 = F_SHSJ（审核通过时间）
            approved = next(
                (r for r in verify_records if (r.get("F_SHZT") or "") == "审核通过"),
                None,
            )
            if approved:
                meta["node_operator"] = approved.get("F_JLR")
                meta["node_time"] = approved.get("F_SHSJ")
            elif verify_records:
                # 降级：取最新提交的验证单，时间 = 登记/提交时间
                meta["node_operator"] = verify_records[0].get("F_JLR")
                meta["node_time"] = verify_records[0].get("F_JLSJ") or verify_records[0].get("F_DJRQ")

            # 最终 operator 仍空时从质控记录（T_ZKJL）补充
            if not meta["node_operator"] and zkjl_list:
                meta["node_operator"] = zkjl_list[0].get("F_JLR")

    elif node_name == "动物实验计划":
        # stage_val: T_DWSY_JH detail dict（含 F_SYR 实验人、F_CJSJ 创建时间）
        if isinstance(stage_val, dict):
            meta["node_operator"] = stage_val.get("F_SYR")
            meta["node_time"] = stage_val.get("F_CJSJ")
            if not meta["node_operator"]:
                gdxx = stage_val.get("GDXX") or {}
                meta["node_operator"] = gdxx.get("F_SQR")

    elif node_name == "动物实验记录":
        # stage_val: {"plan_info": {...}, "gcjl_list": [...], ...}
        if isinstance(stage_val, dict):
            gcjl = stage_val.get("gcjl_list") or []
            if gcjl:
                first = gcjl[0]
                meta["node_operator"] = first.get("F_JLR")
                meta["node_time"] = first.get("F_JLRQ") or first.get("F_CJSJ")

    elif node_name == "药物实验计划":
        # stage_val: T_SYJH detail dict（含 F_SYR 实验人、F_CJSJ 创建时间）
        if isinstance(stage_val, dict):
            meta["node_operator"] = stage_val.get("F_SYR")
            meta["node_time"] = stage_val.get("F_CJSJ")
            if not meta["node_operator"]:
                gdxx = stage_val.get("GDXX") or {}
                meta["node_operator"] = gdxx.get("F_SQR")

    elif node_name == "药物实验记录":
        # stage_val: T_SYJL dict（F_JLR 记录人、F_JLRQ 记录日期、F_CJSJ 创建时间）
        if isinstance(stage_val, dict):
            meta["node_operator"] = stage_val.get("F_JLR")
            meta["node_time"] = stage_val.get("F_JLRQ") or stage_val.get("F_CJSJ")

    elif node_name == "代次管理":
        # stage_val: list of child T_PYGD rows（含 F_SQR / F_CJSJ）
        if isinstance(stage_val, list) and stage_val:
            first = stage_val[0]
            meta["node_operator"] = first.get("F_SQR")
            meta["node_time"] = first.get("F_CJSJ") or first.get("F_PYSJ")

    elif node_name == "冻存记录":
        # stage_val: {"GDXX": {...}, "DCJL": {...}, "XHMSYJL": [...]}
        if isinstance(stage_val, dict):
            dcjl = stage_val.get("DCJL") or {}
            meta["node_operator"] = dcjl.get("F_CZR")
            meta["node_time"] = dcjl.get("F_DCRQ")

    # 统一时间为字符串
    if meta["node_time"] is not None and hasattr(meta["node_time"], "strftime"):
        try:
            meta["node_time"] = meta["node_time"].strftime("%Y-%m-%d %H:%M:%S")
        except Exception:
            meta["node_time"] = str(meta["node_time"])

    return meta


def _enrich_nodes_with_meta(
    nodes: List[Dict[str, Any]],
    stage_data: Dict[str, Any],
    user_saved_zkjds: Optional[Set[str]] = None,
) -> List[Dict[str, Any]]:
    """
    将 node_operator / node_time / node_has_data / node_user_saved / node_interval_hours
    注入到 nodes 列表的每个节点对象中（原地修改并返回）。

    node_user_saved：是否存在用户行为下的保存（模板预填为 false）。
    node_interval_hours：当前节点与上一个有有效时间记录的节点之间的小时差，
                         首个节点或无法计算时为 null。
    """
    prev_dt: Optional[datetime] = None
    saved = user_saved_zkjds or set()

    for node in nodes:
        name = node.get("F_JD_LX") or ""
        meta = _extract_node_meta(name, stage_data.get(name), saved)
        node.update(meta)

        cur_dt = _parse_time(node.get("node_time"))
        if cur_dt is not None and prev_dt is not None:
            delta = (cur_dt - prev_dt).total_seconds() / 3600
            node["node_interval_hours"] = round(delta, 2)
        else:
            node["node_interval_hours"] = None

        if cur_dt is not None:
            prev_dt = cur_dt

    return nodes


# ---------------------------------------------------------------------------
# 主入口
# ---------------------------------------------------------------------------
# 统一入参解析
# ---------------------------------------------------------------------------

def _resolve_gdbh(value: str) -> tuple[str, str]:
    """
    从统一入参 F_VALUE 解析出培养工单编号（F_GDBH）及匹配字段名。

    按以下顺序依次尝试匹配，返回第一个命中的结果：
      1. T_PYGD.F_GDBH  —— 培养工单号精确匹配
      2. T_PYGD.F_ZDYXH —— 自定义编号精确匹配（多条时取 F_PYSJ 最新的一条）
      3. T_YBZJ.F_SJTMH —— 送检条码号，取对应 F_ZZID 下 F_PYSJ 最新的培养工单
      4. T_YBZJ.F_ZZID  —— 标本号
      5. T_YBFZ.F_SGBH / F_ZZID —— 样本编号或标本号（标本直接分装无工单时给出明确提示）

    Parameters
    ----------
    value : 前端传入的统一查询值

    Returns
    -------
    (gdbh, matched_by)  matched_by 为 'F_GDBH' / 'F_ZDYXH' / 'F_SJTMH' / 'F_ZZID' / 'F_SGBH'

    Raises
    ------
    ValueError  未找到任何匹配时抛出；样本/标本存在但未关联培养工单时抛出明确提示
    """
    adapter = get_adapter()
    v = value.strip()
    no_order_msg = "该样本无关联工单，请重新输入"

    def _latest_gdbh_by_zzid(cur, zzid) -> Optional[str]:
        if zzid is None or str(zzid).strip() == "":
            return None
        sql, extra = adapter.paginate(
            "SELECT F_GDBH FROM T_PYGD WHERE F_ZZID = ?"
            " ORDER BY CASE WHEN F_PYSJ IS NULL THEN 1 ELSE 0 END ASC, F_PYSJ DESC, F_ID DESC",
            0, 1,
        )
        cur.execute(sql, [zzid] + extra)
        row = cur.fetchone()
        return row[0] if row else None

    with adapter.connect() as conn:
        cur = conn.cursor()

        # ── 策略 1：精确匹配培养工单号 ──────────────────────────────────────
        cur.execute("SELECT F_GDBH FROM T_PYGD WHERE F_GDBH = ?", (v,))
        row = cur.fetchone()
        if row:
            return row[0], "F_GDBH"

        # ── 策略 2：自定义编号（F_ZDYXH）────────────────────────────────────
        zdyxh_sql, zdyxh_extra = adapter.paginate(
            "SELECT F_GDBH FROM T_PYGD WHERE F_ZDYXH = ?"
            " ORDER BY CASE WHEN F_PYSJ IS NULL THEN 1 ELSE 0 END ASC, F_PYSJ DESC, F_ID DESC",
            0, 1,
        )
        cur.execute(zdyxh_sql, [v] + zdyxh_extra)
        row = cur.fetchone()
        if row:
            return row[0], "F_ZDYXH"

        # ── 策略 3：送检条码号（T_YBZJ.F_SJTMH）────────────────────────────
        cur.execute(
            "SELECT F_ZZID FROM T_YBZJ WHERE F_SJTMH = ? ORDER BY F_ID DESC",
            (v,),
        )
        zzid_row = cur.fetchone()
        if zzid_row:
            gdbh = _latest_gdbh_by_zzid(cur, zzid_row[0])
            if gdbh:
                return gdbh, "F_SJTMH"
            raise ValueError(no_order_msg)

        # ── 策略 4：标本号（T_YBZJ.F_ZZID）────────────────────────────────
        cur.execute(
            "SELECT F_ZZID FROM T_YBZJ WHERE F_ZZID = ? ORDER BY F_ID DESC",
            (v,),
        )
        zzid_row = cur.fetchone()
        if zzid_row:
            gdbh = _latest_gdbh_by_zzid(cur, zzid_row[0])
            if gdbh:
                return gdbh, "F_ZZID"
            raise ValueError(no_order_msg)

        # ── 策略 5：样本编号 / 标本号（T_YBFZ）──────────────────────────────
        # 标本直接分装：T_YBFZ 有记录但 F_PYGD_ID 为空
        ybfz_sql, ybfz_extra = adapter.paginate(
            "SELECT F_PYGD_ID, F_ZZID FROM T_YBFZ"
            " WHERE F_SGBH = ? OR F_ZZID = ?"
            " ORDER BY F_ID DESC",
            0, 1,
        )
        cur.execute(ybfz_sql, [v, v] + ybfz_extra)
        ybfz_row = cur.fetchone()
        if ybfz_row:
            pygd_id, zzid = ybfz_row[0], ybfz_row[1]
            if pygd_id:
                cur.execute("SELECT F_GDBH FROM T_PYGD WHERE F_ID = ?", (pygd_id,))
                row = cur.fetchone()
                if row:
                    return row[0], "F_SGBH"
            gdbh = _latest_gdbh_by_zzid(cur, zzid)
            if gdbh:
                return gdbh, "F_SGBH"
            raise ValueError(no_order_msg)

    raise ValueError(f"未找到匹配记录，请确认输入值是否正确：{value}")


# ---------------------------------------------------------------------------

def get_workstation_detail(value: str) -> Dict[str, Any]:
    """
    获取培养工单的快捷工位详情。

    Parameters
    ----------
    value : 统一查询字段，支持工单号 / 自定义编号 / 送检条码 / 样本编号

    Returns
    -------
    dict 包含：
        matched_by   : 实际命中的字段名（F_GDBH / F_ZDYXH / F_SJTMH / F_SGBH）
        order        : 工单基础信息
        pipeline_jm  : 建模阶段流水线模板 + 各节点业务数据（无模板时为 null）
        pipeline_lx  : 流向阶段流水线模板 + 各节点业务数据（无模板时为 null）
    """
    # 1. 解析 F_VALUE → F_GDBH
    gdbh, matched_by = _resolve_gdbh(value)

    # 2. 按工单编号查工单，取出内部 F_ID 供后续子查询使用
    order = _get_order_basic(gdbh)
    if not order:
        raise ValueError(f"培养工单不存在：F_GDBH={gdbh}")

    jm_pipeline_id = order.get("F_PIPELINE_JM")
    # F_PIPELINE_LX 现为 JSON 数组字符串，反序列化为 int 列表
    import json as _json

    def _parse_lx(raw) -> List[int]:
        if raw is None:
            return []
        if isinstance(raw, (int, float)):
            return [int(raw)]
        if isinstance(raw, list):
            return [int(i) for i in raw if i is not None]
        try:
            parsed = _json.loads(str(raw))
            if isinstance(parsed, list):
                return [int(i) for i in parsed if i is not None]
            return [int(parsed)]
        except Exception:
            try:
                return [int(raw)]
            except Exception:
                return []

    lx_pipeline_ids: List[int] = _parse_lx(order.get("F_PIPELINE_LX"))

    if not jm_pipeline_id and not lx_pipeline_ids:
        return {
            "matched_by": matched_by,
            "order": order,
            "pipeline_jm": None,
            "pipeline_lx": [],
            "message": "该工单未使用流水线模板",
        }

    gdid: int = order["F_ID"]
    result: Dict[str, Any] = {"matched_by": matched_by, "order": order}
    user_saved_zkjds = _load_qc_user_saved_stages(gdid)

    # 2. 建模阶段（单个）
    if jm_pipeline_id:
        pipeline_jm = _get_pipeline_with_nodes(int(jm_pipeline_id))
        if pipeline_jm:
            node_names: Set[str] = {
                (n.get("F_JD_LX") or "") for n in (pipeline_jm.get("nodes") or [])
            }
            stage_data = _fetch_jm_stage_data(gdid, node_names, order=order)
            enriched_nodes_jm = _enrich_nodes_with_meta(
                list(pipeline_jm.get("nodes") or []), stage_data, user_saved_zkjds
            )
            result["pipeline_jm"] = {
                **pipeline_jm,
                "nodes": enriched_nodes_jm,
                "stage_data": stage_data,
            }
        else:
            result["pipeline_jm"] = None
    else:
        result["pipeline_jm"] = None

    # 3. 流向阶段（多个，返回数组）
    pipeline_lx_list = []
    for lx_id in lx_pipeline_ids:
        pipeline_lx = _get_pipeline_with_nodes(int(lx_id))
        if pipeline_lx:
            node_names_lx: Set[str] = {
                (n.get("F_JD_LX") or "") for n in (pipeline_lx.get("nodes") or [])
            }
            stage_data_lx = _fetch_lx_stage_data(gdid, node_names_lx, order=order)
            enriched_nodes_lx = _enrich_nodes_with_meta(
                list(pipeline_lx.get("nodes") or []), stage_data_lx, user_saved_zkjds
            )
            pipeline_lx_list.append({
                **pipeline_lx,
                "nodes": enriched_nodes_lx,
                "stage_data": stage_data_lx,
            })
    result["pipeline_lx"] = pipeline_lx_list

    return result


def get_latest_culturing_order(user: Optional[Dict[str, Any]], scope: Optional[str]) -> Optional[Dict[str, Any]]:
    """当前用户数据权限下最新一条培养中工单。培养中即 F_GDZT=已创建。"""
    from app.core.data_scope import apply_owner_filter

    adapter = get_adapter()
    where = ["p.F_GDZT = ?"]
    params: List[Any] = ["已创建"]
    apply_owner_filter(where, params, scope=scope, user=user, column="p.F_SQR", match="name")
    sql = f"""
    SELECT p.F_ID, p.F_GDBH, p.F_ZZID, p.F_XM, p.F_BRBH, p.F_AZ, p.F_SQR,
           p.F_YBLY, p.F_ZDYXH, p.F_DC, p.F_PYSJ, p.F_GDZT, p.F_CJSJ,
           p.F_PIPELINE_JM, p.F_PIPELINE_LX
    FROM T_PYGD p
    WHERE {" AND ".join(where)}
    ORDER BY p.F_ID DESC
    """
    page_sql, extra = adapter.paginate(sql, skip=0, limit=1)
    rows, cols = adapter.fetch_all(page_sql, list(params) + list(extra))
    if not rows:
        return None
    record = dict(zip(cols, rows[0]))
    for key in ("F_PYSJ", "F_CJSJ"):
        value = record.get(key)
        if value is not None and hasattr(value, "strftime"):
            record[key] = value.strftime("%Y-%m-%d %H:%M:%S")
    return record
