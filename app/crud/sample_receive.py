from typing import Optional, Dict, Any
from app.db.factory import get_adapter
from app.db.adapters.dameng import DamengAdapter
from typing import Tuple
from datetime import datetime
from typing import List
from typing import Tuple as _Tuple


def query_by_ids(F_ZZID: Optional[str], F_ZYH: Optional[str], F_SJTMH: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """根据标本号、住院号或送检条码号查询：优先读取 T_YBZJ（最新记录），缺失字段再回落 T_ZZXX。

    - 主表：T_YBZJ（已整合 JZXX/BBXX 字段）
    - 兜底：T_ZZXX（历史/缺失补全）
    
    参数优先级：F_ZZID > F_SJTMH > F_ZYH
    """
    if not (F_ZZID or F_ZYH or F_SJTMH):
        return None

    adapter = get_adapter()

    # 解析 F_ZZID：优先使用直接提供的 F_ZZID
    zzid = (F_ZZID or "").strip() if F_ZZID else None
    z_row = None
    
    # 如果没有 F_ZZID，尝试通过送检条码号查询
    if not zzid and F_SJTMH:
        sj_sql = """
            SELECT F_ZZID, F_XM, F_BRBH, F_ZYH, F_XB, F_NL, F_DH, F_SFZH, F_DZ,
                   F_BBLY, F_LCZD, F_ZQTYS, F_ZQTYSLB, F_MZH, F_CHH, F_BZTZ,
                   F_BBLX, F_CXSJ, F_LTSJ, F_SJTMH, F_CSNY
            FROM T_YBZJ WHERE F_SJTMH = ? ORDER BY F_ID DESC
        """
        sj_sql_paginated, sj_extra = adapter.paginate(sj_sql, 0, 1)
        rows, cols = adapter.fetch_all(sj_sql_paginated, [F_SJTMH] + sj_extra)
        if rows:
            z_row = dict(zip(cols, rows[0]))
            zzid = z_row.get("F_ZZID")
    
    # 如果仍然没有 F_ZZID，通过住院号到 T_ZZXX 解析 F_ZZID
    if not zzid and F_ZYH:
        z_sql = """
            SELECT F_ZZID, F_XM, F_BRBH, F_ZYH, F_XB, F_NL, F_DH, F_SFZH, F_DZ,
                   F_BBLY, F_LCZD, F_ZQTYS, F_ZQTYSLB, F_MZH, F_CHH, F_BZTZ,
                   F_BBLX, F_SSMC, F_SSRQ, F_CXSJ, F_LTSJ, F_CRB
            FROM T_ZZXX WHERE F_ZYH = ? ORDER BY F_ID DESC
        """
        z_sql_paginated, z_extra = adapter.paginate(z_sql, 0, 1)
        rows, cols = adapter.fetch_all(z_sql_paginated, [F_ZYH] + z_extra)
        if rows:
            z_row = dict(zip(cols, rows[0]))
            zzid = z_row.get("F_ZZID")
    
    if not zzid:
        return None

    # 读取最新一条 YBZJ
    y_sql = "SELECT * FROM T_YBZJ WHERE F_ZZID = ? ORDER BY F_ID DESC"
    y_paginated, y_extra = adapter.paginate(y_sql, 0, 1)
    y_rows, y_cols = adapter.fetch_all(y_paginated, [zzid] + y_extra)
    ybzj = dict(zip(y_cols, y_rows[0])) if y_rows else {}

    # 兜底读取 ZZXX（F_ZZID 是唯一键，直接查询无需排序）
    if z_row is None:
        z_sql2 = (
            "SELECT F_XM, F_BRBH, F_ZYH, F_XB, F_NL, F_DH, F_SFZH, F_DZ, "
            "F_BBLY, F_LCZD, F_ZQTYS, F_ZQTYSLB, F_MZH, F_CHH, F_BZTZ, "
            "F_ZZID, F_BBLX, F_SSMC, F_SSRQ, F_CXSJ, F_LTSJ, F_CRB "
            "FROM T_ZZXX WHERE F_ZZID = ?"
        )
        rows2, cols2 = adapter.fetch_all(z_sql2, [zzid])
        z_row = dict(zip(cols2, rows2[0])) if rows2 else {}

    # 组装 JZXX（优先 YBZJ）
    jz_fields = [
        "F_XM","F_BRBH","F_ZYH","F_XB","F_NL","F_DH","F_SFZH","F_DZ",
        "F_BBLY","F_LCZD","F_ZQTYS","F_ZQTYSLB","F_MZH","F_CHH","F_BZTZ",
        "F_SJTMH",  # 送检条码号
        "F_CSNY",  # 出生年月
    ]
    patient: Dict[str, Any] = {f: (ybzj.get(f) if ybzj.get(f) is not None else z_row.get(f)) for f in jz_fields}

    # 组装 BBXX（优先 YBZJ）
    bb_fields = [
        "F_ZZID","F_BBLX","F_SSMC","F_SSRQ","F_CXSJ","F_LTSJ","F_CRB",
        "F_JSSJ","F_CJBW","F_QYFS","F_SSS","F_SSYS",
    ]
    specimen: Dict[str, Any] = {f: (ybzj.get(f) if ybzj.get(f) is not None else z_row.get(f)) for f in bb_fields}

    # 格式化日期
    # F_DJRQ: date -> YYYY-MM-DD
    if ybzj.get("F_DJRQ") is not None:
        v = ybzj.get("F_DJRQ")
        try:
            ybzj["F_DJRQ"] = v.strftime("%Y-%m-%d") if hasattr(v, "strftime") else str(v)
        except Exception:
            ybzj["F_DJRQ"] = str(v)
    # F_JSSJ: datetime -> YYYY-MM-DD HH:MM:SS
    if specimen.get("F_JSSJ") is not None:
        jv = specimen.get("F_JSSJ")
        try:
            specimen["F_JSSJ"] = jv.strftime("%Y-%m-%d %H:%M:%S") if hasattr(jv, "strftime") else str(jv)
        except Exception:
            specimen["F_JSSJ"] = str(jv)
    # F_SSRQ: date -> YYYY-MM-DD
    if specimen.get("F_SSRQ") is not None:
        dv = specimen.get("F_SSRQ")
        try:
            specimen["F_SSRQ"] = dv.strftime("%Y-%m-%d") if hasattr(dv, "strftime") else str(dv)
        except Exception:
            specimen["F_SSRQ"] = str(dv)
    # F_CXSJ/F_LTSJ: datetime -> YYYY-MM-DD HH:MM:SS
    for tk in ("F_CXSJ", "F_LTSJ"):
        tv = specimen.get(tk)
        if tv is not None:
            try:
                specimen[tk] = tv.strftime("%Y-%m-%d %H:%M:%S") if hasattr(tv, "strftime") else str(tv)
            except Exception:
                specimen[tk] = str(tv)
    # F_NL 数值 -> 字符串
    if patient.get("F_NL") is not None and not isinstance(patient.get("F_NL"), str):
        try:
            patient["F_NL"] = str(patient.get("F_NL"))
        except Exception:
            patient["F_NL"] = f"{patient.get('F_NL')}"

    # F_SYTP_DZ: 由文件ID映射为静态路径
    if ybzj and ybzj.get("F_SYTP") is not None:
        try:
            fid = int(ybzj.get("F_SYTP"))
            from app.crud.file_center import get_file_by_id
            f = get_file_by_id(fid)
            if f and f.get("F_PATH"):
                ybzj["F_SYTP_DZ"] = f.get("F_PATH")
        except Exception:
            pass

    # JZXX 补充知情同意书 ID数组 与静态路径（来自 YBZJ.F_ZQTYS_ID）
    if ybzj and ybzj.get("F_ZQTYS_ID") is not None:
        try:
            from app.crud.file_center import get_file_by_id as _get_file
            import json
            
            # 解析ID数组
            id_list = []
            raw_ids = ybzj.get("F_ZQTYS_ID")
            if isinstance(raw_ids, str):
                try:
                    id_list = json.loads(raw_ids)
                except:
                    # 兼容旧数据（单个ID）
                    try:
                        id_list = [int(raw_ids)]
                    except:
                        pass
            elif isinstance(raw_ids, list):
                id_list = raw_ids
            elif isinstance(raw_ids, int):
                id_list = [raw_ids]
            
            # 获取所有文件详情
            consent_files = []
            for cid in id_list:
                try:
                    cf = _get_file(int(cid))
                    if cf:
                        consent_files.append({
                            "id": int(cid),
                            "path": cf.get("F_PATH"),
                            "name": cf.get("F_ORIGINAL_NAME")
                        })
                except Exception:
                    pass
            
            patient["F_ZQTYS_FILES"] = consent_files  # 返回文件详情数组
            
            # 兼容字段：返回第一个文件的ID和路径（向后兼容）
            if consent_files:
                patient["F_ZQTYS_ID"] = str(consent_files[0]["id"])  # 转为字符串
                patient["F_ZQTYS_DZ"] = consent_files[0]["path"]
            else:
                patient["F_ZQTYS_ID"] = None
                patient["F_ZQTYS_DZ"] = None
        except Exception:
            patient["F_ZQTYS_FILES"] = []
            patient["F_ZQTYS_ID"] = None
            patient["F_ZQTYS_DZ"] = None
    else:
        # 没有知情同意书数据时，设置默认值
        patient["F_ZQTYS_FILES"] = []
        patient["F_ZQTYS_ID"] = None
        patient["F_ZQTYS_DZ"] = None

    adapter2 = get_adapter()
    df_rows, df_cols = adapter2.fetch_all(
        """
        SELECT F_ID, F_ZZID, F_PYRY, F_MXLX, F_GDLX, F_FFSJ, F_FFCJR, F_GDZT
        FROM T_FFJL WHERE F_ZZID = ? ORDER BY F_FFSJ DESC, F_ID DESC
        """,
        [zzid],
    )
    
    # 收集所有用户ID
    user_ids = set()
    for row in df_rows or []:
        pyry_id = row[df_cols.index("F_PYRY")] if "F_PYRY" in df_cols else None
        if pyry_id:
            try:
                user_ids.add(int(pyry_id))
            except (ValueError, TypeError):
                pass
    
    # 批量查询用户名
    user_name_map = {}
    if user_ids:
        with adapter2.connect() as conn:
            cur = conn.cursor()
            try:
                placeholders = ", ".join(["?"] * len(user_ids))
                cur.execute(
                    f"SELECT F_ID, F_Ktype FROM T_USER WHERE F_ID IN ({placeholders})",
                    list(user_ids)
                )
                for user_row in cur.fetchall():
                    uid = int(user_row[0]) if user_row[0] is not None else None
                    uname = user_row[1]
                    if uid is not None and uname:
                        user_name_map[uid] = uname
            except Exception as e:
                pass
    
    df_list: List[Dict[str, Any]] = []
    for row in df_rows or []:
        item = dict(zip(df_cols, row))
        
        # 将F_PYRY从用户ID映射为用户名
        if item.get("F_PYRY"):
            try:
                pyry_id = int(item["F_PYRY"])
                item["F_PYRY"] = user_name_map.get(pyry_id, item["F_PYRY"])
            except (ValueError, TypeError):
                pass
        
        for key in ("F_FFSJ",):
            val = item.get(key)
            if val is not None:
                try:
                    item[key] = val.strftime("%Y-%m-%d %H:%M:%S") if hasattr(val, "strftime") else str(val)
                except Exception:
                    item[key] = str(val)
        df_list.append(item)

    return {"JZXX": patient, "BBXX": specimen, "YBZJ": (ybzj or None), "FFJL": df_list}


def save_all(jzxx: Dict[str, Any] | None, bbxx: Dict[str, Any] | None, ybzj: Dict[str, Any] | None) -> None:
    """在一个事务内保存/更新质检(T_YBZJ)；JZXX/BBXX 字段合并入 T_YBZJ。

    规则：
    - 必须具备 F_ZZID
    - 写入优先 T_YBZJ；不再写 T_SGXX；T_ZZXX 仅作为历史保留，不在此处更新
    - T_YBZJ 按 F_ZZID upsert
    """
    if not (jzxx or bbxx or ybzj):
        return
    adapter = get_adapter()
    # 规范空字符串字段：空串 -> None (防止数据类型转换错误)
    def _normalize_empty_fields(d: Dict[str, Any] | None) -> Dict[str, Any] | None:
        if not d:
            return d
        for k, v in list(d.items()):
            if isinstance(v, str) and v.strip() == "":
                d[k] = None
        return d
    jzxx = _normalize_empty_fields(jzxx)
    bbxx = _normalize_empty_fields(bbxx)
    ybzj = _normalize_empty_fields(ybzj)
    with adapter.connect() as conn:
        cur = conn.cursor()
        # 合并 BBXX 到 JZXX（写入 T_ZZXX 的字段）
        if bbxx:
            overlap_to_zzxx = ("F_BBLX", "F_SSMC", "F_SSRQ", "F_CXSJ", "F_LTSJ", "F_CRB")
            for k in overlap_to_zzxx:
                if k in bbxx and bbxx.get(k) is not None:
                    if jzxx is None:
                        jzxx = {"F_ZZID": (bbxx.get("F_ZZID") or (ybzj or {}).get("F_ZZID"))}
                    jzxx[k] = bbxx.get(k)
        # 合并 BBXX 中可落 T_ZZXX 的字段，保证查询能读到（避免历史空值）
        if bbxx and bbxx.get("F_ZZID") is not None:
            overlap_keys = ["F_BBLX", "F_SSMC", "F_SSRQ", "F_CXSJ", "F_LTSJ", "F_CRB"]
            if jzxx is None:
                jzxx = {"F_ZZID": bbxx.get("F_ZZID")}
            for k in overlap_keys:
                if k in bbxx and bbxx.get(k) is not None:
                    jzxx[k] = bbxx.get(k)
        # T_ZZXX upsert（按 F_ZZID）
        if jzxx and jzxx.get("F_ZZID") is not None:
            key_val = jzxx.get("F_ZZID")
            cur.execute("SELECT 1 FROM T_ZZXX WHERE F_ZZID = ?", (key_val,))
            exists = cur.fetchone() is not None
            keys = [k for k in jzxx.keys()]
            if exists:
                set_cols = [k for k in keys if k != "F_ZZID"]
                if set_cols:
                    sets = ", ".join([f"{c} = ?" for c in set_cols])
                    params = [jzxx[c] for c in set_cols] + [key_val]
                    cur.execute(f"UPDATE T_ZZXX SET {sets} WHERE F_ZZID = ?", tuple(params))
            else:
                # 避免仅传 F_ZZID 时盲插，至少需要除主键外还有其它字段
                if len(keys) > 1:
                    cols = keys
                    placeholders = ", ".join(["?" for _ in cols])
                    cur.execute(f"INSERT INTO T_ZZXX ({', '.join(cols)}) VALUES ({placeholders})", tuple(jzxx[c] for c in cols))

        # 合并工具
        def merge_into(target: Dict[str, Any] | None, src: Dict[str, Any] | None, keys: List[str]) -> Dict[str, Any]:
            if target is None:
                target = {}
            if not src:
                return target
            for k in keys:
                if k in src and src.get(k) is not None:
                    target[k] = src.get(k)
            return target

        jz_keys = [
            "F_XM","F_BRBH","F_ZYH","F_XB","F_NL","F_DH","F_SFZH","F_DZ",
            "F_BBLY","F_LCZD","F_ZQTYS","F_ZQTYSLB","F_MZH","F_CHH","F_BZTZ",
            "F_SJTMH",  # 送检条码号
        ]
        bb_keys = [
            "F_BBLX","F_SSMC","F_SSRQ","F_CXSJ","F_LTSJ","F_CRB",
            "F_JSSJ","F_CJBW","F_QYFS","F_SSS","F_SSYS",
            "F_ZQTYS","F_ZQTYSLB",
        ]
        ybzj = merge_into(ybzj, jzxx, jz_keys)
        ybzj = merge_into(ybzj, bbxx, bb_keys)

        # T_YBZJ upsert（按 F_ZZID 唯一）
        yz = ybzj.get("F_ZZID")
        cur.execute("SELECT 1 FROM T_YBZJ WHERE F_ZZID = ?", (yz,))
        exists = cur.fetchone() is not None
        keys = [k for k in ybzj.keys()]
        if exists:
            set_cols = [k for k in keys if k != "F_ZZID"]
            if set_cols:
                sets = ", ".join([f"{c} = ?" for c in set_cols])
                params = [ybzj[c] for c in set_cols] + [yz]
                cur.execute(f"UPDATE T_YBZJ SET {sets} WHERE F_ZZID = ?", tuple(params))
        else:
            placeholders = ", ".join(["?" for _ in keys])
            cur.execute(f"INSERT INTO T_YBZJ ({', '.join(keys)}) VALUES ({placeholders})", tuple(ybzj[c] for c in keys))
def mark_unqualified(zzid: str, reason: str | None, creator_code: str = "") -> Optional[int]:
    """将样本标记为不合格：更新 T_YBZJ.F_BHG='true'、F_BHGYY=reason，并创建质控审核记录。

    返回: 质控审核记录ID（如果创建成功）
    """
    adapter = get_adapter()
    with adapter.connect() as conn:
        cur = conn.cursor()
        # 若记录存在则更新，否则插入占位记录
        cur.execute("SELECT 1 FROM T_YBZJ WHERE F_ZZID = ?", (zzid,))
        exists = cur.fetchone() is not None
        if exists:
            cur.execute("UPDATE T_YBZJ SET F_BHG = 'true', F_BHGYY = ? WHERE F_ZZID = ?", (reason or '', zzid))
        else:
            cur.execute(
                "INSERT INTO T_YBZJ (F_ZZID, F_BHG, F_BHGYY) VALUES (?, 'true', ?)",
                (zzid, reason or ''),
            )
        conn.commit()
    
    # ========== 质控审核记录（在事务外执行） ==========
    qc_audit_id = None
    if zzid and creator_code:
        try:
            from app.crud.qc_audit import create_qc_audit_record, get_sample_basic_info
            import logging
            logger = logging.getLogger(__name__)
            
            # 获取样本基本信息
            sample_info = get_sample_basic_info(zzid)
            
            # 标为不合格的变更信息
            changes = {
                "操作": "标为不合格",
                "关键信息": {
                    "标本号": zzid,
                    "不合格原因": reason or "未填写原因"
                }
            }
            
            # 创建质控审核记录（标为不合格场景无工单，不需要 F_YBLY）
            qc_audit_id = create_qc_audit_record(
                F_ZKJD="标为不合格",
                F_BGLX="修改",
                F_ZZID=zzid,
                F_CHANGES=changes,
                F_JLR=creator_code,
                F_XM=sample_info.get("F_XM"),
                F_BRBH=sample_info.get("F_BRBH"),
                F_ZZBW=sample_info.get("F_ZZBW")
            )
            logger.info(f"[标为不合格质控] 创建质控审核记录成功，标本号: {zzid}, 质控ID: {qc_audit_id}")
        except Exception as e:
            import logging
            logger = logging.getLogger(__name__)
            logger.error(f"[标为不合格质控] 创建质控审核记录失败 (标本号: {zzid}): {e}", exc_info=True)
    
    return qc_audit_id


def _update_only(cur, table: str, key_col: str, key_val: Any, data: Dict[str, Any]) -> bool:
    """只更新存在的记录；返回是否更新成功。"""
    if not data:
        return False
    cur.execute(f"SELECT 1 FROM {table} WHERE {key_col} = ?", (key_val,))
    if cur.fetchone() is None:
        return False
    cols = [k for k in data.keys() if k != key_col]
    if not cols:
        return False
    sets = ", ".join([f"{c} = ?" for c in cols])
    params = [data[c] for c in cols] + [key_val]
    cur.execute(f"UPDATE {table} SET {sets} WHERE {key_col} = ?", tuple(params))
    return True


def update_partial(zzid: str, jzxx: Dict[str, Any] | None, bbxx: Dict[str, Any] | None, ybzj: Dict[str, Any] | None) -> Tuple[bool, bool, bool]:
    """只更新：优先更新 T_YBZJ，将 JZXX/BBXX 字段合并后写入；T_ZZXX/T_SGXX 不再更新。

    返回 (False, False, u3)
    """
    adapter = get_adapter()
    # 规范空字符串时间字段：空串 -> None
    def _normalize_empty_time_fields(d: Dict[str, Any] | None) -> Dict[str, Any] | None:
        if not d:
            return d
        for k in ("F_CXSJ", "F_LTSJ", "F_JSSJ"):
            v = d.get(k)
            if isinstance(v, str) and v.strip() == "":
                d[k] = None
        return d
    jzxx = _normalize_empty_time_fields(jzxx)
    bbxx = _normalize_empty_time_fields(bbxx)
    ybzj = _normalize_empty_time_fields(ybzj)
    with adapter.connect() as conn:
        cur = conn.cursor()
        merged = {"F_ZZID": zzid}
        def merge_into(target: Dict[str, Any], src: Dict[str, Any] | None):
            if not src:
                return
            for k, v in src.items():
                if k != "F_ZZID" and v is not None:
                    target[k] = v
        merge_into(merged, jzxx)
        merge_into(merged, bbxx)
        merge_into(merged, ybzj)
        u3 = _update_only(cur, "T_YBZJ", "F_ZZID", zzid, merged)
        conn.commit()
        return False, False, u3


def save_and_distribute(
    jzxx: Dict[str, Any] | None,
    bbxx: Dict[str, Any] | None,
    ybzj: Dict[str, Any] | None,
    distributes: List[Dict[str, Any]] | None,
    creator_code: str | None,
) -> Dict[str, Any]:
    """在一个事务内：保存/更新 JZXX/BBXX/YBZJ，并批量创建 T_FFJL 记录。
    
    同时记录质控审核信息。

    返回：{"ybzj_saved": bool, "distribute_ids": List[int], "qc_audit_ids": List[int], "pdf_id": int}
    """
    from app.crud.qc_audit import get_old_data_for_comparison, get_sample_basic_info
    
    adapter = get_adapter()
    ybzj_saved = False
    created_ids: List[int] = []
    updated_ids: List[int] = []
    qc_audit_ids: List[int] = []  # 质控审核记录ID列表
    
    # 获取F_ZZID（用于后续查询）
    fzzid = None
    if ybzj and ybzj.get("F_ZZID"):
        fzzid = ybzj.get("F_ZZID")
    elif bbxx and bbxx.get("F_ZZID"):
        fzzid = bbxx.get("F_ZZID")
    elif jzxx and jzxx.get("F_ZZID"):
        fzzid = jzxx.get("F_ZZID")
    
    # 在保存前获取旧数据（用于变更对比）
    old_ybzj_data = None
    is_new_sample = True
    if fzzid:
        old_ybzj_data = get_old_data_for_comparison(fzzid, "T_YBZJ")
        is_new_sample = (old_ybzj_data is None)
    
    with adapter.connect() as conn:
        cur = conn.cursor()
        # 规范空字符串字段：空串 -> None (防止数据类型转换错误)
        def _normalize_empty_fields(d: Dict[str, Any] | None) -> Dict[str, Any] | None:
            if not d:
                return d
            for k, v in list(d.items()):
                if isinstance(v, str) and v.strip() == "":
                    d[k] = None
            return d
        jzxx = _normalize_empty_fields(jzxx)
        bbxx = _normalize_empty_fields(bbxx)
        ybzj = _normalize_empty_fields(ybzj)
        
        # 调试日志：检查规范化后的数据
        import logging
        logger = logging.getLogger(__name__)
        logger.info(f"[DEBUG] After normalize - JZXX: {jzxx}")
        logger.info(f"[DEBUG] After normalize - BBXX: {bbxx}")
        logger.info(f"[DEBUG] After normalize - YBZJ: {ybzj}")
        
        # 不再写入 T_ZZXX/T_SGXX；接收/采集与就诊、标本字段合并到 ybzj
        if bbxx:
            for k in ("F_JSSJ", "F_CJBW", "F_QYFS", "F_SSS", "F_SSYS"):
                if k in bbxx and bbxx.get(k) is not None:
                    if ybzj is None:
                        ybzj = {"F_ZZID": bbxx.get("F_ZZID")}
                    ybzj[k] = bbxx.get(k)
            # 也合并 BBXX 的其他关键字段到 YBZJ (包括 F_BZTZ 病灶特征、F_LCZD 临床诊断、F_AZ 癌种)
            for k in ("F_BBLX", "F_SSMC", "F_SSRQ", "F_CXSJ", "F_LTSJ", "F_CRB", "F_ZQTYS", "F_ZQTYSLB", "F_BZTZ", "F_LCZD", "F_AZ"):
                if k in bbxx and bbxx.get(k) is not None:
                    if ybzj is None:
                        ybzj = {"F_ZZID": bbxx.get("F_ZZID")}
                    ybzj[k] = bbxx.get(k)
        if jzxx:
            for k, v in jzxx.items():
                if k != "F_ZZID" and v is not None:
                    if ybzj is None:
                        ybzj = {"F_ZZID": jzxx.get("F_ZZID")}
                    # F_BGYJDZ (办公/邮寄地址) 映射到 F_DZ (地址)
                    if k == "F_BGYJDZ":
                        ybzj["F_DZ"] = v
                    else:
                        # 自动处理JZXX中的所有字段（包括F_CSNY出生年月等）
                        ybzj[k] = v
                        # 记录关键字段的处理
                        if k == "F_CSNY":
                            logger.info(f"[DEBUG] 处理出生年月字段: F_CSNY = {v}")

        # ---------- T_YBZJ upsert ----------
        if ybzj and ybzj.get("F_ZZID") is not None:
            ybzj = _filter_existing_columns(cur, adapter, "T_YBZJ", ybzj) or {}
            yz = ybzj.get("F_ZZID")
            if not yz:
                ybzj_saved = False
            else:
                cur.execute("SELECT 1 FROM T_YBZJ WHERE F_ZZID = ?", (yz,))
                exists = cur.fetchone() is not None
                keys = [k for k in ybzj.keys() if ybzj.get(k) is not None or k == "F_ZZID"]
                if not keys:
                    exists = True  # nothing to insert/update
                if exists:
                    set_cols = [k for k in keys if k != "F_ZZID"]
                    if set_cols:
                        sets = ", ".join([f"{c} = ?" for c in set_cols])
                        params = [ybzj[c] for c in set_cols] + [yz]
                        
                        import logging
                        logger = logging.getLogger(__name__)
                        logger.info(f"[DEBUG] UPDATE T_YBZJ set_cols: {set_cols}")
                        logger.info(f"[DEBUG] UPDATE T_YBZJ params: {params}")
                        logger.info(f"[DEBUG] UPDATE T_YBZJ value types: {[(k, type(ybzj[k]).__name__) for k in set_cols]}")
                        
                        try:
                            cur.execute(f"UPDATE T_YBZJ SET {sets} WHERE F_ZZID = ?", tuple(params))
                            ybzj_saved = True
                        except Exception as e:
                            # 输出详细错误信息
                            logger.error(f"[ERROR] 更新T_YBZJ失败: {e}")
                            logger.error(f"[ERROR] 字段-值对应:")
                            for k in set_cols:
                                logger.error(f"  {k} = {repr(ybzj[k])} (类型: {type(ybzj[k]).__name__})")
                            
                            # 尝试逐字段测试来定位问题字段
                            logger.error(f"[ERROR] 开始逐字段测试UPDATE...")
                            problem_fields = []
                            for test_key in set_cols:
                                try:
                                    test_sql = f"UPDATE T_YBZJ SET {test_key} = ? WHERE F_ZZID = ?"
                                    test_cur = conn.cursor()
                                    test_cur.execute(test_sql, (ybzj[test_key], yz))
                                    conn.rollback()  # 立即回滚测试更新
                                except Exception as field_err:
                                    problem_fields.append((test_key, str(field_err)))
                                    logger.error(f"  ❌ {test_key} 更新失败: {field_err}")
                            
                            if problem_fields:
                                logger.error(f"[ERROR] 发现问题字段: {[f[0] for f in problem_fields]}")
                            
                            raise Exception(f"更新T_YBZJ失败: {e}. 问题字段: {[f[0] for f in problem_fields] if problem_fields else '未知'}. 请检查日志查看详细字段信息")
                else:
                    placeholders = ", ".join(["?" for _ in keys])
                    if placeholders:
                        # 调试日志：打印即将插入的数据
                        import logging
                        logger = logging.getLogger(__name__)
                        logger.info(f"[DEBUG] INSERT T_YBZJ keys: {keys}")
                        logger.info(f"[DEBUG] INSERT T_YBZJ values: {[ybzj[c] for c in keys]}")
                        logger.info(f"[DEBUG] INSERT T_YBZJ value types: {[(k, type(ybzj[k]).__name__) for k in keys]}")
                        
                        try:
                            cur.execute(f"INSERT INTO T_YBZJ ({', '.join(keys)}) VALUES ({placeholders})", tuple(ybzj[c] for c in keys))
                            ybzj_saved = True
                        except Exception as e:
                            # 输出详细错误信息
                            logger.error(f"[ERROR] 插入T_YBZJ失败: {e}")
                            logger.error(f"[ERROR] 字段-值对应:")
                            for k in keys:
                                logger.error(f"  {k} = {repr(ybzj[k])} (类型: {type(ybzj[k]).__name__})")
                            
                            # 尝试逐字段测试来定位问题字段
                            logger.error(f"[ERROR] 开始逐字段测试...")
                            problem_fields = []
                            for test_key in keys:
                                try:
                                    test_sql = f"INSERT INTO T_YBZJ ({test_key}) VALUES (?)"
                                    test_cur = conn.cursor()
                                    test_cur.execute(test_sql, (ybzj[test_key],))
                                    conn.rollback()  # 立即回滚测试插入
                                except Exception as field_err:
                                    problem_fields.append((test_key, str(field_err)))
                                    logger.error(f"  ❌ {test_key} 插入失败: {field_err}")
                            
                            if problem_fields:
                                logger.error(f"[ERROR] 发现问题字段: {[f[0] for f in problem_fields]}")
                            
                            raise Exception(f"插入T_YBZJ失败: {e}. 问题字段: {[f[0] for f in problem_fields] if problem_fields else '未知'}. 请检查日志查看详细字段信息")
        # ---------- 批量创建 T_FFJL ----------
        if distributes:
            from datetime import datetime as _dt

            def _parse_datetime(val: Any) -> Any:
                if val is None:
                    return None
                if isinstance(val, str):
                    txt = val.strip()
                    if not txt:
                        return None
                    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%Y/%m/%d %H:%M:%S"):
                        try:
                            return _dt.strptime(txt, fmt)
                        except ValueError:
                            continue
                    return txt
                return val

            def _get_base_zzid(d: Dict[str, Any]) -> str | None:
                return (
                    d.get("F_ZZID")
                    or (jzxx or {}).get("F_ZZID")
                    or (bbxx or {}).get("F_ZZID")
                    or (ybzj or {}).get("F_ZZID")
                )

            for d in distributes:
                if not isinstance(d, dict):
                    continue
                fid = d.get("F_ID")
                base_zzid = _get_base_zzid(d)
                if not base_zzid:
                    continue

                if fid is not None:
                    try:
                        fid_int = int(fid)
                    except (TypeError, ValueError):
                        continue
                    cur.execute("SELECT F_ZZID FROM T_FFJL WHERE F_ID = ?", (fid_int,))
                    row = cur.fetchone()
                    if row is None:
                        continue
                    update_fields: List[str] = []
                    params: List[Any] = []
                    target_zzid = d.get("F_ZZID") or row[0] or base_zzid
                    if target_zzid:
                        update_fields.append("F_ZZID = ?")
                        params.append(target_zzid)
                    for key in ("F_PYRY", "F_MXLX", "F_GDLX", "F_FFSJ", "F_FFCJR", "F_GDZT"):
                        if key in d and d[key] is not None:
                            val = d[key]
                            if key == "F_FFSJ":
                                val = _parse_datetime(val)
                            update_fields.append(f"{key} = ?")
                            params.append(val)
                    if not update_fields:
                        continue
                    params.append(fid_int)
                    cur.execute(f"UPDATE T_FFJL SET {', '.join(update_fields)} WHERE F_ID = ?", tuple(params))
                    if cur.rowcount:
                        updated_ids.append(fid_int)
                    continue

                pyry = d.get("F_PYRY")
                mxlx = d.get("F_MXLX")
                gdlx = d.get("F_GDLX")
                if pyry is None or not mxlx:
                    continue
                ffsj = _parse_datetime(d.get("F_FFSJ")) or _dt.now()
                ffcjr = d.get("F_FFCJR") or creator_code or ""
                gdzt = d.get("F_GDZT") or "待创建"
                
                # 从 T_YBZJ 读取 F_AZ（癌种）字段
                f_az = None
                try:
                    cur.execute("SELECT F_AZ FROM T_YBZJ WHERE F_ZZID = ?", (base_zzid,))
                    az_row = cur.fetchone()
                    if az_row:
                        f_az = az_row[0]
                except Exception:
                    f_az = None
                
                if gdlx is not None:
                    cur.execute(
                        "INSERT INTO T_FFJL (F_ZZID, F_PYRY, F_MXLX, F_GDLX, F_FFSJ, F_FFCJR, F_GDZT, F_AZ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                        (base_zzid, pyry, mxlx, gdlx, ffsj, ffcjr, gdzt, f_az),
                    )
                else:
                    cur.execute(
                        "INSERT INTO T_FFJL (F_ZZID, F_PYRY, F_MXLX, F_FFSJ, F_FFCJR, F_GDZT, F_AZ) VALUES (?, ?, ?, ?, ?, ?, ?)",
                        (base_zzid, pyry, mxlx, ffsj, ffcjr, gdzt, f_az),
                    )
                try:
                    cur.execute("SELECT MAX(F_ID) FROM T_FFJL WHERE F_ZZID = ?", (base_zzid,))
                    new_id = cur.fetchone()
                    if new_id and new_id[0] is not None:
                        created_ids.append(int(new_id[0]))
                except Exception:
                    pass

        conn.commit()
    
    # ========== 质控审核记录（在事务外执行） ==========
    from app.crud.qc_audit import create_qc_audit_record, calculate_changes
    import logging
    logger = logging.getLogger(__name__)
    
    # 调试日志：检查关键变量
    logger.info(f"[DEBUG] 质控审核前置条件检查:")
    logger.info(f"  - ybzj_saved: {ybzj_saved}")
    logger.info(f"  - fzzid: {fzzid}")
    logger.info(f"  - creator_code: {creator_code}")
    logger.info(f"  - is_new_sample: {is_new_sample}")
    logger.info(f"  - old_ybzj_data 是否为空: {old_ybzj_data is None}")
    
    pdf_id = None
    
    # 1. 样本信息的质控审核记录（登记接收）
    if ybzj_saved and fzzid and creator_code:
        try:
            # 获取样本基本信息
            sample_info = get_sample_basic_info(fzzid)
            
            # ✅ 获取完整的详情数据（用于F_OLD_DATA字段）
            # 对于修改操作：应该是修改前的旧数据
            # 对于新增操作：应该是新增后的新数据
            detail_data_for_old_data = None
            if is_new_sample:
                # ✅ 新增操作：获取新增后的完整数据
                try:
                    detail_data_for_old_data = query_by_ids(fzzid, None, None)
                    logger.info(f"[登记接收质控] 成功获取新增后的详情数据用于备份，标本号: {fzzid}")
                except Exception as e:
                    logger.warning(f"[登记接收质控] 获取新增后的详情数据失败: {e}")
            else:
                # ✅ 修改操作：使用修改前的旧数据（old_detail_data）
                # 注意：old_detail_data 在函数开始时已经被获取并存储在 old_ybzj_data 中
                # 但我们需要完整的页面数据，不仅仅是 ybzj 数据
                try:
                    # 使用修改前查询的旧数据（需要在保存前查询）
                    # 由于目前代码结构限制，我们在保存后获取，但这是修改后的数据
                    # 理想情况下应该在保存前获取，但由于架构问题暂时使用 old_ybzj_data 构建
                    detail_data_for_old_data = old_detail_data if 'old_detail_data' in locals() else None
                    if not detail_data_for_old_data:
                        # 备用方案：从 old_ybzj_data 构建简化版
                        detail_data_for_old_data = {"样本登记": old_ybzj_data} if old_ybzj_data else None
                    logger.info(f"[登记接收质控] 成功获取修改前的旧详情数据用于备份，标本号: {fzzid}")
                except Exception as e:
                    logger.warning(f"[登记接收质控] 获取修改前的旧详情数据失败: {e}")
            
            # 计算变更内容
            if is_new_sample:
                # 新增：记录关键信息
                changes = {
                    "操作": "新增",
                    "关键信息": {
                        "F_ZZID": fzzid,
                        "F_XM": sample_info.get("F_XM"),
                        "F_BRBH": sample_info.get("F_BRBH"),
                        "F_ZZBW": sample_info.get("F_ZZBW")
                    }
                }
                bglx = "新增"
                logger.info(f"[DEBUG] 新增样本，changes: {changes}")
            else:
                # 修改：计算字段变更
                logger.info(f"[DEBUG] 修改样本，准备计算变更")
                logger.info(f"  - old_ybzj_data 字段数: {len(old_ybzj_data) if old_ybzj_data else 0}")
                logger.info(f"  - ybzj 内容: {ybzj}")
                changes = calculate_changes(old_ybzj_data, ybzj or {})
                bglx = "修改"
                logger.info(f"[DEBUG] 修改样本，计算出的changes: {changes}")
            
            # 如果有变更（或是新增），生成PDF并创建质控审核记录
            logger.info(f"[DEBUG] 检查是否需要创建质控审核记录:")
            logger.info(f"  - is_new_sample: {is_new_sample}")
            logger.info(f"  - changes: {changes}")
            logger.info(f"  - 条件结果: {is_new_sample or (changes.get('变更字段') and len(changes['变更字段']) > 0)}")
            
            if is_new_sample or (changes.get("变更字段") and len(changes["变更字段"]) > 0):
                logger.info(f"[DEBUG] ✅ 满足条件，创建质控审核记录（PDF将在审核通过后生成）")
                
                # ✅ 创建质控审核记录，包含F_OLD_DATA
                logger.info(f"[DEBUG] 正在创建质控审核记录...")
                logger.info(f"  - F_ZKJD: 登记接收")
                logger.info(f"  - F_BGLX: {bglx}")
                logger.info(f"  - F_ZZID: {fzzid}")
                logger.info(f"  - F_JLR: {creator_code}")
                
                audit_id = create_qc_audit_record(
                    F_ZKJD="登记接收",
                    F_BGLX=bglx,
                    F_ZZID=fzzid,
                    F_CHANGES=changes,
                    F_JLR=creator_code,
                    F_PDF=None,  # 审核通过后再生成PDF
                    F_XM=sample_info.get("F_XM"),
                    F_BRBH=sample_info.get("F_BRBH"),
                    F_ZZBW=sample_info.get("F_ZZBW"),
                    F_OLD_DATA=detail_data_for_old_data  # ✅ 新增时传入新数据，修改时传入旧数据
                )
                logger.info(f"[DEBUG] 质控审核记录创建完成，ID: {audit_id}")
                if audit_id:
                    qc_audit_ids.append(audit_id)
            else:
                logger.info(f"[DEBUG] ❌ 不满足条件，跳过质控审核记录创建")
        except Exception as e:
            logger.error(f"创建样本接收质控审核记录失败: {e}", exc_info=True)
    else:
        logger.info(f"[DEBUG] ❌ 不满足外层条件，跳过样本接收质控审核:")
        logger.info(f"  - ybzj_saved: {ybzj_saved}")
        logger.info(f"  - fzzid: {fzzid}")
        logger.info(f"  - creator_code: {creator_code}")
    
    # 2. 分发记录的质控审核记录
    if created_ids:  # 有新创建的分发记录
        for dist_id in created_ids:
            try:
                # 获取样本基本信息
                sample_info = get_sample_basic_info(fzzid) if fzzid else {}
                
                # ✅ 查询新创建的分发记录完整数据
                new_detail_data = None
                try:
                    adapter = get_adapter()
                    with adapter.connect() as conn:
                        cur = conn.cursor()
                        cur.execute("""
                            SELECT F_ID, F_ZZID, F_PYRY, F_MXLX, F_GDLX, F_FFSJ, F_FFCJR, F_GDZT, F_AZ
                            FROM T_FFJL WHERE F_ID = ?
                        """, (dist_id,))
                        row = cur.fetchone()
                        if row:
                            new_detail_data = {
                                "分发记录": {
                                    "F_ID": row[0],
                                    "F_ZZID": row[1],
                                    "F_PYRY": row[2],
                                    "F_MXLX": row[3],
                                    "F_GDLX": row[4],
                                    "F_FFSJ": row[5],
                                    "F_FFCJR": row[6],
                                    "F_GDZT": row[7],
                                    "F_AZ": row[8]
                                }
                            }
                except Exception as e:
                    logger.warning(f"[分发质控] 获取分发记录详情失败: {e}")
                
                # 分发记录的变更信息
                changes = {
                    "操作": "新增分发记录",
                    "关键信息": {
                        "分发记录ID": dist_id,
                        "标本号": fzzid
                    }
                }
                
                # 创建质控审核记录（分发场景无工单，不需要 F_YBLY）
                audit_id = create_qc_audit_record(
                    F_ZKJD="分发",
                    F_BGLX="新增",
                    F_ZZID=fzzid or "",
                    F_CHANGES=changes,
                    F_JLR=creator_code or "",
                    F_XM=sample_info.get("F_XM"),
                    F_BRBH=sample_info.get("F_BRBH"),
                    F_ZZBW=sample_info.get("F_ZZBW"),
                    F_OLD_DATA=new_detail_data  # ✅ 新增操作记录新增后的完整数据
                )
                if audit_id:
                    qc_audit_ids.append(audit_id)
            except Exception as e:
                import logging
                logger = logging.getLogger(__name__)
                logger.error(f"创建分发质控审核记录失败: {e}")
    
    return {
        "ybzj_saved": ybzj_saved, 
        "distribute_ids": created_ids, 
        "updated_ids": updated_ids,
        "qc_audit_ids": qc_audit_ids,
        "pdf_id": pdf_id
    }


def list_ybzj(q: Dict[str, Any]) -> Dict[str, Any]:
    adapter = get_adapter()
    where: List[str] = []
    params: List[Any] = []

    def like(col: str, val: str | None):
        if val:
            pred, p = adapter.like(col, val.strip())
            where.append(pred)
            params.extend(p)

    like("y.F_ZZID", q.get("F_ZZID"))
    like("y.F_BBLX", q.get("F_BBLX"))
    like("y.F_XM", q.get("F_XM"))
    like("y.F_BRBH", q.get("F_BRBH"))
    like("y.F_ZYH", q.get("F_ZYH"))
    if q.get("F_BHG"):
        where.append("y.F_BHG = ?")
        params.append(q.get("F_BHG"))
    # 是否培养：是 -> 过滤存在分发记录；否 -> 无分发记录
    sfpy = (q.get("F_SFPY") or "").strip()
    if sfpy in ("是", "否"):
        op = "> 0" if sfpy == "是" else "= 0"
        where.append(f"(SELECT COUNT(1) FROM T_FFJL ff WHERE ff.F_ZZID = y.F_ZZID) {op}")
    if q.get("F_DJRQ_START"):
        where.append("y.F_DJRQ >= ?")
        params.append(q.get("F_DJRQ_START"))
    if q.get("F_DJRQ_END"):
        where.append("y.F_DJRQ <= ?")
        params.append(q.get("F_DJRQ_END"))
    if q.get("F_CXSJ_START"):
        where.append("y.F_CXSJ >= ?")
        params.append(q.get("F_CXSJ_START"))
    if q.get("F_CXSJ_END"):
        where.append("y.F_CXSJ <= ?")
        params.append(q.get("F_CXSJ_END"))

    where_sql = (" WHERE " + " AND ".join(where)) if where else ""

    base_core = f"""
SELECT
  y.F_ZZID, y.F_BBLX, y.F_BHG,
  y.F_ZQTYS,
  y.F_XM, y.F_XB, y.F_BRBH, y.F_ZYH,
  y.F_DJRQ, y.F_CXSJ,
  y.F_SJJG,
  y.F_ZQTYS_ID
FROM T_YBZJ y
{where_sql}
"""

    order_sql = " ORDER BY y.F_ID DESC"
    count_sql = f"SELECT COUNT(1) FROM ( {base_core} ) t"
    base_select = base_core + order_sql
    sql, extra = adapter.paginate(base_select, skip=q.get("skip") or 0, limit=q.get("limit"))

    with adapter.connect() as conn:
        cur = conn.cursor()
        # total
        cur.execute(count_sql, tuple(params))
        total = int(cur.fetchone()[0])
        # items
        cur.execute(sql, tuple(params + extra))
        rows = cur.fetchall()
        cols = [c[0] for c in cur.description]
        items = [dict(zip(cols, r)) for r in rows]

        # 映射知情同意书路径；并计算培养状态
        from app.crud.file_center import get_file_by_id
        for it in items:
            # F_BHG 语义映射：true -> 不合格，false -> 合格（可保留原值）
            if it.get("F_BHG") is not None:
                val = str(it.get("F_BHG")).lower()
                it["F_BHG_MS"] = "不合格" if val == "true" else ("合格" if val == "false" else it.get("F_BHG"))
            # 日期格式化
            if it.get("F_DJRQ") is not None:
                v = it["F_DJRQ"]
                try:
                    it["F_DJRQ"] = v.strftime("%Y-%m-%d") if hasattr(v, "strftime") else str(v)
                except Exception:
                    it["F_DJRQ"] = str(v)
            for tk in ("F_CXSJ",):
                tv = it.get(tk)
                if tv is not None:
                    try:
                        it[tk] = tv.strftime("%Y-%m-%d %H:%M:%S") if hasattr(tv, "strftime") else str(tv)
                    except Exception:
                        it[tk] = str(tv)

            # 知情同意书文件列表（支持多份）
            it["F_ZQTYS_FILES"] = []
            if it.get("F_ZQTYS_ID") is not None:
                try:
                    import json
                    # 解析ID数组
                    id_list = []
                    raw_ids = it.get("F_ZQTYS_ID")
                    if isinstance(raw_ids, str):
                        try:
                            id_list = json.loads(raw_ids)
                        except:
                            # 兼容旧数据（单个ID）
                            try:
                                id_list = [int(raw_ids)]
                            except:
                                pass
                    elif isinstance(raw_ids, list):
                        id_list = raw_ids
                    elif isinstance(raw_ids, int):
                        id_list = [raw_ids]
                    
                    # 获取所有文件详情
                    for fid in id_list:
                        try:
                            f = get_file_by_id(int(fid))
                            if f:
                                it["F_ZQTYS_FILES"].append({
                                    "id": int(fid),
                                    "path": f.get("F_PATH"),
                                    "name": f.get("F_ORIGINAL_NAME")
                                })
                        except Exception:
                            pass
                except Exception:
                    pass
            # 不再返回额外的是否有知情同意书字段

            # 组织/血液培养状态
            zzid = it.get("F_ZZID")
            it["F_ZZPY"] = "未培养"
            it["F_XYPY"] = "未培养"
            try:
                # 查询分发记录
                cur.execute(
                    "SELECT F_ID, F_GDLX FROM T_FFJL WHERE F_ZZID = ? ORDER BY F_ID DESC",
                    (zzid,),
                )
                ff_rows = cur.fetchall()
                if ff_rows:
                    # 检查是否已有培养工单
                    paginated_py_sql, py_extra = adapter.paginate(
                        "SELECT F_DC FROM T_PYGD WHERE F_ZZID = ? ORDER BY F_ID DESC",
                        0,
                        1,
                    )
                    cur.execute(paginated_py_sql, (zzid,) + tuple(py_extra))
                    py = cur.fetchone()
                    latest_dc = py[0] if py else None
                    # 最近的分发记录工单类型
                    latest_gdlx = None
                    try:
                        latest_gdlx = ff_rows[0][1]
                    except Exception:
                        latest_gdlx = None
                    # 组织
                    if latest_gdlx == "组织":
                        it["F_ZZPY"] = latest_dc if latest_dc else "待创建"
                    # 血液
                    if latest_gdlx == "血液":
                        it["F_XYPY"] = latest_dc if latest_dc else "待创建"
                # 若根本没有分发记录：保持“未培养”
            except Exception:
                pass

            # 占位符字段
            it["F_LK"] = 0
            it["F_OTC"] = 0
            it["F_XQ"] = 0
            it["F_XJ"] = 0
            it["F_PBMC"] = 0
            it["F_PDO"] = 0
            it["F_CTC"] = 0
            it["F_BLSJ"] = None
            it["F_ZXSJ"] = None

        return {"items": items, "total": total}

def create_distribute(zzid: str, pyry_user_id: int, mxlx: str, creator_code: str, gdlx: str | None = None) -> Dict[str, Any]:
    """插入一条分发记录到 T_FFJL，返回插入后的完整行。

    要求：表已存在，F_ID 为自增。
    """
    adapter = get_adapter()
    now = datetime.now()
    with adapter.connect() as conn:
        cur = conn.cursor()
        
        # 从 T_YBZJ 读取 F_AZ（癌种）字段
        f_az = None
        try:
            cur.execute("SELECT F_AZ FROM T_YBZJ WHERE F_ZZID = ?", (zzid,))
            az_row = cur.fetchone()
            if az_row:
                f_az = az_row[0]
        except Exception:
            f_az = None
        
        # 插入
        if gdlx is not None:
            cur.execute(
                "INSERT INTO T_FFJL (F_ZZID, F_PYRY, F_MXLX, F_GDLX, F_FFSJ, F_FFCJR, F_GDZT, F_AZ) VALUES (?, ?, ?, ?, ?, ?, '待创建', ?)",
                (zzid, pyry_user_id, mxlx, gdlx, now, creator_code, f_az),
            )
        else:
            cur.execute(
                "INSERT INTO T_FFJL (F_ZZID, F_PYRY, F_MXLX, F_FFSJ, F_FFCJR, F_GDZT, F_AZ) VALUES (?, ?, ?, ?, ?, '待创建', ?)",
                (zzid, pyry_user_id, mxlx, now, creator_code, f_az),
            )
        # 读取自增ID（SQLServer、达梦都支持 SCOPE_IDENTITY()/IDENTITY_VALUE() 的不同写法，改为通用：按最大ID读取）
        try:
            cur.execute("SELECT MAX(F_ID) FROM T_FFJL WHERE F_ZZID = ?", (zzid,))
            new_id = int(cur.fetchone()[0])
        except Exception:
            new_id = None
        # 返回该行
        if new_id is not None:
            cur.execute("SELECT F_ID, F_ZZID, F_PYRY, F_MXLX, F_GDLX, F_FFSJ, F_FFCJR FROM T_FFJL WHERE F_ID = ?", (new_id,))
        else:
            cur.execute(
                "SELECT F_ID, F_ZZID, F_PYRY, F_MXLX, F_GDLX, F_FFSJ, F_FFCJR FROM T_FFJL WHERE F_ZZID = ? ORDER BY F_ID DESC",
                (zzid,),
            )
        row = cur.fetchone()
        cols = [c[0] for c in cur.description]
        conn.commit()
        rec = dict(zip(cols, row)) if row else {
            "F_ID": new_id or 0,
            "F_ZZID": zzid,
            "F_PYRY": pyry_user_id,
            "F_MXLX": mxlx,
            "F_GDLX": gdlx,
            "F_FFSJ": now,
            "F_FFCJR": creator_code,
        }
        # 统一 F_FFSJ 为字符串
        if rec.get("F_FFSJ") is not None:
            v = rec["F_FFSJ"]
            try:
                rec["F_FFSJ"] = v.strftime("%Y-%m-%d %H:%M:%S") if hasattr(v, "strftime") else str(v)
            except Exception:
                rec["F_FFSJ"] = str(v)
    
    # ========== 质控审核记录（在事务外执行） ==========
    qc_audit_id = None
    if new_id and zzid and creator_code:
        try:
            from app.crud.qc_audit import create_qc_audit_record, get_sample_basic_info
            import logging
            logger = logging.getLogger(__name__)
            
            # 获取样本基本信息
            sample_info = get_sample_basic_info(zzid)
            
            # ✅ 查询新创建的分发记录完整数据
            new_detail_data = None
            try:
                adapter = get_adapter()
                with adapter.connect() as conn:
                    cur = conn.cursor()
                    cur.execute("""
                        SELECT F_ID, F_ZZID, F_PYRY, F_MXLX, F_GDLX, F_FFSJ, F_FFCJR, F_GDZT, F_AZ
                        FROM T_FFJL WHERE F_ID = ?
                    """, (new_id,))
                    row = cur.fetchone()
                    if row:
                        new_detail_data = {
                            "分发记录": {
                                "F_ID": row[0],
                                "F_ZZID": row[1],
                                "F_PYRY": row[2],
                                "F_MXLX": row[3],
                                "F_GDLX": row[4],
                                "F_FFSJ": row[5],
                                "F_FFCJR": row[6],
                                "F_GDZT": row[7],
                                "F_AZ": row[8]
                            }
                        }
            except Exception as e:
                logger.warning(f"[分发质控] 获取分发记录详情失败: {e}")
            
            # 分发记录的变更信息
            changes = {
                "操作": "新增分发记录",
                "关键信息": {
                    "分发记录ID": new_id,
                    "标本号": zzid,
                    "培养人员ID": pyry_user_id,
                    "模型类型": mxlx,
                    "工单类型": gdlx
                }
            }
            
            # 创建质控审核记录（分发场景无工单，不需要 F_YBLY）
            qc_audit_id = create_qc_audit_record(
                F_ZKJD="分发",
                F_BGLX="新增",
                F_ZZID=zzid,
                F_CHANGES=changes,
                F_JLR=creator_code,
                F_XM=sample_info.get("F_XM"),
                F_BRBH=sample_info.get("F_BRBH"),
                F_ZZBW=sample_info.get("F_ZZBW"),
                F_OLD_DATA=new_detail_data  # ✅ 新增操作记录新增后的完整数据
            )
            logger.info(f"[分发质控] 创建质控审核记录成功，分发ID: {new_id}, 质控ID: {qc_audit_id}")
        except Exception as e:
            import logging
            logger = logging.getLogger(__name__)
            logger.error(f"[分发质控] 创建质控审核记录失败 (分发ID: {new_id}): {e}", exc_info=True)
    
    # 添加质控审核记录ID到返回结果
    if qc_audit_id:
        rec["qc_audit_id"] = qc_audit_id
    
    return rec


def update_distribute(rec_id: int, pyry_user_id: int | None, mxlx: str | None, gdlx: str | None) -> bool:
    """仅在 F_GDZT='待创建' 时允许更新，返回是否成功。"""
    adapter = get_adapter()
    sets: List[str] = []
    params: List[Any] = []
    if pyry_user_id is not None:
        sets.append("F_PYRY = ?")
        params.append(pyry_user_id)
    if mxlx is not None:
        sets.append("F_MXLX = ?")
        params.append(mxlx)
    if gdlx is not None:
        sets.append("F_GDLX = ?")
        params.append(gdlx)
    if not sets:
        return False
    sql = f"UPDATE T_FFJL SET {', '.join(sets)} WHERE F_ID = ? AND F_GDZT = '待创建'"
    params.append(rec_id)
    with adapter.connect() as conn:
        cur = conn.cursor()
        cur.execute(sql, tuple(params))
        affected = cur.rowcount
        conn.commit()
        return affected > 0


def delete_distribute(rec_id: int) -> bool:
    """仅在 F_GDZT='待创建' 时允许删除，返回是否成功。"""
    adapter = get_adapter()
    with adapter.connect() as conn:
        cur = conn.cursor()
        cur.execute("DELETE FROM T_FFJL WHERE F_ID = ? AND F_GDZT = '待创建'", (rec_id,))
        affected = cur.rowcount
        conn.commit()
        return affected > 0


def list_distributes(zzid: str, pyry_user_id: int | None, mxlx: str | None, skip: int = 0, limit: int | None = 20) -> List[Dict[str, Any]]:
    adapter = get_adapter()
    where: List[str] = ["F_ZZID = ?"]
    params: List[Any] = [zzid]

    if pyry_user_id is not None:
        # 直接按用户ID过滤
        where.append("F_PYRY = ?")
        params.append(pyry_user_id)

    if mxlx:
        pred, p = adapter.like("F_MXLX", mxlx)
        where.append(pred)
        params.extend(p)

    where_sql = " WHERE " + " AND ".join(where)
    base_select = f"SELECT F_ID, F_ZZID, F_PYRY, F_MXLX, F_GDLX, F_GDZT, F_FFSJ, F_FFCJR FROM T_FFJL{where_sql} ORDER BY F_FFSJ DESC"
    sql, extra = adapter.paginate(base_select, skip=skip or 0, limit=limit)

    with adapter.connect() as conn:
        cur = conn.cursor()
        # items
        cur.execute(sql, tuple(params + extra))
        rows = cur.fetchall()
        cols = [c[0] for c in cur.description]
        items = [dict(zip(cols, r)) for r in rows]

        # 映射 F_PYRY_NAME（根据 T_USER.F_KTYPE），此时 F_PYRY 为用户ID
        pyry_names: Dict[int, str] = {}
        try:
            ids = list({int(it.get("F_PYRY")) for it in items if it.get("F_PYRY") is not None})
            if ids:
                q_marks = ",".join(["?"] * len(ids))
                from app.crud.user import _name_column
                col = _name_column(adapter, quoted=True)
                cur.execute(f"SELECT F_ID, {col} FROM T_USER WHERE F_ID IN ({q_marks})", tuple(ids))
                for uid, name in cur.fetchall():
                    pyry_names[int(uid)] = name
        except Exception:
            pass

        # 统一时间与姓名输出
        for it in items:
            v = it.get("F_FFSJ")
            if v is not None:
                try:
                    it["F_FFSJ"] = v.strftime("%Y-%m-%d %H:%M:%S") if hasattr(v, "strftime") else str(v)
                except Exception:
                    it["F_FFSJ"] = str(v)
            uid = it.get("F_PYRY")
            it["F_PYRY_NAME"] = pyry_names.get(int(uid)) if uid is not None else None

        return items


def _filter_existing_columns(cur, adapter, table: str, data: Dict[str, Any] | None) -> Dict[str, Any] | None:
    if not data:
        return data
    if isinstance(adapter, DamengAdapter):
        return data
    filtered: Dict[str, Any] = {}
    for key, value in data.items():
        try:
            if adapter.column_exists(cur, table, key):
                filtered[key] = value
        except Exception:
            continue
    return filtered

