from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple
from app.db.factory import get_adapter
from datetime import datetime
import json


def _clean_num(v: Any) -> Optional[float]:
    if v is None:
        return None
    try:
        s = str(v).strip()
        if s == "":
            return None
        return float(s)
    except Exception:
        return None


def _clean_str(v: Any) -> Optional[str]:
    if v is None:
        return None
    s = str(v).strip()
    return s if s != "" else None


def _json_dumps(obj: Any) -> Optional[str]:
    try:
        return json.dumps(obj, ensure_ascii=False)
    except Exception:
        return None


def _json_loads(s: Any) -> List[int] | None:
    if s is None:
        return None
    try:
        if isinstance(s, list):
            return s
        return json.loads(s)
    except Exception:
        return None


def _fetch_files(cur, ids: List[int]) -> List[Dict[str, Any]]:
    if not ids:
        return []
    q = ",".join(["?" for _ in ids])
    cur.execute(f"SELECT F_ID, F_PATH, F_ORIGINAL_NAME AS F_NAME FROM T_FILES WHERE F_ID IN ({q})", tuple(ids))
    rows = cur.fetchall()
    cols = [c[0] for c in cur.description]
    out = [dict(zip(cols, r)) for r in rows]
    # 映射统一字段名
    mapped: List[Dict[str, Any]] = []
    for r in out:
        mapped.append({
            "id": r.get("F_ID"),
            "path": r.get("F_PATH"),
            "name": r.get("F_NAME"),
        })
    # 保持与 ids 传入顺序尽量一致
    order = {i: idx for idx, i in enumerate(ids)}
    mapped.sort(key=lambda x: order.get(x.get("id"), 1_000_000))
    return mapped


