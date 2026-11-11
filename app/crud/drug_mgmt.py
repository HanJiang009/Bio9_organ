"""
药物管理模块 CRUD
"""
from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Dict, List, Optional

from app.db.factory import get_adapter


def check_drug_exists(ywmc: str) -> Dict[str, Any]:
    """验证药物名称是否已存在
    
    Args:
        ywmc: 药物名称
        
    Returns:
        验证结果字典，包含exists、message、drug_id、drug_code字段
    """
    adapter = get_adapter()
    
    with adapter.connect() as conn:
        cur = conn.cursor()
        
        ywmc = (ywmc or "").strip()
        # 查询同名药物
        cur.execute("""
            SELECT F_ID, F_YWBH, F_YWMC 
            FROM T_YWGL 
            WHERE F_YWMC = ?
        """, (ywmc,))
        
        row = cur.fetchone()
        
        if row:
            # 药物已存在
            return {
                "exists": True,
                "message": "已存在该药物，请直接入库",
                "drug_id": row[0],
                "drug_code": row[1]
            }
        else:
            # 药物不存在，可以登记
            return {
                "exists": False,
                "message": "药物可登记",
                "drug_id": None,
                "drug_code": None
            }


def _generate_drug_code(cur) -> str:
    """生成药物编号（YW001、YW002...），按数字部分取最大，避免字符串 MAX 错序。"""
    cur.execute("SELECT F_YWBH FROM T_YWGL WHERE F_YWBH LIKE 'YW%'")
    max_num = 0
    for row in cur.fetchall() or []:
        code = row[0] if row else None
        if not code:
            continue
        try:
            max_num = max(max_num, int(str(code)[2:]))
        except (TypeError, ValueError):
            continue
    return f"YW{max_num + 1:03d}"


def _require_new_drug_name(cur, ywmc: str) -> str:
    name = (ywmc or "").strip()
    if not name:
        raise ValueError("药物名称不能为空")
    cur.execute(
        "SELECT F_ID, F_YWBH FROM T_YWGL WHERE F_YWMC = ?",
        (name,),
    )
    row = cur.fetchone()
    if row:
        raise ValueError("已存在该药物，请直接入库")
    return name


def create_drug(data: Dict[str, Any]) -> Dict[str, Any]:
    """创建药物。同名拒绝；事务内锁表尾行，避免连点写出多条。"""
    adapter = get_adapter()
    now = datetime.now()
    
    with adapter.connect() as conn:
        cur = conn.cursor()

        # 串行化创建：后到的请求等锁，再做同名校验，避免慢网连点各写一条
        try:
            cur.execute("SELECT F_ID FROM T_YWGL ORDER BY F_ID DESC LIMIT 1 FOR UPDATE")
        except Exception:
            cur.execute("SELECT F_ID FROM T_YWGL ORDER BY F_ID DESC")

        ywmc = _require_new_drug_name(cur, data.get("F_YWMC"))
        data["F_YWMC"] = ywmc

        # 自动生成药物编号
        ywbh = _generate_drug_code(cur)
        
        # 处理试剂配方（将数组序列化为JSON字符串）
        sjpf = data.get("F_SJPF")
        sjpf_json = None
        if sjpf is not None:
            if isinstance(sjpf, list):
                sjpf_json = json.dumps(sjpf)
            elif isinstance(sjpf, str):
                sjpf_json = sjpf
        
        # 插入药物记录
        insert_data = {
            "F_YWBH": ywbh,
            "F_YWLX": data.get("F_YWLX"),
            "F_YWMC": data.get("F_YWMC"),
            "F_YWSPM": data.get("F_YWSPM"),
            "F_CAS": data.get("F_CAS"),
            "F_YWDM": data.get("F_YWDM"),
            "F_HH": data.get("F_HH"),
            "F_GSMC": data.get("F_GSMC"),
            "F_YWPZQK": data.get("F_YWPZQK"),
            "F_YWFZL": data.get("F_YWFZL"),
            "F_YWSYZ": data.get("F_YWSYZ"),
            "F_ZYBD": data.get("F_ZYBD"),
            "F_YWCCND": data.get("F_YWCCND"),
            "F_ZYTL": data.get("F_ZYTL"),
            "F_CCLX": data.get("F_CCLX"),
            "F_CCBX": data.get("F_CCBX"),
            "F_CD": data.get("F_CD"),
            "F_YJKC": data.get("F_YJKC"),
            "F_CCHZ": data.get("F_CCHZ"),
            "F_SJPF": sjpf_json,
            "F_DJR": data.get("F_DJR"),
            "F_DJRQ": data.get("F_DJRQ", now),
            "F_BZ": data.get("F_BZ"),
        }
        
        # 过滤 None 值
        cols = [k for k, v in insert_data.items() if v is not None]
        vals = [insert_data[k] for k in cols]
        placeholders = ", ".join(["?" for _ in cols])
        
        cur.execute(
            f"INSERT INTO T_YWGL ({', '.join(cols)}) VALUES ({placeholders})",
            tuple(vals)
        )
        
        # 获取新 ID
        cur.execute("SELECT MAX(F_ID) FROM T_YWGL")
        new_id = cur.fetchone()[0]
        
        conn.commit()
    
    return {
        "F_ID": new_id,
        "F_YWBH": ywbh,
        "F_YWMC": data.get("F_YWMC")
    }


