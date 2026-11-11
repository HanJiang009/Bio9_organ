"""
动物实验记录模块 CRUD
包含：
  - 观察记录（T_DWSY_GCJL + T_DWSY_GCJL_ZB）：1:N per plan
  - 终点记录（T_DWSY_SYJL）：1:1 per plan
"""
import json
import logging
import copy
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from app.db.factory import get_adapter

logger = logging.getLogger(__name__)


# ==================== 工具函数 ====================

def _safe_encode_for_db(value: Any) -> Any:
    """将字符串中无法被 GBK 编码的特殊 Unicode 字符替换为兼容字符"""
    if not isinstance(value, str):
        return value
    replacements = {
        '×': 'x', '⁰': '0', '¹': '1', '²': '2', '³': '3', '⁴': '4',
        '⁵': '5', '⁶': '6', '⁷': '7', '⁸': '8', '⁹': '9',
        '₀': '0', '₁': '1', '₂': '2', '₃': '3', '₄': '4',
        '₅': '5', '₆': '6', '₇': '7', '₈': '8', '₉': '9',
        'μ': 'u', '±': '+/-', '°': 'deg', '≤': '<=', '≥': '>=', '≈': '~',
    }
    result = value
    for old, new in replacements.items():
        result = result.replace(old, new)
    try:
        result.encode('gbk')
        return result
    except UnicodeEncodeError:
        return result.encode('gbk', errors='ignore').decode('gbk')


def _clean_data_for_db(data: Any) -> Any:
    """递归清理 dict / list / str 中无法被 GBK 编码的特殊字符"""
    if isinstance(data, str):
        return _safe_encode_for_db(data)
    if isinstance(data, dict):
        return {k: _clean_data_for_db(v) for k, v in data.items()}
    if isinstance(data, list):
        return [_clean_data_for_db(item) for item in data]
    return data


