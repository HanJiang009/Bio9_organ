from fastapi import APIRouter, Depends, UploadFile, File, Form
from app.schemas.common import ApiResponse
from app.core.response import ok, err
from app.core.auth import require_roles, get_current_user_optional
from app.crud import organoid_thaw as crud
from app.core.storage import build_upload_path
from app.crud.file_center import insert_file_record
from typing import Any, Dict, List
import json


router = APIRouter(prefix="/organoid-thaw", tags=["organoid_thaw"])


@router.post("/save", response_model=ApiResponse[dict], response_model_exclude_none=True,
             dependencies=[Depends(require_roles(["organoid_culture_tissue"]))])
async def save(
    F_GDID: int = Form(..., description="工单ID"),
    FSXX: str | None = Form(default=None, description="JSON：复苏相关字段"),
    PB: str | None = Form(default=None, description="JSON：铺板相关字段（含 JZJMX 可选）"),
    PY: str | None = Form(default=None, description="JSON：培养箱相关字段"),
    JZJMX: str | None = Form(default=None, description="JSON数组：基质胶明细，可不传（将从PB读取）"),
    JZJMX_DEL: str | None = Form(default=None, description="JSON数组：要删除的基质胶明细ID"),
    F_FJ: List[UploadFile] | None = File(None),
    F_FJ_IDS: str | None = Form(default=None, description="JSON数组：覆盖用，文件ID列表"),
    F_FJ_IDS_ADD: str | None = Form(default=None, description="JSON数组：增量新增文件ID"),
    F_FJ_IDS_DEL: str | None = Form(default=None, description="JSON数组：增量删除文件ID"),
    current_user: dict = Depends(get_current_user_optional),
):
    gdid = int(F_GDID)

    # 处理上传附件：追加入文件中心，返回新的文件ID，并叠加到 F_FJ_IDS_ADD
    add_ids: List[int] = []
    if F_FJ is not None:
        for f in F_FJ:
            dst_path, rel = build_upload_path(f.filename)
            content = await f.read()
            dst_path.write_bytes(content)
            fid = insert_file_record(rel, f.filename)
            if fid is not None:
                add_ids.append(int(fid))

    def _parse_ids(x) -> List[int] | None:
        if x is None:
            return None
        if isinstance(x, list):
            return [int(i) for i in x if i is not None]
        try:
            arr = json.loads(x)
            return [int(i) for i in arr if i is not None]
        except Exception:
            return None

    # 解析分组 JSON：FSXX（复苏）、PB（铺板）、PY（培养）
    def _parse_json(j: str | None) -> dict:
        if j is None:
            return {}
        try:
            d = json.loads(j)
            return d if isinstance(d, dict) else {}
        except Exception:
            return {}

    fsxx = _parse_json(FSXX)
    pb = _parse_json(PB)
    py = _parse_json(PY)

    # 组装为后端统一字段（与 T_PYMX_ZZ 对齐的列名）
    fsjl = {}
    # FSXX → 复苏核心
    for k in ("F_FSWD","F_FSSJ","F_PYJ","F_LXSL","F_LXSJ","F_HXHL","F_HZXS","F_DCQZXS","F_FSXL"):
        if fsxx.get(k) is not None:
            fsjl[k] = fsxx.get(k)
    # PB → 板/成胶；并兼容从 PB 中读取 JZJMX
    for k in ("F_PYBBH","F_JZKBS","F_DKJZTJ","F_CJWD","F_CJSJ"):
        if pb.get(k) is not None:
            fsjl[k] = pb.get(k)
    # PY → 培养箱
    for k in ("F_TJPYJ","F_PYX_TEMP","F_PYX_CO2","F_PYX_HUM","F_PYX_FRSJ"):
        if py.get(k) is not None:
            fsjl[k] = py.get(k)

    # JZJMX 优先取顶层，其次 PB.JZJMX，再次 PB.F_JZJ（若为数组）
    jzjmx = None
    if JZJMX is not None:
        try:
            arr = json.loads(JZJMX)
            if isinstance(arr, list):
                jzjmx = arr
        except Exception:
            jzjmx = None
    if not isinstance(jzjmx, list):
        jzjmx = pb.get("JZJMX") if isinstance(pb.get("JZJMX"), list) else None
    if not isinstance(jzjmx, list):
        jz_from_pb = pb.get("F_JZJ")
        jzjmx = jz_from_pb if isinstance(jz_from_pb, list) else []

    try:
        jzjmx_del = json.loads(JZJMX_DEL) if JZJMX_DEL else []
        if not isinstance(jzjmx_del, list):
            jzjmx_del = []
    except Exception:
        jzjmx_del = []
    override_ids = _parse_ids(F_FJ_IDS)
    add_ids_all = (_parse_ids(F_FJ_IDS_ADD) or []) + add_ids
    del_ids = _parse_ids(F_FJ_IDS_DEL)

    try:
        operator = (current_user.get("F_Ktype") or current_user.get("F_YSM")) if current_user else None
        res = crud.save_thaw(
            gdid=gdid,
            fsjl=fsjl,
            jzjmx=jzjmx if isinstance(jzjmx, list) else [],
            jzjmx_del_ids=jzjmx_del if isinstance(jzjmx_del, list) else [],
            file_ids_override=override_ids,
            add_file_ids=add_ids_all,
            del_file_ids=del_ids,
            operator=operator,
        )
        return ok(res, "保存成功")
    except ValueError as ve:
        return err(str(ve))
    except Exception as e:
        return err(f"保存失败: {e}")


@router.post("/detail", response_model=ApiResponse[dict], response_model_exclude_none=True,
             dependencies=[Depends(require_roles(["organoid_culture_tissue"]))])
def detail(payload: Dict[str, Any]):
    try:
        gdid = int(payload.get("F_GDID"))
    except Exception:
        return err("缺少或非法的 F_GDID")
    data = crud.get_thaw_detail_by_gdid(gdid)
    if not data:
        return err("未找到记录")
    return ok(data, "查询成功")


