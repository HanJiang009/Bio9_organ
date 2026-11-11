from fastapi import APIRouter, Depends, UploadFile, File, Form, Request
from typing import List
from fastapi.responses import FileResponse
from app.schemas.common import ApiResponse
from app.schemas.sample_receive import (
    SampleReceiveQuery, SampleReceiveOut, MarkUnqualifiedIn,
    SampleReceiveDeleteIn, SampleReceiveDeleteOut,
    SampleReceiveUpdateIn, DistributeCreate, DistributeOut,
    DistributeListQuery, DistributeItemOut, DistributeListOut, DistributeListResponse, DistributeUpdate, DistributeDelete,
    DistributeBatchItem, SaveAndDistributeOut,
    YBZJListQuery, YBZJListItem,
    PdoDetailQuery, PdoDetailOut,
    PreviewZZIDRequest, PreviewZZIDResponse,
)
from app.core.response import ok, err
from app.core.auth import require_roles, get_current_user, get_current_user_optional
from app.crud import sample_receive as crud
from app.core.storage import build_upload_path
from app.crud.file_center import get_file_by_id, insert_file_record
import json


def _build_zzid_context(jz: dict, bb: dict, yb: dict) -> dict:
    ctx: dict = {}
    for _src in (jz, bb, yb):
        if _src:
            for _k, _v in _src.items():
                if _v is not None and _k not in ("F_ZZID", "F_ID", "__null_fields__", "F_ZZID_CUSTOM"):
                    ctx.setdefault(_k, _v)
    return ctx


def _peek_zzid_from_context(ctx: dict, logger) -> str | None:
    try:
        from app.crud.code_rule import peek_code as _peek_code
        fzzid = _peek_code("F_ZZID", ctx)
        if fzzid:
            return fzzid
    except Exception as e:
        logger.warning(f"[save-and-distribute] 窥探标本号失败({e})，尝试兜底")
    try:
        from app.crud.code_rule import (
            _replace_datetime_placeholders as _rdt,
            _peek_sequence_placeholders as _psp,
        )
        _tmpl = _rdt("ZZ-{YYYYMMDD}-{SEQ:4}")
        return _psp(_tmpl, "__FALLBACK_ZZID__", {})
    except Exception:
        return None


def _generate_zzid_from_context(ctx: dict, logger) -> str | None:
    try:
        from app.crud.code_rule import generate_code as _gen_code
        fzzid = _gen_code("F_ZZID", ctx)
        if fzzid:
            return fzzid
        raise ValueError("字典编码规则生成的标本号为空")
    except Exception as _e:
        logger.warning(f"[save-and-distribute] 字典编码规则生成标本号失败({_e})，降级硬编码兜底")
        try:
            from app.crud.code_rule import (
                _replace_datetime_placeholders as _rdt,
                _replace_sequence_placeholders as _rsp,
            )
            _tmpl = _rdt("ZZ-{YYYYMMDD}-{SEQ:4}")
            return _rsp(_tmpl, "__FALLBACK_ZZID__", {})
        except Exception as _e2:
            logger.error(f"[save-and-distribute] 硬编码兜底也失败: {_e2}")
            return None


router = APIRouter(prefix="/sample-receive", tags=["sample_receive"])


@router.post("/check-zzid", response_model=ApiResponse[dict], response_model_exclude_none=True,
             dependencies=[Depends(require_roles(["sample_receive"]))])
def check_zzid(payload: dict):
    """
    标本号查重接口（前端实时调用）

    ## 请求
    ```json
    { "F_ZZID": "ZZ-20260420-0001" }
    ```

    ## 返回
    - `exists: false` → 标本号可用
    - `exists: true`  → 已存在，返回已有记录的基础信息（姓名、病历号、登记日期、登记人）
    """
    zzid = (payload.get("F_ZZID") or "").strip()
    if not zzid:
        return err("缺少 F_ZZID")
    hit = crud.check_zzid_exists(zzid)
    if hit:
        return ok({"exists": True, "record": hit}, f"标本号 {zzid} 已存在")
    return ok({"exists": False}, "标本号可用")


@router.post("/query", response_model=ApiResponse[SampleReceiveOut], response_model_exclude_none=False,
             dependencies=[Depends(require_roles(["sample_receive"]))])
