from __future__ import annotations

from typing import Any, Dict, Optional
import json
from datetime import datetime
from app.db.factory import get_adapter
from app.core.scoring import calc_score_by_code, load_template, _rules_to_options
from app.crud.sample_receive import query_by_ids


def _now_str() -> str:
	return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _parse_dt_any(v: Any) -> Optional[datetime]:
	if v is None:
		return None
	if isinstance(v, datetime):
		return v
	if isinstance(v, str):
		s = v.strip()
		if not s:
			return None
		for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%Y/%m/%d %H:%M:%S", "%Y/%m/%d"):
			try:
				return datetime.strptime(s, fmt)
			except Exception:
				continue
	return None


def create_or_update_preprocess_by_gdid(
    gdid: int,
    zzid: Optional[str],
    fields: Dict[str, Any],
    template_code: str,
    operator: Optional[str] = None,
    from_pipeline_template: bool = False,
) -> Dict[str, Any]:
	adapter = get_adapter()
	
	# 提前查询工单信息（独立连接）
	order_info = {}
	if gdid is not None:
		try:
			with adapter.connect() as conn_order:
				cur_order = conn_order.cursor()
				cur_order.execute("SELECT F_ZZID, F_GDBH, F_DCID, F_DC, F_PYSJ, F_SQR, F_XM, F_BRBH, F_YBLY FROM T_PYGD WHERE F_ID = ?", (gdid,))
				row_order = cur_order.fetchone()
				if row_order:
					if not zzid:
						zzid = row_order[0]
					order_info = {
						"F_GDBH": row_order[1],
						"F_DCID": row_order[2],
						"F_DC": row_order[3],
						"F_PYSJ": row_order[4],
						"F_SQR": row_order[5],
						"F_XM": row_order[6],
						"F_BRBH": row_order[7],
						"F_YBLY": row_order[8]
					}
		except Exception as e:
			import logging
			logger = logging.getLogger(__name__)
			logger.error(f"查询工单信息失败: {e}", exc_info=True)
	
	if not zzid:
		raise ValueError("无法解析 F_ZZID")
	
	# 查询是否已存在记录并获取旧数据（独立连接）- 先查询以便判断是否需要重新计算评分
	existing_id = None
	is_new = True
	old_data = None
	
	try:
		with adapter.connect() as conn_check:
			cur_check = conn_check.cursor()
			cur_check.execute("SELECT F_ID FROM T_YCL WHERE F_GDID = ?", (gdid,))
			row_check = cur_check.fetchone()
			
			if row_check and row_check[0] is not None:
				existing_id = int(row_check[0])
				is_new = False
				
				# 获取旧数据用于变更对比
				cur_check.execute("""
					SELECT F_ZZID, F_GDID, F_LTSJ, F_KSCLSJ, F_QXYCF, F_ZZSPCC, 
					       F_YBYCLQK, F_YBZLTJ, F_SFDC, F_DCYCF, F_YBYCL, 
					       F_ZLXBZB, F_XBHL, F_YBPFB
					FROM T_YCL WHERE F_ID = ?
				""", (existing_id,))
				old_row = cur_check.fetchone()
				if old_row:
					old_data = {
						"F_ZZID": old_row[0],
						"F_GDID": old_row[1],
						"F_LTSJ": old_row[2],
						"F_KSCLSJ": old_row[3],
						"F_QXYCF": old_row[4],
						"F_ZZSPCC": old_row[5],
						"F_YBYCLQK": old_row[6],
						"F_YBZLTJ": old_row[7],
						"F_SFDC": old_row[8],
						"F_DCYCF": old_row[9],
						"F_YBYCL": old_row[10],
						"F_ZLXBZB": old_row[11],
						"F_XBHL": old_row[12],
						"F_YBPFB": old_row[13]
					}
	except Exception as e:
		import logging
		logger = logging.getLogger(__name__)
		logger.error(f"查询旧数据失败: {e}", exc_info=True)
	
	# ✅ 智能评分：只在评分相关字段变化时才重新计算，否则复用旧评分（避免时间戳变化导致无意义的"变更"）
	score = None  # dict格式，用于访问字段
	score_str = None  # 字符串格式，用于数据库存储（复用时保持原格式）
	need_rescore = True  # 默认需要重新计算
	
	if not is_new and old_data and old_data.get("F_YBPFB"):
		# 更新操作：检查影响评分的字段是否有变化
		# 评分相关字段：F_YBZLTJ(样本质量), F_ZLXBZB(肿瘤细胞占比), F_XBHL(细胞活力), F_KSCLSJ(开始处理时间-影响离体时间评分)
		# 注意：病灶特征、取样方式在BBXX中，不在此接口修改；F_LTSJ从历史数据读取，不检查变化
		scoring_fields = ["F_YBZLTJ", "F_ZLXBZB", "F_XBHL", "F_KSCLSJ"]
		
		# 检查是否有评分相关字段被修改
		has_scoring_change = False
		for field in scoring_fields:
			if field in fields:  # 只检查本次传入的字段
				old_val = str(old_data.get(field) or "")
				new_val = str(fields.get(field) or "")
				if old_val != new_val:
					has_scoring_change = True
					break
		
		if not has_scoring_change:
			# 无评分相关字段变化，复用旧评分
			try:
				# ✅ 保留原始字符串格式（避免重新序列化导致格式差异）+ 解析为dict供访问
				if isinstance(old_data["F_YBPFB"], str):
					score_str = old_data["F_YBPFB"]  # 原始字符串
					score = json.loads(score_str)  # 解析为dict
				else:
					score = old_data["F_YBPFB"]
					score_str = json.dumps(score, ensure_ascii=False, default=str)
				need_rescore = False
				import logging
				logger = logging.getLogger(__name__)
				logger.info(f"[预处理评分] 复用旧评分（评分相关字段未变化），工单ID: {gdid}")
			except Exception as parse_err:
				import logging
				logger = logging.getLogger(__name__)
				logger.warning(f"[预处理评分] 解析旧评分失败，将重新计算: {parse_err}")
				need_rescore = True
	
	# 读取历史数据（用于评分计算和离体时间补齐）
	base = None
	ltsj_from_history = None
	
	# 需要重新计算评分（新增 或 评分相关字段有变化 或 旧评分解析失败）
	if need_rescore:
		# 读取此前录入的数据供评分使用
		base = query_by_ids(zzid, None) or {"JZXX": {}, "BBXX": {}, "YBZJ": {}}
		# 平铺结构：评分上下文中的 YCL 部分仅用于评分时引用，但入参不再嵌套
		ycl = {**fields, "F_ZZID": zzid, "F_GDID": gdid}
		# 自动补齐离体时间：仅从历史读取（优先 YBZJ 再 BBXX），不需要入参
		ltsj_from_history = (base.get("YBZJ", {}) or {}).get("F_LTSJ") or (base.get("BBXX", {}) or {}).get("F_LTSJ")
		ycl["F_LTSJ"] = ltsj_from_history
		merged = {**base, "YCL": ycl}
		
		# 计算评分
		score = calc_score_by_code(template_code, merged)
		score["calculated_at"] = _now_str()
		if operator:
			score["operator"] = operator
		score_str = json.dumps(score, ensure_ascii=False, default=str)
		
		import logging
		logger = logging.getLogger(__name__)
		logger.info(f"[预处理评分] {'新增记录' if is_new else '评分字段变化'}，重新计算评分，工单ID: {gdid}, 总分: {score.get('total', 'N/A')}")
	else:
		# 复用评分时，从old_data或历史数据中获取离体时间
		if old_data and old_data.get("F_LTSJ"):
			ltsj_from_history = old_data.get("F_LTSJ")
		else:
			# 只有在old_data中没有时才读取历史数据
			base = query_by_ids(zzid, None) or {"JZXX": {}, "BBXX": {}, "YBZJ": {}}
			ltsj_from_history = (base.get("YBZJ", {}) or {}).get("F_LTSJ") or (base.get("BBXX", {}) or {}).get("F_LTSJ")
	
	with adapter.connect() as conn:
		cur = conn.cursor()
		
		# F_KSCLSJ：用户保存未传则取当前时间；流水线模板预填不得用创建时刻冒充开始处理时间
		ksclsj_val = fields.get("F_KSCLSJ")
		if isinstance(ksclsj_val, str) and ksclsj_val.strip() == "":
			ksclsj_val = None
		ksclsj_dt = _parse_dt_any(ksclsj_val)
		if ksclsj_dt is None and not from_pipeline_template:
			ksclsj_dt = datetime.now()

		# F_LTSJ：来自历史记录，统一为 datetime（无法解析则置空）
		ltsj_dt = _parse_dt_any(ltsj_from_history)

		ybycl_present = "F_YBYCL" in fields

		from app.utils.yhbc import yhbc_flag
		payload: Dict[str, Any] = {
			"F_ZZID": zzid,
			"F_GDID": gdid,
			"F_LTSJ": ltsj_dt,
			"F_QXYCF": fields.get("F_QXYCF"),
			"F_ZZSPCC": fields.get("F_ZZSPCC"),
			"F_YBYCLQK": fields.get("F_YBYCLQK"),
			"F_YBZLTJ": fields.get("F_YBZLTJ"),
			"F_SFDC": fields.get("F_SFDC"),
			"F_DCYCF": fields.get("F_DCYCF"),
			"F_CZR": operator,
			"F_CZSJ": None if from_pipeline_template else datetime.now(),
			# 模板预填=0；用户保存接口调用=1（更新时也会刷成 1）
			"F_YHBC": yhbc_flag(from_pipeline_template),
		}
		# 模板预填且未配置开始处理时间：不写入该列，避免 NOT NULL 列被显式 INSERT NULL
		if ksclsj_dt is not None or not from_pipeline_template:
			payload["F_KSCLSJ"] = ksclsj_dt
		# ✅ 只在评分表存在时才更新（使用score_str保持原始格式）
		if score_str is not None:
			payload["F_YBPFB"] = score_str

		# F_ZLXBZB、F_XBHL 这两个字段已迁移到培养明细中录入
		# 只有在请求中明确传了这两个字段时，才更新（避免覆盖培养明细中的值）
		if "F_ZLXBZB" in fields:
			payload["F_ZLXBZB"] = fields.get("F_ZLXBZB")
		if "F_XBHL" in fields:
			payload["F_XBHL"] = fields.get("F_XBHL")
		# F_YBYCL 现在存 JSON 数组字符串（或 None），路由层已处理好直接写入
		if ybycl_present:
			payload["F_YBYCL"] = fields.get("F_YBYCL") or None
		
		def _exec_with_fallback(cur, payload: dict, is_new: bool, gdid: int):
			"""执行 INSERT/UPDATE，若因新列不存在失败则去掉可选新列后降级重试。"""
			extra_cols = {"F_CZR", "F_CZSJ", "F_YHBC"}
			for attempt in range(2):
				try:
					if not is_new:
						sc = [k for k in payload.keys()]
						sets = ", ".join([f"{c} = ?" for c in sc])
						params = [payload[c] for c in sc] + [gdid]
						cur.execute(f"UPDATE T_YCL SET {sets} WHERE F_GDID = ?", tuple(params))
					else:
						c_list = list(payload.keys())
						ph = ", ".join(["?" for _ in c_list])
						cur.execute(
							f"INSERT INTO T_YCL ({', '.join(c_list)}) VALUES ({ph})",
							tuple(payload[c] for c in c_list),
						)
					return  # success
				except Exception:
					if attempt == 0:
						# 降级：移除新增列再重试
						payload = {k: v for k, v in payload.items() if k not in extra_cols}
					else:
						raise

		if not is_new:  # 已存在记录，执行更新
			_exec_with_fallback(cur, payload, False, gdid)
			cur.execute("SELECT F_ID FROM T_YCL WHERE F_GDID = ?", (gdid,))
			new_id = int(cur.fetchone()[0])
		else:  # 新记录，执行插入
			_exec_with_fallback(cur, payload, True, gdid)
			cur.execute("SELECT MAX(F_ID) FROM T_YCL WHERE F_GDID = ?", (gdid,))
			new_id = int(cur.fetchone()[0])

		conn.commit()
	
	# ========== 创建质控审核记录（在事务外执行） ==========
	import logging
	logger = logging.getLogger(__name__)
	
	qc_audit_id = None
	if zzid and operator:
		try:
			from app.crud.qc_audit import create_qc_audit_record, get_sample_basic_info, calculate_changes
			
			# 获取样本基本信息
			sample_info = get_sample_basic_info(zzid)
			
			# ✅ 获取完整的旧详情数据（用于F_OLD_DATA字段）
			# 修改前获取完整详情（包含工单信息和预处理信息）
			old_detail_data = None
			if not is_new:
				try:
					old_detail_data = fetch_preprocess_detail_by_gdid(gdid)
					logger.info(f"[预处理质控] 成功获取旧详情数据用于备份（包含工单信息），工单ID: {gdid}")
				except Exception as e:
					logger.warning(f"[预处理质控] 获取旧详情数据失败: {e}")
			
			# 准备变更信息
			if is_new:
				# 新增预处理记录 - 获取新增后的完整详情数据
				changes = {
					"操作": "新增预处理信息",
					"关键信息": {
						"预处理ID": new_id,
						"工单ID": gdid,
						"标本号": zzid,
						"评分模板": template_code,
						"总分": score.get("total_score") if score else None
					}
				}
				bglx = "新增"
				
				# ✅ 新增操作：获取新增后的完整详情数据（包含工单信息和预处理信息）
				try:
					old_detail_data = fetch_preprocess_detail_by_gdid(gdid)
					logger.info(f"[预处理质控] 新增操作：成功获取新详情数据用于记录（包含工单信息），工单ID: {gdid}")
				except Exception as e:
					logger.warning(f"[预处理质控] 新增操作：获取新详情数据失败: {e}")
					old_detail_data = None
			else:
				# 修改预处理记录 - 计算变更
				# ✅ 如果评分是复用的（未重新计算），从payload中排除F_YBPFB，避免因JSON格式差异被误判为"变更"
				payload_for_compare = payload.copy()
				if not need_rescore and "F_YBPFB" in payload_for_compare:
					# 评分未重新计算（复用旧评分），不应被计入变更
					del payload_for_compare["F_YBPFB"]
					logger.info(f"[预处理质控] 评分复用，从变更对比中排除F_YBPFB字段")
				
				changes = calculate_changes(old_data, payload_for_compare)
				if not changes.get("变更字段"):
					# 没有实际变更，不创建质控记录
					logger.info(f"[预处理质控] 预处理信息未变更，跳过质控记录创建 (工单ID: {gdid})")
					return {"F_ID": new_id, "F_GDID": gdid, "F_ZZID": zzid, "score": score}
				bglx = "修改"
			
			# ✅ 创建质控审核记录，包含F_OLD_DATA
			qc_audit_id = create_qc_audit_record(
				F_ZKJD="预处理",
				F_BGLX=bglx,
				F_ZZID=zzid,
				F_CHANGES=changes,
				F_JLR=operator or "系统",
				F_GDID=gdid,
				F_XM=sample_info.get("F_XM") or order_info.get("F_XM"),
				F_BRBH=sample_info.get("F_BRBH") or order_info.get("F_BRBH"),
				F_ZZBW=sample_info.get("F_ZZBW"),
				F_YBLY=order_info.get("F_YBLY"),  # 从工单信息中获取样本来源
				# 工单相关字段
				F_GDBH=order_info.get("F_GDBH"),
				F_DCID=order_info.get("F_DCID"),
				F_DC=order_info.get("F_DC"),
				F_PYSJ=order_info.get("F_PYSJ"),
				F_SQR=order_info.get("F_SQR"),
				F_OLD_DATA=old_detail_data,  # ✅ 传入完整旧详情数据
				from_pipeline_template=from_pipeline_template,
			)
			logger.info(f"[预处理质控] 创建质控审核记录成功，工单ID: {gdid}, 类型: {bglx}, 质控ID: {qc_audit_id}")
		except Exception as e:
			logger.error(f"[预处理质控] 创建质控审核记录失败 (工单ID: {gdid}): {e}", exc_info=True)
	else:
		logger.warning(f"[预处理质控] 跳过创建质控记录 - zzid: {zzid}, operator: '{operator}'")
	
	result = {"F_ID": new_id, "F_GDID": gdid, "F_ZZID": zzid, "score": score}
	if qc_audit_id:
		result["qc_audit_id"] = qc_audit_id
	
	return result


