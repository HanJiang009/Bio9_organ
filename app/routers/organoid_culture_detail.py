from fastapi import APIRouter, Depends
from random import uniform
from app.schemas.common import ApiResponse
from app.core.response import ok
from app.core.auth import require_roles, get_current_user_optional
from fastapi import Form, File, UploadFile
import json
from typing import List
from app.core.storage import build_upload_path
from app.crud.file_center import insert_file_record
from app.crud import organoid_culture_tissue as crud_order
from app.crud import organoid_culture_detail as crud
from pydantic import BaseModel


router = APIRouter(prefix="/organoid-culture-detail", tags=["organoid_culture_detail"])


@router.post("/env/data", response_model=ApiResponse[dict], response_model_exclude_none=True,
             dependencies=[Depends(require_roles(["organoid_culture_tissue"]))])
def random_env():
    """测试环境：随机返回培养间温度/湿度。

    温度范围: 15~30 (℃)
    湿度范围: 30~70 (%)
    """
    temp = round(uniform(15.0, 30.0), 1)
    humi = round(uniform(30.0, 70.0), 1)
    return ok({"F_PYJ_WD": temp, "F_PYJ_SD": humi}, "获取成功")


@router.post("/mx/create", response_model=ApiResponse[dict], response_model_exclude_none=True,
             dependencies=[Depends(require_roles(["organoid_culture_tissue"]))])
async def create_mx(
    F_GDID: int = Form(...),
    PYJTJJL: str | None = Form(default=None),
    PYXX_XH: str | None = Form(default=None),
    XHMSYJL: str | None = Form(default=None),
    PYXX_PB: str | None = Form(default=None),
    PYXX_PY: str | None = Form(default=None),
    CDQXX: str | None = Form(default=None),
    F_QXYCF: str | None = Form(default=None),
    F_FJ_IDS: str | None = Form(default=None),
    F_FJ: List[UploadFile] | None = File(None),
    current_user: dict = Depends(get_current_user_optional),
):
    # parse json fragments
    try:
        pyjtjjl = json.loads(PYJTJJL) if PYJTJJL else {}
        pyxx_xh = json.loads(PYXX_XH) if PYXX_XH else {}
        xhmsy_list = json.loads(XHMSYJL) if XHMSYJL else []
        pyxx_pb = json.loads(PYXX_PB) if PYXX_PB else {}
        pyxx_py = json.loads(PYXX_PY) if PYXX_PY else {}
        cdqxx = json.loads(CDQXX) if CDQXX else {}
        if isinstance(xhmsy_list, dict):
            xhmsy_list = [xhmsy_list]
    except Exception:
        return ok(None, "表单数据解析失败")

    # prepare file ids
    file_ids: List[int] = []
    if F_FJ_IDS:
        try:
            arr = json.loads(F_FJ_IDS)
            if isinstance(arr, list):
                for v in arr:
                    try:
                        file_ids.append(int(v))
                    except Exception:
                        pass
        except Exception:
            pass
    if F_FJ:
        for f in F_FJ:
            if not f:
                continue
            dst_path, rel = build_upload_path(f.filename)
            content = await f.read()
            dst_path.write_bytes(content)
            fid = insert_file_record(rel, f.filename)
            if fid is not None:
                file_ids.append(int(fid))

    operator = (current_user.get("F_Ktype") or current_user.get("F_YSM") or "") if current_user else ""

    res = crud.create_mx(
        gdid=int(F_GDID),
        pyjtjjl=pyjtjjl,
        pyxx_xh=pyxx_xh,
        xhmsy_list=xhmsy_list,
        pyxx_pb=pyxx_pb,
        pyxx_py=pyxx_py,
        cdqxx=cdqxx,
        qxycf=F_QXYCF,
        file_ids=file_ids,
        operator=operator,
    )
    return ok(res, "创建成功")


@router.post("/mx/update", response_model=ApiResponse[dict], response_model_exclude_none=True,
             dependencies=[Depends(require_roles(["organoid_culture_tissue"]))])
