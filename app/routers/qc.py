from fastapi import APIRouter, Depends, UploadFile, File, Form, Body, BackgroundTasks
from typing import Dict, Any, List, Optional
import json
from app.schemas.common import ApiResponse
from app.core.response import ok, err
from app.core.auth import require_roles, get_current_user_optional
from app.crud import organoid_culture_tissue as crud_order
from app.crud import preprocess as crud_pre
from app.crud import organoid_observation as crud_obs
from app.crud.sample_receive import query_by_ids
from app.crud.file_center import get_files_by_ids
from app.core.settings import settings
from datetime import datetime
from app.core.docx_pdf import render_pdf_with_images
from app.crud.file_center import insert_file_record
from pathlib import Path


router = APIRouter(prefix="/qc", tags=["qc"])


def _deny_gdid(user, gdid, module="organoid_culture_tissue"):
    from app.core.data_scope import deny_pygd_access
    return deny_pygd_access(user, gdid, module=module)


def _deny_zzid(user, zzid, module="sample_receive"):
    from app.core.data_scope import deny_sample_access_by_zzid
    return deny_sample_access_by_zzid(user, zzid, module=module)


def _parse_file_ids_del(raw: str | None) -> tuple[set[int], str | None]:
    """解析要解除关联的文件 ID。

    接受 JSON 数组（[4241,4242]）、逗号分隔（4241,4242）和单个数字。
    空值表示不删除。无法解析时返回错误说明。
    """
    if raw is None:
        return set(), None
    text = str(raw).strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in ('"', "'"):
        text = text[1:-1].strip()
    if not text or text.lower() in ("null", "none"):
        return set(), None

    values: List[Any]
    if text.startswith("["):
        try:
            parsed = json.loads(text)
        except Exception:
            parsed = None
        if isinstance(parsed, list):
            values = parsed
        elif isinstance(parsed, (int, float)) and not isinstance(parsed, bool):
            values = [parsed]
        else:
            return set(), "F_FILE_IDS_DEL 格式不正确，请传 JSON 数组或逗号分隔的文件ID"
    else:
        values = [part.strip() for part in text.replace("，", ",").split(",")]

    ids: set[int] = set()
    for item in values:
        if item is None or (isinstance(item, str) and not item.strip()):
            continue
        try:
            if isinstance(item, bool) or (isinstance(item, float) and not item.is_integer()):
                raise ValueError
            ids.add(int(str(item).strip()))
        except (TypeError, ValueError):
            return set(), "F_FILE_IDS_DEL 包含无法识别的文件ID"
    return ids, None


_VERIFY_SCALAR_LABELS = {
    "F_SZQK": "生长情况",
    "F_PYTS": "培养天数",
    "F_XT": "形态",
    "F_SFWR": "是否污染",
    "F_XBSL": "细胞数量",
    "F_XBHL": "细胞活率",
    "F_ZYTJCJG": "支原体检测结果",
}
_VERIFY_IMAGE_LABELS = {
    "F_IMG_D1M4": "接种第一天明场4倍图",
    "F_IMG_D1M20": "接种第一天明场20倍图",
    "F_IMG_D3M4": "接种第三天明场4倍图",
    "F_IMG_D3M20": "接种第三天明场20倍图",
    "F_IMG_D7M4": "最终明场4倍图",
    "F_IMG_D7M20": "最终明场20倍图",
}


def _blank_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _file_id_list(value: Any) -> List[int]:
    """从文件 ID、ID 数组或 {id/F_WJ_ID} 对象数组中取出文件 ID，保持原顺序。"""
    if value is None or value == "":
        return []
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return []
        try:
            value = json.loads(text)
        except Exception:
            try:
                return [int(text)]
            except (TypeError, ValueError):
                return []
    if isinstance(value, dict):
        value = [value]
    if not isinstance(value, list):
        try:
            return [int(value)]
        except (TypeError, ValueError):
            return []
    ids: List[int] = []
    for item in value:
        raw = item
        if isinstance(item, dict):
            raw = item.get("id", item.get("F_WJ_ID", item.get("F_ID")))
        if raw is None or raw == "":
            continue
        try:
            ids.append(int(raw))
        except (TypeError, ValueError):
            continue
    return ids


def _file_change_items(ids: List[int]) -> List[Dict[str, Any]]:
    """变更展示用的附件值，结构与观察记录附件变更一致。"""
    if not ids:
        return []
    by_id: Dict[int, Dict[str, Any]] = {}
    try:
        for info in get_files_by_ids(ids):
            try:
                by_id[int(info.get("F_ID"))] = info
            except (TypeError, ValueError):
                continue
    except Exception:
        by_id = {}
    items: List[Dict[str, Any]] = []
    for fid in ids:
        info = by_id.get(fid) or {}
        path = str(info.get("F_PATH") or "").replace("\\", "/").lstrip("/")
        items.append({
            "id": fid,
            "path": path,
            "name": info.get("F_ORIGINAL_NAME") or "",
        })
    return items


def _lqg_from_old_data(raw: Any) -> Dict[str, Any] | None:
    """从上一次提交备份的概览里取出类器官质控表单。读不到时返回 None。"""
    if raw is None:
        return None
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8", errors="replace")
    if isinstance(raw, str):
        text = raw.strip()
        if not text:
            return None
        try:
            raw = json.loads(text)
        except Exception:
            return None
    if not isinstance(raw, dict):
        return None
    form = raw.get("LQGZKJL")
    return form if isinstance(form, dict) else None


def _verification_field_changes(
    old_form: Dict[str, Any],
    new_form: Dict[str, Any],
    new_images: Dict[str, List[int]],
) -> List[Dict[str, Any]]:
    """对比驳回前的验证单和本次重新提交，生成「变更字段」。"""
    from app.crud.qc_audit import calculate_changes

    old_scalars = {key: _blank_text(old_form.get(key)) for key in _VERIFY_SCALAR_LABELS}
    new_scalars = {key: _blank_text(new_form.get(key)) for key in _VERIFY_SCALAR_LABELS}
    diff = calculate_changes(old_scalars, new_scalars, _VERIFY_SCALAR_LABELS)
    items = list(diff.get("变更字段") or [])
    for field, label in _VERIFY_IMAGE_LABELS.items():
        old_ids = _file_id_list(old_form.get(field))
        new_ids = _file_id_list(new_images.get(field))
        if old_ids == new_ids:
            continue
        items.append({
            "field": field,
            "label": label,
            "old_value": _file_change_items(old_ids),
            "new_value": _file_change_items(new_ids),
        })
    return items


def _qc_overview_result(gdid: int):
    """组装工单质控概览。供接口与 submit/save-draft 内部复用，不走 FastAPI Depends。"""
    try:
        from app.crud.qc_record import get_qc_overview
        data = get_qc_overview(gdid)
        if data is None:
            return err(f"未找到工单 (F_GDID={gdid})")
        return ok(data, "查询成功")
    except Exception as e:
        import traceback
        return err(f"查询失败: {str(e)}\n{traceback.format_exc()}")


@router.post("/overview-by-gdid", response_model=ApiResponse[dict], response_model_exclude_none=True,
             dependencies=[Depends(require_roles(["qc_review"]))])
def overview_by_gdid(payload: Dict[str, Any], current_user: dict = Depends(get_current_user_optional)):
    try:
        gdid = int(payload.get("F_GDID"))
    except Exception as e:
        return err(f"缺少或非法的 F_GDID: {str(e)}")
    from app.core.data_scope import deny_pygd_access
    denied = deny_pygd_access(current_user, gdid, module="qc_review")
    if denied:
        return denied
    return _qc_overview_result(gdid)


@router.post("/submit", response_model=ApiResponse[dict], response_model_exclude_none=True,
             dependencies=[Depends(require_roles(["organoid_culture_tissue"]))])
