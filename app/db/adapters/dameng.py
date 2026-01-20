# app/db/adapters/dameng.py
import logging
import os
from pathlib import Path
from typing import Iterable, Any, Tuple, Optional
from ..base import DatabaseAdapter
from ..sql_logger import LoggingConnection

logger = logging.getLogger(__name__)
_dm = None


def _ensure_dm_python():
    """惰性加载 dmPython，并在导入前尝试添加达梦客户端 DLL 路径。"""
    global _dm
    if _dm is not None:
        return _dm

    # 优先读取环境变量 DM_CLIENT_DIR 或 DM_HOME，尝试添加到 DLL 搜索路径
    dll_dir = os.environ.get("DM_CLIENT_DIR") or os.environ.get("DM_HOME")
    if dll_dir:
        try:
            Path(dll_dir).expanduser().resolve()
            os.add_dll_directory(dll_dir)
        except Exception as exc:  # noqa: BLE001
            logger.warning("add_dll_directory failed for %s: %s", dll_dir, exc)

    try:
        import dmPython as dm  # type: ignore
    except Exception as exc:  # noqa: BLE001
        logger.error("导入 dmPython 失败，请确认已安装对应版本并配置达梦客户端 DLL 路径 (DM_CLIENT_DIR/DM_HOME): %s", exc)
        raise
    _dm = dm
    return _dm

class DamengAdapter(DatabaseAdapter):
    def __init__(self, conf: dict) -> None:
        self.conf = conf

    def build_conn_str(self) -> str:
        # dmPython 不使用连接串
        return ""

    def connect(self):
        dmPython = _ensure_dm_python()
        server = f"{self.conf['host']}:{int(self.conf.get('port', 5236))}"
        conn = dmPython.connect(
            user=self.conf["user"],
            password=self.conf["password"],
            server=server,
        )
        schema = self.conf.get("schema") or self.conf.get("database")
        if schema:
            cur = conn.cursor()
            schema_name = str(schema).strip()
            if schema_name:
                attempts = []
                if schema_name.startswith('"') and schema_name.endswith('"'):
                    attempts.append(schema_name)
                else:
                    attempts.append(schema_name)
                    attempts.append(schema_name.upper())
                    attempts.append(f'"{schema_name}"')
                    attempts.append(f'"{schema_name.upper()}"')
                set_ok = False
                for name in attempts:
                    try:
                        cur.execute(f"set schema {name}")
                        set_ok = True
                        break
                    except Exception:
                        continue
                if not set_ok:
                    logger.warning("DamengAdapter: failed to set schema, tried %s", attempts)
            cur.close()
        
        # 检查是否启用 SQL 日志
        try:
            from app.core.settings import settings
            if settings.sql_log:
                return LoggingConnection(conn)
        except Exception:
            pass
        return conn

    def fetch_all(self, sql: str, params: Optional[Iterable[Any]] = None) -> Tuple[list[tuple], list[str]]:
        conn = self.connect()
        cur = conn.cursor()
        try:
            cur.execute(sql, tuple(params or []))
            rows = cur.fetchall()
            cols = [d[0] for d in cur.description]
            return rows, cols
        finally:
            cur.close()
            conn.close()

    # 方言方法
    def paginate(self, base_sql: str, skip: int, limit: int | None):
        sql_lower = (base_sql or "").lower()
        has_order = "order by" in sql_lower
        if limit and limit > 0:
            if has_order:
                return base_sql + " LIMIT ? OFFSET ?", [limit, skip]
            return base_sql + " ORDER BY F_ID LIMIT ? OFFSET ?", [limit, skip]
        return base_sql if has_order else base_sql + " ORDER BY F_ID", []

    def like(self, col: str, keyword: str):
        return f"{col} LIKE ?", [f"%{keyword}%"]

    def csv_contains(self, col: str, needle: str):
        return f"(','||COALESCE({col},'')||',') LIKE ?", [f"%,{needle},%"]

    def limit(self, base_sql: str, limit: int, offset: int = 0):
        sql = base_sql
        params: list[Any] = []
        if "order by" not in (base_sql or "").lower():
            sql += " ORDER BY F_ID"
        sql += " LIMIT ? OFFSET ?"
        params.extend([limit, offset])
        return sql, params

    def column_exists(self, cursor, table: str, column: str) -> bool:
        try:
            cursor.execute(
                "SELECT 1 FROM ALL_TAB_COLUMNS WHERE TABLE_NAME = ? AND COLUMN_NAME = ?",
                (table.upper(), column.upper()),
            )
            return cursor.fetchone() is not None
        except Exception:
            return False

    def subquery_limit1(self, select_expr: str, from_clause: str, where_clause: str, order_by: str) -> str:
        """
        生成取第一条记录的子查询 SQL (达梦数据库语法)
        
        Args:
            select_expr: SELECT 子句的字段表达式,如 "z.F_SHZT"
            from_clause: FROM 子句,如 "T_ZKSH z"
            where_clause: WHERE 子句,如 "z.F_GDBH = p.F_GDBH"
            order_by: ORDER BY 子句,如 "z.F_ID DESC"
            
        Returns:
            完整的子查询 SQL (单行格式,避免嵌套 f-string 问题)
        """
        return f"SELECT {select_expr} FROM {from_clause} WHERE {where_clause} ORDER BY {order_by} LIMIT 1"