def update_drug(drug_id: int, data: Dict[str, Any]) -> Dict[str, Any]:
    """更新药物
    
    Args:
        drug_id: 药物ID
        data: 要更新的数据
        
    Returns:
        更新后的药物信息
    """
    adapter = get_adapter()
    
    with adapter.connect() as conn:
        cur = conn.cursor()
        
        # 检查药物是否存在
        cur.execute("SELECT F_ID, F_YWMC, F_SJPF FROM T_YWGL WHERE F_ID = ?", (drug_id,))
        row = cur.fetchone()
        if not row:
            raise ValueError(f"药物ID {drug_id} 不存在")
        
        # 处理试剂配方删除逻辑
        sjpf_del = data.pop("F_SJPF_DEL", None)
        if sjpf_del and isinstance(sjpf_del, list):
            # 获取现有配方
            current_sjpf_json = row[2]  # F_SJPF字段
            if current_sjpf_json:
                try:
                    current_sjpf = json.loads(current_sjpf_json) if isinstance(current_sjpf_json, str) else current_sjpf_json
                    if isinstance(current_sjpf, list):
                        # 从现有配方中删除指定的原药ID
                        new_sjpf = [drug_id for drug_id in current_sjpf if drug_id not in sjpf_del]
                        # 更新data中的F_SJPF
                        data["F_SJPF"] = json.dumps(new_sjpf) if new_sjpf else None
                except Exception:
                    pass
        
        # 处理试剂配方覆盖逻辑（将数组序列化为JSON字符串）
        if "F_SJPF" in data and data["F_SJPF"] is not None:
            sjpf = data["F_SJPF"]
            if isinstance(sjpf, list):
                data["F_SJPF"] = json.dumps(sjpf)
        
        # 构造更新字段
        update_fields = []
        update_values = []
        
        for key, value in data.items():
            if value is not None:
                update_fields.append(f"{key} = ?")
                update_values.append(value)
        
        if not update_fields:
            # 没有要更新的字段
            return {
                "F_ID": drug_id,
                "F_YWMC": row[1]
            }
        
        update_values.append(drug_id)
        
        cur.execute(
            f"UPDATE T_YWGL SET {', '.join(update_fields)} WHERE F_ID = ?",
            tuple(update_values)
        )
        
        # 获取更新后的药物名称
        cur.execute("SELECT F_YWMC FROM T_YWGL WHERE F_ID = ?", (drug_id,))
        ywmc = cur.fetchone()[0]
        
        conn.commit()
    
    return {
        "F_ID": drug_id,
        "F_YWMC": ywmc
    }


def get_drug_detail(drug_id: int) -> Optional[Dict[str, Any]]:
    """获取药物详情
    
    Args:
        drug_id: 药物ID
        
    Returns:
        药物详情
    """
    adapter = get_adapter()
    
    with adapter.connect() as conn:
        cur = conn.cursor()
        
        cur.execute("SELECT * FROM T_YWGL WHERE F_ID = ?", (drug_id,))
        row = cur.fetchone()
        
        if not row:
            return None
        
        columns = [col[0] for col in cur.description]
        result = dict(zip(columns, row))
        
        # 格式化日期
        if result.get("F_DJRQ"):
            result["F_DJRQ"] = result["F_DJRQ"].strftime("%Y-%m-%d %H:%M:%S") if isinstance(result["F_DJRQ"], datetime) else str(result["F_DJRQ"])
        
        # 解析试剂配方（将JSON字符串反序列化为数组）
        if result.get("F_SJPF"):
            try:
                if isinstance(result["F_SJPF"], str):
                    result["F_SJPF"] = json.loads(result["F_SJPF"])
            except Exception:
                result["F_SJPF"] = None
        
        # 如果是试剂且有配方，查询配方中原药的详细信息
        if result.get("F_YWLX") == "试剂" and result.get("F_SJPF") and isinstance(result["F_SJPF"], list):
            sjpf_details = []
            for drug_id in result["F_SJPF"]:
                try:
                    cur.execute(
                        "SELECT F_ID, F_YWBH, F_YWMC FROM T_YWGL WHERE F_ID = ?",
                        (drug_id,)
                    )
                    drug_row = cur.fetchone()
                    if drug_row:
                        sjpf_details.append({
                            "F_ID": drug_row[0],
                            "F_YWBH": drug_row[1],
                            "F_YWMC": drug_row[2]
                        })
                except Exception:
                    continue
            
            result["F_SJPF_DETAILS"] = sjpf_details if sjpf_details else None
        
        return result


def list_drugs(
    ywbh: Optional[str] = None,
    ywlx: Optional[str] = None,
    ywmc: Optional[str] = None,
    gsmc: Optional[str] = None,
    skip: int = 0,
    limit: int = 20
) -> Dict[str, Any]:
    """获取药物列表
    
    Args:
        ywbh: 药物编号（模糊匹配）
        ywlx: 药物类型（模糊匹配）
        ywmc: 药物名称（模糊匹配）
        gsmc: 公司名称（模糊匹配）
        skip: 跳过记录数
        limit: 返回记录数
        
    Returns:
        药物列表和总数
    """
    adapter = get_adapter()
    
    with adapter.connect() as conn:
        cur = conn.cursor()
        
        # 构造 WHERE 子句
        where_clauses = []
        params = []
        
        if ywbh:
            where_clauses.append("F_YWBH LIKE ?")
            params.append(f"%{ywbh}%")
        
        if ywlx:
            where_clauses.append("F_YWLX LIKE ?")
            params.append(f"%{ywlx}%")
        
        if ywmc:
            where_clauses.append("F_YWMC LIKE ?")
            params.append(f"%{ywmc}%")
        
        if gsmc:
            where_clauses.append("F_GSMC LIKE ?")
            params.append(f"%{gsmc}%")
        
        where_sql = ""
        if where_clauses:
            where_sql = "WHERE " + " AND ".join(where_clauses)
        
        # 查询总数
        cur.execute(f"SELECT COUNT(*) FROM T_YWGL {where_sql}", tuple(params))
        total = cur.fetchone()[0]
        
        # 查询列表（包含库存统计）
        # 使用子查询分别计算入库和出库，避免笛卡尔积导致的错误计算
        base_sql = f"""
            SELECT T_YWGL.*, 
                   COALESCE(rk_stat.total_rksl, 0) - COALESCE(ck_stat.total_cksl, 0) AS F_ZKSL,
                   COALESCE(rk_stat.rk_count, 0) AS F_RKJL,
                   COALESCE(ck_stat.ck_count, 0) AS F_CKJL
            FROM T_YWGL
            LEFT JOIN (
                SELECT F_YWID, SUM(F_RKSL) AS total_rksl, COUNT(F_ID) AS rk_count
                FROM T_YW_RKD
                GROUP BY F_YWID
            ) rk_stat ON T_YWGL.F_ID = rk_stat.F_YWID
            LEFT JOIN (
                SELECT F_YWID, SUM(F_CKSL) AS total_cksl, COUNT(F_ID) AS ck_count
                FROM T_YW_CKD
                GROUP BY F_YWID
            ) ck_stat ON T_YWGL.F_ID = ck_stat.F_YWID
            {where_sql}
            ORDER BY T_YWGL.F_DJRQ DESC, T_YWGL.F_ID DESC
        """
        
        sql, extra = adapter.paginate(base_sql, skip=skip, limit=limit)
        cur.execute(sql, tuple(params + extra))
        rows = cur.fetchall()
        columns = [col[0] for col in cur.description]
        
        data = []
        for row in rows:
            item = dict(zip(columns, row))
            
            # 格式化日期
            if item.get("F_DJRQ"):
                item["F_DJRQ"] = item["F_DJRQ"].strftime("%Y-%m-%d %H:%M:%S") if isinstance(item["F_DJRQ"], datetime) else str(item["F_DJRQ"])
            
            # 解析试剂配方（将JSON字符串反序列化为数组）
            if item.get("F_SJPF"):
                try:
                    if isinstance(item["F_SJPF"], str):
                        item["F_SJPF"] = json.loads(item["F_SJPF"])
                except Exception:
                    item["F_SJPF"] = None
            
            data.append(item)
        
        return {
            "total": total,
            "data": data
        }


