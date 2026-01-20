import hashlib
from typing import Optional, Dict, Any, List, Iterable, Tuple
from app.db.factory import get_adapter
from app.db.adapters.dameng import DamengAdapter
from ..schemas.user import UserCreate, UserUpdate


def _rows_to_dicts(rows: List[tuple], cols: List[str]) -> List[Dict[str, Any]]:
    items = [dict(zip(cols, r)) for r in rows]
    return [_normalize_user_record(item) for item in items]


def _normalize_user_record(record: Dict[str, Any]) -> Dict[str, Any]:
    if record is None:
        return record
    if "F_Ktype" not in record or record.get("F_Ktype") is None:
        for key in ("F_Ktype", "F_KTYPE", "F_KType", "F_kTYPE"):
            if key in record and record[key] is not None:
                record["F_Ktype"] = record[key]
                break
    return record


def _name_column(adapter, quoted: bool = False) -> str:
    """返回列名（用于 SQL 拼接）。
    
    Args:
        adapter: 数据库适配器
        quoted: 是否在 SQL 中加引号（达梦需要，SQL Server 不需要）
    
    Returns:
        列名字符串（不含 Python 字符串的引号，而是SQL语法的引号）
    """
    if isinstance(adapter, DamengAdapter):
        # 达梦：区分大小写，需要双引号
        return '"F_Ktype"' if quoted else 'F_Ktype'
    # SQL Server：大小写不敏感，不需要引号
    return 'F_KTYPE'


def _execute_non_query(sql: str, params: Iterable[Any] | None = None) -> int:
    # 仅用于 INSERT/UPDATE/DELETE
    adapter = get_adapter()
    with adapter.connect() as conn:
        cur = conn.cursor()
        cur.execute(sql, tuple(params or []))
        affected = cur.rowcount
        conn.commit()
    return affected


def get_user(user_id: int) -> Optional[Dict[str, Any]]:
    adapter = get_adapter()
    col = _name_column(adapter, quoted=True)
    sql = f"""
    SELECT F_ID, F_YSM, {col} as F_Ktype, F_JS, F_YXQ, F_ZT, F_TOKEN
    FROM T_USER WHERE F_ID = ?
    """
    rows, cols = adapter.fetch_all(sql, [user_id])
    return _rows_to_dicts(rows, cols)[0] if rows else None


def get_user_by_code(code: str) -> Optional[Dict[str, Any]]:
    adapter = get_adapter()
    col = _name_column(adapter, quoted=True)
    sql = f"""
    SELECT F_ID, F_YSM, {col} AS F_Ktype, F_JS, F_YXQ, F_ZT, F_TOKEN
    FROM T_USER WHERE F_YSM = ?
    """
    rows, cols = adapter.fetch_all(sql, [code])
    return _rows_to_dicts(rows, cols)[0] if rows else None


def get_user_by_name(name: str, exclude_id: Optional[int] = None) -> Optional[Dict[str, Any]]:
    adapter = get_adapter()
    col = _name_column(adapter, quoted=True)
    sql = f"""
    SELECT F_ID, F_YSM, {col} AS F_Ktype, F_JS, F_YXQ, F_ZT, F_TOKEN
    FROM T_USER WHERE {col} = ?
    """
    params: List[Any] = [name]
    if exclude_id is not None:
        sql += " AND F_ID <> ?"
        params.append(exclude_id)
    rows, cols = get_adapter().fetch_all(sql, params)
    return _rows_to_dicts(rows, cols)[0] if rows else None


def list_users(skip: int = 0, limit: Optional[int] = 20, keyword: Optional[str] = None, status: Optional[str] = None, role: Optional[str] = None) -> List[Dict[str, Any]]:
    # 规范入参：空串按 None 处理
    kw = (keyword or "").strip() or None
    st = (status or "").strip() or None  # 允许"是/否"或不传

    # 获取数据库适配器
    adapter = get_adapter()
    
    where, params = [], []

    if kw:
        name_col = _name_column(adapter, quoted=True)
        where.append(f"(F_YSM LIKE ? OR {name_col} LIKE ?)")
        like = f"%{kw}%"
        params.extend([like, like])
    
    if role:
        pred, p = adapter.csv_contains("F_JS", role)
        where.append(pred)
        params.extend(p)

    if st:
        # “是/否”与 true/false、启用/停用、1/0、yes/no、y/n、ys/no 等写法
        st_norm = st.strip().lower()
        if st_norm in ("是", "true", "1", "yes", "y", "ys"):
            vals = ("true", "启用", "1", "yes", "y", "ys")
        elif st_norm in ("否", "false", "0", "no", "n"):
            vals = ("false", "停用", "0", "no", "n")
        else:
            vals = None  # 非法值等同于不筛选
        if vals:
            where.append("LOWER(COALESCE(F_ZT,'')) IN (" + ",".join(["?"] * len(vals)) + ")")
            params.extend([v.lower() for v in vals])
            
    where_sql = (" WHERE " + " AND ".join(where)) if where else ""
    col = _name_column(adapter, quoted=True)
    base_sql = f"""
    SELECT F_ID, F_YSM, {col} as F_Ktype, F_JS, F_YXQ, F_ZT, F_TOKEN
    FROM T_USER
    {where_sql}
    """
    sql, extra = adapter.paginate(base_sql, skip=skip, limit=limit)
    rows, cols = adapter.fetch_all(sql, params + extra)
    return _rows_to_dicts(rows, cols)

