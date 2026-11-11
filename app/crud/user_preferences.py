from typing import Any, Dict, Optional
import json
from app.db.factory import get_adapter


def get_preference(user_id: int, page_key: str) -> Optional[Dict[str, Any]]:
    adapter = get_adapter()
    sql = "SELECT F_CONFIG FROM T_USER_PREFERENCES WHERE F_USER_ID = ? AND F_PAGE_KEY = ?"
    rows, _ = adapter.fetch_all(sql, [user_id, page_key])
    if not rows:
        return None
    raw = rows[0][0]
    if not raw:
        return None
    try:
        return json.loads(raw)
    except Exception:
        return None


def save_preference(user_id: int, page_key: str, config: Any) -> None:
    config_str = json.dumps(config, ensure_ascii=False)
    adapter = get_adapter()

    check_sql = "SELECT F_ID FROM T_USER_PREFERENCES WHERE F_USER_ID = ? AND F_PAGE_KEY = ?"
    rows, _ = adapter.fetch_all(check_sql, [user_id, page_key])

    with adapter.connect() as conn:
        cur = conn.cursor()
        if rows:
            cur.execute(
                "UPDATE T_USER_PREFERENCES SET F_CONFIG = ? WHERE F_USER_ID = ? AND F_PAGE_KEY = ?",
                (config_str, user_id, page_key),
            )
        else:
            cur.execute(
                "INSERT INTO T_USER_PREFERENCES (F_USER_ID, F_PAGE_KEY, F_CONFIG) VALUES (?, ?, ?)",
                (user_id, page_key, config_str),
            )
        conn.commit()