def list_drugs_simple(
    ywbh: Optional[str] = None,
    ywlx: Optional[str] = None,
    ywmc: Optional[str] = None,
    gsmc: Optional[str] = None,
    skip: int = 0,
    limit: int = 20
) -> Dict[str, Any]:
    """获取原药简化列表（仅返回ID和名称）
    
    用于试剂登记时的下拉选择，只返回原药（排除试剂）。
    
    Args:
        ywbh: 药物编号（模糊匹配）
        ywlx: 药物类型（模糊匹配）
        ywmc: 药物名称（模糊匹配）
        gsmc: 公司名称（模糊匹配）
        skip: 跳过记录数
        limit: 返回记录数
        
    Returns:
        原药简化列表和总数
    """
    adapter = get_adapter()
    
    with adapter.connect() as conn:
        cur = conn.cursor()
        
        # 构造 WHERE 子句，默认排除试剂
        where_clauses = ["F_YWLX != ?"]
        params = ["试剂"]
        
        if ywbh:
            where_clauses.append("F_YWBH LIKE ?")
            params.append(f"%{ywbh}%")
        
        if ywlx:
            where_clauses.append("F_YWLX LIKE ?")
            params.append(f"%{ywlx}%")
        
        if ywmc:
            where_clauses.append("F_YWMC LIKE ?")
            params.append(f"%{ywmc}%")
        
        if gsmc:
            where_clauses.append("F_GSMC LIKE ?")
            params.append(f"%{gsmc}%")
        
        where_sql = "WHERE " + " AND ".join(where_clauses)
        # 查询总数
        count_sql = f"SELECT COUNT(*) FROM T_YWGL {where_sql}"
        cur.execute(count_sql, params)
        total = cur.fetchone()[0]

        # 查询数据（只返回 ID、编号、名称）
        data_sql = f"""
            SELECT F_ID, F_YWBH, F_YWMC 
            FROM T_YWGL 
            {where_sql}
            ORDER BY F_ID DESC
            OFFSET ? ROWS FETCH NEXT ? ROWS ONLY
        """
        cur.execute(data_sql, params + [skip, limit])
        rows = cur.fetchall()
        data = [
            {
                "F_ID": row[0],
                "F_YWBH": row[1],
                "F_YWMC": row[2]
            }
            for row in rows
        ]
        return {
            "total": total,
            "data": data
        }
    

def list_reagents(
    ywbh: Optional[str] = None,
    ywmc: Optional[str] = None,
    skip: int = 0,
    limit: int = 20
) -> Dict[str, Any]:
    """获取试剂列表（用于药物实验）
    
    返回试剂及其入库批次信息，只包含类型为"试剂"且有库存的记录。
    每个试剂聚合其所有有库存的入库批次（包含批次号、有效期、在库数量）。
    
    Args:
        ywbh: 药物编号（模糊匹配）
        ywmc: 药物名称（模糊匹配）
        skip: 跳过记录数
        limit: 返回记录数
        
    Returns:
        试剂列表（含批次信息）和总数
    """
    adapter = get_adapter()
    
    with adapter.connect() as conn:
        cur = conn.cursor()
        
        # 构造 WHERE 子句，固定过滤条件：试剂类型
        where_clauses = ["yw.F_YWLX = ?"]
        params = ["试剂"]
        
        if ywbh:
            where_clauses.append("yw.F_YWBH LIKE ?")
            params.append(f"%{ywbh}%")
        
        if ywmc:
            where_clauses.append("yw.F_YWMC LIKE ?")
            params.append(f"%{ywmc}%")
        
        where_clause = " AND ".join(where_clauses)
        
        # 先查询符合条件且有库存的试剂列表
        base_sql = f"""
            SELECT 
                yw.F_ID,
                yw.F_YWBH,
                yw.F_YWMC,
                yw.F_YWSPM,
                yw.F_CAS,
                yw.F_GSMC,
                COALESCE(rk.total_rksl, 0) - COALESCE(ck.total_cksl, 0) AS F_ZKSL
            FROM T_YWGL yw
            LEFT JOIN (
                SELECT F_YWID, SUM(F_RKSL) AS total_rksl
                FROM T_YW_RKD
                GROUP BY F_YWID
            ) rk ON yw.F_ID = rk.F_YWID
            LEFT JOIN (
                SELECT F_YWID, SUM(F_CKSL) AS total_cksl
                FROM T_YW_CKD
                GROUP BY F_YWID
            ) ck ON yw.F_ID = ck.F_YWID
            WHERE {where_clause}
        """
        
        # 查询总数（使用子查询过滤在库数量 > 0）
        count_sql = f"""
            SELECT COUNT(*) FROM (
                {base_sql}
            ) AS subquery
            WHERE subquery.F_ZKSL > 0
        """
        cur.execute(count_sql, tuple(params))
        total = cur.fetchone()[0]
        
        # 查询试剂列表
        list_sql = f"""
            SELECT * FROM (
                {base_sql}
            ) AS subquery
            WHERE subquery.F_ZKSL > 0
            ORDER BY F_ZKSL DESC, F_ID DESC
        """
        
        sql, extra = adapter.paginate(list_sql, skip=skip, limit=limit)
        cur.execute(sql, tuple(params + extra))
        rows = cur.fetchall()
        columns = [col[0] for col in cur.description]
        
        # 组装试剂数据
        data = []
        for row in rows:
            reagent = dict(zip(columns, row))
            reagent_id = reagent["F_ID"]
            
            # 查询该试剂的所有有库存的入库批次
            batch_sql = """
                SELECT 
                    rk.F_RKDH,
                    rk.F_PCH,
                    rk.F_YXQ,
                    rk.F_RKSL,
                    COALESCE(ck_sum.total_cksl, 0) AS F_CKSL,
                    rk.F_RKSL - COALESCE(ck_sum.total_cksl, 0) AS F_ZKSL
                FROM T_YW_RKD rk
                LEFT JOIN (
                    SELECT F_RKDH, SUM(F_CKSL) AS total_cksl
                    FROM T_YW_CKD
                    GROUP BY F_RKDH
                ) ck_sum ON rk.F_RKDH = ck_sum.F_RKDH
                WHERE rk.F_YWID = ?
                    AND rk.F_RKSL - COALESCE(ck_sum.total_cksl, 0) > 0
                ORDER BY rk.F_YXQ ASC, rk.F_RKDH ASC
            """
            
            cur.execute(batch_sql, (reagent_id,))
            batch_rows = cur.fetchall()
            batch_columns = [col[0] for col in cur.description]
            
            batches = []
            for batch_row in batch_rows:
                batch = dict(zip(batch_columns, batch_row))
                # 格式化日期
                if batch.get("F_YXQ"):
                    from datetime import datetime
                    if isinstance(batch["F_YXQ"], datetime):
                        batch["F_YXQ"] = batch["F_YXQ"].strftime("%Y-%m-%d %H:%M:%S")
                    else:
                        batch["F_YXQ"] = str(batch["F_YXQ"])
                batches.append(batch)
            
            reagent["batches"] = batches
            data.append(reagent)
        
        return {
            "total": total,
            "data": data
        }
        
        # 查询总数
        cur.execute(f"SELECT COUNT(*) FROM T_YWGL {where_sql}", tuple(params))
        total = cur.fetchone()[0]
        
        # 查询简化列表（仅需要ID、编号和名称）
        base_sql = f"""
            SELECT F_ID, F_YWBH, F_YWMC
            FROM T_YWGL
            {where_sql}
            ORDER BY F_DJRQ DESC, F_ID DESC
        """
        
        sql, extra = adapter.paginate(base_sql, skip=skip, limit=limit)
        cur.execute(sql, tuple(params + extra))
        rows = cur.fetchall()
        columns = [col[0] for col in cur.description]
        
        data = []
        for row in rows:
            item = dict(zip(columns, row))
            data.append(item)
        
        return {
            "total": total,
            "data": data
        }


