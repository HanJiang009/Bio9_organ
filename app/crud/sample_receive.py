from typing import Optional, Dict, Any
from app.db.factory import get_adapter
from app.db.adapters.dameng import DamengAdapter
from typing import Tuple
from datetime import datetime
from typing import List
from typing import Tuple as _Tuple
from app.utils.field_coerce import dump_str_list, parse_str_list


def check_zzid_exists(zzid: str) -> Optional[Dict[str, Any]]:
    """查重：检查标本号是否已存在于 T_YBZJ。

    返回:
      - None：不存在（可以新建）
      - dict：已存在的基础信息 {"F_ZZID", "F_XM", "F_BRBH", "F_DJRQ", "F_DJR"}
    """
    if not zzid or not zzid.strip():
        return None
    adapter = get_adapter()
    rows, cols = adapter.fetch_all(
        "SELECT F_ZZID, F_XM, F_BRBH, F_DJRQ, F_DJR FROM T_YBZJ WHERE F_ZZID = ?",
        [zzid.strip()],
    )
    if not rows:
        return None
    d = dict(zip(cols, rows[0]))
    if d.get("F_DJRQ") is not None:
        v = d["F_DJRQ"]
        try:
            d["F_DJRQ"] = v.strftime("%Y-%m-%d") if hasattr(v, "strftime") else str(v)
        except Exception:
            d["F_DJRQ"] = str(v)
    return d


def _parse_optional_int(value: Any) -> Optional[int]:
    """将入参解析为可选整数 ID。"""
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def get_ybzj_by_id(ybzj_id: int) -> Optional[Dict[str, Any]]:
    """根据 T_YBZJ.F_ID 获取样本登记记录。"""
    adapter = get_adapter()
    rows, cols = adapter.fetch_all("SELECT * FROM T_YBZJ WHERE F_ID = ?", [ybzj_id])
    if not rows:
        return None
    return dict(zip(cols, rows[0]))


_DELETE_BLOCKED_MSG = "已产生分发/冻存数据，不支持删除"
_ZZID_SEQUENCE_RULES = ("F_ZZID", "__FALLBACK_ZZID__")


def _match_recyclable_sequence(zzid: str, rows: List[tuple]) -> Optional[int]:
    """被删标本号正好是某条编码流水的当前号时，返回该流水行 F_ID。"""
    text = (zzid or "").strip()
    best_id: Optional[int] = None
    best_len = -1
    for seq_id, prefix, current in rows:
        prefix_text = "" if prefix is None else str(prefix)
        if not prefix_text or not text.startswith(prefix_text):
            continue
        suffix = text[len(prefix_text):]
        if not suffix.isdigit():
            continue
        try:
            current_value = int(current)
            suffix_value = int(suffix)
        except (TypeError, ValueError):
            continue
        if suffix_value != current_value or current_value < 1:
            continue
        if len(prefix_text) > best_len:
            best_len = len(prefix_text)
            best_id = int(seq_id)
    return best_id


def delete_received_sample(ybzj_id: int) -> Dict[str, Any]:
    """删除尚未分发、冻存或建培养工单的标本，并在标本号是当前流水末号时回退一位。"""
    adapter = get_adapter()
    with adapter.connect() as conn:
        cur = conn.cursor()
        cur.execute("SELECT F_ID, F_ZZID FROM T_YBZJ WHERE F_ID = ?", (int(ybzj_id),))
        row = cur.fetchone()
        if not row or not row[1]:
            raise ValueError("标本不存在")
        zzid = str(row[1]).strip()

        blocked = False
        for sql in (
            "SELECT 1 FROM T_FFJL WHERE F_ZZID = ?",
            "SELECT 1 FROM T_YBFZ WHERE F_ZZID = ?",
            "SELECT 1 FROM T_PYGD WHERE F_ZZID = ?",
        ):
            cur.execute(sql, (zzid,))
            if cur.fetchone():
                blocked = True
                break
        if blocked:
            raise ValueError(_DELETE_BLOCKED_MSG)

        from app.crud.qc_audit import zkjd_variants
        stages: List[str] = []
        for name in ("登记接收", "标为不合格"):
            stages.extend(zkjd_variants(name))
        placeholders = ", ".join(["?"] * len(stages))
        cur.execute(
            f"DELETE FROM T_ZKSH WHERE F_ZZID = ? AND F_ZKJD IN ({placeholders})",
            (zzid, *stages),
        )
        cur.execute("DELETE FROM T_YBZJ WHERE F_ID = ?", (int(ybzj_id),))

        rule_marks = ", ".join(["?"] * len(_ZZID_SEQUENCE_RULES))
        cur.execute(
            f"""
            SELECT F_ID, F_PREFIX, F_CURRENT_VALUE
            FROM T_DICT_CODE_SEQUENCE
            WHERE F_RULE_CODE IN ({rule_marks})
            """,
            _ZZID_SEQUENCE_RULES,
        )
        seq_id = _match_recyclable_sequence(zzid, cur.fetchall() or [])
        recycled = False
        if seq_id is not None:
            cur.execute(
                "UPDATE T_DICT_CODE_SEQUENCE SET F_CURRENT_VALUE = F_CURRENT_VALUE - 1 WHERE F_ID = ? AND F_CURRENT_VALUE >= 1",
                (seq_id,),
            )
            recycled = cur.rowcount > 0
        conn.commit()

    return {"F_ID": int(ybzj_id), "F_ZZID": zzid, "F_ZZID_RECYCLED": recycled}


