from typing import Dict, Any, List, Optional, Tuple
import json
from datetime import datetime
from app.db.factory import get_adapter
from app.crud.drug_mgmt import _generate_outbound_code


def _safe_encode_for_db(value: Any) -> Any:
    """
    将字符串中的特殊Unicode字符转换为GBK兼容的字符
    """
    if not isinstance(value, str):
        return value
    
    # 常见的Unicode特殊字符替换
    replacements = {
        '×': 'x',  # 乘号 → 小写x
        '⁰': '0', '¹': '1', '²': '2', '³': '3', '⁴': '4',
        '⁵': '5', '⁶': '6', '⁷': '7', '⁸': '8', '⁹': '9',  # 上标数字
        '₀': '0', '₁': '1', '₂': '2', '₃': '3', '₄': '4',
        '₅': '5', '₆': '6', '₇': '7', '₈': '8', '₉': '9',  # 下标数字
        'μ': 'u',  # 微
        '±': '+/-',  # 正负号
        '°': 'deg',  # 度
        '≤': '<=', '≥': '>=',  # 小于等于/大于等于
        '≈': '~',  # 约等于
    }
    
    result = value
    for old, new in replacements.items():
        result = result.replace(old, new)
    
    # 如果还有GBK无法编码的字符，尝试编码测试
    try:
        result.encode('gbk')
        return result
    except UnicodeEncodeError:
        # 移除或替换无法编码的字符
        return result.encode('gbk', errors='ignore').decode('gbk')


def _coerce_plan_text(value: Any, *, cell_count: bool = False) -> Any:
    """主表文本列。数组和对象不能原样绑定，空数组会被驱动写成 ()。"""
    if value is None or isinstance(value, (int, float)):
        return value
    if isinstance(value, str):
        return _safe_encode_for_db(value)
    if isinstance(value, dict):
        return _coerce_plan_text(_plan_text_from_parts(list(value.values()), cell_count=cell_count), cell_count=cell_count)
    if isinstance(value, (list, tuple, set)):
        return _plan_text_from_parts(list(value), cell_count=cell_count)
    return _safe_encode_for_db(str(value).strip())


def _plan_text_from_parts(parts: List[Any], *, cell_count: bool = False) -> str:
    items: List[str] = []
    for part in parts:
        if part is None or isinstance(part, (list, tuple, set, dict)):
            text = _coerce_plan_text(part, cell_count=cell_count)
            if not isinstance(text, str):
                continue
        else:
            text = str(part).strip()
        if text:
            items.append(text)
    if not items:
        return ""
    if len(items) == 1:
        return _safe_encode_for_db(items[0])
    if cell_count and len(items) == 2 and all(_is_plain_number(item) for item in items):
        return _safe_encode_for_db(f"{items[0]}x10^{items[1]}")
    return _safe_encode_for_db(",".join(items))


def _is_plain_number(value: str) -> bool:
    try:
        float(value)
    except (TypeError, ValueError):
        return False
    return True


def _clean_data_for_db(data: Dict[str, Any]) -> Dict[str, Any]:
    """递归清理数据中的特殊字符"""
    cleaned = {}
    for key, value in data.items():
        if isinstance(value, str):
            cleaned[key] = _safe_encode_for_db(value)
        elif isinstance(value, dict):
            cleaned[key] = _clean_data_for_db(value)
        elif isinstance(value, list):
            cleaned[key] = [
                _clean_data_for_db(item) if isinstance(item, dict)
                else _safe_encode_for_db(item) if isinstance(item, str)
                else item
                for item in value
            ]
        else:
            cleaned[key] = value
    return cleaned


def _parse_datetime(value: Any) -> Optional[str]:
    """解析时间字段为字符串"""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d %H:%M:%S")
    if isinstance(value, str):
        return value
    return str(value)


def _parse_json_field(value: Any) -> Any:
    """解析JSON字段"""
    if value is None:
        return None
    if isinstance(value, (bytes, bytearray)):
        try:
            return json.loads(value.decode("utf-8", errors="ignore"))
        except Exception:
            return None
    if isinstance(value, str):
        try:
            return json.loads(value)
        except Exception:
            return None
    return value


def _well_position_label(index: int, cols: int = 12) -> str:
    """按 96 孔板行优先顺序生成孔位标签：A1, A2, …, A12, B1, …"""
    row = index // cols
    col = index % cols + 1
    if row > 25:
        return str(index + 1)
    return f"{chr(ord('A') + row)}{col}"


def normalize_kwxx_positions(
    wells: List[Dict[str, Any]], cols: int = 12
) -> List[Dict[str, Any]]:
    """为缺失 F_WZ 的孔位按顺序补全 A1/A2/…（流水线模板等场景）。"""
    result: List[Dict[str, Any]] = []
    for idx, well in enumerate(wells or []):
        item = dict(well)
        wz = item.get("F_WZ")
        if wz is None or str(wz).strip() == "":
            item["F_WZ"] = _well_position_label(idx, cols)
        else:
            item["F_WZ"] = str(wz).strip()
        result.append(item)
    return result


def _generate_test_plan_code(adapter) -> str:
    """
    生成实验编号
    格式：SYSY + YYYYMMDDHHMMSS + 毫秒(3位)
    例如：SYSY20251120153045123
    """
    return "SYSY" + datetime.now().strftime("%Y%m%d%H%M%S%f")[:-3]


def _parse_drug_amount(amount: Any) -> Optional[float]:
    """解析用药量/出库量，非法或空则返回 None。"""
    if amount is None:
        return None
    text = str(amount).strip()
    if not text:
        return None
    try:
        val = float(text)
        return val if val > 0 else None
    except (TypeError, ValueError):
        return None


def _batch_stock_sql() -> str:
    """按入库单维度计算批次在库数量的子查询 SQL 片段。"""
    return """
        SELECT
            rkd.F_RKDH,
            rkd.F_YXQ,
            rkd.F_RKSL - COALESCE(
                (SELECT SUM(ckd.F_CKSL)
                 FROM T_YW_CKD ckd
                 WHERE ckd.F_RKDH = rkd.F_RKDH),
                0
            ) AS F_ZKSL
        FROM T_YW_RKD rkd
        WHERE rkd.F_YWID = ? AND rkd.F_PCH = ?
        ORDER BY rkd.F_YXQ ASC, rkd.F_RKDH ASC
    """


def _get_batch_stock_rows(cur, drug_id: int, batch: str) -> List[Tuple[str, Any, float]]:
    """查询同一试剂+批次号下各入库单的在库量（跳过 ≤0 的行）。"""
    batch_key = str(batch or "").strip()
    if not batch_key:
        return []
    cur.execute(_batch_stock_sql(), (drug_id, batch_key))
    rows: List[Tuple[str, Any, float]] = []
    for rkdh, yxq, zksl in cur.fetchall():
        stock = float(zksl or 0)
        if stock > 0:
            rows.append((rkdh, yxq, stock))
    return rows


def _get_batch_total_stock(cur, drug_id: int, batch: str) -> float:
    """汇总同一试剂+批次号在所有入库单上的在库总量。"""
    return sum(stock for _, _, stock in _get_batch_stock_rows(cur, drug_id, batch))


def _pick_inbound_for_outbound(cur, drug_id: int, batch: str, amount: float) -> Tuple[str, Any]:
    """
    选择可出库的入库单：优先有效期更早的批次（FEFO），且单张入库单在库量 ≥ amount。
    """
    candidates = _get_batch_stock_rows(cur, drug_id, batch)
    eligible = [(rkdh, yxq, stock) for rkdh, yxq, stock in candidates if stock >= amount]
    if not eligible:
        total = sum(stock for _, _, stock in candidates)
        raise ValueError(
            f"批次 {batch} 无足够库存的入库单（需要 {amount}，各入库单可用合计 {total}）"
        )
    eligible.sort(key=lambda x: (x[1] if x[1] is not None else datetime.max, x[0]))
    rkdh, yxq, _ = eligible[0]
    return rkdh, yxq