def delete_drug(drug_id: int) -> Dict[str, Any]:
    """删除药物
    
    Args:
        drug_id: 药物ID
        
    Returns:
        删除的药物ID
    """
    adapter = get_adapter()
    
    with adapter.connect() as conn:
        cur = conn.cursor()
        
        # 检查药物是否存在
        cur.execute("SELECT F_ID FROM T_YWGL WHERE F_ID = ?", (drug_id,))
        if not cur.fetchone():
            raise ValueError(f"药物ID {drug_id} 不存在")
        
        # 删除药物
        cur.execute("DELETE FROM T_YWGL WHERE F_ID = ?", (drug_id,))
        
        conn.commit()
    
    return {"F_ID": drug_id}


# ==================== 入库单相关 ====================

def _generate_inbound_code() -> str:
    """生成入库单号
    格式：YWRKD + YYYYMMDDHHMMSS + 毫秒(3位)
    例如：YWRKD20231027153045123
    """
    return "YWRKD" + datetime.now().strftime("%Y%m%d%H%M%S%f")[:-3]


def create_inbound(data: Dict[str, Any]) -> Dict[str, Any]:
    """创建入库单（支持试剂入库并自动生成原药出库单）
    
    Args:
        data: 入库单数据
              - F_YWID: 药物ID
              - F_RKSL: 入库数量
              - F_YYSY: 原药使用信息（仅试剂入库时需要）
                  [{F_YWID: 原药ID, F_RKDH: 原药入库单号, F_SYSL: 使用数量}]
        
    Returns:
        创建的入库单信息
        如果是试剂入库，还包含：
        - auto_outbound_count: 自动创建的原药出库单数量
        - auto_outbound_codes: 自动创建的原药出库单号列表
    """
    adapter = get_adapter()
    now = datetime.now()
    
    with adapter.connect() as conn:
        cur = conn.cursor()
        
        # 验证药物是否存在并获取药物类型
        cur.execute("SELECT F_ID, F_YWLX, F_YWMC FROM T_YWGL WHERE F_ID = ?", (data["F_YWID"],))
        drug_row = cur.fetchone()
        if not drug_row:
            raise ValueError("药物不存在")
        
        drug_id, drug_type, drug_name = drug_row
        is_reagent = (drug_type == "试剂")

        # 批次号校重：同一药物下不允许重复录入相同批次号
        pch = data.get("F_PCH")
        if pch is not None and str(pch).strip():
            pch = str(pch).strip()
            data["F_PCH"] = pch
            cur.execute(
                "SELECT F_RKDH FROM T_YW_RKD WHERE F_YWID = ? AND F_PCH = ?",
                (drug_id, pch),
            )
            dup_row = cur.fetchone()
            if dup_row:
                raise ValueError(
                    f"药物「{drug_name}」已存在批次号「{pch}」（入库单号：{dup_row[0]}），请勿重复入库"
                )
        
        # 自动生成入库单号
        rkdh = _generate_inbound_code()
        
        # 插入入库单记录
        insert_data = {
            "F_RKDH": rkdh,
            "F_YWID": data.get("F_YWID"),
            "F_RKSL": data.get("F_RKSL"),
            "F_PCH": data.get("F_PCH"),
            "F_YXQ": data.get("F_YXQ"),
            "F_RKCZR": data.get("F_RKCZR"),
            "F_RKRQ": data.get("F_RKRQ", now),
            "F_BZ": data.get("F_BZ"),
        }
        
        # 过滤 None 值
        cols = [k for k, v in insert_data.items() if v is not None]
        vals = [insert_data[k] for k in cols]
        placeholders = ", ".join(["?" for _ in cols])
        
        cur.execute(
            f"INSERT INTO T_YW_RKD ({', '.join(cols)}) VALUES ({placeholders})",
            tuple(vals)
        )
        
        # 获取新 ID
        cur.execute("SELECT MAX(F_ID) FROM T_YW_RKD")
        new_id = cur.fetchone()[0]
        
        # 如果是试剂入库，自动为使用的原药创建出库单
        auto_outbound_codes = []
        if is_reagent and data.get("F_YYSY"):
            yysy_list = data.get("F_YYSY", [])
            
            for usage in yysy_list:
                try:
                    # 生成出库单号
                    ckdh = _generate_outbound_code()
                    
                    # 获取原药入库单信息
                    cur.execute("""
                        SELECT F_YWID, F_RKSL, F_PCH, F_YXQ
                        FROM T_YW_RKD WHERE F_RKDH = ?
                    """, (usage["F_RKDH"],))
                    
                    rkd_row = cur.fetchone()
                    if not rkd_row:
                        raise ValueError(f"原药入库单号 {usage['F_RKDH']} 不存在")
                    
                    ywid, rksl, pch, yxq = rkd_row
                    
                    # 验证库存
                    cur.execute("""
                        SELECT COALESCE(SUM(F_CKSL), 0)
                        FROM T_YW_CKD WHERE F_RKDH = ?
                    """, (usage["F_RKDH"],))
                    
                    total_cksl = cur.fetchone()[0] or 0
                    sykc = float(rksl) - float(total_cksl)
                    
                    if float(usage["F_SYSL"]) > sykc:
                        raise ValueError(f"原药 {usage['F_YWID']} 入库单 {usage['F_RKDH']} 库存不足，剩余库存: {sykc}，需要: {usage['F_SYSL']}")
                    
                    # 创建出库单
                    outbound_data = {
                        "F_CKDH": ckdh,
                        "F_RKDH": usage["F_RKDH"],
                        "F_YWID": ywid,
                        "F_CKSL": usage["F_SYSL"],
                        "F_PCH": pch,
                        "F_YXQ": yxq,
                        "F_CKRQ": now,
                        "F_CKR": data.get("F_RKCZR"),  # 使用入库操作人作为出库人
                        "F_BZ": f"试剂'{drug_name}'入库自动出库",
                    }
                    
                    # 过滤 None 值
                    out_cols = [k for k, v in outbound_data.items() if v is not None]
                    out_vals = [outbound_data[k] for k in out_cols]
                    out_placeholders = ", ".join(["?" for _ in out_cols])
                    
                    cur.execute(
                        f"INSERT INTO T_YW_CKD ({', '.join(out_cols)}) VALUES ({out_placeholders})",
                        tuple(out_vals)
                    )
                    
                    auto_outbound_codes.append(ckdh)
                    
                except Exception as e:
                    conn.rollback()
                    raise ValueError(f"创建原药出库单失败: {str(e)}")
        
        conn.commit()
        
        # 查询并返回完整记录
        cur.execute("SELECT * FROM T_YW_RKD WHERE F_ID = ?", (new_id,))
        row = cur.fetchone()
        columns = [col[0] for col in cur.description]
        result = dict(zip(columns, row))
        
        # 格式化日期
        if result.get("F_YXQ"):
            result["F_YXQ"] = result["F_YXQ"].strftime("%Y-%m-%d %H:%M:%S") if isinstance(result["F_YXQ"], datetime) else str(result["F_YXQ"])
        if result.get("F_RKRQ"):
            result["F_RKRQ"] = result["F_RKRQ"].strftime("%Y-%m-%d %H:%M:%S") if isinstance(result["F_RKRQ"], datetime) else str(result["F_RKRQ"])
        if result.get("F_CJSJ"):
            result["F_CJSJ"] = result["F_CJSJ"].strftime("%Y-%m-%d %H:%M:%S") if isinstance(result["F_CJSJ"], datetime) else str(result["F_CJSJ"])
        
        # 如果是试剂入库，添加自动出库单信息
        if auto_outbound_codes:
            result["auto_outbound_count"] = len(auto_outbound_codes)
            result["auto_outbound_codes"] = auto_outbound_codes
        
        return result


