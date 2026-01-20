from fastapi import APIRouter, Depends, UploadFile, File, Form, Request
from fastapi.responses import FileResponse
from app.schemas.common import ApiResponse
from app.schemas.sample_receive import (
    SampleReceiveQuery, SampleReceiveOut, MarkUnqualifiedIn,
    SampleReceiveUpdateIn, DistributeCreate, DistributeOut,
    DistributeListQuery, DistributeItemOut, DistributeUpdate, DistributeDelete,
    DistributeBatchItem, SaveAndDistributeOut,
    YBZJListQuery, YBZJListItem
)
from app.core.response import ok, err
from app.core.auth import require_roles, get_current_user, get_current_user_optional
from app.crud import sample_receive as crud
from app.core.storage import build_upload_path
from app.crud.file_center import get_file_by_id, insert_file_record
import json


router = APIRouter(prefix="/sample-receive", tags=["sample_receive"])


@router.post("/query", response_model=ApiResponse[SampleReceiveOut], response_model_exclude_none=False,
             dependencies=[Depends(require_roles(["sample_receive"]))])
def query(payload: SampleReceiveQuery):
    data = crud.query_by_ids(payload.F_ZZID, payload.F_ZYH, payload.F_SJTMH)
    if not data:
        return err("未查询到数据")
    return ok(data, "查询成功")

#录入接收质检信息（可伴随添加就诊信息和标本信息录入）
@router.post("/save", response_model=ApiResponse[dict], response_model_exclude_none=True,
             dependencies=[Depends(require_roles(["sample_receive"]))])
async def save(
    # JSON 串（由前端以 multipart 字段提交）
    JZXX: str | None = Form(default=None),
    BBXX: str | None = Form(default=None),
    # 质检信息可不传
    YBZJ: str = Form(default='{}'),
    # 图片文件（可选）
    F_SYTP: UploadFile | None = File(None),
    request: Request = None,
    current_user: dict = Depends(get_current_user),
):
    try:
        jz = json.loads(JZXX) if JZXX else {}
        bb = json.loads(BBXX) if BBXX else {}
        yb = json.loads(YBZJ) if YBZJ else {}
    except Exception:
        return err("表单数据解析失败")

    # 仅支持接收文件：保存本地并登记 T_FILES，返回 F_FILE_ID 与路径
    saved_file_id = None
    if F_SYTP is not None:
        dst_path, rel = build_upload_path(F_SYTP.filename)
        content = await F_SYTP.read()
        dst_path.write_bytes(content)
        file_id = insert_file_record(rel, F_SYTP.filename)
        if file_id is not None:
            yb["F_SYTP"] = int(file_id)
            saved_file_id = file_id

    # 规范关键键位：确保三者使用同一 F_ZZID（允许仅在 YBZJ 中提供）
    fzzid = (jz.get("F_ZZID") if jz else None) or (bb.get("F_ZZID") if bb else None) or yb.get("F_ZZID")
    if fzzid:
        jz["F_ZZID"] = fzzid
        bb["F_ZZID"] = fzzid
        yb["F_ZZID"] = fzzid

    # 规范：若 YBZJ 需要关联，则写入 F_ZZID
    if bb.get("F_ZZID") and "F_ZZID" not in yb:
        yb["F_ZZID"] = bb.get("F_ZZID")

    # 自动填充登记人/登记时间
    if current_user:
        # 优先采用账号姓名 F_Ktype，若无则回退工号 F_YSM
        yb["F_DJR"] = current_user.get("F_Ktype") or current_user.get("F_YSM")

    # 当前日期（YYYY-MM-DD）
    from datetime import datetime
    yb["F_DJRQ"] = datetime.now().strftime("%Y-%m-%d")

    crud.save_all(jz, bb, yb)
    return ok({"YBZJ": yb, "F_FILE_ID": saved_file_id }, "保存成功")


@router.post("/mark-unqualified", response_model=ApiResponse[dict], response_model_exclude_none=True,
             dependencies=[Depends(require_roles(["sample_receive"]))])
def mark_unqualified(payload: MarkUnqualifiedIn, current_user: dict = Depends(get_current_user_optional)):
    zzid = payload.F_ZZID
    reason = payload.F_BHGYY
    if not zzid:
        return err("缺少 F_ZZID")
    
    # 获取操作人信息
    creator = (current_user.get("F_Ktype") or current_user.get("F_YSM") or "") if current_user else ""
    
    # 标记为不合格并创建质控审核记录
    qc_audit_id = crud.mark_unqualified(zzid, reason, creator)
    
    result = {
        "F_ZZID": zzid,
        "F_BHG": "true",
        "F_BHGYY": reason or ""
    }
    
    # 添加质控审核记录ID到返回结果
    if qc_audit_id:
        result["qc_audit_id"] = qc_audit_id
    
    return ok(result, "已标为不合格")


