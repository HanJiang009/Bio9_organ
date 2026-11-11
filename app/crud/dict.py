"""
字典管理模块 CRUD
"""
from typing import Dict, Any, List, Optional
from app.db.factory import get_adapter
from datetime import datetime
import logging

logger = logging.getLogger(__name__)


# ============================================
# 字典类别相关函数
# ============================================

def list_dict_categories(query: Dict[str, Any]) -> Dict[str, Any]:
    """
    查询字典类别列表
    
    Args:
        query: 查询条件
            - F_MODULE: 所属模块过滤（可选）
            - F_ENABLED: 是否启用过滤（可选）
            - skip: 跳过记录数
            - limit: 每页记录数
    
    Returns:
        {items: List[dict], total: int}
    """
    adapter = get_adapter()
    
    where: List[str] = []
    params: List[Any] = []
    
    # 模块过滤
    if query.get("F_MODULE"):
        where.append("F_MODULE = ?")
        params.append(query["F_MODULE"])
    
    # 启用状态过滤
    if query.get("F_ENABLED") is not None:
        where.append("F_ENABLED = ?")
        params.append(query["F_ENABLED"])
    
    where_sql = (" WHERE " + " AND ".join(where)) if where else ""
    
    base_sql = f"""
        SELECT F_ID, F_CODE, F_NAME, F_DESC, F_MODULE, F_ENABLED, F_SORT,
               F_CREATE_TIME, F_UPDATE_TIME
        FROM T_DICT_CATEGORY
        {where_sql}
        ORDER BY F_SORT ASC, F_ID ASC
    """
    
    # 统计总数
    count_sql = f"SELECT COUNT(1) FROM T_DICT_CATEGORY{where_sql}"
    
    # 分页查询
    sql, extra = adapter.paginate(base_sql, skip=query.get("skip", 0), limit=query.get("limit", 100))
    
    with adapter.connect() as conn:
        cur = conn.cursor()
        
        # 查询总数
        cur.execute(count_sql, tuple(params))
        total = int(cur.fetchone()[0])
        
        # 查询列表
        cur.execute(sql, tuple(params + extra))
        rows = cur.fetchall()
        cols = [c[0] for c in cur.description]
        items: List[Dict[str, Any]] = [dict(zip(cols, r)) for r in rows]
        
        return {"items": items, "total": total}


def get_dict_category_by_id(category_id: int) -> Optional[Dict[str, Any]]:
    """根据ID查询字典类别"""
    adapter = get_adapter()
    sql = """
        SELECT F_ID, F_CODE, F_NAME, F_DESC, F_MODULE, F_ENABLED, F_SORT,
               F_CREATE_TIME, F_UPDATE_TIME
        FROM T_DICT_CATEGORY WHERE F_ID = ?
    """
    with adapter.connect() as conn:
        cur = conn.cursor()
        cur.execute(sql, (category_id,))
        row = cur.fetchone()
        if row:
            cols = [c[0] for c in cur.description]
            return dict(zip(cols, row))
    return None


def get_dict_category_by_code(category_code: str) -> Optional[Dict[str, Any]]:
    """根据代码查询字典类别"""
    adapter = get_adapter()
    sql = """
        SELECT F_ID, F_CODE, F_NAME, F_DESC, F_MODULE, F_ENABLED, F_SORT,
               F_CREATE_TIME, F_UPDATE_TIME
        FROM T_DICT_CATEGORY WHERE F_CODE = ?
    """
    with adapter.connect() as conn:
        cur = conn.cursor()
        cur.execute(sql, (category_code,))
        row = cur.fetchone()
        if row:
            cols = [c[0] for c in cur.description]
            return dict(zip(cols, row))
    return None


