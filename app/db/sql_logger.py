"""SQL 执行日志包装器：用于调试时记录所有 SQL 语句"""
import logging
import time
from typing import Any

logger = logging.getLogger("sql")


class LoggingCursor:
    """数据库游标包装器，记录所有 SQL 执行"""
    
    def __init__(self, cursor, conn):
        self._cursor = cursor
        self._conn = conn
    
    def execute(self, sql: str, params: Any = None):
        """执行 SQL 并记录日志"""
        start_time = time.time()
        try:
            # 格式化参数用于日志显示
            params_str = ""
            if params:
                if isinstance(params, (list, tuple)):
                    # 限制参数长度，避免日志过长
                    params_display = []
                    for p in params:
                        if isinstance(p, str) and len(p) > 100:
                            params_display.append(f"{p[:100]}...")
                        elif isinstance(p, bytes):
                            params_display.append(f"<bytes:{len(p)}>")
                        else:
                            params_display.append(repr(p))
                    params_str = f" | params: [{', '.join(params_display)}]"
                else:
                    params_str = f" | params: {params}"
            
            # 执行 SQL
            result = self._cursor.execute(sql, params) if params is not None else self._cursor.execute(sql)
            
            # 计算执行时间
            elapsed_ms = (time.time() - start_time) * 1000
            
            # 记录日志（INFO 级别）
            logger.info(f"SQL executed in {elapsed_ms:.2f}ms | {sql}{params_str}")
            
            return result
        except Exception as e:
            # 错误时也记录 SQL
            elapsed_ms = (time.time() - start_time) * 1000
            logger.error(f"SQL failed in {elapsed_ms:.2f}ms | {sql} | error: {e}")
            raise
    
    def executemany(self, sql: str, params_seq):
        """批量执行 SQL 并记录日志"""
        start_time = time.time()
        try:
            count = len(params_seq) if hasattr(params_seq, '__len__') else '?'
            result = self._cursor.executemany(sql, params_seq)
            elapsed_ms = (time.time() - start_time) * 1000
            logger.info(f"SQL executemany ({count} rows) in {elapsed_ms:.2f}ms | {sql}")
            return result
        except Exception as e:
            elapsed_ms = (time.time() - start_time) * 1000
            logger.error(f"SQL executemany failed in {elapsed_ms:.2f}ms | {sql} | error: {e}")
            raise
    
    def fetchone(self):
        """获取一行数据"""
        return self._cursor.fetchone()
    
    def fetchall(self):
        """获取所有数据"""
        return self._cursor.fetchall()
    
    def fetchmany(self, size=None):
        """获取指定数量的数据"""
        if size is None:
            return self._cursor.fetchmany()
        return self._cursor.fetchmany(size)
    
    @property
    def description(self):
        """列描述信息"""
        return self._cursor.description
    
    @property
    def rowcount(self):
        """影响的行数"""
        return self._cursor.rowcount
    
    def close(self):
        """关闭游标"""
        return self._cursor.close()
    
    def __enter__(self):
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
    
    def __iter__(self):
        return iter(self._cursor)
    
    def __getattr__(self, name):
        """转发其他属性和方法到原始游标"""
        return getattr(self._cursor, name)


class LoggingConnection:
    """数据库连接包装器，返回带日志功能的游标"""
    
    def __init__(self, conn):
        self._conn = conn
    
    def cursor(self):
        """返回带日志功能的游标"""
        return LoggingCursor(self._conn.cursor(), self._conn)
    
    def commit(self):
        """提交事务"""
        return self._conn.commit()
    
    def rollback(self):
        """回滚事务"""
        return self._conn.rollback()
    
    def close(self):
        """关闭连接"""
        return self._conn.close()
    
    def __enter__(self):
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        if exc_type:
            self._conn.rollback()
        else:
            self._conn.commit()
        self._conn.close()
    
    def __getattr__(self, name):
        """转发其他属性和方法到原始连接"""
        return getattr(self._conn, name)

