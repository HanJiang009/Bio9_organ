from fastapi import APIRouter, Depends, UploadFile, File, Form
from typing import List, Optional
from app.schemas.common import ApiResponse
from app.schemas.organoid_culture_tissue import (
    PendingOrderListQuery, PendingOrderListOut,
    CultureOrderListQuery, CultureOrderListOut,
    CultureOrderDetailQuery, CultureOrderItem, CultureOrderUpdateIn, CultureOrderAttachmentDeleteIn,
    MarkCultureOrdersUnqualifiedIn, MarkCultureOrdersUnqualifiedOut,
    SaveCustomFieldsRequest, UploadAttachmentResponse,
    UpdateOrderStatusRequest, UpdateOrderStatusResponse,
    CultureOrderExportQuery,
    UpdateJHCDCSRequest, UpdateJHCDCSResponse,
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
    from app.core.data_scope import resolve_data_scope
    data = crud.list_pending_orders(
        payload.model_dump(),
        user=current_user,
        scope=resolve_data_scope(current_user, "organoid_culture_tissue"),
    )
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
    # 用户手动指定工单编号（可选；优先级高于字典编码规则）
    gdbh_override = payload.get("F_GDBH") or None
    # 流水线关联（可选）
    pipeline_jm = payload.get("F_PIPELINE_JM") or None
    pipeline_lx = payload.get("F_PIPELINE_LX") or None

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

    try:
        data = crud.create_order_from_ffjl(int(ffjl_id), zzbw, payload.get("F_XYZL"), payload.get("F_QYFS"), ybgg, pysj, file_ids, creator, gdbh_override, pipeline_jm, pipeline_lx)
    except ValueError as e:
        return err(str(e))
    return ok(data, "创建成功")


@router.post("/orders/culture/list", response_model=ApiResponse[CultureOrderListOut], response_model_exclude_none=False,
             dependencies=[Depends(require_roles(["organoid_culture_tissue"]))])
def list_culture_orders(payload: CultureOrderListQuery, current_user: dict = Depends(get_current_user_optional)):
    from app.core.data_scope import resolve_data_scope
    data = crud.list_culture_orders(
        payload.model_dump(),
        user=current_user,
        scope=resolve_data_scope(current_user, "organoid_culture_tissue"),
    )
    return ok(data, f"查询成功，共 {data.get('total', 0)} 条")


@router.post("/orders/culture/detail", response_model=ApiResponse[CultureOrderItem], response_model_exclude_none=False,
             dependencies=[Depends(require_roles(["organoid_culture_tissue"]))])
def culture_order_detail(payload: CultureOrderDetailQuery, current_user: dict = Depends(get_current_user_optional)):
    from app.core.data_scope import deny_pygd_access
    denied = deny_pygd_access(current_user, payload.F_ID, module="organoid_culture_tissue")
    if denied:
        return denied
    it = crud.get_culture_order_detail(payload.F_ID)
    return ok(it or {}, "查询成功" if it else "未找到记录")


@router.post("/orders/culture/update", response_model=ApiResponse[dict], response_model_exclude_none=True,
             dependencies=[Depends(require_roles(["organoid_culture_tissue"]))])
async def culture_order_update(
    GDXX: str = Form(..., description="JSON：{F_ID, F_ZZBW?, F_XYZL?, F_YBLY?, F_YBGG?, F_PYSJ?, F_CDQXBZS?, F_FSQXBZS?}"),
    F_FJ_IDS: str | None = Form(default=None, description="JSON数组：文件ID列表，追加到现有附件中"),
    F_FILES_DELETE: str | None = Form(default=None, description="JSON数组：需要删除的文件ID列表"),
    F_FJ: List[UploadFile] | None = File(None),
    current_user: dict = Depends(get_current_user_optional),
):
    try:
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

        # 获取操作人信息
        operator = (
            current_user.get("F_Ktype")
            or current_user.get("F_YSM")
            or ""
        ) if current_user else ""

        gdid = int(data.get("F_ID"))

        # 空串占位不算“设置流水线”，与 create 侧 coerce 对齐
        if "F_PIPELINE_JM" in data:
            data["F_PIPELINE_JM"] = crud._coerce_pipeline_jm(data.get("F_PIPELINE_JM"))
        if "F_PIPELINE_LX" in data:
            data["F_PIPELINE_LX"] = crud._coerce_pipeline_lx(data.get("F_PIPELINE_LX"))

        new_jm = data.get("F_PIPELINE_JM") if "F_PIPELINE_JM" in data else None
        new_lx = data.get("F_PIPELINE_LX") if "F_PIPELINE_LX" in data else None
        pipeline_being_set = new_jm is not None or new_lx is not None

        # 读取当前工单流水线信息（用于判断是否是"新关联"）
        old_pipeline_jm = None
        old_pipeline_lx = None
        if pipeline_being_set:
            try:
                from app.db.factory import get_adapter as _get_adapter
                from app.crud.organoid_culture_tissue import _deserialize_lx as _dlx
                _rows, _cols = _get_adapter().fetch_all(
                    "SELECT F_PIPELINE_JM, F_PIPELINE_LX FROM T_PYGD WHERE F_ID = ?", [gdid]
                )
                if _rows:
                    old_pipeline_jm = _rows[0][0]
                    old_pipeline_lx = _dlx(_rows[0][1])
            except Exception:
                pass

        # 判断流水线是否"新关联"（之前没有 → 现在有）
        is_new_pipeline = (
            pipeline_being_set
            and not old_pipeline_jm
            and not old_pipeline_lx
        )

        # 若是新关联，先检查工单是否已有业务数据
        if is_new_pipeline:
            from app.crud.pipeline_init import check_order_has_data
            has_data, data_desc = check_order_has_data(gdid)
            if has_data:
                return err(f"工单已有业务数据（{data_desc}），不允许重新关联流水线")

        # ── 执行更新 ─────────────────────────────────────────────────────────────
        res = crud.update_culture_order_with_files(data, add_ids, del_ids, operator=operator)
        if not res:
            return err("记录不存在")

        # ── 流水线初始化（更新完成后，若是新关联则执行）────────────────────────
        if is_new_pipeline:
            try:
                from app.crud.pipeline_init import apply_pipeline_to_order
                init_result = apply_pipeline_to_order(gdid, operator)
                res["pipeline_init"] = init_result
            except Exception as init_err:
                # 初始化失败：回滚已保存的流水线字段
                try:
                    from app.crud.organoid_culture_tissue import _serialize_lx as _slx
                    from app.db.factory import get_adapter as _get_adapter
                    with _get_adapter().connect() as _conn:
                        _conn.cursor().execute(
                            "UPDATE T_PYGD SET F_PIPELINE_JM = ?, F_PIPELINE_LX = ? WHERE F_ID = ?",
                            (old_pipeline_jm, _slx(old_pipeline_lx), gdid)
                        )
                        _conn.commit()
                except Exception as revert_err:
                    import logging as _logging
                    _logging.getLogger(__name__).error(f"[update 流水线] 回滚流水线字段失败: {revert_err}")
                return err(f"工单更新成功，但流水线初始化失败，已回滚流水线关联。原因：{init_err}")

        return ok(res, "更新成功")
    except ValueError as e:
        return err(str(e))
    except Exception as e:
        return err(f"更新失败: {e}")


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
    GDXX: str = Form(..., description="JSON：{F_GDID, F_ZZBW?, F_YBGG?, F_PYSJ?, F_CDQXBZS?}"),
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
    
    pipeline_jm = data.get("F_PIPELINE_JM")
    pipeline_lx = data.get("F_PIPELINE_LX")

    try:
        res = crud.create_passage_order_from_order(
            orig_gdid=gdid,
            zzbw=data.get("F_ZZBW"),
            ybgg=data.get("F_YBGG"),
            pysj_str=data.get("F_PYSJ"),
            file_ids=file_ids or None,
            creator_code=operator,
            pipeline_jm=crud._coerce_pipeline_jm(pipeline_jm),
            pipeline_lx=crud._coerce_pipeline_lx(pipeline_lx),
            cdqxbzs=crud._coerce_cell_total(data.get("F_CDQXBZS")),
        )
        return ok(res, "创建成功")
    except ValueError as e:
        return err(str(e))
    except Exception as e:
        return err(f"创建失败: {e}")


@router.post("/orders/culture/thaw", response_model=ApiResponse[dict], response_model_exclude_none=True,
             dependencies=[Depends(require_roles(["organoid_culture_tissue"]))])
async def create_thaw_order(
    GDXX: str = Form(..., description="JSON：{F_GDID, F_ZZBW?, F_YBGG?, F_PYSJ?} 或 {F_ZZID, ...}（无原培养工单时，直接由标本登记复苏）"),
    F_FJ_IDS: str | None = Form(default=None, description="JSON数组：文件ID列表"),
    F_FJ: List[UploadFile] | None = File(None),
    current_user: dict = Depends(get_current_user),
):
    """创建复苏培养工单：
    - 传 F_GDID：复苏自某张已存在的培养工单（原工单代次为 P0 → 样本来源"原代复苏"，否则"传代复苏"）
    - 不传 F_GDID 但传 F_ZZID：样本管从未走过培养流程（直接由标本登记 T_YBZJ 分装冻存），
      无原工单可复苏，直接以标本登记信息新建一张 P0 代次、样本来源"原代复苏"的工单
    """
    try:
        data = json.loads(GDXX) if GDXX else {}
    except Exception:
        return err("GDXX 解析失败")

    gdid: int | None = None
    if data.get("F_GDID") not in (None, ""):
        try:
            gdid = int(data.get("F_GDID"))
        except Exception:
            return err("非法的 F_GDID")
    zzid = (data.get("F_ZZID") or "").strip() if isinstance(data.get("F_ZZID"), str) else data.get("F_ZZID")
    if not gdid and not zzid:
        return err("缺少 F_GDID 或 F_ZZID（二者至少提供一个）")

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
    
    pipeline_jm = data.get("F_PIPELINE_JM")
    pipeline_lx = data.get("F_PIPELINE_LX")

    try:
        if gdid:
            res = crud.create_thaw_order_from_order(
                orig_gdid=gdid,
                zzbw=data.get("F_ZZBW"),
                ybgg=data.get("F_YBGG"),
                pysj_str=data.get("F_PYSJ"),
                file_ids=file_ids or None,
                creator_code=operator,
                pipeline_jm=crud._coerce_pipeline_jm(pipeline_jm),
                pipeline_lx=crud._coerce_pipeline_lx(pipeline_lx),
                fsqxbzs=crud._coerce_cell_total(data.get("F_FSQXBZS")),
            )
        else:
            res = crud.create_thaw_order_from_ybzj(
                zzid=str(zzid),
                zzbw=data.get("F_ZZBW"),
                ybgg=data.get("F_YBGG"),
                pysj_str=data.get("F_PYSJ"),
                file_ids=file_ids or None,
                creator_code=operator,
                pipeline_jm=crud._coerce_pipeline_jm(pipeline_jm),
                pipeline_lx=crud._coerce_pipeline_lx(pipeline_lx),
                fsqxbzs=crud._coerce_cell_total(data.get("F_FSQXBZS")),
            )
        return ok(res, "创建成功")
    except ValueError as e:
        return err(str(e))
    except Exception as e:
        return err(f"创建失败: {e}")


@router.post("/orders/culture/export", response_model=ApiResponse[dict], response_model_exclude_none=True,
             dependencies=[Depends(require_roles(["organoid_culture_tissue"]))])
def export_culture_orders(payload: CultureOrderExportQuery, current_user: dict = Depends(get_current_user_optional)):
    """
    导出培养工单列表到 Excel
    
    根据与列表接口相同的过滤条件导出数据，支持以下两种模式：
    - 全量导出：不传 F_GDIDS，按其他过滤条件导出（仅限当前用户的工单）
    - 勾选导出：传入 F_GDIDS 工单ID数组，仅导出指定的工单（仍受当前用户权限限制）
    
    Args:
        payload: 查询参数（在列表接口参数基础上新增可选的 F_GDIDS）
        current_user: 当前登录用户
        
    Returns:
        包含文件ID、文件路径、文件URL等信息
    """
    operator = None
    try:
        if current_user:
            operator = current_user.get("F_YSM")
    except Exception:
        pass

    try:
        from app.core.data_scope import resolve_data_scope
        q = payload.model_dump(exclude={"F_GDIDS"})
        result = crud.export_culture_orders(
            q,
            operator=operator,
            user=current_user,
            scope=resolve_data_scope(current_user, "organoid_culture_tissue"),
            order_ids=payload.F_GDIDS or None,
        )
        return ok(result, f"导出成功，共 {result.get('total', 0)} 条")
    except ValueError as e:
        return err(str(e))
    except Exception as e:
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
            operator=operator,
            unqualified_type=payload.F_BHGLX,
        )
        return ok(result, f"已标记 {result['updated_count']} 个工单为不合格")
    except ValueError as e:
        return err(str(e))
    except Exception as e:
        return err(f"标记失败: {e}")