def _sum_plan_releasable_stock(
    cur,
    plan_id: int,
    replace_group_ids: List[int],
    del_group_ids: List[int],
) -> Dict[Tuple[int, str], float]:
    """
    统计本次更新即将删除/重建的组别中，按 (F_SJID, F_PCH) 可还库的出库量。
    用于更新场景：校验新出库前，把本实验即将释放的库存加回可用量。
    """
    zb_ids = {int(x) for x in (replace_group_ids or []) if x} | {int(x) for x in (del_group_ids or []) if x}
    if not zb_ids:
        return {}

    placeholders = ",".join(["?"] * len(zb_ids))
    sql = f"""
        SELECT yy.F_SJID, yy.F_PCH, COALESCE(SUM(ckd.F_CKSL), 0) AS release_qty
        FROM T_SYJH_YY yy
        INNER JOIN T_SYJH_ZB zb ON zb.F_ID = yy.F_ZB_ID
        LEFT JOIN T_YW_CKD ckd ON ckd.F_CKDH = yy.F_CKDH
        WHERE zb.F_SYJH_ID = ? AND yy.F_ZB_ID IN ({placeholders})
        GROUP BY yy.F_SJID, yy.F_PCH
    """
    cur.execute(sql, (plan_id, *tuple(zb_ids)))
    result: Dict[Tuple[int, str], float] = {}
    for sjid, pch, qty in cur.fetchall():
        if sjid is None:
            continue
        key = (int(sjid), str(pch or "").strip())
        result[key] = result.get(key, 0.0) + float(qty or 0)
    return result


def _validate_drug_stock(
    adapter,
    drug_id: int,
    batch: str,
    required_amount: str,
    *,
    extra_available: float = 0,
) -> bool:
    """
    验证药物库存是否充足（同一批次号可能对应多张入库单，需汇总在库量）。
    extra_available：更新场景下本实验即将还库的出库量。
    """
    required = _parse_drug_amount(required_amount)
    if required is None:
        return False

    batch_key = str(batch or "").strip()
    if not batch_key:
        return False

    with adapter.connect() as conn:
        cur = conn.cursor()
        total = _get_batch_total_stock(cur, drug_id, batch_key)

    return (total + float(extra_available or 0)) >= required