async def update_mx(
    F_GDID: int = Form(...),
    F_MXID: int = Form(...),
    PYJTJJL: str | None = Form(default=None),
    PYXX_XH: str | None = Form(default=None),
    XHMSYJL: str | None = Form(default=None),
    PYXX_PB: str | None = Form(default=None),
    PYXX_PY: str | None = Form(default=None),
    CDQXX: str | None = Form(default=None),
    F_QXYCF: str | None = Form(default=None),
    F_FJ_IDS_ADD: str | None = Form(default=None),
    F_FJ_IDS_DEL: str | None = Form(default=None),
    JZJ_IDS_DELETE: str | None = Form(default=None),
    XHMSYJL_DELETE: str | None = Form(default=None),
    current_user: dict = Depends(get_current_user_optional),
):
    try:
        pyjtjjl = json.loads(PYJTJJL) if PYJTJJL else None
        pyxx_xh = json.loads(PYXX_XH) if PYXX_XH else None
        xhmsy_list = json.loads(XHMSYJL) if XHMSYJL else None
        pyxx_pb = json.loads(PYXX_PB) if PYXX_PB else None
        pyxx_py = json.loads(PYXX_PY) if PYXX_PY else None
        cdqxx = json.loads(CDQXX) if CDQXX else None
        if isinstance(xhmsy_list, dict):
            xhmsy_list = [xhmsy_list]
    except Exception:
        return ok(None, "表单数据解析失败")

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

    add_ids = parse_ids(F_FJ_IDS_ADD)
    del_ids = parse_ids(F_FJ_IDS_DEL)
    jzj_del_ids = parse_ids(JZJ_IDS_DELETE)
    xhmsy_del_ids = parse_ids(XHMSYJL_DELETE)
    operator = (current_user.get("F_Ktype") or current_user.get("F_YSM") or "") if current_user else ""

    res = crud.update_mx(
        gdid=int(F_GDID),
        mx_id=int(F_MXID),
        pyjtjjl=pyjtjjl,
        pyxx_xh=pyxx_xh,
        xhmsy_list=xhmsy_list,
        pyxx_pb=pyxx_pb,
        pyxx_py=pyxx_py,
        cdqxx=cdqxx,
        qxycf=F_QXYCF,
        add_file_ids=add_ids,
        del_file_ids=del_ids,
        jzj_delete_ids=jzj_del_ids,
        xhmsy_delete_ids=xhmsy_del_ids,
        operator=operator,
    )
    if not res:
        return ok(None, "未找到记录或工单不匹配")
    return ok(res, "更新成功")


class GetMxByGdidIn(BaseModel):
    F_GDID: int


@router.post("/mx/get-by-gdid", response_model=ApiResponse[dict], response_model_exclude_none=True,
             dependencies=[Depends(require_roles(["organoid_culture_tissue"]))])
def get_mx_by_gdid(payload: GetMxByGdidIn, current_user: dict = Depends(get_current_user_optional)):
    from app.core.data_scope import deny_pygd_access
    denied = deny_pygd_access(current_user, payload.F_GDID, module="organoid_culture_tissue")
    if denied:
        return denied
    data = crud.get_mx_by_gdid(int(payload.F_GDID))
    return ok(data, "查询成功" if data else "未找到记录")


@router.post("/detail-by-gdid", response_model=ApiResponse[dict], response_model_exclude_none=True,
             dependencies=[Depends(require_roles(["organoid_culture_tissue"]))])
def detail_by_gdid(payload: GetMxByGdidIn, current_user: dict = Depends(get_current_user_optional)):
    gdid = int(payload.F_GDID)
    from app.core.data_scope import deny_pygd_access
    denied = deny_pygd_access(current_user, gdid, module="organoid_culture_tissue")
    if denied:
        return denied
    order = crud_order.get_culture_order_detail(gdid) if hasattr(crud_order, "get_culture_order_detail") else None
    detail = crud.get_mx_by_gdid(gdid)
    if not order and not detail:
        return ok({}, "未找到记录")
    return ok({
        "order": order or {},
        "cultureDetail": detail or {},
    }, "查询成功")