@router.post("/orders/culture/save-custom-fields", response_model=ApiResponse[dict],
             response_model_exclude_none=True, dependencies=[Depends(require_roles(["organoid_culture_tissue"]))])
def save_custom_fields_api(
    payload: SaveCustomFieldsRequest,
    current_user: dict = Depends(get_current_user_optional)
):
    """保存自定义字段（自定义序号和/或实验记录）"""
    try:
        # 至少需要提供一个字段
        if payload.F_ZDYXH is None and not payload.F_SYJL:
            return err("至少需要提供自定义序号或实验记录中的一个字段")
        
        operator = current_user.get("F_Ktype") or current_user.get("F_YSM") if current_user else None
        result = crud.save_custom_fields(
            gdid=payload.F_ID,
            zdyxh=payload.F_ZDYXH,
            syjl=payload.F_SYJL,
            operator=operator
        )
        return ok(result, "保存成功")
    except ValueError as e:
        return err(str(e))
    except Exception as e:
        return err(f"保存失败: {e}")


@router.post("/orders/culture/update-status", response_model=ApiResponse[UpdateOrderStatusResponse],
             response_model_exclude_none=True, dependencies=[Depends(require_roles(["organoid_culture_tissue"]))])
def update_order_status_api(
    payload: UpdateOrderStatusRequest,
    current_user: dict = Depends(get_current_user_optional)
):
    """更新培养工单状态（用户可自由输入任意状态值）"""
    try:
        operator = current_user.get("F_Ktype") or current_user.get("F_YSM") if current_user else None
        result = crud.update_order_status(
            gdid=payload.F_ID,
            status=payload.F_GDZT,
            operator=operator
        )
        
        message = result.get("message")
        if message:
            return ok(result, message)
        else:
            return ok(result, f"工单状态已更新为：{result['F_GDZT']}")
    except ValueError as e:
        return err(str(e))
    except Exception as e:
        return err(f"更新状态失败: {e}")