def query(payload: SampleReceiveQuery, current_user: dict = Depends(get_current_user_optional)):
    data = crud.query_by_ids(payload.F_ZZID, payload.F_ZYH, payload.F_SJTMH)
    if not data:
        return err("未查询到数据")
    from app.core.data_scope import deny_owner_access
    ybzj = data.get("YBZJ") or data.get("ybzj") or {}
    owner = ybzj.get("F_DJR") if isinstance(ybzj, dict) else None
    if owner is None and isinstance(data, dict):
        owner = data.get("F_DJR")
    denied = deny_owner_access(current_user, owner, module="sample_receive")
    if denied:
        return denied
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
        "F_BHG": "pending",
        "F_BHG_MS": "不合格审批中",
        "F_BHGYY": reason or ""
    }
    
    # 添加质控审核记录ID到返回结果
    if qc_audit_id:
        result["qc_audit_id"] = qc_audit_id
    
    return ok(result, "操作成功")


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
    return ok({"F_ZZID": zzid, "updated": {"T_YBZJ": u3}, "F_FILE_ID": saved_file_id}, "更新完成")


@router.post("/distribute", response_model=ApiResponse[DistributeOut], response_model_exclude_none=True,
             dependencies=[Depends(require_roles(["sample_receive"]))])
def distribute(payload: DistributeCreate, current_user: dict = Depends(get_current_user_optional)):
    if not payload.F_ZZID or payload.F_PYRY is None or not payload.F_MXLX:
        return err("参数不完整")
    creator = (current_user.get("F_Ktype") or current_user.get("F_YSM") or "") if current_user else ""
    try:
        rec = crud.create_distribute(
            payload.F_ZZID, payload.F_PYRY, payload.F_MXLX, creator,
            payload.F_GDLX, qywc=payload.F_QYWC or 0,
        )
    except ValueError as e:
        return err(str(e))
    return ok(rec, "创建成功")


@router.post("/distribute/list", response_model=DistributeListResponse, response_model_exclude_none=False,
             dependencies=[Depends(require_roles(["sample_receive"]))])
def list_distributes(payload: DistributeListQuery, current_user: dict = Depends(get_current_user_optional)):
    result = crud.list_distributes(payload.F_ZZID, payload.F_PYRY, payload.F_MXLX, skip=payload.skip, limit=payload.limit)
    items = result.get("items", [])
    return {
        "code": 1,
        "msg": f"查询成功，共 {len(items)} 条",
        "F_QYWC": result.get("F_QYWC"),
        "data": items,
    }


@router.post("/distribute/update", response_model=ApiResponse[dict], response_model_exclude_none=True,
             dependencies=[Depends(require_roles(["sample_receive"]))])
def distribute_update(payload: DistributeUpdate, current_user: dict = Depends(get_current_user_optional)):
    oked = crud.update_distribute(payload.F_ID, payload.F_PYRY, payload.F_MXLX, payload.F_GDLX, qywc=payload.F_QYWC)
    if not oked:
        return err("仅在‘待创建’状态可修改，或无可更新字段")
    return ok({
        "F_ID": payload.F_ID,
        "F_PYRY": payload.F_PYRY,
        "F_MXLX": payload.F_MXLX,
        "F_GDLX": payload.F_GDLX,
        "F_QYWC": payload.F_QYWC,
    }, "更新成功")


@router.post("/distribute/delete", response_model=ApiResponse[dict], response_model_exclude_none=True,
             dependencies=[Depends(require_roles(["sample_receive"]))])
def distribute_delete(payload: DistributeDelete, current_user: dict = Depends(get_current_user_optional)):
    oked = crud.delete_distribute(payload.F_ID)
    if not oked:
        return err("仅在‘待创建’状态可删除，或记录不存在")
    return ok({"F_ID": payload.F_ID}, "删除成功")


@router.post("/preview-zzid", response_model=ApiResponse[PreviewZZIDResponse],
             dependencies=[Depends(require_roles(["sample_receive"]))])