def create_dict_category(data: Dict[str, Any]) -> int:
    """
    创建字典类别
    
    Args:
        data: 类别数据
    
    Returns:
        新创建的类别ID
    """
    adapter = get_adapter()
    
    # 检查代码是否已存在
    existing = get_dict_category_by_code(data["F_CODE"])
    if existing:
        raise ValueError(f"类别代码 {data['F_CODE']} 已存在")
    
    sql = """
        INSERT INTO T_DICT_CATEGORY (
            F_CODE, F_NAME, F_DESC, F_MODULE, F_ENABLED, F_SORT
        ) VALUES (?, ?, ?, ?, ?, ?)
    """
    
    with adapter.connect() as conn:
        cur = conn.cursor()
        cur.execute(sql, (
            data["F_CODE"],
            data["F_NAME"],
            data.get("F_DESC"),
            data.get("F_MODULE"),
            data.get("F_ENABLED", 1),
            data.get("F_SORT", 0)
        ))
        
        # 获取新插入的ID
        cur.execute("SELECT MAX(F_ID) FROM T_DICT_CATEGORY")
        new_id = cur.fetchone()[0]
        
        conn.commit()
        return int(new_id)


def update_dict_category(category_id: int, data: Dict[str, Any]) -> bool:
    """
    更新字典类别
    
    Args:
        category_id: 类别ID
        data: 更新数据
    
    Returns:
        是否更新成功
    """
    adapter = get_adapter()
    
    # 检查是否存在
    existing = get_dict_category_by_id(category_id)
    if not existing:
        raise ValueError(f"类别ID {category_id} 不存在")
    
    updates: List[str] = []
    params: List[Any] = []
    
    # 构建更新字段
    for field in ["F_CODE", "F_NAME", "F_DESC", "F_MODULE", "F_ENABLED", "F_SORT"]:
        if field in data and data[field] is not None:
            updates.append(f"{field} = ?")
            params.append(data[field])
    
    if not updates:
        return True  # 没有需要更新的字段
    
    # 添加更新时间
    updates.append(f"F_UPDATE_TIME = {adapter.now()}")
    
    params.append(category_id)
    sql = f"UPDATE T_DICT_CATEGORY SET {', '.join(updates)} WHERE F_ID = ?"
    
    with adapter.connect() as conn:
        cur = conn.cursor()
        cur.execute(sql, tuple(params))
        conn.commit()
        return True


def delete_dict_category(category_id: int) -> bool:
    """
    删除字典类别（会级联删除相关字典项）
    
    Args:
        category_id: 类别ID
    
    Returns:
        是否删除成功
    """
    adapter = get_adapter()
    
    # 检查是否存在
    existing = get_dict_category_by_id(category_id)
    if not existing:
        raise ValueError(f"类别ID {category_id} 不存在")
    
    sql = "DELETE FROM T_DICT_CATEGORY WHERE F_ID = ?"
    
    with adapter.connect() as conn:
        cur = conn.cursor()
        cur.execute(sql, (category_id,))
        conn.commit()
        return True


# ============================================
# 字典项相关函数
# ============================================