def update_user(user_id: int, data: UserUpdate) -> Optional[Dict[str, Any]]:
    # 动态构建可更新字段
    fields: Dict[str, Any] = {}
    if data.F_Ktype is not None:
        # 唯一性校验：姓名唯一（排除自身）
        if str(data.F_Ktype).strip():
            existed = get_user_by_name(str(data.F_Ktype).strip(), exclude_id=user_id)
            if existed:
                raise ValueError("姓名已存在")
        fields["F_Ktype"] = data.F_Ktype
    if data.F_JS is not None:
        fields["F_JS"] = ",".join([x.strip() for x in data.F_JS if str(x).strip()]) or None
    if data.F_YXQ is not None:
        fields["F_YXQ"] = data.F_YXQ
    if data.F_ZT is not None:
        fields["F_ZT"] = data.F_ZT
    if data.F_TOKEN is not None:
        fields["F_TOKEN"] = data.F_TOKEN

    if not fields:
        return get_user(user_id)

    sets = ", ".join([f"{k} = ?" for k in fields.keys()])
    params = list(fields.values()) + [user_id]
    sql = f"UPDATE T_USER SET {sets} WHERE F_ID = ?"
    _execute_non_query(sql, params)
    return get_user(user_id)


def delete_user(user_id: int) -> bool:
    sql = "DELETE FROM T_USER WHERE F_ID = ?"
    affected = _execute_non_query(sql, [user_id])
    return affected > 0


def reset_password(user_id: int) -> Optional[Dict[str, Any]]:
    # 默认重置为 123456
    hashed = _md5_hex("123456")
    sql = "UPDATE T_USER SET F_MM = '', F_MM2 = ? WHERE F_ID = ?"
    _execute_non_query(sql, [hashed, user_id])
    return get_user(user_id)


def _md5_hex(s: str) -> str:
    md5 = hashlib.md5()
    md5.update(s.encode("utf-8"))
    return md5.hexdigest()


def verify_password(plain: str, hashed_hex: str | None) -> bool:
    if not hashed_hex:
        return False
    return _md5_hex(plain) == hashed_hex


def get_user_for_auth(code: str) -> Optional[Dict[str, Any]]:
    """查询用于认证的最小字段（包含 F_MM2）。"""
    adapter = get_adapter()
    col = _name_column(adapter, quoted=True)
    sql = f"""
    SELECT F_ID, F_YSM, {col} as F_Ktype, F_JS, F_YXQ, F_ZT, F_TOKEN, F_MM2, F_YSM as F_NAME
    FROM T_USER WHERE F_YSM = ?
    """
    rows, cols = adapter.fetch_all(sql, [code])
    return _rows_to_dicts(rows, cols)[0] if rows else None


def create_user(data: UserCreate) -> Optional[Dict[str, Any]]:
    # 唯一性检查
    if get_user_by_code(data.F_YSM):
        return None
    if data.F_Ktype and get_user_by_name(data.F_Ktype):
        # 与路由约定：通过异常传递明确错误
        raise ValueError("姓名已存在")

    # 角色：列表 -> 逗号串
    js_str = ",".join([x.strip() for x in (data.F_JS or []) if str(x).strip()]) or None

    # 密码：只存加密，不落明文
    plain = data.F_MM or "123456"
    F_MM2 = _md5_hex(plain)

    # 组装入库字段（与表字段名一致）
    adapter = get_adapter()
    base_columns = [
        "F_YSM",
        "F_KTYPE",
        "F_JS",
        "F_YXQ",
        "F_ZT",
        "F_TOKEN",
        "F_MM",
        "F_MM2",
    ]
    values = [
        data.F_YSM,
        data.F_Ktype,
        js_str,
        data.F_YXQ,
        data.F_ZT or "true",
        data.F_TOKEN,
        "",
        F_MM2,
    ]

    col_names_parts: List[str] = []
    for col in base_columns:
        if col == "F_KTYPE":
            col_names_parts.append(_name_column(adapter, quoted=True))
        else:
            col_names_parts.append(col)

    placeholders = ", ".join(["?" for _ in base_columns])
    col_names = ", ".join(col_names_parts)
    sql = f"INSERT INTO T_USER ({col_names}) VALUES ({placeholders})"
    _execute_non_query(sql, values)

    return get_user_by_code(data.F_YSM)