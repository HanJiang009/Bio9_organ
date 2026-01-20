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


def _generate_test_plan_code(adapter) -> str:
    """
    生成实验编号
    格式：SYSY + YYYYMMDDHHMMSS + 毫秒(3位)
    例如：SYSY20251120153045123
    """
    return "SYSY" + datetime.now().strftime("%Y%m%d%H%M%S%f")[:-3]


def _validate_drug_stock(adapter, drug_id: int, batch: str, required_amount: str) -> bool:
    """
    验证药物库存是否充足
    返回：True表示库存充足，False表示库存不足
    """
    with adapter.connect() as conn:
        cur = conn.cursor()
        # 查询该批次的库存
        sql = """
            SELECT 
                rkd.F_RKSL - COALESCE(
                    (SELECT SUM(ckd.F_CKSL) 
                     FROM T_YW_CKD ckd 
                     WHERE ckd.F_RKDH = rkd.F_RKDH), 
                    0
                ) AS F_ZKSL
            FROM T_YW_RKD rkd
            WHERE rkd.F_YWID = ? AND rkd.F_PCH = ?
        """
        cur.execute(sql, (drug_id, batch))
        row = cur.fetchone()
        
        if not row or row[0] is None:
            return False
        
        stock = float(row[0])
        # 简单判断：库存大于0即可（实际用量单位可能需要转换）
        return stock > 0


def _create_auto_outbound(adapter, conn, drug_id: int, batch: str, amount: str, remark: str) -> Optional[str]:
    """
    自动创建出库单
    返回：出库单号
    """
    cur = conn.cursor()
    
    # 查询入库单号
    cur.execute("""
        SELECT F_RKDH, F_YXQ 
        FROM T_YW_RKD 
        WHERE F_YWID = ? AND F_PCH = ?
    """, (drug_id, batch))
    row = cur.fetchone()
    
    if not row:
        raise ValueError(f"未找到批次号 {batch} 的入库记录")
    
    rkdh = row[0]
    yxq = row[1]
    
    # 生成出库单号
    ckdh = _generate_outbound_code()
    
    # 创建出库单
    insert_sql = """
        INSERT INTO T_YW_CKD (F_YWID, F_PCH, F_CKSL, F_YXQ, F_CKRQ, F_CKR, F_BZ, F_CKDH, F_RKDH)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """
    
    cur.execute(insert_sql, (
        drug_id,
        batch,
        amount,
        yxq,
        datetime.now(),
        "系统自动",
        remark,
        ckdh,
        rkdh
    ))
    
    return ckdh