def _parse_datetime(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d %H:%M:%S")
    return str(value)


def _dumps(val: Any) -> Optional[str]:
    if val is None:
        return None
    if isinstance(val, str):
        return val
    try:
        return json.dumps(val, ensure_ascii=False)
    except Exception:
        return None


def _loads(val: Any) -> Any:
    if val is None:
        return None
    if isinstance(val, (dict, list)):
        return val
    if isinstance(val, (bytes, bytearray)):
        try:
            val = val.decode("utf-8", errors="ignore")
        except Exception:
            return None
    if isinstance(val, str):
        try:
            return json.loads(val)
        except Exception:
            return val
    return val


def _normalize_fj_id(fj_val: Any) -> Optional[int]:
    """
    将 F_FJ 字段统一归一化为整数文件ID。
    支持三种来源格式：
      - int:  101                          （直接ID）
      - str:  "101"                        （字符串ID）
      - dict: {"id": 101, "path": "..."}  （详情接口返回的文件信息对象，前端原样回传时）
    """
    if fj_val is None:
        return None
    if isinstance(fj_val, int):
        return fj_val
    if isinstance(fj_val, str):
        try:
            return int(fj_val)
        except ValueError:
            return None
    if isinstance(fj_val, dict):
        # 详情接口返回格式：{"id": 101, "path": "...", "name": "..."}
        raw_id = fj_val.get("id") or fj_val.get("F_ID")
        if raw_id is not None:
            try:
                return int(raw_id)
            except (ValueError, TypeError):
                return None
    return None


def _replace_fj_index(fj_list: Any, file_ids: List[int]) -> List[Dict[str, Any]]:
    """
    处理组别观察的附件列表：
      1. 将 F_FJ_INDEX 替换为本次上传的实际文件ID（新上传）
      2. 将已有附件的 F_FJ 归一化为整数ID（兼容详情接口返回的文件对象格式）
      3. 不在列表中的附件视为已删除（隐式删除）

    支持的混合输入格式：
      [
        {"F_FJ_INDEX": 0, "F_BS": "40x"},                        # 新上传，按全局索引映射
        {"F_FJ": 101, "F_BS": "100x"},                           # 已有文件ID（保留）
        {"F_FJ": {"id": 101, "path": "...", "name": "..."},      # 详情原样回传（自动归一化）
         "F_BS": "200x"}
      ]

    输出格式（入库）：
      [{"F_FJ": <int_id>, "F_BS": "40x"}, ...]
    """
    if not fj_list or not isinstance(fj_list, list):
        return []
    result = []
    for item in fj_list:
        if not isinstance(item, dict):
            continue
        new_item = {k: v for k, v in item.items() if k not in ("F_FJ_INDEX", "F_FJ")}

        if "F_FJ_INDEX" in item:
            # 新上传文件：按全局索引映射
            idx = item["F_FJ_INDEX"]
            if isinstance(idx, int) and 0 <= idx < len(file_ids):
                new_item["F_FJ"] = file_ids[idx]
            else:
                logger.warning(f"[动物实验观察附件] 无效索引 {idx}，跳过")
                continue
        elif "F_FJ" in item:
            # 已有文件：归一化为整数ID（兼容详情对象格式）
            fid = _normalize_fj_id(item["F_FJ"])
            if fid is None:
                logger.warning(f"[动物实验观察附件] 无法解析文件ID: {item['F_FJ']}，跳过")
                continue
            new_item["F_FJ"] = fid
        else:
            # 既无 F_FJ_INDEX 也无 F_FJ，跳过
            continue

        result.append(new_item)
    return result


def _fj_ids_to_info(fj_list: Any) -> List[Dict[str, Any]]:
    """将组别观察 F_FJ 中的文件ID转为文件信息（path/name），保留 F_BS 字段"""
    if not fj_list or not isinstance(fj_list, list):
        return []
    try:
        from app.crud.file_center import get_files_by_ids
        ids = [int(item["F_FJ"]) for item in fj_list if isinstance(item, dict) and item.get("F_FJ")]
        if not ids:
            return []
        files_map = {f["F_ID"]: f for f in get_files_by_ids(ids)}
        result = []
        for item in fj_list:
            if not isinstance(item, dict) or not item.get("F_FJ"):
                continue
            fid = int(item["F_FJ"])
            file_info = files_map.get(fid)
            entry: Dict[str, Any] = {"F_BS": item.get("F_BS")}
            if file_info:
                entry["F_FJ"] = {
                    "id":   file_info.get("F_ID"),
                    "path": file_info.get("F_PATH"),
                    "name": file_info.get("F_ORIGINAL_NAME"),
                }
            else:
                entry["F_FJ"] = {"id": fid}
            result.append(entry)
        return result
    except Exception as e:
        logger.warning(f"[动物实验观察附件] 转换文件信息失败: {e}")
        return fj_list


def _endpoint_fj_to_info(fj_val: Any) -> List[Dict[str, Any]]:
    """将终点记录 F_FJ（文件ID数组）转为文件信息列表"""
    ids = _loads(fj_val) if isinstance(fj_val, str) else fj_val
    if not ids or not isinstance(ids, list):
        return []
    try:
        from app.crud.file_center import get_files_by_ids
        int_ids = [int(i) for i in ids if i]
        files = get_files_by_ids(int_ids)
        return [{"id": f.get("F_ID"), "path": f.get("F_PATH"), "name": f.get("F_ORIGINAL_NAME")} for f in files]
    except Exception as e:
        logger.warning(f"[动物实验终点附件] 转换文件信息失败: {e}")
        return []


def _get_plan_qc_context(plan_id: int) -> Optional[Dict[str, Any]]:
    """获取实验计划关联的工单与样本信息，用于写质控记录"""
    try:
        from app.crud.animal_test import get_animal_test_plan_detail
        from app.crud.qc_audit import get_sample_basic_info
        plan = get_animal_test_plan_detail(plan_id)
        if not plan:
            return None
        gdxx = plan.get("GDXX") or {}
        zzid = gdxx.get("F_ZZID")
        if not zzid:
            return None
        sample_info = get_sample_basic_info(zzid)
        return {
            "plan":        plan,
            "gdxx":        gdxx,
            "sample_info": sample_info,
            "zzid":        zzid,
            "gdid":        plan.get("F_GDID"),
        }
    except Exception as e:
        logger.warning(f"[质控上下文] 获取失败: {e}")
        return None


def _write_qc(ctx: Optional[Dict], zkjd: str, bglx: str,
              changes: Dict, operator: str, old_data: Any = None,
              from_pipeline_template: bool = False) -> Optional[int]:
    """写质控记录（失败不影响主流程）"""
    if not ctx:
        return None
    try:
        from app.crud.qc_audit import create_qc_audit_record
        gdxx = ctx["gdxx"]
        si   = ctx["sample_info"]
        return create_qc_audit_record(
            F_ZKJD=zkjd,
            F_BGLX=bglx,
            F_ZZID=ctx["zzid"],
            F_CHANGES=changes,
            F_JLR=operator or "系统",
            F_GDID=ctx["gdid"],
            F_XM=si.get("F_XM"),
            F_BRBH=si.get("F_BRBH"),
            F_YBLY=gdxx.get("F_YBLY"),
            F_GDBH=gdxx.get("F_GDBH"),
            F_DCID=gdxx.get("F_DCID"),
            F_DC=gdxx.get("F_DC"),
            F_PYSJ=gdxx.get("F_PYSJ"),
            F_SQR=gdxx.get("F_SQR"),
            F_OLD_DATA=old_data,
            from_pipeline_template=from_pipeline_template,
        )
    except Exception as e:
        logger.error(f"[质控记录] 写入失败: {e}", exc_info=True)
        return None


# ==================== 观察记录：查询 ====================

def _row_to_gcjl(row: tuple, cols: List[str]) -> Dict[str, Any]:
    d = dict(zip(cols, row))
    d["F_GCSJ"]  = _parse_datetime(d.get("F_GCSJ"))
    d["F_JLRQ"]  = _parse_datetime(d.get("F_JLRQ"))
    d["F_CJSJ"]  = _parse_datetime(d.get("F_CJSJ"))
    d["F_GXSJ"]  = _parse_datetime(d.get("F_GXSJ"))
    return d


def _cell_str(row: Any, idx: int) -> str:
    if not isinstance(row, (list, tuple)) or idx >= len(row):
        return ""
    v = row[idx]
    if v is None:
        return ""
    return str(v).strip()


def _get_plan_group_names(plan_id: int, cur=None) -> List[str]:
    """接种记录按 F_SORT 顺序的组别名（含空串，不去重；空则后续再兜底）。"""
    sql = """
        SELECT F_ZB FROM T_DWSY_JZ
        WHERE F_SYJH_ID = ?
        ORDER BY F_SORT, F_ID
    """
    rows: List[Any] = []
    if cur is not None:
        cur.execute(sql, (int(plan_id),))
        rows = cur.fetchall() or []
    else:
        adapter = get_adapter()
        with adapter.connect() as conn:
            c = conn.cursor()
            c.execute(sql, (int(plan_id),))
            rows = c.fetchall() or []

    return [_cell_str(r, 0) for r in rows]


def _unique_group_names(names: List[str]) -> List[str]:
    out: List[str] = []
    seen = set()
    for n in names or []:
        s = (n or "").strip()
        if not s or s in seen:
            continue
        seen.add(s)
        out.append(s)
    return out


def _fill_zbxx_f_zb(zbxx: Optional[List[Dict[str, Any]]], group_names: List[str]) -> None:
    """就地补全 ZBXX 中缺失的 F_ZB（兼容误传 F_ZBMC；查不到真实组别名则保持 null）。"""
    if not zbxx or not isinstance(zbxx, list):
        return
    unique_names = _unique_group_names(group_names)
    for idx, zb in enumerate(zbxx):
        if not isinstance(zb, dict):
            continue
        cur_zb = zb.get("F_ZB")
        if (cur_zb is None or str(cur_zb).strip() == "") and zb.get("F_ZBMC") not in (None, ""):
            zb["F_ZB"] = zb.get("F_ZBMC")
            cur_zb = zb.get("F_ZB")
        if cur_zb is not None and str(cur_zb).strip() != "":
            continue

        name_idx = idx
        sort_val = zb.get("F_SORT")
        if sort_val is not None:
            try:
                si = int(sort_val) - 1
                if si >= 0:
                    name_idx = si
            except Exception:
                pass

        name = ""
        if unique_names:
            if 0 <= name_idx < len(unique_names):
                name = unique_names[name_idx]
            elif 0 <= idx < len(unique_names):
                name = unique_names[idx]
            else:
                name = unique_names[-1]
        if not name and group_names:
            if 0 <= name_idx < len(group_names) and group_names[name_idx]:
                name = group_names[name_idx]
            elif 0 <= idx < len(group_names) and group_names[idx]:
                name = group_names[idx]
        zb["F_ZB"] = name if name else None


def enrich_observation_zbxx_f_zb(gcjl: Dict[str, Any], cur=None, plan_id: Any = None) -> Dict[str, Any]:
    """为单条观察记录补全 ZBXX.F_ZB（读详情 / 质控反显共用）。"""
    if not isinstance(gcjl, dict):
        return gcjl
    zbxx = gcjl.get("ZBXX")
    if not isinstance(zbxx, list) or not zbxx:
        return gcjl
    need_fill = any(
        isinstance(z, dict) and (z.get("F_ZB") is None or str(z.get("F_ZB")).strip() == "")
        for z in zbxx
    )
    if not need_fill:
        return gcjl
    pid = gcjl.get("F_SYJH_ID")
    if pid is None:
        pid = plan_id
    names: List[str] = []
    if pid is not None:
        try:
            names = _get_plan_group_names(int(pid), cur=cur)
        except Exception as e:
            logger.warning(f"[观察记录] 读取接种组别失败 plan_id={pid}: {e}")
    _fill_zbxx_f_zb(zbxx, names)
    return gcjl


def enrich_animal_obs_f_zb_in_payload(data: Dict[str, Any]) -> Dict[str, Any]:
    """补全质控 F_OLD_DATA / 整页快照中的观察组别 F_ZB。"""
    if not isinstance(data, dict):
        return data
    plan_id = data.get("F_SYJH_ID")
    plan_info = data.get("plan_info")
    if plan_id is None and isinstance(plan_info, dict):
        plan_id = plan_info.get("F_ID") or plan_info.get("F_SYJH_ID")
    if "ZBXX" in data:
        enrich_observation_zbxx_f_zb(data, plan_id=plan_id)
    gcjl_list = data.get("gcjl_list")
    if isinstance(gcjl_list, list):
        for item in gcjl_list:
            if isinstance(item, dict):
                enrich_observation_zbxx_f_zb(item, plan_id=plan_id)
    return data


def _fetch_zbxx_for_gcjl(cur, gcjl_id: int, resolve_files: bool = True) -> List[Dict[str, Any]]:
    """查询某观察记录下的所有组别观察数据"""
    cur.execute("""
        SELECT F_ID, F_GCJL_ID, F_ZB, F_XSYS, F_HDD, F_YCBX,
               F_XSTZ, F_ZLDX, F_TJJS, F_YZWZSFYC, F_FJ, F_BZ, F_SORT
        FROM T_DWSY_GCJL_ZB
        WHERE F_GCJL_ID = ?
        ORDER BY F_SORT, F_ID
    """, (gcjl_id,))
    rows = cur.fetchall()
    result = []
    for r in rows:
        item = {
            "F_ID":        r[0],
            "F_GCJL_ID":   r[1],
            "F_ZB":        r[2],
            "F_XSYS":      r[3],
            "F_HDD":       r[4],
            "F_YCBX":      r[5],
            "F_XSTZ":      r[6],
            "F_ZLDX":      r[7],
            "F_TJJS":      r[8],
            "F_YZWZSFYC":  r[9],
            "F_FJ":        _loads(r[10]),
            "F_BZ":        r[11],
            "F_SORT":      r[12],
        }
        if resolve_files:
            item["F_FJ"] = _fj_ids_to_info(item["F_FJ"])
        result.append(item)
    return result


def get_observation_detail(gcjl_id: int) -> Optional[Dict[str, Any]]:
    """获取单条观察记录详情（含各组别数据）"""
    adapter = get_adapter()
    with adapter.connect() as conn:
        cur = conn.cursor()
        cur.execute("""
            SELECT F_ID, F_SYJH_ID, F_GCSJ, F_PYDS, F_JLRQ, F_JLR, F_SORT, F_CJSJ, F_GXSJ
            FROM T_DWSY_GCJL WHERE F_ID = ?
        """, (gcjl_id,))
        row = cur.fetchone()
        if not row:
            return None
        cols = [c[0] for c in cur.description]
        gcjl = _row_to_gcjl(row, cols)
        gcjl["ZBXX"] = _fetch_zbxx_for_gcjl(cur, gcjl_id)
        enrich_observation_zbxx_f_zb(gcjl, cur=cur)
        return gcjl


def list_observations(plan_id: int) -> Tuple[List[Dict[str, Any]], int]:
    """获取实验计划的所有观察记录列表，按培养天数升序"""
    adapter = get_adapter()
    with adapter.connect() as conn:
        cur = conn.cursor()
        cur.execute("""
            SELECT F_ID, F_SYJH_ID, F_GCSJ, F_PYDS, F_JLRQ, F_JLR, F_SORT, F_CJSJ, F_GXSJ
            FROM T_DWSY_GCJL
            WHERE F_SYJH_ID = ?
            ORDER BY F_PYDS ASC, F_SORT ASC, F_ID ASC
        """, (plan_id,))
        rows = cur.fetchall()
        cols = [c[0] for c in cur.description]
        group_names = _get_plan_group_names(plan_id, cur=cur)
        items = []
        for row in rows:
            gcjl = _row_to_gcjl(row, cols)
            gcjl["ZBXX"] = _fetch_zbxx_for_gcjl(cur, gcjl["F_ID"])
            _fill_zbxx_f_zb(gcjl.get("ZBXX"), group_names)
            items.append(gcjl)
        return items, len(items)


# ==================== 观察记录：保存（新增 / 更新）====================

def save_observation(
    plan_id: int,
    gcjl_id: Optional[int],
    header: Dict[str, Any],
    zbxx: List[Dict[str, Any]],
    file_ids: List[int],
    operator: Optional[str] = None,
    skip_qc: bool = False,
    from_pipeline_template: bool = False,
) -> Dict[str, Any]:
    """
    保存观察记录。
    - gcjl_id 为 None → 新增
    - gcjl_id 有值  → 更新（ZBXX 整体覆盖替换）
    - skip_qc=True  → 不写质控记录（由上层统一写）
    """
    adapter = get_adapter()
    now = datetime.now()
    is_create = (gcjl_id is None)

    # 获取修改前数据（用于质控对比）
    old_detail = None if is_create else get_observation_detail(gcjl_id)

    # 处理各组别附件（替换 F_FJ_INDEX → 实际文件ID）
    for zb in zbxx:
        fj_raw = zb.get("F_FJ") or []
        zb["F_FJ"] = _replace_fj_index(fj_raw if isinstance(fj_raw, list) else _loads(fj_raw), file_ids)

    # 前端漏传 F_ZB 时，用计划接种组别名回填后再落库
    _fill_zbxx_f_zb(zbxx, _get_plan_group_names(plan_id))

    with adapter.connect() as conn:
        cur = conn.cursor()

        if is_create:
            # 检查计划是否存在
            cur.execute("SELECT F_ZT FROM T_DWSY_JH WHERE F_ID = ?", (plan_id,))
            plan_row = cur.fetchone()
            if not plan_row:
                raise ValueError("实验计划不存在")

            # 插入观察记录头
            sort_val = header.get("F_PYDS") or 0
            cur.execute("""
                INSERT INTO T_DWSY_GCJL (F_SYJH_ID, F_GCSJ, F_PYDS, F_JLRQ, F_JLR, F_SORT, F_CJSJ, F_GXSJ)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                plan_id,
                header.get("F_GCSJ"),
                header.get("F_PYDS"),
                header.get("F_JLRQ"),
                header.get("F_JLR") or operator,
                sort_val,
                now, now,
            ))
            cur.execute("SELECT MAX(F_ID) FROM T_DWSY_GCJL WHERE F_SYJH_ID = ?", (plan_id,))
            gcjl_id = cur.fetchone()[0]

            # 计划状态：已创建 → 进行中
            if plan_row[0] == "已创建":
                cur.execute("UPDATE T_DWSY_JH SET F_ZT = '进行中' WHERE F_ID = ?", (plan_id,))
                logger.info(f"[动物实验] 计划ID={plan_id} 状态更新为进行中")
        else:
            # 更新观察记录头
            cur.execute("SELECT F_ID FROM T_DWSY_GCJL WHERE F_ID = ? AND F_SYJH_ID = ?", (gcjl_id, plan_id))
            if not cur.fetchone():
                raise ValueError(f"观察记录 ID={gcjl_id} 不存在或不属于该实验计划")

            upd_fields: Dict[str, Any] = {"F_GXSJ": now}
            for f in ("F_GCSJ", "F_PYDS", "F_JLRQ", "F_JLR"):
                if header.get(f) is not None:
                    upd_fields[f] = header[f]
            if len(upd_fields) > 1:
                set_clause = ", ".join(f"{k}=?" for k in upd_fields)
                cur.execute(
                    f"UPDATE T_DWSY_GCJL SET {set_clause} WHERE F_ID=?",
                    list(upd_fields.values()) + [gcjl_id]
                )

            # 整体覆盖 ZBXX
            cur.execute("DELETE FROM T_DWSY_GCJL_ZB WHERE F_GCJL_ID = ?", (gcjl_id,))

        # 插入组别观察数据
        for idx, zb in enumerate(zbxx):
            zb = _clean_data_for_db(zb)   # 清理 ³ μ × 等 GBK 不兼容字符
            sort_val = zb.get("F_SORT") if zb.get("F_SORT") is not None else (idx + 1)
            cur.execute("""
                INSERT INTO T_DWSY_GCJL_ZB
                    (F_GCJL_ID, F_ZB, F_XSYS, F_HDD, F_YCBX, F_XSTZ, F_ZLDX, F_TJJS, F_YZWZSFYC, F_FJ, F_BZ, F_SORT)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                gcjl_id,
                zb.get("F_ZB"),
                zb.get("F_XSYS"),
                zb.get("F_HDD"),
                zb.get("F_YCBX"),
                zb.get("F_XSTZ"),
                zb.get("F_ZLDX"),
                zb.get("F_TJJS"),
                zb.get("F_YZWZSFYC"),
                _dumps(zb.get("F_FJ") or []),
                zb.get("F_BZ"),
                sort_val,
            ))

        conn.commit()

    # ===== 质控记录 =====
    new_detail = get_observation_detail(gcjl_id)

    if not skip_qc:
        ctx = _get_plan_qc_context(plan_id)
        if is_create:
            changes = {
                "变更字段": [
                    {"field": "操作", "old_value": "", "new_value": "新增观察记录"},
                    {"field": "培养天数", "old_value": "", "new_value": header.get("F_PYDS")},
                    {"field": "观察组别数", "old_value": "", "new_value": len(zbxx)},
                ]
            }
            _write_qc(ctx, "动物实验观察记录", "新增", changes, operator or "系统", old_data=new_detail, from_pipeline_template=from_pipeline_template)
        else:
            try:
                from app.crud.qc_audit import calculate_changes
                def _strip_ids(data):
                    d = copy.deepcopy(data)
                    for zb in (d.get("ZBXX") or []):
                        zb.pop("F_ID", None)
                        zb.pop("F_GCJL_ID", None)
                    return d
                changes = calculate_changes(_strip_ids(old_detail), _strip_ids(new_detail))
            except Exception:
                changes = {"变更字段": [{"field": "操作", "old_value": "", "new_value": "修改观察记录"}]}
            _write_qc(ctx, "动物实验观察记录", "修改", changes, operator or "系统", old_data=old_detail, from_pipeline_template=from_pipeline_template)

    return new_detail


