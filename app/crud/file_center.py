from typing import Optional, Dict, Any
from app.db.factory import get_adapter
from pathlib import Path
from app.core.settings import settings


def get_file_by_id(file_id: int) -> Optional[Dict[str, Any]]:
    """从中心文件表 T_FILES 读取文件信息。

    期望字段：F_ID, F_PATH, F_ORIGINAL_NAME（若不存在某字段，将忽略）。
    返回：{"F_ID": id, "F_PATH": path, "F_ORIGINAL_NAME": name} 或 None
    """
    adapter = get_adapter()
    with adapter.connect() as conn:
        cur = conn.cursor()
        try:
            cur.execute("SELECT F_ID, F_PATH, F_ORIGINAL_NAME FROM T_FILES WHERE F_ID = ?", (file_id,))
            row = cur.fetchone()
            if not row:
                return None
            cols = [c[0] for c in cur.description]
            return dict(zip(cols, row))
        except Exception:
            # 兼容部分中心表字段差异（若缺少 F_ORIGINAL_NAME 列）
            try:
                cur.execute("SELECT F_ID, F_PATH FROM T_FILES WHERE F_ID = ?", (file_id,))
                row = cur.fetchone()
                if not row:
                    return None
                cols = [c[0] for c in cur.description]
                return dict(zip(cols, row))
            except Exception:
                return None


def insert_file_record(path: str, original_name: str) -> Optional[int]:
    """向 T_FILES 写入一条记录，返回新文件的 F_ID。

    为兼容不同库的列设计，按多种列组合依次尝试插入：
    1) (F_PATH, F_ORIGINAL_NAME)
    2) (F_TYPE, F_BUSINESS_ID, F_DEVICE_ID, F_PATH, F_ORIGINAL_NAME)
    3) (F_TYPE, F_PATH, F_ORIGINAL_NAME)
    4) (F_PATH)
    插入成功后，使用 F_PATH 定位最新一条的 F_ID。
    """
    adapter = get_adapter()
    with adapter.connect() as conn:
        cur = conn.cursor()
        tried = False
        # 1) 仅路径+原名
        try:
            cur.execute(
                "INSERT INTO T_FILES (F_PATH, F_ORIGINAL_NAME) VALUES (?, ?)",
                (path, original_name),
            )
            tried = True
        except Exception:
            # 2) 带类型/业务/设备占位
            try:
                cur.execute(
                    "INSERT INTO T_FILES (F_TYPE, F_BUSINESS_ID, F_DEVICE_ID, F_PATH, F_ORIGINAL_NAME) VALUES (?, ?, ?, ?, ?)",
                    ("sample_receive", 0, 0, path, original_name),
                )
                tried = True
            except Exception:
                # 3) 类型+路径+原名
                try:
                    cur.execute(
                        "INSERT INTO T_FILES (F_TYPE, F_PATH, F_ORIGINAL_NAME) VALUES (?, ?, ?)",
                        ("sample_receive", path, original_name),
                    )
                    tried = True
                except Exception:
                    # 4) 仅路径
                    try:
                        cur.execute(
                            "INSERT INTO T_FILES (F_PATH) VALUES (?)",
                            (path,),
                        )
                        tried = True
                    except Exception:
                        return None

        if not tried:
            return None
        # 获取新ID（按路径取最大ID，避免跨库不兼容）
        try:
            cur.execute("SELECT MAX(F_ID) FROM T_FILES WHERE F_PATH = ?", (path,))
            new_id = cur.fetchone()[0]
        except Exception:
            new_id = None
        conn.commit()
        return int(new_id) if new_id is not None else None


def get_files_by_ids(ids: list[int]) -> list[Dict[str, Any]]:
    """批量按 F_ID 获取 T_FILES 记录，仅返回存在的记录，字段含 F_ID/F_PATH/F_ORIGINAL_NAME（若可用）。"""
    if not ids:
        return []
    adapter = get_adapter()
    with adapter.connect() as conn:
        cur = conn.cursor()
        # 尝试完整字段
        try:
            q_marks = ",".join(["?"] * len(ids))
            cur.execute(f"SELECT F_ID, F_PATH, F_ORIGINAL_NAME FROM T_FILES WHERE F_ID IN ({q_marks})", tuple(ids))
            rows = cur.fetchall()
            return [{"F_ID": r[0], "F_PATH": r[1], "F_ORIGINAL_NAME": r[2]} for r in rows]
        except Exception:
            try:
                q_marks = ",".join(["?"] * len(ids))
                cur.execute(f"SELECT F_ID, F_PATH FROM T_FILES WHERE F_ID IN ({q_marks})", tuple(ids))
                rows = cur.fetchall()
                return [{"F_ID": r[0], "F_PATH": r[1]} for r in rows]
            except Exception:
                return []


def get_files_by_paths(paths: list[str]) -> list[Dict[str, Any]]:
    """批量按 F_PATH 获取 T_FILES 记录。"""
    if not paths:
        return []
    adapter = get_adapter()
    with adapter.connect() as conn:
        cur = conn.cursor()
        try:
            q_marks = ",".join(["?"] * len(paths))
            cur.execute(f"SELECT F_ID, F_PATH, F_ORIGINAL_NAME FROM T_FILES WHERE F_PATH IN ({q_marks})", tuple(paths))
            rows = cur.fetchall()
            return [{"F_ID": r[0], "F_PATH": r[1], "F_ORIGINAL_NAME": r[2]} for r in rows]
        except Exception:
            try:
                q_marks = ",".join(["?"] * len(paths))
                cur.execute(f"SELECT F_ID, F_PATH FROM T_FILES WHERE F_PATH IN ({q_marks})", tuple(paths))
                rows = cur.fetchall()
                return [{"F_ID": r[0], "F_PATH": r[1]} for r in rows]
            except Exception:
                return []


def delete_file_physical(rel_path: str) -> bool:
    """按相对路径删除本地物理文件。"""
    try:
        base = Path(__file__).resolve().parents[2] / settings.upload_dir
        p = (base / rel_path).resolve()
        if p.exists():
            p.unlink()
        return True
    except Exception:
        return False


def delete_file_record(file_id: int) -> bool:
    """从 T_FILES 删除记录。"""
    adapter = get_adapter()
    with adapter.connect() as conn:
        cur = conn.cursor()
        try:
            cur.execute("DELETE FROM T_FILES WHERE F_ID = ?", (file_id,))
            affected = cur.rowcount
            conn.commit()
            return affected > 0
        except Exception:
            return False


def get_file_url(file_id: Any) -> str:
    """
    根据文件ID获取文件URL路径
    
    Args:
        file_id: 文件ID或文件ID字符串
        
    Returns:
        文件URL路径，如 "/uploads/xxx.jpg"，失败返回空字符串
    """
    if not file_id:
        return ""
    
    try:
        fid = int(file_id)
        file_info = get_file_by_id(fid)
        if file_info and file_info.get("F_PATH"):
            path = file_info.get("F_PATH")
            # 确保路径以/开头
            if not str(path).startswith('/'):
                return f"/{path}"
            return str(path)
    except Exception as e:
        print(f"警告: get_file_url({file_id}) 失败: {e}")
    
    return ""