def list_dict_items(category_code: Optional[str] = None, enabled_only: bool = False,
                    skip: int = 0, limit: int = 1000,
                    name: Optional[str] = None) -> Dict[str, Any]:
    """
    查询字典项列表。

    优先级：category_code 精确匹配 > name 精确匹配类别名称 > 全量。
    两者可同时传入（AND 关系）。

    Args:
        category_code: 类别代码（精确，可选）
        name: 类别名称（精确匹配，可选）；需 JOIN T_DICT_CATEGORY
        enabled_only: 是否只返回启用的字典项
        skip: 跳过记录数
        limit: 每页记录数

    Returns:
        {items: List[dict], total: int}
    """
    adapter = get_adapter()

    use_join = bool(name)  # 需要按类别名称过滤时才 JOIN

    where: List[str] = []
    params: List[Any] = []

    if category_code:
        col = "i.F_CATEGORY_CODE" if use_join else "F_CATEGORY_CODE"
        where.append(f"{col} = ?")
        params.append(category_code)

    if name:
        where.append("c.F_NAME = ?")
        params.append(name.strip())

    if enabled_only:
        col = "i.F_ENABLED" if use_join else "F_ENABLED"
        where.append(f"{col} = 1")

    where_sql = (" WHERE " + " AND ".join(where)) if where else ""

    if use_join:
        from_clause = """
            T_DICT_ITEM i
            JOIN T_DICT_CATEGORY c ON c.F_CODE = i.F_CATEGORY_CODE
        """
        select_cols = """i.F_ID, i.F_CATEGORY_CODE, i.F_CODE, i.F_VALUE,
               i.F_SORT, i.F_ENABLED, i.F_IS_DEFAULT, i.F_REMARK,
               i.F_CREATE_TIME, i.F_UPDATE_TIME"""
        order_clause = "ORDER BY i.F_SORT ASC, i.F_ID ASC"
    else:
        from_clause = "T_DICT_ITEM"
        select_cols = """F_ID, F_CATEGORY_CODE, F_CODE, F_VALUE,
               F_SORT, F_ENABLED, F_IS_DEFAULT, F_REMARK,
               F_CREATE_TIME, F_UPDATE_TIME"""
        order_clause = "ORDER BY F_SORT ASC, F_ID ASC"

    base_sql = f"""
        SELECT {select_cols}
        FROM {from_clause}
        {where_sql}
        {order_clause}
    """
    count_sql = f"SELECT COUNT(1) FROM {from_clause}{where_sql}"

    sql, extra = adapter.paginate(base_sql, skip=skip, limit=limit)

    with adapter.connect() as conn:
        cur = conn.cursor()

        cur.execute(count_sql, tuple(params))
        total = int(cur.fetchone()[0])

        cur.execute(sql, tuple(params + extra))
        rows = cur.fetchall()
        cols = [c[0] for c in cur.description]
        items: List[Dict[str, Any]] = [dict(zip(cols, r)) for r in rows]

        return {"items": items, "total": total}


def get_dict_items_batch(category_codes: List[str], enabled_only: bool = True) -> Dict[str, List[Dict[str, Any]]]:
    """
    批量获取多个类别的字典项
    
    Args:
        category_codes: 类别代码列表
        enabled_only: 是否只返回启用的项
    
    Returns:
        字典格式，key为类别代码，value为字典项列表
    """
    adapter = get_adapter()
    
    if not category_codes:
        return {}
    
    placeholders = ",".join(["?" for _ in category_codes])
    where = f"F_CATEGORY_CODE IN ({placeholders})"
    params = list(category_codes)
    
    if enabled_only:
        where += " AND F_ENABLED = 1"
    
    sql = f"""
        SELECT F_ID, F_CATEGORY_CODE, F_CODE, F_VALUE, F_SORT, F_ENABLED,
               F_IS_DEFAULT, F_REMARK, F_CREATE_TIME, F_UPDATE_TIME
        FROM T_DICT_ITEM
        WHERE {where}
        ORDER BY F_CATEGORY_CODE, F_SORT ASC, F_ID ASC
    """
    
    with adapter.connect() as conn:
        cur = conn.cursor()
        cur.execute(sql, tuple(params))
        rows = cur.fetchall()
        cols = [c[0] for c in cur.description]
        
        # 按类别分组
        result: Dict[str, List[Dict[str, Any]]] = {code: [] for code in category_codes}
        for row in rows:
            item = dict(zip(cols, row))
            category_code = item["F_CATEGORY_CODE"]
            if category_code in result:
                result[category_code].append(item)
        
        return result


def get_dict_item_by_id(item_id: int) -> Optional[Dict[str, Any]]:
    """根据ID查询字典项"""
    adapter = get_adapter()
    sql = """
        SELECT F_ID, F_CATEGORY_CODE, F_CODE, F_VALUE, F_SORT, F_ENABLED,
               F_IS_DEFAULT, F_REMARK, F_CREATE_TIME, F_UPDATE_TIME
        FROM T_DICT_ITEM WHERE F_ID = ?
    """
    with adapter.connect() as conn:
        cur = conn.cursor()
        cur.execute(sql, (item_id,))
        row = cur.fetchone()
        if row:
            cols = [c[0] for c in cur.description]
            return dict(zip(cols, row))
    return None