# ==================== 观察记录：删除 ====================

def delete_observation(gcjl_id: int, plan_id: int, operator: Optional[str] = None,
                       skip_qc: bool = False) -> bool:
    """删除观察记录（级联删除组别观察数据）。skip_qc=True 时不写质控记录。"""
    adapter = get_adapter()

    old_detail = get_observation_detail(gcjl_id)
    if not old_detail:
        raise ValueError("观察记录不存在")
    if old_detail.get("F_SYJH_ID") != plan_id:
        raise ValueError("观察记录不属于该实验计划")

    with adapter.connect() as conn:
        cur = conn.cursor()
        # T_DWSY_GCJL_ZB 通过外键级联删除
        cur.execute("DELETE FROM T_DWSY_GCJL WHERE F_ID = ?", (gcjl_id,))
        conn.commit()

    # ===== 质控记录 =====
    if not skip_qc:
        ctx = _get_plan_qc_context(plan_id)
        changes = {
            "变更字段": [
                {"field": "操作", "old_value": "", "new_value": "删除观察记录"},
                {"field": "培养天数", "old_value": old_detail.get("F_PYDS"), "new_value": ""},
                {"field": "观察组别数", "old_value": len(old_detail.get("ZBXX") or []), "new_value": ""},
            ]
        }
        _write_qc(ctx, "动物实验观察记录", "删除", changes, operator or "系统", old_data=old_detail)
    return True