def list_inbounds(
    ywid: Optional[int] = None,
    ywbh_list: Optional[List[str]] = None,
    pch: Optional[str] = None,
    skip: int = 0,
    limit: int = 20
) -> Dict[str, Any]:
    """获取入库单列表
    
    Args:
        ywid: 药物ID
        ywbh_list: 药物编号数组（多选过滤）
        pch: 批次号（模糊匹配）
        skip: 跳过记录数
        limit: 返回记录数
        
    Returns:
        入库单列表和总数
    """
    adapter = get_adapter()
    
    with adapter.connect() as conn:
        cur = conn.cursor()
        
        # 构造 WHERE 子句
        where_clauses = []
        params = []
        
        if ywid is not None:
            where_clauses.append("rkd.F_YWID = ?")
            params.append(ywid)
        
        if ywbh_list and len(ywbh_list) > 0:
            # 药物编号数组过滤（IN 查询）
            placeholders = ", ".join(["?" for _ in ywbh_list])
            where_clauses.append(f"yw.F_YWBH IN ({placeholders})")
            params.extend(ywbh_list)
        
        if pch:
            where_clauses.append("rkd.F_PCH LIKE ?")
            params.append(f"%{pch}%")
        
        where_sql = ""
        if where_clauses:
            where_sql = "WHERE " + " AND ".join(where_clauses)
        
        # 查询总数（需要 JOIN 以支持 F_YWBH 过滤）
        count_sql = f"""
            SELECT COUNT(*) 
            FROM T_YW_RKD rkd
            LEFT JOIN T_YWGL yw ON yw.F_ID = rkd.F_YWID
            {where_sql}
        """
        cur.execute(count_sql, tuple(params))
        total = cur.fetchone()[0]
        
        # 查询列表（包含药物信息和剩余库存）
        base_sql = f"""
            SELECT 
                rkd.F_ID,
                rkd.F_RKDH,
                rkd.F_YWID,
                rkd.F_RKSL,
                rkd.F_PCH,
                rkd.F_YXQ,
                rkd.F_RKCZR,
                rkd.F_RKRQ,
                rkd.F_BZ,
                rkd.F_CJSJ,
                yw.F_YWBH,
                yw.F_YWMC,
                yw.F_YWLX,
                yw.F_GSMC,
                -- 计算本入库单的剩余在库数量
                (
                    rkd.F_RKSL - COALESCE(
                        (SELECT SUM(ckd.F_CKSL) 
                         FROM T_YW_CKD ckd 
                         WHERE ckd.F_RKDH = rkd.F_RKDH), 
                        0
                    )
                ) AS F_ZKSL
            FROM T_YW_RKD rkd
            LEFT JOIN T_YWGL yw ON yw.F_ID = rkd.F_YWID
            {where_sql}
            ORDER BY rkd.F_CJSJ DESC
        """
        
        sql, extra = adapter.paginate(base_sql, skip=skip, limit=limit)
        cur.execute(sql, tuple(params + extra))
        rows = cur.fetchall()
        columns = [col[0] for col in cur.description]
        
        data = []
        for row in rows:
            item = dict(zip(columns, row))
            
            # 格式化日期
            if item.get("F_YXQ"):
                item["F_YXQ"] = item["F_YXQ"].strftime("%Y-%m-%d %H:%M:%S") if isinstance(item["F_YXQ"], datetime) else str(item["F_YXQ"])
            if item.get("F_RKRQ"):
                item["F_RKRQ"] = item["F_RKRQ"].strftime("%Y-%m-%d %H:%M:%S") if isinstance(item["F_RKRQ"], datetime) else str(item["F_RKRQ"])
            if item.get("F_CJSJ"):
                item["F_CJSJ"] = item["F_CJSJ"].strftime("%Y-%m-%d %H:%M:%S") if isinstance(item["F_CJSJ"], datetime) else str(item["F_CJSJ"])
            
            data.append(item)
        
        return {
            "total": total,
            "data": data
        }