def preview_zzid(payload: PreviewZZIDRequest):
    """
    预览标本号（预计值，不消耗序列号）。

    仅窥探下一个可用标本号，写入数据库前不会递增编码序列。
    用户关闭登记页不保存时不会产生跳号。

    保存时请仍将返回的 `F_ZZID` 作为 `save-and-distribute` 入参提交；
    新建标本时保存接口会真正分配并消耗序列号（可能与预览值一致，并发时以后端为准）。
    """
    import logging as _logging
    _logger = _logging.getLogger(__name__)

    jz = payload.JZXX or {}
    bb = payload.BBXX or {}
    yb = payload.YBZJ or {}

    # 构建 context（与 save-and-distribute 中完全相同的逻辑）
    ctx: dict = {}
    for src in (jz, bb, yb):
        for k, v in src.items():
            if v is not None and k not in ("F_ZZID", "__null_fields__"):
                ctx.setdefault(k, v)

    source = "code_rule"
    fzzid: str | None = None

    try:
        from app.crud.code_rule import peek_code as _peek_code
        fzzid = _peek_code("F_ZZID", ctx)
        if not fzzid:
            raise ValueError("字典编码规则窥探的标本号为空")
        _logger.info(f"[preview-zzid] 字典编码规则窥探: {fzzid}")
    except Exception as e:
        _logger.warning(f"[preview-zzid] 字典编码规则失败({e})，降级硬编码兜底")
        source = "fallback"
        try:
            from app.crud.code_rule import (
                _replace_datetime_placeholders as _rdt,
                _peek_sequence_placeholders as _psp,
            )
            _FALLBACK_TPL = "ZZ-{YYYYMMDD}-{SEQ:4}"
            _tmpl = _rdt(_FALLBACK_TPL)
            fzzid = _psp(_tmpl, "__FALLBACK_ZZID__", {})
            _logger.info(f"[preview-zzid] 硬编码兜底窥探: {fzzid}")
        except Exception as e2:
            _logger.error(f"[preview-zzid] 兜底也失败: {e2}")
            return err(f"标本号预览失败: {e2}")

    return ok({"F_ZZID": fzzid, "source": source}, "预览成功")


# 合并：保存质检并批量创建分发记录
@router.post("/save-and-distribute", response_model=ApiResponse[SaveAndDistributeOut], response_model_exclude_none=True,
             dependencies=[Depends(require_roles(["sample_receive"]))])