@router.post("/update", response_model=ApiResponse[dict], response_model_exclude_none=True,
             dependencies=[Depends(require_roles(["sample_receive"]))])
async def update(
    JZXX: str | None = Form(default=None),
    BBXX: str | None = Form(default=None),
    YBZJ: str | None = Form(default=None),
    F_SYTP: UploadFile | None = File(None),
    request: Request = None,
    current_user: dict = Depends(get_current_user),
):
    import json
    # 解析 JSON 片段
    try:
        jz = json.loads(JZXX) if JZXX else {}
        bb = json.loads(BBXX) if BBXX else {}
        yb = json.loads(YBZJ) if YBZJ else {}
    except Exception:
        return err("表单数据解析失败")

    # 读取 F_ZZID
    zzid = (jz.get("F_ZZID") if jz else None) or (bb.get("F_ZZID") if bb else None) or (yb.get("F_ZZID") if yb else None)
    if not zzid:
        return err("缺少 F_ZZID")

    # 仅支持接收文件
    saved_file_id = None
    if F_SYTP is not None:
        dst_path, rel = build_upload_path(F_SYTP.filename)
        content = await F_SYTP.read()
        dst_path.write_bytes(content)
        file_id = insert_file_record(rel, F_SYTP.filename)
        if file_id is not None:
            yb["F_SYTP"] = int(file_id)
            saved_file_id = file_id

    # 不允许通过此接口更改主键
    jz.pop("F_ZZID", None)
    bb.pop("F_ZZID", None)
    yb.pop("F_ZZID", None)

    # 仅更新存在的记录
    u1, u2, u3 = crud.update_partial(zzid, jz, bb, yb)
    return ok({"F_ZZID": zzid, "updated": {"T_ZZXX": u1, "T_SGXX": u2, "T_YBZJ": u3}, "F_FILE_ID": saved_file_id}, "更新完成")


@router.post("/distribute", response_model=ApiResponse[DistributeOut], response_model_exclude_none=True,
             dependencies=[Depends(require_roles(["sample_receive"]))])
def distribute(payload: DistributeCreate, current_user: dict = Depends(get_current_user_optional)):
    if not payload.F_ZZID or payload.F_PYRY is None or not payload.F_MXLX:
        return err("参数不完整")
    creator = (current_user.get("F_Ktype") or current_user.get("F_YSM") or "") if current_user else ""
    rec = crud.create_distribute(payload.F_ZZID, payload.F_PYRY, payload.F_MXLX, creator, payload.F_GDLX)
    return ok(rec, "创建成功")


@router.post("/distribute/list", response_model=ApiResponse[list[DistributeItemOut]], response_model_exclude_none=True,
             dependencies=[Depends(require_roles(["sample_receive"]))])
def list_distributes(payload: DistributeListQuery, current_user: dict = Depends(get_current_user_optional)):
    items = crud.list_distributes(payload.F_ZZID, payload.F_PYRY, payload.F_MXLX, skip=payload.skip, limit=payload.limit)
    return ok(items, f"查询成功，共 {len(items)} 条")


@router.post("/distribute/update", response_model=ApiResponse[dict], response_model_exclude_none=True,
             dependencies=[Depends(require_roles(["sample_receive"]))])
def distribute_update(payload: DistributeUpdate, current_user: dict = Depends(get_current_user_optional)):
    oked = crud.update_distribute(payload.F_ID, payload.F_PYRY, payload.F_MXLX, payload.F_GDLX)
    if not oked:
        return err("仅在‘待创建’状态可修改，或无可更新字段")
    return ok({"F_ID": payload.F_ID, "F_PYRY": payload.F_PYRY, "F_MXLX": payload.F_MXLX, "F_GDLX": payload.F_GDLX}, "更新成功")


@router.post("/distribute/delete", response_model=ApiResponse[dict], response_model_exclude_none=True,
             dependencies=[Depends(require_roles(["sample_receive"]))])
def distribute_delete(payload: DistributeDelete, current_user: dict = Depends(get_current_user_optional)):
    oked = crud.delete_distribute(payload.F_ID)
    if not oked:
        return err("仅在‘待创建’状态可删除，或记录不存在")
    return ok({"F_ID": payload.F_ID}, "删除成功")


# 合并：保存质检并批量创建分发记录
@router.post("/save-and-distribute", response_model=ApiResponse[SaveAndDistributeOut], response_model_exclude_none=True,
             dependencies=[Depends(require_roles(["sample_receive"]))])
