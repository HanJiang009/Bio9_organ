from fastapi import APIRouter, Depends, UploadFile, File, Form
from typing import List, Dict, Any, Optional
from app.schemas.common import ApiResponse
from app.core.response import ok, err
from app.core.auth import require_roles, get_current_user_optional
from app.core.storage import build_upload_path
from app.crud.file_center import insert_file_record, format_file_static_url
from app.crud import organoid_observation as crud
from app.crud import organoid_culture_tissue as crud_order
from app.crud import organoid_culture_detail as crud_detail
from app.crud import organoid_recycle as crud_recycle
from app.crud.file_center import get_files_by_ids
from app.core.settings import settings
import json
from datetime import datetime
from pydantic import BaseModel


router = APIRouter(prefix="/organoid-observation", tags=["organoid_observation"])


def _file_ref_attachment(it: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """文件中心引用。已有 F_TPBM 原样带回，供保存时保持原号。"""
    if not isinstance(it, dict):
        return None
    try:
        att: Dict[str, Any] = {"F_BS": it.get("F_BS"), "F_WJ_ID": int(it.get("F_WJ_ID"))}
    except (TypeError, ValueError):
        return None
    if it.get("F_URL"):
        att["F_URL"] = format_file_static_url(it.get("F_URL"))
    if it.get("F_NAME"):
        att["F_NAME"] = it.get("F_NAME")
    code = it.get("F_TPBM")
    if code is not None and str(code).strip():
        att["F_TPBM"] = str(code).strip()
    return att


def _chart_numeric_value(v: Any) -> Optional[float]:
    """生长曲线：F_XBSL 为字符串时尝试解析为数值，无法解析则跳过。"""
    if v is None:
        return None
    if isinstance(v, bool):
        return float(int(v))
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip()
    if not s:
        return None
    try:
        return float(s) if "." in s else float(int(s))
    except (TypeError, ValueError):
        return None


@router.post("/create", response_model=ApiResponse[dict], response_model_exclude_none=False,
             dependencies=[Depends(require_roles(["organoid_culture_tissue"]))])
async def create_observation(
    GCXX: str = Form(..., description="JSON：观察记录主体字段（不含附件）"),
    F_FJ: List[UploadFile] | None = File(None),
    F_FJ_BS: str | None = Form(default=None, description="JSON数组，与F_FJ文件顺序一一对应，例如 [10,20]") ,
    F_FJ_IDS: str | None = Form(default=None, description="JSON数组：[{\"F_BS\": 10, \"F_WJ_ID\": 123, \"F_TPBM\": \"已有编码\"}]"),
    current_user: dict = Depends(get_current_user_optional),
):
    # 解析主体 JSON
    try:
        data: Dict[str, Any] = json.loads(GCXX) if GCXX else {}
    except Exception:
        return err("GCXX 解析失败")

    # 必填：工单ID
    if not data.get("F_GDID"):
        return err("缺少 F_GDID")

    # 可选：倍数数组（与直传文件对齐）
    bs_list: List[Any] | None = None
    if F_FJ_BS:
        try:
            parsed = json.loads(F_FJ_BS)
            if isinstance(parsed, list):
                bs_list = parsed
            else:
                return err("F_FJ_BS 应为JSON数组")
        except Exception:
            return err("F_FJ_BS 解析失败")

    attachments: List[Dict[str, Any]] = []

    # 路径A：直传文件与倍数一一对应
    if F_FJ:
        if bs_list is None:
            return err("直传文件需同时提供 F_FJ_BS 并与文件数对齐")
        if len(bs_list) != len(F_FJ):
            return err("文件数量与倍数数量不一致")
        for idx, f in enumerate(F_FJ):
            if not f:
                continue
            dst_path, rel = build_upload_path(f.filename)
            content = await f.read()
            dst_path.write_bytes(content)
            fid = insert_file_record(rel, f.filename)
            if fid is not None:
                attachments.append({
                    "F_BS": bs_list[idx],
                    "F_WJ_ID": int(fid),
                    "F_URL": format_file_static_url(rel),
                    "F_NAME": f.filename,
                })

    # 路径B：来自文件中心的ID引用
    if F_FJ_IDS:
        try:
            parsed_ids = json.loads(F_FJ_IDS)
            if not isinstance(parsed_ids, list):
                return err("F_FJ_IDS 应为JSON数组")
            for it in parsed_ids:
                att = _file_ref_attachment(it)
                if att:
                    attachments.append(att)
        except Exception:
            return err("F_FJ_IDS 解析失败")

    # 自动填充记录人/日期（记录日期始终使用当前日期，不读取入参）
    if not data.get("F_JLR") and current_user:
        data["F_JLR"] = current_user.get("F_Ktype") or current_user.get("F_YSM")
    data["F_JLRQ"] = datetime.now().strftime("%Y-%m-%d")

    operator = (current_user.get("F_Ktype") or current_user.get("F_YSM") or "") if current_user else ""

    res = crud.create_observation(data, attachments, operator)
    return ok(res, "创建成功")


@router.post("/update", response_model=ApiResponse[dict], response_model_exclude_none=False,
             dependencies=[Depends(require_roles(["organoid_culture_tissue"]))])
async def update_observation(
    F_ID: int = Form(..., description="观察记录ID"),
    GCXX: str = Form(..., description="JSON：观察记录主体字段（不含附件）"),
    F_FJ: List[UploadFile] | None = File(None),
    F_FJ_BS: str | None = Form(default=None, description="JSON数组，与F_FJ文件顺序一一对应，例如 [10,20]"),
    F_FJ_IDS: str | None = Form(default=None, description="JSON数组：[{\"F_BS\": 10, \"F_WJ_ID\": 123, \"F_TPBM\": \"已有编码\"}]"),
    current_user: dict = Depends(get_current_user_optional),
):
    """
    编辑观察记录
    
    ## 业务规则
    - 只更新传入的字段，未传入的字段保持不变
    - 如果提供了附件相关参数（F_FJ、F_FJ_BS、F_FJ_IDS），则完全替换原有附件
    - 如果未提供附件参数，则保留原有附件
    - 自动记录质控数据
    
    ## 入参说明
    - **F_ID**: 观察记录ID（必填）
    - **GCXX**: 观察记录主体字段JSON（必填）
    - **F_FJ**: 新上传的文件（可选）
    - **F_FJ_BS**: 新上传文件对应的倍数数组（可选，与F_FJ对应）
    - **F_FJ_IDS**: 引用文件中心已有文件的ID和倍数（可选）
    
    ## 示例
    ```json
    {
        "F_ID": 123,
        "GCXX": {
            "F_PYTS": 7,
            "F_PJZJ": 150.5,
            "F_XBSL": 1000000,
            "F_BZ": "状态良好"
        },
        "F_FJ_IDS": [
            {"F_BS": 10, "F_WJ_ID": 456},
            {"F_BS": 20, "F_WJ_ID": 789}
        ]
    }
    ```
    """
    # 解析主体 JSON
    try:
        data: Dict[str, Any] = json.loads(GCXX) if GCXX else {}
    except Exception:
        return err("GCXX 解析失败")
    
    # 可选：倍数数组（与直传文件对齐）
    bs_list: List[Any] | None = None
    if F_FJ_BS:
        try:
            parsed = json.loads(F_FJ_BS)
            if isinstance(parsed, list):
                bs_list = parsed
            else:
                return err("F_FJ_BS 应为JSON数组")
        except Exception:
            return err("F_FJ_BS 解析失败")
    
    # 处理附件（只有明确提供了附件参数时才更新）
    attachments: List[Dict[str, Any]] | None = None
    has_attachment_update = F_FJ or F_FJ_IDS
    
    if has_attachment_update:
        attachments = []
        
        # 路径A：直传文件与倍数一一对应
        if F_FJ:
            if bs_list is None:
                return err("直传文件需同时提供 F_FJ_BS 并与文件数对齐")
            if len(bs_list) != len(F_FJ):
                return err("文件数量与倍数数量不一致")
            for idx, f in enumerate(F_FJ):
                if not f:
                    continue
                dst_path, rel = build_upload_path(f.filename)
                content = await f.read()
                dst_path.write_bytes(content)
                fid = insert_file_record(rel, f.filename)
                if fid is not None:
                    attachments.append({
                        "F_BS": bs_list[idx],
                        "F_WJ_ID": int(fid),
                        "F_URL": format_file_static_url(rel),
                        "F_NAME": f.filename,
                    })
        
        # 路径B：来自文件中心的ID引用
        if F_FJ_IDS:
            try:
                parsed_ids = json.loads(F_FJ_IDS)
                if not isinstance(parsed_ids, list):
                    return err("F_FJ_IDS 应为JSON数组")
                for it in parsed_ids:
                    att = _file_ref_attachment(it)
                    if att:
                        attachments.append(att)
            except Exception:
                return err("F_FJ_IDS 解析失败")
    
    # 自动填充记录人（如果未提供）
    if not data.get("F_JLR") and current_user:
        data["F_JLR"] = current_user.get("F_Ktype") or current_user.get("F_YSM")
    
    operator = (current_user.get("F_Ktype") or current_user.get("F_YSM") or "") if current_user else ""
    
    try:
        res = crud.update_observation(int(F_ID), data, attachments, operator)
        return ok(res, "更新成功")
    except ValueError as ve:
        return err(str(ve))
    except Exception as e:
        return err(f"更新失败: {e}")


class DeleteIn(BaseModel):
    F_ID: int


@router.post("/delete", response_model=ApiResponse[dict], response_model_exclude_none=True,
             dependencies=[Depends(require_roles(["organoid_culture_tissue"]))])
def delete_observation(payload: DeleteIn, current_user: dict = Depends(get_current_user_optional)):
    operator = (current_user.get("F_Ktype") or current_user.get("F_YSM") or "") if current_user else ""
    ok_flag = crud.delete_observation(int(payload.F_ID), operator)
    if not ok_flag:
        return err("未找到记录或删除失败")
    return ok({"F_ID": int(payload.F_ID)}, "删除成功")


class DetailByGdIn(BaseModel):
    F_GDID: int


@router.post("/detail-by-gdid", response_model=ApiResponse[dict], response_model_exclude_none=True,
             dependencies=[Depends(require_roles(["organoid_culture_tissue"]))])
def detail_by_gdid(payload: DetailByGdIn, current_user: dict = Depends(get_current_user_optional)):
    gdid = int(payload.F_GDID)
    from app.core.data_scope import deny_pygd_access
    denied = deny_pygd_access(current_user, gdid, module="organoid_culture_tissue")
    if denied:
        return denied

    # 工单详情（按需要可扩展为单独CRUD，这里复用已存在查询能力）
    order = crud_order.get_culture_order_detail(gdid) if hasattr(crud_order, "get_culture_order_detail") else None

    # 培养箱参数：取最新一条培养明细
    mx = crud_detail.get_mx_by_gdid(gdid)
    incubator = None
    if mx:
        py = mx.get("PYXX_PY") or {}
        incubator = {
            "F_TJPYJ": py.get("F_TJPYJ"),
            "F_PYX_TEMP": py.get("F_PYX_TEMP"),
            "F_PYX_CO2": py.get("F_PYX_CO2"),
            "F_PYX_HUM": py.get("F_PYX_HUM"),
            "F_PYX_FRSJ": py.get("F_PYX_FRSJ"),
        }

    # 观察记录列表
    obs = crud.list_observations_by_gdid(gdid)
    # 收集所有文件ID
    file_ids: List[int] = []
    for it in obs:
        for att in (it.get("F_FJ") or []):
            try:
                file_ids.append(int(att.get("F_WJ_ID")))
            except Exception:
                pass
    files_map: Dict[int, Dict[str, Any]] = {}
    if file_ids:
        recs = get_files_by_ids(sorted(list({i for i in file_ids if i is not None})))
        for r in recs:
            files_map[int(r["F_ID"])] = r
    def _clean_path(raw_path: Any) -> Optional[str]:
        if not raw_path:
            return None
        path = str(raw_path).lstrip("/")
        if path.startswith("uploads/"):
            path = path[8:]
        return path

    for it in obs:
        fj_new = []
        for att in (it.get("F_FJ") or []):
            fid = att.get("F_WJ_ID")
            rec = files_map.get(int(fid)) if fid is not None else None
            url = _clean_path(rec.get("F_PATH")) if rec and rec.get("F_PATH") else None
            code = att.get("F_TPBM")
            item = {
                "F_BS": att.get("F_BS"),
                "F_WJ_ID": fid,
                "F_URL": url,
                "F_TPBM": "" if code is None else str(code),
            }
            if att.get("F_NAME"):
                item["F_NAME"] = att.get("F_NAME")
            fj_new.append(item)
        it["F_FJ"] = fj_new

    # 生长曲线
    diameter_series = []
    cellcount_series = []
    for it in obs:
        day = it.get("F_PYTS")
        if day is not None:
            if it.get("F_PJZJ") is not None:
                diameter_series.append({"day": int(day), "value": it.get("F_PJZJ")})
            xbsl_val = _chart_numeric_value(it.get("F_XBSL"))
            if xbsl_val is not None:
                cellcount_series.append({"day": int(day), "value": xbsl_val})
    diameter_series.sort(key=lambda x: x["day"])
    cellcount_series.sort(key=lambda x: x["day"])

    # 回收记录
    # ✅ get_recycle_by_gdid 已经将 F_FJ 从 ID 数组转换为详情数组（包含 id, path, name）
    # 格式：[{"id": 2555, "path": "2026/01/28/xxx.jpg", "name": "原始文件名.jpg"}]
    recycle = crud_recycle.get_recycle_by_gdid(gdid)

    return ok({
        "order": order or {},
        "incubator": incubator or {},
        "observations": obs,
        "growthCurves": {"diameterSeries": diameter_series, "cellCountSeries": cellcount_series},
        "recycle": recycle or {},
    }, "查询成功")