async def _process_attachment_upload(
    F_ID: int,
    F_FJ: Optional[List[UploadFile]],
    F_BZ: Optional[str],
    F_FJ_IDS_DEL: Optional[str],
    current_user: Optional[dict],
    upload_fn,
) -> dict:
    """三个附件上传端点共用的处理逻辑。F_FJ 可不传（仅更新备注或删除附件）。"""
    operator = current_user.get("F_Ktype") or current_user.get("F_YSM") if current_user else None

    uploaded_ids = []
    for file in (F_FJ or []):
        dst_path, rel = build_upload_path(file.filename)
        content = await file.read()
        dst_path.write_bytes(content)
        file_id = insert_file_record(rel, file.filename)
        if file_id:
            uploaded_ids.append(file_id)

    ids_to_delete = []
    if F_FJ_IDS_DEL:
        try:
            ids_to_delete = json.loads(F_FJ_IDS_DEL)
            if not isinstance(ids_to_delete, list):
                ids_to_delete = []
        except Exception:
            ids_to_delete = []

    return upload_fn(
        gdid=F_ID,
        file_ids=uploaded_ids,
        bz=F_BZ,
        ids_to_delete=ids_to_delete,
        operator=operator,
    )


@router.post("/orders/culture/upload-pathology", response_model=ApiResponse[UploadAttachmentResponse],
             response_model_exclude_none=True, dependencies=[Depends(require_roles(["organoid_culture_tissue"]))])
