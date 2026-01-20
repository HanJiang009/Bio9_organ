from fastapi import APIRouter, Depends, UploadFile, File, Form, Body, BackgroundTasks
from typing import Dict, Any, List
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


@router.post("/overview-by-gdid", response_model=ApiResponse[dict], response_model_exclude_none=True,
             dependencies=[Depends(require_roles(["qc_review"]))])
def overview_by_gdid(payload: Dict[str, Any]):
    try:
        gdid = int(payload.get("F_GDID"))
    except Exception as e:
        return err(f"缺少或非法的 F_GDID: {str(e)}")

    # 工单信息
    try:
        order = crud_order.get_culture_order_detail(gdid)
        if not order:
            return err(f"未找到工单 (F_GDID={gdid})")
    except Exception as e:
        import traceback
        return err(f"查询工单失败: {str(e)}\n{traceback.format_exc()}")

    # 计算当前培养天数：now - F_PYSJ（按自然日差）
    current_pyts = None
    pysj = order.get("F_PYSJ")
    if pysj:
        dt = None
        if isinstance(pysj, str):
            # 兼容 "YYYY-MM-DD HH:MM:SS" 与 ISO
            try:
                dt = datetime.fromisoformat(pysj)
            except Exception:
                try:
                    dt = datetime.strptime(pysj, "%Y-%m-%d %H:%M:%S")
                except Exception:
                    dt = None
        elif hasattr(pysj, "date"):
            dt = pysj
        if dt is not None:
            current_pyts = (datetime.now().date() - dt.date()).days

    # 样本质控记录（接收时间）
    zzid = order.get("F_ZZID") 
    sample_qc = {}
    if zzid:
        try:
            base = query_by_ids(zzid, None) or {}
            bbxx = base.get("BBXX") or {}
            if bbxx.get("F_JSSJ") is not None:
                sample_qc["F_JSSJ"] = bbxx.get("F_JSSJ")
        except Exception as e:
            # 样本查询失败不影响整体流程
            pass

    # 预处理信息
    try:
        preprocess = crud_pre.fetch_preprocess_by_gdid(gdid) or {}
    except Exception as e:
        # 预处理查询失败不影响整体流程
        preprocess = {}

    # 观察记录挑选固定天数与倍率
    wanted_days = {1, 3, 7}
    wanted_mags = {4, 20}
    try:
        obs = crud_obs.list_observations_by_gdid(gdid)
    except Exception as e:
        # 观察记录查询失败不影响整体流程
        obs = []
    # 选"同一天最新"的记录：按 F_GCSJ(desc)/F_ID(desc)
    def _parse_dt(s: Any):
        if s is None:
            return None
        if isinstance(s, datetime):
            return s
        try:
            return datetime.fromisoformat(str(s))
        except Exception:
            try:
                return datetime.strptime(str(s), "%Y-%m-%d %H:%M:%S")
            except Exception:
                return None

    latest_by_day: Dict[int, Dict[str, Any]] = {}
    for it in obs:
        day = it.get("F_PYTS")
        if day is None:
            continue
        try:
            day_int = int(day)
        except Exception:
            continue
        if day_int not in wanted_days:
            continue
        cur = latest_by_day.get(day_int)
        ts_new = _parse_dt(it.get("F_GCSJ"))
        if not cur:
            latest_by_day[day_int] = it
        else:
            ts_old = _parse_dt(cur.get("F_GCSJ"))
            # 若新记录时间更晚，或时间不可比且ID更大，则替换
            if (ts_new and ts_old and ts_new > ts_old) or (ts_new and not ts_old) or (
                not ts_new and not ts_old and (it.get("F_ID") or 0) > (cur.get("F_ID") or 0)
            ):
                latest_by_day[day_int] = it

    # 收集需要的文件ID
    need_ids: List[int] = []
    obs_images: List[Dict[str, Any]] = []
    for day in sorted(latest_by_day.keys()):
        record = latest_by_day[day]
        fj = record.get("F_FJ") or []
        # 从该记录中找倍率 4 与 20
        for mag in sorted(list(wanted_mags)):
            pick = None
            for e in fj:
                try:
                    if int(e.get("F_BS")) == int(mag) and e.get("F_WJ_ID") is not None:
                        pick = int(e.get("F_WJ_ID"))
                        break
                except Exception:
                    continue
            if pick is not None:
                need_ids.append(pick)
                obs_images.append({"day": day, "mag": mag, "fileId": pick})

    # 查文件路径并组装 URL
    id_to_path: Dict[int, str] = {}
    if need_ids:
        files = get_files_by_ids(sorted(list({i for i in need_ids if i is not None})))
        for f in files:
            try:
                id_to_path[int(f.get("F_ID"))] = f.get("F_PATH")
            except Exception:
                pass
    base_prefix = "/" + settings.upload_dir.strip("/") + "/"
    for it in obs_images:
        p = id_to_path.get(int(it["fileId"]))
        it["url"] = (base_prefix + str(p).lstrip("/")) if p else None

    # 构建查找表方便填充 1/3/7 天 × 4/20 倍 的 URL
    urls_by_day_mag: Dict[tuple, str] = {}
    for it in obs_images:
        d = it.get("day")
        m = it.get("mag")
        u = it.get("url")
        if d is not None and m is not None and u:
            try:
                urls_by_day_mag[(int(d), int(m))] = u
            except Exception:
                pass

    # 查询质控记录
    zkjl = {}
    try:
        from app.crud.qc_record import get_qc_record_by_gdid
        zkjl_record = get_qc_record_by_gdid(gdid)
        if zkjl_record:
            zkjl = {
                "F_ID": zkjl_record.get("F_ID"),
                "F_GDID": zkjl_record.get("F_GDID"),
                "F_QKMS": zkjl_record.get("F_QKMS"),
                "F_IMG_D1M4": zkjl_record.get("F_IMG_D1M4"),
                "F_IMG_D1M20": zkjl_record.get("F_IMG_D1M20"),
                "F_IMG_D3M4": zkjl_record.get("F_IMG_D3M4"),
                "F_IMG_D3M20": zkjl_record.get("F_IMG_D3M20"),
                "F_IMG_D7M4": zkjl_record.get("F_IMG_D7M4"),
                "F_IMG_D7M20": zkjl_record.get("F_IMG_D7M20"),
                "F_PYTS": zkjl_record.get("F_PYTS"),
                "F_XT": zkjl_record.get("F_XT"),
                "F_SFWR": zkjl_record.get("F_SFWR"),
                "F_XBSL": zkjl_record.get("F_XBSL"),
                "F_XBHL": zkjl_record.get("F_XBHL"),
                "F_ZYT": zkjl_record.get("F_ZYT"),
                "F_PDF": zkjl_record.get("F_PDF_PARSED"),
                "F_PDF_FXJC": zkjl_record.get("F_PDF_FXJC_PARSED"),  # 返回解析后的JSON格式
                "F_JLR": zkjl_record.get("F_JLR"),
                "F_JLSJ": str(zkjl_record.get("F_JLSJ")) if zkjl_record.get("F_JLSJ") else None,
                "F_GXSJ": str(zkjl_record.get("F_GXSJ")) if zkjl_record.get("F_GXSJ") else None,
            }
            # 将文件ID转换为URL
            from app.crud.file_center import get_file_url
            for img_field in ["F_IMG_D1M4", "F_IMG_D1M20", "F_IMG_D3M4", "F_IMG_D3M20", "F_IMG_D7M4", "F_IMG_D7M20"]:
                file_id = zkjl.get(img_field)
                if file_id:
                    url = get_file_url(file_id)
                    zkjl[f"{img_field}_URL"] = url if url else None
    except Exception as e:
        import traceback
        print(f"警告: 查询质控记录失败: {e}\n{traceback.format_exc()}")

    # 组织返回结构（严格按需求）
    gdxx: Dict[str, Any] = {
        "F_ID": order.get("F_ID"),
        "F_GDBH": order.get("F_GDBH"),
        "F_ZZID": order.get("F_ZZID"),
        "F_XM": order.get("F_XM"),
        "F_BRBH": order.get("F_BRBH"),
        "F_BBLX": order.get("F_BBLX"),
        "F_DC": order.get("F_DC"),
        "F_DCID": order.get("F_DCID"),
        "F_ZZBW": order.get("F_ZZBW"),
        "F_XYZL": order.get("F_XYZL"),
        "F_QYFS": order.get("F_QYFS"),
        "F_YBLY": order.get("F_YBLY"),
        "F_YBGG": order.get("F_YBGG"),
        "F_SQR": order.get("F_SQR"),
        "F_PYSJ": order.get("F_PYSJ"),
    }

    ybzkjl: Dict[str, Any] = {
        "F_JSSJ": sample_qc.get("F_JSSJ") if sample_qc else None,
        "F_KSCLSJ": preprocess.get("F_KSCLSJ") if preprocess else None,
        "F_YBYCLQK": preprocess.get("F_YBYCLQK") if preprocess else None,
        "F_YBYCL_URL": preprocess.get("F_YBYCL_URL") if preprocess else None,
    }

    # "来曲唑"质控记录（观察记录 - 固定天数倍率图）
    # 优先从ZKJL质控记录获取，如果没有则从观察记录获取
    lqgzkjl: Dict[str, Any] = {}
    
    if zkjl:
        # 如果存在质控记录，使用质控记录的数据
        lqgzkjl = {
            "F_PYTS": zkjl.get("F_PYTS") or (int(current_pyts) if current_pyts is not None else None),
            "F_SZQK": zkjl.get("F_QKMS"),
            "F_XT": zkjl.get("F_XT"),
            "F_SFWR": zkjl.get("F_SFWR"),
            "F_XBSL": zkjl.get("F_XBSL"),
            "F_XBHL": zkjl.get("F_XBHL"),
            "F_ZYTJCJG": zkjl.get("F_ZYT"),
            "DAY1_BS4_URL": zkjl.get("F_IMG_D1M4_URL") or urls_by_day_mag.get((1, 4)),
            "DAY1_BS20_URL": zkjl.get("F_IMG_D1M20_URL") or urls_by_day_mag.get((1, 20)),
            "DAY3_BS4_URL": zkjl.get("F_IMG_D3M4_URL") or urls_by_day_mag.get((3, 4)),
            "DAY3_BS20_URL": zkjl.get("F_IMG_D3M20_URL") or urls_by_day_mag.get((3, 20)),
            "DAY7_BS4_URL": zkjl.get("F_IMG_D7M4_URL") or urls_by_day_mag.get((7, 4)),
            "DAY7_BS20_URL": zkjl.get("F_IMG_D7M20_URL") or urls_by_day_mag.get((7, 20)),
        }
    else:
        # 如果不存在质控记录，使用观察记录的数据（保持原有逻辑）
        lqgzkjl = {
            "F_PYTS": int(current_pyts) if current_pyts is not None else None,
            "DAY1_BS4_URL": urls_by_day_mag.get((1, 4)),
            "DAY1_BS20_URL": urls_by_day_mag.get((1, 20)),
            "DAY3_BS4_URL": urls_by_day_mag.get((3, 4)),
            "DAY3_BS20_URL": urls_by_day_mag.get((3, 20)),
            "DAY7_BS4_URL": urls_by_day_mag.get((7, 4)),
            "DAY7_BS20_URL": urls_by_day_mag.get((7, 20)),
        }

    return ok({
        "GDXX": gdxx,
        "YBZKJL": ybzkjl,
        "LQGZKJL": lqgzkjl,
        "ZKJL": zkjl,  # 新增：质控记录
    }, "查询成功")


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
    # 用户上传的6张图片(可选)
    IMG_DAY1_BS4: UploadFile = File(None),
    IMG_DAY1_BS20: UploadFile = File(None),
    IMG_DAY3_BS4: UploadFile = File(None),
    IMG_DAY3_BS20: UploadFile = File(None),
    IMG_DAY7_BS4: UploadFile = File(None),
    IMG_DAY7_BS20: UploadFile = File(None),
    IMG_YBYCL: UploadFile = File(None),
    current_user: dict = Depends(get_current_user_optional),
):
    """
    提交质控单接口（验证单阶段）
    
    此接口用于提交验证单，质控阶段固定为"验证单"。
    验证单审核通过后会生成放行检测报告，并回写工单质控状态。
    
    支持两种图片模式:
    1. 用户上传图片: 通过 form-data 上传图片文件
    2. 自动读取图片: 从观察记录中读取图片
    
    优先级: 用户上传 > 观察记录
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

    # 复用概览数据
    overview = overview_by_gdid({"F_GDID": gdid})
    if (overview or {}).get("code") != 1:
        return err("无法生成概览数据")
    data = (overview or {}).get("data") or {}

    gdxx = data.get("GDXX") or {}
    ybzkjl = data.get("YBZKJL") or {}
    lqgzkjl = data.get("LQGZKJL") or {}

    # 合并用户填写字段到 LQGZKJL
    for k in ("F_SZQK", "F_XT", "F_SFWR", "F_XBSL", "F_XBHL", "F_ZYTJCJG"):
        if k in payload:
            lqgzkjl[k] = payload.get(k)

    # ✅ 一工单+一质控阶段仅允许一条质控审核记录：
    # - 若已存在且 F_SHZT='待审核'，允许覆盖更新
    # - 若已审核（非"待审核"），拒绝创建
    # - 不同质控阶段（F_ZKJD）是独立的记录
    from app.db.factory import get_adapter
    adapter = get_adapter()
    existing: Dict[str, Any] | None = None
    with adapter.connect() as conn:
        cur = conn.cursor()
        # ✅ 扩展查询字段，用于完整的旧数据对比
        # ✅ 重要：必须同时匹配 F_GDBH + F_ZKJD，确保不同阶段的质控记录是独立的
        sql = """SELECT F_ID, F_ZKBH, F_PDF, F_SHZT, F_ZKJD, F_DCID, F_DC, F_ZZID, 
                        F_XM, F_BRBH, F_ZZBW, F_YBLY, F_KSPYRQ, F_PYTS, F_PYR 
                 FROM T_ZKSH WHERE F_GDBH = ? AND F_ZKJD = ? ORDER BY F_ID DESC"""
        paginated_sql, extra = adapter.paginate(sql, 0, 1)
        cur.execute(paginated_sql, (gdxx.get("F_GDBH"), F_ZKJD) + tuple(extra))
        row_exist = cur.fetchone()
        if row_exist:
            cols = [c[0] for c in cur.description]
            existing = dict(zip(cols, row_exist))
            shzt_val = str((existing.get("F_SHZT") or "")).strip()
            if shzt_val != "待审核":
                return err(f"该工单的【{F_ZKJD}】阶段已审核，不能重复创建")

    # 组装渲染上下文与图片路径
    ctx: Dict[str, Any] = {}
    ctx.update({f"GDXX_{k}": v for k, v in gdxx.items()})
    ctx.update({f"YBZKJL_{k}": v for k, v in ybzkjl.items()})
    ctx.update({f"LQGZKJL_{k}": v for k, v in lqgzkjl.items()})

    # 处理上传的图片
    async def save_uploaded_image(upload_file: UploadFile) -> Path | None:
        """保存用户上传的图片到临时目录"""
        if not upload_file or not upload_file.filename:
            return None
        try:
            import os
            from app.core.storage import build_upload_path_in
            # 保存到 uploads/qc_images/ 目录
            timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
            ext = os.path.splitext(upload_file.filename)[1]
            filename = f"qc_{gdid}_{timestamp}_{upload_file.filename}"
            dst_path, rel = build_upload_path_in("qc_images", filename)
            content = await upload_file.read()
            dst_path.write_bytes(content)
            return dst_path
        except Exception as e:
            print(f"保存上传图片失败: {e}")
            return None
    
    # 保存所有上传的图片
    uploaded_images = {
        "IMG_DAY1_BS4": await save_uploaded_image(IMG_DAY1_BS4),
        "IMG_DAY1_BS20": await save_uploaded_image(IMG_DAY1_BS20),
        "IMG_DAY3_BS4": await save_uploaded_image(IMG_DAY3_BS4),
        "IMG_DAY3_BS20": await save_uploaded_image(IMG_DAY3_BS20),
        "IMG_DAY7_BS4": await save_uploaded_image(IMG_DAY7_BS4),
        "IMG_DAY7_BS20": await save_uploaded_image(IMG_DAY7_BS20),
        "IMG_YBYCL": await save_uploaded_image(IMG_YBYCL),
    }
    
    # 图片字段：优先使用用户上传的图片,没有则使用观察记录的图片
    def to_local_path(url: Any) -> Any:
        if not url:
            return None
        try:
            p = str(url)
            # url 如 "/uploads/2025/..."，需要映射到磁盘路径
            if p.startswith("/"):
                p = p[1:]
            return (Path(__file__).resolve().parents[2] / p)
        except Exception:
            return None

    from pathlib import Path as _P
    image_fields: Dict[str, Any] = {}
    
    # 6张倍率图: 优先使用上传的图片
    for day in (1, 3, 7):
        for mag in (4, 20):
            key = f"DAY{day}_BS{mag}_URL"
            ctx_key = f"IMG_DAY{day}_BS{mag}"
            # 优先级: 用户上传 > 观察记录
            if uploaded_images.get(ctx_key):
                image_fields[ctx_key] = uploaded_images[ctx_key]
            else:
                image_fields[ctx_key] = to_local_path(lqgzkjl.get(key))
    
    # 预处理图片: 优先使用上传的图片
    if uploaded_images.get("IMG_YBYCL"):
        image_fields["IMG_YBYCL"] = uploaded_images["IMG_YBYCL"]
    else:
        image_fields["IMG_YBYCL"] = to_local_path(ybzkjl.get("F_YBYCL_URL"))

    # 生成 ZKBH（覆盖更新沿用原编号，否则新生成）
    zkbh = (existing.get("F_ZKBH") if isinstance(existing, dict) else None) or f"QC-{datetime.now().strftime('%Y%m%d%H%M%S')}"
    
    # 渲染 PDF（失败不影响后续流程）
    pdf_id = None
    pdf_url = None
    pdf_error = None
    try:
        out_basename = f"zk_{gdxx.get('F_GDBH')}_{datetime.now().strftime('%Y%m%d%H%M%S')}"
        pdf_path = render_pdf_with_images("zk_template.docx", ctx, image_fields, out_basename)

        # 登记文件中心
        # settings.upload_dir 现在是绝对路径,需要转换为 Path 对象
        upload_dir_path = Path(settings.upload_dir)
        rel = str(pdf_path.relative_to(upload_dir_path)).replace("\\", "/")
        pdf_id = insert_file_record(rel, f"{out_basename}.pdf")
        pdf_url = f"/{rel}"
        print(f"PDF生成成功: {pdf_path}, 文件ID={pdf_id}")
    except Exception as e:
        import traceback
        pdf_error = str(e)
        print(f"警告: PDF生成失败，继续处理数据写入: {e}\n{traceback.format_exc()}")
    
    # 将上传的图片注册到文件中心，获取文件ID
    from app.crud.file_center import insert_file_record as insert_file
    
    img_file_ids = {}
    for key, path in uploaded_images.items():
        if path and path.exists():
            # 将绝对路径转换为相对于upload_dir的路径
            try:
                rel_path = str(path.relative_to(upload_dir_path)).replace("\\", "/")
                file_id = insert_file(rel_path, path.name)
                img_file_ids[key] = file_id
                print(f"注册上传图片到文件中心: {key} -> 文件ID={file_id}")
            except Exception as e:
                print(f"警告: 注册图片失败 ({key}): {e}")
    
    # 从观察记录中获取图片文件ID（如果用户没有上传）
    obs_img_file_ids = {}
    for day in (1, 3, 7):
        for mag in (4, 20):
            key = f"IMG_DAY{day}_BS{mag}"
            if key not in img_file_ids:  # 用户没有上传这张图片
                # 尝试从观察记录中获取
                url_key = f"DAY{day}_BS{mag}_URL"
                url = lqgzkjl.get(url_key)
                if url:
                    # URL格式: /uploads/2025/11/12/xxx.jpg
                    # 需要从观察记录的F_FJ JSON中获取对应的F_WJ_ID
                    # 这里简化处理：从最新观察记录中查找对应天数和倍数的文件ID
                    try:
                        from app.crud import observation as crud_obs
                        obs = crud_obs.list_observations_by_gdid(gdid)
                        for record in obs:
                            if record.get("F_PYTS") == day:
                                fj = record.get("F_FJ") or []
                                for item in fj:
                                    if item.get("F_BS") == mag and item.get("F_WJ_ID"):
                                        obs_img_file_ids[key] = int(item["F_WJ_ID"])
                                        break
                                if key in obs_img_file_ids:
                                    break
                    except Exception as e:
                        print(f"警告: 从观察记录获取图片文件ID失败 ({key}): {e}")
    
    # 合并图片文件ID（用户上传优先）
    final_img_ids = {**obs_img_file_ids, **img_file_ids}
    
    # 获取最新观察记录的培养天数
    final_pyts = pyts_val
    if final_pyts is None:
        try:
            from app.crud import observation as crud_obs
            obs_list = crud_obs.list_observations_by_gdid(gdid)
            if obs_list:
                # 取最新的观察记录
                latest_obs = obs_list[0]  # 已按时间倒序
                final_pyts = latest_obs.get("F_PYTS")
        except Exception as e:
            print(f"警告: 获取观察记录培养天数失败: {e}")
    
    # 写入 T_ZKJL 质控记录
    try:
        from app.crud.qc_record import create_or_update_qc_record
        qc_data = {
            "F_QKMS": F_SZQK,  # 类器官生长情况描述
            "F_IMG_D1M4": final_img_ids.get("IMG_DAY1_BS4"),
            "F_IMG_D1M20": final_img_ids.get("IMG_DAY1_BS20"),
            "F_IMG_D3M4": final_img_ids.get("IMG_DAY3_BS4"),
            "F_IMG_D3M20": final_img_ids.get("IMG_DAY3_BS20"),
            "F_IMG_D7M4": final_img_ids.get("IMG_DAY7_BS4"),
            "F_IMG_D7M20": final_img_ids.get("IMG_DAY7_BS20"),
            "F_PYTS": final_pyts,
            "F_XT": F_XT,
            "F_SFWR": F_SFWR if isinstance(F_SFWR, str) else ("是" if sfwr_val == 1 else "否"),
            "F_XBSL": F_XBSL,
            "F_XBHL": xbhl_val,
            "F_ZYT": F_ZYTJCJG,  # 注意：入参是F_ZYTJCJG，表字段是F_ZYT
            "F_PDF": pdf_id,  # 记录生成的PDF文件ID
        }
        # 获取当前用户（这里简化处理，实际应从token中获取）
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
    bglx = "修改" if existing else "新增"
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
        "F_PDF": int(pdf_id) if pdf_id is not None else None,
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
            overview_resp = overview_by_gdid({"F_GDID": gdid})
            if overview_resp and overview_resp.get("code") == 1:
                old_detail_data = overview_resp.get("data")
                logger.info(f"[验证单质控] 成功获取旧详情数据用于备份，工单ID: {gdid}")
        except Exception as e:
            logger.warning(f"[验证单质控] 获取旧详情数据失败: {e}")
    else:
        # ✅ 新增操作：记录关键信息，无旧数据
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
    
    with adapter.connect() as conn:
        cur = conn.cursor()
        kspyrq = (gdxx.get("F_PYSJ") or None)[:10] if gdxx.get("F_PYSJ") else None
        pyts_int = _to_int_or_none(lqgzkjl.get("F_PYTS"))
        pdf_int = int(pdf_id) if pdf_id is not None else None
        changes_json = json.dumps(changes, ensure_ascii=False, default=str)
        # ✅ 序列化旧详情数据（稍后单独UPDATE以避免行长度超限）
        old_data_json = json.dumps(old_detail_data, ensure_ascii=False, default=str) if old_detail_data else None
        jlsj = datetime.now()
        
        if existing:
            # ✅ UPDATE语句（不包含F_OLD_DATA和F_ZKJD）
            # ✅ F_ZKJD是记录的关键标识之一，不应该被更新
            cur.execute(
                """UPDATE T_ZKSH SET 
                   F_SHZT = '待审核', F_DCID = ?, F_DC = ?, F_ZZID = ?, 
                   F_XM = ?, F_BRBH = ?, F_ZZBW = ?, F_YBLY = ?, F_KSPYRQ = ?, 
                   F_PYTS = ?, F_PYR = ?, F_PDF = ?,
                   F_BGLX = ?, F_CHANGES = ?, F_JLR = ?, F_JLSJ = ?
                   WHERE F_ID = ?""",
                (
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
                  F_ZKBH, F_ZKJD, F_SHZT, F_GDBH, F_DCID, F_DC, F_ZZID, F_XM, F_BRBH,
                  F_ZZBW, F_YBLY, F_KSPYRQ, F_PYTS, F_PYR, F_PDF,
                  F_BGLX, F_CHANGES, F_JLR, F_JLSJ
                ) VALUES (?, ?, '待审核', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    zkbh,
                    (payload.get("F_ZKJD") or "验证单"),
                    gdxx.get("F_GDBH"), gdxx.get("F_DCID"), gdxx.get("F_DC"), gdxx.get("F_ZZID"), gdxx.get("F_XM"), gdxx.get("F_BRBH"),
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

    # 返回结果
    result = {
        "GDXX": gdxx,
        "YBZKJL": ybzkjl,
        "LQGZKJL": lqgzkjl,
        "F_ZKSH_ID": zksh_id,
        "F_ZKBH": zkbh,
        "F_PDF_ID": pdf_id,
        "F_PDF_URL": pdf_url,
        "pygd_updated": pygd_affected,  # 返回受影响的行数
        "qc_audit_record": {
            "id": zksh_id,
            "type": bglx,
            "operator": operator,
            "stage": "验证单",
        }
    }
    
    # 如果PDF生成失败，添加警告信息
    if pdf_error:
        result["pdf_warning"] = f"质控记录已提交，但PDF生成失败: {pdf_error}"
        return ok(result, f"提交成功（PDF生成失败: {pdf_error}）")
    
    return ok(result, "提交成功")


def _generate_qc_pdf_async(rec_id: int, zkjd: str, gdbh: str, zzid: str):
    """
    后台异步生成质控 PDF
    
    Args:
        rec_id: 质控记录ID
        zkjd: 质控阶段
        gdbh: 工单编号
        zzid: 标本号
    """
    import logging
    from app.db.factory import get_adapter
    
    logger = logging.getLogger(__name__)
    adapter = get_adapter()
    
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
                try:
                    from app.crud.release_inspection_pdf import generate_release_inspection_pdf
                    # 获取工单ID
                    cur.execute("SELECT F_ID FROM T_PYGD WHERE F_GDBH = ?", (gdbh,))
                    gdid_row = cur.fetchone()
                    if gdid_row and gdid_row[0]:
                        gdid = gdid_row[0]
                        pdf_result = generate_release_inspection_pdf(gdid)
                        pdf_id = pdf_result.get("file_id")
                        logger.info(f"放行检测报告生成成功: 质控记录ID={rec_id}, PDF ID={pdf_id}")
                    else:
                        logger.warning(f"放行检测报告生成失败: 未找到工单ID, 工单编号={gdbh}")
                        return
                except Exception as e:
                    logger.error(f"放行检测报告生成失败: 质控记录ID={rec_id}, 错误={e}", exc_info=True)
                    return
            
            elif zkjd == "药物试验报告":
                # TODO: 实现药物试验报告PDF生成逻辑
                # 1. 查询实验计划和实验记录数据
                # 2. 渲染PDF模板
                # 3. 保存PDF文件并更新 T_ZKSH.F_PDF
                logger.info(f"[药物试验报告PDF] 触发PDF生成任务（功能待实现）：rec_id={rec_id}, gdbh={gdbh}")
                # 暂不生成PDF，直接返回
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
         - 验证单：放行检测报告
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
        cur.execute(f"SELECT F_ID, F_GDBH, F_ZKJD, F_ZZID, F_ZKBH FROM T_ZKSH WHERE {where_sql}", (query_param,))
        row = cur.fetchone()
        if not row:
            return err("未找到质控记录")
        
        actual_rec_id = row[0]  # 实际的记录ID（用于后台任务）
        gdbh = row[1]
        zkjd = row[2]  # 质控阶段
        zzid = row[3]  # 标本号
        zkbh_value = row[4]  # 质控编号
        
        # 2. 更新 T_ZKSH 审核状态
        sets = ["F_SHZT = ?"]
        params: List[Any] = [shzt]
        if shbz is not None:
            sets.append("F_SHBZ = ?")
            params.append(shbz)
        params.append(query_param)
        cur.execute(f"UPDATE T_ZKSH SET {', '.join(sets)} WHERE {where_sql}", tuple(params))
        affected = cur.rowcount
        
        # 3. 如果审核通过，添加后台任务生成PDF
        if shzt == "审核通过" and zkjd in ["登记接收", "预处理", "验证单", "药物试验报告"]:
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
        if gdbh and zkjd == "验证单":
            cur.execute("UPDATE T_PYGD SET F_ZKZT = ? WHERE F_GDBH = ?", (shzt, gdbh))
            pygd_affected = cur.rowcount
        
        conn.commit()
    
    if affected <= 0:
        return err("未找到记录或未更新")
    
    # 构建返回结果
    result = {
        "affected": affected, 
        "pygd_affected": pygd_affected,
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
        if zkjd == "登记接收":
            msg += "，样本接收质控PDF正在后台生成"
        elif zkjd == "预处理":
            msg += "，预处理评分PDF正在后台生成"
        elif zkjd == "验证单":
            msg += "，放行检测报告正在后台生成"
        elif zkjd == "药物试验报告":
            msg += "，药物试验报告PDF正在后台生成（功能待实现）"
    
    return ok(result, msg)


@router.post("/detail", response_model=ApiResponse[dict], response_model_exclude_none=True,
             dependencies=[Depends(require_roles(["qc_review"]))])
def get_qc_detail(payload: Dict[str, Any]):
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
                SELECT F_ID, F_CHANGES, F_OLD_DATA
                FROM T_ZKSH 
                WHERE F_ID = ?
            """
            cur.execute(sql, (zksh_id,))
            row = cur.fetchone()
            if not row:
                return err(f"未找到质控审核记录 (F_ID={zksh_id})")
            
            cols = [c[0] for c in cur.description]
            zksh = dict(zip(cols, row))
            
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
            
            return ok(zksh, "查询成功")
            
    except Exception as e:
        import traceback
        return err(f"查询质控审核记录失败: {str(e)}\n{traceback.format_exc()}")


