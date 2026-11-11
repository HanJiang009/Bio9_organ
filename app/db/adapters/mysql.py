# app/db/adapters/mysql.py
import logging
import re
from typing import Iterable, Any, Tuple, Optional, List
from ..base import DatabaseAdapter
from ..sql_logger import LoggingConnection

logger = logging.getLogger(__name__)


_PAT_OFFSET_FETCH = re.compile(
    r'\bOFFSET\s+(\?|\d+)\s+ROWS\s+FETCH\s+NEXT\s+(\?|\d+)\s+ROWS\s+ONLY\b',
    re.IGNORECASE,
)
_PAT_FETCH_FIRST = re.compile(
    r'\bFETCH\s+FIRST\s+(\?|\d+)\s+ROWS\s+ONLY\b',
    re.IGNORECASE,
)


def _normalize_pagination(sql: str, params) -> tuple:
    """将 SQL Server 分页语法转换为 MySQL LIMIT/OFFSET，同时处理参数顺序。"""
    params_list: List = list(params) if params else []

    # FETCH FIRST n ROWS ONLY → LIMIT n
    def _repl_fetch_first(m):
        v = m.group(1)
        return f'LIMIT {v}' if v != '?' else 'LIMIT ?'
    sql = _PAT_FETCH_FIRST.sub(_repl_fetch_first, sql)

    # OFFSET x ROWS FETCH NEXT y ROWS ONLY → LIMIT y OFFSET x
    # 当两个都是 ? 时需要交换对应参数（SQL Server 顺序是 offset, limit；MySQL 是 limit, offset）
    q_positions = [m.start() for m in re.finditer(r'\?', sql)]
    result_parts = []
    last_end = 0
    for m in _PAT_OFFSET_FETCH.finditer(sql):
        result_parts.append(sql[last_end:m.start()])
        ov, lv = m.group(1), m.group(2)
        if ov == '?' and lv == '?':
            idxs = [i for i, p in enumerate(q_positions) if m.start() <= p <= m.end()]
            if len(idxs) >= 2 and idxs[0] < len(params_list) and idxs[1] < len(params_list):
                params_list[idxs[0]], params_list[idxs[1]] = params_list[idxs[1]], params_list[idxs[0]]
            result_parts.append('LIMIT ? OFFSET ?')
        elif ov == '?':
            result_parts.append(f'LIMIT {lv} OFFSET ?')
        elif lv == '?':
            result_parts.append(f'LIMIT ? OFFSET {ov}')
        else:
            result_parts.append(f'LIMIT {lv} OFFSET {ov}')
        last_end = m.end()
    if last_end > 0:
        result_parts.append(sql[last_end:])
        sql = ''.join(result_parts)

    return sql, (tuple(params_list) if params_list else params)


class _PlaceholderCursor:
    """将 SQL 中的 ? 占位符自动转换为 PyMySQL 的 %s 占位符，并规范化分页语法。"""

    def __init__(self, cursor):
        self._cursor = cursor

    @staticmethod
    def _convert(sql: str) -> str:
        return sql.replace("?", "%s")

    def execute(self, sql: str, params=None):
        sql, params = _normalize_pagination(sql, params)
        return self._cursor.execute(self._convert(sql), params)

    def executemany(self, sql: str, params_seq):
        return self._cursor.executemany(self._convert(sql), params_seq)

    def __getattr__(self, name):
        return getattr(self._cursor, name)

    def __iter__(self):
        return iter(self._cursor)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self._cursor.close()


class _PlaceholderConnection:
    """包装 PyMySQL 连接，使游标自动转换 ? 为 %s"""

    def __init__(self, conn):
        self._conn = conn

    def cursor(self):
        return _PlaceholderCursor(self._conn.cursor())

    def __getattr__(self, name):
        return getattr(self._conn, name)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if exc_type:
            self._conn.rollback()
        else:
            self._conn.commit()
        self._conn.close()


class MySQLAdapter(DatabaseAdapter):
    def __init__(self, conf: dict) -> None:
        self.conf = conf

    def build_conn_str(self) -> str:
        return ""

    def connect(self):
        try:
            import pymysql
        except ImportError as exc:
            raise ImportError(
                "需要安装 pymysql 才能连接 MySQL，请执行: pip install pymysql"
            ) from exc

        conn = pymysql.connect(
            host=self.conf["host"],
            port=int(self.conf.get("port", 3306)),
            user=self.conf["user"],
            password=self.conf["password"],
            database=self.conf["database"],
            charset=self.conf.get("charset", "utf8mb4"),
            autocommit=False,
        )

        try:
            from app.core.settings import settings
            if settings.sql_log:
                return LoggingConnection(_PlaceholderConnection(conn))
        except Exception:
            pass
        return _PlaceholderConnection(conn)

    @staticmethod
    def _to_pymysql_sql(sql: str) -> str:
        """将 ? 占位符转换为 PyMySQL 使用的 %s 占位符"""
        return sql.replace("?", "%s")

    def fetch_all(self, sql: str, params: Optional[Iterable[Any]] = None) -> Tuple[list[tuple], list[str]]:
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(self._to_pymysql_sql(sql), tuple(params or []))
            rows = cur.fetchall()
            cols = [d[0] for d in cur.description]
            return rows, cols

    # 方言方法
    def paginate(self, base_sql: str, skip: int, limit: int | None):
        sql_lower = (base_sql or "").lower()
        has_order = "order by" in sql_lower
        if limit and limit > 0:
            if has_order:
                return base_sql + " LIMIT %s OFFSET %s", [limit, skip]
            return base_sql + " ORDER BY F_ID LIMIT %s OFFSET %s", [limit, skip]
        return base_sql if has_order else base_sql + " ORDER BY F_ID", []

    def like(self, col: str, keyword: str):
        return f"{col} LIKE %s", [f"%{keyword}%"]

    def csv_contains(self, col: str, needle: str):
        # FIND_IN_SET 精确匹配逗号分隔列中的完整 token
        return f"FIND_IN_SET(%s, {col}) > 0", [needle]

    def limit(self, base_sql: str, limit: int, offset: int = 0):
        sql = base_sql
        params: list[Any] = []
        if "order by" not in (base_sql or "").lower():
            sql += " ORDER BY F_ID"
        sql += " LIMIT %s OFFSET %s"
        params.extend([limit, offset])
        return sql, params

    def column_exists(self, cursor, table: str, column: str) -> bool:
        try:
            cursor.execute(
                "SELECT 1 FROM INFORMATION_SCHEMA.COLUMNS "
                "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s AND COLUMN_NAME = %s",
                (table, column),
            )
            return cursor.fetchone() is not None
        except Exception:
            return False

    def subquery_limit1(self, select_expr: str, from_clause: str, where_clause: str, order_by: str) -> str:
        return f"SELECT {select_expr} FROM {from_clause} WHERE {where_clause} ORDER BY {order_by} LIMIT 1"

    def now(self) -> str:
        return "NOW()"

    def last_insert_id(self, cursor) -> int | None:
        try:
            cursor.execute("SELECT LAST_INSERT_ID()")
            row = cursor.fetchone()
            if row and row[0] is not None:
                return int(row[0])
        except Exception:
            pass
        return None
