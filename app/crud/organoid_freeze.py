from __future__ import annotations

from typing import Any, Dict, List, Optional
from app.db.factory import get_adapter
from datetime import datetime


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


def _clean_int(v: Any) -> Optional[int]:
    if v is None:
        return None
    if isinstance(v, bool):
        return int(v)
    try:
        s = str(v).strip()
        if s == "":
            return None
        if "." in s:
            return int(float(s))
        return int(s)
    except Exception:
        return None


def _clean_str(v: Any) -> Optional[str]:
    if v is None:
        return None
    s = str(v).strip()
    return s if s != "" else None


def _insert_xhmsy(cur, rec: Dict[str, Any]) -> None:
    # 尝试多种列组合，兼容不同库或列演进
    cols_sets = [
        ["F_GDID", "F_MXID", "F_XHM", "F_YL", "F_ND", "F_LXWD", "F_XHWD", "F_LXSL", "F_XHSJ", "F_DCJL", "F_SYCS"],
        ["F_GDID", "F_MXID", "F_XHM", "F_YL", "F_ND", "F_LXWD", "F_XHWD", "F_LXSL", "F_XHSJ", "F_DCJL"],
        ["F_GDID", "F_MXID", "F_XHM", "F_YL", "F_ND", "F_LXWD", "F_XHWD", "F_XHSJ", "F_DCJL"],
        ["F_GDID", "F_XHM", "F_YL", "F_ND", "F_LXWD", "F_XHWD", "F_LXSL", "F_XHSJ", "F_DCJL", "F_SYCS"],
        ["F_GDID", "F_XHM", "F_YL", "F_ND", "F_LXWD", "F_XHWD", "F_LXSL", "F_XHSJ", "F_DCJL"],
        ["F_GDID", "F_XHM", "F_YL", "F_ND", "F_LXWD", "F_XHWD", "F_XHSJ", "F_DCJL"],
        ["F_GDID", "F_MXID", "F_XHM", "F_YL", "F_ND", "F_LXWD", "F_LXSL", "F_XHSJ", "F_DCJL", "F_SYCS"],
        ["F_GDID", "F_MXID", "F_XHM", "F_YL", "F_ND", "F_LXWD", "F_LXSL", "F_XHSJ", "F_DCJL"],
        ["F_GDID", "F_XHM", "F_YL", "F_ND", "F_LXWD", "F_LXSL", "F_XHSJ", "F_DCJL", "F_SYCS"],
        ["F_GDID", "F_XHM", "F_YL", "F_ND", "F_LXWD", "F_LXSL", "F_XHSJ", "F_DCJL"],
        ["F_GDID", "F_XHM", "F_YL", "F_ND", "F_LXWD", "F_XHSJ", "F_DCJL", "F_SYCS"],
        ["F_GDID", "F_XHM", "F_YL", "F_ND", "F_LXWD", "F_XHSJ", "F_DCJL"],
        ["F_GDID", "F_XHM", "F_YL", "F_ND", "F_LXSL", "F_XHSJ", "F_DCJL", "F_SYCS"],
        ["F_GDID", "F_XHM", "F_YL", "F_ND", "F_LXSL", "F_XHSJ", "F_DCJL"],
        ["F_GDID", "F_XHM", "F_YL", "F_ND", "F_XHSJ", "F_DCJL", "F_SYCS"],
        ["F_GDID", "F_XHM", "F_YL", "F_ND", "F_XHSJ", "F_DCJL"],
        ["F_GDID", "F_XHM", "F_YL", "F_ND", "F_XHSJ", "F_SYCS"],
        ["F_GDID", "F_XHM", "F_YL", "F_ND", "F_XHSJ"],
    ]
    for cols in cols_sets:
        try:
            values = [rec.get(c) for c in cols]
            ph = ", ".join(["?" for _ in cols])
            cur.execute(f"INSERT INTO T_XHMSY ({', '.join(cols)}) VALUES ({ph})", tuple(values))
            return
        except Exception:
            continue
    raise RuntimeError("T_XHMSY 插入失败：未匹配到可用列组合")