async def upload_pathology_attachment_api(
    F_ID: int = Form(..., description="工单ID"),
    F_FJ: Optional[List[UploadFile]] = File(default=None, description="病理附件（可不传，仅更新备注时省略）"),
    F_BZ: Optional[str] = Form(default=None, description="共用备注（传值时覆盖，不传时保留原备注）"),
    F_FJ_IDS_DEL: Optional[str] = Form(default=None, description="需要删除的附件ID数组（JSON格式）"),
    current_user: dict = Depends(get_current_user_optional),
):
    """上传病理附件（form-data）。F_FJ 可省略，仅传 F_BZ 时只更新备注。"""
    try:
        result = await _process_attachment_upload(F_ID, F_FJ, F_BZ, F_FJ_IDS_DEL, current_user, crud.upload_pathology_attachment)
        return ok(result, "操作成功")
    except ValueError as e:
        return err(str(e))
    except Exception as e:
        return err(f"操作失败: {e}")


@router.post("/orders/culture/upload-sequencing", response_model=ApiResponse[UploadAttachmentResponse],
             response_model_exclude_none=True, dependencies=[Depends(require_roles(["organoid_culture_tissue"]))])
async def upload_sequencing_attachment_api(
    F_ID: int = Form(..., description="工单ID"),
    F_FJ: Optional[List[UploadFile]] = File(default=None, description="测序附件（可不传，仅更新备注时省略）"),
    F_BZ: Optional[str] = Form(default=None, description="共用备注（传值时覆盖，不传时保留原备注）"),
    F_FJ_IDS_DEL: Optional[str] = Form(default=None, description="需要删除的附件ID数组（JSON格式）"),
    current_user: dict = Depends(get_current_user_optional),
):
    """上传测序附件（form-data）。F_FJ 可省略，仅传 F_BZ 时只更新备注。"""
    try:
        result = await _process_attachment_upload(F_ID, F_FJ, F_BZ, F_FJ_IDS_DEL, current_user, crud.upload_sequencing_attachment)
        return ok(result, "操作成功")
    except ValueError as e:
        return err(str(e))
    except Exception as e:
        return err(f"操作失败: {e}")


