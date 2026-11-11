from fastapi import APIRouter, Depends, UploadFile, File, Form
from app.schemas.common import ApiResponse
from app.core.response import ok, err
from app.core.auth import require_roles, get_current_user_optional
from app.crud import preprocess as crud
from app.crud import organoid_culture_tissue as crud_order
from pydantic import BaseModel
from app.core.storage import build_upload_path
from app.crud.file_center import insert_file_record
from typing import List
import json

router = APIRouter(prefix="/preprocess", tags=["preprocess"])


async def _resolve_ybycl_ids(
    gdid: int,
    upload_files: List[UploadFile],
    ybycl_ids_str: str | None,
    ybycl_ids_del_str: str | None,
) -> str | None:
    """处理预处理图片多附件逻辑，返回最终 JSON 数组字符串（或 None）。

    流程：
    1. 从 DB 读取当前 F_YBYCL（兼容旧整数/新 JSON 数组）
    2. 若传入 F_YBYCL_IDS，以该值替换当前列表
    3. 从列表中删除 F_YBYCL_IDS_DEL 中的 ID
    4. 上传新文件，追加 ID
    5. 返回最终 JSON 字符串（为空则返回 None）
    """
    from app.db.factory import get_adapter
    adapter = get_adapter()

    # 读取 DB 旧值
    old_ids: list = []
    try:
        with adapter.connect() as conn:
            cur = conn.cursor()
            cur.execute("SELECT F_YBYCL FROM T_YCL WHERE F_GDID = ?", (gdid,))
            row = cur.fetchone()
            if row and row[0] is not None:
                raw = row[0]
                if isinstance(raw, str):
                    try:
                        parsed = json.loads(raw)
                        old_ids = parsed if isinstance(parsed, list) else [int(parsed)]
                    except Exception:
                        try:
                            old_ids = [int(raw)]
                        except Exception:
                            pass
                elif isinstance(raw, int):
                    old_ids = [raw]
    except Exception:
        pass

    # 用户传入的保留 ID 列表（优先于旧值）
    if ybycl_ids_str is not None:
        try:
            parsed = json.loads(ybycl_ids_str)
            existing_ids = [int(x) for x in (parsed if isinstance(parsed, list) else [parsed])]
        except Exception:
            existing_ids = list(old_ids)
    else:
        existing_ids = list(old_ids)

    # 删除指定 ID
    if ybycl_ids_del_str:
        try:
            del_ids = {int(x) for x in json.loads(ybycl_ids_del_str)}
            existing_ids = [x for x in existing_ids if x not in del_ids]
        except Exception:
            pass

    # 上传新文件
    for uf in (upload_files or []):
        if uf and uf.filename:
            dst_path, rel = build_upload_path(uf.filename)
            content = await uf.read()
            dst_path.write_bytes(content)
            fid = insert_file_record(rel, uf.filename)
            if fid is not None:
                existing_ids.append(int(fid))

    if existing_ids:
        return json.dumps(existing_ids, ensure_ascii=False)
    return None


@router.post("/save-and-score", response_model=ApiResponse[dict], response_model_exclude_none=True,
			 dependencies=[Depends(require_roles(["organoid_culture_tissue"]))])
async def save_and_score(
	DATA: str = Form(..., description="JSON：其余预处理字段，含F_GDID、template_code等"),
	F_YBYCL: List[UploadFile] = File(default=[]),
	F_YBYCL_IDS: str | None = Form(default=None, description="保留的图片文件ID数组JSON字符串"),
	F_YBYCL_IDS_DEL: str | None = Form(default=None, description="需要删除的图片文件ID数组JSON字符串"),
	current_user: dict = Depends(get_current_user_optional),
):
	# 解析 DATA
	try:
		payload = json.loads(DATA) if DATA else {}
	except Exception:
		return err("DATA 解析失败")
	try:
		gdid = int(payload.get("F_GDID"))
	except Exception:
		return err("缺少或非法的 F_GDID")
	template_code = payload.get("template_code") or "S32421"

	# 处理多附件
	ybycl_json = await _resolve_ybycl_ids(gdid, F_YBYCL, F_YBYCL_IDS, F_YBYCL_IDS_DEL)

	# 平铺结构入参，去除保留键
	reserved = {"F_GDID", "template_code", "F_ZZID", "F_YBYCL"}
	fields = {k: v for k, v in (payload or {}).items() if k not in reserved}
	fields["F_YBYCL"] = ybycl_json  # 始终写入（None 表示清空）

	try:
		operator = (current_user.get("F_Ktype") or current_user.get("F_YSM")) if current_user else None
		res = crud.create_or_update_preprocess_by_gdid(gdid, None, fields, template_code, operator)
		return ok(res, "保存并评分成功")
	except ValueError as ve:
		return err(str(ve))
	except Exception as e:
		return err(f"保存失败: {e}")