def fetch_ltsj_for_gdid(gdid: int) -> Optional[str]:
	"""独立获取离体时间（从样本制剂表）"""
	adapter = get_adapter()
	with adapter.connect() as conn:
		cur = conn.cursor()
		try:
			cur.execute("SELECT F_ZZID FROM T_PYGD WHERE F_ID = ?", (gdid,))
			row = cur.fetchone()
		except Exception:
			return None
		zzid = row[0] if row else None
		if not zzid:
			return None
		try:
			lookup_sql = "SELECT F_LTSJ FROM T_YBZJ WHERE F_ZZID = ? ORDER BY F_ID DESC"
			paginated_sql, extra = adapter.paginate(lookup_sql, 0, 1)
			cur.execute(paginated_sql, (zzid,) + tuple(extra))
			row2 = cur.fetchone()
		except Exception:
			return None
		if not row2:
			return None
		val = row2[0]
		if isinstance(val, datetime):
			return val.strftime("%Y-%m-%d %H:%M:%S")
		return str(val) if val is not None else None


def fetch_preprocess_detail_by_gdid(gdid: int) -> Dict[str, Any] | None:
	"""
	获取完整的预处理详情数据（包含工单信息和预处理信息）
	用于质控记录的F_OLD_DATA字段
	
	Returns:
		{
			"order": {...},  # 工单详情
			"preprocess": {...}  # 预处理详情
		}
	"""
	try:
		from app.crud.organoid_culture_tissue import get_culture_order_detail
		
		# 获取工单详情
		order = get_culture_order_detail(gdid)
		
		# 获取预处理详情
		prep = fetch_preprocess_by_gdid(gdid)
		
		# 如果两者都没有，返回None
		if not prep and not order:
			return None
		
		result = {
			"order": order or {},
			"preprocess": prep or {},
		}
		
		return result
	except Exception as e:
		import logging
		logger = logging.getLogger(__name__)
		logger.error(f"获取预处理完整详情失败 (工单ID: {gdid}): {e}", exc_info=True)
		return None


