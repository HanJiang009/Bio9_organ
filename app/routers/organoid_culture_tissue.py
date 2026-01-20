from fastapi import APIRouter, Depends, UploadFile, File, Form
from typing import List
from app.schemas.common import ApiResponse
from app.schemas.organoid_culture_tissue import (
    PendingOrderListQuery, PendingOrderListOut,
    CultureOrderListQuery, CultureOrderListOut,
    CultureOrderDetailQuery, CultureOrderItem, CultureOrderUpdateIn, CultureOrderAttachmentDeleteIn,
    MarkCultureOrdersUnqualifiedIn, MarkCultureOrdersUnqualifiedOut,
)
from app.core.response import ok, err
from app.core.auth import require_roles, get_current_user_optional, get_current_user
from app.crud import organoid_culture_tissue as crud
from app.core.storage import build_upload_path
from app.crud.file_center import insert_file_record
import json


router = APIRouter(prefix="/organoid-culture-tissue", tags=["organoid_culture_tissue"])


@router.post("/orders/list", response_model=ApiResponse[PendingOrderListOut], response_model_exclude_none=False,
             dependencies=[Depends(require_roles(["organoid_culture_tissue"]))])
def list_pending_orders(payload: PendingOrderListQuery, current_user: dict = Depends(get_current_user_optional)):
    uid = None
    try:
        uid = int(current_user.get("F_ID")) if current_user and current_user.get("F_ID") is not None else None
    except Exception:
        uid = None
    data = crud.list_pending_orders(payload.model_dump(), user_id=uid)
    return ok(data, f"查询成功，共 {data.get('total', 0)} 条")


@router.post("/orders/create", response_model=ApiResponse[dict], response_model_exclude_none=True,
             dependencies=[Depends(require_roles(["organoid_culture_tissue"]))])
async def create_order(
    GDXX: str = Form(..., description="JSON：{F_FFJL_ID, F_ZZBW?, F_YBGG?, F_PYSJ?}"),
    F_FJ_IDS: str | None = Form(default=None, description="JSON数组：文件ID列表，来自统一上传模块(T_FILES)"),
    F_FJ: List[UploadFile] | None = File(None),
    current_user: dict = Depends(get_current_user_optional),
):
    try:
        payload = json.loads(GDXX) if GDXX else {}
    except Exception:
        return err("JSON 解析失败")

    ffjl_id = payload.get("F_FFJL_ID")
    if not ffjl_id:
        return err("缺少 F_FFJL_ID")
    zzbw = payload.get("F_ZZBW")
    ybgg = payload.get("F_YBGG")
    pysj = payload.get("F_PYSJ")

    # 优先使用统一上传模块返回的文件ID
    file_ids = []
    if F_FJ_IDS:
        try:
            parsed = json.loads(F_FJ_IDS)
            if isinstance(parsed, list):
                for v in parsed:
                    try:
                        file_ids.append(int(v))
                    except Exception:
                        pass
        except Exception:
            return err("F_FJ_IDS 解析失败，应为JSON数组")

    # 若直接传文件：逐个保存并登记 T_FILES 拿到 ID
    if not file_ids and F_FJ:
        for f in F_FJ:
            if not f:
                continue
            dst_path, rel = build_upload_path(f.filename)
            content = await f.read()
            dst_path.write_bytes(content)
            fid = insert_file_record(rel, f.filename)
            if fid is not None:
                file_ids.append(int(fid))

    # 获取操作人信息
    creator = (current_user.get("F_Ktype") or current_user.get("F_YSM") or "") if current_user else ""

    data = crud.create_order_from_ffjl(int(ffjl_id), zzbw, payload.get("F_XYZL"), payload.get("F_QYFS"), ybgg, pysj, file_ids, creator)
    return ok(data, "创建成功")


@router.post("/orders/culture/list", response_model=ApiResponse[CultureOrderListOut], response_model_exclude_none=False,
             dependencies=[Depends(require_roles(["organoid_culture_tissue"]))])
def list_culture_orders(payload: CultureOrderListQuery, current_user: dict = Depends(get_current_user_optional)):
    user_ktype = None
    try:
        user_ktype = str(current_user.get("F_Ktype")) if current_user and current_user.get("F_Ktype") is not None else None
    except Exception:
        user_ktype = None
    data = crud.list_culture_orders(payload.model_dump(), user_ktype=user_ktype)
    return ok(data, f"查询成功，共 {data.get('total', 0)} 条")


