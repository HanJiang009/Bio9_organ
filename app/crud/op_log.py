from datetime import datetime
from typing import Optional, Dict, Any, List
import json
import sys
from pathlib import Path
from app.db.factory import get_adapter
from app.schemas.op_log import OpLogQuery


_ALIAS_MAP: Dict[str, str] = {
    # 路由前缀 -> 权限Key
    "users": "user_mgmt",
    "user": "user_mgmt",
    "roles": "role_mgmt",
    "role": "role_mgmt",
    "rzgl": "op_log",
    "op_log": "op_log",
    "sample-receive": "sample_receive",
    "sample_receive": "sample_receive",
}


def _load_permission_labels() -> Dict[str, Any]:
    """加载权限标签配置，支持 PyInstaller 打包环境"""
    try:
        # 统一从程序根目录的 config/ 读取
        if getattr(sys, 'frozen', False):
            # 打包后：从可执行文件同级的 config/ 目录读取
            base_dir = Path(sys.executable).parent
        else:
            # 开发环境：app/crud/op_log.py -> parents[1] 是 app 目录
            base_dir = Path(__file__).resolve().parents[1]
        
        p = base_dir / "config" / "permission_labels.json"
        data = json.loads(p.read_text(encoding='utf-8-sig'))
        return data if isinstance(data, dict) else {"labels": {}}
    except Exception:
        return {"labels": {}}


def _normalize_module_key(module: str) -> str:
    if not module:
        return module
    key = module.strip("/").lower()
    return _ALIAS_MAP.get(key, key)


def _module_to_label(module: str) -> str:
    labels = (_load_permission_labels().get("labels") or {})
    # 先将路由前缀规范为权限Key
    norm = _normalize_module_key(module)
    # 若是权限Key，转中文；若传入已是中文或未知，原样返回
    if norm in labels:
        return labels[norm]
    if module in labels.values():
        return module
    return module


def insert_op_log(module: str, op_type: str, operator: Optional[str], status: str, detail: Dict[str, Any] | str, operator_code: Optional[str] = None) -> None:
    """写入操作日志到 T_OP_LOG。

    字段约定：
    - F_MK: 模块标识
    - F_CZLX: 操作类型（如 GET/POST/PUT/DELETE 或自定义）
    - F_CZR: 操作人（优先取 token.code）
    - F_CZSJ: 操作时间（当前时间）
    - F_ZT: 状态（success/fail/error 或状态码）
    - F_CZXQ: 详情（建议使用可读文本，也允许传入字典）
    - F_YSM: 操作人工号
    """
    adapter = get_adapter()
    with adapter.connect() as conn:
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO T_OP_LOG (F_MK, F_CZLX, F_CZR, F_CZSJ, F_ZT, F_CZXQ, F_YSM) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                _module_to_label(module),
                op_type,
                operator or "",
                datetime.now(),
                status,
                (detail if isinstance(detail, str) else json.dumps(detail, ensure_ascii=False)),
                operator_code or "",
            ),
        )
        conn.commit()

def list_op_logs(q: OpLogQuery, user: Dict[str, Any] | None = None,
                scope: str | None = None) -> Dict[str, Any]:
    adapter = get_adapter()
    where_clauses: List[str] = []
    params: list = []

    if q.F_MK:
        mk_raw = q.F_MK
        mk_label = _module_to_label(mk_raw)
        pred1, p1 = adapter.like("F_MK", mk_label)
        pred2, p2 = adapter.like("F_MK", mk_raw)
        if mk_label != mk_raw:
            where_clauses.append(f"({pred1} OR {pred2})")
            params.extend(p1 + p2)
        else:
            where_clauses.append(pred1)
            params.extend(p1)
    if q.F_CZLX:
        pred, p = adapter.like("F_CZLX", q.F_CZLX)
        where_clauses.append(pred)
        params.extend(p)
    if q.F_CZR:
        pred, p = adapter.like("F_CZR", q.F_CZR)
        where_clauses.append(pred)
        params.extend(p)
    if q.F_YSM:
        pred, p = adapter.like("F_YSM", q.F_YSM)
        where_clauses.append(pred)
        params.extend(p)

    if q.F_CZSJ:
        where_clauses.append("F_CZSJ = ?")
        params.append(q.F_CZSJ)
    if getattr(q, "F_CZSJ_START", None):
        where_clauses.append("F_CZSJ >= ?")
        params.append(q.F_CZSJ_START)
    if getattr(q, "F_CZSJ_END", None):
        where_clauses.append("F_CZSJ <= ?")
        params.append(q.F_CZSJ_END)

    from app.core.data_scope import apply_owner_or_filter
    apply_owner_or_filter(
        where_clauses, params, scope=scope, user=user, columns=["F_CZR", "F_YSM"], match="name",
    )

    where_sql = (" WHERE " + " AND ".join(where_clauses)) if where_clauses else ""

    # 统计总数
    count_sql = f"SELECT COUNT(1) AS cnt FROM T_OP_LOG{where_sql}"

    # 查询数据（倒序 + 分页）
    select_base = f"SELECT F_MK, F_CZLX, F_CZR, F_CZSJ, F_ZT, F_CZXQ, F_YSM FROM T_OP_LOG{where_sql} ORDER BY F_CZSJ DESC"
    sql, extra = adapter.paginate(select_base, skip=q.skip or 0, limit=q.limit)

    with adapter.connect() as conn:
        cur = conn.cursor()
        # total
        cur.execute(count_sql, tuple(params))
        cnt_row = cur.fetchone()
        total = int(cnt_row[0]) if cnt_row else 0
        # items
        cur.execute(sql, tuple(params + extra))
        rows = cur.fetchall()
        cols = [c[0] for c in cur.description]
        items = [dict(zip(cols, r)) for r in rows]
        return {"items": items, "total": total}