def fetch_preprocess_by_gdid(gdid: int) -> Dict[str, Any] | None:
	adapter = get_adapter()
	with adapter.connect() as conn:
		cur = conn.cursor()
		# 优先查询含操作人/时间/用户保存标记；若列不存在则逐级降级
		try:
			cur.execute(
				"""
				SELECT F_ID, F_ZZID, F_GDID, F_LTSJ, F_KSCLSJ, F_QXYCF, F_ZZSPCC,
				       F_YBYCL, F_YBYCLQK, F_YBZLTJ, F_ZLXBZB, F_XBHL, F_YBPFB,
				       F_SFDC, F_DCYCF, F_CZR, F_CZSJ, F_YHBC
				FROM T_YCL WHERE F_GDID = ?
				""",
				(gdid,),
			)
		except Exception:
			try:
				cur.execute(
					"""
					SELECT F_ID, F_ZZID, F_GDID, F_LTSJ, F_KSCLSJ, F_QXYCF, F_ZZSPCC,
					       F_YBYCL, F_YBYCLQK, F_YBZLTJ, F_ZLXBZB, F_XBHL, F_YBPFB,
					       F_SFDC, F_DCYCF, F_CZR, F_CZSJ
					FROM T_YCL WHERE F_GDID = ?
					""",
					(gdid,),
				)
			except Exception:
				cur.execute(
					"""
					SELECT F_ID, F_ZZID, F_GDID, F_LTSJ, F_KSCLSJ, F_QXYCF, F_ZZSPCC,
					       F_YBYCL, F_YBYCLQK, F_YBZLTJ, F_ZLXBZB, F_XBHL, F_YBPFB,
					       F_SFDC, F_DCYCF
					FROM T_YCL WHERE F_GDID = ?
					""",
					(gdid,),
				)
		row = cur.fetchone()
		if not row:
			return None
		cols = [c[0] for c in cur.description]
		d = dict(zip(cols, row))
		# 统一时间输出字符串
		for tk, fmt in (
			("F_LTSJ", "%Y-%m-%d %H:%M:%S"),
			("F_KSCLSJ", "%Y-%m-%d %H:%M:%S"),
			("F_CZSJ", "%Y-%m-%d %H:%M:%S"),
		):
			v = d.get(tk)
			if v is not None:
				try:
					d[tk] = v.strftime(fmt) if hasattr(v, "strftime") else str(v)
				except Exception:
					d[tk] = str(v)
		# 解析评分 JSON
		try:
			if isinstance(d.get("F_YBPFB"), (bytes, bytearray)):
				d["F_YBPFB"] = json.loads(d["F_YBPFB"].decode("utf-8", errors="ignore"))
			elif isinstance(d.get("F_YBPFB"), str):
				d["F_YBPFB"] = json.loads(d["F_YBPFB"])
		except Exception:
			pass
		# F_YBYCL_FILES：解析 JSON 数组，组装文件详情数组（兼容旧整数值）
		d["F_YBYCL_FILES"] = []
		try:
			from app.crud.file_center import get_file_by_id
			raw_ybycl = d.get("F_YBYCL")
			if raw_ybycl is not None:
				id_list: list = []
				if isinstance(raw_ybycl, str):
					try:
						parsed = json.loads(raw_ybycl)
						id_list = parsed if isinstance(parsed, list) else [int(parsed)]
					except Exception:
						try:
							id_list = [int(raw_ybycl)]
						except Exception:
							pass
				elif isinstance(raw_ybycl, int):
					id_list = [raw_ybycl]
				elif isinstance(raw_ybycl, list):
					id_list = raw_ybycl

				files = []
				for fid in id_list:
					try:
						rec = get_file_by_id(int(fid))
						if rec:
							files.append({
								"id": int(fid),
								"path": rec.get("F_PATH"),
								"name": rec.get("F_ORIGINAL_NAME"),
							})
					except Exception:
						pass
				d["F_YBYCL_FILES"] = files
		except Exception:
			pass
		return d


def fetch_score_template(template_code: str) -> Dict[str, Any]:
	tpl = load_template(template_code)
	return {
		"template_code": tpl.get("template_code"),
		"name": tpl.get("name"),
		"version": tpl.get("version"),
		"items": [
			{
				"key": item.get("key"),
				"label": item.get("label") or item.get("key"),
				"type": item.get("type"),
				"options": _rules_to_options(item.get("type"), item.get("rules", [])),
			}
			for item in tpl.get("items", [])
		],
	}