@router.post("/orders/culture/detail", response_model=ApiResponse[CultureOrderItem], response_model_exclude_none=False,
             dependencies=[Depends(require_roles(["organoid_culture_tissue"]))])
def culture_order_detail(payload: CultureOrderDetailQuery):
    it = crud.get_culture_order_detail(payload.F_ID)
    return ok(it or {}, "查询成功" if it else "未找到记录")


@router.post("/orders/culture/update", response_model=ApiResponse[dict], response_model_exclude_none=True,
             dependencies=[Depends(require_roles(["organoid_culture_tissue"]))])
async def culture_order_update(
    GDXX: str = Form(..., description="JSON：{F_ID, F_ZZBW?, F_XYZL?, F_YBLY?, F_YBGG?, F_PYSJ?}"),
    F_FJ_IDS: str | None = Form(default=None, description="JSON数组：文件ID列表，追加到现有附件中"),
    F_FILES_DELETE: str | None = Form(default=None, description="JSON数组：需要删除的文件ID列表"),
    F_FJ: List[UploadFile] | None = File(None),
):
    # 解析 GDXX
    try:
        data = json.loads(GDXX) if GDXX else {}
    except Exception:
        return err("GDXX 解析失败")
    if not data.get("F_ID"):
        return err("缺少 F_ID")

    # 解析 F_FJ_IDS
    add_ids: List[int] = []
    if F_FJ_IDS:
        try:
            parsed = json.loads(F_FJ_IDS)
            if isinstance(parsed, list):
                for v in parsed:
                    try:
                        add_ids.append(int(v))
                    except Exception:
                        pass
        except Exception:
            return err("F_FJ_IDS 解析失败")

    # 解析 F_FILES_DELETE
    del_ids: List[int] = []
    if F_FILES_DELETE:
        try:
            parsed = json.loads(F_FILES_DELETE)
            if isinstance(parsed, list):
                for v in parsed:
                    try:
                        del_ids.append(int(v))
                    except Exception:
                        pass
        except Exception:
            return err("F_FILES_DELETE 解析失败")

    # 若直接传文件：保存并登记，ID加入 add_ids
    if F_FJ:
        for f in F_FJ:
            if not f:
                continue
            dst_path, rel = build_upload_path(f.filename)
            content = await f.read()
            dst_path.write_bytes(content)
            from app.crud.file_center import insert_file_record
            fid = insert_file_record(rel, f.filename)
            if fid is not None:
                add_ids.append(int(fid))

    res = crud.update_culture_order_with_files(data, add_ids, del_ids)
    if not res:
        return err("记录不存在")
    return ok(res, "更新成功")


@router.post("/orders/culture/attachment/delete", response_model=ApiResponse[dict], response_model_exclude_none=True,
             dependencies=[Depends(require_roles(["organoid_culture_tissue"]))])
def culture_order_attachment_delete(payload: CultureOrderAttachmentDeleteIn):
    res = crud.remove_culture_attachment(payload.F_ID, payload.F_FILE_ID)
    if not res:
        return err("未找到记录或文件")
    return ok(res, "附件已删除")


@router.post("/orders/culture/pass", response_model=ApiResponse[dict], response_model_exclude_none=True,
             dependencies=[Depends(require_roles(["organoid_culture_tissue"]))])
async def create_passage_order(
    GDXX: str = Form(..., description="JSON：{F_GDID, F_ZZBW?, F_YBGG?, F_PYSJ?}"),
    F_FJ_IDS: str | None = Form(default=None, description="JSON数组：文件ID列表"),
    F_FJ: List[UploadFile] | None = File(None),
    current_user: dict = Depends(get_current_user),
):
    # 解析 GDXX
    try:
        data = json.loads(GDXX) if GDXX else {}
    except Exception:
        return err("GDXX 解析失败")
    try:
        gdid = int(data.get("F_GDID"))
    except Exception:
        return err("缺少或非法的 F_GDID")

    # 收集附件ID
    file_ids: List[int] = []
    if F_FJ_IDS:
        try:
            parsed = json.loads(F_FJ_IDS)
            if isinstance(parsed, list):
                for v in parsed:
                    try:
                        file_ids.append(int(v))
                    except Exception:
                        pass
        except Exception:
            return err("F_FJ_IDS 解析失败")
    if not file_ids and F_FJ:
        for f in F_FJ:
            dst_path, rel = build_upload_path(f.filename)
            content = await f.read()
            dst_path.write_bytes(content)
            fid = insert_file_record(rel, f.filename)
            if fid is not None:
                file_ids.append(int(fid))

    # 获取操作人员编码
    operator = current_user.get("F_Ktype") or ""
    
    try:
        res = crud.create_passage_order_from_order(
            orig_gdid=gdid,
            zzbw=data.get("F_ZZBW"),
            ybgg=data.get("F_YBGG"),
            pysj_str=data.get("F_PYSJ"),
            file_ids=file_ids or None,
            creator_code=operator,
        )
        return ok(res, "创建成功")
    except Exception as e:
        return err(f"创建失败: {e}")