def query_by_ids(F_ZZID: Optional[str], F_ZYH: Optional[str], F_SJTMH: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """根据标本号、住院号或送检条码号查询 T_YBZJ。

    - 主表：T_YBZJ（已整合 JZXX/BBXX 字段）
    
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
                   F_BBLX, F_CXSJ, F_LTSJ, F_SJTMH, F_CSNY, F_XMMC, F_XMBH
            FROM T_YBZJ WHERE F_SJTMH = ? ORDER BY F_ID DESC
        """
        sj_sql_paginated, sj_extra = adapter.paginate(sj_sql, 0, 1)
        rows, cols = adapter.fetch_all(sj_sql_paginated, [F_SJTMH] + sj_extra)
        if rows:
            z_row = dict(zip(cols, rows[0]))
            zzid = z_row.get("F_ZZID")
    
    # 如果仍然没有 F_ZZID，通过住院号到 T_YBZJ 解析 F_ZZID
    if not zzid and F_ZYH:
        zyh_sql = "SELECT F_ZZID FROM T_YBZJ WHERE F_ZYH = ? ORDER BY F_ID DESC"
        zyh_paginated, zyh_extra = adapter.paginate(zyh_sql, 0, 1)
        try:
            rows, cols = adapter.fetch_all(zyh_paginated, [F_ZYH] + zyh_extra)
            if rows:
                zzid = rows[0][0]
        except Exception:
            pass
    
    if not zzid:
        return None

    # 读取最新一条 YBZJ
    y_sql = "SELECT * FROM T_YBZJ WHERE F_ZZID = ? ORDER BY F_ID DESC"
    y_paginated, y_extra = adapter.paginate(y_sql, 0, 1)
    y_rows, y_cols = adapter.fetch_all(y_paginated, [zzid] + y_extra)
    ybzj = dict(zip(y_cols, y_rows[0])) if y_rows else {}

    # z_row 为 None 时（未通过 F_ZYH/F_SJTMH 途径填充），以空字典代替，所有字段从 T_YBZJ 取
    if z_row is None:
        z_row = {}

    # 组装 JZXX（优先 YBZJ）
    jz_fields = [
        "F_XM","F_BRBH","F_ZYH","F_XB","F_NL","F_DH","F_SFZH","F_DZ",
        "F_BBLY","F_LCZD","F_ZQTYS","F_ZQTYSLB","F_MZH","F_CHH","F_BZTZ",
        "F_SJTMH",  # 送检条码号
        "F_CSNY",  # 出生年月
        "F_XMMC",  # 项目名称（快照）
        "F_XMBH",  # 项目编号（快照）
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

    # 病灶特征：库内 JSON 字符串，出参统一字符串数组
    patient["F_BZTZ"] = parse_str_list(patient.get("F_BZTZ"))
    try:
        from app.core.emr_client import try_build_emr_url
        patient["F_DZBL"] = try_build_emr_url(patient)
    except Exception:
        patient["F_DZBL"] = None

    # F_SYTP_FILES: 由 JSON 数组 ID 映射为文件详情数组（兼容旧的单整数值）
    ybzj["F_SYTP_FILES"] = []
    if ybzj and ybzj.get("F_SYTP") is not None:
        try:
            from app.crud.file_center import get_file_by_id as _get_sytp_file
            import json as _json

            raw_sytp = ybzj.get("F_SYTP")
            id_list: list = []
            if isinstance(raw_sytp, str):
                try:
                    parsed = _json.loads(raw_sytp)
                    id_list = parsed if isinstance(parsed, list) else [int(parsed)]
                except Exception:
                    try:
                        id_list = [int(raw_sytp)]
                    except Exception:
                        pass
            elif isinstance(raw_sytp, int):
                id_list = [raw_sytp]
            elif isinstance(raw_sytp, list):
                id_list = raw_sytp

            sytp_files = []
            for fid in id_list:
                try:
                    f = _get_sytp_file(int(fid))
                    if f:
                        sytp_files.append({
                            "id": int(fid),
                            "path": f.get("F_PATH"),
                            "name": f.get("F_ORIGINAL_NAME"),
                        })
                except Exception:
                    pass
            ybzj["F_SYTP_FILES"] = sytp_files
        except Exception:
            pass

    # JZXX.F_ZQTYS_FILES: 知情同意书文件详情数组（来自 YBZJ.F_ZQTYS_ID）
    patient["F_ZQTYS_FILES"] = []
    if ybzj and ybzj.get("F_ZQTYS_ID") is not None:
        try:
            from app.crud.file_center import get_file_by_id as _get_file
            import json

            id_list = []
            raw_ids = ybzj.get("F_ZQTYS_ID")
            if isinstance(raw_ids, str):
                try:
                    id_list = json.loads(raw_ids)
                except Exception:
                    try:
                        id_list = [int(raw_ids)]
                    except Exception:
                        pass
            elif isinstance(raw_ids, list):
                id_list = raw_ids
            elif isinstance(raw_ids, int):
                id_list = [raw_ids]

            consent_files = []
            for cid in id_list:
                try:
                    cf = _get_file(int(cid))
                    if cf:
                        consent_files.append({
                            "id": int(cid),
                            "path": cf.get("F_PATH"),
                            "name": cf.get("F_ORIGINAL_NAME"),
                        })
                except Exception:
                    pass
            patient["F_ZQTYS_FILES"] = consent_files
        except Exception:
            pass

    adapter2 = get_adapter()
    df_rows, df_cols = adapter2.fetch_all(
        """
        SELECT F_ID, F_ZZID, F_PYRY, F_MXLX, F_GDLX, F_FFSJ, F_FFCJR, F_GDZT, F_QYWC
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


def _collect_empty_string_fields(d: Dict[str, Any] | None) -> set[str]:
    """收集入参中显式传空字符串的字段名（须在 _normalize_empty_fields 之前调用）。"""
    cleared: set[str] = set()
    if not d:
        return cleared
    for k, v in d.items():
        if k in ("F_ZZID", "F_ID", "__null_fields__"):
            continue
        if isinstance(v, str) and v.strip() == "":
            cleared.add(k)
    return cleared


def _expand_cleared_field_aliases(cleared: set[str]) -> set[str]:
    """空字符串清空时的字段别名（如 JZXX.F_BGYJDZ -> YBZJ.F_DZ）。"""
    out = set(cleared)
    if "F_BGYJDZ" in out:
        out.add("F_DZ")
    return out


def _normalize_empty_fields(d: Dict[str, Any] | None) -> Dict[str, Any] | None:
    """空串 -> None，避免数据库类型转换错误。"""
    if not d:
        return d
    for k, v in list(d.items()):
        if isinstance(v, str) and v.strip() == "":
            d[k] = None
    return d


def _serialize_bztz_for_db(d: Dict[str, Any] | None) -> Optional[set[str]]:
    """F_BZTZ 数组落库前转为 JSON 字符串。返回需要显式写 NULL 的字段集合。"""
    if not d or "F_BZTZ" not in d:
        return None
    dumped = dump_str_list(d.get("F_BZTZ"))
    d["F_BZTZ"] = dumped
    return {"F_BZTZ"} if dumped is None else None


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
    cleared_fields = _expand_cleared_field_aliases(
        _collect_empty_string_fields(jzxx)
        | _collect_empty_string_fields(bbxx)
        | _collect_empty_string_fields(ybzj)
    )
    jzxx = _normalize_empty_fields(jzxx)
    bbxx = _normalize_empty_fields(bbxx)
    ybzj = _normalize_empty_fields(ybzj)
    with adapter.connect() as conn:
        cur = conn.cursor()
        # T_ZZXX 已废弃，不再写入

        # 合并工具（空字符串字段规范化后为 None，仍应写入以清空数据库列）
        def merge_into(target: Dict[str, Any] | None, src: Dict[str, Any] | None, keys: List[str]) -> Dict[str, Any]:
            if target is None:
                target = {}
            if not src:
                return target
            for k in keys:
                if k in src and (src.get(k) is not None or k in cleared_fields):
                    target[k] = src.get(k)
            return target

        jz_keys = [
            "F_XM","F_BRBH","F_ZYH","F_XB","F_NL","F_DH","F_SFZH","F_DZ",
            "F_BBLY","F_LCZD","F_ZQTYS","F_ZQTYSLB","F_MZH","F_CHH","F_BZTZ",
            "F_SJTMH",  # 送检条码号
            "F_XMMC",  # 项目名称（快照）
            "F_XMBH",  # 项目编号（快照）
        ]
        bb_keys = [
            "F_BBLX","F_SSMC","F_SSRQ","F_CXSJ","F_LTSJ","F_CRB",
            "F_JSSJ","F_CJBW","F_QYFS","F_SSS","F_SSYS",
            "F_ZQTYS","F_ZQTYSLB","F_BZTZ",
        ]
        ybzj = merge_into(ybzj, jzxx, jz_keys)
        ybzj = merge_into(ybzj, bbxx, bb_keys)
        null_from_bztz = _serialize_bztz_for_db(ybzj)
        if null_from_bztz:
            cleared_fields.update(null_from_bztz)

        # T_YBZJ upsert（按 F_ZZID 唯一，幂等保护：INSERT 并发冲突时自动降级为 UPDATE）
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
            try:
                cur.execute(f"INSERT INTO T_YBZJ ({', '.join(keys)}) VALUES ({placeholders})", tuple(ybzj[c] for c in keys))
            except Exception as _ins_err:
                # 幂等保护：并发竞态导致唯一键冲突，自动降级为 UPDATE
                import logging as _log
                _ins_msg = str(_ins_err).lower()
                _unique_kws = ("unique", "duplicate", "primary key", "constraint", "违反", "唯一")
                if any(kw in _ins_msg for kw in _unique_kws):
                    _log.getLogger(__name__).warning(
                        f"[T_YBZJ 幂等] INSERT 并发冲突，降级为 UPDATE: F_ZZID={yz}"
                    )
                    set_cols = [k for k in keys if k != "F_ZZID"]
                    if set_cols:
                        sets = ", ".join([f"{c} = ?" for c in set_cols])
                        params = [ybzj[c] for c in set_cols] + [yz]
                        cur.execute(f"UPDATE T_YBZJ SET {sets} WHERE F_ZZID = ?", tuple(params))
                else:
                    raise
def mark_unqualified(zzid: str, reason: str | None, creator_code: str = "") -> Optional[int]:
    """记录不合格原因并创建质控审核记录，等待审核通过后才真正标记为不合格。
    
    流程：
    1. 设置 F_BHG='pending'（不合格审批中），记录 F_BHGYY
    2. 创建质控审核记录（待审核）
    3. 审核通过后，通过 /qc/review 接口更新 F_BHG='true'
    4. 审核不通过则恢复 F_BHG='false'（合格）

    返回: 质控审核记录ID（如果创建成功）
    """
    adapter = get_adapter()
    original_bhg = "合格"
    with adapter.connect() as conn:
        cur = conn.cursor()
        cur.execute("SELECT F_BHG FROM T_YBZJ WHERE F_ZZID = ?", (zzid,))
        row = cur.fetchone()
        if row:
            val = str(row[0] or "").lower()
            if val == "true":
                original_bhg = "不合格"
            elif val == "false" or val == "":
                original_bhg = "合格"
            else:
                original_bhg = str(row[0])
            cur.execute(
                "UPDATE T_YBZJ SET F_BHG = 'pending', F_BHGYY = ? WHERE F_ZZID = ?",
                (reason or '', zzid),
            )
        else:
            cur.execute(
                "INSERT INTO T_YBZJ (F_ZZID, F_BHG, F_BHGYY) VALUES (?, 'pending', ?)",
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
                "原状态": original_bhg,
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


# 从 T_YBZJ 继承到 T_PYGD 的字段集合，标本更新时需级联同步到所有关联工单
_PYGD_CASCADE_FIELDS = {"F_XM", "F_BRBH", "F_BBLX", "F_QYFS", "F_AZ", "F_XYZL"}


def cascade_update_culture_orders(zzid: str, merged_data: Dict[str, Any]) -> int:
    """将标本字段变更级联更新到所有关联培养工单（含传代子代工单）。

    由于原代工单和所有传代子代工单共享相同的 F_ZZID，
    一次 UPDATE WHERE F_ZZID=? 即可覆盖全部代次，无需递归遍历。

    返回受影响的工单数量（0 表示无可级联字段或无关联工单）。
    """
    import logging
    logger = logging.getLogger(__name__)

    cascade = {k: v for k, v in merged_data.items() if k in _PYGD_CASCADE_FIELDS}
    if not cascade:
        return 0

    adapter = get_adapter()
    with adapter.connect() as conn:
        cur = conn.cursor()
        # F_XYZL 为可选列，需先探测 T_PYGD 中是否存在
        if "F_XYZL" in cascade:
            try:
                if not adapter.column_exists(cur, "T_PYGD", "F_XYZL"):
                    cascade.pop("F_XYZL")
            except Exception:
                cascade.pop("F_XYZL", None)
        if not cascade:
            return 0

        cols = list(cascade.keys())
        sets = ", ".join(f"{c} = ?" for c in cols)
        params = [cascade[c] for c in cols] + [zzid]
        cur.execute(f"UPDATE T_PYGD SET {sets} WHERE F_ZZID = ?", tuple(params))
        conn.commit()

        try:
            cur.execute("SELECT COUNT(*) FROM T_PYGD WHERE F_ZZID = ?", (zzid,))
            row = cur.fetchone()
            count = int(row[0]) if row else 0
        except Exception:
            count = 0

        logger.info(
            f"[cascade] 标本 {zzid} 级联更新培养工单完成，"
            f"字段={list(cascade.keys())}，影响工单数={count}"
        )
        return count


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

    # 级联更新关联培养工单（在 T_YBZJ 事务提交后执行，避免锁竞争）
    if u3:
        try:
            cascade_update_culture_orders(zzid, merged)
        except Exception:
            import logging
            logging.getLogger(__name__).warning(
                f"[cascade] 级联更新培养工单失败 zzid={zzid}", exc_info=True
            )
    return False, False, u3


def save_and_distribute(
    jzxx: Dict[str, Any] | None,
    bbxx: Dict[str, Any] | None,
    ybzj: Dict[str, Any] | None,
    distributes: List[Dict[str, Any]] | None,
    creator_code: str | None,
    ybzj_id: Optional[int] = None,
) -> Dict[str, Any]:
    """在一个事务内：保存/更新 JZXX/BBXX/YBZJ，并批量创建 T_FFJL 记录。
    
    同时记录质控审核信息。

    返回：{"ybzj_saved": bool, "distribute_ids": List[int], "qc_audit_ids": List[int], "pdf_id": int, "ybzj_id": int|None}
    """
    from app.crud.qc_audit import get_old_data_for_comparison, get_sample_basic_info
    
    adapter = get_adapter()
    ybzj_saved = False
    created_ids: List[int] = []
    updated_ids: List[int] = []
    qc_audit_ids: List[int] = []  # 质控审核记录ID列表
    saved_ybzj_id: Optional[int] = None

    is_new_sample = ybzj_id is None

    # 获取F_ZZID（用于后续查询）
    fzzid = None
    if ybzj_id:
        old_ybzj_row = get_ybzj_by_id(ybzj_id)
        if not old_ybzj_row:
            raise ValueError(f"样本记录 F_ID={ybzj_id} 不存在")
        old_ybzj_data = old_ybzj_row
        fzzid = old_ybzj_row.get("F_ZZID")
        saved_ybzj_id = ybzj_id
    else:
        old_ybzj_data = None
        if ybzj and ybzj.get("F_ZZID"):
            fzzid = ybzj.get("F_ZZID")
        elif bbxx and bbxx.get("F_ZZID"):
            fzzid = bbxx.get("F_ZZID")
        elif jzxx and jzxx.get("F_ZZID"):
            fzzid = jzxx.get("F_ZZID")
    
    with adapter.connect() as conn:
        cur = conn.cursor()
        cleared_fields = _expand_cleared_field_aliases(
            _collect_empty_string_fields(jzxx)
            | _collect_empty_string_fields(bbxx)
            | _collect_empty_string_fields(ybzj)
        )
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
                if k in bbxx and (bbxx.get(k) is not None or k in cleared_fields):
                    if ybzj is None:
                        ybzj = {"F_ZZID": bbxx.get("F_ZZID")}
                    ybzj[k] = bbxx.get(k)
            # 也合并 BBXX 的其他关键字段到 YBZJ (包括 F_BZTZ 病灶特征、F_LCZD 临床诊断、F_AZ 癌种)
            for k in ("F_BBLX", "F_SSMC", "F_SSRQ", "F_CXSJ", "F_LTSJ", "F_CRB", "F_ZQTYS", "F_ZQTYSLB", "F_BZTZ", "F_LCZD", "F_AZ"):
                if k in bbxx and (bbxx.get(k) is not None or k in cleared_fields):
                    if ybzj is None:
                        ybzj = {"F_ZZID": bbxx.get("F_ZZID")}
                    ybzj[k] = bbxx.get(k)
        if jzxx:
            for k, v in jzxx.items():
                if k == "F_ZZID":
                    continue
                if v is not None or k in cleared_fields:
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
            # 提取需要显式写 NULL 的字段列表，并从 ybzj 中移除该元数据键
            explicit_null_fields: set = set(ybzj.pop("__null_fields__", []) or [])
            explicit_null_fields.update(cleared_fields)
            null_from_bztz = _serialize_bztz_for_db(ybzj)
            if null_from_bztz:
                explicit_null_fields.update(null_from_bztz)
            ybzj = _filter_existing_columns(cur, adapter, "T_YBZJ", ybzj) or {}
            yz = ybzj.get("F_ZZID")
            if not yz:
                ybzj_saved = False
            else:
                # 普通字段：非 None；显式 NULL 字段：即使值为 None 也纳入
                keys = [k for k in ybzj.keys()
                        if ybzj.get(k) is not None or k == "F_ZZID" or k in explicit_null_fields]
                keys = [k for k in keys if k != "F_ID"]
                if not keys:
                    ybzj_saved = True
                elif ybzj_id:
                    set_cols = [k for k in keys if k not in ("F_ZZID", "F_ID")]
                    if set_cols:
                        sets = ", ".join([f"{c} = ?" for c in set_cols])
                        params = [ybzj[c] for c in set_cols] + [ybzj_id]

                        import logging
                        logger = logging.getLogger(__name__)
                        logger.info(f"[DEBUG] UPDATE T_YBZJ by F_ID={ybzj_id} set_cols: {set_cols}")

                        try:
                            cur.execute(f"UPDATE T_YBZJ SET {sets} WHERE F_ID = ?", tuple(params))
                            ybzj_saved = True
                            saved_ybzj_id = ybzj_id
                        except Exception as e:
                            logger.error(f"[ERROR] 更新T_YBZJ失败: {e}")
                            raise Exception(f"更新T_YBZJ失败: {e}")
                    else:
                        ybzj_saved = True
                        saved_ybzj_id = ybzj_id
                else:
                    cur.execute("SELECT 1 FROM T_YBZJ WHERE F_ZZID = ?", (yz,))
                    if cur.fetchone() is not None:
                        raise ValueError(f"标本号 {yz} 已存在，请重新预览获取")

                    placeholders = ", ".join(["?" for _ in keys])
                    if placeholders:
                        import logging
                        logger = logging.getLogger(__name__)
                        logger.info(f"[DEBUG] INSERT T_YBZJ keys: {keys}")

                        try:
                            cur.execute(
                                f"INSERT INTO T_YBZJ ({', '.join(keys)}) VALUES ({placeholders})",
                                tuple(ybzj[c] for c in keys),
                            )
                            ybzj_saved = True
                            cur.execute(
                                "SELECT F_ID FROM T_YBZJ WHERE F_ZZID = ? ORDER BY F_ID DESC",
                                (yz,),
                            )
                            id_row = cur.fetchone()
                            if id_row and id_row[0] is not None:
                                saved_ybzj_id = int(id_row[0])
                        except Exception as e:
                            _ins_msg = str(e).lower()
                            _unique_kws = ("unique", "duplicate", "primary key", "constraint", "违反", "唯一")
                            if any(kw in _ins_msg for kw in _unique_kws):
                                raise ValueError(f"标本号 {yz} 已存在，请重新预览获取") from e
                            logger.error(f"[ERROR] 插入T_YBZJ失败: {e}")
                            raise Exception(f"插入T_YBZJ失败: {e}")
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

            # 拦截：若该标本已标记取用完成，禁止新增分发记录
            has_new_inserts = any(
                isinstance(d, dict) and d.get("F_ID") is None
                for d in distributes
            )
            if has_new_inserts and fzzid:
                try:
                    cur.execute("SELECT F_QYWC FROM T_YBZJ WHERE F_ZZID = ?", (fzzid,))
                    qywc_row = cur.fetchone()
                    if qywc_row and qywc_row[0] == 1:
                        raise ValueError(f"标本 {fzzid} 已标记取用完成，无法再创建分发记录")
                except ValueError:
                    raise
                except Exception:
                    pass  # F_QYWC 列不存在时跳过拦截

            # 标记本次批次中是否有需要写回取用完成的记录
            any_qywc_done = False

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
                    for key in ("F_PYRY", "F_MXLX", "F_GDLX", "F_FFSJ", "F_FFCJR", "F_GDZT", "F_QYWC"):
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
                    if d.get("F_QYWC") == 1:
                        any_qywc_done = True
                    continue

                pyry = d.get("F_PYRY")
                mxlx = d.get("F_MXLX")
                gdlx = d.get("F_GDLX")
                if pyry is None or not mxlx:
                    continue
                ffsj = _parse_datetime(d.get("F_FFSJ")) or _dt.now()
                ffcjr = d.get("F_FFCJR") or creator_code or ""
                gdzt = d.get("F_GDZT") or "待创建"
                d_qywc = 1 if d.get("F_QYWC") == 1 else 0
                if d_qywc == 1:
                    any_qywc_done = True
                
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
                        "INSERT INTO T_FFJL (F_ZZID, F_PYRY, F_MXLX, F_GDLX, F_FFSJ, F_FFCJR, F_GDZT, F_AZ, F_QYWC) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        (base_zzid, pyry, mxlx, gdlx, ffsj, ffcjr, gdzt, f_az, d_qywc),
                    )
                else:
                    cur.execute(
                        "INSERT INTO T_FFJL (F_ZZID, F_PYRY, F_MXLX, F_FFSJ, F_FFCJR, F_GDZT, F_AZ, F_QYWC) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                        (base_zzid, pyry, mxlx, ffsj, ffcjr, gdzt, f_az, d_qywc),
                    )
                try:
                    cur.execute("SELECT MAX(F_ID) FROM T_FFJL WHERE F_ZZID = ?", (base_zzid,))
                    new_id = cur.fetchone()
                    if new_id and new_id[0] is not None:
                        created_ids.append(int(new_id[0]))
                except Exception:
                    pass

            # 写回 T_YBZJ.F_QYWC=1（取用完成的权威状态）
            if any_qywc_done and fzzid:
                try:
                    cur.execute("UPDATE T_YBZJ SET F_QYWC = 1 WHERE F_ZZID = ?", (fzzid,))
                except Exception:
                    pass  # 列不存在时忽略

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
                    logger.info(f"[登记接收质控] query_by_ids 返回: {type(detail_data_for_old_data)}, keys={list(detail_data_for_old_data.keys()) if detail_data_for_old_data else None}")
                    
                    # ✅ 转换文件ID为静态地址（用于F_OLD_DATA）
                    if detail_data_for_old_data and detail_data_for_old_data.get("YBZJ"):
                        ybzj_data = detail_data_for_old_data["YBZJ"]
                        jzxx_data = detail_data_for_old_data.get("JZXX", {})
                        
                        # 转换 F_SYTP：使用文件详情数组（F_SYTP_FILES 已由 query_by_ids 组装）
                        if ybzj_data.get("F_SYTP_FILES"):
                            ybzj_data["F_SYTP"] = ybzj_data["F_SYTP_FILES"]

                        # 转换 F_ZQTYS_ID：使用文件详情数组替换原始ID数组
                        if jzxx_data.get("F_ZQTYS_FILES"):
                            ybzj_data["F_ZQTYS_ID"] = jzxx_data["F_ZQTYS_FILES"]
                    
                    # 尝试序列化，提前发现不可序列化的字段
                    import json as _test_json
                    _test_json.dumps(detail_data_for_old_data, ensure_ascii=False, default=str)
                    logger.info(f"[登记接收质控] 成功获取新增后的详情数据用于备份，标本号: {fzzid}")
                except Exception as e:
                    import traceback
                    logger.error(f"[登记接收质控] 获取新增后的详情数据失败: {e}\n{traceback.format_exc()}")
            else:
                # ✅ 修改操作：使用修改前的旧数据（old_detail_data）
                # 注意：old_detail_data 在函数开始时已经被获取并存储在 old_ybzj_data 中
                # 但我们需要完整的页面数据，不仅仅是 ybzj 数据
                try:
                    # 使用保存前已获取的 old_ybzj_data 构建旧数据快照
                    detail_data_for_old_data = None
                    if not detail_data_for_old_data:
                        # 备用方案：从 old_ybzj_data 构建简化版，并转换文件ID
                        if old_ybzj_data:
                            # ✅ 转换文件ID为静态地址
                            converted_data = old_ybzj_data.copy()
                            
                            # 转换 F_SYTP：解析 JSON 数组，转换为文件详情数组
                            if converted_data.get("F_SYTP"):
                                try:
                                    import json as _j
                                    from app.crud.file_center import get_file_by_id as _gf
                                    raw_sytp = converted_data.get("F_SYTP")
                                    sid_list: list = []
                                    if isinstance(raw_sytp, str):
                                        try:
                                            parsed = _j.loads(raw_sytp)
                                            sid_list = parsed if isinstance(parsed, list) else [int(parsed)]
                                        except Exception:
                                            try:
                                                sid_list = [int(raw_sytp)]
                                            except Exception:
                                                pass
                                    elif isinstance(raw_sytp, int):
                                        sid_list = [raw_sytp]
                                    elif isinstance(raw_sytp, list):
                                        sid_list = raw_sytp
                                    sytp_files = []
                                    for sid in sid_list:
                                        try:
                                            sf = _gf(int(sid))
                                            if sf:
                                                sytp_files.append({"id": int(sid), "path": sf.get("F_PATH"), "name": sf.get("F_ORIGINAL_NAME")})
                                        except Exception:
                                            pass
                                    if sytp_files:
                                        converted_data["F_SYTP"] = sytp_files
                                except Exception:
                                    pass

                            # 转换 F_ZQTYS_ID：解析 JSON 数组，转换为文件详情数组
                            if converted_data.get("F_ZQTYS_ID"):
                                try:
                                    import json
                                    from app.crud.file_center import get_file_by_id

                                    id_list = []
                                    raw_ids = converted_data.get("F_ZQTYS_ID")
                                    if isinstance(raw_ids, str):
                                        try:
                                            id_list = json.loads(raw_ids)
                                        except Exception:
                                            try:
                                                id_list = [int(raw_ids)]
                                            except Exception:
                                                pass
                                    elif isinstance(raw_ids, list):
                                        id_list = raw_ids
                                    elif isinstance(raw_ids, int):
                                        id_list = [raw_ids]

                                    consent_files = []
                                    for cid in id_list:
                                        try:
                                            cf = get_file_by_id(int(cid))
                                            if cf:
                                                consent_files.append({
                                                    "id": int(cid),
                                                    "path": cf.get("F_PATH"),
                                                    "name": cf.get("F_ORIGINAL_NAME"),
                                                })
                                        except Exception:
                                            pass

                                    if consent_files:
                                        converted_data["F_ZQTYS_ID"] = consent_files
                                except Exception:
                                    pass
                            
                            detail_data_for_old_data = {"样本登记": converted_data}
                        else:
                            detail_data_for_old_data = None
                    
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
                            pyry_id = row[2]
                            pyry_name = None
                            
                            # ✅ 将F_PYRY从用户ID转换为姓名
                            if pyry_id:
                                try:
                                    cur.execute("SELECT F_Ktype FROM T_USER WHERE F_ID = ?", (pyry_id,))
                                    user_row = cur.fetchone()
                                    if user_row:
                                        pyry_name = user_row[0]
                                except Exception as e:
                                    logger.warning(f"[分发质控] 查询培养人员姓名失败 (ID: {pyry_id}): {e}")
                            
                            new_detail_data = {
                                "分发记录": {
                                    "F_ID": row[0],
                                    "F_ZZID": row[1],
                                    "F_PYRY": pyry_name or pyry_id,  # 优先使用姓名，查询失败则保留ID
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
    
    # 级联更新关联培养工单（标本字段变更同步到 T_PYGD）
    if ybzj_saved and fzzid:
        try:
            cascade_update_culture_orders(fzzid, ybzj or {})
        except Exception:
            import logging as _clog
            _clog.getLogger(__name__).warning(
                f"[cascade] save_and_distribute 级联更新培养工单失败 zzid={fzzid}", exc_info=True
            )

    return {
        "ybzj_saved": ybzj_saved, 
        "distribute_ids": created_ids, 
        "updated_ids": updated_ids,
        "qc_audit_ids": qc_audit_ids,
        "pdf_id": pdf_id,
        "ybzj_id": saved_ybzj_id,
    }


def _normalize_bhg_filter_labels(raw: Any) -> List[str]:
    """将 F_BHG 入参归一化为标签列表（兼容 str / list）。"""
    if raw is None:
        return []
    if isinstance(raw, str):
        s = raw.strip()
        return [] if not s or s == "全部" else [s]
    if isinstance(raw, list):
        items = [
            str(x).strip()
            for x in raw
            if x is not None and str(x).strip() and str(x).strip() != "全部"
        ]
        return list(dict.fromkeys(items))
    return []


def _append_bhg_filter(where: List[str], bhg_labels: List[str]) -> None:
    """按展示标签构建 F_BHG 多选 OR 条件。"""
    clauses: List[str] = []
    for label in bhg_labels:
        if label == "不合格":
            clauses.append("y.F_BHG = 'true'")
        elif label == "合格":
            clauses.append("(y.F_BHG = 'false' OR y.F_BHG IS NULL OR y.F_BHG = '')")
        elif label in ("不合格审批中", "不合格审核中"):
            clauses.append("y.F_BHG = 'pending'")
    if clauses:
        where.append("(" + " OR ".join(clauses) + ")")


def list_ybzj(q: Dict[str, Any], user: Dict[str, Any] | None = None,
             scope: str | None = None) -> Dict[str, Any]:
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
    _append_bhg_filter(where, _normalize_bhg_filter_labels(q.get("F_BHG")))
    # 是否培养：是 -> 过滤存在分发记录；否 -> 无分发记录
    sfpy = (q.get("F_SFPY") or "").strip()
    if sfpy in ("是", "否"):
        op = "> 0" if sfpy == "是" else "= 0"
        where.append(f"(SELECT COUNT(1) FROM T_FFJL ff WHERE ff.F_ZZID = y.F_ZZID) {op}")
            # 组织培养状态过滤
    f_zzpy = (q.get("F_ZZPY") or "").strip()
    if f_zzpy:
        if f_zzpy == "未培养":
            # 没有工单类型为"组织"的分发记录
            where.append("""
                NOT EXISTS (
                    SELECT 1 FROM T_FFJL ff 
                    WHERE ff.F_ZZID = y.F_ZZID AND ff.F_GDLX = '组织'
                )
            """)
        elif f_zzpy == "待创建":
            # 有"组织"分发记录，但没有培养工单
            where.append("""
                EXISTS (
                    SELECT 1 FROM T_FFJL ff 
                    WHERE ff.F_ZZID = y.F_ZZID AND ff.F_GDLX = '组织'
                )
                AND NOT EXISTS (
                    SELECT 1 FROM T_PYGD pg 
                    WHERE pg.F_ZZID = y.F_ZZID
                )
            """)
        elif f_zzpy == "已培养":
            # 已有培养工单（任意代次均算"已培养"）
            where.append("""
                EXISTS (
                    SELECT 1 FROM T_FFJL ff 
                    WHERE ff.F_ZZID = y.F_ZZID AND ff.F_GDLX = '组织'
                )
                AND EXISTS (
                    SELECT 1 FROM T_PYGD pg 
                    WHERE pg.F_ZZID = y.F_ZZID
                )
            """)
        else:
            # 特定代次（如"P0", "P1", "P2"等）
            where.append("""
                EXISTS (
                    SELECT 1 FROM T_FFJL ff 
                    WHERE ff.F_ZZID = y.F_ZZID AND ff.F_GDLX = '组织'
                )
                AND EXISTS (
                    SELECT 1 FROM T_PYGD pg 
                    WHERE pg.F_ZZID = y.F_ZZID AND pg.F_DC = ?
                )
            """)
            params.append(f_zzpy)

    # 血液培养状态过滤
    f_xypy = (q.get("F_XYPY") or "").strip()
    if f_xypy:
        if f_xypy == "未培养":
            # 没有工单类型为"血液"的分发记录
            where.append("""
                NOT EXISTS (
                    SELECT 1 FROM T_FFJL ff 
                    WHERE ff.F_ZZID = y.F_ZZID AND ff.F_GDLX = '血液'
                )
            """)
        elif f_xypy == "待创建":
            # 有"血液"分发记录，但没有培养工单
            where.append("""
                EXISTS (
                    SELECT 1 FROM T_FFJL ff 
                    WHERE ff.F_ZZID = y.F_ZZID AND ff.F_GDLX = '血液'
                )
                AND NOT EXISTS (
                    SELECT 1 FROM T_PYGD pg 
                    WHERE pg.F_ZZID = y.F_ZZID
                )
            """)
        elif f_xypy == "已培养":
            # 已有培养工单（任意代次均算"已培养"）
            where.append("""
                EXISTS (
                    SELECT 1 FROM T_FFJL ff 
                    WHERE ff.F_ZZID = y.F_ZZID AND ff.F_GDLX = '血液'
                )
                AND EXISTS (
                    SELECT 1 FROM T_PYGD pg 
                    WHERE pg.F_ZZID = y.F_ZZID
                )
            """)
        else:
            # 特定代次
            where.append("""
                EXISTS (
                    SELECT 1 FROM T_FFJL ff 
                    WHERE ff.F_ZZID = y.F_ZZID AND ff.F_GDLX = '血液'
                )
                AND EXISTS (
                    SELECT 1 FROM T_PYGD pg 
                    WHERE pg.F_ZZID = y.F_ZZID AND pg.F_DC = ?
                )
            """)
            params.append(f_xypy)
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

    from app.core.data_scope import apply_owner_filter
    apply_owner_filter(where, params, scope=scope, user=user, column="y.F_DJR", match="name")

    where_sql = (" WHERE " + " AND ".join(where)) if where else ""

    base_core = f"""
SELECT
  y.F_ID, y.F_ZZID, y.F_BBLX, y.F_BHG,
  y.F_ZQTYS,
  y.F_XM, y.F_XB, y.F_BRBH, y.F_ZYH, y.F_SFZH, y.F_MZH,
  y.F_DJRQ, y.F_CXSJ,
  y.F_SJJG,
  y.F_ZQTYS_ID,
  y.F_DJR
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

        zzid_list = [it.get("F_ZZID") for it in items if it.get("F_ZZID")]

        # 分装样本类型计数：T_YBFZ.F_YBLX → 列表列
        # F_PDO：F_YBLX=类器官；F_ZKPDO：同样是类器官，且库存状态为在库
        yblx_count_map: dict = {}
        _YBLX_FIELD = {
            "蜡块": "F_LK",
            "otc": "F_OTC",
            "oct": "F_OTC",
            "血清": "F_XQ",
            "血浆": "F_XJ",
            "pbmc": "F_PBMC",
            "ctc": "F_CTC",
            "类器官": "F_PDO",
        }

        def _yblx_field(name) -> str | None:
            s = str(name or "").strip()
            if not s:
                return None
            return _YBLX_FIELD.get(s) or _YBLX_FIELD.get(s.lower())

        if zzid_list:
            try:
                ph = ",".join(["?"] * len(zzid_list))
                cur.execute(
                    f"""
                    SELECT fz.F_ZZID, fz.F_YBLX, fz.F_KCZT, COUNT(1)
                    FROM T_YBFZ fz
                    WHERE fz.F_ZZID IN ({ph})
                    GROUP BY fz.F_ZZID, fz.F_YBLX, fz.F_KCZT
                    """,
                    tuple(zzid_list),
                )
                for row in cur.fetchall():
                    field = _yblx_field(row[1])
                    if not field:
                        continue
                    zz = row[0]
                    bucket = yblx_count_map.setdefault(zz, {})
                    if zz is not None:
                        yblx_count_map.setdefault(str(zz), bucket)
                    cnt = int(row[3] or 0)
                    bucket[field] = bucket.get(field, 0) + cnt
                    if field == "F_PDO" and str(row[2] or "").strip() == "在库":
                        bucket["F_ZKPDO"] = bucket.get("F_ZKPDO", 0) + cnt
            except Exception:
                pass

        # 映射知情同意书路径；并计算培养状态
        from app.crud.file_center import get_file_by_id
        from app.core.emr_client import load_emr_config, try_build_emr_url
        emr_cfg = load_emr_config()
        for it in items:
            it["F_DZBL"] = try_build_emr_url(it, emr_cfg)
            it.pop("F_SFZH", None)
            it.pop("F_MZH", None)
            # F_BHG 语义映射：true -> 不合格，false -> 合格，pending -> 不合格审批中
            if it.get("F_BHG") is not None:
                val = str(it.get("F_BHG")).lower()
                if val == "true":
                    it["F_BHG_MS"] = "不合格"
                elif val == "pending":
                    it["F_BHG_MS"] = "不合格审批中"
                elif val == "false":
                    it["F_BHG_MS"] = "合格"
                else:
                    it["F_BHG_MS"] = it.get("F_BHG")
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

            yblx_counts = yblx_count_map.get(zzid) or yblx_count_map.get(str(zzid) if zzid is not None else "") or {}
            it["F_LK"] = yblx_counts.get("F_LK", 0)
            it["F_OTC"] = yblx_counts.get("F_OTC", 0)
            it["F_XQ"] = yblx_counts.get("F_XQ", 0)
            it["F_XJ"] = yblx_counts.get("F_XJ", 0)
            it["F_PBMC"] = yblx_counts.get("F_PBMC", 0)
            it["F_PDO"] = yblx_counts.get("F_PDO", 0)
            it["F_ZKPDO"] = yblx_counts.get("F_ZKPDO", 0)
            it["F_CTC"] = yblx_counts.get("F_CTC", 0)
            it["F_BLSJ"] = None
            it["F_ZXSJ"] = None

        return {"items": items, "total": total}


def _pdo_detail_yblx_filter(raw: str) -> tuple[list[str], bool]:
    """样本类型入参 → (F_YBLX 匹配值, 是否只查在库)。

    列表列名与类型名称都可传。未识别的值按 F_YBLX 原文字面匹配。
    在库类器官是 F_YBLX=类器官 且库存状态为在库。
    """
    key = str(raw or "").strip()
    folded = key.lower()
    aliases = {
        "f_lk": (["蜡块"], False),
        "蜡块": (["蜡块"], False),
        "f_otc": (["otc", "oct"], False),
        "otc": (["otc", "oct"], False),
        "oct": (["otc", "oct"], False),
        "f_xq": (["血清"], False),
        "血清": (["血清"], False),
        "f_xj": (["血浆"], False),
        "血浆": (["血浆"], False),
        "f_pbmc": (["pbmc"], False),
        "pbmc": (["pbmc"], False),
        "f_ctc": (["ctc"], False),
        "ctc": (["ctc"], False),
        "f_pdo": (["类器官"], False),
        "类器官": (["类器官"], False),
        "f_zkpdo": (["类器官"], True),
        "在库类器官": (["类器官"], True),
    }
    if not key:
        raise ValueError("样本类型不能为空")
    return aliases.get(folded, ([key], False))


def list_pdo_detail_by_ybzj_id(ybzj_id: int, yblx: str) -> Dict[str, Any]:
    """
    按标本接收列表行 F_ID（T_YBZJ.F_ID）和样本类型查询分装明细。

    只返回该标本号、该样本类型下的 T_YBFZ。在库类器官为 F_YBLX=类器官 且 F_KCZT=在库。
    """
    yblx_values, in_stock_only = _pdo_detail_yblx_filter(yblx)
    adapter = get_adapter()
    with adapter.connect() as conn:
        cur = conn.cursor()
        cur.execute("SELECT F_ID, F_ZZID FROM T_YBZJ WHERE F_ID = ?", (int(ybzj_id),))
        row = cur.fetchone()
        if not row:
            raise ValueError(f"未找到标本登记记录 F_ID={ybzj_id}")
        zzid = row[1]
        if not zzid:
            return {"F_ID": int(ybzj_id), "F_ZZID": None, "items": [], "total": 0}

        placeholders = ", ".join(["?"] * len(yblx_values))
        stock_sql = " AND fz.F_KCZT = '在库'" if in_stock_only else ""
        sql = f"""
        SELECT
            COALESCE(fz.F_DC, p.F_DC) AS F_DC,
            fz.F_SGBH,
            fz.F_KCZT,
            fz.F_YSZT,
            fz.F_YBLX,
            fz.F_XBSL,
            p.F_GDBH,
            fz.F_RKSJ
        FROM T_YBFZ fz
        LEFT JOIN T_PYGD p ON p.F_ID = fz.F_PYGD_ID
        WHERE fz.F_ZZID = ?
          AND LOWER(fz.F_YBLX) IN ({placeholders})
          {stock_sql}
        ORDER BY fz.F_ID DESC
        """
        cur.execute(sql, (zzid, *[v.lower() for v in yblx_values]))
        cols = [c[0] for c in cur.description]
        items: List[Dict[str, Any]] = []
        for r in cur.fetchall():
            rec = dict(zip(cols, r))
            kczt = rec.get("F_KCZT")
            yszt = rec.get("F_YSZT")
            ybzt = kczt if kczt not in (None, "") else yszt
            rksj = rec.get("F_RKSJ")
            if rksj is not None and hasattr(rksj, "strftime"):
                rksj = rksj.strftime("%Y-%m-%d %H:%M:%S")
            elif rksj is not None:
                rksj = str(rksj)
            dc = rec.get("F_DC")
            items.append({
                "F_DC": str(dc) if dc is not None else None,
                "F_SGBH": rec.get("F_SGBH"),
                "F_YBZT": ybzt,
                "F_YBLX": rec.get("F_YBLX"),
                "F_XBSL": rec.get("F_XBSL"),
                "F_GDBH": rec.get("F_GDBH"),
                "F_RKSJ": rksj,
            })

        return {
            "F_ID": int(ybzj_id),
            "F_ZZID": zzid,
            "items": items,
            "total": len(items),
        }

def create_distribute(zzid: str, pyry_user_id: int, mxlx: str, creator_code: str, gdlx: str | None = None, qywc: int = 0) -> Dict[str, Any]:
    """插入一条分发记录到 T_FFJL，返回插入后的完整行。

    要求：表已存在，F_ID 为自增。
    若 T_YBZJ.F_QYWC=1（取用已完成），拒绝新建并抛出 ValueError。
    若 qywc=1，在 INSERT 成功后将 T_YBZJ.F_QYWC 更新为 1。
    """
    adapter = get_adapter()
    now = datetime.now()
    qywc_int = 1 if qywc == 1 else 0
    with adapter.connect() as conn:
        cur = conn.cursor()

        # 拦截：若该标本已标记取用完成，禁止再次分发
        try:
            cur.execute("SELECT F_QYWC FROM T_YBZJ WHERE F_ZZID = ?", (zzid,))
            qywc_row = cur.fetchone()
            if qywc_row and qywc_row[0] == 1:
                raise ValueError(f"标本 {zzid} 已标记取用完成，无法再创建分发记录")
        except ValueError:
            raise
        except Exception:
            pass  # F_QYWC 列不存在或其他异常时跳过拦截
        
        # 从 T_YBZJ 读取 F_AZ（癌种）字段
        f_az = None
        try:
            cur.execute("SELECT F_AZ FROM T_YBZJ WHERE F_ZZID = ?", (zzid,))
            az_row = cur.fetchone()
            if az_row:
                f_az = az_row[0]
        except Exception:
            f_az = None
        
        # 插入（T_FFJL.F_QYWC 作为快照字段一并写入）
        if gdlx is not None:
            cur.execute(
                "INSERT INTO T_FFJL (F_ZZID, F_PYRY, F_MXLX, F_GDLX, F_FFSJ, F_FFCJR, F_GDZT, F_AZ, F_QYWC) VALUES (?, ?, ?, ?, ?, ?, '待创建', ?, ?)",
                (zzid, pyry_user_id, mxlx, gdlx, now, creator_code, f_az, qywc_int),
            )
        else:
            cur.execute(
                "INSERT INTO T_FFJL (F_ZZID, F_PYRY, F_MXLX, F_FFSJ, F_FFCJR, F_GDZT, F_AZ, F_QYWC) VALUES (?, ?, ?, ?, ?, '待创建', ?, ?)",
                (zzid, pyry_user_id, mxlx, now, creator_code, f_az, qywc_int),
            )

        # 若本次标记取用完成，将 T_YBZJ.F_QYWC 更新为 1（权威状态）
        if qywc_int == 1:
            try:
                cur.execute("UPDATE T_YBZJ SET F_QYWC = 1 WHERE F_ZZID = ?", (zzid,))
            except Exception:
                pass  # 列不存在时忽略，不影响分发记录写入

        # 读取自增ID（SQLServer、达梦都支持 SCOPE_IDENTITY()/IDENTITY_VALUE() 的不同写法，改为通用：按最大ID读取）
        try:
            cur.execute("SELECT MAX(F_ID) FROM T_FFJL WHERE F_ZZID = ?", (zzid,))
            new_id = int(cur.fetchone()[0])
        except Exception:
            new_id = None
        # 返回该行
        if new_id is not None:
            cur.execute("SELECT F_ID, F_ZZID, F_PYRY, F_MXLX, F_GDLX, F_FFSJ, F_FFCJR, F_QYWC FROM T_FFJL WHERE F_ID = ?", (new_id,))
        else:
            cur.execute(
                "SELECT F_ID, F_ZZID, F_PYRY, F_MXLX, F_GDLX, F_FFSJ, F_FFCJR, F_QYWC FROM T_FFJL WHERE F_ZZID = ? ORDER BY F_ID DESC",
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
            "F_QYWC": qywc_int,
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
                        pyry_id = row[2]
                        pyry_name = None
                        
                        # ✅ 将F_PYRY从用户ID转换为姓名
                        if pyry_id:
                            try:
                                cur.execute("SELECT F_Ktype FROM T_USER WHERE F_ID = ?", (pyry_id,))
                                user_row = cur.fetchone()
                                if user_row:
                                    pyry_name = user_row[0]
                            except Exception as e:
                                logger.warning(f"[分发质控] 查询培养人员姓名失败 (ID: {pyry_id}): {e}")
                        
                        new_detail_data = {
                            "分发记录": {
                                "F_ID": row[0],
                                "F_ZZID": row[1],
                                "F_PYRY": pyry_name or pyry_id,  # 优先使用姓名，查询失败则保留ID
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


def update_distribute(rec_id: int, pyry_user_id: int | None, mxlx: str | None, gdlx: str | None, qywc: int | None = None) -> bool:
    """仅在 F_GDZT='待创建' 时允许更新，返回是否成功。
    若 qywc=1，同步将 T_YBZJ.F_QYWC 更新为 1。
    """
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
    if qywc is not None:
        sets.append("F_QYWC = ?")
        params.append(1 if qywc == 1 else 0)
    if not sets:
        return False
    sql = f"UPDATE T_FFJL SET {', '.join(sets)} WHERE F_ID = ? AND F_GDZT = '待创建'"
    params.append(rec_id)
    with adapter.connect() as conn:
        cur = conn.cursor()
        cur.execute(sql, tuple(params))
        affected = cur.rowcount
        # 若标记取用完成，找到对应 ZZID 并更新 T_YBZJ
        if affected > 0 and qywc == 1:
            try:
                cur.execute("SELECT F_ZZID FROM T_FFJL WHERE F_ID = ?", (rec_id,))
                zzid_row = cur.fetchone()
                if zzid_row and zzid_row[0]:
                    cur.execute("UPDATE T_YBZJ SET F_QYWC = 1 WHERE F_ZZID = ?", (zzid_row[0],))
            except Exception:
                pass  # F_QYWC 列不存在时忽略
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
    base_select = f"SELECT F_ID, F_ZZID, F_PYRY, F_MXLX, F_GDLX, F_GDZT, F_FFSJ, F_FFCJR, F_QYWC FROM T_FFJL{where_sql} ORDER BY F_FFSJ DESC"
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

        # 读取 T_YBZJ.F_QYWC（标本取用完成的权威状态）
        ybzj_qywc = None
        try:
            cur.execute("SELECT F_QYWC FROM T_YBZJ WHERE F_ZZID = ?", (zzid,))
            qywc_row = cur.fetchone()
            if qywc_row is not None:
                ybzj_qywc = qywc_row[0]
        except Exception:
            pass

        return {"items": items, "total": len(items), "F_QYWC": ybzj_qywc}


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