@router.post("/list", response_model=ApiResponse[dict], response_model_exclude_none=False,
             dependencies=[Depends(require_roles(["qc_review"]))])
def list_qc(payload: Dict[str, Any]):
    """质控审核列表，支持过滤：质控编号、培养工单编号、审核状态、标本号、代次ID、组织部位。

    入参可选：F_ZKBH, F_GDBH, F_SHZT, F_ZZID, F_DCID, F_ZZBW, skip, limit
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
    like("z.F_ZKBH", payload.get("F_ZKBH"))
    like("z.F_GDBH", payload.get("F_GDBH"))
    like("z.F_ZZID", payload.get("F_ZZID"))
    like("z.F_DCID", payload.get("F_DCID"))
    like("z.F_ZZBW", payload.get("F_ZZBW"))

    # 审核状态精确匹配（若传）
    if payload.get("F_SHZT") is not None and str(payload.get("F_SHZT")).strip() != "":
        where.append("z.F_SHZT = ?")
        params.append(payload.get("F_SHZT"))

    where_sql = (" WHERE " + " AND ".join(where)) if where else ""

    base_core = f"""
SELECT z.F_ID, z.F_ZKBH, z.F_ZKJD, z.F_SHZT, z.F_GDBH, z.F_DCID, z.F_DC, z.F_ZZID, z.F_XM, z.F_BRBH,
       z.F_ZZBW, z.F_YBLY, z.F_KSPYRQ, z.F_PYTS, z.F_PYR, z.F_PDF, p.F_GDZT
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
def get_culture_detail_report_data(payload: Dict[str, Any]):
    """
    获取培养工单详情报告数据接口（JSON格式）
    
    功能：
    - 提供完整的嵌套JSON数据结构
    - 供前端预览或PDF生成使用
    - 包含所有必要的数据字段（工单信息、样本信息、预处理信息、培养详情、观察记录等）
    
    前置条件:
    - T_PYGD.F_ZKZT 为 "待审核"、"审核通过" 或 "审核不通过"
    
    Args:
        payload: {"F_GDID": 工单ID}
        
    Returns:
        完整的报告数据（嵌套JSON结构）
    """
    from app.crud.culture_detail_report import assemble_culture_detail_report
    
    F_GDID = payload.get("F_GDID")
    if not F_GDID:
        return err("缺少必填参数: F_GDID")
    
    try:
        report_data = assemble_culture_detail_report(F_GDID)
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
    
    try:
        pdf_list = get_pdf_list_with_details(int(gdid))
        return ok(pdf_list, "查询成功")
    except Exception as e:
        import traceback
        return err(f"查询失败: {e}\n{traceback.format_exc()}")