def create_test_plan(data: Dict[str, Any], operator: Optional[str] = None) -> Dict[str, Any]:
    """创建实验计划"""
    import logging
    logger = logging.getLogger(__name__)
    
    adapter = get_adapter()
    
    # 清理数据中的特殊字符（避免GBK编码错误）
    data = _clean_data_for_db(data)
    
    # ========== 业务逻辑校验 ==========
    # 组别级复孔数/浓度校验与孔位匹配
    groups = data.get("ZBXX", []) or []
    wells = data.get("KWXX", []) or []

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
    
    with adapter.connect() as conn:
        cur = conn.cursor()
        
        # 1. 插入主表
        main_fields = {
            "F_GDID": data.get("F_GDID"),
            "F_SYBH": sybh,
            "F_SYMC": data.get("F_SYMC"),
            "F_SYKSRQ": data.get("F_SYKSRQ"),
            "F_SYR": data.get("F_SYR"),
            "F_SYJHYMD": data.get("F_SYJHYMD"),
            "F_LQGMD": data.get("F_LQGMD"),
            "F_JZJND": data.get("F_JZJND"),
            "F_JZJTJ": data.get("F_JZJTJ"),
            "F_FJ": data.get("F_FJ"),
            "F_KBLX": data.get("F_KBLX"),
            "F_KBBH": data.get("F_KBBH"),
            "F_BZ": data.get("F_BZ"),
            "F_ZT": "已创建",
            "F_CJSJ": datetime.now()
        }
        
        cols = [k for k, v in main_fields.items() if v is not None]
        vals = [main_fields[k] for k in cols]
        placeholders = ", ".join(["?" for _ in cols])
        
        insert_sql = f"INSERT INTO T_SYJH ({', '.join(cols)}) VALUES ({placeholders})"
        cur.execute(insert_sql, tuple(vals))
        
        # 获取主表ID
        cur.execute("SELECT MAX(F_ID) FROM T_SYJH WHERE F_GDID = ?", (data.get("F_GDID"),))
        plan_id = cur.fetchone()[0]
        
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
                
                # 验证库存
                if not _validate_drug_stock(adapter, drug_id, batch, amount):
                    conn.rollback()
                    raise ValueError(f"药物ID {drug_id} 批次 {batch} 库存不足或不存在")
                
                # 自动创建出库单
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
            cur.execute("SELECT F_ZZID, F_DCID, F_DC, F_GDBH, F_SQR, F_PYSJ FROM T_PYGD WHERE F_ID = ?", (gdid,))
            gd_row = cur.fetchone()
        
        if gd_row:
            zzid = gd_row[0]
            dcid = gd_row[1]
            dc = gd_row[2]
            gdbh = gd_row[3]
            sqr = gd_row[4]
            pysj = gd_row[5]
            
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
                F_GDBH=gdbh,
                F_DCID=dcid,
                F_DC=dc,
                F_PYSJ=pysj,
                F_SQR=sqr,
                F_OLD_DATA=new_detail_data
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
            SELECT F_ID, F_GDID, F_SYBH, F_SYMC, F_SYKSRQ, F_SYR, F_SYJHYMD, F_LQGMD, F_JZJND, F_JZJTJ,
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
        for well_row in cur.fetchall():
            wells.append({
                "F_ID": well_row[0],
                "F_WZ": well_row[1],
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


def list_test_plans(query: Dict[str, Any]) -> Tuple[List[Dict[str, Any]], int]:
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
            main.F_KBLX, main.F_KBBH, main.F_ZT, main.F_CJSJ,
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
            items.append({
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
                "F_GROUP_COUNT": row[10] or 0,
                "F_WELL_COUNT": row[11] or 0,
                # 工单信息
                "F_ZZID": row[12],
                "F_DCID": row[13],
                "F_GDBH": row[14],
                "F_ZZBW": row[15],
                "F_YBLY": row[16],
                "F_SQR": row[17],
                "F_PYSJ": _parse_datetime(row[18])
            })
        
        return items, total


def update_test_plan(data: Dict[str, Any], operator: Optional[str] = None) -> Dict[str, Any]:
    """更新实验计划"""
    import logging
    logger = logging.getLogger(__name__)
    
    # 清理数据中的特殊Unicode字符
    data = _clean_data_for_db(data)
    
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
            allowed_fields = ["F_SYMC", "F_SYKSRQ", "F_SYR", "F_SYJHYMD", "F_LQGMD", "F_JZJND", 
                              "F_JZJTJ", "F_FJ", "F_KBLX", "F_KBBH", "F_ZT", "F_BZ"]
        elif current_status == "进行中":
            # 进行中状态：只能修改记录性字段
            allowed_fields = ["F_SYMC", "F_SYR", "F_SYJHYMD", "F_FJ", "F_ZT", "F_BZ"]
        else:
            allowed_fields = []
        
        for field in allowed_fields:
            if data.get(field) is not None:
                update_fields[field] = data.get(field)
        
        if update_fields:
            set_clause = ", ".join([f"{k} = ?" for k in update_fields.keys()])
            params = list(update_fields.values()) + [plan_id]
            cur.execute(f"UPDATE T_SYJH SET {set_clause} WHERE F_ID = ?", tuple(params))
        
        auto_outbound_codes = []
        
        # 检测是否修改了影响孔位数量的关键参数
        key_params_changed = False
        if current_status == "已创建":
            if data.get("ZBXX") is not None:
                key_params_changed = True
                if data.get("KWXX") is None:
                    raise ValueError(
                        "修改了组别配置（含复孔数/浓度），必须同时更新孔位信息（KWXX字段）。"
                        "请提供完整的孔位配置数据。"
                    )
        
        # 2. 如果更新了组别和用药信息
        if data.get("ZBXX") is not None:
            # 进行中的实验不允许修改组别和用药信息
            if current_status == "进行中":
                raise ValueError("进行中的实验不允许修改组别和用药信息")
            
            # 先查询旧的出库单号（如果状态是已创建）
            old_ckdhs = []
            if current_status == "已创建":
                cur.execute("""
                    SELECT yy.F_CKDH
                    FROM T_SYJH_YY yy
                    INNER JOIN T_SYJH_ZB zb ON yy.F_ZB_ID = zb.F_ID
                    WHERE zb.F_SYJH_ID = ? AND yy.F_CKDH IS NOT NULL
                """, (plan_id,))
                old_ckdhs = [row[0] for row in cur.fetchall()]
            
            # 先验证新的用药信息库存（在删除旧出库单之前）
            groups = data.get("ZBXX", [])
            # 校验组别级复孔/浓度
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
            
            # 预先验证所有用药的库存
            for group in groups:
                drugs = group.get("F_YY", [])
                for drug in drugs:
                    drug_id = drug.get("F_SJID")
                    batch = drug.get("F_PCH")
                    amount = drug.get("F_YL")
                    if not _validate_drug_stock(adapter, drug_id, batch, amount):
                        raise ValueError(f"药物ID {drug_id} 批次 {batch} 库存不足或不存在")
            
            # 删除旧的出库单（如果状态是已创建，且验证通过）
            if current_status == "已创建" and old_ckdhs:
                for ckdh in old_ckdhs:
                    cur.execute("DELETE FROM T_YW_CKD WHERE F_CKDH = ?", (ckdh,))
                    # 检查删除是否成功
                    if cur.rowcount == 0:
                        # 出库单可能已被删除或不存在，记录但不中断流程
                        pass
            
            # 删除旧的组别和用药信息（级联删除）
            cur.execute("DELETE FROM T_SYJH_ZB WHERE F_SYJH_ID = ?", (plan_id,))
            
            # 自动处理F_SORT：如果没有传F_SORT，则按数组顺序自动生成1,2,3...
            for idx, group in enumerate(groups):
                if group.get("F_SORT") is None:
                    group["F_SORT"] = idx + 1
            
            # 插入新的组别和用药信息
            for group in groups:
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
                
                cur.execute("SELECT MAX(F_ID) FROM T_SYJH_ZB WHERE F_SYJH_ID = ?", (plan_id,))
                group_id = cur.fetchone()[0]
                
                drugs = group.get("F_YY", [])
                for drug in drugs:
                    drug_id = drug.get("F_SJID")
                    batch = drug.get("F_PCH")
                    amount = drug.get("F_YL")
                    xsfa = drug.get("F_XSFA")
                    
                    # 创建新的出库单（库存已在前面验证过）
                    try:
                        remark = f"用于实验计划：{data.get('F_SYMC') or plan_id}"
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
            
            wells = data.get("KWXX", []) or []
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
        
        # 如果用户没有传入 ZBXX，则不对比组别信息（避免记录ID变化）
        if data.get("ZBXX") is None:
            old_data_for_compare.pop("ZBXX", None)
            new_data_for_compare.pop("ZBXX", None)
        else:
            # 用户传入了 ZBXX，移除自动生成的字段（F_ID、F_CKDH）后再对比
            def _remove_auto_fields_from_zbxx(zbxx_list):
                if not zbxx_list:
                    return zbxx_list
                cleaned = []
                for group in zbxx_list:
                    g = copy.deepcopy(group)
                    g.pop("F_ID", None)
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
    
    valid_statuses = ["已创建", "进行中", "已完成"]
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
    将实验计划标记为不合格
    
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
    
    # 1. 更新实验计划的不合格标识
    with adapter.connect() as conn:
        cur = conn.cursor()
        
        # 检查实验计划是否存在
        cur.execute("SELECT F_ID, F_SYMC FROM T_SYJH WHERE F_ID = ?", (plan_id,))
        plan_row = cur.fetchone()
        
        if not plan_row:
            raise ValueError(f"实验计划不存在：ID={plan_id}")
        
        plan_name = plan_row[1] if len(plan_row) > 1 else None
        
        # 更新不合格标识和原因
        cur.execute(
            "UPDATE T_SYJH SET F_BHG = 'true', F_BHGYY = ? WHERE F_ID = ?",
            (reason or '', plan_id)
        )
        conn.commit()
    
    # 2. 创建质控审核记录（在事务外执行）
    qc_audit_id = None
    if plan_id and creator_code:
        try:
            from app.crud.qc_audit import create_qc_audit_record
            
            # 标为不合格的变更信息
            changes = {
                "操作": "标为不合格",
                "关键信息": {
                    "实验计划ID": plan_id,
                    "实验名称": plan_name or "未知",
                    "不合格原因": reason or "未填写原因"
                }
            }
            
            # 创建质控审核记录
            qc_audit_id = create_qc_audit_record(
                F_ZKJD="药敏实验-标为不合格",
                F_BGLX="修改",
                F_CHANGES=changes,
                F_JLR=creator_code,
                F_SYJH_ID=plan_id
            )
            logger.info(f"[实验计划标为不合格] 创建质控审核记录成功，计划ID: {plan_id}, 质控ID: {qc_audit_id}")
        except Exception as e:
            logger.error(f"[实验计划标为不合格] 创建质控审核记录失败 (计划ID: {plan_id}): {e}", exc_info=True)
    
    return qc_audit_id