def create_freeze(
    gdid: int,
    xhmsy_list: List[Dict[str, Any]] | None,
    dcjl: Dict[str, Any] | None,
    operator: Optional[str],
    delete_xh_ids: List[int] | None = None,
) -> Dict[str, Any]:
    adapter = get_adapter()
    
    # ========== 在事务前获取旧数据（用于质控变更比对） ==========
    old_dcjl_data = {}
    is_update = False
    
    with adapter.connect() as conn_check:
        cur_check = conn_check.cursor()
        # 查询是否已有冻存记录
        sql_check = "SELECT F_ID, F_DCQ_XBHL, F_DCQ_XTPG, F_DCQ_HXBSL, F_DCY, F_DCYND, F_DCGS, F_MG_DCSL FROM T_DCJL WHERE F_GDID = ? ORDER BY F_ID DESC"
        paginated_sql_check, extra_check = adapter.paginate(sql_check, 0, 1)
        cur_check.execute(paginated_sql_check, (gdid,) + tuple(extra_check))
        exist_check = cur_check.fetchone()
        if exist_check:
            is_update = True
            old_dcjl_data = {
                "F_DCQ_XBHL": exist_check[1],
                "F_DCQ_XTPG": exist_check[2],
                "F_DCQ_HXBSL": exist_check[3],
                "F_DCY": exist_check[4],
                "F_DCYND": exist_check[5],
                "F_DCGS": exist_check[6],
                "F_MG_DCSL": exist_check[7],
            }
    
    with adapter.connect() as conn:
        cur = conn.cursor()

        # 1) 删除指定的消化酶使用记录（可选，仅限本工单）
        deleted_xh_ids: List[int] = []
        if delete_xh_ids:
            ids = [int(i) for i in delete_xh_ids if i is not None]
            if ids:
                q_marks = ",".join(["?" for _ in ids])
                try:
                    cur.execute(
                        f"DELETE FROM T_XHMSY WHERE F_GDID = ? AND F_ID IN ({q_marks})",
                        tuple([gdid] + ids),
                    )
                    deleted_xh_ids = ids
                except Exception:
                    deleted_xh_ids = []

        # 2) 写入冻存记录（优先处理，以便获取 dc_id 用于关联消化酶记录）
        dc_id: Optional[int] = None
        if dcjl is not None:
            row = {
                "F_GDID": gdid,
                "F_DCQ_XBHL": _clean_num(dcjl.get("F_DCQ_XBHL")),
                "F_DCQ_XTPG": _clean_str(dcjl.get("F_DCQ_XTPG")),
                "F_DCQ_HXBSL": _clean_num(dcjl.get("F_DCQ_HXBSL")),
                "F_DCY": _clean_str(dcjl.get("F_DCY")),
                "F_DCYND": _clean_str(dcjl.get("F_DCYND")),
                "F_DCGS": _clean_num(dcjl.get("F_DCGS")),
                "F_MG_DCSL": _clean_num(dcjl.get("F_MG_DCSL")),
            }
            # 查询是否已有记录
            sql = "SELECT F_ID FROM T_DCJL WHERE F_GDID = ? ORDER BY F_ID DESC"
            paginated_sql, extra = adapter.paginate(sql, 0, 1)
            cur.execute(paginated_sql, (gdid,) + tuple(extra))
            exist = cur.fetchone()
            if exist:
                dc_id = int(exist[0])
                # 更新（全部列），并尝试在为空时补写 F_CZR / F_DCRQ
                sets = "F_DCQ_XBHL = ?, F_DCQ_XTPG = ?, F_DCQ_HXBSL = ?, F_DCY = ?, F_DCYND = ?, F_DCGS = ?, F_MG_DCSL = ?"
                base_params = [
                    row["F_DCQ_XBHL"], row["F_DCQ_XTPG"], row["F_DCQ_HXBSL"],
                    row["F_DCY"], row["F_DCYND"], row["F_DCGS"], row["F_MG_DCSL"]
                ]
                try:
                    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    sets2 = sets + ", F_CZR = COALESCE(F_CZR, ?), F_DCRQ = COALESCE(F_DCRQ, ?)"
                    params2 = base_params + [operator, now_str, dc_id]
                    cur.execute(f"UPDATE T_DCJL SET {sets2} WHERE F_ID = ?", tuple(params2))
                except Exception:
                    params = base_params + [dc_id]
                    cur.execute(f"UPDATE T_DCJL SET {sets} WHERE F_ID = ?", tuple(params))
            else:
                cols = [
                    "F_GDID", "F_DCQ_XBHL", "F_DCQ_XTPG", "F_DCQ_HXBSL",
                    "F_DCY", "F_DCYND", "F_DCGS", "F_MG_DCSL",
                ]
                vals = [row[c] for c in cols]
                # 创建时自动填充操作人与日期（若目标库存在列 F_CZR, F_DCRQ）
                inserted = False
                try:
                    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    cols2 = cols + ["F_CZR", "F_DCRQ"]
                    vals2 = vals + [operator, now_str]
                    ph2 = ", ".join(["?" for _ in cols2])
                    cur.execute(f"INSERT INTO T_DCJL ({', '.join(cols2)}) VALUES ({ph2})", tuple(vals2))
                    inserted = True
                except Exception:
                    inserted = False
                if not inserted:
                    ph = ", ".join(["?" for _ in cols])
                    cur.execute(f"INSERT INTO T_DCJL ({', '.join(cols)}) VALUES ({ph})", tuple(vals))
                try:
                    lookup_sql = "SELECT F_ID FROM T_DCJL WHERE F_GDID = ? ORDER BY F_ID DESC"
                    paginated_sql2, extra2 = adapter.paginate(lookup_sql, 0, 1)
                    cur.execute(paginated_sql2, (gdid,) + tuple(extra2))
                    row_new = cur.fetchone()
                    if row_new:
                        dc_id = int(row_new[0])
                except Exception:
                    dc_id = None

        # 3) 写入消化酶使用记录（必须在冻存记录之后，以便正确关联 F_DCJL）
        inserted_xh_ids: List[int] = []
        updated_xh_ids: List[int] = []
        if xhmsy_list:
            # 使用已获取的 dc_id，或从数据库查询历史记录
            dc_id_fallback: Optional[int] = dc_id
            if dc_id_fallback is None:
                try:
                    sql = "SELECT F_ID FROM T_DCJL WHERE F_GDID = ? ORDER BY F_ID DESC"
                    paginated_sql, extra = adapter.paginate(sql, 0, 1)
                    cur.execute(paginated_sql, (gdid,) + tuple(extra))
                    r = cur.fetchone()
                    if r:
                        dc_id_fallback = int(r[0])
                except Exception:
                    dc_id_fallback = None
            
            for e in xhmsy_list:
                rec = {
                    "F_GDID": gdid,
                    "F_MXID": None,
                    "F_XHM": _clean_str((e or {}).get("F_XHM")),
                    "F_YL": _clean_num((e or {}).get("F_YL")),
                    "F_ND": _clean_str((e or {}).get("F_ND")),
                    "F_LXWD": _clean_num((e or {}).get("F_LXWD")),
                    "F_LXSL": _clean_num((e or {}).get("F_LXSL")),
                    "F_XHSJ": _clean_num((e or {}).get("F_XHSJ")),
                    "F_DCJL": _clean_num((e or {}).get("F_DCJL") or dc_id_fallback),
                    "F_SYCS": _clean_int((e or {}).get("F_SYCS")),
                }
                existing_id = (e or {}).get("F_ID")
                updated = False
                if existing_id not in (None, ""):
                    try:
                        existing_id_int = int(existing_id)
                    except Exception:
                        existing_id_int = None
                    if existing_id_int is not None:
                        update_cols_sets = [
                            ["F_XHM", "F_YL", "F_ND", "F_LXWD", "F_XHWD", "F_LXSL", "F_XHSJ", "F_DCJL", "F_SYCS"],
                            ["F_XHM", "F_YL", "F_ND", "F_LXWD", "F_XHWD", "F_LXSL", "F_XHSJ", "F_DCJL"],
                            ["F_XHM", "F_YL", "F_ND", "F_LXWD", "F_LXSL", "F_XHSJ", "F_DCJL", "F_SYCS"],
                            ["F_XHM", "F_YL", "F_ND", "F_LXWD", "F_LXSL", "F_XHSJ", "F_DCJL"],
                            ["F_XHM", "F_YL", "F_ND", "F_LXWD", "F_XHSJ", "F_DCJL", "F_SYCS"],
                            ["F_XHM", "F_YL", "F_ND", "F_LXWD", "F_XHSJ", "F_DCJL"],
                            ["F_XHM", "F_YL", "F_ND", "F_XHSJ", "F_DCJL", "F_SYCS"],
                            ["F_XHM", "F_YL", "F_ND", "F_XHSJ", "F_DCJL"],
                            ["F_XHM", "F_YL", "F_ND", "F_XHSJ", "F_SYCS"],
                            ["F_XHM", "F_YL", "F_ND", "F_XHSJ"],
                        ]
                        for cols in update_cols_sets:
                            try:
                                sets = ", ".join([f"{c} = ?" for c in cols])
                                params = [rec.get(c) for c in cols] + [existing_id_int, gdid]
                                cur.execute(
                                    f"UPDATE T_XHMSY SET {sets} WHERE F_ID = ? AND F_GDID = ?",
                                    tuple(params),
                                )
                                if getattr(cur, "rowcount", None):
                                    if cur.rowcount > 0:
                                        updated_xh_ids.append(existing_id_int)
                                        updated = True
                                        break
                                else:
                                    # 某些驱动可能不支持 rowcount，这里简单视为成功
                                    updated_xh_ids.append(existing_id_int)
                                    updated = True
                                    break
                            except Exception:
                                continue
                if updated:
                    continue
                try:
                    _insert_xhmsy(cur, rec)
                    try:
                        cur.execute("SELECT MAX(F_ID) FROM T_XHMSY WHERE F_GDID = ?", (gdid,))
                        new_id = int(cur.fetchone()[0])
                        inserted_xh_ids.append(new_id)
                    except Exception:
                        pass
                except Exception:
                    continue

        conn.commit()
    
    # ========== 创建质控审核记录（在事务外执行） ==========
    import logging
    logger = logging.getLogger(__name__)
    
    logger.info(f"[冻存质控] 准备创建质控记录 - gdid: {gdid}, dc_id: {dc_id}, operator: '{operator}'")
    
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
                    logger.warning(f"[冻存质控] 未找到工单信息 - gdid: {gdid}")
                    return {
                        "F_GDID": gdid,
                        "XHMSY_INSERTED_IDS": inserted_xh_ids,
                        "XHMSY_UPDATED_IDS": updated_xh_ids,
                        "XHMSY_DELETED_IDS": deleted_xh_ids,
                        "F_DCJL_ID": dc_id,
                    }
                
                gdbh, zzid, xm, brbh, zzbw, ybly, dcid, dc, pysj, sqr = row
                
                # ✅ 获取完整的旧详情数据（用于F_OLD_DATA字段）
                old_detail_data = None
                if is_update:
                    try:
                        old_detail_data = get_freeze_detail_by_gdid(gdid)
                        logger.info(f"[冻存质控] 成功获取旧详情数据用于备份，工单ID: {gdid}")
                    except Exception as e:
                        logger.warning(f"[冻存质控] 获取旧详情数据失败: {e}")
                
                # 判断是新增还是修改
                if is_update and dcjl is not None:
                    # 修改：对比新旧数据
                    bglx = "修改"
                    
                    new_dcjl_data = {
                        "F_DCQ_XBHL": dcjl.get("F_DCQ_XBHL"),
                        "F_DCQ_XTPG": dcjl.get("F_DCQ_XTPG"),
                        "F_DCQ_HXBSL": dcjl.get("F_DCQ_HXBSL"),
                        "F_DCY": dcjl.get("F_DCY"),
                        "F_DCYND": dcjl.get("F_DCYND"),
                        "F_DCGS": dcjl.get("F_DCGS"),
                        "F_MG_DCSL": dcjl.get("F_MG_DCSL"),
                    }
                    
                    changes = calculate_changes(old_dcjl_data, new_dcjl_data)
                    
                    # 添加消化酶记录变更信息
                    if inserted_xh_ids or updated_xh_ids or deleted_xh_ids:
                        if "变更字段" not in changes:
                            changes["变更字段"] = []
                        if inserted_xh_ids:
                            changes["变更字段"].append({
                                "field": "XHMSY_新增",
                                "field_name": "消化酶使用记录新增",
                                "old_value": "",
                                "new_value": f"新增{len(inserted_xh_ids)}条记录"
                            })
                        if updated_xh_ids:
                            changes["变更字段"].append({
                                "field": "XHMSY_修改",
                                "field_name": "消化酶使用记录修改",
                                "old_value": "",
                                "new_value": f"修改{len(updated_xh_ids)}条记录"
                            })
                        if deleted_xh_ids:
                            changes["变更字段"].append({
                                "field": "XHMSY_删除",
                                "field_name": "消化酶使用记录删除",
                                "old_value": f"删除{len(deleted_xh_ids)}条记录",
                                "new_value": ""
                            })
                else:
                    # 新增：记录关键信息
                    bglx = "新增"
                    changes = {
                        "操作": "冻存记录",
                        "关键信息": {
                            "工单ID": gdid,
                            "工单编号": gdbh,
                            "标本号": zzid,
                            "冻存记录ID": dc_id,
                        }
                    }
                    
                    # 添加冻存关键字段
                    if dcjl:
                        if dcjl.get("F_DCY"):
                            changes["关键信息"]["冻存液"] = dcjl.get("F_DCY")
                        if dcjl.get("F_DCGS"):
                            changes["关键信息"]["冻存管数"] = dcjl.get("F_DCGS")
                        if dcjl.get("F_MG_DCSL"):
                            changes["关键信息"]["每管冻存量"] = dcjl.get("F_MG_DCSL")
                    
                    # 添加消化酶记录信息
                    if inserted_xh_ids:
                        changes["关键信息"]["消化酶记录数"] = len(inserted_xh_ids)
            
            # ✅ 创建质控审核记录，包含F_OLD_DATA
            qc_audit_id = create_qc_audit_record(
                F_ZKJD="冻存",
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
            logger.info(f"[冻存质控] 质控记录创建成功 - qc_audit_id: {qc_audit_id}")
        except Exception as e:
            import traceback
            logger.error(f"[冻存质控] 质控记录创建失败: {e}\n{traceback.format_exc()}")
    else:
        logger.warning(f"[冻存质控] 跳过质控记录创建 - gdid: {gdid}, operator: '{operator}'")
    
    result = {
        "F_GDID": gdid,
        "XHMSY_INSERTED_IDS": inserted_xh_ids,
        "XHMSY_UPDATED_IDS": updated_xh_ids,
        "XHMSY_DELETED_IDS": deleted_xh_ids,
        "F_DCJL_ID": dc_id,
    }
    if qc_audit_id:
        result["qc_audit_id"] = qc_audit_id
    
    return result


