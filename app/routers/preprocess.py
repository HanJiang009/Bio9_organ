from fastapi import APIRouter, Depends, UploadFile, File, Form
from app.schemas.common import ApiResponse
from app.core.response import ok, err
from app.core.auth import require_roles, get_current_user_optional
from app.crud import preprocess as crud
from app.crud import organoid_culture_tissue as crud_order
from pydantic import BaseModel
from app.core.storage import build_upload_path
from app.crud.file_center import insert_file_record
import json

router = APIRouter(prefix="/preprocess", tags=["preprocess"])


@router.post("/save-and-score", response_model=ApiResponse[dict], response_model_exclude_none=True,
			 dependencies=[Depends(require_roles(["organoid_culture_tissue"]))])
async def save_and_score(
	DATA: str = Form(..., description="JSON：其余预处理字段，含F_GDID、template_code等"),
	F_YBYCL: UploadFile | None = File(None),
	F_YBYCL_ID: str | None = Form(default=None),
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

	# 处理 F_YBYCL 文件/ID
	file_id = None
	if F_YBYCL is not None:
		dst_path, rel = build_upload_path(F_YBYCL.filename)
		content = await F_YBYCL.read()
		dst_path.write_bytes(content)
		fid = insert_file_record(rel, F_YBYCL.filename)
		if fid is not None:
			file_id = int(fid)
	if F_YBYCL_ID is not None:
		try:
			file_id = int(F_YBYCL_ID)
		except Exception:
			return err("F_YBYCL_ID 非法")

	# 平铺结构入参，去除保留键
	reserved = {"F_GDID", "template_code", "F_ZZID", "F_YBYCL"}
	fields = {k: v for k, v in (payload or {}).items() if k not in reserved}
	if file_id is not None:
		fields["F_YBYCL"] = file_id

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
def detail_by_gdid(payload: DetailByGdIn):
	try:
		gdid = int(payload.F_GDID)
	except Exception:
		return err("缺少或非法的 F_GDID")
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
	F_YBYCL: UploadFile | None = File(None),
	F_YBYCL_ID: str | None = Form(default=None),
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

	# 处理 F_YBYCL 文件/ID
	file_id = None
	if F_YBYCL is not None:
		dst_path, rel = build_upload_path(F_YBYCL.filename)
		content = await F_YBYCL.read()
		dst_path.write_bytes(content)
		fid = insert_file_record(rel, F_YBYCL.filename)
		if fid is not None:
			file_id = int(fid)
	if F_YBYCL_ID is not None:
		try:
			file_id = int(F_YBYCL_ID)
		except Exception:
			return err("F_YBYCL_ID 非法")

	reserved = {"F_GDID", "template_code", "F_ZZID", "F_YBYCL"}
	fields = {k: v for k, v in (payload or {}).items() if k not in reserved}
	if file_id is not None:
		fields["F_YBYCL"] = file_id

	try:
		operator = (current_user.get("F_Ktype") or current_user.get("F_YSM")) if current_user else None
		res = crud.create_or_update_preprocess_by_gdid(gdid, None, fields, payload.get("template_code") or "S32421", operator)
		return ok(res, "更新成功")
	except Exception as e:
		return err(f"更新失败: {e}")