# ==================== 终点记录：查询 ====================

def get_endpoint_record(plan_id: int) -> Optional[Dict[str, Any]]:
    """获取实验终点记录详情"""
    adapter = get_adapter()
    with adapter.connect() as conn:
        cur = conn.cursor()
        cur.execute("""
            SELECT F_ID, F_SYJH_ID, F_SYZQ, F_YZZZQK, F_FJ, F_SYJL, F_JLR, F_JLRQ, F_CJSJ, F_GXSJ
            FROM T_DWSY_SYJL WHERE F_SYJH_ID = ?
        """, (plan_id,))
        row = cur.fetchone()
        if not row:
            return None
        cols = [c[0] for c in cur.description]
        d = dict(zip(cols, row))
        d["F_JLRQ"] = _parse_datetime(d.get("F_JLRQ"))
        d["F_CJSJ"] = _parse_datetime(d.get("F_CJSJ"))
        d["F_GXSJ"] = _parse_datetime(d.get("F_GXSJ"))
        d["F_FJ"]   = _endpoint_fj_to_info(d.get("F_FJ"))
        return d


def _get_default_syzq(plan_id: int) -> Optional[str]:
    """取该计划最后一条观察记录的培养天数作为默认实验周期"""
    adapter = get_adapter()
    rows, _ = adapter.fetch_all(
        "SELECT MAX(F_PYDS) FROM T_DWSY_GCJL WHERE F_SYJH_ID = ?",
        [plan_id]
    )
    val = rows[0][0] if rows else None
    return str(val) if val is not None else None