@router.post("/mx/save", response_model=ApiResponse[dict], response_model_exclude_none=True,
             dependencies=[Depends(require_roles(["organoid_culture_tissue"]))])
async def save_mx(
    F_GDID: int = Form(...),
    PYJTJJL: str | None = Form(default=None),
    PYXX_XH: str | None = Form(default=None),
    PYXX_PB: str | None = Form(default=None),
    PYXX_PY: str | None = Form(default=None),
    CDQXX: str | None = Form(default=None),
    XHMSYJL: str | None = Form(default=None),
    XHMSYJL_DELETE: str | None = Form(default=None),
    F_QXYCF: str | None = Form(default=None),
    F_FJ_IDS: str | None = Form(default=None),
    F_FJ_IDS_ADD: str | None = Form(default=None),
    F_FJ_IDS_DEL: str | None = Form(default=None),
    JZJ_IDS_DELETE: str | None = Form(default=None),
    F_FJ: List[UploadFile] | None = File(None),
    current_user: dict = Depends(get_current_user_optional),
):
    # 解析各 JSON 片段（CDQXX 需兼容二次编码 / 智能引号，避免静默丢字段）
    def parse_json(s: str | None, default):
        if s is None:
            return default
        try:
            return json.loads(s)
        except Exception:
            return default

    def parse_cdqxx(s: str | None):
        if s is None:
            return None
        raw = str(s).strip()
        if not raw:
            return None
        # 兼容中文弯引号（复制粘贴导致 JSON 非法）
        raw_fixed = (
            raw.replace("\u201c", '"')
            .replace("\u201d", '"')
            .replace("\u2018", "'")
            .replace("\u2019", "'")
        )
        try:
            v = json.loads(raw_fixed)
        except Exception:
            import logging
            logging.getLogger(__name__).warning(
                "[mx/save] CDQXX JSON 解析失败，已忽略。raw=%s", raw[:200]
            )
            return None
        # 兼容双重 stringify
        if isinstance(v, str):
            try:
                v = json.loads(v)
            except Exception:
                return None
        return v if isinstance(v, dict) else None

    pyjtjjl = parse_json(PYJTJJL, None)
    pyxx_xh = parse_json(PYXX_XH, None)
    pyxx_pb = parse_json(PYXX_PB, None)
    pyxx_py = parse_json(PYXX_PY, None)
    cdqxx = parse_cdqxx(CDQXX)
    xhmsy_list = parse_json(XHMSYJL, None)
    if isinstance(xhmsy_list, dict):
        xhmsy_list = [xhmsy_list]

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

    xhmsy_del_ids = parse_ids(XHMSYJL_DELETE)
    jzj_del_ids = parse_ids(JZJ_IDS_DELETE)
    override_ids = parse_ids(F_FJ_IDS)
    add_ids = parse_ids(F_FJ_IDS_ADD)
    del_ids = parse_ids(F_FJ_IDS_DEL)

    # 处理直接上传文件：保存并登记，ID并入 add_ids（若存在覆盖参数，则以覆盖为准）
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

    operator = (current_user.get("F_Ktype") or current_user.get("F_YSM") or "") if current_user else ""

    res = crud.save_mx(
        gdid=int(F_GDID),
        pyjtjjl=pyjtjjl,
        pyxx_xh=pyxx_xh,
        xhmsy_list=xhmsy_list,
        xhmsy_delete_ids=xhmsy_del_ids,
        pyxx_pb=pyxx_pb,
        pyxx_py=pyxx_py,
        cdqxx=cdqxx,
        qxycf=F_QXYCF,
        file_ids_override=override_ids if F_FJ_IDS is not None else None,
        add_file_ids=add_ids,
        del_file_ids=del_ids,
        jzj_delete_ids=jzj_del_ids,
        operator=operator,
    )
    return ok(res, "保存成功")