@router.post("/orders/culture/upload-other", response_model=ApiResponse[UploadAttachmentResponse],
             response_model_exclude_none=True, dependencies=[Depends(require_roles(["organoid_culture_tissue"]))])
async def upload_other_attachment_api(
    F_ID: int = Form(..., description="工单ID"),
    F_FJ: Optional[List[UploadFile]] = File(default=None, description="其他类附件（可不传，仅更新备注时省略）"),
    F_BZ: Optional[str] = Form(default=None, description="共用备注（传值时覆盖，不传时保留原备注）"),
    F_FJ_IDS_DEL: Optional[str] = Form(default=None, description="需要删除的附件ID数组（JSON格式）"),
    current_user: dict = Depends(get_current_user_optional),
):
    """上传其他类附件（form-data）。F_FJ 可省略，仅传 F_BZ 时只更新备注。"""
    try:
        result = await _process_attachment_upload(F_ID, F_FJ, F_BZ, F_FJ_IDS_DEL, current_user, crud.upload_other_attachment)
        return ok(result, "操作成功")
    except ValueError as e:
        return err(str(e))
    except Exception as e:
        return err(f"操作失败: {e}")


@router.post("/orders/culture/update-jhcdcs", response_model=ApiResponse[UpdateJHCDCSResponse],
             dependencies=[Depends(require_roles(["organoid_culture_tissue"]))])
def update_jhcdcs_api(payload: UpdateJHCDCSRequest):
    """更新培养工单的计划传代次数（代次管理节点专用）。

    - F_JHCDCS 传整数则更新，传 null 则清空。
    """
    try:
        result = crud.update_jhcdcs(payload.F_ID, payload.F_JHCDCS)
        return ok(result, "更新成功")
    except ValueError as e:
        return err(str(e))
    except Exception as e:
        return err(f"更新失败: {e}")