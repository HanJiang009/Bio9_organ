from __future__ import annotations

from typing import Any, Optional

from pathlib import Path

from fastapi import APIRouter, Body, Depends, File, Form, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from app.agents_host import get_runtime
from app.agents_host.auth import current_principal
from app.core.response import err, ok

router = APIRouter(prefix="/agent", tags=["agent"])


class DraftPatchIn(BaseModel):
    set: dict[str, Any] = Field(default_factory=dict)
    clear: list[str] = Field(default_factory=list)


class ConfirmIn(BaseModel):
    payload: Optional[dict[str, Any]] = None


class DistributeRowIn(BaseModel):
    F_PYRY: int
    F_MXLX: str = Field(min_length=1)
    F_GDLX: Optional[str] = None
    F_QYWC: Optional[int] = 0


class DistributeIn(BaseModel):
    items: list[DistributeRowIn] = Field(default_factory=list)


class MarkUnqualifiedIn(BaseModel):
    F_BHGYY: str = Field(min_length=1)


class FreezeDirectIn(BaseModel):
    F_FGS: int = Field(ge=1)
    F_YBLX: Optional[str] = None
    F_YBGLX: Optional[str] = None
    F_CCXS: Optional[str] = None
    F_TJ: Optional[str] = None


def _fail(exc: Exception):
    from organ_agent import AgentError

    if isinstance(exc, AgentError):
        data = {"code": exc.code}
        if exc.data is not None:
            data["detail"] = exc.data
        return err(exc.message, data=data)
    return err(str(exc))


def _envelope(task, extra: dict | None = None) -> dict:
    data = task.to_public()
    if extra:
        data.update(extra)
    return data


@router.post("/tasks")
async def create_task(
    spec_id: str = Form(...),
    file: UploadFile = File(...),
    note: str = Form(default=""),
    principal: dict = Depends(current_principal),
):
    try:
        raw = await file.read()
        task = get_runtime().create_task(
            spec_id=spec_id.strip(),
            user_id=str((principal["user"] or {}).get("F_ID") or ""),
            roles=principal.get("roles") or [],
            filename=file.filename or "upload.bin",
            file_bytes=raw,
            note=note or "",
        )
        return ok(_envelope(task), "任务已创建")
    except Exception as exc:
        return _fail(exc)


@router.get("/tasks/{task_id}")
def get_task(task_id: str, principal: dict = Depends(current_principal)):
    try:
        _ = principal
        return ok(_envelope(get_runtime().get_task(task_id)), "ok")
    except Exception as exc:
        return _fail(exc)


@router.post("/tasks/{task_id}/runs")
def start_run(task_id: str, principal: dict = Depends(current_principal)):
    try:
        task, run = get_runtime().start_run(
            task_id,
            user_id=str((principal["user"] or {}).get("F_ID") or ""),
            roles=principal.get("roles") or [],
        )
        return ok(_envelope(task, {"run_id": run.run_id}), "抽取完成")
    except Exception as exc:
        return _fail(exc)


@router.get("/tasks/{task_id}/runs/{run_id}")
def get_run(task_id: str, run_id: str, principal: dict = Depends(current_principal)):
    try:
        _ = principal
        run = get_runtime().get_run(task_id, run_id)
        return ok(
            {
                "task_id": task_id,
                "spec_id": run.spec_id,
                "state": None,
                **run.to_public(),
            },
            "ok",
        )
    except Exception as exc:
        return _fail(exc)


@router.get("/catalog")
def catalog():
    from app.agents_host import _ensure_organ_agent_importable

    _ensure_organ_agent_importable()
    from organ_agent.extractors.fields import catalog_public

    return ok(catalog_public(), "ok")


@router.get("/distribute-options")
def distribute_options(principal: dict = Depends(current_principal)):
    try:
        _ = principal
        return ok(get_runtime().list_distribute_options(), "ok")
    except Exception as exc:
        return _fail(exc)


@router.get("/console")
def console_page():
    """核对台单页。仅开关打开时随 /agent 挂载。"""
    here = Path(__file__).resolve()
    workspace = here.parents[3]
    path = workspace / "organ_agent_web" / "console.html"
    if not path.is_file():
        sibling = here.parents[2].parent / "organ_agent_web" / "console.html"
        path = sibling if sibling.is_file() else path
    if not path.is_file():
        return err("核对台页面不存在")
    return FileResponse(path, media_type="text/html; charset=utf-8")