def get_inbound_options_by_drug(ywid: int) -> Dict[str, Any]:
    """获取指定药物的入库单选项（用于试剂配制时选择原药批次）
    
    Args:
        ywid: 药物ID
        
    Returns:
        入库单选项列表（含入库单号、批次号、有效期、剩余库存）
    """
    adapter = get_adapter()
    
    with adapter.connect() as conn:
        cur = conn.cursor()
        
        # 查询该药物的所有入库单（包含剩余库存）
        # 只返回剩余库存 > 0 的入库单
        # 按有效期升序排列（先到期的优先）
        # 使用子查询来过滤剩余库存>0的记录
        sql = """
            SELECT 
                t.F_RKDH,
                t.F_PCH,
                t.F_YXQ,
                t.F_ZKSL
            FROM (
                SELECT 
                    rkd.F_RKDH,
                    rkd.F_PCH,
                    rkd.F_YXQ,
                    rkd.F_CJSJ,
                    (
                        rkd.F_RKSL - COALESCE(
                            (SELECT SUM(ckd.F_CKSL) 
                             FROM T_YW_CKD ckd 
                             WHERE ckd.F_RKDH = rkd.F_RKDH), 
                            0
                        )
                    ) AS F_ZKSL
                FROM T_YW_RKD rkd
                WHERE rkd.F_YWID = ?
            ) t
            WHERE t.F_ZKSL > 0
            ORDER BY t.F_YXQ ASC, t.F_CJSJ DESC
        """
        
        cur.execute(sql, (ywid,))
        rows = cur.fetchall()
        columns = [col[0] for col in cur.description]
        
        data = []
        for row in rows:
            item = dict(zip(columns, row))
            
            # 格式化有效期
            if item.get("F_YXQ"):
                item["F_YXQ"] = item["F_YXQ"].strftime("%Y-%m-%d") if isinstance(item["F_YXQ"], datetime) else str(item["F_YXQ"])
            
            data.append(item)
        
        return {
            "data": data
        }


def list_inbounds_for_outbound(
    ywbh: Optional[str] = None,
    ywmc: Optional[str] = None,
    ywlx: Optional[str] = None,
    rkdh: Optional[str] = None,
    skip: int = 0,
    limit: int = 20
) -> Dict[str, Any]:
    """获取可用于出库的入库单列表
    
    Args:
        ywbh: 药物编号（模糊匹配）
        ywmc: 药物名称（模糊匹配）
        ywlx: 药物类型（精确匹配）
        rkdh: 入库单号（模糊匹配）
        skip: 跳过记录数
        limit: 返回记录数
        
    Returns:
        入库单列表和总数（仅包含剩余库存 > 0 的记录）
    """
    adapter = get_adapter()
    
    with adapter.connect() as conn:
        cur = conn.cursor()
        
        # 构造 WHERE 子句（针对子查询结果）
        where_clauses = []
        params = []
        
        if ywbh:
            where_clauses.append("t.F_YWBH LIKE ?")
            params.append(f"%{ywbh}%")
        
        if ywmc:
            where_clauses.append("t.F_YWMC LIKE ?")
            params.append(f"%{ywmc}%")
        
        if ywlx:
            where_clauses.append("t.F_YWLX = ?")
            params.append(ywlx)
        
        if rkdh:
            where_clauses.append("t.F_RKDH LIKE ?")
            params.append(f"%{rkdh}%")
        
        # 强制过滤：剩余库存 > 0
        where_clauses.append("t.F_ZKSL > 0")
        
        where_sql = "WHERE " + " AND ".join(where_clauses)
        
        # 使用子查询计算剩余库存，外层过滤
        subquery_sql = """
            SELECT 
                rkd.F_ID,
                rkd.F_RKDH,
                rkd.F_YWID,
                rkd.F_RKSL,
                rkd.F_PCH,
                rkd.F_YXQ,
                rkd.F_RKCZR,
                rkd.F_RKRQ,
                rkd.F_CJSJ,
                yw.F_YWBH,
                yw.F_YWMC,
                yw.F_YWLX,
                yw.F_GSMC,
                (
                    rkd.F_RKSL - COALESCE(
                        (SELECT SUM(ckd.F_CKSL) 
                         FROM T_YW_CKD ckd 
                         WHERE ckd.F_RKDH = rkd.F_RKDH), 
                        0
                    )
                ) AS F_ZKSL
            FROM T_YW_RKD rkd
            LEFT JOIN T_YWGL yw ON yw.F_ID = rkd.F_YWID
        """
        
        # 查询总数
        count_sql = f"""
            SELECT COUNT(*) 
            FROM ({subquery_sql}) t
            {where_sql}
        """
        cur.execute(count_sql, tuple(params))
        total = cur.fetchone()[0]
        
        # 查询列表
        base_sql = f"""
            SELECT 
                t.F_ID,
                t.F_RKDH,
                t.F_YWID,
                t.F_YWBH,
                t.F_YWMC,
                t.F_YWLX,
                t.F_GSMC,
                t.F_RKSL,
                t.F_ZKSL,
                t.F_PCH,
                t.F_YXQ,
                t.F_RKRQ,
                t.F_RKCZR
            FROM ({subquery_sql}) t
            {where_sql}
            ORDER BY t.F_YXQ ASC, t.F_CJSJ DESC
        """
        
        sql, extra = adapter.paginate(base_sql, skip=skip, limit=limit)
        cur.execute(sql, tuple(params + extra))
        rows = cur.fetchall()
        columns = [col[0] for col in cur.description]
        
        data = []
        for row in rows:
            item = dict(zip(columns, row))
            
            # 格式化日期
            if item.get("F_YXQ"):
                item["F_YXQ"] = item["F_YXQ"].strftime("%Y-%m-%d") if isinstance(item["F_YXQ"], datetime) else str(item["F_YXQ"])
            if item.get("F_RKRQ"):
                item["F_RKRQ"] = item["F_RKRQ"].strftime("%Y-%m-%d %H:%M:%S") if isinstance(item["F_RKRQ"], datetime) else str(item["F_RKRQ"])
            
            data.append(item)
        
        return {
            "total": total,
            "data": data
        }


# ==================== 出库单相关 ====================

def _generate_outbound_code() -> str:
    """生成出库单号
    格式：YWCKD + YYYYMMDDHHMMSS + 毫秒(3位)
    例如：YWCKD20231027153045123
    """
    return "YWCKD" + datetime.now().strftime("%Y%m%d%H%M%S%f")[:-3]


