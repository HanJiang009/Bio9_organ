"""
动物实验报告 PDF 管理模块
管理 T_DWSY_JH.F_BG_PDF 字段（JSON），结构与药物试验报告一致：
  {"generated": 123, "uploaded": [456, 789]}
"""
import json
import os
from typing import Any, Dict, List, Optional

from app.db.factory import get_adapter


def _get_raw(syjh_id: int) -> Dict[str, Any]:
    adapter = get_adapter()
    with adapter.connect() as conn:
        cur = conn.cursor()
        cur.execute("SELECT F_BG_PDF FROM T_DWSY_JH WHERE F_ID = ?", (syjh_id,))
        row = cur.fetchone()
    if not row or not row[0]:
        return {"generated": None, "uploaded": []}
    try:
        d = json.loads(row[0])
        if not isinstance(d, dict):
            return {"generated": None, "uploaded": []}
        if not isinstance(d.get("uploaded"), list):
            d["uploaded"] = []
        return d
    except Exception:
        return {"generated": None, "uploaded": []}


def _save_raw(syjh_id: int, data: Dict[str, Any]) -> bool:
    adapter = get_adapter()
    with adapter.connect() as conn:
        cur = conn.cursor()
        cur.execute(
            "UPDATE T_DWSY_JH SET F_BG_PDF = ? WHERE F_ID = ?",
            (json.dumps(data, ensure_ascii=False), syjh_id),
        )
        conn.commit()
    return True


def set_generated_pdf(syjh_id: int, file_id: int) -> bool:
    """回写系统生成的 PDF 文件ID（替换旧的）"""
    d = _get_raw(syjh_id)
    d["generated"] = file_id
    return _save_raw(syjh_id, d)


def add_uploaded_pdf(syjh_id: int, file_id: int) -> bool:
    """追加用户手动上传的 PDF 文件ID（去重）"""
    d = _get_raw(syjh_id)
    if file_id not in d["uploaded"]:
        d["uploaded"].append(file_id)
    return _save_raw(syjh_id, d)


def remove_uploaded_pdf(syjh_id: int, file_id: int) -> bool:
    """删除指定用户上传 PDF"""
    d = _get_raw(syjh_id)
    d["uploaded"] = [i for i in d["uploaded"] if i != file_id]
    return _save_raw(syjh_id, d)


def get_pdf_data(syjh_id: int) -> Dict[str, Any]:
    """返回 {"generated": id|None, "uploaded": [...]}"""
    return _get_raw(syjh_id)


def _clean_path(raw_path: str) -> str:
    path = raw_path or ""
    if "\\" in path or ":" in path:
        if "uploads" in path:
            path = path.split("uploads")[-1].lstrip("\\/")
        else:
            path = os.path.basename(path)
    path = path.lstrip("/\\")
    if path.startswith("uploads/") or path.startswith("uploads\\"):
        path = path[8:]
    return path.replace("\\", "/")


def get_pdf_list_with_details(syjh_id: int) -> Dict[str, Any]:
    """返回带文件信息的 PDF 列表，结构与药物试验报告保持一致"""
    from app.crud.file_center import get_file_by_id

    raw = _get_raw(syjh_id)
    result: Dict[str, Any] = {"generated": None, "uploaded": []}

    if raw.get("generated"):
        info = get_file_by_id(raw["generated"])
        if info:
            result["generated"] = {
                "id":         info.get("F_ID"),
                "name":       info.get("F_ORIGINAL_NAME") or "",
                "path":       _clean_path(info.get("F_PATH", "")),
                "created_at": str(info.get("F_UPLOAD_TIME", "")),
            }

    for fid in raw.get("uploaded", []):
        info = get_file_by_id(fid)
        if info:
            result["uploaded"].append({
                "id":         info.get("F_ID"),
                "name":       info.get("F_ORIGINAL_NAME") or "",
                "path":       _clean_path(info.get("F_PATH", "")),
                "created_at": str(info.get("F_UPLOAD_TIME", "")),
            })

    return result