def _create_auto_outbound(adapter, conn, drug_id: int, batch: str, amount: str, remark: str) -> Optional[str]:
    """
    自动创建出库单（从在库量充足的入库单出库，优先 FEFO）。
    返回：出库单号
    """
    cur = conn.cursor()
    batch_key = str(batch or "").strip()
    outbound_qty = _parse_drug_amount(amount)
    if outbound_qty is None:
        raise ValueError(f"出库数量非法: {amount!r}")

    rkdh, yxq = _pick_inbound_for_outbound(cur, drug_id, batch_key, outbound_qty)

    ckdh = _generate_outbound_code()
    cur.execute(
        """
        INSERT INTO T_YW_CKD (F_YWID, F_PCH, F_CKSL, F_YXQ, F_CKRQ, F_CKR, F_BZ, F_CKDH, F_RKDH)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            drug_id,
            batch_key,
            amount,
            yxq,
            datetime.now(),
            "系统自动",
            remark,
            ckdh,
            rkdh,
        ),
    )
    return ckdh


def _validate_unique_drug_usage_in_groups(groups: List[Dict[str, Any]]) -> None:
    """校验每个组别用药列表内 (F_SJID, F_PCH) 不重复"""
    for idx, g in enumerate(groups):
        seen: set[Tuple[Any, str]] = set()
        zbmc = g.get("F_ZBMC") or f"第{idx + 1}个组别"
        for drug in g.get("F_YY") or []:
            drug_id = drug.get("F_SJID")
            batch = str(drug.get("F_PCH") or "").strip()
            key = (drug_id, batch)
            if key in seen:
                raise ValueError(
                    f"第 {idx + 1} 个组别「{zbmc}」的用药信息中存在重复："
                    f"试剂ID {drug_id}、批次号 {batch or '(空)'}"
                )
            seen.add(key)


def create_test_plan(data: Dict[str, Any], operator: Optional[str] = None, skip_stock_check: bool = False, from_pipeline_template: bool = False) -> Dict[str, Any]:
    """创建实验计划。
    skip_stock_check=True 时跳过药物库存校验和自动出库，用于流水线模板初始化场景。
    """
    import logging
    logger = logging.getLogger(__name__)
    
    adapter = get_adapter()
    
    # 清理数据中的特殊字符（避免GBK编码错误）
    data = _clean_data_for_db(data)
    if not from_pipeline_template:
        from app.crud.field_config import assert_page
        assert_page("drug_plan", data)

    # ========== 前置参数校验 ==========
    if not data.get("F_GDID"):
        raise ValueError("F_GDID（关联工单ID）不能为空")

    # ========== 业务逻辑校验 ==========
    # 组别级复孔数/浓度校验与孔位匹配
    groups = data.get("ZBXX", []) or []
    wells = normalize_kwxx_positions(data.get("KWXX", []) or [])
    data["KWXX"] = wells

    if not groups:
        raise ValueError("ZBXX 不能为空")

    group_map: Dict[str, Dict[str, Any]] = {}
    expected_well_count = 0
    for idx, g in enumerate(groups):
        fkssz = g.get("F_FKSSZ")
        nd_list = g.get("F_ND") or []
        try:
            fkssz_int = int(fkssz)
        except Exception:
            raise ValueError(f"第 {idx + 1} 个组别的 F_FKSSZ 非法，需为整数")
        if fkssz_int <= 0:
            raise ValueError(f"第 {idx + 1} 个组别的 F_FKSSZ 必须大于0")
        if not isinstance(nd_list, list) or len(nd_list) == 0:
            raise ValueError(f"第 {idx + 1} 个组别的 F_ND 不能为空")
        nd_strs = [str(x) for x in nd_list]
        expected_well_count += fkssz_int * len(nd_strs)
        group_map[g.get("F_ZBMC")] = {"fkssz": fkssz_int, "nds": set(nd_strs)}
        # 确保排序字段
        if g.get("F_SORT") is None:
            g["F_SORT"] = idx + 1

    _validate_unique_drug_usage_in_groups(groups)

    well_count = len(wells)
    if well_count != expected_well_count:
        raise ValueError(
            f"孔位数量不匹配！期望孔位数 = Σ(组别复孔数 × 该组浓度数量) = {expected_well_count}，"
            f"但实际传入了 {well_count} 个孔位信息。请检查孔位配置是否完整。"
        )

    # 校验每个孔位的组别与浓度归属
    for idx, well in enumerate(wells):
        zbmc = well.get("F_ZBMC")
        nd_val = str(well.get("F_ND"))
        if zbmc not in group_map:
            raise ValueError(f"孔位第 {idx + 1} 条引用了不存在的组别: {zbmc}")
        if nd_val not in group_map[zbmc]["nds"]:
            raise ValueError(f"孔位第 {idx + 1} 条的浓度 {nd_val} 不属于组别 {zbmc} 的浓度列表")
    
    # 生成实验编号
    sybh = _generate_test_plan_code(adapter)

    # 在事务外提前校验所有药物库存，避免事务内嵌套新连接读取同一张表导致自锁卡死
    if not skip_stock_check:
        for g_idx, g in enumerate(data.get("ZBXX", [])):
            for drug in g.get("F_YY", []):
                drug_id = drug.get("F_SJID")
                batch = drug.get("F_PCH")
                amount = drug.get("F_YL")
                if not _validate_drug_stock(adapter, drug_id, batch, amount):
                    raise ValueError(f"药物ID {drug_id} 批次 {batch} 库存不足或不存在")

    with adapter.connect() as conn:
        cur = conn.cursor()
        
        # 1. 插入主表
        from app.utils.yhbc import yhbc_flag
        main_fields = {
            "F_GDID": data.get("F_GDID"),
            "F_SYBH": sybh,
            "F_SYMC": _coerce_plan_text(data.get("F_SYMC")),
            "F_SYKSRQ": _coerce_plan_text(data.get("F_SYKSRQ")),
            "F_SYR": _coerce_plan_text(data.get("F_SYR")),
            "F_SYJHYMD": _coerce_plan_text(data.get("F_SYJHYMD")),
            "F_XBSL": _coerce_plan_text(data.get("F_XBSL"), cell_count=True),
            "F_JZJND": _coerce_plan_text(data.get("F_JZJND")),
            "F_JZJTJ": _coerce_plan_text(data.get("F_JZJTJ")),
            "F_FJ": data.get("F_FJ"),
            "F_KBLX": _coerce_plan_text(data.get("F_KBLX")),
            "F_KBBH": _coerce_plan_text(data.get("F_KBBH")),
            "F_BZ": _coerce_plan_text(data.get("F_BZ")),
            "F_ZT": "已创建",
            "F_CJSJ": datetime.now(),
            "F_YHBC": yhbc_flag(from_pipeline_template),
        }
        
        cols = [k for k, v in main_fields.items() if v is not None]
        vals = [main_fields[k] for k in cols]
        placeholders = ", ".join(["?" for _ in cols])
        
        insert_sql = f"INSERT INTO T_SYJH ({', '.join(cols)}) VALUES ({placeholders})"
        try:
            cur.execute(insert_sql, tuple(vals))
        except Exception:
            main_fields.pop("F_YHBC", None)
            cols = [k for k, v in main_fields.items() if v is not None]
            vals = [main_fields[k] for k in cols]
            placeholders = ", ".join(["?" for _ in cols])
            insert_sql = f"INSERT INTO T_SYJH ({', '.join(cols)}) VALUES ({placeholders})"
            cur.execute(insert_sql, tuple(vals))
        
        # 获取主表ID（通过唯一编号定位，避免 F_GDID=None 时的 NULL 匹配问题）
        cur.execute("SELECT MAX(F_ID) FROM T_SYJH WHERE F_SYBH = ?", (sybh,))
        plan_id = cur.fetchone()[0]
        if plan_id is None:
            raise ValueError(f"创建实验计划失败：无法获取新增记录ID（SYBH={sybh}）")
        
        # 用于统计自动生成的出库单
        auto_outbound_codes = []
        
        # 2. 插入组别和用药信息
        groups = data.get("ZBXX", [])
        
        for group in groups:
            # 插入组别
            cur.execute("""
                INSERT INTO T_SYJH_ZB (F_SYJH_ID, F_ZBMC, F_ZBLX, F_SORT, F_FKSSZ, F_ND)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (
                plan_id,
                group.get("F_ZBMC"),
                group.get("F_ZBLX"),
                group.get("F_SORT"),
                group.get("F_FKSSZ"),
                json.dumps(group.get("F_ND", []), ensure_ascii=False)
            ))
            
            # 获取组别ID
            cur.execute("SELECT MAX(F_ID) FROM T_SYJH_ZB WHERE F_SYJH_ID = ?", (plan_id,))
            group_id = cur.fetchone()[0]
            
            # 插入用药信息
            drugs = group.get("F_YY", [])
            for drug in drugs:
                drug_id = drug.get("F_SJID")
                batch = drug.get("F_PCH")
                amount = drug.get("F_YL")
                xsfa = drug.get("F_XSFA")
                
                ckdh = None
                if not skip_stock_check:
                    # 自动创建出库单（库存已在事务外统一校验过）
                    try:
                        remark = f"用于实验计划：{data.get('F_SYMC')}"
                        ckdh = _create_auto_outbound(adapter, conn, drug_id, batch, amount, remark)
                        auto_outbound_codes.append(ckdh)
                    except Exception as e:
                        conn.rollback()
                        raise ValueError(f"创建出库单失败: {e}")
                
                # 插入用药记录
                cur.execute("""
                    INSERT INTO T_SYJH_YY (F_ZB_ID, F_SJID, F_PCH, F_YL, F_XSFA, F_CKDH)
                    VALUES (?, ?, ?, ?, ?, ?)
                """, (group_id, drug_id, batch, amount, xsfa, ckdh))
        
        # 3. 插入孔位信息
        wells = data.get("KWXX", [])
        for well in wells:
            cur.execute("""
                INSERT INTO T_SYJH_KW (F_SYJH_ID, F_WZ, F_ZBMC, F_ND, F_JYTJ, F_XSFA, F_YS)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (
                plan_id,
                well.get("F_WZ"),
                well.get("F_ZBMC"),
                well.get("F_ND"),
                well.get("F_JYTJ"),
                well.get("F_XSFA"),
                well.get("F_YS")
            ))
        
        conn.commit()
    
    # ========== 创建质控记录 ==========
    qc_audit_id = None
    try:
        from app.crud.qc_audit import create_qc_audit_record, get_sample_basic_info
        
        # 获取工单和样本信息
        gdid = data.get("F_GDID")
        with adapter.connect() as conn:
            cur = conn.cursor()
            cur.execute("SELECT F_ZZID, F_DCID, F_DC, F_GDBH, F_SQR, F_PYSJ, F_YBLY FROM T_PYGD WHERE F_ID = ?", (gdid,))
            gd_row = cur.fetchone()
        
        if gd_row:
            zzid = gd_row[0]
            dcid = gd_row[1]
            dc = gd_row[2]
            gdbh = gd_row[3]
            sqr = gd_row[4]
            pysj = gd_row[5]
            ybly = gd_row[6]  # ✅ 获取样本来源
            
            # 获取样本基本信息
            sample_info = get_sample_basic_info(zzid) if zzid else {}
            
            # 获取新增后的完整详情数据
            new_detail_data = get_test_plan_detail(plan_id)
            
            # 准备变更内容
            changes = {
                "变更字段": [
                    {"字段": "实验名称", "新值": data.get("F_SYMC")},
                    {"字段": "实验编号", "新值": sybh},
                    {"字段": "实验开始日期", "新值": data.get("F_SYKSRQ")},
                    {"字段": "实验人", "新值": data.get("F_SYR")},
                    {"字段": "孔板类型", "新值": data.get("F_KBLX")},
                    {"字段": "孔板编号", "新值": data.get("F_KBBH")},
                    {"字段": "组别数量", "新值": len(groups)},
                    {"字段": "孔位数量", "新值": len(wells)}
                ]
            }
            
            # 创建质控记录
            qc_audit_id = create_qc_audit_record(
                F_ZKJD="药物实验计划",
                F_BGLX="新增",
                F_ZZID=zzid,
                F_CHANGES=changes,
                F_JLR=operator or "系统",
                F_GDID=gdid,
                F_XM=sample_info.get("F_XM"),
                F_BRBH=sample_info.get("F_BRBH"),
                F_YBLY=ybly,  # ✅ 从工单信息中获取样本来源
                F_GDBH=gdbh,
                F_DCID=dcid,
                F_DC=dc,
                F_PYSJ=pysj,
                F_SQR=sqr,
                F_OLD_DATA=new_detail_data,
                from_pipeline_template=from_pipeline_template,
            )
            logger.info(f"[药物实验计划质控] 质控记录创建成功，计划ID: {plan_id}, 质控ID: {qc_audit_id}")
    except Exception as e:
        logger.error(f"[药物实验计划质控] 创建质控审核记录失败（计划ID: {plan_id}）: {e}", exc_info=True)
    
    result = {
        "F_ID": plan_id,
        "F_GDID": data.get("F_GDID"),
        "F_SYBH": sybh,
        "F_SYMC": data.get("F_SYMC"),
        "auto_outbound_count": len(auto_outbound_codes),
        "auto_outbound_codes": auto_outbound_codes
    }
    
    if qc_audit_id:
        result["qc_audit_id"] = qc_audit_id
    
    return result


def get_test_plan_detail(plan_id: int) -> Optional[Dict[str, Any]]:
    """获取实验计划详情"""
    adapter = get_adapter()
    
    with adapter.connect() as conn:
        cur = conn.cursor()
        
        # 1. 查询主表
        cur.execute("""
            SELECT F_ID, F_GDID, F_SYBH, F_SYMC, F_SYKSRQ, F_SYR, F_SYJHYMD, F_XBSL, F_JZJND, F_JZJTJ,
                   F_FJ, F_KBLX, F_KBBH, F_ZT, F_CJSJ, F_BZ
            FROM T_SYJH
            WHERE F_ID = ?
        """, (plan_id,))
        
        row = cur.fetchone()
        if not row:
            return None
        
        cols = [c[0] for c in cur.description]
        main_data = dict(zip(cols, row))
        
        # 解析时间字段
        main_data["F_SYKSRQ"] = _parse_datetime(main_data.get("F_SYKSRQ"))
        main_data["F_CJSJ"] = _parse_datetime(main_data.get("F_CJSJ"))
        
        # 转换附件ID为静态URL
        if main_data.get("F_FJ"):
            try:
                from app.crud.file_center import get_files_by_ids
                file_id = int(main_data["F_FJ"])
                files = get_files_by_ids([file_id])
                if files:
                    main_data["F_FJ"] = files[0].get("F_PATH")  # 返回静态路径，如 "uploads/xxx/xxx.jpg"
                else:
                    main_data["F_FJ"] = None
            except Exception:
                main_data["F_FJ"] = None
        
        # 2. 查询组别和用药信息
        cur.execute("""
            SELECT F_ID, F_ZBMC, F_ZBLX, F_SORT, F_FKSSZ, F_ND
            FROM T_SYJH_ZB
            WHERE F_SYJH_ID = ?
            ORDER BY F_SORT, F_ID
        """, (plan_id,))
        
        groups = []
        for group_row in cur.fetchall():
            fkssz_val = group_row[4] if group_row[4] is not None else 0
            nd_list_val = _parse_json_field(group_row[5]) or []
            group_data = {
                "F_ID": group_row[0],
                "F_ZBMC": group_row[1],
                "F_ZBLX": group_row[2],
                "F_SORT": group_row[3],
                "F_FKSSZ": int(fkssz_val),
                "F_ND": nd_list_val,
                "F_YY": []
            }
            
            # 查询该组的用药信息
            cur.execute("""
                SELECT F_ID, F_SJID, F_PCH, F_YL, F_XSFA, F_CKDH
                FROM T_SYJH_YY
                WHERE F_ZB_ID = ?
            """, (group_row[0],))
            
            for drug_row in cur.fetchall():
                group_data["F_YY"].append({
                    "F_ID": drug_row[0],
                    "F_SJID": drug_row[1],
                    "F_PCH": drug_row[2],
                    "F_YL": drug_row[3],
                    "F_XSFA": drug_row[4],
                    "F_CKDH": drug_row[5]
                })
            
            groups.append(group_data)
        
        main_data["ZBXX"] = groups
        
        # 3. 查询孔位信息
        cur.execute("""
            SELECT F_ID, F_WZ, F_ZBMC, F_ND, F_JYTJ, F_XSFA, F_YS
            FROM T_SYJH_KW
            WHERE F_SYJH_ID = ?
            ORDER BY F_WZ
        """, (plan_id,))
        
        wells = []
        for idx, well_row in enumerate(cur.fetchall()):
            wz = well_row[1]
            if wz is None or str(wz).strip() == "":
                wz = _well_position_label(idx)
            else:
                wz = str(wz).strip()
            wells.append({
                "F_ID": well_row[0],
                "F_WZ": wz,
                "F_ZBMC": well_row[2],
                "F_ND": well_row[3],
                "F_JYTJ": well_row[4],
                "F_XSFA": well_row[5],
                "F_YS": well_row[6]
            })
        
        main_data["KWXX"] = wells
        
        # 4. 查询工单样本信息
        gdid = main_data.get("F_GDID")
        if gdid:
            try:
                cur.execute("""
                    SELECT F_ZZID, F_DCID, F_DC, F_GDBH, F_YBLY, F_SQR, F_PYSJ
                    FROM T_PYGD
                    WHERE F_ID = ?
                """, (gdid,))
                gd_row = cur.fetchone()
                if gd_row:
                    main_data["GDXX"] = {
                        "F_ZZID": gd_row[0],
                        "F_DCID": gd_row[1],
                        "F_DC": gd_row[2],
                        "F_GDBH": gd_row[3],
                        "F_YBLY": gd_row[4],
                        "F_SQR": gd_row[5],
                        "F_PYSJ": _parse_datetime(gd_row[6])
                    }
                else:
                    main_data["GDXX"] = None
            except Exception:
                main_data["GDXX"] = None
        else:
            main_data["GDXX"] = None
        
        return main_data


def _format_pdf_urls(pdf_json_str: Optional[str]) -> Dict[str, Any]:
    """
    将F_BG_PDF的JSON字符串转换为包含URL的结构（不带uploads前缀）
    
    Args:
        pdf_json_str: PDF的JSON字符串
        
    Returns:
        {
            "generated": {"id": 123, "name": "xxx.pdf", "path": "drug_test_report/xxx.pdf"},
            "uploaded": [{"id": 456, "name": "yyy.pdf", "path": "xxx/yyy.pdf"}]
        }
    """
    from app.crud.file_center import get_file_by_id
    import json
    import os
    
    if not pdf_json_str:
        return {"generated": None, "uploaded": []}
    
    try:
        pdf_data = json.loads(pdf_json_str)
        result = {"generated": None, "uploaded": []}
        
        # 处理系统生成的PDF
        if pdf_data.get("generated"):
            file_info = get_file_by_id(pdf_data["generated"])
            if file_info:
                # 获取F_PATH并清理为相对路径
                path = file_info.get("F_PATH", "")
                
                # 如果是Windows绝对路径，提取相对路径部分
                if "\\" in path or ":" in path:
                    # 尝试提取 uploads/ 之后的路径
                    if "uploads" in path:
                        parts = path.split("uploads")
                        if len(parts) > 1:
                            path = parts[-1].lstrip("\\/")
                    else:
                        # 如果没有uploads，使用文件名
                        path = os.path.basename(path)
                
                # 移除开头的斜杠和 uploads/ 前缀
                path = path.lstrip("/\\")
                if path.startswith("uploads/") or path.startswith("uploads\\"):
                    path = path[8:]
                
                # 统一使用正斜杠
                path = path.replace("\\", "/")
                
                result["generated"] = {
                    "id": file_info.get("F_ID"),
                    "name": file_info.get("F_ORIGINAL_NAME") or "",
                    "path": path  # 相对路径，如 "drug_test_report/xxx.pdf"
                }
        
        # 处理用户上传的PDF
        for file_id in pdf_data.get("uploaded", []):
            file_info = get_file_by_id(file_id)
            if file_info:
                # 获取F_PATH并清理为相对路径
                path = file_info.get("F_PATH", "")
                
                # 如果是Windows绝对路径，提取相对路径部分
                if "\\" in path or ":" in path:
                    # 尝试提取 uploads/ 之后的路径
                    if "uploads" in path:
                        parts = path.split("uploads")
                        if len(parts) > 1:
                            path = parts[-1].lstrip("\\/")
                    else:
                        # 如果没有uploads，使用文件名
                        path = os.path.basename(path)
                
                # 移除开头的斜杠和 uploads/ 前缀
                path = path.lstrip("/\\")
                if path.startswith("uploads/") or path.startswith("uploads\\"):
                    path = path[8:]
                
                # 统一使用正斜杠
                path = path.replace("\\", "/")
                
                result["uploaded"].append({
                    "id": file_info.get("F_ID"),
                    "name": file_info.get("F_ORIGINAL_NAME") or "",
                    "path": path
                })
        
        return result
    except:
        return {"generated": None, "uploaded": []}


def list_test_plans(query: Dict[str, Any], user: Dict[str, Any] | None = None,
                   scope: str | None = None) -> Tuple[List[Dict[str, Any]], int]:
    """查询实验计划列表"""
    adapter = get_adapter()
    
    skip = query.get("skip", 0)
    limit = query.get("limit", 20)
    
    conditions = []
    params = []
    
    # 实验计划相关过滤
    if query.get("F_GDID"):
        conditions.append("main.F_GDID = ?")
        params.append(query.get("F_GDID"))
    
    if query.get("F_SYBH"):
        conditions.append("main.F_SYBH LIKE ?")
        params.append(f"%{query.get('F_SYBH')}%")
    
    if query.get("F_SYMC"):
        conditions.append("main.F_SYMC LIKE ?")
        params.append(f"%{query.get('F_SYMC')}%")
    
    if query.get("F_SYR"):
        conditions.append("main.F_SYR LIKE ?")
        params.append(f"%{query.get('F_SYR')}%")
    
    if query.get("F_ZT"):
        conditions.append("main.F_ZT = ?")
        params.append(query.get("F_ZT"))
    
    # 工单相关过滤
    if query.get("F_ZZID"):
        conditions.append("gd.F_ZZID LIKE ?")
        params.append(f"%{query.get('F_ZZID')}%")
    
    if query.get("F_GDBH"):
        conditions.append("gd.F_GDBH LIKE ?")
        params.append(f"%{query.get('F_GDBH')}%")
    
    if query.get("F_DCID"):
        conditions.append("gd.F_DCID LIKE ?")
        params.append(f"%{query.get('F_DCID')}%")
    
    if query.get("F_ZZBW"):
        conditions.append("gd.F_ZZBW LIKE ?")
        params.append(f"%{query.get('F_ZZBW')}%")
    
    if query.get("F_SQR"):
        conditions.append("gd.F_SQR LIKE ?")
        params.append(f"%{query.get('F_SQR')}%")

    from app.core.data_scope import apply_owner_filter
    apply_owner_filter(conditions, params, scope=scope, user=user, column="main.F_SYR", match="name")

    where_clause = " AND ".join(conditions) if conditions else "1=1"
    
    # 查询总数
    count_sql = f"""
        SELECT COUNT(*) 
        FROM T_SYJH main
        LEFT JOIN T_PYGD gd ON main.F_GDID = gd.F_ID
        WHERE {where_clause}
    """
    
    # 查询列表（JOIN 工单表获取工单信息）
    list_sql = f"""
        SELECT 
            main.F_ID, main.F_GDID, main.F_SYBH, main.F_SYMC, main.F_SYKSRQ, main.F_SYR,
            main.F_KBLX, main.F_KBBH, main.F_ZT, main.F_CJSJ, main.F_BG_PDF,
            (SELECT COUNT(*) FROM T_SYJH_ZB WHERE F_SYJH_ID = main.F_ID) AS F_GROUP_COUNT,
            (SELECT COUNT(*) FROM T_SYJH_KW WHERE F_SYJH_ID = main.F_ID) AS F_WELL_COUNT,
            gd.F_ZZID, gd.F_DCID, gd.F_GDBH, gd.F_ZZBW, gd.F_YBLY, gd.F_SQR, gd.F_PYSJ
        FROM T_SYJH main
        LEFT JOIN T_PYGD gd ON main.F_GDID = gd.F_ID
        WHERE {where_clause}
        ORDER BY main.F_CJSJ DESC
    """
    
    with adapter.connect() as conn:
        cur = conn.cursor()
        
        # 获取总数
        cur.execute(count_sql, tuple(params))
        total = cur.fetchone()[0]
        
        # 分页查询
        paginated_sql, extra = adapter.paginate(list_sql, skip=skip, limit=limit)
        
        cur.execute(paginated_sql, tuple(params + extra))
        rows = cur.fetchall()
        
        items = []
        for row in rows:
            item = {
                "F_ID": row[0],
                "F_GDID": row[1],
                "F_SYBH": row[2],
                "F_SYMC": row[3],
                "F_SYKSRQ": _parse_datetime(row[4]),
                "F_SYR": row[5],
                "F_KBLX": row[6],
                "F_KBBH": row[7],
                "F_ZT": row[8],
                "F_CJSJ": _parse_datetime(row[9]),
                "F_GROUP_COUNT": row[11] or 0,
                "F_WELL_COUNT": row[12] or 0,
                # 工单信息
                "F_ZZID": row[13],
                "F_DCID": row[14],
                "F_GDBH": row[15],
                "F_ZZBW": row[16],
                "F_YBLY": row[17],
                "F_SQR": row[18],
                "F_PYSJ": _parse_datetime(row[19])
            }
            
            # 处理F_BG_PDF字段（映射为静态地址，不带uploads前缀）
            f_bg_pdf_json = row[10]  # F_BG_PDF字段
            item["F_BG_PDF"] = _format_pdf_urls(f_bg_pdf_json)
            
            items.append(item)
        
        return items, total


def update_test_plan(data: Dict[str, Any], operator: Optional[str] = None) -> Dict[str, Any]:
    """更新实验计划"""
    import logging
    logger = logging.getLogger(__name__)
    
    # 清理数据中的特殊Unicode字符
    data = _clean_data_for_db(data)
    from app.crud.field_config import assert_page, scrub_hidden
    scrub_hidden("drug_plan", data)
    assert_page("drug_plan", data, updating=True)
    
    adapter = get_adapter()
    plan_id = data.get("F_ID")
    
    if not plan_id:
        raise ValueError("缺少实验计划ID")
    
    # 获取修改前的完整详情数据（用于质控记录）
    old_detail_data = get_test_plan_detail(plan_id)
    
    with adapter.connect() as conn:
        cur = conn.cursor()
        
        # 检查状态
        cur.execute("SELECT F_ZT FROM T_SYJH WHERE F_ID = ?", (plan_id,))
        row = cur.fetchone()
        if not row:
            raise ValueError("实验计划不存在")
        
        current_status = row[0]
        if current_status == "已完成":
            raise ValueError("已完成的实验计划不允许修改")
        
        # 1. 更新主表
        update_fields = {}
        
        # 根据状态确定允许修改的字段
        if current_status == "已创建":
            # 已创建状态：可以修改所有字段
            allowed_fields = ["F_SYMC", "F_SYKSRQ", "F_SYR", "F_SYJHYMD", "F_XBSL", "F_JZJND",
                              "F_JZJTJ", "F_FJ", "F_KBLX", "F_KBBH", "F_ZT", "F_BZ"]
        elif current_status == "进行中":
            # 进行中状态：只能修改记录性字段
            allowed_fields = ["F_SYMC", "F_SYR", "F_SYJHYMD", "F_FJ", "F_ZT", "F_BZ"]
        else:
            allowed_fields = []
        
        for field in allowed_fields:
            if field not in data or data.get(field) is None:
                continue
            value = data.get(field)
            if field == "F_FJ":
                if isinstance(value, bool) or not isinstance(value, (int, str)) or str(value).strip() == "":
                    continue
            elif field == "F_XBSL":
                value = _coerce_plan_text(value, cell_count=True)
            else:
                value = _coerce_plan_text(value)
            update_fields[field] = value

        # 用户更新计划 → 标记已保存
        update_fields["F_YHBC"] = 1
        if update_fields:
            try:
                set_clause = ", ".join([f"{k} = ?" for k in update_fields.keys()])
                params = list(update_fields.values()) + [plan_id]
                cur.execute(f"UPDATE T_SYJH SET {set_clause} WHERE F_ID = ?", tuple(params))
            except Exception:
                update_fields.pop("F_YHBC", None)
                if update_fields:
                    set_clause = ", ".join([f"{k} = ?" for k in update_fields.keys()])
                    params = list(update_fields.values()) + [plan_id]
                    cur.execute(f"UPDATE T_SYJH SET {set_clause} WHERE F_ID = ?", tuple(params))
        
        auto_outbound_codes = []
        
        # 检测是否修改了影响孔位数量的关键参数
        key_params_changed = False
        zbxx_changed = data.get("ZBXX") is not None or bool(data.get("ZBXX_DEL_IDS"))
        if current_status == "已创建":
            if zbxx_changed:
                key_params_changed = True
                if data.get("KWXX") is None:
                    raise ValueError(
                        "修改了组别配置（含复孔数/浓度），必须同时更新孔位信息（KWXX字段）。"
                        "请提供完整的孔位配置数据。"
                    )
        
        # 2. 增量更新组别和用药信息（有F_ID=更新，无F_ID=新增，ZBXX_DEL_IDS=删除）
        if zbxx_changed:
            # 进行中的实验不允许修改组别和用药信息
            if current_status == "进行中":
                raise ValueError("进行中的实验不允许修改组别和用药信息")

            groups = data.get("ZBXX") or []

            _validate_unique_drug_usage_in_groups(groups)

            replace_group_ids = [g.get("F_ID") for g in groups if g.get("F_ID")]
            del_ids = data.get("ZBXX_DEL_IDS") or []
            releasable_map = _sum_plan_releasable_stock(cur, plan_id, replace_group_ids, del_ids)

            # 预先校验所有组别字段 & 用药库存（在任何写操作之前统一验证）
            for idx, g in enumerate(groups):
                if g.get("F_FKSSZ") is None:
                    raise ValueError(f"第 {idx + 1} 个组别缺少 F_FKSSZ")
                try:
                    fkssz_int = int(g.get("F_FKSSZ"))
                except Exception:
                    raise ValueError(f"第 {idx + 1} 个组别的 F_FKSSZ 非法，需为整数")
                if fkssz_int <= 0:
                    raise ValueError(f"第 {idx + 1} 个组别的 F_FKSSZ 必须大于0")
                nd_list = g.get("F_ND") or []
                if not isinstance(nd_list, list) or len(nd_list) == 0:
                    raise ValueError(f"第 {idx + 1} 个组别的 F_ND 不能为空")
                for drug in g.get("F_YY", []):
                    drug_id = drug.get("F_SJID")
                    batch = drug.get("F_PCH")
                    amount = drug.get("F_YL")
                    batch_key = str(batch or "").strip()
                    extra = releasable_map.get((int(drug_id), batch_key), 0.0) if drug_id else 0.0
                    if not _validate_drug_stock(
                        adapter, drug_id, batch, amount, extra_available=extra
                    ):
                        raise ValueError(f"药物ID {drug_id} 批次 {batch} 库存不足或不存在")

            # ── Step 1：删除指定组别 ──────────────────────────────────────────
            for zb_id in del_ids:
                # 安全校验：该组别必须属于当前实验计划
                cur.execute(
                    "SELECT F_ID FROM T_SYJH_ZB WHERE F_ID = ? AND F_SYJH_ID = ?",
                    (zb_id, plan_id)
                )
                if not cur.fetchone():
                    raise ValueError(f"组别 ID={zb_id} 不存在或不属于该实验计划，无法删除")
                # 删除该组别的关联出库单
                if current_status == "已创建":
                    cur.execute(
                        "SELECT F_CKDH FROM T_SYJH_YY WHERE F_ZB_ID = ? AND F_CKDH IS NOT NULL",
                        (zb_id,)
                    )
                    for (ckdh,) in cur.fetchall():
                        cur.execute("DELETE FROM T_YW_CKD WHERE F_CKDH = ?", (ckdh,))
                # 删除组别（T_SYJH_YY 通过外键级联删除，或手动先删）
                cur.execute("DELETE FROM T_SYJH_YY WHERE F_ZB_ID = ?", (zb_id,))
                cur.execute("DELETE FROM T_SYJH_ZB WHERE F_ID = ?", (zb_id,))

            # ── Step 2：处理 ZBXX 中的新增 / 更新 ─────────────────────────────
            # 自动补全缺省 F_SORT：仅对无 F_ID 的新增条目按数组顺序赋值
            new_sort_counter = 1
            for group in groups:
                if group.get("F_ID") is None and group.get("F_SORT") is None:
                    group["F_SORT"] = new_sort_counter
                new_sort_counter += 1

            remark = f"用于实验计划：{data.get('F_SYMC') or plan_id}"

            for group in groups:
                group_id = group.get("F_ID")

                if group_id:
                    # ── 更新已有组别（保留原 F_ID）──────────────────────────
                    cur.execute(
                        "SELECT F_ID FROM T_SYJH_ZB WHERE F_ID = ? AND F_SYJH_ID = ?",
                        (group_id, plan_id)
                    )
                    if not cur.fetchone():
                        raise ValueError(f"组别 ID={group_id} 不存在或不属于该实验计划")
                    _upd_cols = ["F_ZBMC=?", "F_ZBLX=?", "F_FKSSZ=?", "F_ND=?"]
                    _upd_vals = [
                        group.get("F_ZBMC"),
                        group.get("F_ZBLX"),
                        group.get("F_FKSSZ"),
                        json.dumps(group.get("F_ND", []), ensure_ascii=False),
                    ]
                    if group.get("F_SORT") is not None:
                        _upd_cols.insert(2, "F_SORT=?")
                        _upd_vals.insert(2, group.get("F_SORT"))
                    _upd_vals.append(group_id)
                    cur.execute(
                        f"UPDATE T_SYJH_ZB SET {', '.join(_upd_cols)} WHERE F_ID=?",
                        tuple(_upd_vals)
                    )
                    # 用药信息对该组整体替换（用药变动必须重建出库单）
                    if current_status == "已创建":
                        cur.execute(
                            "SELECT F_CKDH FROM T_SYJH_YY WHERE F_ZB_ID = ? AND F_CKDH IS NOT NULL",
                            (group_id,)
                        )
                        for (ckdh,) in cur.fetchall():
                            cur.execute("DELETE FROM T_YW_CKD WHERE F_CKDH = ?", (ckdh,))
                    cur.execute("DELETE FROM T_SYJH_YY WHERE F_ZB_ID = ?", (group_id,))
                    for drug in group.get("F_YY", []):
                        drug_id = drug.get("F_SJID")
                        batch = drug.get("F_PCH")
                        amount = drug.get("F_YL")
                        xsfa = drug.get("F_XSFA")
                        try:
                            ckdh = _create_auto_outbound(adapter, conn, drug_id, batch, amount, remark)
                            auto_outbound_codes.append(ckdh)
                        except Exception as e:
                            conn.rollback()
                            raise ValueError(f"创建出库单失败: {e}")
                        cur.execute("""
                            INSERT INTO T_SYJH_YY (F_ZB_ID, F_SJID, F_PCH, F_YL, F_XSFA, F_CKDH)
                            VALUES (?, ?, ?, ?, ?, ?)
                        """, (group_id, drug_id, batch, amount, xsfa, ckdh))

                else:
                    # ── 新增组别（系统分配新 F_ID）──────────────────────────
                    cur.execute("""
                        INSERT INTO T_SYJH_ZB (F_SYJH_ID, F_ZBMC, F_ZBLX, F_SORT, F_FKSSZ, F_ND)
                        VALUES (?, ?, ?, ?, ?, ?)
                    """, (
                        plan_id,
                        group.get("F_ZBMC"),
                        group.get("F_ZBLX"),
                        group.get("F_SORT"),
                        group.get("F_FKSSZ"),
                        json.dumps(group.get("F_ND", []), ensure_ascii=False)
                    ))
                    cur.execute(
                        "SELECT MAX(F_ID) FROM T_SYJH_ZB WHERE F_SYJH_ID = ?", (plan_id,)
                    )
                    group_id = cur.fetchone()[0]
                    for drug in group.get("F_YY", []):
                        drug_id = drug.get("F_SJID")
                        batch = drug.get("F_PCH")
                        amount = drug.get("F_YL")
                        xsfa = drug.get("F_XSFA")
                        try:
                            ckdh = _create_auto_outbound(adapter, conn, drug_id, batch, amount, remark)
                            auto_outbound_codes.append(ckdh)
                        except Exception as e:
                            conn.rollback()
                            raise ValueError(f"创建出库单失败: {e}")
                        cur.execute("""
                            INSERT INTO T_SYJH_YY (F_ZB_ID, F_SJID, F_PCH, F_YL, F_XSFA, F_CKDH)
                            VALUES (?, ?, ?, ?, ?, ?)
                        """, (group_id, drug_id, batch, amount, xsfa, ckdh))
        
        # 3. 如果更新了孔位信息
        if data.get("KWXX") is not None:
            # 进行中的实验不允许修改孔位信息
            if current_status == "进行中":
                raise ValueError("进行中的实验不允许修改孔位信息")
            
            # ========== 校验孔位数量 & 组别浓度匹配（组别级配置） ==========
            # 取得最新的组别配置（可能已重建，也可能沿用旧的）
            cur.execute("""
                SELECT F_ZBMC, F_FKSSZ, F_ND
                FROM T_SYJH_ZB
                WHERE F_SYJH_ID = ?
            """, (plan_id,))
            group_rows = cur.fetchall()
            group_map: Dict[str, Dict[str, Any]] = {}
            expected_well_count = 0
            for g in group_rows:
                zbmc = g[0]
                fkssz = g[1] or 0
                nd_list = _parse_json_field(g[2]) or []
                nd_list = [str(x) for x in nd_list]
                expected_well_count += int(fkssz) * len(nd_list)
                group_map[zbmc] = {"fkssz": int(fkssz), "nds": set(nd_list)}
            
            wells = normalize_kwxx_positions(data.get("KWXX", []) or [])
            data["KWXX"] = wells
            well_count = len(wells)
            if well_count != expected_well_count:
                conn.rollback()
                raise ValueError(
                    f"孔位数量不匹配！期望孔位数 = Σ(组别复孔数 × 该组浓度数量) = {expected_well_count}，"
                    f"但实际传入了 {well_count} 个孔位信息。请检查孔位配置是否完整。"
                )

            for idx, well in enumerate(wells):
                zbmc = well.get("F_ZBMC")
                nd_val = str(well.get("F_ND"))
                if zbmc not in group_map:
                    conn.rollback()
                    raise ValueError(f"孔位第 {idx + 1} 条引用了不存在的组别: {zbmc}")
                if nd_val not in group_map[zbmc]["nds"]:
                    conn.rollback()
                    raise ValueError(f"孔位第 {idx + 1} 条的浓度 {nd_val} 不属于组别 {zbmc} 的浓度列表")
            
            # 删除旧的孔位信息
            cur.execute("DELETE FROM T_SYJH_KW WHERE F_SYJH_ID = ?", (plan_id,))
            
            # 插入新的孔位信息
            wells = data.get("KWXX", [])
            for well in wells:
                cur.execute("""
                    INSERT INTO T_SYJH_KW (F_SYJH_ID, F_WZ, F_ZBMC, F_ND, F_JYTJ, F_XSFA, F_YS)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                """, (
                    plan_id,
                    well.get("F_WZ"),
                    well.get("F_ZBMC"),
                    well.get("F_ND"),
                    well.get("F_JYTJ"),
                    well.get("F_XSFA"),
                    well.get("F_YS")
                ))
        
        conn.commit()
    
    # ========== 创建质控记录 ==========
    qc_audit_id = None
    try:
        from app.crud.qc_audit import create_qc_audit_record, calculate_changes, get_sample_basic_info
        import copy
        
        # 获取修改后的完整详情数据
        new_detail_data = get_test_plan_detail(plan_id)
        
        # 过滤掉未实际修改的字段（避免记录由于删除重建导致的ID变化）
        old_data_for_compare = copy.deepcopy(old_detail_data)
        new_data_for_compare = copy.deepcopy(new_detail_data)
        
        # 如果用户没有操作组别信息，则不对比（避免噪音）
        if not zbxx_changed:
            old_data_for_compare.pop("ZBXX", None)
            new_data_for_compare.pop("ZBXX", None)
        else:
            # 用户操作了组别信息（新增/更新/删除）：
            # 增量更新后已有组别的 F_ID 保持稳定，无需全量剥离；
            # 仅对 F_YY 中的 F_CKDH（出库单号，系统生成）做剥离以避免误报。
            def _remove_auto_fields_from_zbxx(zbxx_list):
                if not zbxx_list:
                    return zbxx_list
                cleaned = []
                for group in zbxx_list:
                    g = copy.deepcopy(group)
                    if "F_YY" in g and isinstance(g["F_YY"], list):
                        g["F_YY"] = [
                            {k: v for k, v in drug.items() if k not in ["F_ID", "F_CKDH"]}
                            for drug in g["F_YY"]
                        ]
                    cleaned.append(g)
                return cleaned

            if "ZBXX" in old_data_for_compare:
                old_data_for_compare["ZBXX"] = _remove_auto_fields_from_zbxx(old_data_for_compare["ZBXX"])
            if "ZBXX" in new_data_for_compare:
                new_data_for_compare["ZBXX"] = _remove_auto_fields_from_zbxx(new_data_for_compare["ZBXX"])
        
        # 如果用户没有传入 KWXX，则不对比孔位信息（避免记录ID变化）
        if data.get("KWXX") is None:
            old_data_for_compare.pop("KWXX", None)
            new_data_for_compare.pop("KWXX", None)
        else:
            # 用户传入了 KWXX，移除自动生成的 F_ID 字段后再对比
            def _remove_id_from_kwxx(kwxx_list):
                if not kwxx_list:
                    return kwxx_list
                return [{k: v for k, v in well.items() if k != "F_ID"} for well in kwxx_list]
            
            if "KWXX" in old_data_for_compare:
                old_data_for_compare["KWXX"] = _remove_id_from_kwxx(old_data_for_compare["KWXX"])
            if "KWXX" in new_data_for_compare:
                new_data_for_compare["KWXX"] = _remove_id_from_kwxx(new_data_for_compare["KWXX"])
        
        # 计算变更内容
        changes = calculate_changes(old_data_for_compare, new_data_for_compare)
        
        # ✅ 将ZBXX/KWXX变更项的新旧值替换回含F_ID的完整原始数据（JSON序列化）
        # （比较时为避免删除重建导致ID误报而剥除了F_ID，但记录时应保留F_ID以便溯源）
        if isinstance(changes.get("变更字段"), list):
            for item in changes["变更字段"]:
                field_name = item.get("field")
                if field_name == "ZBXX":
                    item["old_value"] = json.dumps(old_detail_data.get("ZBXX") or [], ensure_ascii=False, default=str)
                    item["new_value"] = json.dumps(new_detail_data.get("ZBXX") or [], ensure_ascii=False, default=str)
                elif field_name == "KWXX":
                    item["old_value"] = json.dumps(old_detail_data.get("KWXX") or [], ensure_ascii=False, default=str)
                    item["new_value"] = json.dumps(new_detail_data.get("KWXX") or [], ensure_ascii=False, default=str)
        
        # 获取工单和样本信息
        gdxx = new_detail_data.get("GDXX") or {}
        zzid = gdxx.get("F_ZZID")
        gdid = new_detail_data.get("F_GDID")
        
        if zzid and gdid:
            # 获取样本基本信息
            sample_info = get_sample_basic_info(zzid)
            
            # 创建质控记录
            qc_audit_id = create_qc_audit_record(
                F_ZKJD="药物实验计划",
                F_BGLX="修改",
                F_ZZID=zzid,
                F_CHANGES=changes,
                F_JLR=operator or "系统",
                F_GDID=gdid,
                F_XM=sample_info.get("F_XM"),
                F_BRBH=sample_info.get("F_BRBH"),
                F_YBLY=gdxx.get("F_YBLY"),  # ✅ 从工单信息中获取样本来源
                F_GDBH=gdxx.get("F_GDBH"),
                F_DCID=gdxx.get("F_DCID"),
                F_DC=gdxx.get("F_DC"),
                F_PYSJ=gdxx.get("F_PYSJ"),
                F_SQR=gdxx.get("F_SQR"),
                F_OLD_DATA=new_detail_data
            )
            logger.info(f"[药物实验计划质控] 修改质控记录创建成功，计划ID: {plan_id}, 质控ID: {qc_audit_id}")
    except Exception as e:
        logger.error(f"[药物实验计划质控] 创建修改质控审核记录失败（计划ID: {plan_id}）: {e}", exc_info=True)
    
    result = {
        "F_ID": plan_id,
        "updated": True,
        "auto_outbound_count": len(auto_outbound_codes),
        "auto_outbound_codes": auto_outbound_codes
    }
    
    if qc_audit_id:
        result["qc_audit_id"] = qc_audit_id
    
    return result


def delete_test_plan(plan_id: int, operator: Optional[str] = None) -> bool:
    """
    删除实验计划
    
    业务规则：
    - 只能删除"已创建"状态的实验计划
    - 已录入实验记录的计划（"进行中"状态）不能删除
    - 删除时自动删除关联的出库单，药物/试剂库存会自动还库
    """
    import logging
    logger = logging.getLogger(__name__)
    
    adapter = get_adapter()
    
    # 获取删除前的完整详情数据（用于质控记录）
    old_detail_data = get_test_plan_detail(plan_id)
    
    with adapter.connect() as conn:
        cur = conn.cursor()
        
        # 1. 检查实验计划是否存在
        cur.execute("SELECT F_ZT FROM T_SYJH WHERE F_ID = ?", (plan_id,))
        row = cur.fetchone()
        if not row:
            raise ValueError("实验计划不存在")
        
        current_status = row[0]
        
        # 2. 检查是否已有实验记录
        cur.execute("SELECT COUNT(*) FROM T_SYJL WHERE F_SYJH_ID = ?", (plan_id,))
        record_count = cur.fetchone()[0]
        
        if record_count > 0:
            raise ValueError("该实验计划已有实验记录，无法删除。请先删除实验记录或联系管理员。")
        
        # 3. 检查状态（双重保险）
        if current_status != "已创建":
            raise ValueError(f"只能删除'已创建'状态的实验计划（当前状态：{current_status}）")
        
        # 4. 删除关联的出库单（自动还库）
        cur.execute("""
            SELECT yy.F_CKDH
            FROM T_SYJH_YY yy
            INNER JOIN T_SYJH_ZB zb ON yy.F_ZB_ID = zb.F_ID
            WHERE zb.F_SYJH_ID = ? AND yy.F_CKDH IS NOT NULL
        """, (plan_id,))
        
        ckdhs = [row[0] for row in cur.fetchall()]
        for ckdh in ckdhs:
            # 删除出库单会触发库存还原
            cur.execute("DELETE FROM T_YW_CKD WHERE F_CKDH = ?", (ckdh,))
        
        # 5. 删除实验计划（级联删除组别、用药、孔位）
        cur.execute("DELETE FROM T_SYJH WHERE F_ID = ?", (plan_id,))
        
        conn.commit()
        
        logger.info(f"实验计划删除成功：ID={plan_id}，已删除 {len(ckdhs)} 个出库单（库存已还原）")
    
    # ========== 创建质控记录 ==========
    try:
        from app.crud.qc_audit import create_qc_audit_record, get_sample_basic_info
        
        # 从删除前的数据中提取工单和样本信息
        gdxx = old_detail_data.get("GDXX") or {}
        zzid = gdxx.get("F_ZZID")
        gdid = old_detail_data.get("F_GDID")
        
        if zzid and gdid:
            # 获取样本基本信息
            sample_info = get_sample_basic_info(zzid)
            
            # 准备变更内容
            changes = {
                "变更字段": [
                    {"字段": "操作", "新值": "删除实验计划"},
                    {"字段": "实验名称", "旧值": old_detail_data.get("F_SYMC")},
                    {"字段": "实验编号", "旧值": old_detail_data.get("F_SYBH")},
                    {"字段": "已删除出库单数量", "新值": len(ckdhs)}
                ]
            }
            
            # 创建质控记录
            qc_audit_id = create_qc_audit_record(
                F_ZKJD="药物实验计划",
                F_BGLX="删除",
                F_ZZID=zzid,
                F_CHANGES=changes,
                F_JLR=operator or "系统",
                F_GDID=gdid,
                F_XM=sample_info.get("F_XM"),
                F_BRBH=sample_info.get("F_BRBH"),
                F_YBLY=gdxx.get("F_YBLY"),  # ✅ 从工单信息中获取样本来源
                F_GDBH=gdxx.get("F_GDBH"),
                F_DCID=gdxx.get("F_DCID"),
                F_DC=gdxx.get("F_DC"),
                F_PYSJ=gdxx.get("F_PYSJ"),
                F_SQR=gdxx.get("F_SQR"),
                F_OLD_DATA=old_detail_data
            )
            logger.info(f"[药物实验计划质控] 删除质控记录创建成功，计划ID: {plan_id}, 质控ID: {qc_audit_id}")
    except Exception as e:
        logger.error(f"[药物实验计划质控] 创建删除质控审核记录失败（计划ID: {plan_id}）: {e}", exc_info=True)
    
    return True


def update_test_plan_status(plan_id: int, status: str) -> bool:
    """更新实验计划状态"""
    adapter = get_adapter()
    
    valid_statuses = ["已创建", "进行中", "待审核", "已完成", "不合格"]
    if status not in valid_statuses:
        raise ValueError(f"无效的状态：{status}，有效值为：{', '.join(valid_statuses)}")
    
    with adapter.connect() as conn:
        cur = conn.cursor()
        
        cur.execute("SELECT F_ZT FROM T_SYJH WHERE F_ID = ?", (plan_id,))
        row = cur.fetchone()
        if not row:
            raise ValueError("实验计划不存在")
        
        cur.execute("UPDATE T_SYJH SET F_ZT = ? WHERE F_ID = ?", (status, plan_id))
        conn.commit()
        
        return True


def save_experiment_record(data: Dict[str, Any]) -> Dict[str, Any]:
    """
    保存实验记录（新增或更新）
    
    业务规则：
    1. 一个实验计划只能有一个实验结果记录
    2. 如果该实验计划已有记录 → 自动更新
    3. 如果该实验计划没有记录 → 自动新增
    4. 保存成功后自动将实验计划状态更新为"进行中"
    """
    adapter = get_adapter()
    plan_id = data.get("F_SYJH_ID")
    
    if not plan_id:
        raise ValueError("缺少实验计划ID")
    
    with adapter.connect() as conn:
        cur = conn.cursor()
        
        # 检查实验计划是否存在
        cur.execute("SELECT F_ID, F_ZT FROM T_SYJH WHERE F_ID = ?", (plan_id,))
        plan_row = cur.fetchone()
        if not plan_row:
            raise ValueError("实验计划不存在")
        
        # 检查是否已有实验记录
        cur.execute("SELECT F_ID FROM T_SYJL WHERE F_SYJH_ID = ?", (plan_id,))
        record_row = cur.fetchone()
        
        # 准备数据
        record_data = {
            "F_SYJH_ID": plan_id,
            "F_KBDS_ZBQ": data.get("KBDS"),  # 孔板读数-组别前
            "F_KBDS_ZBH": data.get("KBDS"),  # 孔板读数-组别后（暂时相同）
            "F_XTXBH": data.get("XTXBH"),   # 形态学变化
            "F_XBHL_PJ": data.get("XBHL_PJ"),  # 细胞活率-平均
            "F_XBHL": data.get("XBHL"),     # 细胞活率-孔级
            "F_XBHL_WC": data.get("XBHL_WC"),  # 细胞活率-标准误差
            "F_GMD": data.get("GMD"),       # 光敏度
            "F_IC50": data.get("IC50"),     # IC50
            "F_SYJL": data.get("SYJL"),     # 实验结论
        }
        
        if record_row:
            # 更新现有记录
            record_id = record_row[0]
            update_fields = {k: v for k, v in record_data.items() if k != "F_SYJH_ID" and v is not None}
            
            if update_fields:
                set_clause = ", ".join([f"{k} = ?" for k in update_fields.keys()])
                params = list(update_fields.values()) + [record_id]
                cur.execute(f"UPDATE T_SYJL SET {set_clause} WHERE F_ID = ?", tuple(params))
        else:
            # 新增记录
            fields = [k for k, v in record_data.items() if v is not None]
            placeholders = ", ".join(["?" for _ in fields])
            values = [record_data[k] for k in fields]
            
            cur.execute(
                f"INSERT INTO T_SYJL ({', '.join(fields)}) VALUES ({placeholders})",
                tuple(values)
            )
            
            cur.execute("SELECT MAX(F_ID) FROM T_SYJL WHERE F_SYJH_ID = ?", (plan_id,))
            record_id = cur.fetchone()[0]
        
        # 自动更新实验计划状态为"进行中"（如果当前状态是"已创建"）
        current_status = plan_row[1]
        if current_status == "已创建":
            cur.execute("UPDATE T_SYJH SET F_ZT = ? WHERE F_ID = ?", ("进行中", plan_id))
        
        conn.commit()
        
        return {
            "F_ID": record_id,
            "F_SYJH_ID": plan_id,
            "status_updated": current_status == "已创建",
            "current_status": "进行中" if current_status == "已创建" else current_status
        }


def mark_unqualified(plan_id: int, reason: str | None, creator_code: str = "") -> Optional[int]:
    """
    将实验计划标记为不合格（进入审批中状态，审核通过后才正式不合格）
    
    Args:
        plan_id: 实验计划ID
        reason: 不合格原因
        creator_code: 操作人姓名
    
    Returns:
        质控审核记录ID（如果创建成功）
    """
    import logging
    logger = logging.getLogger(__name__)
    
    adapter = get_adapter()
    
    with adapter.connect() as conn:
        cur = conn.cursor()
        
        cur.execute("SELECT F_ID, F_SYMC, F_ZT FROM T_SYJH WHERE F_ID = ?", (plan_id,))
        plan_row = cur.fetchone()
        
        if not plan_row:
            raise ValueError(f"实验计划不存在：ID={plan_id}")
        
        plan_name = plan_row[1] if len(plan_row) > 1 else None
        original_zt = plan_row[2] or "已创建"
        
        # 设置中间状态，等待审批
        cur.execute(
            "UPDATE T_SYJH SET F_BHGYY = ?, F_ZT = '不合格审批中' WHERE F_ID = ?",
            (reason or '', plan_id)
        )
        logger.info(f"[实验计划标为不合格] 已更新计划状态为'不合格审批中'，计划ID: {plan_id}")
        conn.commit()
    
    qc_audit_id = None
    if plan_id and creator_code:
        try:
            from app.crud.qc_audit import create_qc_audit_record
            
            changes = {
                "操作": "标为不合格",
                "原状态": original_zt,
                "关键信息": {
                    "实验计划ID": plan_id,
                    "实验名称": plan_name or "未知",
                    "不合格原因": reason or "未填写原因"
                }
            }
            
            qc_audit_id = create_qc_audit_record(
                F_ZKJD="药敏实验-标为不合格",
                F_BGLX="修改",
                F_CHANGES=changes,
                F_JLR=creator_code,
            )
            logger.info(f"[实验计划标为不合格] 创建质控审核记录成功，计划ID: {plan_id}, 质控ID: {qc_audit_id}")
        except Exception as e:
            logger.error(f"[实验计划标为不合格] 创建质控审核记录失败 (计划ID: {plan_id}): {e}", exc_info=True)
    
    return qc_audit_id

