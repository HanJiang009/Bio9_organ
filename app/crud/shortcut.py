import json
from typing import Optional, Dict, Any, List
from app.db.factory import get_adapter
from app.schemas.shortcut import ShortcutCreate, ShortcutUpdate


def _rows_to_dicts(rows: List[tuple], cols: List[str]) -> List[Dict[str, Any]]:
    return [dict(zip(cols, r)) for r in rows]


def get_shortcut_by_user(user_id: int) -> Optional[Dict[str, Any]]:
    rows, cols = get_adapter().fetch_all("SELECT * FROM T_KJJ WHERE F_UID = ?", [user_id])
    return _rows_to_dicts(rows, cols)[0] if rows else None


def get_shortcut(shortcut_id: int) -> Optional[Dict[str, Any]]:
    rows, cols = get_adapter().fetch_all("SELECT * FROM T_KJJ WHERE F_ID = ?", [shortcut_id])
    return _rows_to_dicts(rows, cols)[0] if rows else None


def save_shortcut(user_id: int, shortcuts_json: str) -> Optional[Dict[str, Any]]:
    # 验证JSON
    try:
        json.loads(shortcuts_json)
    except Exception:
        return None
    existing = get_shortcut_by_user(user_id)
    adapter = get_adapter()
    with adapter.connect() as conn:
        cur = conn.cursor()
        if existing:
            cur.execute("UPDATE T_KJJ SET F_KJJS = ? WHERE F_UID = ?", (shortcuts_json, user_id))
        else:
            cur.execute("INSERT INTO T_KJJ (F_UID, F_KJJS) VALUES (?, ?)", (user_id, shortcuts_json))
        conn.commit()
    return get_shortcut_by_user(user_id)


def list_shortcuts(skip: int = 0, limit: int = 20) -> List[Dict[str, Any]]:
    rows, cols = get_adapter().fetch_all("SELECT * FROM T_KJJ ORDER BY F_ID LIMIT ? OFFSET ?", [limit, skip])
    return _rows_to_dicts(rows, cols)



def list_enabled_shortcuts_by_user(user_id: int) -> List[Dict[str, Any]]:
    """返回用户开启的快捷键列表。

    兼容两种存储结构：
    1) 列表：[{ id,label?,icon?, enabled/shortcut: bool, ... }]
       - 过滤 enabled=True 或 shortcut=True 的项
    2) 映射：{ id: true/false, ... }
       - 取值为真者，返回 {id: <key>} 列表
    其他情况：返回空列表
    """
    rec = get_shortcut_by_user(user_id)
    if not rec:
        return []
    raw = rec.get("F_KJJS")
    if raw is None:
        return []
    try:
        data = raw if isinstance(raw, (list, dict)) else json.loads(raw)
    except Exception:
        return []

    result: List[Dict[str, Any]] = []
    if isinstance(data, list):
        for item in data:
            if not isinstance(item, dict):
                continue
            flag = bool(item.get("enabled") or item.get("shortcut"))
            if flag:
                result.append(item)
        return result
    if isinstance(data, dict):
        for k, v in data.items():
            try:
                if bool(v):
                    result.append({"id": k})
            except Exception:
                continue
        return result
    return []