@router.post("/get-by-gdid", response_model=ApiResponse[dict], response_model_exclude_none=True,
             dependencies=[Depends(require_roles(["organoid_culture_tissue"]))])
def get_by_gdid(payload: dict):
	try:
		gdid = int(payload.get("F_GDID"))
	except Exception:
		return err("缺少或非法的 F_GDID")
	rec = crud.fetch_preprocess_by_gdid(gdid)
	if not rec:
		return err("未找到记录")
	return ok(rec, "查询成功")


class DetailByGdIn(BaseModel):
	F_GDID: int


@router.post("/detail-by-gdid", response_model=ApiResponse[dict], response_model_exclude_none=True,
			 dependencies=[Depends(require_roles(["organoid_culture_tissue"]))])
def detail_by_gdid(payload: DetailByGdIn, current_user: dict = Depends(get_current_user_optional)):
	try:
		gdid = int(payload.F_GDID)
	except Exception:
		return err("缺少或非法的 F_GDID")
	from app.core.data_scope import deny_pygd_access
	denied = deny_pygd_access(current_user, gdid, module="organoid_culture_tissue")
	if denied:
		return denied
	order = crud_order.get_culture_order_detail(gdid) if hasattr(crud_order, "get_culture_order_detail") else None
	prep = crud.fetch_preprocess_by_gdid(gdid)
	if not prep and not order:
		return err("未找到记录")
	resp = {
		"order": order or {},
		"preprocess": prep or {},
	}
	# 获取离体时间：优先使用预处理记录中的值，如果没有或为空则从样本制剂表查询
	if not (resp.get("preprocess") and resp["preprocess"].get("F_LTSJ")):
		ltsj = crud.fetch_ltsj_for_gdid(gdid)
		if ltsj:
			resp.setdefault("preprocess", {})["F_LTSJ"] = ltsj
	if not (resp.get("preprocess") and resp["preprocess"].get("F_YBPFB")):
		template_code = "S32421"
		try:
			template_code = resp.get("order", {}).get("F_PFBMBH") or template_code
		except Exception:
			template_code = "S32421"
		tpl = crud.fetch_score_template(template_code)
		resp.setdefault("preprocess", {})["F_YBPFB"] = {
			"template_code": tpl.get("template_code"),
			"name": tpl.get("name"),
			"version": tpl.get("version"),
			"total": None,
			"details": [
				{**item, "value": None, "score": None}
				for item in (tpl.get("items") or [])
			],
		}
	return ok(resp, "查询成功")


@router.post("/update", response_model=ApiResponse[dict], response_model_exclude_none=True,
			 dependencies=[Depends(require_roles(["organoid_culture_tissue"]))])
async def update(
	DATA: str = Form(..., description="JSON：其余预处理字段，含F_GDID、template_code等"),
	F_YBYCL: List[UploadFile] = File(default=[]),
	F_YBYCL_IDS: str | None = Form(default=None, description="保留的图片文件ID数组JSON字符串"),
	F_YBYCL_IDS_DEL: str | None = Form(default=None, description="需要删除的图片文件ID数组JSON字符串"),
	current_user: dict = Depends(get_current_user_optional),
):
	try:
		payload = json.loads(DATA) if DATA else {}
	except Exception:
		return err("DATA 解析失败")
	try:
		gdid = int(payload.get("F_GDID"))
	except Exception:
		return err("缺少或非法的 F_GDID")

	# 处理多附件
	ybycl_json = await _resolve_ybycl_ids(gdid, F_YBYCL, F_YBYCL_IDS, F_YBYCL_IDS_DEL)

	reserved = {"F_GDID", "template_code", "F_ZZID", "F_YBYCL"}
	fields = {k: v for k, v in (payload or {}).items() if k not in reserved}
	fields["F_YBYCL"] = ybycl_json  # 始终写入（None 表示清空）

	try:
		operator = (current_user.get("F_Ktype") or current_user.get("F_YSM")) if current_user else None
		res = crud.create_or_update_preprocess_by_gdid(gdid, None, fields, payload.get("template_code") or "S32421", operator)
		return ok(res, "更新成功")
	except Exception as e:
		return err(f"更新失败: {e}")