async def save_and_distribute(
    JZXX: str | None = Form(default=None),
    BBXX: str | None = Form(default=None),
    YBZJ: str = Form(default='{}'),
    FFJL: str | None = Form(default=None, description="分发记录数组的JSON字符串"),
    F_SYTP: UploadFile | None = File(None),
    F_ZQTYS_UPLOAD: UploadFile | None = File(None),
    request: Request = None,
    current_user: dict = Depends(get_current_user),
):
    try:
        jz = json.loads(JZXX) if JZXX else {}
        bb = json.loads(BBXX) if BBXX else {}
        yb = json.loads(YBZJ) if YBZJ else {}
        d_raw = json.loads(FFJL) if FFJL else []
        # 兼容：FFJL 可为对象或数组
        if isinstance(d_raw, dict):
            d_list = [d_raw]
        elif isinstance(d_raw, list):
            d_list = d_raw
        else:
            d_list = []
    except Exception:
        return err("表单数据解析失败")

    # 仅支持接收文件
    saved_file_id = None
    if F_SYTP is not None:
        dst_path, rel = build_upload_path(F_SYTP.filename)
        content = await F_SYTP.read()
        dst_path.write_bytes(content)
        file_id = insert_file_record(rel, F_SYTP.filename)
        if file_id is not None:
            yb["F_SYTP"] = int(file_id)
            saved_file_id = file_id

    # 知情同意书专用目录 uploads/consents 下保存，支持多份文件，存储为JSON数组
    consent_file_ids = []
    
    # 获取已有的知情同意书ID列表
    existing_ids = []
    if yb.get("F_ZQTYS_ID"):
        try:
            # 尝试解析为JSON数组
            if isinstance(yb["F_ZQTYS_ID"], str):
                existing_ids = json.loads(yb["F_ZQTYS_ID"])
            elif isinstance(yb["F_ZQTYS_ID"], list):
                existing_ids = yb["F_ZQTYS_ID"]
            elif isinstance(yb["F_ZQTYS_ID"], int):
                existing_ids = [yb["F_ZQTYS_ID"]]
        except Exception:
            pass
    
    # 上传新文件
    if F_ZQTYS_UPLOAD is not None:
        from app.core.storage import build_upload_path_in
        c_dst_path, c_rel = build_upload_path_in("consents", F_ZQTYS_UPLOAD.filename)
        c_content = await F_ZQTYS_UPLOAD.read()
        c_dst_path.write_bytes(c_content)
        c_fid = insert_file_record(c_rel, F_ZQTYS_UPLOAD.filename)
        if c_fid is not None:
            existing_ids.append(int(c_fid))
            consent_file_ids.append(int(c_fid))
    
    # 将ID数组存储为JSON字符串
    if existing_ids:
        yb["F_ZQTYS_ID"] = json.dumps(existing_ids, ensure_ascii=False)

    # 统一 F_ZZID
    fzzid = (jz.get("F_ZZID") if jz else None) or (bb.get("F_ZZID") if bb else None) or yb.get("F_ZZID")
    if not fzzid and d_list:
        # 从首条分发中兜底读取
        fzzid = d_list[0].get("F_ZZID")
    if fzzid:
        jz["F_ZZID"] = fzzid
        bb["F_ZZID"] = fzzid
        yb["F_ZZID"] = fzzid
    else:
        return err("缺少 F_ZZID")

    # 自动填充登记人与日期
    if current_user:
        yb["F_DJR"] = current_user.get("F_Ktype") or current_user.get("F_YSM")
    from datetime import datetime
    yb["F_DJRQ"] = datetime.now().strftime("%Y-%m-%d")

    creator = (current_user.get("F_Ktype") or current_user.get("F_YSM") or "") if current_user else ""

    # 执行事务性保存（包含质控审核记录）
    res = crud.save_and_distribute(jz, bb, yb, d_list, creator)

    return ok({
        "F_ZZID": fzzid,
        "ybzj_saved": res.get("ybzj_saved", False),
        "distribute_ids": res.get("distribute_ids", []),
        "qc_audit_ids": res.get("qc_audit_ids", []),
        "pdf_id": res.get("pdf_id"),
        "F_FILE_ID": saved_file_id,
        "F_ZQTYS_IDS": consent_file_ids,  # 返回新上传的知情同意书ID数组
    }, "保存成功")


@router.post("/ybzj/list", response_model=ApiResponse[dict], response_model_exclude_none=False,
             dependencies=[Depends(require_roles(["sample_receive"]))])
def list_ybzj(payload: YBZJListQuery):
    data = crud.list_ybzj(payload.model_dump())
    return ok(data, f"查询成功，共 {data.get('total', 0)} 条")