async def submit(
    F_GDID: int = Form(...),
    F_SZQK: str = Form(default=""),
    F_PYTS: str = Form(default=""),  # 改为str，函数内部转换
    F_XT: str = Form(default=""),
    F_SFWR: str = Form(default=""),  # 改为str，函数内部转换
    F_XBSL: str = Form(default=""),
    F_XBHL: str = Form(default=""),  # 改为str，函数内部转换
    F_ZYTJCJG: str = Form(default=""),
    F_ZKJD: str = Form(default="验证单"),
    F_PYR: str = Form(default=""),
    F_FILE_IDS_DEL: str = Form(default=None, description="需要解除关联的文件ID，JSON数组 [123,456] 或逗号分隔 123,456"),
    # 用户上传的图片（每个槽位支持多附件）
    IMG_DAY1_BS4:  List[UploadFile] = File(default=None),
    IMG_DAY1_BS20: List[UploadFile] = File(default=None),
    IMG_DAY3_BS4:  List[UploadFile] = File(default=None),
    IMG_DAY3_BS20: List[UploadFile] = File(default=None),
    IMG_DAY7_BS4:  List[UploadFile] = File(default=None),
    IMG_DAY7_BS20: List[UploadFile] = File(default=None),
    IMG_YBYCL:     List[UploadFile] = File(default=None),
    background_tasks: BackgroundTasks = None,  # 暂不使用，PDF生成已改为审核通过后触发
    current_user: dict = Depends(get_current_user_optional),
):
    """
    提交质控单接口（验证单阶段）

    此接口用于提交验证单，质控阶段固定为"验证单"。
    验证单审核通过后会生成放行检测报告，并回写工单质控状态。

    每个图片槽位支持多附件上传，合并策略：
      最终文件列表 = (LQGZKJL现有IDs) - (F_FILE_IDS_DEL) + (本次新上传IDs)

    注意：提交时不生成PDF，PDF将在验证单审核通过后才生成。
    （原提交时生成PDF的逻辑已注释保留，如需恢复请搜索 "# [PDF-SUBMIT]" 标记）
    """
    try:
        gdid = int(F_GDID)
    except Exception:
        return err("缺少或非法的 F_GDID")
    
    # 辅助函数：安全转换字符串为int/float，空字符串返回None
    def safe_int(val: str) -> int | None:
        if not val or val.strip() == "":
            return None
        try:
            return int(val)
        except Exception:
            return None
    
    def safe_float(val: str) -> float | None:
        if not val or val.strip() == "":
            return None
        try:
            return float(val)
        except Exception:
            return None
    
    # 转换数值类型字段
    pyts_val = safe_int(F_PYTS)
    sfwr_val = safe_int(F_SFWR)
    xbhl_val = safe_float(F_XBHL)
    
    # 构建 payload 字典(兼容原有逻辑)
    payload = {
        "F_GDID": gdid,
        "F_SZQK": F_SZQK,
        "F_PYTS": pyts_val,
        "F_XT": F_XT,
        "F_SFWR": sfwr_val,
        "F_XBSL": F_XBSL,
        "F_XBHL": xbhl_val,
        "F_ZYTJCJG": F_ZYTJCJG,
        "F_ZKJD": F_ZKJD,
        "F_PYR": F_PYR,
    }

    denied = _deny_gdid(current_user, gdid)
    if denied:
        return denied

    # 复用概览数据（直接查库，不调用带 Depends 的路由函数）
    overview = _qc_overview_result(gdid)
    if (overview or {}).get("code") != 1:
        return err((overview or {}).get("msg") or "无法生成概览数据")
    data = (overview or {}).get("data") or {}

    gdxx = data.get("GDXX") or {}
    ybzkjl = data.get("YBZKJL") or {}
    lqgzkjl = data.get("LQGZKJL") or {}
    # 合并本次表单前的质控内容。驳回后再提交时，若上次备份读不到，用它做对比基准。
    lqgzkjl_before_submit = dict(lqgzkjl)

    # 合并用户填写字段到 LQGZKJL
    for k in ("F_SZQK", "F_XT", "F_SFWR", "F_XBSL", "F_XBHL", "F_ZYTJCJG", "F_PYTS"):
        if k in payload:
            lqgzkjl[k] = payload.get(k)

    # 质控审核记录策略（同一工单+同一 F_ZKJD 可有多条，取最新一条判断当前状态）：
    # - 无记录 → INSERT（首次提交）
    # - 最新为「待审核」→ UPDATE 覆盖（审核前修改）
    # - 最新为「审核不通过」→ INSERT 新记录（驳回后重新提交，保留历史）
    # - 最新为「审核通过」→ 拒绝（终态不可再提）
    from app.db.factory import get_adapter
    adapter = get_adapter()
    latest: Dict[str, Any] | None = None
    existing: Dict[str, Any] | None = None   # 待 UPDATE 的记录（仅待审核）
    prev_rejected: Dict[str, Any] | None = None  # 驳回记录（用于重新提交关联）
    resubmit = False
    with adapter.connect() as conn:
        cur = conn.cursor()
        from app.crud.qc_audit import zkjd_variants
        _zkjd_base, _zkjd_tpl = zkjd_variants(F_ZKJD)
        sql = """SELECT F_ID, F_ZKBH, F_PDF, F_SHZT, F_ZKJD, F_DCID, F_DC, F_ZZID, 
                        F_XM, F_BRBH, F_ZZBW, F_YBLY, F_KSPYRQ, F_PYTS, F_PYR, F_SHBZ
                 FROM T_ZKSH WHERE F_GDBH = ? AND F_ZKJD IN (?, ?) ORDER BY F_ID DESC"""
        paginated_sql, extra = adapter.paginate(sql, 0, 1)
        cur.execute(paginated_sql, (gdxx.get("F_GDBH"), _zkjd_base, _zkjd_tpl) + tuple(extra))
        row_exist = cur.fetchone()
        if row_exist:
            cols = [c[0] for c in cur.description]
            latest = dict(zip(cols, row_exist))
            shzt_val = str((latest.get("F_SHZT") or "")).strip()
            if shzt_val == "待审核":
                existing = latest
            elif shzt_val == "审核通过":
                return err(f"该工单的【{F_ZKJD}】阶段已审核通过，不能重复提交")
            elif shzt_val == "审核不通过":
                prev_rejected = latest
                resubmit = True
            else:
                return err(f"该工单的【{F_ZKJD}】阶段状态异常（{shzt_val}），无法提交")

    # [PDF-SUBMIT] 组装渲染上下文与图片路径（提交时生成PDF所需，已注释）
    # ctx: Dict[str, Any] = {}
    # ctx.update({f"GDXX_{k}": v for k, v in gdxx.items()})
    # ctx.update({f"YBZKJL_{k}": v for k, v in ybzkjl.items()})
    # ctx.update({f"LQGZKJL_{k}": v for k, v in lqgzkjl.items()})

    # ===== 多附件图片处理 =====
    from app.core.storage import build_upload_path_in
    from app.crud.file_center import insert_file_record as insert_file

    # 解析要删除的文件 ID
    _submit_del_ids, del_err = _parse_file_ids_del(F_FILE_IDS_DEL)
    if del_err:
        return err(del_err)

    async def _save_file_list_submit(upload_files) -> List[int]:
        """保存一组上传文件并注册到文件中心，返回文件ID列表。"""
        new_ids: List[int] = []
        for uf in (upload_files or []):
            if not uf or not getattr(uf, "filename", None):
                continue
            try:
                timestamp = datetime.now().strftime("%Y%m%d%H%M%S%f")
                filename = f"qc_{gdid}_{timestamp}_{uf.filename}"
                dst_path, _ = build_upload_path_in("qc_images", filename)
                dst_path.write_bytes(await uf.read())
                rel_path = str(dst_path.relative_to(Path(settings.upload_dir))).replace("\\", "/")
                fid = insert_file(rel_path, uf.filename)
                if fid is not None:
                    new_ids.append(int(fid))
                    print(f"[submit] 注册上传图片: {uf.filename} -> 文件ID={fid}")
            except Exception as e:
                print(f"[submit] 保存/注册图片失败: {e}")
        return new_ids

    _SLOT_TO_LQ_SUBMIT = {
        "IMG_DAY1_BS4":  "F_IMG_D1M4",
        "IMG_DAY1_BS20": "F_IMG_D1M20",
        "IMG_DAY3_BS4":  "F_IMG_D3M4",
        "IMG_DAY3_BS20": "F_IMG_D3M20",
        "IMG_DAY7_BS4":  "F_IMG_D7M4",
        "IMG_DAY7_BS20": "F_IMG_D7M20",
    }
    _slot_uploads_submit = {
        "IMG_DAY1_BS4":  IMG_DAY1_BS4,
        "IMG_DAY1_BS20": IMG_DAY1_BS20,
        "IMG_DAY3_BS4":  IMG_DAY3_BS4,
        "IMG_DAY3_BS20": IMG_DAY3_BS20,
        "IMG_DAY7_BS4":  IMG_DAY7_BS4,
        "IMG_DAY7_BS20": IMG_DAY7_BS20,
    }

    # 逐槽位合并：(lqgzkjl 现有IDs) - del_ids + 新上传IDs
    final_slot_ids: Dict[str, List[int]] = {}
    for slot_key, lq_field in _SLOT_TO_LQ_SUBMIT.items():
        existing_slot = [
            int(item["id"]) for item in (lqgzkjl.get(lq_field) or [])
            if isinstance(item, dict) and item.get("id") is not None
               and int(item["id"]) not in _submit_del_ids
        ]
        new_slot_ids = await _save_file_list_submit(_slot_uploads_submit.get(slot_key))
        final_slot_ids[slot_key] = existing_slot + new_slot_ids

    # 将 final_slot_ids 整合为 final_img_ids（保持与后续逻辑兼容）
    final_img_ids: Dict[str, List[int]] = final_slot_ids

    # [PDF-SUBMIT] 图片字段（用于 PDF 渲染，每个槽位取第一张，已注释）
    # def to_local_path(url: Any) -> Any:
    #     if not url:
    #         return None
    #     try:
    #         p = str(url).lstrip("/")
    #         return (Path(__file__).resolve().parents[2] / p)
    #     except Exception:
    #         return None
    #
    # image_fields: Dict[str, Any] = {}
    #
    # # 6张倍率图：新上传优先，否则取 lqgzkjl 第一个 URL
    # for day in (1, 3, 7):
    #     for mag in (4, 20):
    #         ctx_key = f"IMG_DAY{day}_BS{mag}"
    #         slot_ids = final_slot_ids.get(ctx_key) or []
    #         if slot_ids:
    #             from app.crud.file_center import get_file_by_id as _get_file
    #             try:
    #                 _f = _get_file(slot_ids[0])
    #                 _p = str(_f.get("F_PATH", "")).replace("\\", "/").lstrip("/") if _f else None
    #                 image_fields[ctx_key] = to_local_path(_p)
    #             except Exception:
    #                 image_fields[ctx_key] = None
    #         else:
    #             url_list = lqgzkjl.get(f"DAY{day}_BS{mag}_URL") or []
    #             image_fields[ctx_key] = to_local_path(url_list[0] if url_list else None)
    #
    # # [PDF-SUBMIT] 预处理图片：取第一张用于PDF渲染（已注释）
    # ybycl_new_ids = await _save_file_list_submit(IMG_YBYCL)
    # ybycl_files = ybzkjl.get("F_YBYCL_FILES") or []
    # if ybycl_new_ids:
    #     from app.crud.file_center import get_file_by_id as _get_file2
    #     try:
    #         _f2 = _get_file2(ybycl_new_ids[0])
    #         _p2 = str(_f2.get("F_PATH", "")).replace("\\", "/").lstrip("/") if _f2 else None
    #         image_fields["IMG_YBYCL"] = to_local_path(_p2)
    #     except Exception:
    #         image_fields["IMG_YBYCL"] = None
    # else:
    #     first_ybycl_path = ybycl_files[0].get("path") if ybycl_files else None
    #     image_fields["IMG_YBYCL"] = to_local_path(first_ybycl_path)

    # 处理预处理图片上传（IMG_YBYCL）：保存文件到磁盘并注册到文件中心
    await _save_file_list_submit(IMG_YBYCL)

    # 生成 ZKBH（覆盖更新沿用原编号，否则新生成）
    zkbh = (existing.get("F_ZKBH") if isinstance(existing, dict) else None) or f"QC-{datetime.now().strftime('%Y%m%d%H%M%S')}"

    # [PDF-SUBMIT] PDF状态变量（提交时生成PDF所需，已注释）
    # pdf_id = None
    # pdf_url = None
    # pdf_status = "generating"

    # 培养天数
    final_pyts = pyts_val if pyts_val is not None else lqgzkjl.get("F_PYTS")

    # 写入 T_ZKJL 质控记录
    try:
        from app.crud.qc_record import create_or_update_qc_record
        qc_data = {
            "F_QKMS":    F_SZQK,
            "F_IMG_D1M4":  final_img_ids.get("IMG_DAY1_BS4", []),
            "F_IMG_D1M20": final_img_ids.get("IMG_DAY1_BS20", []),
            "F_IMG_D3M4":  final_img_ids.get("IMG_DAY3_BS4", []),
            "F_IMG_D3M20": final_img_ids.get("IMG_DAY3_BS20", []),
            "F_IMG_D7M4":  final_img_ids.get("IMG_DAY7_BS4", []),
            "F_IMG_D7M20": final_img_ids.get("IMG_DAY7_BS20", []),
            "F_PYTS":  final_pyts,
            "F_XT":    F_XT,
            "F_SFWR":  F_SFWR if isinstance(F_SFWR, str) else ("是" if sfwr_val == 1 else "否"),
            "F_XBSL":  F_XBSL,
            "F_XBHL":  xbhl_val,
            "F_ZYT":   F_ZYTJCJG,
            # "F_PDF": pdf_id,  # [PDF-SUBMIT] 提交时写入PDF ID，已注释
            "F_PDF":   None,
        }
        operator = gdxx.get("F_SQR") or "系统"
        zkjl_id = create_or_update_qc_record(gdid, qc_data, operator)
        print(f"质控记录已保存: ZKJL_ID={zkjl_id}, GDID={gdid}")

    except Exception as e:
        import traceback
        print(f"警告: 保存质控记录失败: {e}\n{traceback.format_exc()}")

    # 写入 T_ZKSH：存在待审核则覆盖更新，否则插入
    def _to_int_or_none(v: Any):
        try:
            return int(v)
        except Exception:
            return None
    
    # ========== 准备质控变更记录 ==========
    from app.crud.qc_audit import calculate_changes
    import json
    import logging
    
    logger = logging.getLogger(__name__)
    operator = (current_user.get("F_Ktype") or current_user.get("F_YSM") or "系统") if current_user else "系统"
    if existing:
        bglx = "修改"
    elif resubmit:
        bglx = "重新提交"
    else:
        bglx = "新增"
    changes: Dict[str, Any] = {}
    old_detail_data = None  # 用于F_OLD_DATA字段
    
    # 新数据（只包含T_ZKSH表中实际存在的字段）
    new_data = {
        "F_ZKJD": payload.get("F_ZKJD") or "验证单",
        "F_DCID": gdxx.get("F_DCID"),
        "F_DC": gdxx.get("F_DC"),
        "F_ZZID": gdxx.get("F_ZZID"),
        "F_XM": gdxx.get("F_XM"),
        "F_BRBH": gdxx.get("F_BRBH"),
        "F_ZZBW": gdxx.get("F_ZZBW"),
        "F_YBLY": gdxx.get("F_YBLY"),
        "F_KSPYRQ": (gdxx.get("F_PYSJ") or None)[:10] if gdxx.get("F_PYSJ") else None,
        "F_PYTS": _to_int_or_none(lqgzkjl.get("F_PYTS")),
        "F_PYR": gdxx.get("F_SQR"),
        # "F_PDF": int(pdf_id) if pdf_id is not None else None,  # [PDF-SUBMIT] 已注释
        "F_PDF": None,
    }
    
    if existing:
        # ✅ 修改操作：获取旧数据并计算变更
        # ✅ 不包含F_ZKJD，因为F_ZKJD是记录的关键标识，不应该变更
        # ✅ 只包含T_ZKSH表中实际存在的字段
        old_data = {
            "F_DCID": existing.get("F_DCID"),
            "F_DC": existing.get("F_DC"),
            "F_ZZID": existing.get("F_ZZID"),
            "F_XM": existing.get("F_XM"),
            "F_BRBH": existing.get("F_BRBH"),
            "F_ZZBW": existing.get("F_ZZBW"),
            "F_YBLY": existing.get("F_YBLY"),
            "F_KSPYRQ": existing.get("F_KSPYRQ"),
            "F_PYTS": existing.get("F_PYTS"),
            "F_PYR": existing.get("F_PYR"),
            "F_PDF": existing.get("F_PDF"),
        }
        # 从new_data中筛选出与old_data对应的字段进行对比
        # 这样可以避免将T_ZKJL表的字段（F_SZQK, F_XT等）误记录为T_ZKSH的变更
        new_data_for_compare = {k: new_data[k] for k in old_data.keys() if k in new_data}
        changes = calculate_changes(old_data, new_data_for_compare)
        
        # ✅ 调用详情接口获取完整旧数据（用于F_OLD_DATA）
        try:
            overview_resp = _qc_overview_result(gdid)
            if overview_resp and overview_resp.get("code") == 1:
                old_detail_data = overview_resp.get("data")
                logger.info(f"[验证单质控] 成功获取旧详情数据用于备份，工单ID: {gdid}")
        except Exception as e:
            logger.warning(f"[验证单质控] 获取旧详情数据失败: {e}")
    else:
        # 新增 / 驳回后重新提交：INSERT 新审核记录
        if resubmit and prev_rejected:
            submit_round = 2
            prev_form = None
            try:
                with adapter.connect() as _conn:
                    _cur = _conn.cursor()
                    _cur.execute(
                        "SELECT COUNT(*) FROM T_ZKSH WHERE F_GDBH = ? AND F_ZKJD IN (?, ?)",
                        (gdxx.get("F_GDBH"), _zkjd_base, _zkjd_tpl),
                    )
                    _cnt_row = _cur.fetchone()
                    if _cnt_row and _cnt_row[0]:
                        submit_round = int(_cnt_row[0]) + 1
                    _cur.execute(
                        "SELECT F_OLD_DATA FROM T_ZKSH WHERE F_ID = ?",
                        (prev_rejected.get("F_ID"),),
                    )
                    _old_row = _cur.fetchone()
                    if _old_row:
                        prev_form = _lqg_from_old_data(_old_row[0])
            except Exception as e:
                logger.warning(f"[验证单质控] 统计提交轮次或读取上次验证单失败: {e}")
            if not isinstance(prev_form, dict):
                prev_form = lqgzkjl_before_submit
            sfwr_saved = F_SFWR if isinstance(F_SFWR, str) else ("是" if sfwr_val == 1 else "否")
            new_form = {
                "F_SZQK": F_SZQK,
                "F_PYTS": final_pyts,
                "F_XT": F_XT,
                "F_SFWR": sfwr_saved,
                "F_XBSL": F_XBSL,
                "F_XBHL": xbhl_val,
                "F_ZYTJCJG": F_ZYTJCJG,
            }
            new_images = {
                lq_field: final_img_ids.get(slot) or []
                for slot, lq_field in _SLOT_TO_LQ_SUBMIT.items()
            }
            try:
                field_changes = _verification_field_changes(prev_form, new_form, new_images)
            except Exception as e:
                logger.warning(f"[验证单质控] 计算重新提交变更字段失败: {e}")
                field_changes = []
            changes = {
                "操作": "重新提交验证单",
                "提交轮次": submit_round,
                "前次记录": {
                    "F_ID": prev_rejected.get("F_ID"),
                    "F_ZKBH": prev_rejected.get("F_ZKBH"),
                    "F_SHZT": prev_rejected.get("F_SHZT"),
                    "F_SHBZ": prev_rejected.get("F_SHBZ"),
                },
                "关键信息": {
                    "工单ID": gdid,
                    "工单编号": gdxx.get("F_GDBH"),
                    "标本号": gdxx.get("F_ZZID"),
                    "质控阶段": F_ZKJD,
                    "生长情况": F_SZQK,
                    "培养天数": pyts_val,
                },
                "变更字段": field_changes,
            }
        else:
            changes = {
                "操作": "提交质控单（验证单）",
                "关键信息": {
                    "工单ID": gdid,
                    "工单编号": gdxx.get("F_GDBH"),
                    "标本号": gdxx.get("F_ZZID"),
                    "质控阶段": "验证单",
                    "生长情况": F_SZQK,
                    "培养天数": pyts_val,
                }
            }
        
        # ✅ 调用详情接口获取完整新数据（用于F_OLD_DATA）
        try:
            overview_resp = _qc_overview_result(gdid)
            if overview_resp and overview_resp.get("code") == 1:
                old_detail_data = overview_resp.get("data")
                logger.info(f"[验证单质控] 成功获取新详情数据用于备份，工单ID: {gdid}")
        except Exception as e:
            logger.warning(f"[验证单质控] 获取新详情数据失败: {e}")
    
    with adapter.connect() as conn:
        cur = conn.cursor()
        kspyrq = (gdxx.get("F_PYSJ") or None)[:10] if gdxx.get("F_PYSJ") else None
        pyts_int = _to_int_or_none(lqgzkjl.get("F_PYTS"))
        # pdf_int = int(pdf_id) if pdf_id is not None else None  # [PDF-SUBMIT] 已注释
        pdf_int = None
        changes_json = json.dumps(changes, ensure_ascii=False, default=str)
        # ✅ 序列化旧详情数据（稍后单独UPDATE以避免行长度超限）
        old_data_json = json.dumps(old_detail_data, ensure_ascii=False, default=str) if old_detail_data else None
        jlsj = datetime.now()
        
        if existing:
            # ✅ UPDATE语句（不包含F_OLD_DATA和F_ZKJD）
            # ✅ F_ZKJD是记录的关键标识之一，不应该被更新
            cur.execute(
                """UPDATE T_ZKSH SET 
                   F_SHZT = '待审核', F_GDID = ?, F_DCID = ?, F_DC = ?, F_ZZID = ?, 
                   F_XM = ?, F_BRBH = ?, F_ZZBW = ?, F_YBLY = ?, F_KSPYRQ = ?, 
                   F_PYTS = ?, F_PYR = ?, F_PDF = ?,
                   F_BGLX = ?, F_CHANGES = ?, F_JLR = ?, F_JLSJ = ?
                   WHERE F_ID = ?""",
                (
                    gdid,
                    gdxx.get("F_DCID"), gdxx.get("F_DC"), gdxx.get("F_ZZID"), gdxx.get("F_XM"), gdxx.get("F_BRBH"),
                    gdxx.get("F_ZZBW"), gdxx.get("F_YBLY"),
                    kspyrq,
                    pyts_int,
                    gdxx.get("F_SQR"),
                    pdf_int,
                    bglx, changes_json, operator, jlsj,
                    int(existing.get("F_ID")),
                ),
            )
            zksh_id = int(existing.get("F_ID"))
            
            # ✅ 单独UPDATE F_OLD_DATA（避免行长度超限）
            if old_data_json is not None:
                try:
                    cur.execute("UPDATE T_ZKSH SET F_OLD_DATA = ? WHERE F_ID = ?", (old_data_json, zksh_id))
                except Exception as e:
                    logger.warning(f"[验证单质控] 更新旧数据失败 (ID={zksh_id}): {e}")
        else:
            # ✅ INSERT语句（不包含F_OLD_DATA）
            cur.execute(
                """
                INSERT INTO T_ZKSH (
                  F_ZKBH, F_ZKJD, F_SHZT, F_GDBH, F_GDID, F_DCID, F_DC, F_ZZID, F_XM, F_BRBH,
                  F_ZZBW, F_YBLY, F_KSPYRQ, F_PYTS, F_PYR, F_PDF,
                  F_BGLX, F_CHANGES, F_JLR, F_JLSJ
                ) VALUES (?, ?, '待审核', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    zkbh,
                    (payload.get("F_ZKJD") or "验证单"),
                    gdxx.get("F_GDBH"), gdid, gdxx.get("F_DCID"), gdxx.get("F_DC"), gdxx.get("F_ZZID"), gdxx.get("F_XM"), gdxx.get("F_BRBH"),
                    gdxx.get("F_ZZBW"), gdxx.get("F_YBLY"),
                    kspyrq,
                    pyts_int,
                    gdxx.get("F_SQR"),
                    pdf_int,
                    bglx, changes_json, operator, jlsj,
                ),
            )
            cur.execute("SELECT MAX(F_ID) FROM T_ZKSH WHERE F_ZKBH = ?", (zkbh,))
            zksh_id = int(cur.fetchone()[0])
            
            # ✅ 单独UPDATE F_OLD_DATA（避免行长度超限）
            if old_data_json is not None:
                try:
                    cur.execute("UPDATE T_ZKSH SET F_OLD_DATA = ? WHERE F_ID = ?", (old_data_json, zksh_id))
                except Exception as e:
                    logger.warning(f"[验证单质控] 更新旧数据失败 (ID={zksh_id}): {e}")
        
        # 更新培养工单的质控状态为"待审核"
        gdbh = gdxx.get("F_GDBH")
        if gdbh:
            cur.execute(
                "UPDATE T_PYGD SET F_ZKZT = '待审核' WHERE F_GDBH = ?",
                (gdbh,)
            )
            pygd_affected = cur.rowcount
        else:
            pygd_affected = 0
        
        conn.commit()

    # [PDF-SUBMIT] 添加后台任务：异步生成PDF（已注释，PDF改为审核通过后生成）
    # if background_tasks:
    #     background_tasks.add_task(
    #         _generate_qc_pdf_for_submit,
    #         zksh_id=zksh_id,
    #         zkjl_id=zkjl_id,
    #         gdid=gdid,
    #         ctx=ctx,
    #         image_fields=image_fields,
    #         gdbh=gdxx.get("F_GDBH"),
    #     )
    #     print(f"已添加PDF后台生成任务: ZKSH_ID={zksh_id}, GDID={gdid}")

    # 返回结果
    result = {
        "GDXX": gdxx,
        "YBZKJL": ybzkjl,
        "LQGZKJL": lqgzkjl,
        "F_ZKSH_ID": zksh_id,
        "F_ZKBH": zkbh,
        # "F_PDF_ID": pdf_id,    # [PDF-SUBMIT] 已注释
        # "F_PDF_URL": pdf_url,  # [PDF-SUBMIT] 已注释
        # "pdf_status": pdf_status,  # [PDF-SUBMIT] 已注释
        "pygd_updated": pygd_affected,
        "qc_audit_record": {
            "id": zksh_id,
            "type": bglx,
            "operator": operator,
            "stage": "验证单",
        }
    }

    # return ok(result, "提交成功，PDF生成中")  # [PDF-SUBMIT] 已注释
    return ok(result, "重新提交成功" if resubmit else "提交成功")


@router.post("/save-draft", response_model=ApiResponse[dict], response_model_exclude_none=True,
             dependencies=[Depends(require_roles(["organoid_culture_tissue"]))])
async def save_draft(
    F_GDID: int = Form(...),
    F_SZQK: str = Form(default=""),
    F_PYTS: str = Form(default=""),
    F_XT: str = Form(default=""),
    F_SFWR: str = Form(default=""),
    F_XBSL: str = Form(default=""),
    F_XBHL: str = Form(default=""),
    F_ZYTJCJG: str = Form(default=""),
    F_ZKJD: str = Form(default="验证单"),
    F_PYR: str = Form(default=""),
    F_FILE_IDS_DEL: str = Form(default=None, description="需要解除关联的文件ID，JSON数组 [123,456] 或逗号分隔 123,456"),
    IMG_DAY1_BS4:  List[UploadFile] = File(default=None),
    IMG_DAY1_BS20: List[UploadFile] = File(default=None),
    IMG_DAY3_BS4:  List[UploadFile] = File(default=None),
    IMG_DAY3_BS20: List[UploadFile] = File(default=None),
    IMG_DAY7_BS4:  List[UploadFile] = File(default=None),
    IMG_DAY7_BS20: List[UploadFile] = File(default=None),
    IMG_YBYCL:     List[UploadFile] = File(default=None),
    current_user: dict = Depends(get_current_user_optional),
):
    """
    统一保存验证单（新增/编辑均走此接口）

    只将表单内容保存到 T_ZKJL 质控记录表，不创建 T_ZKSH 审核记录，
    不生成 PDF，不更新工单质控状态，可多次调用覆盖。

    图片合并策略（每个槽位独立处理）：
      最终文件列表 = (数据库现有IDs) - (F_FILE_IDS_DEL) + (本次新上传IDs)
      若某槽位为空且有观察记录预填，自动带入观察记录文件ID
    """
    try:
        gdid = int(F_GDID)
    except Exception:
        return err("缺少或非法的 F_GDID")

    def safe_float(val: str):
        if not val or val.strip() == "":
            return None
        try:
            return float(val)
        except Exception:
            return None

    def safe_int(val: str):
        if not val or val.strip() == "":
            return None
        try:
            return int(val)
        except Exception:
            return None

    pyts_val = safe_int(F_PYTS)
    xbhl_val = safe_float(F_XBHL)

    denied = _deny_gdid(current_user, gdid)
    if denied:
        return denied

    # ===== 1. 获取工单概览（含 LQGZKJL 现有图片 ID 数组 + 观察记录预填）=====
    overview_resp = _qc_overview_result(gdid)
    if (overview_resp or {}).get("code") != 1:
        return err((overview_resp or {}).get("msg") or "获取工单概览失败")
    data = (overview_resp or {}).get("data") or {}
    gdxx = data.get("GDXX") or {}
    lqgzkjl = data.get("LQGZKJL") or {}

    # ===== 2. 解析要删除的文件 ID =====
    del_ids_set, del_err = _parse_file_ids_del(F_FILE_IDS_DEL)
    if del_err:
        return err(del_err)

    # ===== 3. 上传新文件并注册到文件中心 =====
    from app.core.storage import build_upload_path_in
    from app.crud.file_center import insert_file_record as _insert_file

    async def _save_file_list(upload_files) -> List[int]:
        """保存一组上传文件，返回新注册的文件ID列表。"""
        new_ids: List[int] = []
        for uf in (upload_files or []):
            if not uf or not getattr(uf, "filename", None):
                continue
            try:
                timestamp = datetime.now().strftime("%Y%m%d%H%M%S%f")
                filename = f"qc_{gdid}_{timestamp}_{uf.filename}"
                dst_path, _ = build_upload_path_in("qc_images", filename)
                dst_path.write_bytes(await uf.read())
                rel_path = str(dst_path.relative_to(Path(settings.upload_dir))).replace("\\", "/")
                fid = _insert_file(rel_path, uf.filename)
                if fid is not None:
                    new_ids.append(int(fid))
            except Exception as e:
                print(f"[暂存] 保存/注册图片失败: {e}")
        return new_ids

    # 槽位名 → lqgzkjl 字段名（overview 已统一返回数组）
    _SLOT_TO_LQ = {
        "IMG_DAY1_BS4":  "F_IMG_D1M4",
        "IMG_DAY1_BS20": "F_IMG_D1M20",
        "IMG_DAY3_BS4":  "F_IMG_D3M4",
        "IMG_DAY3_BS20": "F_IMG_D3M20",
        "IMG_DAY7_BS4":  "F_IMG_D7M4",
        "IMG_DAY7_BS20": "F_IMG_D7M20",
    }

    slot_uploads = {
        "IMG_DAY1_BS4":  IMG_DAY1_BS4,
        "IMG_DAY1_BS20": IMG_DAY1_BS20,
        "IMG_DAY3_BS4":  IMG_DAY3_BS4,
        "IMG_DAY3_BS20": IMG_DAY3_BS20,
        "IMG_DAY7_BS4":  IMG_DAY7_BS4,
        "IMG_DAY7_BS20": IMG_DAY7_BS20,
    }

    # ===== 4. 逐槽位合并：现有 - 删除 + 新上传 =====
    final_slot_ids: Dict[str, List[int]] = {}
    for slot_key, lq_field in _SLOT_TO_LQ.items():
        existing: List[int] = [
            int(item["id"]) for item in (lqgzkjl.get(lq_field) or [])
            if isinstance(item, dict) and item.get("id") is not None
               and int(item["id"]) not in del_ids_set
        ]
        new_ids = await _save_file_list(slot_uploads.get(slot_key))
        combined = existing + new_ids
        final_slot_ids[slot_key] = combined if combined else []

    # ===== 5. 确定最终培养天数 =====
    final_pyts = pyts_val if pyts_val is not None else lqgzkjl.get("F_PYTS")

    # ===== 6. 写入 T_ZKJL（不写 T_ZKSH，不生成 PDF）=====
    zkjl_id = None
    try:
        from app.crud.qc_record import create_or_update_qc_record
        qc_data = {
            "F_QKMS":    F_SZQK,
            "F_IMG_D1M4":  final_slot_ids["IMG_DAY1_BS4"],
            "F_IMG_D1M20": final_slot_ids["IMG_DAY1_BS20"],
            "F_IMG_D3M4":  final_slot_ids["IMG_DAY3_BS4"],
            "F_IMG_D3M20": final_slot_ids["IMG_DAY3_BS20"],
            "F_IMG_D7M4":  final_slot_ids["IMG_DAY7_BS4"],
            "F_IMG_D7M20": final_slot_ids["IMG_DAY7_BS20"],
            "F_PYTS":  final_pyts,
            "F_XT":    F_XT,
            "F_SFWR":  F_SFWR,
            "F_XBSL":  F_XBSL,
            "F_XBHL":  xbhl_val,
            "F_ZYT":   F_ZYTJCJG,
        }
        operator = (
            (current_user.get("F_Ktype") or current_user.get("F_YSM") or "系统")
            if current_user else "系统"
        )
        zkjl_id = create_or_update_qc_record(gdid, qc_data, operator)
        print(f"[暂存] 草稿已保存: ZKJL_ID={zkjl_id}, GDID={gdid}")
    except Exception as e:
        import traceback
        print(f"[暂存] 写入T_ZKJL失败: {e}\n{traceback.format_exc()}")
        return err(f"暂存失败: {str(e)}")

    return ok(
        {
            "F_ZKJL_ID": zkjl_id,
            "GDID": gdid,
            "GDXX": gdxx,
        },
        "暂存成功",
    )


def _generate_qc_pdf_for_submit(
    zksh_id: int,
    zkjl_id: int,
    gdid: int,
    ctx: Dict[str, Any],
    image_fields: Dict[str, Any],
    gdbh: str
):
    """
    后台异步生成提交时的质控PDF（验证单）
    
    此函数在 /qc/submit 接口提交后在后台执行，用于生成质控PDF。
    执行完成后会自动更新：
    - T_ZKJL.F_PDF (质控记录)
    - T_ZKSH.F_PDF (审核记录)
    
    Args:
        zksh_id: 审核记录ID (T_ZKSH.F_ID)
        zkjl_id: 质控记录ID (T_ZKJL.F_ID)
        gdid: 工单ID
        ctx: 渲染上下文（包含工单、质控、观察记录数据）
        image_fields: 图片字段映射（Path对象）
        gdbh: 工单编号
    """
    import logging
    from app.db.factory import get_adapter
    from app.core.docx_pdf import render_pdf_with_images
    from app.crud.file_center import insert_file_record
    from datetime import datetime
    from pathlib import Path
    from app.core.settings import settings
    
    logger = logging.getLogger(__name__)
    adapter = get_adapter()
    
    try:
        # 1. 生成PDF
        out_basename = f"zk_{gdbh}_{datetime.now().strftime('%Y%m%d%H%M%S')}"
        logger.info(f"[异步PDF-提交] 开始生成: 工单ID={gdid}, 工单编号={gdbh}")
        
        pdf_path = render_pdf_with_images(
            "zk_template.docx",
            ctx,
            image_fields,
            out_basename
        )
        
        # 2. 注册到文件中心
        upload_dir_path = Path(settings.upload_dir)
        rel = str(pdf_path.relative_to(upload_dir_path)).replace("\\", "/")
        pdf_id = insert_file_record(rel, f"{out_basename}.pdf")
        
        logger.info(f"[异步PDF-提交] 生成成功: 工单ID={gdid}, PDF ID={pdf_id}, 路径={rel}")
        
        # 3. 更新数据库
        with adapter.connect() as conn:
            cur = conn.cursor()
            
            # 更新质控记录
            cur.execute(
                "UPDATE T_ZKJL SET F_PDF = ? WHERE F_ID = ?",
                (pdf_id, zkjl_id)
            )
            
            # 更新审核记录
            cur.execute(
                "UPDATE T_ZKSH SET F_PDF = ? WHERE F_ID = ?",
                (pdf_id, zksh_id)
            )
            
            conn.commit()
            
        logger.info(f"[异步PDF-提交] 数据库更新成功: ZKSH_ID={zksh_id}, ZKJL_ID={zkjl_id}, PDF_ID={pdf_id}")
        
    except Exception as e:
        logger.error(
            f"[异步PDF-提交] 生成失败: ZKSH_ID={zksh_id}, 工单ID={gdid}, 错误={e}",
            exc_info=True
        )


def _generate_verification_review_pdfs(cur, gdid: int, rec_id: int, logger) -> Optional[int]:
    """
    验证单审核通过时批量生成关联 PDF：
    - 放行检测报告 -> T_ZKJL.F_PDF_FXJC
    - 样本评分表（有预处理记录时）-> T_YCL.F_PDF
    - 类器官质控 -> T_ZKJL.F_PDF

    返回用于回写 T_ZKSH.F_PDF 的主 PDF ID（放行报告）。
    """
    release_pdf_id = None

    try:
        from app.crud.release_inspection_pdf import generate_release_inspection_pdf
        pdf_result = generate_release_inspection_pdf(gdid)
        release_pdf_id = pdf_result.get("file_id")
        logger.info(
            f"放行检测报告生成成功: 质控记录ID={rec_id}, GDID={gdid}, PDF ID={release_pdf_id}"
        )
    except Exception as e:
        logger.error(
            f"放行检测报告生成失败: 质控记录ID={rec_id}, GDID={gdid}, 错误={e}",
            exc_info=True,
        )

    try:
        cur.execute("SELECT 1 FROM T_YCL WHERE F_GDID = ?", (gdid,))
        if cur.fetchone():
            from app.crud.preprocess_score_pdf import generate_preprocess_score_pdf
            score_result = generate_preprocess_score_pdf(gdid)
            logger.info(
                f"样本评分表PDF生成成功: 质控记录ID={rec_id}, GDID={gdid}, "
                f"PDF ID={score_result.get('file_id')}"
            )
        else:
            logger.info(f"样本评分表PDF跳过: 工单 {gdid} 无预处理记录")
    except Exception as e:
        logger.error(
            f"样本评分表PDF生成失败: 质控记录ID={rec_id}, GDID={gdid}, 错误={e}",
            exc_info=True,
        )

    try:
        from app.crud.culture_detail_pdf import generate_culture_detail_pdf
        from app.crud.qc_record import update_qc_record_pdf
        qc_result = generate_culture_detail_pdf(gdid)
        qc_pdf_id = qc_result.get("file_id")
        if qc_pdf_id:
            update_qc_record_pdf(gdid, int(qc_pdf_id))
            logger.info(
                f"类器官质控PDF生成成功: 质控记录ID={rec_id}, GDID={gdid}, PDF ID={qc_pdf_id}"
            )
    except Exception as e:
        logger.error(
            f"类器官质控PDF生成失败: 质控记录ID={rec_id}, GDID={gdid}, 错误={e}",
            exc_info=True,
        )

    return release_pdf_id


def _generate_qc_pdf_async(rec_id: int, zkjd: str, gdbh: str, zzid: str):
    """
    后台异步生成质控 PDF（审核通过后）
    
    此函数在 /qc/review 接口审核通过后在后台执行。
    
    Args:
        rec_id: 质控记录ID
        zkjd: 质控阶段
        gdbh: 工单编号
        zzid: 标本号
    """
    import logging
    from app.db.factory import get_adapter
    from app.crud.qc_audit import normalize_zkjd, zkjd_matches
    
    logger = logging.getLogger(__name__)
    adapter = get_adapter()
    zkjd = normalize_zkjd(zkjd)
    
    pdf_id = None
    
    try:
        with adapter.connect() as conn:
            cur = conn.cursor()
            
            # 根据质控阶段生成相应的PDF
            if zkjd == "登记接收" and zzid:
                try:
                    from app.crud.sample_receive_pdf import generate_sample_receive_pdf
                    pdf_result = generate_sample_receive_pdf(zzid)
                    pdf_id = pdf_result.get("file_id")
                    logger.info(f"样本接收PDF生成成功: 质控记录ID={rec_id}, PDF ID={pdf_id}")
                except Exception as e:
                    logger.error(f"样本接收PDF生成失败: 质控记录ID={rec_id}, 错误={e}", exc_info=True)
                    return
            
            elif zkjd == "预处理" and gdbh:
                try:
                    from app.crud.preprocess_score_pdf import generate_preprocess_score_pdf
                    # 获取工单ID
                    cur.execute("SELECT F_ID FROM T_PYGD WHERE F_GDBH = ?", (gdbh,))
                    gdid_row = cur.fetchone()
                    if gdid_row and gdid_row[0]:
                        gdid = gdid_row[0]
                        pdf_result = generate_preprocess_score_pdf(gdid)
                        pdf_id = pdf_result.get("file_id")
                        logger.info(f"预处理评分PDF生成成功: 质控记录ID={rec_id}, PDF ID={pdf_id}")
                    else:
                        logger.warning(f"预处理PDF生成失败: 未找到工单ID, 工单编号={gdbh}")
                        return
                except Exception as e:
                    logger.error(f"预处理PDF生成失败: 质控记录ID={rec_id}, 错误={e}", exc_info=True)
                    return
            
            elif zkjd == "验证单" and gdbh:
                cur.execute("SELECT F_ID FROM T_PYGD WHERE F_GDBH = ?", (gdbh,))
                gdid_row = cur.fetchone()
                if gdid_row and gdid_row[0]:
                    gdid = int(gdid_row[0])
                    pdf_id = _generate_verification_review_pdfs(cur, gdid, rec_id, logger)
                    if not pdf_id:
                        return
                else:
                    logger.warning(f"验证单PDF生成失败: 未找到工单ID, 工单编号={gdbh}")
                    return
            
            elif zkjd in ("药物实验报告", "药物试验报告"):
                try:
                    from app.crud.drug_test_report_pdf import generate_drug_test_report_pdf

                    cur.execute("SELECT F_OLD_DATA FROM T_ZKSH WHERE F_ID = ?", (rec_id,))
                    old_data_row = cur.fetchone()
                    if not old_data_row:
                        logger.warning(f"药物试验报告PDF生成失败: 未找到质控记录, rec_id={rec_id}")
                        return

                    import json
                    old_data = json.loads(old_data_row[0]) if old_data_row[0] else {}
                    syjh_id = old_data.get("F_SYJH_ID") or old_data.get("实验计划ID")

                    if not syjh_id:
                        logger.warning(f"药物试验报告PDF生成失败: 无法获取实验计划ID, rec_id={rec_id}")
                        return

                    logger.info(f"药物试验报告PDF生成: 开始生成, 质控记录ID={rec_id}, 实验计划ID={syjh_id}")
                    result = generate_drug_test_report_pdf(syjh_id=int(syjh_id))

                    if result["success"]:
                        pdf_id = result["file_id"]
                        try:
                            from app.crud.drug_test import update_test_plan_status
                            update_test_plan_status(int(syjh_id), "已完成")
                            logger.info(f"药物实验计划状态已更新为已完成: 计划ID={syjh_id}")
                        except Exception as e2:
                            logger.warning(f"药物实验计划状态更新失败（不影响PDF生成）: {e2}")
                        logger.info(f"药物试验报告PDF生成成功: 质控记录ID={rec_id}, 实验计划ID={syjh_id}, PDF ID={pdf_id}")
                    else:
                        logger.error(f"药物试验报告PDF生成失败: 质控记录ID={rec_id}, 实验计划ID={syjh_id}, 错误={result['message']}")
                        return

                except Exception as e:
                    logger.error(f"药物试验报告PDF生成失败: 质控记录ID={rec_id}, 错误={e}", exc_info=True)
                    return

            elif zkjd == "动物实验报告":
                try:
                    from app.crud.animal_test_report_pdf import generate_animal_test_report_pdf

                    cur.execute("SELECT F_OLD_DATA FROM T_ZKSH WHERE F_ID = ?", (rec_id,))
                    old_data_row = cur.fetchone()
                    if not old_data_row:
                        logger.warning(f"动物实验报告PDF生成失败: 未找到质控记录, rec_id={rec_id}")
                        return

                    import json
                    old_data = json.loads(old_data_row[0]) if old_data_row[0] else {}
                    # 从 get_experiment_record 的快照结构中取实验计划ID
                    ep = old_data.get("endpoint") or {}
                    syjh_id = ep.get("F_SYJH_ID") or old_data.get("F_SYJH_ID")

                    if not syjh_id:
                        logger.warning(f"动物实验报告PDF生成失败: 无法获取实验计划ID, rec_id={rec_id}")
                        return

                    logger.info(f"动物实验报告PDF生成: 开始生成, 质控记录ID={rec_id}, 实验计划ID={syjh_id}")
                    result = generate_animal_test_report_pdf(syjh_id=int(syjh_id))

                    if result["success"]:
                        pdf_id = result["file_id"]
                        # 审核通过后将计划状态更新为"已完成"
                        try:
                            from app.crud.animal_test import update_animal_test_plan_status
                            update_animal_test_plan_status(int(syjh_id), "已完成")
                            logger.info(f"动物实验计划状态已更新为已完成: 计划ID={syjh_id}")
                        except Exception as e2:
                            logger.warning(f"动物实验计划状态更新失败（不影响PDF生成）: {e2}")
                        logger.info(f"动物实验报告PDF生成成功: 质控记录ID={rec_id}, 实验计划ID={syjh_id}, PDF ID={pdf_id}")
                    else:
                        logger.error(f"动物实验报告PDF生成失败: 质控记录ID={rec_id}, 实验计划ID={syjh_id}, 错误={result['message']}")
                        return

                except Exception as e:
                    logger.error(f"动物实验报告PDF生成失败: 质控记录ID={rec_id}, 错误={e}", exc_info=True)
                    return

            else:
                logger.warning(f"[质控PDF] 未知的质控阶段: {zkjd}")
                return
            
            # 更新质控记录的PDF字段
            if pdf_id:
                cur.execute("UPDATE T_ZKSH SET F_PDF = ? WHERE F_ID = ?", (pdf_id, rec_id))
                conn.commit()
                logger.info(f"质控记录PDF字段更新成功: F_ID={rec_id}, F_PDF={pdf_id}")
    
    except Exception as e:
        logger.error(f"PDF生成任务执行失败: 质控记录ID={rec_id}, 错误={e}", exc_info=True)


@router.post("/review", response_model=ApiResponse[dict], response_model_exclude_none=True,
             dependencies=[Depends(require_roles(["qc_review"]))])
def review(payload: Dict[str, Any], background_tasks: BackgroundTasks):
    """审核结果更新接口（PDF异步生成）。

    入参：
      - F_ID 或 F_ZKBH（二选一，用于定位记录）
      - F_SHZT（审核状态，必填）
      - F_SHBZ（审核备注，可选）
    
    功能：
      1. 更新 T_ZKSH 的审核状态和备注
      2. 如果审核通过，根据质控阶段在后台异步生成PDF：
         - 登记接收：样本接收质控PDF
         - 预处理：预处理评分PDF
         - 验证单：放行检测报告、样本评分表（有预处理时）、类器官质控PDF
      3. 只有质控阶段为"验证单"时，才回写工单状态到 T_PYGD.F_ZKZT
         （其他阶段的审核不影响工单整体状态，避免阻塞后续步骤）
    
    性能优化：
      - PDF 生成改为后台异步执行，不阻塞接口响应
      - 接口立即返回审核结果，PDF 生成完成后会自动更新 T_ZKSH.F_PDF
      - 返回值中 pdf_status="generating" 表示PDF正在后台生成
    """
    rec_id = payload.get("F_ID")
    zkbh = payload.get("F_ZKBH")
    shzt = payload.get("F_SHZT")
    shbz = payload.get("F_SHBZ")
    if not shzt:
        return err("缺少 F_SHZT")
    if rec_id is None and (not zkbh):
        return err("缺少 F_ID 或 F_ZKBH")

    from app.db.factory import get_adapter
    import logging
    
    adapter = get_adapter()
    logger = logging.getLogger(__name__)
    
    pdf_generating = False  # 标记是否触发了PDF生成
    
    with adapter.connect() as conn:
        cur = conn.cursor()
        
        # 1. 查询质控记录，获取 F_GDBH, F_ZKJD, F_ZZID
        where_sql = "F_ID = ?" if rec_id is not None else "F_ZKBH = ?"
        query_param = int(rec_id) if rec_id is not None else zkbh
        cur.execute(
            f"SELECT F_ID, F_GDBH, F_ZKJD, F_ZZID, F_ZKBH, F_GDID, F_CHANGES, F_JLSJ, F_OLD_DATA FROM T_ZKSH WHERE {where_sql}",
            (query_param,),
        )
        row = cur.fetchone()
        if not row:
            return err("未找到质控记录")
        
        actual_rec_id = row[0]  # 实际的记录ID（用于后台任务）
        gdbh = row[1]
        zkjd = row[2]  # 质控阶段
        zzid = row[3]  # 标本号
        zkbh_value = row[4]  # 质控编号
        gdid = row[5]  # 工单ID
        changes_json = row[6]  # 变更内容JSON
        jlsj = row[7]  # 登记时间
        old_data_json = row[8]  # 业务快照

        # 完成审核（通过/不通过）前，须先处理同阶段更早的待审核记录（含模板后缀变体；
        # 有工单 ID（含由 F_GDBH 反查）仅同工单，否则才按标本号）
        from app.crud.qc_audit import check_qc_review_order, resolve_qc_review_gdid
        resolved_gdid = resolve_qc_review_gdid(
            cur,
            int(gdid) if gdid is not None else None,
            gdbh,
        )
        if shzt in ("审核通过", "审核不通过"):
            order_err = check_qc_review_order(
                cur,
                rec_id=int(actual_rec_id),
                zkjd=zkjd or "",
                gdid=resolved_gdid,
                zzid=zzid,
                gdbh=gdbh,
                jlsj=jlsj,
            )
            if order_err:
                return err(order_err)

        # 历史数据：提交时未写 F_GDID 的记录，审核时由 F_GDBH 反查后回填
        if resolved_gdid is not None and gdid is None:
            cur.execute(
                "UPDATE T_ZKSH SET F_GDID = ? WHERE F_ID = ? AND F_GDID IS NULL",
                (resolved_gdid, actual_rec_id),
            )
        
        # 2. 更新 T_ZKSH 审核状态；审核完成（非待审核）时同步记录审核时间 F_SHSJ
        sets = ["F_SHZT = ?"]
        params: List[Any] = [shzt]
        if shbz is not None:
            sets.append("F_SHBZ = ?")
            params.append(shbz)
        if shzt != "待审核":
            sets.append("F_SHSJ = ?")
            params.append(datetime.now())
        params.append(query_param)
        cur.execute(f"UPDATE T_ZKSH SET {', '.join(sets)} WHERE {where_sql}", tuple(params))
        affected = cur.rowcount
        
        # 3. 如果审核通过，添加后台任务生成PDF
        from app.crud.qc_audit import normalize_zkjd, zkjd_matches
        _pdf_stages = {"登记接收", "预处理", "验证单", "药物实验报告", "药物试验报告", "动物实验报告"}
        if shzt == "审核通过" and normalize_zkjd(zkjd) in _pdf_stages:
            background_tasks.add_task(
                _generate_qc_pdf_async,
                rec_id=actual_rec_id,
                zkjd=zkjd,
                gdbh=gdbh or "",
                zzid=zzid or ""
            )
            pdf_generating = True
            logger.info(f"已添加后台任务生成PDF: 质控记录ID={actual_rec_id}, 阶段={zkjd}")
        
        # 4. 回写质控状态到培养工单表 T_PYGD.F_ZKZT
        # 只有质控阶段为"验证单"时才回写工单状态，其他阶段的审核不影响工单整体状态
        pygd_affected = 0
        if gdbh and zkjd_matches(zkjd, "验证单"):
            cur.execute("UPDATE T_PYGD SET F_ZKZT = ? WHERE F_GDBH = ?", (shzt, gdbh))
            pygd_affected = cur.rowcount
        
        # 5. "标为不合格"类审核：通过/不通过时回写对应业务表状态
        from app.crud.qc_audit import apply_unqualified_review, apply_drug_report_review
        unqualified_result = apply_unqualified_review(
            cur, zkjd, shzt, zzid, gdid, changes_json, logger
        )
        drug_report_result = apply_drug_report_review(
            cur, zkjd, shzt, old_data_json, logger
        )
        ybzj_affected = unqualified_result.get("ybzj_affected", 0)
        syjh_affected = (
            unqualified_result.get("syjh_affected", 0)
            + drug_report_result.get("syjh_affected", 0)
        )
        pygd_unqualified_affected = unqualified_result.get("pygd_affected", 0)
        if pygd_unqualified_affected:
            pygd_affected = pygd_unqualified_affected
        
        conn.commit()
    
    if affected <= 0:
        return err("未找到记录或未更新")
    
    # 构建返回结果
    result = {
        "affected": affected, 
        "pygd_affected": pygd_affected,
        "ybzj_affected": unqualified_result.get("ybzj_affected", 0),
        "syjh_affected": syjh_affected,
        "dwsy_affected": unqualified_result.get("dwsy_affected", 0),
        "F_SHZT": shzt, 
        "F_SHBZ": shbz,
        "F_GDBH": gdbh,
        "F_ZKBH": zkbh_value
    }
    
    # 标记PDF生成状态
    if pdf_generating:
        result["pdf_status"] = "generating"  # 正在生成中
    
    # 构建提示消息
    msg = "审核结果已更新"
    if pygd_affected > 0:
        msg += "，培养工单质控状态已同步"  # 只有验证单阶段才会触发
    
    # 根据质控阶段提示PDF生成情况
    if pdf_generating:
        if zkjd_matches(zkjd, "登记接收"):
            msg += "，样本接收质控PDF正在后台生成"
        elif zkjd_matches(zkjd, "预处理"):
            msg += "，预处理评分PDF正在后台生成"
        elif zkjd_matches(zkjd, "验证单"):
            msg += "，放行检测报告正在后台生成"
        elif normalize_zkjd(zkjd) in ("药物实验报告", "药物试验报告"):
            msg += "，药物实验报告PDF正在后台生成"
        elif zkjd_matches(zkjd, "动物实验报告"):
            msg += "，动物实验报告PDF正在后台生成"
    
    return ok(result, msg)


@router.post("/batch-review", response_model=ApiResponse[dict], response_model_exclude_none=True,
             dependencies=[Depends(require_roles(["qc_review"]))])
def batch_review(payload: Dict[str, Any], background_tasks: BackgroundTasks):
    """批量审核接口（PDF 异步生成）。

    入参：
      - F_IDS（必填，待审核的质控记录 ID 数组）
      - F_SHZT（审核状态，必填）
      - F_SHBZ（审核备注，可选，对所有记录生效）

    功能与 /review 一致，对每条记录依次执行：
      0. 同批记录按 F_JLSJ/F_ID 升序处理；完成审核前校验同阶段更早待审记录（批内 ID 互不阻塞）
      1. 更新 T_ZKSH 审核状态和备注
      2. 审核通过时在后台异步生成对应 PDF
      3. 质控阶段为"验证单"时回写 T_PYGD.F_ZKZT
      4. 质控阶段为"标为不合格"且审核通过时更新 T_YBZJ.F_BHG

    返回 code：全部成功为 1；存在失败/未找到记录为 0（data 仍含 failed / not_found_ids 明细）。
    """
    f_ids = payload.get("F_IDS")
    shzt = payload.get("F_SHZT")
    shbz = payload.get("F_SHBZ")

    if not shzt:
        return err("缺少 F_SHZT")
    if not f_ids or not isinstance(f_ids, list) or len(f_ids) == 0:
        return err("缺少 F_IDS 或 F_IDS 为空数组")

    try:
        id_list = [int(x) for x in f_ids]
    except Exception:
        return err("F_IDS 包含非法值，须为整数数组")

    from app.db.factory import get_adapter
    import logging
    adapter = get_adapter()
    logger = logging.getLogger(__name__)

    # 批量查询所有目标质控记录
    ph = ",".join(["?"] * len(id_list))
    records: Dict[int, Any] = {}
    with adapter.connect() as conn:
        cur = conn.cursor()
        cur.execute(
            f"SELECT F_ID, F_GDBH, F_ZKJD, F_ZZID, F_ZKBH, F_GDID, F_CHANGES, F_JLSJ, F_OLD_DATA FROM T_ZKSH WHERE F_ID IN ({ph})",
            tuple(id_list),
        )
        for row in cur.fetchall():
            records[int(row[0])] = {
                "F_ID": int(row[0]),
                "F_GDBH": row[1],
                "F_ZKJD": row[2],
                "F_ZZID": row[3],
                "F_ZKBH": row[4],
                "F_GDID": row[5],
                "F_CHANGES": row[6],
                "F_JLSJ": row[7],
                "F_OLD_DATA": row[8],
            }

    not_found = [i for i in id_list if i not in records]

    # 构建 UPDATE 语句片段；审核完成（非待审核）时同步记录审核时间 F_SHSJ
    set_parts = ["F_SHZT = ?"]
    set_vals: List[Any] = [shzt]
    if shbz is not None:
        set_parts.append("F_SHBZ = ?")
        set_vals.append(shbz)
    if shzt != "待审核":
        set_parts.append("F_SHSJ = ?")
        set_vals.append(datetime.now())
    set_clause = ", ".join(set_parts)

    total_affected = 0
    total_pygd_affected = 0
    total_ybzj_affected = 0
    total_syjh_affected = 0
    total_dwsy_affected = 0
    pdf_triggered: List[int] = []
    failed: List[Dict[str, Any]] = []

    from app.crud.qc_audit import (
        apply_unqualified_review,
        apply_drug_report_review,
        normalize_zkjd,
        zkjd_matches,
        check_qc_review_order,
    )
    _pdf_stages = {"登记接收", "预处理", "验证单", "药物实验报告", "药物试验报告", "动物实验报告"}

    # 同批内按登记时间、ID 升序处理（与 exclude_ids 配合，先审 batch 内更早的记录）
    process_ids = sorted(
        id_list,
        key=lambda rid: (
            records[rid]["F_JLSJ"] if rid in records and records[rid].get("F_JLSJ") is not None else datetime.min,
            rid,
        ),
    )

    with adapter.connect() as conn:
        cur = conn.cursor()
        for rid in process_ids:
            rec = records.get(rid)
            if not rec:
                failed.append({"F_ID": rid, "reason": "记录不存在"})
                continue
            try:
                if shzt in ("审核通过", "审核不通过"):
                    order_err = check_qc_review_order(
                        cur,
                        rec_id=rid,
                        zkjd=rec.get("F_ZKJD") or "",
                        gdid=int(rec["F_GDID"]) if rec.get("F_GDID") is not None else None,
                        zzid=rec.get("F_ZZID"),
                        gdbh=rec.get("F_GDBH"),
                        jlsj=rec.get("F_JLSJ"),
                        exclude_ids=id_list,
                    )
                    if order_err:
                        failed.append({"F_ID": rid, "reason": order_err})
                        logger.warning(
                            "[批量审核] 记录 %s 审核顺序校验未通过: %s",
                            rid,
                            order_err,
                        )
                        continue

                # 历史数据回填 F_GDID
                from app.crud.qc_audit import resolve_qc_review_gdid
                resolved_gdid = resolve_qc_review_gdid(
                    cur,
                    int(rec["F_GDID"]) if rec.get("F_GDID") is not None else None,
                    rec.get("F_GDBH"),
                )
                if resolved_gdid is not None and rec.get("F_GDID") is None:
                    cur.execute(
                        "UPDATE T_ZKSH SET F_GDID = ? WHERE F_ID = ? AND F_GDID IS NULL",
                        (resolved_gdid, rid),
                    )

                # 更新审核状态
                cur.execute(
                    f"UPDATE T_ZKSH SET {set_clause} WHERE F_ID = ?",
                    tuple(set_vals + [rid]),
                )
                affected = cur.rowcount
                total_affected += affected

                gdbh = rec["F_GDBH"]
                zkjd = rec["F_ZKJD"]
                zzid = rec["F_ZZID"]
                gdid = rec.get("F_GDID")
                changes_json = rec.get("F_CHANGES")
                old_data_json = rec.get("F_OLD_DATA")

                # 验证单：回写工单质控状态
                if gdbh and zkjd_matches(zkjd, "验证单"):
                    cur.execute("UPDATE T_PYGD SET F_ZKZT = ? WHERE F_GDBH = ?", (shzt, gdbh))
                    total_pygd_affected += cur.rowcount

                # "标为不合格"类审核：通过/不通过时回写对应业务表状态
                uq_result = apply_unqualified_review(
                    cur, zkjd, shzt, zzid, gdid, changes_json, logger
                )
                drug_report_result = apply_drug_report_review(
                    cur, zkjd, shzt, old_data_json, logger
                )
                total_ybzj_affected += uq_result.get("ybzj_affected", 0)
                total_pygd_affected += uq_result.get("pygd_affected", 0)
                total_syjh_affected += uq_result.get("syjh_affected", 0)
                total_syjh_affected += drug_report_result.get("syjh_affected", 0)
                total_dwsy_affected += uq_result.get("dwsy_affected", 0)

                # 审核通过且需要生成 PDF：添加后台任务
                if shzt == "审核通过" and normalize_zkjd(zkjd) in _pdf_stages:
                    background_tasks.add_task(
                        _generate_qc_pdf_async,
                        rec_id=rid,
                        zkjd=zkjd,
                        gdbh=gdbh or "",
                        zzid=zzid or "",
                    )
                    pdf_triggered.append(rid)
                    logger.info(f"[批量审核] 已添加后台PDF任务: ID={rid}, 阶段={zkjd}")

            except Exception as e:
                failed.append({"F_ID": rid, "reason": str(e)})
                logger.error(f"[批量审核] 处理记录 {rid} 失败: {e}", exc_info=True)

        conn.commit()

    result = {
        "total": len(id_list),
        "affected": total_affected,
        "pygd_affected": total_pygd_affected,
        "ybzj_affected": total_ybzj_affected,
        "syjh_affected": total_syjh_affected,
        "dwsy_affected": total_dwsy_affected,
        "pdf_triggered_ids": pdf_triggered,
        "not_found_ids": not_found,
        "failed": failed,
        "F_SHZT": shzt,
        "F_SHBZ": shbz,
    }
    if pdf_triggered:
        result["pdf_status"] = "generating"

    success_count = total_affected
    fail_count = len(not_found) + len(failed)
    msg = f"批量审核完成：成功 {success_count} 条"
    if fail_count:
        msg += f"，失败/未找到 {fail_count} 条"
        detail_parts: List[str] = []
        for nf_id in not_found[:3]:
            detail_parts.append(f"ID {nf_id}: 记录不存在")
        for item in failed[:5]:
            detail_parts.append(f"ID {item.get('F_ID')}: {item.get('reason') or '未知原因'}")
        total_detail = len(not_found) + len(failed)
        if detail_parts:
            msg += "；" + "；".join(detail_parts)
            if total_detail > len(detail_parts):
                msg += f"（另有 {total_detail - len(detail_parts)} 条未列出，见 data.failed / data.not_found_ids）"
    if pdf_triggered:
        msg += f"，{len(pdf_triggered)} 条 PDF 正在后台生成"
    if total_pygd_affected:
        msg += "，培养工单质控状态已同步"

    if fail_count == 0 and success_count == len(id_list):
        return ok(result, msg)
    return err(msg, code=0, data=result)


@router.post("/detail", response_model=ApiResponse[dict], response_model_exclude_none=True,
             dependencies=[Depends(require_roles(["qc_review"]))])
def get_qc_detail(payload: Dict[str, Any], current_user: dict = Depends(get_current_user_optional)):
    """
    获取质控审核详情接口（精简版 - 仅返回对比数据）
    
    用于质控审核页面的数据对比功能。
    
    入参：
      - F_ID（T_ZKSH表的主键，质控审核记录ID，必填）
    
    返回：
      - F_ID: 记录ID（用于关联）
      - F_CHANGES: 变更字段对比JSON
      - F_OLD_DATA: 修改前的完整详情数据JSON
    
    说明：
      - 其他字段（如F_ZKBH、F_ZKJD、F_SHZT等）应从列表接口获取
      - 此接口专注于提供数据对比功能
    """
    try:
        zksh_id = int(payload.get("F_ID"))
    except Exception as e:
        return err(f"缺少或非法的 F_ID: {str(e)}")
    
    from app.db.factory import get_adapter
    adapter = get_adapter()
    
    try:
        with adapter.connect() as conn:
            cur = conn.cursor()
            # ✅ 只查询核心对比字段
            sql = """
                SELECT F_ID, F_CHANGES, F_OLD_DATA, F_JLR, F_PYR, F_GDID
                FROM T_ZKSH 
                WHERE F_ID = ?
            """
            cur.execute(sql, (zksh_id,))
            row = cur.fetchone()
            if not row:
                return err(f"未找到质控审核记录 (F_ID={zksh_id})")
            
            cols = [c[0] for c in cur.description]
            zksh = dict(zip(cols, row))
            from app.core.data_scope import is_self_scope, resolve_data_scope, is_owner, fetch_pygd_sqr, access_denied_response
            if is_self_scope(resolve_data_scope(current_user, "qc_review")):
                owners = [zksh.get("F_JLR"), zksh.get("F_PYR")]
                if zksh.get("F_GDID") is not None:
                    owners.append(fetch_pygd_sqr(zksh.get("F_GDID")))
                if not any(is_owner(current_user, o) for o in owners if o not in (None, "")):
                    return access_denied_response()
            zksh.pop("F_JLR", None)
            zksh.pop("F_PYR", None)
            zksh.pop("F_GDID", None)
            
            # 解析变更记录
            if zksh.get("F_CHANGES"):
                try:
                    zksh["F_CHANGES"] = json.loads(zksh["F_CHANGES"]) if isinstance(zksh["F_CHANGES"], str) else zksh["F_CHANGES"]
                except Exception:
                    pass
            
            # ✅ 解析旧数据JSON
            if zksh.get("F_OLD_DATA"):
                try:
                    zksh["F_OLD_DATA"] = json.loads(zksh["F_OLD_DATA"]) if isinstance(zksh["F_OLD_DATA"], str) else zksh["F_OLD_DATA"]
                except Exception:
                    pass

            # 病理/测序/其他附件：补全 old_value 中仅含 ids 的历史记录，便于前端反显
            from app.crud.qc_audit import enrich_qc_changes, enrich_qc_old_data
            if zksh.get("F_CHANGES"):
                zksh["F_CHANGES"] = enrich_qc_changes(zksh["F_CHANGES"])
            if zksh.get("F_OLD_DATA"):
                zksh["F_OLD_DATA"] = enrich_qc_old_data(zksh["F_OLD_DATA"])
            
            return ok(zksh, "查询成功")
            
    except Exception as e:
        import traceback
        return err(f"查询质控审核记录失败: {str(e)}\n{traceback.format_exc()}")


@router.post("/list", response_model=ApiResponse[dict], response_model_exclude_none=False,
             dependencies=[Depends(require_roles(["qc_review"]))])
def list_qc(payload: Dict[str, Any], current_user: dict = Depends(get_current_user_optional)):
    """质控审核列表，支持过滤：变更类型、质控编号、培养工单编号、审核状态、标本号、代次ID、组织部位、质控阶段、姓名。

    入参可选：F_BGLX, F_ZKBH, F_GDBH, F_SHZT, F_ZZID, F_DCID, F_ZZBW, F_ZKJD, F_XM, skip, limit
    """
    from app.db.factory import get_adapter
    adapter = get_adapter()

    where: List[str] = []
    params: List[Any] = []

    def like(col: str, val: Any | None):
        if val is None:
            return
        s = str(val).strip()
        if s == "":
            return  # 空字符串不参与过滤，避免排除 NULL 行
        pred, p = adapter.like(col, s)
        where.append(pred)
        params.extend(p)

    # 模糊匹配字段
    like("z.F_BGLX", payload.get("F_BGLX"))
    like("z.F_ZKBH", payload.get("F_ZKBH"))
    like("z.F_GDBH", payload.get("F_GDBH"))
    like("z.F_ZZID", payload.get("F_ZZID"))
    like("z.F_DCID", payload.get("F_DCID"))
    like("z.F_ZZBW", payload.get("F_ZZBW"))
    like("z.F_ZKJD", payload.get("F_ZKJD"))
    like("z.F_XM", payload.get("F_XM"))

    # 审核状态精确匹配（若传）
    if payload.get("F_SHZT") is not None and str(payload.get("F_SHZT")).strip() != "":
        where.append("z.F_SHZT = ?")
        params.append(payload.get("F_SHZT"))

    from app.core.data_scope import apply_owner_or_filter, resolve_data_scope
    apply_owner_or_filter(
        where, params,
        scope=resolve_data_scope(current_user, "qc_review"),
        user=current_user,
        columns=["z.F_JLR", "z.F_PYR"],
        match="name",
    )

    where_sql = (" WHERE " + " AND ".join(where)) if where else ""

    base_core = f"""
SELECT z.F_ID, z.F_ZKBH, z.F_ZKJD, z.F_SHZT, z.F_SHBZ, z.F_GDBH, z.F_DCID, z.F_DC, z.F_ZZID, z.F_XM, z.F_BRBH,
       z.F_ZZBW, z.F_YBLY, z.F_KSPYRQ, z.F_PYTS, z.F_PYR, z.F_PDF, p.F_GDZT, z.F_BGLX, z.F_JLR, z.F_JLSJ
FROM T_ZKSH z
LEFT JOIN T_PYGD p ON p.F_GDBH = z.F_GDBH
{where_sql}
"""
    order_sql = " ORDER BY F_ID DESC"
    count_sql = f"SELECT COUNT(1) FROM ( {base_core} ) t"
    sql, extra = adapter.paginate(base_core + order_sql, skip=int(payload.get("skip") or 0), limit=payload.get("limit"))

    with adapter.connect() as conn:
        cur = conn.cursor()
        # total
        cur.execute(count_sql, tuple(params))
        total = int(cur.fetchone()[0])
        # items
        cur.execute(sql, tuple(params + extra))
        rows = cur.fetchall()
        cols = [c[0] for c in cur.description]
        items: List[Dict[str, Any]] = [dict(zip(cols, r)) for r in rows]

        # 映射 PDF URL 与日期格式
        from app.crud.file_center import get_file_by_id
        for it in items:
            # F_KSPYRQ -> YYYY-MM-DD
            if it.get("F_KSPYRQ") is not None:
                v = it["F_KSPYRQ"]
                try:
                    it["F_KSPYRQ"] = v.strftime("%Y-%m-%d") if hasattr(v, "strftime") else str(v)[:10]
                except Exception:
                    it["F_KSPYRQ"] = str(v)[:10]
            # PDF URL - 转换为静态路径（不带 /uploads/ 前缀）
            if it.get("F_PDF") is not None:
                try:
                    f = get_file_by_id(int(it["F_PDF"]))
                    if f and f.get("F_PATH"):
                        file_path = str(f["F_PATH"]).replace("\\", "/")
                        
                        # 如果包含 "uploads/"，提取 uploads/ 之后的部分
                        if "uploads/" in file_path:
                            idx = file_path.find("uploads/")
                            file_path = file_path[idx + 8:]  # 跳过 "uploads/"，直接从 pdf/ 开始
                        
                        # 如果是绝对路径（Windows: 带盘符，Linux: 以/开头且很长），尝试转换
                        elif ":" in file_path or (file_path.startswith("/") and len(file_path) > 50):
                            try:
                                # 尝试从完整路径中提取 uploads 之后的部分
                                upload_dir_path = Path(settings.upload_dir)
                                full_path = Path(file_path)
                                if full_path.exists() or upload_dir_path.parts[0] in full_path.parts:
                                    # 计算相对路径
                                    rel_path = str(full_path.relative_to(upload_dir_path)).replace("\\", "/")
                                    file_path = rel_path
                            except Exception:
                                # 如果无法计算，尝试字符串方式提取
                                if "pdf/" in file_path:
                                    idx = file_path.find("pdf/")
                                    file_path = file_path[idx:]
                        
                        # 确保路径以 / 开头，但不包含 /uploads/
                        if not file_path.startswith("/"):
                            file_path = "/" + file_path
                        
                        it["F_PDF_URL"] = file_path
                except Exception:
                    pass

    return ok({"items": items, "total": total}, "查询成功")


@router.post("/culture-detail/report-data", response_model=ApiResponse[dict], response_model_exclude_none=True,
             dependencies=[Depends(require_roles(["organoid_culture_tissue"]))])
def get_culture_detail_report_data(payload: Dict[str, Any], current_user: dict = Depends(get_current_user_optional)):
    """
    获取培养工单详情报告数据接口（JSON格式）
    
    功能：
    - 提供完整的嵌套JSON数据结构
    - 供前端预览或PDF生成使用
    - 包含所有必要的数据字段（工单信息、样本信息、预处理信息、培养详情、观察记录等）
    
    前置条件:
    - T_PYGD.F_ZKZT 为 "待审核"、"审核通过" 或 "审核不通过"
    - 或工单已标为不合格 / 不合格审批中（培养、观察阶段尚未提交验证单）
    
    Args:
        payload: {"F_GDID": 工单ID}
        
    Returns:
        完整的报告数据（嵌套JSON结构）
    """
    from app.crud.culture_detail_report import assemble_culture_detail_report
    
    F_GDID = payload.get("F_GDID")
    if not F_GDID:
        return err("缺少必填参数: F_GDID")
    denied = _deny_gdid(current_user, F_GDID)
    if denied:
        return denied

    try:
        report_data = assemble_culture_detail_report(F_GDID, allow_unqualified=True)
        return ok(report_data, "数据获取成功")
    except ValueError as e:
        # 业务错误(如质控状态不符合、工单不存在)
        return err(str(e))
    except Exception as e:
        # 系统错误
        import traceback
        return err(f"数据获取失败: {e}\n{traceback.format_exc()}")


@router.post("/culture-detail/generate-pdf", response_model=ApiResponse[dict], response_model_exclude_none=True,
             dependencies=[Depends(require_roles(["organoid_culture_tissue"]))])
def generate_culture_detail_pdf_endpoint(
    payload: Dict[str, Any],
    current_user: dict = Depends(get_current_user_optional)
):
    """
    生成培养工单详情 PDF
    
    前置条件:
    - T_PYGD.F_ZKZT 为 "待审核"、"审核通过" 或 "审核不通过"
    - 或工单已标为不合格 / 不合格审批中（培养、观察阶段尚未提交验证单）
    
    包含信息:
    - 工单基本信息
    - 样本信息
    - 预处理信息
    - 培养详情
    - 观察记录(动态表格)
    - 质控信息
    
    Args:
        payload: {"F_GDID": 工单ID}
        current_user: 当前用户
        
    Returns:
        PDF文件信息(file_id, file_url等)
    """
    from app.crud.culture_detail_pdf import generate_culture_detail_pdf
    
    F_GDID = payload.get("F_GDID")
    if not F_GDID:
        return err("缺少必填参数: F_GDID")
    denied = _deny_gdid(current_user, F_GDID)
    if denied:
        return denied
    
    operator = None
    try:
        operator = current_user.get("F_YSM") if current_user else None
    except Exception:
        operator = None
    
    try:
        result = generate_culture_detail_pdf(F_GDID, operator=operator)
        return ok(result, "PDF生成成功")
    except ValueError as e:
        # 业务错误(如质控状态不符合、工单不存在)
        return err(str(e))
    except FileNotFoundError as e:
        # 模板文件不存在
        return err(f"模板文件错误: {e}")
    except Exception as e:
        # 系统错误
        import traceback
        return err(f"PDF生成失败: {e}\n{traceback.format_exc()}")


@router.post("/culture-detail/upload-pdf", response_model=ApiResponse[dict], response_model_exclude_none=True,
             dependencies=[Depends(require_roles(["organoid_culture_tissue"]))])
async def upload_culture_detail_pdf(
    F_GDID: str = Form(..., description="工单ID"),
    pdf_file: UploadFile = File(..., description="PDF文件"),
    current_user: dict = Depends(get_current_user_optional)
):
    """
    上传工单PDF文件
    
    Args:
        F_GDID: 工单ID
        pdf_file: PDF文件
        
    Returns:
        上传结果（包含file_id）
    """
    from app.core.storage import build_upload_path
    from app.crud.order_pdf_manager import add_uploaded_pdf
    
    try:
        gdid = int(F_GDID)
    except:
        return err("工单ID格式错误")
    
    # 检查文件类型
    if not pdf_file.filename.lower().endswith('.pdf'):
        return err("只能上传PDF文件")
    
    try:
        # 保存文件
        dst_path, rel = build_upload_path(pdf_file.filename)
        content = await pdf_file.read()
        dst_path.write_bytes(content)
        
        # 插入文件记录
        file_id = insert_file_record(rel, pdf_file.filename)
        
        if not file_id:
            return err("文件记录保存失败")
        
        # 添加到工单PDF列表
        add_uploaded_pdf(gdid, file_id)
        
        return ok({
            "file_id": file_id,
            "filename": pdf_file.filename,
            "file_url": f"/{rel}",
            "gdid": gdid
        }, "PDF上传成功")
    except Exception as e:
        import traceback
        return err(f"PDF上传失败: {e}\n{traceback.format_exc()}")


@router.post("/culture-detail/remove-uploaded-pdf", response_model=ApiResponse[dict], response_model_exclude_none=True,
             dependencies=[Depends(require_roles(["organoid_culture_tissue"]))])
def remove_uploaded_pdf(
    payload: Dict[str, Any],
    current_user: dict = Depends(get_current_user_optional)
):
    """
    删除用户上传的PDF文件记录
    
    Args:
        payload: {"F_GDID": 工单ID, "file_id": 文件ID}
        
    Returns:
        删除结果
    """
    from app.crud.order_pdf_manager import remove_uploaded_pdf as remove_pdf
    
    gdid = payload.get("F_GDID")
    file_id = payload.get("file_id")
    
    if not gdid or not file_id:
        return err("缺少必填参数: F_GDID 或 file_id")
    
    try:
        remove_pdf(int(gdid), int(file_id))
        return ok({"gdid": gdid, "file_id": file_id}, "删除成功")
    except Exception as e:
        return err(f"删除失败: {e}")


@router.post("/culture-detail/pdf-list", response_model=ApiResponse[dict], response_model_exclude_none=True,
             dependencies=[Depends(require_roles(["organoid_culture_tissue"]))])
def get_pdf_list(
    payload: Dict[str, Any],
    current_user: dict = Depends(get_current_user_optional)
):
    """
    获取工单的PDF列表（包含系统生成和用户上传的）
    
    Args:
        payload: {"F_GDID": 工单ID}
        
    Returns:
        PDF列表
        {
            "generated": {
                "file_id": 123,
                "filename": "xxx.pdf",
                "url": "/uploads/pdf/xxx.pdf",
                "created_at": "2025-11-11 10:00:00"
            },
            "uploaded": [
                {
                    "file_id": 456,
                    "filename": "yyy.pdf",
                    "url": "/uploads/xxx/yyy.pdf",
                    "created_at": "2025-11-11 11:00:00"
                }
            ]
        }
    """
    from app.crud.order_pdf_manager import get_pdf_list_with_details
    
    gdid = payload.get("F_GDID")
    if not gdid:
        return err("缺少必填参数: F_GDID")
    denied = _deny_gdid(current_user, gdid)
    if denied:
        return denied
    
    try:
        pdf_list = get_pdf_list_with_details(int(gdid))
        return ok(pdf_list, "查询成功")
    except Exception as e:
        import traceback
        return err(f"查询失败: {e}\n{traceback.format_exc()}")


# ==================== 样本接收质控PDF ====================

@router.post("/sample-receive/report-data", response_model=ApiResponse[dict], response_model_exclude_none=True,
             dependencies=[Depends(require_roles(["sample_receive"]))])
def get_sample_receive_report_data(payload: Dict[str, Any], current_user: dict = Depends(get_current_user_optional)):
    """
    获取样本接收质控报告数据接口（JSON格式）
    
    功能：
    - 提供完整的嵌套JSON数据结构
    - 供前端预览或PDF生成使用
    - 包含就诊信息、标本信息、质控信息
    
    Args:
        payload: {"F_ZZID": 标本号}
        
    Returns:
        完整的报告数据（嵌套JSON结构）
    """
    from app.crud.sample_receive_report import assemble_sample_receive_report
    
    F_ZZID = payload.get("F_ZZID")
    if not F_ZZID:
        return err("缺少必填参数: F_ZZID")
    denied = _deny_zzid(current_user, F_ZZID)
    if denied:
        return denied
    
    try:
        report_data = assemble_sample_receive_report(F_ZZID)
        return ok(report_data, "数据获取成功")
    except ValueError as e:
        # 业务错误(如标本不存在、数据不完整)
        return err(str(e))
    except Exception as e:
        # 系统错误
        import traceback
        return err(f"数据获取失败: {e}\n{traceback.format_exc()}")


@router.post("/sample-receive/generate-pdf", response_model=ApiResponse[dict], response_model_exclude_none=True,
             dependencies=[Depends(require_roles(["sample_receive"]))])
def generate_sample_receive_pdf_api(payload: Dict[str, Any], current_user: dict = Depends(get_current_user_optional)):
    """
    生成样本接收质控PDF接口
    
    功能：
    - 基于Word模板生成样本接收质控PDF
    - 自动注册到文件中心
    - 自动保存到T_YBZJ.F_PDF字段
    - 返回文件ID和访问路径
    
    Args:
        payload: {"F_ZZID": 标本号}
        
    Returns:
        {"file_id": 文件ID, "file_path": 文件路径, "file_name": 文件名}
    """
    from app.crud.sample_receive_pdf import generate_sample_receive_pdf
    
    F_ZZID = payload.get("F_ZZID")
    if not F_ZZID:
        return err("缺少必填参数: F_ZZID")
    denied = _deny_zzid(current_user, F_ZZID)
    if denied:
        return denied
    
    try:
        result = generate_sample_receive_pdf(F_ZZID)
        
        # 检查是否有警告（PDF转换失败，返回DOCX）
        if "warning" in result:
            return ok(result, f"生成成功（{result['warning']}）")
        
        return ok(result, "PDF生成成功")
    except ValueError as e:
        # 业务错误
        return err(str(e))
    except FileNotFoundError as e:
        # 文件不存在错误
        return err(f"模板文件不存在: {e}")
    except Exception as e:
        # 系统错误
        import traceback
        return err(f"PDF生成失败: {e}\n{traceback.format_exc()}")


@router.post("/sample-receive/pdf-list", response_model=ApiResponse[dict], response_model_exclude_none=True,
             dependencies=[Depends(require_roles(["sample_receive"]))])
def get_sample_receive_pdf_list(payload: Dict[str, Any], current_user: dict = Depends(get_current_user_optional)):
    """
    获取样本接收质控PDF列表接口
    
    功能：
    - 查询样本的所有PDF文件（系统生成+用户上传）
    - 返回文件详细信息（文件名、URL、创建时间等）
    
    Args:
        payload: {"F_ZZID": 标本号}
        
    Returns:
        {
            "generated": {
                "file_id": 123,
                "filename": "sample_receive_xxx.pdf",
                "url": "/uploads/pdf/sample_receive_xxx.pdf",
                "created_at": "2025-11-12 10:00:00"
            },
            "uploaded": [
                {
                    "file_id": 456,
                    "filename": "custom.pdf",
                    "url": "/uploads/xxx/custom.pdf",
                    "created_at": "2025-11-12 11:00:00"
                }
            ]
        }
    """
    from app.crud.sample_receive_pdf_manager import get_pdf_list_with_details
    
    F_ZZID = payload.get("F_ZZID")
    if not F_ZZID:
        return err("缺少必填参数: F_ZZID")
    denied = _deny_zzid(current_user, F_ZZID)
    if denied:
        return denied
    
    try:
        pdf_list = get_pdf_list_with_details(F_ZZID)
        return ok(pdf_list, "查询成功")
    except Exception as e:
        import traceback
        return err(f"查询失败: {e}\n{traceback.format_exc()}")


# ==================== 样本评分表PDF ====================

@router.post("/preprocess-score/report-data", response_model=ApiResponse[dict], response_model_exclude_none=True,
             dependencies=[Depends(require_roles(["organoid_preprocess"]))])
def get_preprocess_score_report_data(payload: Dict[str, Any], current_user: dict = Depends(get_current_user_optional)):
    """
    获取样本评分表报告数据接口（JSON格式）
    
    功能：
    - 提供完整的嵌套JSON数据结构
    - 供前端预览或PDF生成使用
    - 包含工单信息、患者信息、标本信息、预处理信息、评分信息
    
    Args:
        payload: {"F_GDID": 工单ID}
        
    Returns:
        完整的报告数据（嵌套JSON结构）
    """
    from app.crud.preprocess_score_report import assemble_preprocess_score_report
    
    F_GDID = payload.get("F_GDID")
    if not F_GDID:
        return err("缺少必填参数: F_GDID")
    denied = _deny_gdid(current_user, F_GDID)
    if denied:
        return denied
    
    try:
        report_data = assemble_preprocess_score_report(int(F_GDID))
        return ok(report_data, "数据获取成功")
    except ValueError as e:
        # 业务错误(如工单不存在、数据不完整)
        return err(str(e))
    except Exception as e:
        # 系统错误
        import traceback
        return err(f"数据获取失败: {e}\n{traceback.format_exc()}")


@router.post("/preprocess-score/generate-pdf", response_model=ApiResponse[dict], response_model_exclude_none=True,
             dependencies=[Depends(require_roles(["organoid_preprocess"]))])
def generate_preprocess_score_pdf_api(payload: Dict[str, Any], current_user: dict = Depends(get_current_user_optional)):
    """
    生成样本评分表PDF接口
    
    功能：
    - 基于Word模板生成样本评分表PDF
    - 自动注册到文件中心
    - 自动保存到T_YCL.F_PDF字段
    - 返回文件ID和访问路径
    
    Args:
        payload: {"F_GDID": 工单ID}
        
    Returns:
        {"file_id": 文件ID, "file_path": 文件路径, "file_name": 文件名}
    """
    from app.crud.preprocess_score_pdf import generate_preprocess_score_pdf
    
    F_GDID = payload.get("F_GDID")
    if not F_GDID:
        return err("缺少必填参数: F_GDID")
    denied = _deny_gdid(current_user, F_GDID)
    if denied:
        return denied
    
    try:
        result = generate_preprocess_score_pdf(int(F_GDID))
        
        # 检查是否有警告（PDF转换失败，返回DOCX）
        if "warning" in result:
            return ok(result, f"生成成功（{result['warning']}）")
        
        return ok(result, "PDF生成成功")
    except ValueError as e:
        # 业务错误
        return err(str(e))
    except FileNotFoundError as e:
        # 文件不存在错误
        return err(f"模板文件不存在: {e}")
    except Exception as e:
        # 系统错误
        import traceback
        return err(f"PDF生成失败: {e}\n{traceback.format_exc()}")


@router.post("/preprocess-score/pdf-list", response_model=ApiResponse[dict], response_model_exclude_none=True,
             dependencies=[Depends(require_roles(["organoid_preprocess"]))])
def get_preprocess_score_pdf_list(payload: Dict[str, Any], current_user: dict = Depends(get_current_user_optional)):
    """
    获取样本评分表PDF列表接口
    
    功能：
    - 查询工单的所有样本评分表PDF文件（系统生成+用户上传）
    - 返回文件详细信息（文件名、URL、创建时间等）
    
    Args:
        payload: {"F_GDID": 工单ID}
        
    Returns:
        {
            "generated": {
                "file_id": 123,
                "filename": "preprocess_score_xxx.pdf",
                "url": "/uploads/pdf/preprocess_score_xxx.pdf",
                "created_at": "2025-11-12 10:00:00"
            },
            "uploaded": [
                {
                    "file_id": 456,
                    "filename": "custom.pdf",
                    "url": "/uploads/xxx/custom.pdf",
                    "created_at": "2025-11-12 11:00:00"
                }
            ]
        }
    """
    from app.crud.preprocess_score_pdf_manager import get_pdf_list_with_details
    
    F_GDID = payload.get("F_GDID")
    if not F_GDID:
        return err("缺少必填参数: F_GDID")
    denied = _deny_gdid(current_user, F_GDID)
    if denied:
        return denied
    
    try:
        pdf_list = get_pdf_list_with_details(int(F_GDID))
        return ok(pdf_list, "查询成功")
    except Exception as e:
        import traceback
        return err(f"查询失败: {e}\n{traceback.format_exc()}")


# ================================
# 放行检测PDF接口
# ================================

@router.post("/release-inspection/report-data")
async def get_release_inspection_report_data_api(payload: Dict[str, Any] = Body(...), current_user: dict = Depends(get_current_user_optional)):
    """
    获取放行检测报告数据
    
    入参: {"F_GDID": 工单ID}
    
    返回:
    {
        "code": 1,
        "msg": "数据获取成功",
        "data": {
            "报告标题": "类器官放行检测报告",
            "生成时间": "2025年11月13日 10:00:00",
            "工单信息": {...},
            "患者信息": {...},
            "标本信息": {...},
            "预处理信息": {...},
            "培养详情": {...},
            "观察记录": [...],
            "回收记录": [...],
            "质控记录": {...}
        }
    }
    """
    from app.crud.release_inspection_report import assemble_release_inspection_report
    
    F_GDID = payload.get("F_GDID")
    if not F_GDID:
        return err("缺少必填参数: F_GDID")
    denied = _deny_gdid(current_user, F_GDID)
    if denied:
        return denied
    
    try:
        data = assemble_release_inspection_report(int(F_GDID))
        return ok(data, "数据获取成功")
    except ValueError as ve:
        return err(str(ve))
    except Exception as e:
        import traceback
        return err(f"数据获取失败: {e}\n{traceback.format_exc()}")


@router.post("/release-inspection/generate-pdf")
async def generate_release_inspection_pdf_api(payload: Dict[str, Any] = Body(...), current_user: dict = Depends(get_current_user_optional)):
    """
    生成放行检测PDF
    
    入参: {"F_GDID": 工单ID}
    
    返回:
    {
        "code": 1,
        "msg": "PDF生成成功",
        "data": {
            "file_id": 2234,
            "file_path": "/pdf/release_inspection_xxx_20251113_100000.pdf",
            "file_name": "release_inspection_xxx_20251113_100000.pdf"
        }
    }
    """
    from app.crud.release_inspection_pdf import generate_release_inspection_pdf
    
    F_GDID = payload.get("F_GDID")
    if not F_GDID:
        return err("缺少必填参数: F_GDID")
    denied = _deny_gdid(current_user, F_GDID)
    if denied:
        return denied
    
    try:
        result = generate_release_inspection_pdf(int(F_GDID))
        return ok(result, "PDF生成成功")
    except ValueError as ve:
        return err(str(ve))
    except Exception as e:
        import traceback
        return err(f"PDF生成失败: {e}\n{traceback.format_exc()}")


@router.post("/release-inspection/pdf-list")
async def get_release_inspection_pdf_list_api(payload: Dict[str, Any] = Body(...), current_user: dict = Depends(get_current_user_optional)):
    """
    获取放行检测PDF列表（支持系统生成和用户上传）
    
    入参: {"F_GDID": 工单ID}
    
    返回:
    {
        "code": 1,
        "msg": "查询成功",
        "data": {
            "generated": {
                "file_id": 2234,
                "filename": "release_inspection_xxx_20251113_100000.pdf",
                "url": "/pdf/release_inspection_xxx_20251113_100000.pdf",
                "created_at": "2025-11-13 10:00:00"
            },
            "uploaded": [
                {
                    "file_id": 2235,
                    "filename": "user_uploaded.pdf",
                    "url": "/uploads/2025/11/13/user_uploaded.pdf",
                    "created_at": "2025-11-13 11:00:00"
                }
            ]
        }
    }
    """
    from app.crud.qc_record import get_qc_record_by_gdid
    from app.crud.file_center import get_file_details
    
    F_GDID = payload.get("F_GDID")
    if not F_GDID:
        return err("缺少必填参数: F_GDID")
    denied = _deny_gdid(current_user, F_GDID)
    if denied:
        return denied
    
    try:
        # 获取质控记录
        zkjl = get_qc_record_by_gdid(int(F_GDID))
        if not zkjl:
            return ok({"generated": None, "uploaded": []}, "查询成功（无质控记录）")
        
        # 获取放行检测PDF的JSON数据
        pdf_data = zkjl.get("F_PDF_FXJC_PARSED", {"generated": None, "uploaded": []})
        
        result = {
            "generated": None,
            "uploaded": []
        }
        
        # 获取系统生成的PDF详情
        if pdf_data.get("generated"):
            file_info = get_file_details(pdf_data["generated"])
            result["generated"] = file_info if file_info else None
        
        # 获取用户上传的PDF详情
        if pdf_data.get("uploaded"):
            for file_id in pdf_data["uploaded"]:
                file_info = get_file_details(file_id)
                if file_info:
                    result["uploaded"].append(file_info)
        
        return ok(result, "查询成功")
    except Exception as e:
        import traceback
        return err(f"查询失败: {e}\n{traceback.format_exc()}")


