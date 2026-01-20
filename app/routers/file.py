from fastapi import APIRouter, UploadFile, File, Form, Depends
from fastapi.responses import FileResponse
from pathlib import Path
from typing import Optional
from app.core.response import ok, err
from app.core.storage import build_upload_path
from app.crud.file_center import get_file_by_id
from app.core.settings import settings
from pydantic import BaseModel
from app.core.auth import require_roles

router = APIRouter(prefix="/files", tags=["files"])


@router.post("/upload", dependencies=[Depends(require_roles(["files:write"]))])
async def upload_file(file: UploadFile = File(...), biz: Optional[str] = Form(default=None)):
    dst_path, rel = build_upload_path(file.filename)
    content = await file.read()
    dst_path.write_bytes(content)
    return ok({
        "filename": file.filename,
        "path": rel,
        "size": len(content),
        "biz": biz
    }, "上传成功")


@router.post("/download")
def download(path: str):
    # 按相对路径取文件
    base = Path(__file__).resolve().parents[2] / "uploads"
    real = (base / path).resolve()
    if not real.exists():
        return err("文件不存在")
    return FileResponse(real)


class FileStreamIn(BaseModel):
    F_ID: int


@router.post("/stream")
def stream(payload: FileStreamIn):
    """根据中心文件ID返回文件流（无鉴权）。"""
    try:
        rec = get_file_by_id(int(payload.F_ID))
        if not rec:
            return err("文件不存在")
        rel = rec.get("F_PATH")
        if not rel:
            return err("文件路径缺失")
        base = Path(__file__).resolve().parents[2] / settings.upload_dir
        abs_path = (base / rel).resolve()
        if not abs_path.exists():
            return err("文件已丢失")
        filename = rec.get("F_ORIGINAL_NAME") or abs_path.name
        return FileResponse(path=str(abs_path), filename=filename)
    except Exception:
        return err("读取失败")