async def save_and_distribute(
    JZXX: str | None = Form(default=None),
    BBXX: str | None = Form(default=None),
    YBZJ: str = Form(default='{}'),
    FFJL: str | None = Form(default=None, description="分发记录数组的JSON字符串"),
    F_ID: str | None = Form(
        default=None,
        description="T_YBZJ 主键：有则编辑，无则新建",
    ),
    F_SYTP: List[UploadFile] = File(default=[]),
    F_ZQTYS_UPLOAD: List[UploadFile] = File(default=[]),
    F_SYTP_IDS_DEL: str | None = Form(default=None, description="需要删除的收样图片ID数组的JSON字符串"),
    F_ZQTYS_IDS_DEL: str | None = Form(default=None, description="需要删除的知情同意书ID数组的JSON字符串"),
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

        # 解析要删除的附件ID列表
        sytp_del_ids = json.loads(F_SYTP_IDS_DEL) if F_SYTP_IDS_DEL else []
        zqtys_del_ids = json.loads(F_ZQTYS_IDS_DEL) if F_ZQTYS_IDS_DEL else []
    except Exception as e:
        return err(f"表单数据解析失败: {str(e)}")

    # 查询数据库中的旧附件值（F_SYTP 现为 JSON 数组字符串，F_ZQTYS_ID 同）
    fzzid_temp = (jz.get("F_ZZID") if jz else None) or (bb.get("F_ZZID") if bb else None) or yb.get("F_ZZID")
    old_sytp_ids: list = []
    old_zqtys_ids: list = []

    if fzzid_temp:
        from app.db.factory import get_adapter
        adapter = get_adapter()
        try:
            with adapter.connect() as conn:
                cur = conn.cursor()
                cur.execute("SELECT F_SYTP, F_ZQTYS_ID FROM T_YBZJ WHERE F_ZZID = ?", (fzzid_temp,))
                row = cur.fetchone()
                if row:
                    # 解析旧收样图片 ID 列表（兼容旧整数值和新 JSON 数组）
                    raw_sytp = row[0] if row[0] else None
                    if raw_sytp is not None:
                        try:
                            if isinstance(raw_sytp, str):
                                parsed = json.loads(raw_sytp)
                                old_sytp_ids = parsed if isinstance(parsed, list) else [int(parsed)]
                            elif isinstance(raw_sytp, int):
                                old_sytp_ids = [raw_sytp]
                        except Exception:
                            try:
                                old_sytp_ids = [int(raw_sytp)]
                            except Exception:
                                pass

                    # 解析旧知情同意书 ID 列表
                    raw_zqtys = row[1] if row[1] else None
                    if raw_zqtys is not None:
                        try:
                            if isinstance(raw_zqtys, str):
                                parsed = json.loads(raw_zqtys)
                                old_zqtys_ids = parsed if isinstance(parsed, list) else [int(parsed)]
                            elif isinstance(raw_zqtys, list):
                                old_zqtys_ids = raw_zqtys
                            elif isinstance(raw_zqtys, int):
                                old_zqtys_ids = [raw_zqtys]
                        except Exception:
                            try:
                                old_zqtys_ids = [int(raw_zqtys)]
                            except Exception:
                                pass
        except Exception as e:
            print(f"警告: 查询旧附件值失败: {e}")

    null_fields: list = yb.get("__null_fields__", [])

    # ── 收样图片处理 ──────────────────────────────────────────────────────────
    deleted_sytp_ids: list = []

    # 方式1: 通过 F_SYTP_IDS_DEL 明确指定删除
    if sytp_del_ids:
        del_set = {int(x) for x in sytp_del_ids}
        existing_sytp_ids = [int(x) for x in (yb.get("F_SYTP_IDS") or old_sytp_ids)]
        deleted_sytp_ids = [x for x in del_set if x in existing_sytp_ids]
        existing_sytp_ids = [x for x in existing_sytp_ids if x not in del_set]
        yb["__sytp_ids__"] = existing_sytp_ids
        if deleted_sytp_ids:
            print(f"已删除收样图片ID: {deleted_sytp_ids}")
    # 方式2: YBZJ 中明确传了空值，视为清空
    elif "F_SYTP" in yb and (yb.get("F_SYTP") is None or yb.get("F_SYTP") == "") and old_sytp_ids:
        deleted_sytp_ids = list(old_sytp_ids)
        yb["__sytp_ids__"] = []
        null_fields.append("F_SYTP")
        print(f"检测到 F_SYTP 被清空，已删除收样图片ID: {deleted_sytp_ids}")
    else:
        # 未操作删除，沿用旧值（或用户传入的 F_SYTP_IDS）
        yb["__sytp_ids__"] = [int(x) for x in (yb.get("F_SYTP_IDS") or old_sytp_ids)]

    # 上传新的收样图片（多文件）
    saved_file_ids: list = []
    for upload_file in (F_SYTP or []):
        if upload_file and upload_file.filename:
            dst_path, rel = build_upload_path(upload_file.filename)
            content = await upload_file.read()
            dst_path.write_bytes(content)
            file_id = insert_file_record(rel, upload_file.filename)
            if file_id is not None:
                yb["__sytp_ids__"].append(int(file_id))
                saved_file_ids.append(int(file_id))

    # 将最终 ID 数组写入 yb（JSON 字符串存 DB）
    final_sytp_ids = yb.pop("__sytp_ids__", [])
    if final_sytp_ids:
        yb["F_SYTP"] = json.dumps(final_sytp_ids, ensure_ascii=False)
    elif "F_SYTP" in null_fields or not old_sytp_ids:
        yb["F_SYTP"] = None
        if "F_SYTP" not in null_fields:
            null_fields.append("F_SYTP")
    # 否则不写 F_SYTP（保持 DB 原值不变）

    # ── 知情同意书处理 ────────────────────────────────────────────────────────
    consent_file_ids: list = []

    # 获取用户传入的知情同意书 ID 列表
    new_zqtys_ids: list = []
    user_provided_zqtys_id = "F_ZQTYS_ID" in yb

    if yb.get("F_ZQTYS_ID"):
        try:
            raw = yb["F_ZQTYS_ID"]
            if isinstance(raw, str):
                parsed = json.loads(raw)
                new_zqtys_ids = parsed if isinstance(parsed, list) else [int(parsed)]
            elif isinstance(raw, list):
                new_zqtys_ids = raw
            elif isinstance(raw, int):
                new_zqtys_ids = [raw]
        except Exception:
            try:
                new_zqtys_ids = [int(yb["F_ZQTYS_ID"])]
            except Exception:
                pass

    deleted_zqtys_ids: list = []

    # 以旧值或用户传入值为基础
    existing_ids = list(new_zqtys_ids) if user_provided_zqtys_id else list(old_zqtys_ids)

    # 方式1: 通过 F_ZQTYS_IDS_DEL 明确指定删除
    if zqtys_del_ids:
        zqtys_del_set = {int(x) for x in zqtys_del_ids}
        before_ids = list(existing_ids)
        existing_ids = [x for x in existing_ids if int(x) not in zqtys_del_set]
        deleted_zqtys_ids = [x for x in zqtys_del_set if x in [int(y) for y in before_ids]]
        if deleted_zqtys_ids:
            print(f"已从知情同意书列表中删除 {len(deleted_zqtys_ids)} 个ID: {deleted_zqtys_ids}")
    # 方式2: 用户明确传了 F_ZQTYS_ID，对比旧值计算删除
    elif user_provided_zqtys_id:
        old_ids_int = [int(x) for x in old_zqtys_ids]
        new_ids_int = [int(x) for x in new_zqtys_ids]
        deleted_zqtys_ids = [x for x in old_ids_int if x not in new_ids_int]
        if deleted_zqtys_ids:
            print(f"检测到知情同意书变化，已删除ID: {deleted_zqtys_ids}")

    # 上传新的知情同意书（多文件）
    uploaded_new_consent = False
    from app.core.storage import build_upload_path_in
    for consent_file in (F_ZQTYS_UPLOAD or []):
        if consent_file and consent_file.filename:
            c_dst_path, c_rel = build_upload_path_in("consents", consent_file.filename)
            c_content = await consent_file.read()
            c_dst_path.write_bytes(c_content)
            c_fid = insert_file_record(c_rel, consent_file.filename)
            if c_fid is not None:
                existing_ids.append(int(c_fid))
                consent_file_ids.append(int(c_fid))
                uploaded_new_consent = True

    # 将 ID 数组写入 yb（只在有变更时更新）
    if user_provided_zqtys_id or zqtys_del_ids or uploaded_new_consent:
        if existing_ids:
            yb["F_ZQTYS_ID"] = json.dumps(existing_ids, ensure_ascii=False)
        else:
            yb["F_ZQTYS_ID"] = None
            null_fields.append("F_ZQTYS_ID")

    # 将需要显式写 NULL 的字段列表传递给 crud
    if null_fields:
        yb["__null_fields__"] = null_fields

    # 统一 F_ZZID（有 F_ID=编辑；无 F_ID=新建）
    import logging as _logging
    _logger = _logging.getLogger(__name__)

    zzid_source: str | None = None

    for _part in (yb, jz, bb):
        if _part:
            _part.pop("F_ID", None)
            _part.pop("F_ZZID_CUSTOM", None)  # 已废弃，忽略

    ybzj_id = crud._parse_optional_int(F_ID)

    user_fzzid = (jz.get("F_ZZID") if jz else None) or (bb.get("F_ZZID") if bb else None) or yb.get("F_ZZID")
    if not user_fzzid and d_list:
        user_fzzid = d_list[0].get("F_ZZID")
    if user_fzzid is not None:
        user_fzzid = str(user_fzzid).strip() or None

    if ybzj_id is not None:
        record = crud.get_ybzj_by_id(ybzj_id)
        if not record:
            return err(f"样本记录 F_ID={ybzj_id} 不存在")
        fzzid = record.get("F_ZZID")
        if not fzzid:
            return err(f"样本记录 F_ID={ybzj_id} 数据异常：缺少 F_ZZID")
        if user_fzzid and user_fzzid != fzzid:
            return err("编辑模式下不允许修改标本号")
        zzid_source = "existing"
        _logger.info(f"[save-and-distribute] 编辑已有标本 F_ID={ybzj_id}, F_ZZID={fzzid}")
    else:
        if user_fzzid and crud.check_zzid_exists(user_fzzid):
            return err(f"标本号 {user_fzzid} 已被占用，请重新预览获取")

        _ctx = _build_zzid_context(jz, bb, yb)
        fzzid = None

        if not user_fzzid:
            fzzid = _generate_zzid_from_context(_ctx, _logger)
            zzid_source = "auto"
            if fzzid:
                _logger.info(f"[save-and-distribute] 新建标本，系统自动分配: {fzzid}")
        else:
            peek_fzzid = _peek_zzid_from_context(_ctx, _logger)
            if peek_fzzid and user_fzzid == peek_fzzid:
                fzzid = _generate_zzid_from_context(_ctx, _logger)
                zzid_source = "auto"
                if fzzid:
                    _logger.info(f"[save-and-distribute] 新建标本，预览号正式分配: {fzzid}")
                if fzzid and user_fzzid != fzzid:
                    _logger.warning(
                        f"[save-and-distribute] 预览标本号 {user_fzzid} 与实际分配 {fzzid} 不一致（可能并发），以实际分配为准"
                    )
            else:
                fzzid = user_fzzid
                zzid_source = "custom"
                _logger.info(f"[save-and-distribute] 新建标本，使用自定义 F_ZZID: {fzzid}")

    if fzzid:
        jz["F_ZZID"] = fzzid
        bb["F_ZZID"] = fzzid
        yb["F_ZZID"] = fzzid
    elif not fzzid:
        return err("缺少 F_ZZID，且自动编码生成失败")

    # 自动填充登记人与日期
    if current_user:
        yb["F_DJR"] = current_user.get("F_Ktype") or current_user.get("F_YSM")
    from datetime import datetime
    yb["F_DJRQ"] = datetime.now().strftime("%Y-%m-%d")

    creator = (current_user.get("F_Ktype") or current_user.get("F_YSM") or "") if current_user else ""

    try:
        res = crud.save_and_distribute(jz, bb, yb, d_list, creator, ybzj_id=ybzj_id)
    except ValueError as e:
        return err(str(e))

    return ok({
        "F_ZZID": fzzid,
        "F_ID": res.get("ybzj_id"),
        "F_ZZID_SOURCE": zzid_source,
        "ybzj_saved": res.get("ybzj_saved", False),
        "distribute_ids": res.get("distribute_ids", []),
        "qc_audit_ids": res.get("qc_audit_ids", []),
        "pdf_id": res.get("pdf_id"),
        "F_FILE_IDS": saved_file_ids,
        "F_ZQTYS_IDS": consent_file_ids,
        "deleted_sytp_ids": deleted_sytp_ids,
        "deleted_zqtys_ids": deleted_zqtys_ids,
    }, "保存成功")


@router.post("/delete", response_model=ApiResponse[SampleReceiveDeleteOut], response_model_exclude_none=True,
             dependencies=[Depends(require_roles(["sample_receive"]))])
def delete_received_sample(payload: SampleReceiveDeleteIn, current_user: dict = Depends(get_current_user_optional)):
    """删除尚未产生分发、冻存或培养工单的标本。已产生时返回固定提示，不删除数据。"""
    record = crud.get_ybzj_by_id(payload.F_ID)
    if not record:
        return err("标本不存在")
    from app.core.data_scope import deny_owner_access
    denied = deny_owner_access(current_user, record.get("F_DJR"), module="sample_receive")
    if denied:
        return denied
    try:
        data = crud.delete_received_sample(payload.F_ID)
    except ValueError as e:
        return err(str(e))
    return ok(data, "删除成功")


@router.post("/ybzj/list", response_model=ApiResponse[dict], response_model_exclude_none=False,
             dependencies=[Depends(require_roles(["sample_receive"]))])
def list_ybzj(payload: YBZJListQuery, current_user: dict = Depends(get_current_user_optional)):
    from app.core.data_scope import resolve_data_scope
    data = crud.list_ybzj(
        payload.model_dump(),
        user=current_user,
        scope=resolve_data_scope(current_user, "sample_receive"),
    )
    return ok(data, f"查询成功，共 {data.get('total', 0)} 条")


@router.post("/ybzj/pdo-detail", response_model=ApiResponse[PdoDetailOut], response_model_exclude_none=True,
             dependencies=[Depends(require_roles(["sample_receive"]))])
def ybzj_pdo_detail(payload: PdoDetailQuery, current_user: dict = Depends(get_current_user_optional)):
    """
    标本接收列表点击某样本类型数量查看样本详情。

    入参：F_ID（T_YBZJ 主键）、F_YBLX（样本类型）
    返参字段对齐弹框：代次/样本编号/样本状态/样本类型/细胞数量/培养工单编号/入库时间
    只返回该标本、该样本类型下的分装记录。
    """
    from app.core.data_scope import deny_sample_access_by_id
    denied = deny_sample_access_by_id(current_user, payload.F_ID, module="sample_receive")
    if denied:
        return denied
    try:
        data = crud.list_pdo_detail_by_ybzj_id(payload.F_ID, payload.F_YBLX)
        return ok(data, f"查询成功，共 {data.get('total', 0)} 条")
    except ValueError as e:
        return err(str(e))
    except Exception as e:
        return err(f"查询失败: {e}")