def _norm_dict_text(v: Any) -> str:
    if v is None:
        return ""
    return str(v).strip()


def _assert_dict_item_unique(
    category_code: str,
    *,
    value: Any,
    code: Any = None,
    exclude_id: Optional[int] = None,
) -> None:
    """同一字典类别下，字段名称（F_VALUE）与字段编码（F_CODE）均须唯一。"""
    val = _norm_dict_text(value)
    code_n = _norm_dict_text(code)
    if not val:
        raise ValueError("字段名称不能为空")

    adapter = get_adapter()
    with adapter.connect() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT F_ID, F_CODE, F_VALUE FROM T_DICT_ITEM WHERE F_CATEGORY_CODE = ?",
            (category_code,),
        )
        cols = [c[0] for c in cur.description]
        rows = [dict(zip(cols, r)) for r in cur.fetchall()]

    for row in rows:
        rid = row.get("F_ID")
        if exclude_id is not None and rid == exclude_id:
            continue
        if _norm_dict_text(row.get("F_VALUE")) == val:
            raise ValueError(f"字段名称「{val}」在该字典下已存在")
        other_code = _norm_dict_text(row.get("F_CODE"))
        if code_n and other_code and other_code.upper() == code_n.upper():
            raise ValueError(f"字段编码「{code_n}」在该字典下已存在")


def create_dict_item(data: Dict[str, Any]) -> int:
    """
    创建字典项
    
    Args:
        data: 字典项数据
    
    Returns:
        新创建的字典项ID
    """
    adapter = get_adapter()
    
    # 验证类别代码是否存在
    category = get_dict_category_by_code(data["F_CATEGORY_CODE"])
    if not category:
        raise ValueError(f"类别代码 {data['F_CATEGORY_CODE']} 不存在")

    _assert_dict_item_unique(
        data["F_CATEGORY_CODE"],
        value=data.get("F_VALUE"),
        code=data.get("F_CODE"),
    )
    
    sql = """
        INSERT INTO T_DICT_ITEM (
            F_CATEGORY_CODE, F_CODE, F_VALUE, F_SORT, F_ENABLED,
            F_IS_DEFAULT, F_REMARK
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
    """
    
    with adapter.connect() as conn:
        cur = conn.cursor()
        cur.execute(sql, (
            data["F_CATEGORY_CODE"],
            data.get("F_CODE"),
            data["F_VALUE"],
            data.get("F_SORT", 0),
            data.get("F_ENABLED", 1),
            data.get("F_IS_DEFAULT", 0),
            data.get("F_REMARK")
        ))
        
        # 获取新插入的ID
        cur.execute("SELECT MAX(F_ID) FROM T_DICT_ITEM")
        new_id = cur.fetchone()[0]
        
        conn.commit()
        return int(new_id)


def update_dict_item(item_id: int, data: Dict[str, Any]) -> bool:
    """
    更新字典项
    
    Args:
        item_id: 字典项ID
        data: 更新数据
    
    Returns:
        是否更新成功
    """
    adapter = get_adapter()
    
    # 检查是否存在
    existing = get_dict_item_by_id(item_id)
    if not existing:
        raise ValueError(f"字典项ID {item_id} 不存在")

    next_value = data["F_VALUE"] if "F_VALUE" in data and data["F_VALUE"] is not None else existing.get("F_VALUE")
    next_code = data["F_CODE"] if "F_CODE" in data and data["F_CODE"] is not None else existing.get("F_CODE")
    _assert_dict_item_unique(
        existing["F_CATEGORY_CODE"],
        value=next_value,
        code=next_code,
        exclude_id=item_id,
    )
    
    updates: List[str] = []
    params: List[Any] = []
    
    # 构建更新字段
    for field in ["F_CODE", "F_VALUE", "F_SORT", "F_ENABLED", "F_IS_DEFAULT", "F_REMARK"]:
        if field in data and data[field] is not None:
            updates.append(f"{field} = ?")
            params.append(data[field])
    
    if not updates:
        return True  # 没有需要更新的字段
    
    # 添加更新时间
    updates.append(f"F_UPDATE_TIME = {adapter.now()}")
    
    params.append(item_id)
    sql = f"UPDATE T_DICT_ITEM SET {', '.join(updates)} WHERE F_ID = ?"
    
    with adapter.connect() as conn:
        cur = conn.cursor()
        cur.execute(sql, tuple(params))
        conn.commit()
        return True


