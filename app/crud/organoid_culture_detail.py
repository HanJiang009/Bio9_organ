from __future__ import annotations

from typing import Any, Dict, List, Optional
from datetime import datetime
from decimal import Decimal
import json

from app.db.factory import get_adapter
from app.core.scoring import calc_score_by_code
from app.crud.sample_receive import query_by_ids
from app.crud import organoid_culture_tissue as organoid_order


def _now() -> datetime:
    return datetime.now()


def _now_str() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _clean_datetime(v: Any) -> Optional[str]:
    """将入参规范为 DATETIME 列可绑定的 'YYYY-MM-DD HH:MM:SS' 字符串（兼容达梦/SQL Server）。"""
    if v is None:
        return None
    if isinstance(v, datetime):
        return v.strftime("%Y-%m-%d %H:%M:%S")
    s = str(v).strip()
    if not s:
        return None
    if "T" in s:
        s = s.replace("T", " ")
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(s[: len(fmt.replace("%", "0")) if fmt == "%Y-%m-%d" else 19], fmt).strftime(
                "%Y-%m-%d %H:%M:%S"
            )
        except Exception:
            continue
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(s, fmt).strftime("%Y-%m-%d %H:%M:%S")
        except Exception:
            continue
    return s


def _convert_decimals(obj: Any) -> Any:
    """
    递归转换对象中的 Decimal 类型为 float，使其可以被 JSON 序列化
    """
    if isinstance(obj, Decimal):
        return float(obj)
    elif isinstance(obj, dict):
        return {k: _convert_decimals(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [_convert_decimals(item) for item in obj]
    elif isinstance(obj, datetime):
        return obj.isoformat() if obj else None
    else:
        return obj


def _get_full_culture_detail_by_gdid(gdid: int) -> Dict[str, Any]:
    """
    获取完整的培养详情（包含工单信息和培养明细），用于质控审核记录的F_OLD_DATA字段
    模仿 /organoid-culture-detail/detail-by-gdid 接口的返回结构
    """
    order = organoid_order.get_culture_order_detail(gdid) if hasattr(organoid_order, "get_culture_order_detail") else None
    detail = get_mx_by_gdid(gdid)
    return {
        "order": order or {},
        "cultureDetail": detail or {},
    }


def _sync_to_ycl_and_rescore(gdid: int, zzid: str, f_zlxbzb: str | None, f_clhxbhl: str | None, operator: str | None):
    """
    将培养明细中的 F_ZLXBZB、F_CLHXBHL 同步到 T_YCL 的 F_ZLXBZB、F_XBHL（处理后细胞活率），
    并重新计算评分更新 F_YBPFB。
    
    字段映射：
    - T_PYMX_ZZ.F_ZLXBZB -> T_YCL.F_ZLXBZB
    - T_PYMX_ZZ.F_CLHXBHL（处理后细胞活力） -> T_YCL.F_XBHL（细胞活率）
    """
    print(f"\n{'='*60}")
    print(f"[同步开始] 工单ID={gdid}, ZZID={zzid}")
    print(f"[传入值] F_ZLXBZB={f_zlxbzb}, F_CLHXBHL={f_clhxbhl}")
    print(f"{'='*60}")
    
    adapter = get_adapter()
    with adapter.connect() as conn:
        cur = conn.cursor()
        
        # 1. 检查 T_YCL 是否有记录
        print(f"[步骤1] 检查T_YCL表是否有工单{gdid}的记录...")
        cur.execute("SELECT F_ID, F_ZLXBZB, F_XBHL FROM T_YCL WHERE F_GDID = ?", (gdid,))
        row = cur.fetchone()
        
        if not row:
            # 如果 T_YCL 没有记录，尝试创建一条基础记录
            print(f"[步骤1] ❌ 未找到记录，准备创建基础记录")
            try:
                cur.execute(
                    """
                    INSERT INTO T_YCL (F_ZZID, F_GDID, F_ZLXBZB, F_XBHL)
                    VALUES (?, ?, ?, ?)
                    """,
                    (zzid, gdid, f_zlxbzb, f_clhxbhl)
                )
                conn.commit()
                print(f"[步骤1] ✅ 成功创建T_YCL基础记录")
                print(f"[步骤1] 插入的值: F_ZZID={zzid}, F_GDID={gdid}, F_ZLXBZB={f_zlxbzb}, F_XBHL={f_clhxbhl}")
            except Exception as e:
                print(f"[步骤1] ❌ 创建T_YCL记录失败: {e}")
                import traceback
                traceback.print_exc()
                return
        else:
            print(f"[步骤1] ✅ 找到现有记录: F_ID={row[0]}, F_ZLXBZB={row[1]}, F_XBHL={row[2]}")
        
        # 2. 读取评分所需的完整数据
        print(f"[步骤2] 读取评分所需的完整数据...")
        base = query_by_ids(zzid, None) or {"JZXX": {}, "BBXX": {}, "YBZJ": {}}
        
        # 3. 构造评分上下文：读取 T_YCL 现有字段（包括F_YBPFB以便判断是否需要重新计算）
        print(f"[步骤3] 读取T_YCL现有字段...")
        cur.execute(
            """
            SELECT F_LTSJ, F_KSCLSJ, F_QXYCF, F_ZZSPCC, F_YBYCLQK, F_YBZLTJ, F_ZLXBZB, F_XBHL, F_YBPFB
            FROM T_YCL WHERE F_GDID = ?
            """,
            (gdid,),
        )
        ycl_row = cur.fetchone()
        old_ybpfb = None
        if ycl_row:
            cols = [c[0] for c in cur.description]
            ycl_data = dict(zip(cols, ycl_row))
            old_ybpfb = ycl_data.get("F_YBPFB")  # 保存旧评分
            print(f"[步骤3] ✅ 读取到数据: {ycl_data}")
        else:
            ycl_data = {}
            print(f"[步骤3] ⚠️ 未读取到数据，使用空字典")
        
        # 4. 检查评分相关字段是否有实质性变化
        print(f"[步骤4] 检查评分字段是否变化...")
        old_zlxbzb = str(ycl_data.get("F_ZLXBZB") or "")
        old_xbhl = str(ycl_data.get("F_XBHL") or "")
        new_zlxbzb = str(f_zlxbzb or "")
        new_xbhl = str(f_clhxbhl or "")
        
        has_scoring_change = (old_zlxbzb != new_zlxbzb) or (old_xbhl != new_xbhl)
        print(f"[步骤4] F_ZLXBZB: {old_zlxbzb} -> {new_zlxbzb}")
        print(f"[步骤4] F_XBHL: {old_xbhl} -> {new_xbhl}")
        print(f"[步骤4] 评分字段是否变化: {has_scoring_change}")
        
        # 5. 更新评分上下文中的字段
        ycl_data["F_ZZID"] = zzid
        ycl_data["F_GDID"] = gdid
        if f_zlxbzb is not None:
            ycl_data["F_ZLXBZB"] = f_zlxbzb
        if f_clhxbhl is not None:
            ycl_data["F_XBHL"] = f_clhxbhl  # T_YCL 中用 F_XBHL 存储处理后细胞活率
        
        # 6. 如果没有离体时间，从历史数据中读取
        if not ycl_data.get("F_LTSJ"):
            ycl_data["F_LTSJ"] = (base.get("YBZJ", {}) or {}).get("F_LTSJ") or (base.get("BBXX", {}) or {}).get("F_LTSJ")
        
        merged = {**base, "YCL": ycl_data}
        
        # 7. ✅ 智能评分：只在评分字段变化时才重新计算，否则复用旧评分
        score_json = None
        if not has_scoring_change and old_ybpfb:
            # 评分字段未变化，复用旧评分
            print(f"[步骤5] ✅ 评分字段未变化，复用旧评分（避免时间戳变化）")
            score_json = old_ybpfb if isinstance(old_ybpfb, str) else json.dumps(old_ybpfb, ensure_ascii=False, default=str)
        else:
            # 评分字段有变化或无旧评分，重新计算
            print(f"[步骤5] 重新计算样本评分...")
            try:
                score = calc_score_by_code("default", merged)
                score["calculated_at"] = _now_str()
                if operator:
                    score["operator"] = operator
                score_json = json.dumps(score, ensure_ascii=False, default=str)
                print(f"[步骤5] ✅ 评分计算完成: total_score={score.get('total_score', 'N/A')}")
            except Exception as score_err:
                print(f"[步骤5] ⚠️ 评分计算失败（不影响字段更新）: {score_err}")
                # 评分失败不影响字段更新，继续执行
        
        # 8. 更新 T_YCL
        print(f"[步骤6] 准备更新T_YCL表...")
        payload = {
            "F_ZLXBZB": f_zlxbzb,
            "F_XBHL": f_clhxbhl,  # 正确映射：F_CLHXBHL -> F_XBHL
        }
        # 只有评分存在时才更新 F_YBPFB（可能是新计算的，也可能是复用的）
        if score_json is not None:
            payload["F_YBPFB"] = score_json
        set_cols = [k for k, v in payload.items() if v is not None]
        if set_cols:
            sets = ", ".join([f"{c} = ?" for c in set_cols])
            params = [payload[c] for c in set_cols] + [gdid]
            sql = f"UPDATE T_YCL SET {sets} WHERE F_GDID = ?"
            print(f"[步骤6] 执行SQL: {sql}")
            print(f"[步骤6] 参数: {params}")
            cur.execute(sql, tuple(params))
            rowcount = getattr(cur, "rowcount", None)
            print(f"[步骤6] 影响行数: {rowcount}")
        else:
            print(f"[步骤6] ⚠️ 没有需要更新的字段（所有值都为None）")
        
        conn.commit()
        print(f"[步骤7] ✅ 数据库事务已提交")
        print(f"{'='*60}\n")


def _clean_num(v: Any) -> Any:
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return v
    s = str(v).strip()
    if s == "":
        return None
    try:
        if "." in s:
            return float(s)
        return int(s)
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
    if s == "":
        return None
    
    # 清理过度转义的 JSON 字符串（最多尝试 3 次 json.loads）
    try:
        for _ in range(3):
            if s.startswith('"') and s.endswith('"'):
                import json
                s = json.loads(s)
                if not isinstance(s, str):
                    s = str(s)
            else:
                break
    except Exception:
        pass
    
    return s if s else None


def _cdqxbzs_from_cdqxx(cdqxx: Dict[str, Any] | None) -> Optional[str]:
    """传代前细胞总数：写 T_PYMX_ZZ.F_CDQXBZS；兼容流水线模板旧键 F_CDQ_XBMD。"""
    if not cdqxx:
        return None
    v = cdqxx.get("F_CDQXBZS")
    if v is None or (isinstance(v, str) and not str(v).strip()):
        v = cdqxx.get("F_CDQ_XBMD")
    return _clean_str(v)


def _json_dumps(obj: Any) -> Optional[str]:
    try:
        return json.dumps(obj, ensure_ascii=False)
    except Exception:
        return None


def _build_jzj_items_full_replace(items: Any) -> List[Dict[str, Any]]:
    """全量替换基质胶：按入参顺序重建 JSON 数组，F_ID 从 1 递增（忽略入参中的 F_ID）。"""
    if items is None:
        return []
    if isinstance(items, dict):
        items = [items]
    result: List[Dict[str, Any]] = []
    next_id = 1
    for it in (items or []):
        if not isinstance(it, dict):
            continue
        result.append({
            "F_ID": next_id,
            "F_JZJ": _clean_str(it.get("F_JZJ")),
            "F_YL": _clean_num(it.get("F_YL")),
            "F_ND": _clean_str(it.get("F_ND")),
        })
        next_id += 1
    return result


def _apply_pyxx_pb_scalars(target: Dict[str, Any], pyxx_pb: Dict[str, Any]) -> None:
    """将铺板主表标量字段写入 target dict。"""
    if pyxx_pb.get("F_PYBBH") is not None:
        target["F_PYBBH"] = _clean_str(pyxx_pb.get("F_PYBBH"))
    if pyxx_pb.get("F_JZKBS") is not None:
        target["F_JZKBS"] = _clean_num(pyxx_pb.get("F_JZKBS"))
    if pyxx_pb.get("F_DKJZTJ") is not None:
        target["F_DKJZTJ"] = _clean_num(pyxx_pb.get("F_DKJZTJ"))
    if pyxx_pb.get("F_CJWD") is not None:
        target["F_CJWD"] = _clean_num(pyxx_pb.get("F_CJWD"))
    if pyxx_pb.get("F_CJSJ") is not None:
        target["F_CJSJ"] = _clean_num(pyxx_pb.get("F_CJSJ"))


def _build_xhmsy_insert_rec(gdid: int, mx_id: int, e: Dict[str, Any]) -> Dict[str, Any]:
    """组装一条待插入的 T_XHMSY 记录（不含 F_ID）。"""
    return {
        "F_GDID": gdid,
        "F_MXID": mx_id,
        "F_XHM": _clean_str((e or {}).get("F_XHM")),
        "F_YL": _clean_num((e or {}).get("F_YL")),
        "F_ND": _clean_str((e or {}).get("F_ND")),
        "F_SYCS": _clean_int((e or {}).get("F_SYCS")),
        "F_XHWD": _clean_num((e or {}).get("F_XHWD")),
        "F_CZFS": _clean_str((e or {}).get("F_CZFS")),
        "F_LXSL": _clean_num((e or {}).get("F_LXSL")),
        "F_XHSJ": _clean_num((e or {}).get("F_XHSJ")),
        "F_BZ": _clean_str((e or {}).get("F_BZ")),
    }


def _replace_xhmsy_for_mx(
    cur,
    gdid: int,
    mx_id: int,
    xhmsy_list: List[Dict[str, Any]],
) -> tuple[List[int], List[int], List[int]]:
    """
    按增量方式同步本培养明细下的消化酶记录（仅 F_GDID + F_MXID 范围，不影响冻存域 F_DCJL 记录）。

    入参 xhmsy_list 仍代表"保存后应存在的完整记录集合"（即语义上等价于全量替换），但落库时
    改为增量式 UPDATE/INSERT/DELETE，而不是先整体 DELETE 再 INSERT：

    - 携带 F_ID 且该 ID 属于当前明细：就地 UPDATE，保留原 F_ID；
    - 不携带 F_ID（或 F_ID 不属于当前明细）：视为新增，执行 INSERT；
    - 当前存在但未出现在入参列表中的记录：视为被移除，执行 DELETE。

    这样在只修改其他字段、消化酶记录内容未变时，F_ID 不会发生变化，避免质控记录把“内容完全
    相同、只是被重新写入一次”的消化酶记录误判为变更。

    返回 (deleted_ids, inserted_ids, updated_ids)。
    """
    cur.execute(
        "SELECT F_ID FROM T_XHMSY WHERE F_GDID = ? AND F_MXID = ?",
        (gdid, mx_id),
    )
    existing_ids = {int(r[0]) for r in cur.fetchall() if r and r[0] is not None}

    kept_ids: set = set()
    inserted_ids: List[int] = []
    updated_ids: List[int] = []

    for e in (xhmsy_list or []):
        fid = (e or {}).get("F_ID")
        fid_int: Optional[int] = None
        if fid is not None and str(fid).strip() != "":
            try:
                fid_int = int(fid)
            except Exception:
                fid_int = None

        rec = _build_xhmsy_insert_rec(gdid, mx_id, e)

        if fid_int is not None and fid_int in existing_ids:
            # 已存在记录：原地更新，保留 F_ID，避免产生"假变更"
            sets = ", ".join([f"{c} = ?" for c in rec.keys()])
            cur.execute(
                f"UPDATE T_XHMSY SET {sets} WHERE F_ID = ? AND F_GDID = ? AND F_MXID = ?",
                tuple(rec.values()) + (fid_int, gdid, mx_id),
            )
            kept_ids.add(fid_int)
            updated_ids.append(fid_int)
        else:
            # 新记录（无F_ID或F_ID不属于本明细）：插入
            cols = list(rec.keys())
            ph = ", ".join(["?" for _ in cols])
            cur.execute(
                f"INSERT INTO T_XHMSY ({', '.join(cols)}) VALUES ({ph})",
                tuple(rec[c] for c in cols),
            )
            try:
                cur.execute(
                    "SELECT MAX(F_ID) FROM T_XHMSY WHERE F_GDID = ? AND F_MXID = ?",
                    (gdid, mx_id),
                )
                row = cur.fetchone()
                if row and row[0] is not None:
                    new_id = int(row[0])
                    inserted_ids.append(new_id)
                    kept_ids.add(new_id)
            except Exception:
                pass

    deleted_ids = sorted(existing_ids - kept_ids)
    if deleted_ids:
        placeholders = ", ".join(["?" for _ in deleted_ids])
        cur.execute(
            f"DELETE FROM T_XHMSY WHERE F_GDID = ? AND F_MXID = ? AND F_ID IN ({placeholders})",
            (gdid, mx_id, *deleted_ids),
        )

    return deleted_ids, inserted_ids, updated_ids


def create_mx(
    gdid: int,
    pyjtjjl: Dict[str, Any] | None,
    pyxx_xh: Dict[str, Any] | None,
    xhmsy_list: List[Dict[str, Any]] | None,
    pyxx_pb: Dict[str, Any] | None,
    pyxx_py: Dict[str, Any] | None,
    cdqxx: Dict[str, Any] | None,
    qxycf: Optional[str],
    file_ids: List[int] | None,
    operator: Optional[str],
    from_pipeline_template: bool = False,
) -> Dict[str, Any]:
    from app.utils.yhbc import yhbc_flag
    adapter = get_adapter()
    with adapter.connect() as conn:
        cur = conn.cursor()

        # 1) 组装主表数据
        row: Dict[str, Any] = {"F_GDID": gdid}
        now = _now()
        row["F_CZR"] = operator
        # 模板预填只带配置字段，操作时间留给用户真实保存时再写
        row["F_CZSJ"] = None if from_pipeline_template else now
        row["F_YHBC"] = yhbc_flag(from_pipeline_template)

        if pyjtjjl:
            row["F_PYJ_WD"] = _clean_num(pyjtjjl.get("F_PYJ_WD"))
            row["F_PYJ_SD"] = _clean_num(pyjtjjl.get("F_PYJ_SD"))

        if pyxx_xh:
            row["F_MJFA"] = _clean_str(pyxx_xh.get("F_MJFA"))
            row["F_ZZMJFA"] = _clean_str(pyxx_xh.get("F_ZZMJFA"))
            row["F_SWKJ"] = _clean_str(pyxx_xh.get("F_SWKJ"))
            row["F_XBSL"] = _clean_num(pyxx_xh.get("F_XBSL"))
            row["F_XBHL"] = _clean_num(pyxx_xh.get("F_XBHL"))
            row["F_XBDL"] = _clean_num(pyxx_xh.get("F_XBDL"))
            # 新增字段：治疗细胞占比、处理后细胞活力、形态观察记录
            row["F_ZLXBZB"] = _clean_str(pyxx_xh.get("F_ZLXBZB"))
            row["F_CLHXBHL"] = _clean_str(pyxx_xh.get("F_CLHXBHL"))
            row["F_XTGCJL"] = _clean_str(pyxx_xh.get("F_XTGCJL"))

        if pyxx_pb:
            # F_JZJ: 应用层自增ID，存 JSON 数组
            jzj_items = pyxx_pb.get("F_JZJ") or []
            items: List[Dict[str, Any]] = []
            if isinstance(jzj_items, dict):
                jzj_items = [jzj_items]
            if isinstance(jzj_items, list):
                next_id = 1
                for it in jzj_items:
                    try:
                        obj = {
                            "F_ID": int(it.get("F_ID")) if it.get("F_ID") is not None else next_id,
                            "F_JZJ": _clean_str(it.get("F_JZJ")),
                            "F_YL": _clean_num(it.get("F_YL")),
                            "F_ND": _clean_str(it.get("F_ND")),
                        }
                    except Exception:
                        obj = {
                            "F_ID": next_id,
                            "F_JZJ": _clean_str((it or {}).get("F_JZJ")),
                            "F_YL": _clean_num((it or {}).get("F_YL")),
                            "F_ND": _clean_str((it or {}).get("F_ND")),
                        }
                    items.append(obj)
                    next_id = max(next_id, (obj.get("F_ID") or 0) + 1)
            row["F_JZJ"] = _json_dumps(items)

            row["F_PYBBH"] = _clean_str(pyxx_pb.get("F_PYBBH"))
            row["F_JZKBS"] = _clean_num(pyxx_pb.get("F_JZKBS"))
            row["F_DKJZTJ"] = _clean_num(pyxx_pb.get("F_DKJZTJ"))
            row["F_CJWD"] = _clean_num(pyxx_pb.get("F_CJWD"))
            row["F_CJSJ"] = _clean_num(pyxx_pb.get("F_CJSJ"))

        if pyxx_py:
            row["F_TJPYJ"] = _clean_num(pyxx_py.get("F_TJPYJ"))
            row["F_PYX_TEMP"] = _clean_num(pyxx_py.get("F_PYX_TEMP"))
            row["F_PYX_CO2"] = _clean_num(pyxx_py.get("F_PYX_CO2"))
            row["F_PYX_HUM"] = _clean_num(pyxx_py.get("F_PYX_HUM"))
            row["F_PYX_FRSJ"] = _clean_str(pyxx_py.get("F_PYX_FRSJ"))

        if cdqxx:
            row["F_CDQXBZS"] = _cdqxbzs_from_cdqxx(cdqxx)
            row["F_CDQ_XBHL"] = _clean_str(cdqxx.get("F_CDQ_XBHL"))
            row["F_JZMD"] = _clean_str(cdqxx.get("F_JZMD"))

        if qxycf is not None:
            row["F_QXYCF"] = _clean_str(qxycf)

        if file_ids:
            # 去重并存为 JSON
            uniq = []
            seen = set()
            for fid in file_ids:
                try:
                    iv = int(fid)
                    if iv not in seen:
                        seen.add(iv)
                        uniq.append(iv)
                except Exception:
                    continue
            row["F_FJ"] = _json_dumps(uniq)

        # INSERT 主表（F_YHBC 列未迁移时降级）
        def _insert_mx(data: Dict[str, Any]) -> None:
            cols = list(data.keys())
            placeholders = ", ".join(["?" for _ in cols])
            cur.execute(
                f"INSERT INTO T_PYMX_ZZ ({', '.join(cols)}) VALUES ({placeholders})",
                tuple(data[c] for c in cols),
            )

        try:
            _insert_mx(row)
        except Exception:
            row.pop("F_YHBC", None)
            _insert_mx(row)

        # 获取新ID
        cur.execute("SELECT MAX(F_ID) FROM T_PYMX_ZZ WHERE F_GDID = ?", (gdid,))
        mx_id = int(cur.fetchone()[0])

        # 2) 批量写消化酶记录
        xh_ids: List[int] = []
        if xhmsy_list:
            for e in xhmsy_list:
                rec = {
                    "F_GDID": gdid,
                    "F_MXID": mx_id,
                    "F_XHM": _clean_str((e or {}).get("F_XHM")),
                    "F_YL": _clean_num((e or {}).get("F_YL")),
                    "F_ND": _clean_str((e or {}).get("F_ND")),
                    "F_SYCS": _clean_int((e or {}).get("F_SYCS")),
                    "F_XHWD": _clean_num((e or {}).get("F_XHWD")),
                    "F_CZFS": _clean_str((e or {}).get("F_CZFS")),
                    "F_LXSL": _clean_num((e or {}).get("F_LXSL")),
                    "F_XHSJ": _clean_num((e or {}).get("F_XHSJ")),
                    "F_BZ": _clean_str((e or {}).get("F_BZ")),
                }
                cols2 = list(rec.keys())
                ph2 = ", ".join(["?" for _ in cols2])
                cur.execute(
                    f"INSERT INTO T_XHMSY ({', '.join(cols2)}) VALUES ({ph2})",
                    tuple(rec[c] for c in cols2),
                )
                try:
                    cur.execute("SELECT MAX(F_ID) FROM T_XHMSY WHERE F_GDID = ? AND F_MXID = ?", (gdid, mx_id))
                    xh_id = int(cur.fetchone()[0])
                    xh_ids.append(xh_id)
                except Exception:
                    pass

        synced_create = _sync_order_cell_totals_from_cdqxx(gdid, cdqxx, cur=cur)
        conn.commit()
        
        # 同步到 T_YCL 并重新计算评分
        try:
            # 获取 ZZID
            cur.execute("SELECT F_ZZID FROM T_PYGD WHERE F_ID = ?", (gdid,))
            zzid_row = cur.fetchone()
            if zzid_row and zzid_row[0]:
                zzid = zzid_row[0]
                print(f"调试: 开始同步工单{gdid}的F_ZLXBZB={row.get('F_ZLXBZB')}, F_CLHXBHL={row.get('F_CLHXBHL')}到T_YCL")
                _sync_to_ycl_and_rescore(
                    gdid, 
                    zzid, 
                    row.get("F_ZLXBZB"), 
                    row.get("F_CLHXBHL"), 
                    operator
                )
                print(f"成功: 工单{gdid}的样本评分已更新")
        except Exception as e:
            # 记录错误但不影响主流程
            print(f"错误: 同步到T_YCL失败 - {e}")
            import traceback
            traceback.print_exc()

        result = {
            "F_MXID": mx_id,
            "F_GDID": gdid,
            "F_FJ_IDS": json.loads(row.get("F_FJ") or "[]"),
            "XHMSY_IDS": xh_ids,
        }
        if synced_create:
            result["order_cell_totals"] = synced_create
        return result


def update_mx(
    gdid: int,
    mx_id: int,
    pyjtjjl: Dict[str, Any] | None,
    pyxx_xh: Dict[str, Any] | None,
    xhmsy_list: List[Dict[str, Any]] | None,
    xhmsy_delete_ids: List[int] | None,
    pyxx_pb: Dict[str, Any] | None,
    pyxx_py: Dict[str, Any] | None,
    cdqxx: Dict[str, Any] | None,
    qxycf: Optional[str],
    add_file_ids: List[int] | None,
    del_file_ids: List[int] | None,
    jzj_delete_ids: List[int] | None,
    operator: Optional[str],
) -> Dict[str, Any] | None:
    adapter = get_adapter()
    with adapter.connect() as conn:
        cur = conn.cursor()
        # 读取现有
        cur.execute("SELECT * FROM T_PYMX_ZZ WHERE F_ID = ?", (mx_id,))
        row = cur.fetchone()
        if not row:
            return None
        cols = [c[0] for c in cur.description]
        current = dict(zip(cols, row))
        if int(current.get("F_GDID") or 0) != int(gdid):
            return None

        updates: Dict[str, Any] = {"F_CZR": operator, "F_CZSJ": _now(), "F_YHBC": 1}

        if pyjtjjl:
            updates["F_PYJ_WD"] = _clean_num(pyjtjjl.get("F_PYJ_WD"))
            updates["F_PYJ_SD"] = _clean_num(pyjtjjl.get("F_PYJ_SD"))
        if pyxx_xh:
            updates["F_MJFA"] = _clean_str(pyxx_xh.get("F_MJFA"))
            updates["F_ZZMJFA"] = _clean_str(pyxx_xh.get("F_ZZMJFA"))
            updates["F_SWKJ"] = _clean_str(pyxx_xh.get("F_SWKJ"))
            updates["F_XBSL"] = _clean_num(pyxx_xh.get("F_XBSL"))
            updates["F_XBHL"] = _clean_num(pyxx_xh.get("F_XBHL"))
            updates["F_XBDL"] = _clean_num(pyxx_xh.get("F_XBDL"))
            # 新增字段：治疗细胞占比、处理后细胞活力、形态观察记录
            updates["F_ZLXBZB"] = _clean_str(pyxx_xh.get("F_ZLXBZB"))
            updates["F_CLHXBHL"] = _clean_str(pyxx_xh.get("F_CLHXBHL"))
            updates["F_XTGCJL"] = _clean_str(pyxx_xh.get("F_XTGCJL"))
        if pyxx_py:
            updates["F_TJPYJ"] = _clean_num(pyxx_py.get("F_TJPYJ"))
            updates["F_PYX_TEMP"] = _clean_num(pyxx_py.get("F_PYX_TEMP"))
            updates["F_PYX_CO2"] = _clean_num(pyxx_py.get("F_PYX_CO2"))
            updates["F_PYX_HUM"] = _clean_num(pyxx_py.get("F_PYX_HUM"))
            updates["F_PYX_FRSJ"] = _clean_str(pyxx_py.get("F_PYX_FRSJ"))
        if cdqxx:
            updates["F_CDQXBZS"] = _cdqxbzs_from_cdqxx(cdqxx)
            updates["F_CDQ_XBHL"] = _clean_str(cdqxx.get("F_CDQ_XBHL"))
            updates["F_JZMD"] = _clean_str(cdqxx.get("F_JZMD"))
        if qxycf is not None:
            updates["F_QXYCF"] = _clean_str(qxycf)

        # 附件合并
        try:
            exist_fj = json.loads(current.get("F_FJ") or "[]")
            exist_set = {int(x) for x in exist_fj}
        except Exception:
            exist_set = set()
        if add_file_ids:
            for fid in add_file_ids:
                try:
                    exist_set.add(int(fid))
                except Exception:
                    pass
        if del_file_ids:
            for fid in del_file_ids:
                try:
                    exist_set.discard(int(fid))
                except Exception:
                    pass
        updates["F_FJ"] = _json_dumps(sorted(list(exist_set)))

        # 基质胶：入参含 F_JZJ 时全量替换（忽略 JZJ_IDS_DELETE）
        if pyxx_pb:
            _apply_pyxx_pb_scalars(updates, pyxx_pb)
            if "F_JZJ" in pyxx_pb:
                updates["F_JZJ"] = _json_dumps(
                    _build_jzj_items_full_replace(pyxx_pb.get("F_JZJ"))
                )

        # 执行更新（F_YHBC 未迁移时降级）
        set_cols = [k for k, v in updates.items() if v is not None]
        if set_cols:
            try:
                sets = ", ".join([f"{c} = ?" for c in set_cols])
                params = [updates[c] for c in set_cols] + [mx_id]
                cur.execute(f"UPDATE T_PYMX_ZZ SET {sets} WHERE F_ID = ?", tuple(params))
            except Exception:
                set_cols = [c for c in set_cols if c != "F_YHBC"]
                if set_cols:
                    sets = ", ".join([f"{c} = ?" for c in set_cols])
                    params = [updates[c] for c in set_cols] + [mx_id]
                    cur.execute(f"UPDATE T_PYMX_ZZ SET {sets} WHERE F_ID = ?", tuple(params))

        # 消化酶：入参 XHMSYJL 非 None 时按增量方式同步（忽略 XHMSYJL_DELETE），保留未变更记录的 F_ID
        xh_updated: List[int] = []
        xh_inserted: List[int] = []
        xh_deleted: List[int] = []
        if xhmsy_list is not None:
            xh_deleted, xh_inserted, xh_updated = _replace_xhmsy_for_mx(cur, gdid, mx_id, xhmsy_list or [])

        synced_update = _sync_order_cell_totals_from_cdqxx(gdid, cdqxx, cur=cur)
        conn.commit()

        try:
            exist_fj_after = json.loads(updates.get("F_FJ") or current.get("F_FJ") or "[]")
            fj_ids_out = [int(x) for x in exist_fj_after]
        except Exception:
            fj_ids_out = []

        result = {
            "F_MXID": mx_id,
            "F_GDID": gdid,
            "F_FJ_IDS": fj_ids_out,
            "XHMSY_INSERTED_IDS": xh_inserted,
            "XHMSY_UPDATED_IDS": xh_updated,
            "XHMSY_DELETED_IDS": xh_deleted,
        }
        if synced_update:
            result["order_cell_totals"] = synced_update
        return result
    

def get_mx_by_gdid(gdid: int) -> Dict[str, Any] | None:
    adapter = get_adapter()
    with adapter.connect() as conn:
        cur = conn.cursor()
        # 取该工单下最新一条培养明细
        sql = "SELECT * FROM T_PYMX_ZZ WHERE F_GDID = ? ORDER BY F_ID DESC"
        paginated_sql, extra = adapter.paginate(sql, 0, 1)
        cur.execute(paginated_sql, (gdid,) + tuple(extra))
        row = cur.fetchone()
        if not row:
            return None
        cols = [c[0] for c in cur.description]
        data = dict(zip(cols, row))

        mx_id = int(data.get("F_ID"))

        # 解析附件ID数组
        try:
            fj_ids = json.loads(data.get("F_FJ") or "[]")
            fj_ids = [int(x) for x in fj_ids]
        except Exception:
            fj_ids = []

        attachments: List[Dict[str, Any]] = []
        if fj_ids:
            q_marks = ",".join(["?" for _ in fj_ids])
            try:
                cur.execute(
                    f"SELECT F_ID, F_PATH, F_ORIGINAL_NAME FROM T_FILES WHERE F_ID IN ({q_marks})",
                    tuple(fj_ids),
                )
                rows = cur.fetchall()
                cols = [c[0] for c in cur.description]
                raw_files = [dict(zip(cols, r)) for r in rows]
                order_map = {fid: idx for idx, fid in enumerate(fj_ids)}
                for rec in raw_files:
                    fid = rec.get("F_ID")
                    path = (rec.get("F_PATH") or "").lstrip("/")
                    if path.startswith("uploads/"):
                        path = path[8:]
                    attachments.append(
                        {
                            "F_ID": fid,
                            "F_NAME": rec.get("F_ORIGINAL_NAME"),
                            "F_PATH": path,
                        }
                    )
                attachments.sort(key=lambda item: order_map.get(item.get("F_ID"), 999999))
            except Exception:
                attachments = []

        czsj_val = data.get("F_CZSJ")
        if czsj_val is not None and hasattr(czsj_val, "strftime"):
            try:
                czsj_val = czsj_val.strftime("%Y-%m-%d %H:%M:%S")
            except Exception:
                czsj_val = str(czsj_val)
        elif czsj_val is not None:
            czsj_val = str(czsj_val)

        # 解析基质胶JSON
        try:
            jzj = json.loads(data.get("F_JZJ") or "[]")
            if isinstance(jzj, dict):
                jzj = [jzj]
        except Exception:
            jzj = []

        # 查询消化酶使用记录
        cur.execute(
            "SELECT * FROM T_XHMSY WHERE F_GDID = ? AND F_MXID = ? ORDER BY F_ID ASC",
            (gdid, mx_id),
        )
        xh_rows = cur.fetchall()
        xh_cols = [c[0] for c in cur.description]
        xh_list: List[Dict[str, Any]] = [dict(zip(xh_cols, r)) for r in xh_rows]

        # 组装与入参一致的分块结构
        pyjtjjl = {
            "F_PYJ_WD": data.get("F_PYJ_WD"),
            "F_PYJ_SD": data.get("F_PYJ_SD"),
        }
        pyxx_xh = {
            "F_MJFA": data.get("F_MJFA"),
            "F_ZZMJFA": data.get("F_ZZMJFA"),
            "F_SWKJ": data.get("F_SWKJ"),
            "F_XBSL": data.get("F_XBSL"),
            "F_XBHL": data.get("F_XBHL"),
            "F_XBDL": data.get("F_XBDL"),
            # 新增字段：治疗细胞占比、处理后细胞活力、形态观察记录
            "F_ZLXBZB": data.get("F_ZLXBZB"),
            "F_CLHXBHL": data.get("F_CLHXBHL"),
            "F_XTGCJL": data.get("F_XTGCJL"),
        }
        pyxx_pb = {
            "F_JZJ": jzj,
            "F_PYBBH": data.get("F_PYBBH"),
            "F_JZKBS": data.get("F_JZKBS"),
            "F_DKJZTJ": data.get("F_DKJZTJ"),
            "F_CJWD": data.get("F_CJWD"),
            "F_CJSJ": data.get("F_CJSJ"),
        }
        pyxx_py = {
            "F_TJPYJ": data.get("F_TJPYJ"),
            "F_PYX_TEMP": data.get("F_PYX_TEMP"),
            "F_PYX_CO2": data.get("F_PYX_CO2"),
            "F_PYX_HUM": data.get("F_PYX_HUM"),
            "F_PYX_FRSJ": data.get("F_PYX_FRSJ"),
        }
        cdqxx = {
            "F_CDQXBZS": data.get("F_CDQXBZS"),
            "F_CDQ_XBHL": data.get("F_CDQ_XBHL"),
            "F_JZMD": data.get("F_JZMD"),
        }
        # 细胞总数落在工单表，明细回显时一并带上，避免前端只能从 culture/detail 读
        try:
            cur.execute(
                "SELECT F_CDQXBZS, F_FSQXBZS FROM T_PYGD WHERE F_ID = ?",
                (gdid,),
            )
            tot = cur.fetchone()
            if tot:
                cdqxx["F_CDQXBZS"] = tot[0]
                cdqxx["F_FSQXBZS"] = tot[1]
        except Exception:
            pass

        return {
            "F_GDID": gdid,
            "F_MXID": mx_id,
            "F_CZR": data.get("F_CZR"),
            "F_CZSJ": czsj_val,
            "PYJTJJL": pyjtjjl,
            "PYXX_XH": pyxx_xh,
            "PYXX_PB": pyxx_pb,
            "PYXX_PY": pyxx_py,
            "CDQXX": cdqxx,
            "F_QXYCF": data.get("F_QXYCF"),
            "XHMSYJL": xh_list,
            "F_FJ_IDS": fj_ids,
            "F_FJ": attachments,
        }


def _sync_order_cell_totals_from_cdqxx(
    gdid: int,
    cdqxx: Dict[str, Any] | None,
    cur=None,
) -> Dict[str, Any]:
    """
    将 CDQXX 中的传代前/复苏前细胞总数同步到工单表 T_PYGD。

    前端在传代-培养保存时把 F_CDQXBZS（及复苏场景的 F_FSQXBZS）放在 CDQXX 里，
    工单创建编辑页从 culture/detail 读 T_PYGD，故需回写工单字段。

    Args:
        cur: 若传入则复用当前事务游标（不单独 commit）；否则自开连接并 commit。

    Returns:
        实际写入的字段字典，如 {"F_CDQXBZS": "36"}；无待同步字段时返回 {}。
    """
    import logging
    logger = logging.getLogger(__name__)

    if not cdqxx or not isinstance(cdqxx, dict):
        return {}
    sets: List[str] = []
    params: List[Any] = []
    written: Dict[str, Any] = {}
    if "F_CDQXBZS" in cdqxx or "F_CDQ_XBMD" in cdqxx:
        v = cdqxx.get("F_CDQXBZS")
        if v is None or (isinstance(v, str) and not str(v).strip()):
            v = cdqxx.get("F_CDQ_XBMD")
        s = str(v).strip() if v is not None else ""
        val = s if s != "" else None
        sets.append("F_CDQXBZS = ?")
        params.append(val)
        written["F_CDQXBZS"] = val
    if "F_FSQXBZS" in cdqxx:
        v = cdqxx.get("F_FSQXBZS")
        s = str(v).strip() if v is not None else ""
        val = s if s != "" else None
        sets.append("F_FSQXBZS = ?")
        params.append(val)
        written["F_FSQXBZS"] = val
    if not sets:
        return {}
    params.append(int(gdid))
    sql = f"UPDATE T_PYGD SET {', '.join(sets)} WHERE F_ID = ?"

    if cur is not None:
        cur.execute(sql, tuple(params))
        logger.info(f"[培养明细] 同步工单细胞总数 gdid={gdid} values={written}")
        return written

    adapter = get_adapter()
    with adapter.connect() as conn:
        own_cur = conn.cursor()
        own_cur.execute(sql, tuple(params))
        conn.commit()
    logger.info(f"[培养明细] 同步工单细胞总数 gdid={gdid} values={written}")
    return written


def save_mx(
    gdid: int,
    pyjtjjl: Dict[str, Any] | None,
    pyxx_xh: Dict[str, Any] | None,
    xhmsy_list: List[Dict[str, Any]] | None,
    xhmsy_delete_ids: List[int] | None,
    pyxx_pb: Dict[str, Any] | None,
    pyxx_py: Dict[str, Any] | None,
    cdqxx: Dict[str, Any] | None,
    qxycf: Optional[str],
    file_ids_override: List[int] | None,
    add_file_ids: List[int] | None,
    del_file_ids: List[int] | None,
    jzj_delete_ids: List[int] | None,
    operator: Optional[str],
) -> Dict[str, Any]:
    adapter = get_adapter()
    old_detail_data_before_update = None  # ✅ 外层作用域定义，用于更新分支
    old_xhmsy_list = []  # ✅ 外层作用域定义，用于质控对比
    new_xhmsy_list = []  # ✅ 外层作用域定义，用于质控对比
    synced_order_fields: Dict[str, Any] = {}
    
    with adapter.connect() as conn:
        cur = conn.cursor()
        # 查是否已有明细
        sql2 = "SELECT * FROM T_PYMX_ZZ WHERE F_GDID = ? ORDER BY F_ID DESC"
        paginated_sql2, extra2 = adapter.paginate(sql2, 0, 1)
        cur.execute(paginated_sql2, (gdid,) + tuple(extra2))
        row = cur.fetchone()
        is_new_record = not row  # 标志：是否为新增记录
        
        if not row:
            # 创建新记录
            now = _now()
            main: Dict[str, Any] = {"F_GDID": gdid, "F_CZR": operator, "F_CZSJ": now, "F_YHBC": 1}
            if pyjtjjl:
                main["F_PYJ_WD"] = _clean_num(pyjtjjl.get("F_PYJ_WD"))
                main["F_PYJ_SD"] = _clean_num(pyjtjjl.get("F_PYJ_SD"))
            if pyxx_xh:
                main["F_MJFA"] = _clean_str(pyxx_xh.get("F_MJFA"))
                main["F_ZZMJFA"] = _clean_str(pyxx_xh.get("F_ZZMJFA"))
                main["F_SWKJ"] = _clean_str(pyxx_xh.get("F_SWKJ"))
                main["F_XBSL"] = _clean_num(pyxx_xh.get("F_XBSL"))
                main["F_XBHL"] = _clean_num(pyxx_xh.get("F_XBHL"))
                main["F_XBDL"] = _clean_num(pyxx_xh.get("F_XBDL"))
                # 新增字段：治疗细胞占比、处理后细胞活力、形态观察记录
                main["F_ZLXBZB"] = _clean_str(pyxx_xh.get("F_ZLXBZB"))
                main["F_CLHXBHL"] = _clean_str(pyxx_xh.get("F_CLHXBHL"))
                main["F_XTGCJL"] = _clean_str(pyxx_xh.get("F_XTGCJL"))
            # PB 与 JZJ
            jzj_items: List[Dict[str, Any]] = []
            if pyxx_pb:
                items = pyxx_pb.get("F_JZJ")
                if isinstance(items, dict):
                    items = [items]
                next_id = 1
                for it in (items or []):
                    try:
                        fid = int(it.get("F_ID")) if it.get("F_ID") is not None else next_id
                    except Exception:
                        fid = next_id
                    jzj_items.append({
                        "F_ID": fid,
                        "F_JZJ": _clean_str(it.get("F_JZJ")),
                        "F_YL": _clean_num(it.get("F_YL")),
                        "F_ND": _clean_str(it.get("F_ND")),
                    })
                    next_id = max(next_id, fid + 1)
                main["F_PYBBH"] = _clean_str(pyxx_pb.get("F_PYBBH"))
                main["F_JZKBS"] = _clean_num(pyxx_pb.get("F_JZKBS"))
                main["F_DKJZTJ"] = _clean_num(pyxx_pb.get("F_DKJZTJ"))
                main["F_CJWD"] = _clean_num(pyxx_pb.get("F_CJWD"))
                main["F_CJSJ"] = _clean_num(pyxx_pb.get("F_CJSJ"))
            main["F_JZJ"] = _json_dumps(jzj_items)
            if pyxx_py:
                main["F_TJPYJ"] = _clean_num(pyxx_py.get("F_TJPYJ"))
                main["F_PYX_TEMP"] = _clean_num(pyxx_py.get("F_PYX_TEMP"))
                main["F_PYX_CO2"] = _clean_num(pyxx_py.get("F_PYX_CO2"))
                main["F_PYX_HUM"] = _clean_num(pyxx_py.get("F_PYX_HUM"))
                main["F_PYX_FRSJ"] = _clean_str(pyxx_py.get("F_PYX_FRSJ"))
            if cdqxx:
                main["F_CDQXBZS"] = _cdqxbzs_from_cdqxx(cdqxx)
                main["F_CDQ_XBHL"] = _clean_str(cdqxx.get("F_CDQ_XBHL"))
                main["F_JZMD"] = _clean_str(cdqxx.get("F_JZMD"))
            if qxycf is not None:
                main["F_QXYCF"] = _clean_str(qxycf)
            # 附件：覆盖优先
            fj_ids: List[int] = []
            if file_ids_override is not None:
                for v in file_ids_override:
                    try:
                        fj_ids.append(int(v))
                    except Exception:
                        pass
            else:
                # 如果没有覆盖参数，使用 add_file_ids
                if add_file_ids:
                    for v in add_file_ids:
                        try:
                            fj_ids.append(int(v))
                        except Exception:
                            pass
            main["F_FJ"] = _json_dumps(sorted(list({int(x) for x in fj_ids})))

            cols = list(main.keys())
            try:
                placeholders = ", ".join(["?" for _ in cols])
                cur.execute(
                    f"INSERT INTO T_PYMX_ZZ ({', '.join(cols)}) VALUES ({placeholders})",
                    tuple(main[c] for c in cols),
                )
            except Exception:
                cols = [c for c in cols if c != "F_YHBC"]
                placeholders = ", ".join(["?" for _ in cols])
                cur.execute(
                    f"INSERT INTO T_PYMX_ZZ ({', '.join(cols)}) VALUES ({placeholders})",
                    tuple(main[c] for c in cols),
                )
            cur.execute("SELECT MAX(F_ID) FROM T_PYMX_ZZ WHERE F_GDID = ?", (gdid,))
            mx_id = int(cur.fetchone()[0])

            # 写 XHMSYJL（只处理新增项：不带 F_ID）
            xh_inserted: List[int] = []
            if xhmsy_list:
                for e in xhmsy_list:
                    fid = e.get("F_ID")
                    if fid is not None and str(fid).strip() != "":
                        # 新建时对带ID项忽略（避免指向不存在行）
                        continue
                    rec = {
                        "F_GDID": gdid,
                        "F_MXID": mx_id,
                        "F_XHM": _clean_str((e or {}).get("F_XHM")),
                        "F_YL": _clean_num((e or {}).get("F_YL")),
                        "F_ND": _clean_str((e or {}).get("F_ND")),
                        "F_SYCS": _clean_int((e or {}).get("F_SYCS")),
                        "F_XHWD": _clean_num((e or {}).get("F_XHWD")),
                        "F_CZFS": _clean_str((e or {}).get("F_CZFS")),
                        "F_LXSL": _clean_num((e or {}).get("F_LXSL")),
                        "F_XHSJ": _clean_num((e or {}).get("F_XHSJ")),
                        "F_BZ": _clean_str((e or {}).get("F_BZ")),
                    }
                    cols2 = list(rec.keys())
                    ph2 = ", ".join(["?" for _ in cols2])
                    cur.execute(
                        f"INSERT INTO T_XHMSY ({', '.join(cols2)}) VALUES ({ph2})",
                        tuple(rec[c] for c in cols2),
                    )
                    try:
                        cur.execute("SELECT MAX(F_ID) FROM T_XHMSY WHERE F_GDID = ? AND F_MXID = ?", (gdid, mx_id))
                        new_id = int(cur.fetchone()[0])
                        xh_inserted.append(new_id)
                    except Exception:
                        pass

            # 与明细同事务回写工单细胞总数，避免事务外同步失败被静默忽略
            synced_order_fields = _sync_order_cell_totals_from_cdqxx(gdid, cdqxx, cur=cur)
            conn.commit()
        
        else:
            # 已存在 → 按 update 模式
            cols = [c[0] for c in cur.description]
            current = dict(zip(cols, row))
            mx_id = int(current.get("F_ID"))
            
            # 保存旧数据用于变更对比
            old_data = current.copy()
            
            # ✅ 在更新前获取完整的旧详情数据（用于F_OLD_DATA）
            try:
                old_detail_data_before_update = _get_full_culture_detail_by_gdid(gdid)
            except Exception as e:
                import logging
                logger = logging.getLogger(__name__)
                logger.warning(f"[培养明细质控] 获取更新前详情数据失败: {e}")

            updates: Dict[str, Any] = {"F_CZR": operator, "F_CZSJ": _now(), "F_YHBC": 1}

            if pyjtjjl:
                updates["F_PYJ_WD"] = _clean_num(pyjtjjl.get("F_PYJ_WD"))
                updates["F_PYJ_SD"] = _clean_num(pyjtjjl.get("F_PYJ_SD"))
            if pyxx_xh:
                updates["F_MJFA"] = _clean_str(pyxx_xh.get("F_MJFA"))
                updates["F_ZZMJFA"] = _clean_str(pyxx_xh.get("F_ZZMJFA"))
                updates["F_SWKJ"] = _clean_str(pyxx_xh.get("F_SWKJ"))
                updates["F_XBSL"] = _clean_num(pyxx_xh.get("F_XBSL"))
                updates["F_XBHL"] = _clean_num(pyxx_xh.get("F_XBHL"))
                updates["F_XBDL"] = _clean_num(pyxx_xh.get("F_XBDL"))
                # 新增字段：治疗细胞占比、处理后细胞活力、形态观察记录
                updates["F_ZLXBZB"] = _clean_str(pyxx_xh.get("F_ZLXBZB"))
                updates["F_CLHXBHL"] = _clean_str(pyxx_xh.get("F_CLHXBHL"))
                updates["F_XTGCJL"] = _clean_str(pyxx_xh.get("F_XTGCJL"))
            if pyxx_py:
                updates["F_TJPYJ"] = _clean_num(pyxx_py.get("F_TJPYJ"))
                updates["F_PYX_TEMP"] = _clean_num(pyxx_py.get("F_PYX_TEMP"))
                updates["F_PYX_CO2"] = _clean_num(pyxx_py.get("F_PYX_CO2"))
                updates["F_PYX_HUM"] = _clean_num(pyxx_py.get("F_PYX_HUM"))
                updates["F_PYX_FRSJ"] = _clean_str(pyxx_py.get("F_PYX_FRSJ"))
            if cdqxx:
                updates["F_CDQXBZS"] = _cdqxbzs_from_cdqxx(cdqxx)
                updates["F_CDQ_XBHL"] = _clean_str(cdqxx.get("F_CDQ_XBHL"))
                updates["F_JZMD"] = _clean_str(cdqxx.get("F_JZMD"))
            if qxycf is not None:
                updates["F_QXYCF"] = _clean_str(qxycf)

            # 附件：覆盖优先，否则增删
            try:
                exist_fj = json.loads(current.get("F_FJ") or "[]")
                exist_set = {int(x) for x in exist_fj}
            except Exception:
                exist_set = set()
            if file_ids_override is not None:
                exist_set = {int(x) for x in (file_ids_override or []) if x is not None}
            else:
                if add_file_ids:
                    for fid in add_file_ids:
                        try:
                            exist_set.add(int(fid))
                        except Exception:
                            pass
                if del_file_ids:
                    for fid in del_file_ids:
                        try:
                            exist_set.discard(int(fid))
                        except Exception:
                            pass
            updates["F_FJ"] = _json_dumps(sorted(list(exist_set)))

            # 基质胶：入参含 F_JZJ 时全量替换（忽略 JZJ_IDS_DELETE）
            if pyxx_pb:
                _apply_pyxx_pb_scalars(updates, pyxx_pb)
                if "F_JZJ" in pyxx_pb:
                    updates["F_JZJ"] = _json_dumps(
                        _build_jzj_items_full_replace(pyxx_pb.get("F_JZJ"))
                    )

            # 执行更新（F_YHBC 未迁移时降级）
            set_cols = [k for k, v in updates.items() if v is not None]
            if set_cols:
                try:
                    sets = ", ".join([f"{c} = ?" for c in set_cols])
                    params = [updates[c] for c in set_cols] + [mx_id]
                    cur.execute(f"UPDATE T_PYMX_ZZ SET {sets} WHERE F_ID = ?", tuple(params))
                except Exception:
                    set_cols = [c for c in set_cols if c != "F_YHBC"]
                    if set_cols:
                        sets = ", ".join([f"{c} = ?" for c in set_cols])
                        params = [updates[c] for c in set_cols] + [mx_id]
                        cur.execute(f"UPDATE T_PYMX_ZZ SET {sets} WHERE F_ID = ?", tuple(params))

            # 消化酶：入参 XHMSYJL 非 None 时按增量方式同步（忽略 XHMSYJL_DELETE），
            # 保留内容未变更记录的 F_ID，避免质控记录误判为"变更"
            xh_updated: List[int] = []
            xh_inserted: List[int] = []
            xh_deleted: List[int] = []

            # 先查询现有消化酶（用于质控对比）
            cur.execute(
                "SELECT * FROM T_XHMSY WHERE F_GDID = ? AND F_MXID = ? ORDER BY F_ID ASC",
                (gdid, mx_id),
            )
            old_xhmsy_rows = cur.fetchall()
            old_xhmsy_cols = [c[0] for c in cur.description] if cur.description else []
            old_xhmsy_list = []
            for row in old_xhmsy_rows:
                xh_data = dict(zip(old_xhmsy_cols, row))
                xh_data = _convert_decimals(xh_data)
                old_xhmsy_list.append(xh_data)

            if xhmsy_list is not None:
                xh_deleted, xh_inserted, xh_updated = _replace_xhmsy_for_mx(
                    cur, gdid, mx_id, xhmsy_list or []
                )

            # 与明细同事务回写工单细胞总数
            synced_order_fields = _sync_order_cell_totals_from_cdqxx(gdid, cdqxx, cur=cur)
            conn.commit()

            # commit 后查询新消化酶列表（用于质控对比）
            cur.execute(
                "SELECT * FROM T_XHMSY WHERE F_GDID = ? AND F_MXID = ? ORDER BY F_ID ASC",
                (gdid, mx_id),
            )
            new_xhmsy_rows = cur.fetchall()
            new_xhmsy_cols = [c[0] for c in cur.description] if cur.description else []
            new_xhmsy_list = []
            for row in new_xhmsy_rows:
                xh_data = dict(zip(new_xhmsy_cols, row))
                xh_data = _convert_decimals(xh_data)
                new_xhmsy_list.append(xh_data)
    
    # ========== 查询工单信息（新增/更新都需要） ==========
    zzid = None
    order_info = {}
    try:
        with adapter.connect() as conn_order:
            cur_order = conn_order.cursor()
            cur_order.execute("SELECT F_ZZID, F_GDBH, F_DCID, F_DC, F_PYSJ, F_SQR, F_XM, F_BRBH, F_YBLY FROM T_PYGD WHERE F_ID = ?", (gdid,))
            row_order = cur_order.fetchone()
            if row_order:
                zzid = row_order[0]
                order_info = {
                    "F_GDBH": row_order[1],
                    "F_DCID": row_order[2],
                    "F_DC": row_order[3],
                    "F_PYSJ": row_order[4],
                    "F_SQR": row_order[5],
                    "F_XM": row_order[6],
                    "F_BRBH": row_order[7],
                    "F_YBLY": row_order[8]
                }
    except Exception as e:
        import logging
        logger = logging.getLogger(__name__)
        logger.error(f"查询工单信息失败: {e}", exc_info=True)
    
    # ========== 同步到 T_YCL 并重新计算评分（新增/更新都需要） ==========
    try:
        if zzid:
            # 新增分支用 main，更新分支用 updates
            if is_new_record:
                f_zlxbzb = main.get("F_ZLXBZB")
                f_clhxbhl = main.get("F_CLHXBHL")
            else:
                f_zlxbzb = updates.get("F_ZLXBZB")
                f_clhxbhl = updates.get("F_CLHXBHL")
            
            print(f"调试: 开始同步工单{gdid}的F_ZLXBZB={f_zlxbzb}, F_CLHXBHL={f_clhxbhl}到T_YCL")
            _sync_to_ycl_and_rescore(gdid, zzid, f_zlxbzb, f_clhxbhl, operator)
            print(f"成功: 工单{gdid}的样本评分已更新")
    except Exception as e:
        # 记录错误但不影响主流程
        print(f"错误: 同步到T_YCL失败 - {e}")
        import traceback
        traceback.print_exc()

    # ========== 创建质控审核记录（新增/更新都需要） ==========
    qc_audit_id = None

    if zzid and operator:
        try:
            from app.crud.qc_audit import create_qc_audit_record, get_sample_basic_info, calculate_changes
            import logging
            logger = logging.getLogger(__name__)
            
            # 获取样本基本信息
            sample_info = get_sample_basic_info(zzid)
            
            # ✅ 获取完整的旧详情数据（用于F_OLD_DATA字段）
            old_detail_data = None
            if not is_new_record:
                # 编辑操作：使用更新前获取的详情数据
                old_detail_data = old_detail_data_before_update
                if old_detail_data:
                    logger.info(f"[培养明细质控] 使用更新前的旧详情数据，工单ID: {gdid}")
                else:
                    logger.warning(f"[培养明细质控] 更新前的旧详情数据为空，工单ID: {gdid}")
            
            # 判断是新增还是更新
            if is_new_record:
                # 新增分支
                bglx = "新增"
                changes = {
                    "操作": "新增培养明细",
                    "关键信息": {
                        "明细ID": mx_id,
                        "工单ID": gdid,
                        "标本号": zzid
                    }
                }
                
                # ✅ 新增操作：获取新增后的完整详情数据（包含工单信息和培养明细）
                try:
                    old_detail_data = _get_full_culture_detail_by_gdid(gdid)
                    logger.info(f"[培养明细质控] 新增操作：成功获取新详情数据用于记录，工单ID: {gdid}")
                except Exception as e:
                    logger.warning(f"[培养明细质控] 新增操作：获取新详情数据失败: {e}")
                    old_detail_data = None
            else:
                # 更新分支
                bglx = "修改"
                changes = calculate_changes(old_data, updates)
                
                # ✅ 对比消化酶使用记录的新旧列表
                if old_xhmsy_list or new_xhmsy_list:
                    # 对比新旧列表是否有差异
                    xhmsy_changed = json.dumps(old_xhmsy_list, ensure_ascii=False, sort_keys=True) != \
                                   json.dumps(new_xhmsy_list, ensure_ascii=False, sort_keys=True)
                    
                    if xhmsy_changed:
                        # 有差异，添加到变更字段
                        if not changes.get("变更字段"):
                            changes["变更字段"] = []
                        changes["变更字段"].append({
                            "field": "XHMSYJL",
                            "label": "消化酶使用记录",
                            "old_value": old_xhmsy_list,
                            "new_value": new_xhmsy_list
                        })
                
                if not changes.get("变更字段"):
                    # 没有任何实际变更，不创建质控记录
                    logger.info(f"[培养明细质控] 培养明细未变更，跳过质控记录创建 (工单ID: {gdid})")
                    qc_audit_id = None
                    changes = None
            
            # 只有有变更时才创建记录
            if changes:
                # ✅ 创建质控审核记录，包含F_OLD_DATA
                qc_audit_id = create_qc_audit_record(
                    F_ZKJD="培养",
                    F_BGLX=bglx,
                    F_ZZID=zzid,
                    F_CHANGES=changes,
                    F_JLR=operator or "系统",
                    F_GDID=gdid,
                    F_XM=sample_info.get("F_XM") or order_info.get("F_XM"),
                    F_BRBH=sample_info.get("F_BRBH") or order_info.get("F_BRBH"),
                    F_ZZBW=sample_info.get("F_ZZBW"),
                    F_YBLY=order_info.get("F_YBLY"),  # 从工单信息中获取样本来源
                    # 工单相关字段
                    F_GDBH=order_info.get("F_GDBH"),
                    F_DCID=order_info.get("F_DCID"),
                    F_DC=order_info.get("F_DC"),
                    F_PYSJ=order_info.get("F_PYSJ"),
                    F_SQR=order_info.get("F_SQR"),
                    F_OLD_DATA=old_detail_data  # ✅ 传入完整旧详情数据
                )
                logger.info(f"[培养明细质控] 创建质控审核记录成功，工单ID: {gdid}, 类型: {bglx}, 质控ID: {qc_audit_id}")
        except Exception as e:
            import logging
            logger = logging.getLogger(__name__)
            logger.error(f"[培养明细质控] 创建质控审核记录失败 (工单ID: {gdid}): {e}", exc_info=True)
    
    # ========== 返回结果（新增/更新不同） ==========
    if is_new_record:
        # 新增分支
        result = {
            "F_MXID": mx_id,
            "F_GDID": gdid,
            "F_FJ_IDS": json.loads(main.get("F_FJ") or "[]"),
            "XHMSY_INSERTED_IDS": xh_inserted,
            "XHMSY_UPDATED_IDS": [],
            "XHMSY_DELETED_IDS": [],
        }
    else:
        # 更新分支
        result = {
            "F_MXID": mx_id,
            "F_GDID": gdid,
            "F_FJ_IDS": sorted(list(exist_set)),
            "XHMSY_INSERTED_IDS": xh_inserted,
            "XHMSY_UPDATED_IDS": xh_updated,
            "XHMSY_DELETED_IDS": xh_deleted,
        }

    if synced_order_fields:
        result["order_cell_totals"] = synced_order_fields
    
    if qc_audit_id:
        result["qc_audit_id"] = qc_audit_id
    
    return result