def create_outbound(data: Dict[str, Any]) -> Dict[str, Any]:
    """创建出库单（基于入库单）
    
    Args:
        data: 出库单数据，必须包含F_RKDH（入库单号）
        
    Returns:
        创建的出库单信息
        
    Raises:
        ValueError: 入库单不存在、库存不足
    """
    adapter = get_adapter()
    now = datetime.now()
    
    with adapter.connect() as conn:
        cur = conn.cursor()
        
        # 1. 根据入库单号查询入库单信息
        f_rkdh = data.get("F_RKDH")
        if not f_rkdh:
            raise ValueError("必须提供入库单号")
        
        cur.execute("""
            SELECT F_ID, F_YWID, F_RKSL, F_PCH, F_YXQ
            FROM T_YW_RKD WHERE F_RKDH = ?
        """, (f_rkdh,))
        
        rkd_row = cur.fetchone()
        if not rkd_row:
            raise ValueError(f"入库单号 {f_rkdh} 不存在")
        
        rkd_id, ywid, rksl, pch, yxq = rkd_row
        
        # 2. 计算剩余库存（入库数量 - 已出库数量）
        cur.execute("""
            SELECT COALESCE(SUM(F_CKSL), 0)
            FROM T_YW_CKD WHERE F_RKDH = ?
        """, (f_rkdh,))
        
        total_cksl = cur.fetchone()[0] or 0
        sykc = float(rksl) - float(total_cksl)  # 剩余库存
        
        # 3. 验证出库数量不超过剩余库存
        cksl = float(data.get("F_CKSL"))
        if cksl > sykc:
            raise ValueError(f"出库数量({cksl})超过剩余库存({sykc})")
        
        # 4. 自动生成出库单号
        ckdh = _generate_outbound_code()
        
        # 5. 插入出库单记录（自动补充药物信息从入库单获取）
        insert_data = {
            "F_CKDH": ckdh,
            "F_RKDH": f_rkdh,
            "F_YWID": ywid,
            "F_CKSL": cksl,
            "F_PCH": pch,
            "F_YXQ": yxq,
            "F_CKRQ": data.get("F_CKRQ", now),
            "F_CKR": data.get("F_CKR"),
            "F_BZ": data.get("F_BZ"),
        }
        
        # 过滤 None 值
        cols = [k for k, v in insert_data.items() if v is not None]
        vals = [insert_data[k] for k in cols]
        placeholders = ", ".join(["?" for _ in cols])
        
        cur.execute(
            f"INSERT INTO T_YW_CKD ({', '.join(cols)}) VALUES ({placeholders})",
            tuple(vals)
        )
        
        # 获取新 ID
        cur.execute("SELECT MAX(F_ID) FROM T_YW_CKD")
        new_id = cur.fetchone()[0]
        
        conn.commit()
        
        # 查询并返回完整记录
        cur.execute("SELECT * FROM T_YW_CKD WHERE F_ID = ?", (new_id,))
        row = cur.fetchone()
        columns = [col[0] for col in cur.description]
        result = dict(zip(columns, row))
        
        # 格式化日期
        if result.get("F_YXQ"):
            result["F_YXQ"] = result["F_YXQ"].strftime("%Y-%m-%d %H:%M:%S") if isinstance(result["F_YXQ"], datetime) else str(result["F_YXQ"])
        if result.get("F_CKRQ"):
            result["F_CKRQ"] = result["F_CKRQ"].strftime("%Y-%m-%d %H:%M:%S") if isinstance(result["F_CKRQ"], datetime) else str(result["F_CKRQ"])
        if result.get("F_CJSJ"):
            result["F_CJSJ"] = result["F_CJSJ"].strftime("%Y-%m-%d %H:%M:%S") if isinstance(result["F_CJSJ"], datetime) else str(result["F_CJSJ"])
        
        return result


def list_outbounds(
    ywid: Optional[int] = None,
    ywbh: Optional[str] = None,
    ywmc: Optional[str] = None,
    ywlx: Optional[str] = None,
    ckdh: Optional[str] = None,
    rkdh: Optional[str] = None,
    pch: Optional[str] = None,
    skip: int = 0,
    limit: int = 20
) -> Dict[str, Any]:
    """获取出库单列表
    
    Args:
        ywid: 药物ID
        ywbh: 药物编号（模糊匹配）
        ywmc: 药物名称（模糊匹配）
        ywlx: 药物类型（精确匹配）
        ckdh: 出库单号（模糊匹配）
        rkdh: 入库单号（模糊匹配）
        pch: 批次号（模糊匹配）
        skip: 跳过记录数
        limit: 返回记录数
        
    Returns:
        出库单列表和总数
    """
    adapter = get_adapter()
    
    with adapter.connect() as conn:
        cur = conn.cursor()
        
        # 构造 WHERE 子句
        where_clauses = []
        params = []
        
        if ywid is not None:
            where_clauses.append("ckd.F_YWID = ?")
            params.append(ywid)
        
        if ywbh:
            where_clauses.append("yw.F_YWBH LIKE ?")
            params.append(f"%{ywbh}%")
        
        if ywmc:
            where_clauses.append("yw.F_YWMC LIKE ?")
            params.append(f"%{ywmc}%")
        
        if ywlx:
            where_clauses.append("yw.F_YWLX = ?")
            params.append(ywlx)
        
        if ckdh:
            where_clauses.append("ckd.F_CKDH LIKE ?")
            params.append(f"%{ckdh}%")
        
        if rkdh:
            where_clauses.append("ckd.F_RKDH LIKE ?")
            params.append(f"%{rkdh}%")
        
        if pch:
            where_clauses.append("ckd.F_PCH LIKE ?")
            params.append(f"%{pch}%")
        
        where_sql = ""
        if where_clauses:
            where_sql = "WHERE " + " AND ".join(where_clauses)
        
        # 查询总数（需要 JOIN 以支持药物字段过滤）
        count_sql = f"""
            SELECT COUNT(*) 
            FROM T_YW_CKD ckd
            LEFT JOIN T_YWGL yw ON yw.F_ID = ckd.F_YWID
            {where_sql}
        """
        cur.execute(count_sql, tuple(params))
        total = cur.fetchone()[0]
        
        # 查询列表（包含药物信息）
        base_sql = f"""
            SELECT 
                ckd.F_ID,
                ckd.F_CKDH,
                ckd.F_RKDH,
                ckd.F_YWID,
                ckd.F_CKSL,
                ckd.F_PCH,
                ckd.F_YXQ,
                ckd.F_CKRQ,
                ckd.F_CKR,
                ckd.F_BZ,
                ckd.F_CJSJ,
                yw.F_YWBH,
                yw.F_YWMC,
                yw.F_YWLX,
                yw.F_GSMC
            FROM T_YW_CKD ckd
            LEFT JOIN T_YWGL yw ON yw.F_ID = ckd.F_YWID
            {where_sql}
            ORDER BY ckd.F_CJSJ DESC
        """
        
        sql, extra = adapter.paginate(base_sql, skip=skip, limit=limit)
        cur.execute(sql, tuple(params + extra))
        rows = cur.fetchall()
        columns = [col[0] for col in cur.description]
        
        data = []
        for row in rows:
            item = dict(zip(columns, row))
            
            # 格式化日期
            if item.get("F_YXQ"):
                item["F_YXQ"] = item["F_YXQ"].strftime("%Y-%m-%d %H:%M:%S") if isinstance(item["F_YXQ"], datetime) else str(item["F_YXQ"])
            if item.get("F_CKRQ"):
                item["F_CKRQ"] = item["F_CKRQ"].strftime("%Y-%m-%d %H:%M:%S") if isinstance(item["F_CKRQ"], datetime) else str(item["F_CKRQ"])
            if item.get("F_CJSJ"):
                item["F_CJSJ"] = item["F_CJSJ"].strftime("%Y-%m-%d %H:%M:%S") if isinstance(item["F_CJSJ"], datetime) else str(item["F_CJSJ"])
            
            data.append(item)
        
        return {
            "total": total,
            "data": data
        }


