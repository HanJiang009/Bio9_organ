from fastapi import APIRouter, Depends
from fastapi.responses import FileResponse
from typing import Dict
from pathlib import Path
from app.core.docx_pdf import render_pdf
from app.core.response import ok, err
from app.core.auth import require_roles

router = APIRouter(prefix="/pdf", tags=["pdf"])


@router.post("/render", dependencies=[Depends(require_roles(["pdf:write"]))])
def render(payload: Dict[str, str]):
    """使用模板名与上下文生成 PDF。
    请求体示例：{"template": "report.docx", "context": {"title":"xxx"}, "out": "report_20250101"}
    """
    template = payload.get("template")
    ctx = payload.get("context") or {}
    out = payload.get("out") or "output"
    if not template:
        return err("缺少 template")
    pdf_path = render_pdf(template, ctx, out)
    return ok({"path": str(pdf_path.relative_to(Path(__file__).resolve().parents[2]))}, "生成成功")


@router.post("/download")
def download(payload: Dict[str, str]):
    path = payload.get("path")
    if not path:
        return err("缺少 path")
    base = Path(__file__).resolve().parents[2]
    real = (base / path).resolve()
    if not real.exists():
        return err("文件不存在")
    return FileResponse(real)