# ==================== 样本接收质控PDF ====================

@router.post("/sample-receive/report-data", response_model=ApiResponse[dict], response_model_exclude_none=True,
             dependencies=[Depends(require_roles(["sample_receive"]))])
def get_sample_receive_report_data(payload: Dict[str, Any]):
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
def generate_sample_receive_pdf_api(payload: Dict[str, Any]):
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
def get_sample_receive_pdf_list(payload: Dict[str, Any]):
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
    
    try:
        pdf_list = get_pdf_list_with_details(F_ZZID)
        return ok(pdf_list, "查询成功")
    except Exception as e:
        import traceback
        return err(f"查询失败: {e}\n{traceback.format_exc()}")


# ==================== 样本评分表PDF ====================

@router.post("/preprocess-score/report-data", response_model=ApiResponse[dict], response_model_exclude_none=True,
             dependencies=[Depends(require_roles(["organoid_preprocess"]))])
def get_preprocess_score_report_data(payload: Dict[str, Any]):
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
def generate_preprocess_score_pdf_api(payload: Dict[str, Any]):
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
def get_preprocess_score_pdf_list(payload: Dict[str, Any]):
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
async def get_release_inspection_report_data_api(payload: Dict[str, Any] = Body(...)):
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
    
    try:
        data = assemble_release_inspection_report(int(F_GDID))
        return ok(data, "数据获取成功")
    except ValueError as ve:
        return err(str(ve))
    except Exception as e:
        import traceback
        return err(f"数据获取失败: {e}\n{traceback.format_exc()}")


@router.post("/release-inspection/generate-pdf")
async def generate_release_inspection_pdf_api(payload: Dict[str, Any] = Body(...)):
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
    
    try:
        result = generate_release_inspection_pdf(int(F_GDID))
        return ok(result, "PDF生成成功")
    except ValueError as ve:
        return err(str(ve))
    except Exception as e:
        import traceback
        return err(f"PDF生成失败: {e}\n{traceback.format_exc()}")


@router.post("/release-inspection/pdf-list")
async def get_release_inspection_pdf_list_api(payload: Dict[str, Any] = Body(...)):
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