def batch_create_outbounds(items: List[Dict[str, Any]], current_user_code: Optional[str] = None) -> Dict[str, Any]:
    """批量创建出库单
    
    Args:
        items: 出库单列表，每项包含：
            - F_RKDH: 入库单号
            - F_CKSL: 出库数量
            - F_BZ: 备注（可选）
        current_user_code: 当前用户代码（自动填充出库人）
        
    Returns:
        批量创建结果：
            - success_count: 成功数量
            - failed_count: 失败数量
            - success_items: 成功的出库单列表
            - failed_items: 失败的出库单列表（包含错误原因）
    """
    adapter = get_adapter()
    success_items = []
    failed_items = []
    
    # 统一生成出库单号（同一次批量操作共享同一个出库单号）
    batch_ckdh = _generate_outbound_code()
    # 统一出库日期
    ckrq = datetime.now()
    
    with adapter.connect() as conn:
        cur = conn.cursor()
        
        for item in items:
            try:
                f_rkdh = item.get("F_RKDH")
                f_cksl = item.get("F_CKSL")
                f_bz = item.get("F_BZ")
                
                if not f_rkdh:
                    failed_items.append({
                        "F_RKDH": f_rkdh or "",
                        "F_CKSL": f_cksl or 0,
                        "error": "必须提供入库单号"
                    })
                    continue
                
                if not f_cksl or f_cksl <= 0:
                    failed_items.append({
                        "F_RKDH": f_rkdh,
                        "F_CKSL": f_cksl or 0,
                        "error": "出库数量必须大于0"
                    })
                    continue
                
                # 1. 根据入库单号查询入库单信息
                cur.execute("""
                    SELECT F_ID, F_YWID, F_RKSL, F_PCH, F_YXQ
                    FROM T_YW_RKD WHERE F_RKDH = ?
                """, (f_rkdh,))
                
                rkd_row = cur.fetchone()
                if not rkd_row:
                    failed_items.append({
                        "F_RKDH": f_rkdh,
                        "F_CKSL": f_cksl,
                        "error": f"入库单号 {f_rkdh} 不存在"
                    })
                    continue
                
                rkd_id, ywid, rksl, pch, yxq = rkd_row
                
                # 2. 计算剩余库存（入库数量 - 已出库数量）
                cur.execute("""
                    SELECT COALESCE(SUM(F_CKSL), 0)
                    FROM T_YW_CKD WHERE F_RKDH = ?
                """, (f_rkdh,))
                
                total_cksl = cur.fetchone()[0] or 0
                sykc = float(rksl) - float(total_cksl)  # 剩余库存
                
                # 3. 验证出库数量不超过剩余库存
                cksl = float(f_cksl)
                if cksl > sykc:
                    failed_items.append({
                        "F_RKDH": f_rkdh,
                        "F_CKSL": cksl,
                        "error": f"出库数量({cksl})超过剩余库存({sykc})"
                    })
                    continue
                
                # 4. 插入出库单记录（使用统一的出库单号）
                insert_data = {
                    "F_CKDH": batch_ckdh,  # 使用统一的出库单号
                    "F_RKDH": f_rkdh,
                    "F_YWID": ywid,
                    "F_CKSL": cksl,
                    "F_PCH": pch,
                    "F_YXQ": yxq,
                    "F_CKRQ": ckrq,
                    "F_CKR": current_user_code,
                    "F_BZ": f_bz,
                }
                
                # 过滤 None 值
                cols = [k for k, v in insert_data.items() if v is not None]
                vals = [insert_data[k] for k in cols]
                placeholders = ", ".join(["?" for _ in cols])
                
                cur.execute(
                    f"INSERT INTO T_YW_CKD ({', '.join(cols)}) VALUES ({placeholders})",
                    tuple(vals)
                )
                
                # 获取新 ID
                cur.execute("SELECT MAX(F_ID) FROM T_YW_CKD")
                new_id = cur.fetchone()[0]
                
                # 查询药物名称
                cur.execute("SELECT F_YWMC FROM T_YWGL WHERE F_ID = ?", (ywid,))
                ywmc_row = cur.fetchone()
                ywmc = ywmc_row[0] if ywmc_row else None
                
                # 记录成功项
                success_items.append({
                    "F_ID": new_id,
                    "F_CKDH": batch_ckdh,  # 使用统一的出库单号
                    "F_RKDH": f_rkdh,
                    "F_YWID": ywid,
                    "F_YWMC": ywmc,
                    "F_CKSL": cksl,
                    "F_BZ": f_bz
                })
                
            except Exception as e:
                # 单条出库失败，记录错误但继续处理其他项
                failed_items.append({
                    "F_RKDH": item.get("F_RKDH", ""),
                    "F_CKSL": item.get("F_CKSL", 0),
                    "error": f"出库失败: {str(e)}"
                })
        
        # 提交所有成功的出库单
        conn.commit()
    
    return {
        "success_count": len(success_items),
        "failed_count": len(failed_items),
        "success_items": success_items,
        "failed_items": failed_items
    }