def save_thaw(
    gdid: int,
    fsjl: Dict[str, Any] | None,
    jzjmx: List[Dict[str, Any]] | None,
    jzjmx_del_ids: List[int] | None,
    file_ids_override: List[int] | None,
    add_file_ids: List[int] | None,
    del_file_ids: List[int] | None,
    operator: Optional[str],
) -> Dict[str, Any]:
    adapter = get_adapter()
    
    # ========== 在事务前获取旧数据（用于质控变更比对） ==========
    old_fsjl_data = {}
    is_update = False
    old_detail_data = None  # ✅ 在事务前捕获完整旧详情，避免事务提交后取到新数据
    
    with adapter.connect() as conn_check:
        cur_check = conn_check.cursor()
        # 查询是否已有复苏记录
        sql_check = """SELECT F_ID, F_FSWD, F_FSSJ, F_PYJ, F_LXSL, F_LXSJ, F_HXHL, F_HZXS, F_DCQZXS, F_FSXL,
                       F_PYBBH, F_JZKBS, F_DKJZTJ, F_CJWD, F_CJSJ, F_TJPYJ, F_PYX_TEMP, F_PYX_CO2, F_PYX_HUM, F_PYX_FRSJ
                       FROM T_FSJL WHERE F_GDID = ? ORDER BY F_ID DESC"""
        paginated_sql_check, extra_check = adapter.paginate(sql_check, 0, 1)
        cur_check.execute(paginated_sql_check, (gdid,) + tuple(extra_check))
        exist_check = cur_check.fetchone()
        if exist_check:
            is_update = True
            old_fsjl_data = {
                "F_FSWD": exist_check[1],
                "F_FSSJ": exist_check[2],
                "F_PYJ": exist_check[3],
                "F_LXSL": exist_check[4],
                "F_LXSJ": exist_check[5],
                "F_HXHL": exist_check[6],
                "F_HZXS": exist_check[7],
                "F_DCQZXS": exist_check[8],
                "F_FSXL": exist_check[9],
                "F_PYBBH": exist_check[10],
                "F_JZKBS": exist_check[11],
                "F_DKJZTJ": exist_check[12],
                "F_CJWD": exist_check[13],
                "F_CJSJ": exist_check[14],
                "F_TJPYJ": exist_check[15],
                "F_PYX_TEMP": exist_check[16],
                "F_PYX_CO2": exist_check[17],
                "F_PYX_HUM": exist_check[18],
                "F_PYX_FRSJ": exist_check[19],
            }
            # ✅ 事务提交前取完整旧详情（基质胶明细等删除后将不可恢复）
            try:
                old_detail_data = get_thaw_detail_by_gdid(gdid)
            except Exception:
                old_detail_data = None
    
    with adapter.connect() as conn:
        cur = conn.cursor()

        # 校验工单是否存在且为复苏类型
        cur.execute("SELECT F_ID, F_YBLY FROM T_PYGD WHERE F_ID = ?", (gdid,))
        base_row = cur.fetchone()
        if not base_row:
            raise ValueError("复苏工单不存在")
        ybly = (base_row[1] or "").strip() if len(base_row) > 1 else ""
        if ybly not in ("复苏", "原代复苏", "传代复苏"):
            raise ValueError("仅支持在样本来源为复苏、原代复苏或传代复苏的工单上录入复苏明细")

        # 1) Upsert 主表 T_FSJL（按 F_GDID 一对一）
        sql = "SELECT F_ID, F_FJ, F_JZJ FROM T_FSJL WHERE F_GDID = ? ORDER BY F_ID DESC"
        paginated_sql, extra = adapter.paginate(sql, 0, 1)
        cur.execute(paginated_sql, (gdid,) + tuple(extra))
        exist = cur.fetchone()
        exist_id = int(exist[0]) if exist else None
        exist_fj = None
        exist_jzj = []
        if exist:
            if len(exist) > 1:
                exist_fj = _json_loads(exist[1]) or []
            if len(exist) > 2:
                try:
                    exist_jzj = json.loads(exist[2] or "[]")
                except Exception:
                    exist_jzj = []

        # 处理附件ID数组
        final_fj_ids: List[int] = exist_fj or []
        if file_ids_override is not None:
            final_fj_ids = [int(i) for i in file_ids_override if i is not None]
        else:
            # 增量
            add_ids = [int(i) for i in (add_file_ids or []) if i is not None]
            del_ids = set(int(i) for i in (del_file_ids or []) if i is not None)
            # 删除
            final_fj_ids = [i for i in final_fj_ids if i not in del_ids]
            # 添加（去重）
            for i in add_ids:
                if i not in final_fj_ids:
                    final_fj_ids.append(i)

        # 平铺并清洗字段（与 T_PYMX_ZZ 保持一致的重叠命名）
        fs = fsjl or {}
        row = {
            "F_GDID": gdid,
            # 复苏阶段关键参数
            "F_FSWD": _clean_num(fs.get("F_FSWD")),
            "F_FSSJ": _clean_num(fs.get("F_FSSJ")),
            "F_PYJ": _clean_str(fs.get("F_PYJ")),
            "F_LXSL": _clean_num(fs.get("F_LXSL")),
            "F_LXSJ": _clean_num(fs.get("F_LXSJ")),
            "F_HXHL": _clean_num(fs.get("F_HXHL")),
            "F_HZXS": _clean_num(fs.get("F_HZXS")),
            "F_DCQZXS": _clean_num(fs.get("F_DCQZXS")),
            "F_FSXL": _clean_num(fs.get("F_FSXL")),
            # 板与成胶（对齐 T_PYMX_ZZ）
            "F_PYBBH": _clean_str(fs.get("F_PYBBH")),
            "F_JZKBS": _clean_num(fs.get("F_JZKBS")),
            "F_DKJZTJ": _clean_num(fs.get("F_DKJZTJ")),
            "F_CJWD": _clean_num(fs.get("F_CJWD")),
            "F_CJSJ": _clean_num(fs.get("F_CJSJ")),
            # 培养箱（对齐 T_PYMX_ZZ）
            "F_TJPYJ": _clean_num(fs.get("F_TJPYJ")),
            "F_PYX_TEMP": _clean_num(fs.get("F_PYX_TEMP")),
            "F_PYX_CO2": _clean_num(fs.get("F_PYX_CO2")),
            "F_PYX_HUM": _clean_num(fs.get("F_PYX_HUM")),
            "F_PYX_FRSJ": _clean_str(fs.get("F_PYX_FRSJ")),
            # 信息记录（对齐 T_PYMX_ZZ）
            "F_CZR": _clean_str(fs.get("F_CZR") or operator),
            "F_CZSJ": _clean_str(fs.get("F_CZSJ") or datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
            # 附件
            "F_FJ": _json_dumps(final_fj_ids),
        }

        # 基质胶：单字段 JSON，结构与 T_PYMX_ZZ 相同（[{F_ID,F_JZJ,F_YL,F_ND}]）
        # 合并删除/新增/更新
        jzj_current = []
        by_id: Dict[int, Dict[str, Any]] = {}
        max_id = 0
        try:
            for it in (exist_jzj or []):
                fid = int(it.get("F_ID")) if it.get("F_ID") is not None else None
                if fid is not None:
                    by_id[fid] = {
                        "F_ID": fid,
                        "F_JZJ": it.get("F_JZJ"),
                        "F_YL": it.get("F_YL"),
                        "F_ND": it.get("F_ND"),
                    }
                    max_id = max(max_id, fid)
        except Exception:
            by_id = {}
            max_id = 0

        # 删除
        if jzjmx_del_ids:
            delset = {int(x) for x in jzjmx_del_ids if x is not None}
            by_id = {k: v for k, v in by_id.items() if k not in delset}

        # 新增/更新
        if jzjmx:
            items = jzjmx if isinstance(jzjmx, list) else []
            for it in items:
                fid_raw = it.get("F_ID")
                try:
                    fid = int(fid_raw) if fid_raw is not None and str(fid_raw).strip() != "" else None
                except Exception:
                    fid = None
                data = {
                    "F_JZJ": _clean_str(it.get("F_JZJ")),
                    "F_YL": _clean_num(it.get("F_YL")),
                    "F_ND": _clean_str(it.get("F_ND")),
                }
                if fid is None:
                    max_id += 1
                    by_id[max_id] = {"F_ID": max_id, **data}
                else:
                    base = by_id.get(fid) or {"F_ID": fid}
                    base.update({k: v for k, v in data.items() if v is not None})
                    by_id[fid] = base

        jzj_current = [by_id[k] for k in sorted(by_id.keys())]
        row["F_JZJ"] = _json_dumps(jzj_current)

        if exist_id is None:
            cols = list(row.keys())
            vals = [row[c] for c in cols]
            ph = ", ".join(["?" for _ in cols])
            cur.execute(f"INSERT INTO T_FSJL ({', '.join(cols)}) VALUES ({ph})", tuple(vals))
            try:
                cur.execute("SELECT MAX(F_ID) FROM T_FSJL WHERE F_GDID = ?", (gdid,))
                exist_id = int(cur.fetchone()[0])
            except Exception:
                exist_id = None
        else:
            set_cols = [k for k in row.keys() if k != "F_GDID"]
            sets = ", ".join([f"{c} = ?" for c in set_cols])
            params = [row[c] for c in set_cols] + [exist_id]
            cur.execute(f"UPDATE T_FSJL SET {sets} WHERE F_ID = ?", tuple(params))

        conn.commit()
    
    # ========== 创建质控审核记录（在事务外执行） ==========
    import logging
    logger = logging.getLogger(__name__)
    
    logger.info(f"[复苏质控] 准备创建质控记录 - gdid: {gdid}, exist_id: {exist_id}, operator: '{operator}'")
    
    qc_audit_id = None
    if gdid and operator is not None:
        try:
            from app.crud.qc_audit import create_qc_audit_record, calculate_changes
            from app.db.factory import get_adapter as get_adapter_qc
            
            # 获取工单和样本信息
            adapter_qc = get_adapter_qc()
            with adapter_qc.connect() as conn_qc:
                cur_qc = conn_qc.cursor()
                
                # 查询工单信息
                cur_qc.execute(
                    "SELECT F_GDBH, F_ZZID, F_XM, F_BRBH, F_ZZBW, F_YBLY, F_DCID, F_DC, F_PYSJ, F_SQR FROM T_PYGD WHERE F_ID = ?",
                    (gdid,)
                )
                row = cur_qc.fetchone()
                if not row:
                    logger.warning(f"[复苏质控] 未找到工单信息 - gdid: {gdid}")
                    return {"F_FSJL_ID": exist_id, "F_GDID": gdid}
                
                gdbh, zzid, xm, brbh, zzbw, ybly, dcid, dc, pysj, sqr = row
                
                # 判断是新增还是修改
                if is_update and fsjl is not None:
                    # 修改：对比新旧数据
                    bglx = "修改"
                    
                    new_fsjl_data = {
                        "F_FSWD": fsjl.get("F_FSWD"),
                        "F_FSSJ": fsjl.get("F_FSSJ"),
                        "F_PYJ": fsjl.get("F_PYJ"),
                        "F_LXSL": fsjl.get("F_LXSL"),
                        "F_LXSJ": fsjl.get("F_LXSJ"),
                        "F_HXHL": fsjl.get("F_HXHL"),
                        "F_HZXS": fsjl.get("F_HZXS"),
                        "F_DCQZXS": fsjl.get("F_DCQZXS"),
                        "F_FSXL": fsjl.get("F_FSXL"),
                        "F_PYBBH": fsjl.get("F_PYBBH"),
                        "F_JZKBS": fsjl.get("F_JZKBS"),
                        "F_DKJZTJ": fsjl.get("F_DKJZTJ"),
                        "F_CJWD": fsjl.get("F_CJWD"),
                        "F_CJSJ": fsjl.get("F_CJSJ"),
                        "F_TJPYJ": fsjl.get("F_TJPYJ"),
                        "F_PYX_TEMP": fsjl.get("F_PYX_TEMP"),
                        "F_PYX_CO2": fsjl.get("F_PYX_CO2"),
                        "F_PYX_HUM": fsjl.get("F_PYX_HUM"),
                        "F_PYX_FRSJ": fsjl.get("F_PYX_FRSJ"),
                    }
                    
                    changes = calculate_changes(old_fsjl_data, new_fsjl_data)
                    
                    # 基质胶明细：整体前后对比（无论新增/修改/删除，统一记录 F_JZJ 的完整变化）
                    if jzjmx or jzjmx_del_ids:
                        if "变更字段" not in changes:
                            changes["变更字段"] = []
                        changes["变更字段"].append({
                            "field": "F_JZJ",
                            "old_value": json.dumps(exist_jzj or [], ensure_ascii=False),
                            "new_value": json.dumps(jzj_current or [], ensure_ascii=False),
                        })
                    
                    # 附件：借助 calculate_changes 自动将ID转换为含静态地址的对象数组
                    if add_file_ids or del_file_ids:
                        if "变更字段" not in changes:
                            changes["变更字段"] = []
                        fj_diff = calculate_changes(
                            {"F_FJ": exist_fj or []},
                            {"F_FJ": final_fj_ids or []},
                        )
                        if isinstance(fj_diff.get("变更字段"), list) and fj_diff["变更字段"]:
                            changes["变更字段"].extend(fj_diff["变更字段"])
                        else:
                            changes["变更字段"].append({
                                "field": "F_FJ",
                                "old_value": exist_fj or [],
                                "new_value": final_fj_ids or [],
                            })
                else:
                    # 新增：记录关键信息
                    bglx = "新增"
                    changes = {
                        "操作": "复苏记录",
                        "关键信息": {
                            "工单ID": gdid,
                            "工单编号": gdbh,
                            "标本号": zzid,
                            "复苏记录ID": exist_id,
                        }
                    }
                    
                    # 添加复苏关键字段
                    if fsjl:
                        if fsjl.get("F_FSWD"):
                            changes["关键信息"]["复苏温度"] = fsjl.get("F_FSWD")
                        if fsjl.get("F_FSSJ"):
                            changes["关键信息"]["复苏时间"] = fsjl.get("F_FSSJ")
                        if fsjl.get("F_PYJ"):
                            changes["关键信息"]["培养基"] = fsjl.get("F_PYJ")
                        if fsjl.get("F_PYBBH"):
                            changes["关键信息"]["培养板编号"] = fsjl.get("F_PYBBH")
                    
                    # 添加基质胶明细信息
                    if jzjmx:
                        changes["关键信息"]["基质胶明细数"] = len(jzjmx)
                    
                    # ✅ 新增操作：获取新增后的完整详情数据（作为 F_OLD_DATA）
                    try:
                        old_detail_data = get_thaw_detail_by_gdid(gdid)
                        logger.info(f"[复苏质控] 成功获取新增后完整详情数据，工单ID: {gdid}")
                    except Exception as e:
                        logger.warning(f"[复苏质控] 获取新增后详情数据失败: {e}")
            
            # ✅ 创建质控审核记录，包含F_OLD_DATA
            qc_audit_id = create_qc_audit_record(
                F_ZKJD="培养",
                F_BGLX=bglx,
                F_ZZID=zzid,
                F_CHANGES=changes,
                F_JLR=operator or "系统",
                F_GDID=gdid,
                F_XM=xm,
                F_BRBH=brbh,
                F_ZZBW=zzbw,
                F_YBLY=ybly,
                F_GDBH=gdbh,
                F_DCID=dcid,
                F_DC=dc,
                F_PYSJ=pysj,
                F_SQR=sqr,
                F_OLD_DATA=old_detail_data  # ✅ 传入完整旧详情数据
            )
            logger.info(f"[复苏质控] 质控记录创建成功 - qc_audit_id: {qc_audit_id}")
        except Exception as e:
            import traceback
            logger.error(f"[复苏质控] 质控记录创建失败: {e}\n{traceback.format_exc()}")
    else:
        logger.warning(f"[复苏质控] 跳过质控记录创建 - gdid: {gdid}, operator: '{operator}'")
    
    result = {"F_FSJL_ID": exist_id, "F_GDID": gdid}
    if qc_audit_id:
        result["qc_audit_id"] = qc_audit_id
    
    return result


def get_thaw_detail_by_gdid(gdid: int) -> Dict[str, Any] | None:
    adapter = get_adapter()
    with adapter.connect() as conn:
        cur = conn.cursor()

        # 工单
        cur.execute(
            "SELECT F_ID, F_GDBH, F_ZZID, F_XM, F_BRBH, F_BBLX, F_DC, F_DCID, F_ZZBW, F_XYZL, F_QYFS, F_YBLY, F_YBGG, F_SQR, F_PYSJ, F_PARENT_ID, F_AZ FROM T_PYGD WHERE F_ID = ?",
            (gdid,),
        )
        row = cur.fetchone()
        if not row:
            return None
        cols = [c[0] for c in cur.description]
        gdxx = dict(zip(cols, row))

        # 复苏主表
        sql = "SELECT * FROM T_FSJL WHERE F_GDID = ? ORDER BY F_ID DESC"
        paginated_sql, extra = adapter.paginate(sql, 0, 1)
        cur.execute(paginated_sql, (gdid,) + tuple(extra))
        fs_row = cur.fetchone()
        fs_cols = [c[0] for c in cur.description]
        fsjl = dict(zip(fs_cols, fs_row)) if fs_row else None

        # 附件映射
        if fsjl and fsjl.get("F_FJ") is not None:
            ids = _json_loads(fsjl.get("F_FJ")) or []
            files = _fetch_files(cur, [int(i) for i in ids])
            fsjl["F_FJ"] = files
        # 基质胶：解析 JSON
        jzjmx: List[Dict[str, Any]] = []
        if fsjl and fsjl.get("F_JZJ") is not None:
            try:
                arr = json.loads(fsjl.get("F_JZJ") or "[]")
                if isinstance(arr, dict):
                    arr = [arr]
                jzjmx = arr
            except Exception:
                jzjmx = []
            # 将 FSJL.F_JZJ 也改为数组返回，避免转义字符串
            fsjl["F_JZJ"] = jzjmx

        return {
            "GDXX": gdxx,
            "FSJL": fsjl,
            "JZJMX": jzjmx,
            "DCXX": get_dcxx_by_gdid(gdid, gdxx.get("F_PARENT_ID")),
        }


def get_dcxx_by_gdid(gdid: int, parent_id: Any = None) -> Optional[Dict[str, Any]]:
    """
    查询冻存摘要信息（与 /organoid-thaw/detail 的 DCXX 结构一致）。
    复苏类子工单优先从 F_PARENT_ID 关联的父工单读取 T_DCJL。
    """
    try:
        dc_target_id = int(parent_id) if parent_id not in (None, "", 0) else int(gdid)
    except Exception:
        dc_target_id = int(gdid)

    adapter = get_adapter()
    with adapter.connect() as conn:
        cur = conn.cursor()
        sql = (
            "SELECT F_CZR, F_DCRQ, F_DCY, F_DCQ_XBHL, F_DCQ_HXBSL "
            "FROM T_DCJL WHERE F_GDID = ? ORDER BY F_ID DESC"
        )
        paginated_sql, extra = adapter.paginate(sql, 0, 1)
        cur.execute(paginated_sql, (dc_target_id,) + tuple(extra))
        drow = cur.fetchone()
        if not drow:
            return None
        dcols = [c[0] for c in cur.description]
        dcxx = dict(zip(dcols, drow))

    v = dcxx.get("F_DCRQ")
    if v is not None:
        try:
            dcxx["F_DCRQ"] = v.strftime("%Y-%m-%d %H:%M:%S") if hasattr(v, "strftime") else str(v)
        except Exception:
            dcxx["F_DCRQ"] = str(v)
    for k in ("F_DCQ_XBHL", "F_DCQ_HXBSL", "F_DCY"):
        val = dcxx.get(k)
        if val is not None:
            dcxx[k] = str(val)
    return dcxx

