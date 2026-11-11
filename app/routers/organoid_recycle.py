from fastapi import APIRouter, Depends, UploadFile, File, Form
from typing import List, Dict, Any
from app.schemas.common import ApiResponse
from app.core.response import ok, err
from app.core.auth import require_roles, get_current_user_optional
from app.core.storage import build_upload_path
from app.crud.file_center import insert_file_record
from app.crud import organoid_recycle as crud
import json


router = APIRouter(prefix="/organoid-recycle", tags=["organoid_recycle"])


@router.post("/save", response_model=ApiResponse[dict], response_model_exclude_none=True,
             dependencies=[Depends(require_roles(["organoid_culture_tissue"]))])
async def save_recycle(
    F_GDID: int | None = Form(default=None, description="工单ID（必填）"),
    HSXX: str | None = Form(default=None, description="JSON：回收记录主体字段，含 F_HSSJ, F_JSFF, F_HSSL, F_XBZT"),
    F_FJ: List[UploadFile] | None = File(None),
    F_FJ_IDS: str | None = Form(default=None, description="JSON数组：覆盖用，文件ID列表"),
    F_FJ_IDS_ADD: str | None = Form(default=None, description="JSON数组：增量新增文件ID"),
    F_FJ_IDS_DEL: str | None = Form(default=None, description="JSON数组：增量删除文件ID"),
    current_user: dict = Depends(get_current_user_optional),
):
    # 解析主体 JSON
    try:
        data: Dict[str, Any] = json.loads(HSXX) if HSXX else {}
    except Exception:
        return err("HSXX 解析失败")

    # ✅ 从独立参数或 HSXX JSON 中获取 F_GDID（优先使用独立参数）
    gdid = F_GDID or data.get("F_GDID")
    if not gdid:
        return err("缺少 F_GDID")
    
    # 将 F_GDID 写入 data（确保 CRUD 层能获取）
    data["F_GDID"] = int(gdid)

    def parse_ids(s: str | None) -> list[int]:
        arr: list[int] = []
        if not s:
            return arr
        try:
            v = json.loads(s)
            if isinstance(v, list):
                for x in v:
                    try:
                        arr.append(int(x))
                    except Exception:
                        pass
        except Exception:
            pass
        return arr

    override_ids = parse_ids(F_FJ_IDS) if F_FJ_IDS is not None else None
    add_ids = parse_ids(F_FJ_IDS_ADD)
    del_ids = parse_ids(F_FJ_IDS_DEL)

    # 处理直传文件：保存本地并登记文件中心，加入 add_ids
    if F_FJ:
        for f in F_FJ:
            if not f:
                continue
            dst_path, rel = build_upload_path(f.filename)
            content = await f.read()
            dst_path.write_bytes(content)
            fid = insert_file_record(rel, f.filename)
            if fid is not None:
                add_ids.append(int(fid))

    # 获取操作人（与其他模块保持一致）
    operator = (current_user.get("F_Ktype") or current_user.get("F_YSM") or "") if current_user else ""
    
    res = crud.save_recycle_record(data, override_ids, add_ids, del_ids, operator)
    return ok(res, "保存成功")


