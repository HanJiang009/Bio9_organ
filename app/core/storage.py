import os
import uuid
from datetime import datetime
from pathlib import Path
from typing import Tuple

from .settings import settings


def ensure_dir(p: Path) -> None:
    p.mkdir(parents=True, exist_ok=True)


def build_upload_path(original_name: str) -> Tuple[Path, str]:
    base = Path(__file__).resolve().parents[2] / settings.upload_dir
    today = datetime.now()
    sub = Path(str(today.year)) / f"{today.month:02d}" / f"{today.day:02d}"
    ensure_dir(base / sub)
    ext = Path(original_name).suffix
    fname = f"{uuid.uuid4().hex}{ext}"
    rel = sub / fname
    return base / rel, str(rel).replace("\\", "/")


# 在 uploads 下的指定子目录内构建上传路径（同样按日期分级）
def build_upload_path_in(subdir: str, original_name: str) -> Tuple[Path, str]:
    base = Path(__file__).resolve().parents[2] / settings.upload_dir / subdir
    today = datetime.now()
    sub = Path(str(today.year)) / f"{today.month:02d}" / f"{today.day:02d}"
    ensure_dir(base / sub)
    ext = Path(original_name).suffix
    fname = f"{uuid.uuid4().hex}{ext}"
    rel = Path(subdir) / sub / fname
    return (Path(__file__).resolve().parents[2] / settings.upload_dir / rel, str(rel).replace("\\", "/"))