@router.post("/orders/culture/thaw", response_model=ApiResponse[dict], response_model_exclude_none=True,
             dependencies=[Depends(require_roles(["organoid_culture_tissue"]))])
async def create_thaw_order(
    GDXX: str = Form(..., description="JSON：{F_GDID, F_ZZBW?, F_YBGG?, F_PYSJ?}"),
    F_FJ_IDS: str | None = Form(default=None, description="JSON数组：文件ID列表"),
    F_FJ: List[UploadFile] | None = File(None),
    current_user: dict = Depends(get_current_user),
):
    try:
        data = json.loads(GDXX) if GDXX else {}
    except Exception:
        return err("GDXX 解析失败")
    try:
        gdid = int(data.get("F_GDID"))
    except Exception:
        return err("缺少或非法的 F_GDID")

    file_ids: List[int] = []
    if F_FJ_IDS:
        try:
            parsed = json.loads(F_FJ_IDS)
            if isinstance(parsed, list):
                for v in parsed:
                    try:
                        file_ids.append(int(v))
                    except Exception:
                        pass
        except Exception:
            return err("F_FJ_IDS 解析失败")
    if F_FJ:
        for f in F_FJ:
            dst_path, rel = build_upload_path(f.filename)
            content = await f.read()
            dst_path.write_bytes(content)
            fid = insert_file_record(rel, f.filename)
            if fid is not None:
                file_ids.append(int(fid))

    # 获取操作人员编码
    operator = current_user.get("F_Ktype") or ""
    
    try:
        res = crud.create_thaw_order_from_order(
            orig_gdid=gdid,
            zzbw=data.get("F_ZZBW"),
            ybgg=data.get("F_YBGG"),
            pysj_str=data.get("F_PYSJ"),
            file_ids=file_ids or None,
            creator_code=operator,
        )
        return ok(res, "创建成功")
    except Exception as e:
        return err(f"创建失败: {e}")


@router.post("/orders/culture/export", response_model=ApiResponse[dict], response_model_exclude_none=True,
             dependencies=[Depends(require_roles(["organoid_culture_tissue"]))])
def export_culture_orders(payload: CultureOrderListQuery, current_user: dict = Depends(get_current_user_optional)):
    """
    导出培养工单列表到 Excel
    
    根据与列表接口相同的过滤条件导出数据,生成 Excel 文件并保存到文件中心
    
    Args:
        payload: 查询参数(与列表接口相同)
        current_user: 当前登录用户
        
    Returns:
        包含文件ID、文件路径、文件URL等信息
    """
    operator = None
    try:
        operator = current_user.get("F_YSM") if current_user else None
    except Exception:
        operator = None
    
    try:
        result = crud.export_culture_orders(payload.model_dump(), operator=operator)
        return ok(result, f"导出成功，共 {result.get('total', 0)} 条")
    except ValueError as e:
        # 数据量超限或无数据等业务错误
        return err(str(e))
    except Exception as e:
        # 系统错误
        return err(f"导出失败: {e}")


@router.post("/orders/culture/mark-unqualified", response_model=ApiResponse[MarkCultureOrdersUnqualifiedOut],
             response_model_exclude_none=True, dependencies=[Depends(require_roles(["organoid_culture_tissue"]))])
def mark_culture_orders_unqualified(
    payload: MarkCultureOrdersUnqualifiedIn,
    current_user: dict = Depends(get_current_user_optional)
):
    """批量将培养工单标记为不合格"""
    try:
        operator = current_user.get("F_Ktype") or current_user.get("F_YSM") if current_user else None
        result = crud.mark_culture_orders_unqualified(
            gdids=payload.F_GDIDS,
            reason=payload.F_BHGYY,
            operator=operator
        )
        return ok(result, f"已标记 {result['updated_count']} 个工单为不合格")
    except ValueError as e:
        return err(str(e))
    except Exception as e:
        return err(f"标记失败: {e}")