def delete_dict_item(item_id: int) -> bool:
    """
    删除字典项
    
    Args:
        item_id: 字典项ID
    
    Returns:
        是否删除成功
    """
    adapter = get_adapter()
    
    # 检查是否存在
    existing = get_dict_item_by_id(item_id)
    if not existing:
        raise ValueError(f"字典项ID {item_id} 不存在")
    
    sql = "DELETE FROM T_DICT_ITEM WHERE F_ID = ?"
    
    with adapter.connect() as conn:
        cur = conn.cursor()
        cur.execute(sql, (item_id,))
        conn.commit()
        return True


def batch_update_dict_item_sort(category_code: str, sort_items: List[Dict[str, Any]]) -> bool:
    """
    批量更新字典项排序
    
    Args:
        category_code: 类别代码
        sort_items: 排序项列表 [{"F_ID": 1, "F_SORT": 10}, ...]
    
    Returns:
        是否更新成功
    """
    adapter = get_adapter()
    
    if not sort_items:
        return True
    
    # 验证类别代码
    category = get_dict_category_by_code(category_code)
    if not category:
        raise ValueError(f"类别代码 {category_code} 不存在")
    
    with adapter.connect() as conn:
        cur = conn.cursor()
        
        for item in sort_items:
            item_id = item["F_ID"]
            sort_value = item["F_SORT"]
            
            # 验证字典项是否属于该类别
            existing = get_dict_item_by_id(item_id)
            if not existing:
                logger.warning(f"字典项ID {item_id} 不存在，跳过")
                continue
            
            if existing["F_CATEGORY_CODE"] != category_code:
                logger.warning(f"字典项ID {item_id} 不属于类别 {category_code}，跳过")
                continue
            
            # 更新排序
            sql = "UPDATE T_DICT_ITEM SET F_SORT = ? WHERE F_ID = ?"
            cur.execute(sql, (sort_value, item_id))
        
        conn.commit()
        return True