@router.get("/tasks/{task_id}/events")
def events(task_id: str, principal: dict = Depends(current_principal)):
    try:
        _ = principal
        return ok(get_runtime().events(task_id), "ok")
    except Exception as exc:
        return _fail(exc)


@router.get("/tasks/{task_id}/source")
def get_source(task_id: str, principal: dict = Depends(current_principal)):
    try:
        _ = principal
        return ok(get_runtime().get_source(task_id), "ok")
    except Exception as exc:
        return _fail(exc)


@router.patch("/tasks/{task_id}/draft")
def patch_draft(task_id: str, body: DraftPatchIn, principal: dict = Depends(current_principal)):
    try:
        _ = principal
        task = get_runtime().patch_draft(task_id, set_fields=body.set, clear_fields=body.clear)
        return ok(_envelope(task), "草稿已更新")
    except Exception as exc:
        return _fail(exc)


@router.post("/tasks/{task_id}/confirm")
def confirm(
    task_id: str,
    principal: dict = Depends(current_principal),
    body: ConfirmIn = Body(default=ConfirmIn()),
):
    try:
        payload = body.payload if body else None
        task = get_runtime().confirm(
            task_id,
            user=principal.get("user") or {},
            roles=principal.get("roles") or [],
            payload=payload,
        )
        return ok(_envelope(task, {"F_ZZID": task.f_zzid, "YBZJ": (task.draft.payload or {}).get("YBZJ")}), "保存成功")
    except Exception as exc:
        return _fail(exc)


@router.post("/tasks/{task_id}/attachments")
async def add_attachment(
    task_id: str,
    kind: str = Form(...),
    file: UploadFile = File(...),
    draft_id: str = Form(default=""),
    principal: dict = Depends(current_principal),
):
    try:
        _ = principal
        raw = await file.read()
        task = get_runtime().add_attachment(
            task_id,
            kind=kind,
            filename=file.filename or "attachment.bin",
            file_bytes=raw,
            draft_id=draft_id or None,
        )
        return ok(_envelope(task), "附件已挂到本条草稿")
    except Exception as exc:
        return _fail(exc)


@router.delete("/tasks/{task_id}/attachments/{attachment_id}")
def remove_attachment(
    task_id: str,
    attachment_id: str,
    principal: dict = Depends(current_principal),
):
    try:
        _ = principal
        return ok(_envelope(get_runtime().remove_attachment(task_id, attachment_id)), "附件已移除")
    except Exception as exc:
        return _fail(exc)


@router.post("/tasks/{task_id}/distribute")
def distribute(
    task_id: str,
    body: DistributeIn,
    principal: dict = Depends(current_principal),
):
    try:
        task = get_runtime().distribute(
            task_id,
            items=[item.model_dump() for item in (body.items or [])],
            user=principal.get("user") or {},
            roles=principal.get("roles") or [],
        )
        return ok(_envelope(task), "分发成功")
    except Exception as exc:
        return _fail(exc)


@router.post("/tasks/{task_id}/mark-unqualified")
def mark_unqualified(
    task_id: str,
    body: MarkUnqualifiedIn,
    principal: dict = Depends(current_principal),
):
    try:
        task = get_runtime().mark_unqualified(
            task_id,
            reason=body.F_BHGYY,
            user=principal.get("user") or {},
            roles=principal.get("roles") or [],
        )
        return ok(_envelope(task), "已提交不合格审批")
    except Exception as exc:
        return _fail(exc)


@router.post("/tasks/{task_id}/freeze-direct")
def freeze_direct(
    task_id: str,
    body: FreezeDirectIn,
    principal: dict = Depends(current_principal),
):
    try:
        task = get_runtime().freeze_direct(
            task_id,
            fgs=body.F_FGS,
            user=principal.get("user") or {},
            roles=principal.get("roles") or [],
            yblx=body.F_YBLX,
            ybglx=body.F_YBGLX,
            ccxs=body.F_CCXS,
            tj=body.F_TJ,
        )
        return ok(_envelope(task), "已冻存分装")
    except Exception as exc:
        return _fail(exc)


@router.post("/tasks/{task_id}/cancel")
def cancel(task_id: str, principal: dict = Depends(current_principal)):
    try:
        _ = principal
        return ok(_envelope(get_runtime().cancel(task_id)), "已取消")
    except Exception as exc:
        return _fail(exc)
