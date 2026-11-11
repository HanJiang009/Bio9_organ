# app/db/adapters/sqlserver.py
import logging
from typing import Any
import pyodbc
from ..base import DatabaseAdapter
from ..sql_logger import LoggingConnection

logger = logging.getLogger(__name__)

class SqlServerAdapter(DatabaseAdapter):
    def __init__(self, conf: dict) -> None:
        self.conf = conf
    def build_conn_str(self) -> str:
        c = self.conf
        parts = [
            f"DRIVER={{{c.get('driver','ODBC Driver 18 for SQL Server')}}}",
            f"SERVER={c['server']},{int(c.get('port',1433))}",
            f"DATABASE={c['database']}",
            f"UID={c['username']}",
            f"PWD={c['password']}",
            f"Encrypt={c.get('encrypt','no')}",
            f"TrustServerCertificate={'yes' if c.get('trust_server_certificate', True) else 'no'}",
            f"Timeout={int(c.get('timeout',30))}",
        ]
        return ";".join(parts)
    def connect(self):
        """返回数据库连接，如果启用 SQL 日志则返回包装后的连接"""
        conn = pyodbc.connect(self.build_conn_str())
        # 检查是否启用 SQL 日志
        try:
            from app.core.settings import settings
            if settings.sql_log:
                return LoggingConnection(conn)
        except Exception:
            pass
        return conn
    def fetch_all(self, sql, params=None):
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, params or [])
            rows = cur.fetchall()
            cols = [c[0] for c in cur.description]
            return rows, cols

    # 方言方法
    def paginate(self, base_sql: str, skip: int, limit: int | None):
        sql_lower = (base_sql or "").lower()
        has_order = "order by" in sql_lower
        if limit and limit > 0:
            if has_order:
                return base_sql + " OFFSET ? ROWS FETCH NEXT ? ROWS ONLY", [skip, limit]
            return base_sql + " ORDER BY F_ID OFFSET ? ROWS FETCH NEXT ? ROWS ONLY", [skip, limit]
        return base_sql if has_order else base_sql + " ORDER BY F_ID", []

    def like(self, col: str, keyword: str):
        return f"{col} LIKE ?", [f"%{keyword}%"]

    def csv_contains(self, col: str, needle: str):
        return f"(','+COALESCE({col},'')+',') LIKE ?", [f"%,{needle},%"]

    def limit(self, base_sql: str, limit: int, offset: int = 0):
        sql = base_sql
        params: list[Any] = []
        if "order by" not in (base_sql or "").lower():
            sql += " ORDER BY F_ID"
        sql += " OFFSET ? ROWS"
        params.append(offset)
        if limit >= 0:
            sql += " FETCH NEXT ? ROWS ONLY"
            params.append(limit)
        return sql, params

    def column_exists(self, cursor, table: str, column: str) -> bool:
        try:
            cursor.execute(
                "SELECT 1 FROM INFORMATION_SCHEMA.COLUMNS WHERE TABLE_NAME = ? AND COLUMN_NAME = ?",
                (table, column),
            )
            return cursor.fetchone() is not None
        except Exception:
            try:
                cursor.execute("SELECT COL_LENGTH(?, ?)", (table, column))
                return cursor.fetchone()[0] is not None
            except Exception:
                return False

    def subquery_limit1(self, select_expr: str, from_clause: str, where_clause: str, order_by: str) -> str:
        """
        生成取第一条记录的子查询 SQL (SQL Server 语法)
        
        Args:
            select_expr: SELECT 子句的字段表达式,如 "z.F_SHZT"
            from_clause: FROM 子句,如 "T_ZKSH z"
            where_clause: WHERE 子句,如 "z.F_GDBH = p.F_GDBH"
            order_by: ORDER BY 子句,如 "z.F_ID DESC"
            
        Returns:
            完整的子查询 SQL (单行格式,避免嵌套 f-string 问题)
        """
        return f"SELECT {select_expr} FROM {from_clause} WHERE {where_clause} ORDER BY {order_by} OFFSET 0 ROWS FETCH NEXT 1 ROW ONLY"

    def now(self) -> str:
        return "GETDATE()"

    def last_insert_id(self, cursor) -> int | None:
        try:
            cursor.execute("SELECT SCOPE_IDENTITY()")
            row = cursor.fetchone()
            if row and row[0] is not None:
                return int(row[0])
        except Exception:
            pass
        try:
            cursor.execute("SELECT @@IDENTITY")
            row = cursor.fetchone()
            if row and row[0] is not None:
                return int(row[0])
        except Exception:
            pass
        return None