def batch_create_dict_items(category_code: str, items: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    批量创建字典项
    
    Args:
        category_code: 类别代码
        items: 字典项列表
    
    Returns:
        {
            "success_count": 成功数量,
            "failed_count": 失败数量,
            "created_ids": [创建的ID列表],
            "errors": [错误信息列表]
        }
    """
    adapter = get_adapter()
    
    # 验证类别代码是否存在
    category = get_dict_category_by_code(category_code)
    if not category:
        raise ValueError(f"类别代码 {category_code} 不存在")
    
    created_ids: List[int] = []
    errors: List[str] = []
    seen_values: set = set()
    seen_codes: set = set()
    
    with adapter.connect() as conn:
        cur = conn.cursor()
        
        for idx, item_data in enumerate(items):
            try:
                item_data["F_CATEGORY_CODE"] = category_code
                val = _norm_dict_text(item_data.get("F_VALUE"))
                code_n = _norm_dict_text(item_data.get("F_CODE"))
                if val in seen_values:
                    raise ValueError(f"字段名称「{val}」在该字典下已存在")
                if code_n and code_n.upper() in seen_codes:
                    raise ValueError(f"字段编码「{code_n}」在该字典下已存在")
                _assert_dict_item_unique(category_code, value=val, code=code_n)

                sql = """
                    INSERT INTO T_DICT_ITEM (
                        F_CATEGORY_CODE, F_CODE, F_VALUE, F_SORT, F_ENABLED,
                        F_IS_DEFAULT, F_REMARK
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """
                
                cur.execute(sql, (
                    category_code,
                    item_data.get("F_CODE"),
                    item_data.get("F_VALUE"),
                    item_data.get("F_SORT", 0),
                    item_data.get("F_ENABLED", 1),
                    item_data.get("F_IS_DEFAULT", 0),
                    item_data.get("F_REMARK")
                ))
                
                # 获取新插入的ID
                cur.execute("SELECT MAX(F_ID) FROM T_DICT_ITEM")
                new_id = cur.fetchone()[0]
                created_ids.append(int(new_id))
                seen_values.add(val)
                if code_n:
                    seen_codes.add(code_n.upper())
                
            except Exception as e:
                error_msg = f"第 {idx + 1} 项创建失败: {str(e)}"
                errors.append(error_msg)
                logger.error(error_msg)
        
        conn.commit()
    
    return {
        "success_count": len(created_ids),
        "failed_count": len(errors),
        "created_ids": created_ids,
        "errors": errors
    }


def batch_update_dict_items(items: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    批量更新字典项
    
    Args:
        items: 字典项列表，每项必须包含 F_ID
    
    Returns:
        {
            "success_count": 成功数量,
            "failed_count": 失败数量,
            "updated_ids": [更新的ID列表],
            "errors": [错误信息列表]
        }
    """
    adapter = get_adapter()
    
    updated_ids: List[int] = []
    errors: List[str] = []
    seen_values: Dict[str, set] = {}
    seen_codes: Dict[str, set] = {}
    
    with adapter.connect() as conn:
        cur = conn.cursor()
        
        for idx, item_data in enumerate(items):
            try:
                item_id = item_data.get("F_ID")
                if not item_id:
                    raise ValueError("缺少 F_ID 字段")
                
                # 检查是否存在
                existing = get_dict_item_by_id(item_id)
                if not existing:
                    raise ValueError(f"字典项ID {item_id} 不存在")

                cat = existing["F_CATEGORY_CODE"]
                next_value = item_data["F_VALUE"] if item_data.get("F_VALUE") is not None else existing.get("F_VALUE")
                next_code = item_data["F_CODE"] if item_data.get("F_CODE") is not None else existing.get("F_CODE")
                val = _norm_dict_text(next_value)
                code_n = _norm_dict_text(next_code)
                cat_values = seen_values.setdefault(cat, set())
                cat_codes = seen_codes.setdefault(cat, set())
                if val in cat_values:
                    raise ValueError(f"字段名称「{val}」在该字典下已存在")
                if code_n and code_n.upper() in cat_codes:
                    raise ValueError(f"字段编码「{code_n}」在该字典下已存在")
                _assert_dict_item_unique(
                    cat, value=val, code=code_n, exclude_id=int(item_id)
                )
                
                # 构建更新字段（排除 F_ID）
                updates: List[str] = []
                params: List[Any] = []
                
                for field in ["F_CODE", "F_VALUE", "F_SORT", "F_ENABLED", "F_IS_DEFAULT", "F_REMARK"]:
                    if field in item_data and item_data[field] is not None:
                        updates.append(f"{field} = ?")
                        params.append(item_data[field])
                
                if not updates:
                    # 没有需要更新的字段，跳过
                    continue
                
                # 添加更新时间
                updates.append(f"F_UPDATE_TIME = {adapter.now()}")
                
                params.append(item_id)
                sql = f"UPDATE T_DICT_ITEM SET {', '.join(updates)} WHERE F_ID = ?"
                
                cur.execute(sql, tuple(params))
                updated_ids.append(int(item_id))
                cat_values.add(val)
                if code_n:
                    cat_codes.add(code_n.upper())
                
            except Exception as e:
                error_msg = f"第 {idx + 1} 项更新失败: {str(e)}"
                errors.append(error_msg)
                logger.error(error_msg)
        
        conn.commit()
    
    return {
        "success_count": len(updated_ids),
        "failed_count": len(errors),
        "updated_ids": updated_ids,
        "errors": errors
    }
