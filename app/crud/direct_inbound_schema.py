"""直接入库用到的表和管子字段。第一次调用时按当前数据库补齐。"""
from app.db.factory import get_adapter
from app.db.adapters.dameng import DamengAdapter


_ready = False

_DIM_MAX = 50
_SLOT_MAX = 5000


def dialect_name(adapter=None) -> str:
    adapter = adapter or get_adapter()
    if isinstance(adapter, DamengAdapter):
        return "dameng"
    name = type(adapter).__name__.lower()
    if "sqlserver" in name:
        return "sqlserver"
    return "mysql"


def ensure_schema() -> None:
    """T_RQ / T_RQ_HW / T_CRKSH / T_CRKSH_CFG，以及 T_YBFZ 上的容器位置和出库锁。"""
    global _ready
    if _ready:
        return
    adapter = get_adapter()
    kind = dialect_name(adapter)
    with adapter.connect() as conn:
        cur = conn.cursor()
        _ensure_container_tables(adapter, cur, kind)
        _ensure_audit_tables(adapter, cur, kind)
        _ensure_tube_columns(adapter, cur, kind)
        conn.commit()
    _ready = True


def _ensure_container_tables(adapter, cur, kind: str) -> None:
        if not adapter.column_exists(cur, "T_RQ", "F_ID"):
            cur.execute(_create_sql(kind, "T_RQ", """
            F_RQBH VARCHAR(64),
            F_RQMC VARCHAR(200),
            F_RQLB VARCHAR(64),
            F_CCWD VARCHAR(64),
            F_CS INT,
            F_JS INT,
            F_CTS INT,
            F_HXS INT,
            F_ZT VARCHAR(16),
            F_CJSJ VARCHAR(32),
            F_BZ VARCHAR(500)
        """))
        text_type = {
            "mysql": "VARCHAR(64) NULL",
            "sqlserver": "NVARCHAR(64) NULL",
            "dameng": "VARCHAR(64)",
        }[kind]
        _add_column(adapter, cur, kind, "T_RQ", "F_RQLB", text_type)
        if not adapter.column_exists(cur, "T_RQ_C", "F_ID"):
            cur.execute(_create_sql(kind, "T_RQ_C", """
            F_RQ_ID INT,
            F_XH INT,
            F_CMC VARCHAR(64),
            F_JS INT,
            F_CTS INT,
            F_HXS INT,
            F_HS INT,
            F_LS INT
        """))
        if not adapter.column_exists(cur, "T_RQ_HW", "F_ID"):
            cur.execute(_create_sql(kind, "T_RQ_HW", """
            F_RQ_ID INT,
            F_C INT,
            F_J INT,
            F_CT INT,
            F_H INT
        """))


def _ensure_audit_tables(adapter, cur, kind: str) -> None:
    if kind == "mysql":
        detail = "TEXT"
        flag = "VARCHAR(8)"
    elif kind == "sqlserver":
        detail = "NVARCHAR(MAX)"
        flag = "NVARCHAR(8)"
    else:
        detail = "CLOB"
        flag = "VARCHAR(8)"
    if not adapter.column_exists(cur, "T_CRKSH", "F_ID"):
        cur.execute(_create_sql(kind, "T_CRKSH", f"""
            F_DJLX VARCHAR(16),
            F_DJID INT,
            F_DJH VARCHAR(64),
            F_HH VARCHAR(1000),
            F_YBSL INT,
            F_SQR VARCHAR(64),
            F_SQSJ VARCHAR(32),
            F_SHZT VARCHAR(16),
            F_SHR VARCHAR(64),
            F_SHSJ VARCHAR(32),
            F_SHBZ VARCHAR(1000),
            F_DETAIL {detail}
        """))
    if not adapter.column_exists(cur, "T_CRKSH_CFG", "F_ID"):
        cur.execute(_create_sql(kind, "T_CRKSH_CFG", f"""
            F_CKSH {flag},
            F_RKSH {flag}
        """))


def _ensure_tube_columns(adapter, cur, kind: str) -> None:
    int_type = "INT NULL" if kind != "dameng" else "INT"
    text_type = {
        "mysql": "VARCHAR(300) NULL",
        "sqlserver": "NVARCHAR(300) NULL",
        "dameng": "VARCHAR(300)",
    }[kind]
    for column in ("F_RQ_ID", "F_RQC", "F_RQJ", "F_RQCT", "F_RQH", "F_LOCK_CKD_ID"):
        _add_column(adapter, cur, kind, "T_YBFZ", column, int_type)
    _add_column(adapter, cur, kind, "T_YBFZ", "F_CCWZ", text_type)


def _create_sql(kind: str, table: str, columns: str) -> str:
    if kind == "mysql":
        pk = "F_ID INT AUTO_INCREMENT PRIMARY KEY"
        return (
            f"CREATE TABLE IF NOT EXISTS {table} ({pk}, {columns}) "
            "ENGINE=InnoDB DEFAULT CHARSET=utf8mb4"
        )
    pk = "F_ID INT IDENTITY(1,1) PRIMARY KEY"
    return f"CREATE TABLE {table} ({pk}, {columns})"


def _add_column(adapter, cur, kind: str, table: str, column: str, col_type: str) -> None:
    if adapter.column_exists(cur, table, column):
        return
    if kind == "mysql":
        cur.execute(f"ALTER TABLE {table} ADD COLUMN {column} {col_type}")
    else:
        cur.execute(f"ALTER TABLE {table} ADD {column} {col_type}")


def parse_dim(value, label: str) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        raise ValueError(f"{label}必须是整数")
    if number < 1 or number > _DIM_MAX:
        raise ValueError(f"{label}须在 1 到 {_DIM_MAX} 之间")
    return number


def assert_slot_count(layers: int, racks: int, drawers: int, boxes: int) -> int:
    total = layers * racks * drawers * boxes
    if total > _SLOT_MAX:
        raise ValueError(f"盒位总数不能超过 {_SLOT_MAX}，当前为 {total}")
    return total


def format_ccwz(code: str, layer: int, rack: int, drawer: int, box: int) -> str:
    return f"{code} / {layer}层 / {rack}架 / {drawer}抽屉 / {box}盒"


def lock_id(value) -> int | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number or None