# ==================== 终点记录：保存（新增 / 更新）====================

def save_endpoint_record(
    plan_id: int,
    data: Dict[str, Any],
    new_file_ids: List[int],
    operator: Optional[str] = None,
    skip_qc: bool = False,
    from_pipeline_template: bool = False,
) -> Dict[str, Any]:
    """
    保存实验终点记录（自动判断新增 / 更新）。

    附件策略：
      - data["F_FJ"] 为已有文件ID列表（保留）
      - new_file_ids 为本次新上传的文件ID列表（追加）
      - 最终入库 = 已有 + 新上传
    - skip_qc=True 时不写质控记录（由上层统一写）
    """
    adapter = get_adapter()
    now = datetime.now()

    rows, _ = adapter.fetch_all(
        "SELECT F_ID, F_JLR, F_FJ FROM T_DWSY_SYJL WHERE F_SYJH_ID = ?",
        [plan_id]
    )
    record_id    = rows[0][0] if rows else None
    is_create    = (record_id is None)

    old_detail = None if is_create else get_endpoint_record(plan_id)

    # 合并附件：已有（保留） + 本次上传（新增）
    # 兼容两种格式：
    #   - int / "101"                         → 直接文件ID
    #   - {"id": 101, "path": "...", ...}     → 详情接口返回的对象，前端原样回传
    kept_ids: List[int] = []
    raw_fj = data.get("F_FJ")
    if raw_fj:
        if isinstance(raw_fj, str):
            raw_fj = _loads(raw_fj)
        for item in (raw_fj or []):
            fid = _normalize_fj_id(item)
            if fid is not None:
                kept_ids.append(fid)
    final_fj_ids = kept_ids + new_file_ids

    # 清理 GBK 不兼容字符
    data = _clean_data_for_db(data)

    # 实验周期：若未传则取最后一条观察记录的培养天数
    syzq = data.get("F_SYZQ") or (None if not is_create else _get_default_syzq(plan_id))

    with adapter.connect() as conn:
        cur = conn.cursor()

        if is_create:
            cur.execute("SELECT F_ZT FROM T_DWSY_JH WHERE F_ID = ?", (plan_id,))
            plan_row = cur.fetchone()
            if not plan_row:
                raise ValueError("实验计划不存在")

            cur.execute("""
                INSERT INTO T_DWSY_SYJL
                    (F_SYJH_ID, F_SYZQ, F_YZZZQK, F_FJ, F_SYJL, F_JLR, F_JLRQ, F_CJSJ, F_GXSJ)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                plan_id,
                syzq,
                data.get("F_YZZZQK"),
                _dumps(final_fj_ids),
                data.get("F_SYJL"),
                operator,                              # 记录人首次写入，不可更改
                data.get("F_JLRQ") or now,
                now, now,
            ))
            cur.execute("SELECT MAX(F_ID) FROM T_DWSY_SYJL WHERE F_SYJH_ID = ?", (plan_id,))
            record_id = cur.fetchone()[0]
            # 状态变更由"提交复核"接口负责，保存终点记录不改变计划状态
        else:
            upd: Dict[str, Any] = {"F_GXSJ": now, "F_FJ": _dumps(final_fj_ids)}
            if syzq is not None:
                upd["F_SYZQ"] = syzq
            for f in ("F_YZZZQK", "F_SYJL"):
                if data.get(f) is not None:
                    upd[f] = data[f]
            if data.get("F_JLRQ") is not None:
                upd["F_JLRQ"] = data["F_JLRQ"]
            # F_JLR 不更新（保留首次写入的记录人）
            set_clause = ", ".join(f"{k}=?" for k in upd)
            cur.execute(
                f"UPDATE T_DWSY_SYJL SET {set_clause} WHERE F_ID=?",
                list(upd.values()) + [record_id]
            )

        conn.commit()

    # ===== 质控记录 =====
    new_detail = get_endpoint_record(plan_id)

    if not skip_qc:
        ctx = _get_plan_qc_context(plan_id)
        if is_create:
            changes = {
                "变更字段": [
                    {"field": "操作", "old_value": "", "new_value": "新增实验终点记录"},
                    {"field": "实验周期", "old_value": "", "new_value": syzq},
                    {"field": "记录人", "old_value": "", "new_value": operator},
                ]
            }
            _write_qc(ctx, "动物实验终点记录", "新增", changes, operator or "系统", old_data=new_detail, from_pipeline_template=from_pipeline_template)
        else:
            try:
                from app.crud.qc_audit import calculate_changes
                def _mask_fj(d):
                    if not d:
                        return d
                    m = copy.deepcopy(d)
                    if m.get("F_FJ"):
                        m["F_FJ"] = [item.get("id") for item in m["F_FJ"] if isinstance(item, dict)]
                    return m
                changes = calculate_changes(_mask_fj(old_detail), _mask_fj(new_detail))
            except Exception:
                changes = {"变更字段": [{"field": "操作", "old_value": "", "new_value": "修改实验终点记录"}]}
            _write_qc(ctx, "动物实验终点记录", "修改", changes, operator or "系统", old_data=old_detail, from_pipeline_template=from_pipeline_template)

    return new_detail


# ==================== 统一接口：整页查询 / 整页保存 ====================

def get_experiment_record(plan_id: int) -> Dict[str, Any]:
    """
    统一详情：一次返回该实验计划的所有观察记录 + 终点记录。
    供"实验记录"页面初始化加载使用。
    """
    gcjl_list, total = list_observations(plan_id)
    endpoint = get_endpoint_record(plan_id)
    default_syzq = None
    if not endpoint:
        default_syzq = _get_default_syzq(plan_id)

    # 实验计划基础信息
    plan_info = None
    try:
        from app.crud.animal_test import get_animal_test_plan_detail
        plan_info = get_animal_test_plan_detail(plan_id)
    except Exception as e:
        logger.warning(f"[统一详情] 获取实验计划信息失败: {e}")

    return {
        "plan_info":  plan_info,
        "gcjl_list":  gcjl_list,
        "gcjl_total": total,
        "endpoint":   endpoint or {
            "F_SYJH_ID": plan_id,
            "F_SYZQ":    default_syzq,
            "F_FJ":      [],
        },
    }


def save_experiment_record(
    plan_id: int,
    gcjl_list: List[Dict[str, Any]],
    gcjl_del_ids: List[int],
    endpoint_data: Dict[str, Any],
    gcjl_file_ids: List[int],
    endpoint_file_ids: List[int],
    operator: Optional[str] = None,
    skip_qc: bool = False,
) -> Dict[str, Any]:
    """
    统一保存：一次提交观察记录变更（新增/更新/删除）+ 终点记录。
    供"实验记录"页面"确认"按钮使用。

    参数说明：
      gcjl_list         - 当前观察记录列表。
                          有 F_GCJL_ID → 更新；无 F_GCJL_ID → 新增。
      gcjl_del_ids      - 需要删除的观察记录 ID 列表。
      endpoint_data     - 终点记录表单数据（同 save_endpoint_record 的 data 参数）。
      gcjl_file_ids     - 本次上传的观察附件文件ID列表（全局索引，映射至各 ZBXX 中的 F_FJ_INDEX）。
      endpoint_file_ids - 本次上传的终点记录附件文件ID列表。
      skip_qc           - 为 True 时所有子操作均不写质控记录，由调用方统一写（如提交复核）。
    """
    # 1. 删除标记删除的观察记录
    for del_id in (gcjl_del_ids or []):
        try:
            delete_observation(del_id, plan_id, operator, skip_qc=skip_qc)
        except Exception as e:
            logger.warning(f"[统一保存] 删除观察记录 ID={del_id} 失败: {e}")

    # 2. 新增 / 更新观察记录
    for gcjl in (gcjl_list or []):
        gcjl_id = gcjl.get("F_GCJL_ID") or gcjl.get("F_ID")
        header = {
            "F_GCSJ": gcjl.get("F_GCSJ"),
            "F_PYDS": gcjl.get("F_PYDS"),
            "F_JLRQ": gcjl.get("F_JLRQ"),
            "F_JLR":  gcjl.get("F_JLR"),
        }
        zbxx = gcjl.get("ZBXX") or []
        try:
            save_observation(plan_id, gcjl_id, header, zbxx, gcjl_file_ids, operator,
                             skip_qc=skip_qc)
        except Exception as e:
            logger.error(f"[统一保存] 保存观察记录失败: {e}", exc_info=True)
            raise

    # 3. 保存终点记录
    endpoint_result = save_endpoint_record(plan_id, endpoint_data, endpoint_file_ids, operator,
                                           skip_qc=skip_qc)

    # 4. 返回最新整页数据
    gcjl_list_new, gcjl_total = list_observations(plan_id)
    return {
        "gcjl_list":  gcjl_list_new,
        "gcjl_total": gcjl_total,
        "endpoint":   endpoint_result,
    }
