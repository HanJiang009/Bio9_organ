"""
质控审核记录模块

用于记录各个流程的质控审核信息，支持变更追踪和审计功能
"""
from typing import Dict, Any, List, Optional
from datetime import datetime
import json
import logging
from app.db.factory import get_adapter

logger = logging.getLogger(__name__)

# 流水线模板初始化产生的质控记录在 F_ZKJD 末尾追加此后缀
TEMPLATE_ZKJD_SUFFIX = "（模板）"


def template_zkjd(base: str) -> str:
    """为质控阶段名称追加流水线模板后缀。"""
    base = (base or "").strip()
    if not base or base.endswith(TEMPLATE_ZKJD_SUFFIX):
        return base
    return f"{base}{TEMPLATE_ZKJD_SUFFIX}"


def normalize_zkjd(zkjd: str) -> str:
    """去掉流水线模板后缀，得到标准质控阶段名。"""
    zkjd = (zkjd or "").strip()
    if zkjd.endswith(TEMPLATE_ZKJD_SUFFIX):
        return zkjd[: -len(TEMPLATE_ZKJD_SUFFIX)]
    return zkjd


def zkjd_matches(actual: str, expected: str) -> bool:
    """比较两个质控阶段是否相同（忽略模板后缀）。"""
    return normalize_zkjd(actual) == normalize_zkjd(expected)


def zkjd_variants(base: str) -> tuple[str, str]:
    """返回 (标准名, 模板名) 供 SQL IN 查询使用。"""
    b = normalize_zkjd(base)
    return b, template_zkjd(b)


# 流水线模板初始化占位记录，不参与人工审核排队
_PIPELINE_INIT_CHANGES_MARKER = "流水线初始化"


def resolve_qc_review_gdid(
    cur,
    gdid: Optional[int],
    gdbh: Optional[str],
) -> Optional[int]:
    """
    审核顺序校验用工单 ID：优先 F_GDID，缺失时由 F_GDBH 反查 T_PYGD.F_ID。
    """
    if gdid is not None:
        try:
            return int(gdid)
        except (TypeError, ValueError):
            pass
    gdbh_val = (gdbh or "").strip()
    if gdbh_val:
        cur.execute(
            "SELECT F_ID FROM T_PYGD WHERE F_GDBH = ? ORDER BY F_ID DESC",
            (gdbh_val,),
        )
        row = cur.fetchone()
        if row and row[0] is not None:
            return int(row[0])
    return None


def find_earlier_pending_qc_audits(
    cur,
    *,
    rec_id: int,
    zkjd: str,
    gdid: Optional[int],
    zzid: Optional[str],
    jlsj: Any = None,
    exclude_ids: Optional[List[int]] = None,
) -> List[Dict[str, Any]]:
    """
    查找同一工单/标本、同一质控阶段下，登记时间更早且仍为「待审核」的记录。
    阶段匹配包含流水线模板后缀变体（如「培养（模板）」）。

    范围判定（互斥，非 OR）：
    - 有 F_GDID 时仅按工单 ID 匹配（同标本可产生多工单，有工单则不看标本号）
    - 无 F_GDID 时（如标本接收等尚未建工单阶段）才按 F_ZZID 匹配

    调用方应通过 check_qc_review_order 传入已解析的 gdid（含 F_GDBH 反查结果）。
    排除 F_CHANGES 含「流水线初始化」的占位记录（非用户真实提交）。
    """
    std, tpl = zkjd_variants(zkjd)
    scope_sql: Optional[str] = None
    scope_param: Any = None

    if gdid is not None:
        scope_sql = "F_GDID = ?"
        scope_param = gdid
    else:
        zzid_val = (zzid or "").strip()
        if zzid_val:
            scope_sql = "F_ZZID = ?"
            scope_param = zzid_val

    if not scope_sql:
        return []

    # LIKE 模式必须走绑定参数：字面量 '%...%' 在 MySQL/PyMySQL 下会触发 mogrify 格式化错误
    pipeline_like = f"%{_PIPELINE_INIT_CHANGES_MARKER}%"
    conditions = [
        "F_ID != ?",
        "F_SHZT = '待审核'",
        "F_ZKJD IN (?, ?)",
        scope_sql,
        "(F_CHANGES IS NULL OR F_CHANGES NOT LIKE ?)",
    ]
    params = [rec_id, std, tpl, scope_param, pipeline_like]

    if jlsj is not None:
        conditions.append("(F_JLSJ < ? OR (F_JLSJ = ? AND F_ID < ?))")
        params.extend([jlsj, jlsj, rec_id])
    else:
        conditions.append("F_ID < ?")
        params.append(rec_id)

    exclude = [int(x) for x in (exclude_ids or []) if x is not None and int(x) != rec_id]
    if exclude:
        ph = ", ".join(["?"] * len(exclude))
        conditions.append(f"F_ID NOT IN ({ph})")
        params.extend(exclude)

    sql = f"""
        SELECT F_ID, F_ZKBH, F_ZKJD, F_JLSJ
        FROM T_ZKSH
        WHERE {' AND '.join(conditions)}
        ORDER BY F_JLSJ ASC, F_ID ASC
    """
    cur.execute(sql, tuple(params))
    cols = [c[0] for c in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]


def build_qc_review_order_error(pending: List[Dict[str, Any]]) -> str:
    """构造「须先审核更早记录」的提示文案。"""
    if not pending:
        return ""
    labels: List[str] = []
    for item in pending[:5]:
        zkbh = item.get("F_ZKBH") or item.get("F_ID")
        stage = item.get("F_ZKJD") or ""
        labels.append(f"{zkbh}（{stage}）" if stage else str(zkbh))
    stage_name = normalize_zkjd(pending[0].get("F_ZKJD") or "")
    head = f"同一阶段「{stage_name}」存在更早的待审核记录，请先审核以下记录后再处理本条"
    if len(pending) > 5:
        return f"{head}：{', '.join(labels)} 等共 {len(pending)} 条"
    return f"{head}：{', '.join(labels)}"


def check_qc_review_order(
    cur,
    *,
    rec_id: int,
    zkjd: str,
    gdid: Optional[int],
    zzid: Optional[str],
    gdbh: Optional[str] = None,
    jlsj: Any = None,
    exclude_ids: Optional[List[int]] = None,
) -> Optional[str]:
    """
    审核前顺序校验：若存在更早的同阶段待审核记录则返回错误提示，否则返回 None。
    范围：有工单 ID（含由 F_GDBH 反查）时仅同工单；否则仅同标本号。
    """
    resolved_gdid = resolve_qc_review_gdid(cur, gdid, gdbh)
    pending = find_earlier_pending_qc_audits(
        cur,
        rec_id=rec_id,
        zkjd=zkjd,
        gdid=resolved_gdid,
        zzid=zzid,
        jlsj=jlsj,
        exclude_ids=exclude_ids,
    )
    if not pending:
        return None
    return build_qc_review_order_error(pending)


def _generate_zkbh() -> str:
    """生成质控编号（带毫秒和随机后缀，确保同一秒内不重复）"""
    import random
    now = datetime.now()
    timestamp = now.strftime('%Y%m%d%H%M%S')
    milliseconds = now.microsecond // 1000  # 微秒转毫秒
    random_suffix = random.randint(10, 99)  # 2位随机数
    return f"ZKSH{timestamp}{milliseconds:03d}{random_suffix}"


# 病理/测序/其他附件：库存 {"bz", "ids"}，展示 {"bz", "files": [{id, path, name}]}
ATTACHMENT_BZ_FILES_FIELDS = frozenset({"F_BL", "F_CX", "F_QT"})


def _normalize_upload_file_path(path: str) -> str:
    import os
    path = path or ""
    if "\\" in path or ":" in path:
        if "uploads" in path:
            parts = path.split("uploads")
            if len(parts) > 1:
                path = parts[-1].lstrip("\\/")
        else:
            path = os.path.basename(path)
    path = path.lstrip("/\\")
    if path.startswith("uploads/") or path.startswith("uploads\\"):
        path = path[8:]
    return path.replace("\\", "/")


def _attachment_file_from_id(file_id) -> Dict[str, Any]:
    if file_id is None:
        return {"id": 0, "path": "", "name": ""}
    try:
        from app.crud.file_center import get_file_by_id
        file_id_int = int(file_id)
        file_info = get_file_by_id(file_id_int)
        if file_info:
            return {
                "id": file_id_int,
                "path": _normalize_upload_file_path(file_info.get("F_PATH", "")),
                "name": file_info.get("F_ORIGINAL_NAME") or "",
            }
        return {"id": file_id_int, "path": "", "name": ""}
    except Exception:
        try:
            return {"id": int(file_id), "path": "", "name": ""}
        except Exception:
            return {"id": 0, "path": "", "name": ""}


def enhance_attachment_bz_files(value: Any) -> Any:
    """将 {bz, ids} / {bz, files} 统一增强为 {bz, files:[{id, path, name}]}。"""
    if value is None or value == "":
        return {"bz": "", "files": []}
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except Exception:
            return value
    if not isinstance(value, dict):
        return value
    bz = value.get("bz") or ""
    files = value.get("files")
    if isinstance(files, list):
        enhanced = []
        for item in files:
            if isinstance(item, dict):
                fid = item.get("id") or item.get("F_ID")
                if fid is not None and item.get("path") and item.get("name"):
                    enhanced.append({
                        "id": int(fid),
                        "path": item.get("path") or "",
                        "name": item.get("name") or "",
                    })
                elif fid is not None:
                    enhanced.append(_attachment_file_from_id(fid))
                else:
                    enhanced.append(item)
            elif isinstance(item, (int, str)):
                enhanced.append(_attachment_file_from_id(item))
            else:
                enhanced.append(item)
        return {"bz": bz, "files": enhanced}
    ids = value.get("ids")
    if isinstance(ids, list):
        return {
            "bz": bz,
            "files": [_attachment_file_from_id(i) for i in ids if i is not None],
        }
    return {"bz": bz, "files": []}


def _as_change_value(v: Any) -> Any:
    if v is None:
        return ""
    if isinstance(v, (dict, list)):
        return v
    return str(v)


def _normalize_change_item(item: Dict[str, Any]) -> Dict[str, Any]:
    """统一变更项为 field / old_value / new_value，兼容历史中文键。"""
    field = item.get("field")
    if field is None:
        field = item.get("字段")
    old_value = item.get("old_value")
    if old_value is None:
        old_value = item.get("旧值")
    new_value = item.get("new_value")
    if new_value is None:
        new_value = item.get("新值")
    new_item = {
        k: v for k, v in item.items()
        if k not in ("字段", "旧值", "新值", "field", "old_value", "new_value")
    }
    new_item["field"] = "" if field is None else str(field)
    new_item["old_value"] = _as_change_value(old_value)
    new_item["new_value"] = _as_change_value(new_value)
    return new_item


def enrich_qc_changes(changes: Any) -> Any:
    """质控详情展示：补全病理/测序/其他附件变更项中的文件信息。

    仅 enrich「变更字段」数组内的附件值，保留「操作」「关键信息」等其它键，
    供前端区分同阶段（如观察）下的不同弹窗类型。
    历史记录若仍用「字段/旧值/新值」，在此归一为 field/old_value/new_value。
    """
    if not isinstance(changes, dict):
        return changes
    items = changes.get("变更字段")
    if not isinstance(items, list):
        return changes
    enriched_items = []
    for item in items:
        if not isinstance(item, dict):
            enriched_items.append(item)
            continue
        new_item = _normalize_change_item(item)
        field = new_item.get("field")
        if field in ATTACHMENT_BZ_FILES_FIELDS:
            new_item["old_value"] = enhance_attachment_bz_files(new_item.get("old_value"))
            new_item["new_value"] = enhance_attachment_bz_files(new_item.get("new_value"))
        enriched_items.append(new_item)
    result = dict(changes)
    result["变更字段"] = enriched_items
    return result


def enrich_qc_old_data(old_data: Any) -> Any:
    """质控详情展示：补全 F_OLD_DATA 中附件字段的文件信息。"""
    if not isinstance(old_data, dict):
        return old_data
    result = dict(old_data)
    for field in ATTACHMENT_BZ_FILES_FIELDS:
        if field in result:
            result[field] = enhance_attachment_bz_files(result[field])
    # 动物实验观察：历史快照中 F_ZB 为空时回填组别名，避免前端反显 undefined
    try:
        from app.crud.animal_test_record import enrich_animal_obs_f_zb_in_payload
        result = enrich_animal_obs_f_zb_in_payload(result)
    except Exception:
        logger.warning("[质控详情] 补全观察记录 F_ZB 失败", exc_info=True)
    return result


def calculate_changes(
    old_data: Optional[Dict[str, Any]], 
    new_data: Dict[str, Any], 
    field_mapping: Optional[Dict[str, str]] = None
) -> Dict[str, Any]:
    """
    计算数据变更差异
    
    Args:
        old_data: 原始数据（修改时提供，新增时为None）
        new_data: 新数据
        field_mapping: 字段映射（字段名->中文名），可选
    
    Returns:
        变更差异JSON
    """
    changes = []
    
    # 新增操作：只记录关键信息
    if old_data is None:
        return {
            "操作": "新增",
            "关键信息": new_data
        }
    
    # ✅ 定义需要简化的复杂JSON字段（避免记录完整内容，防止数据膨胀）
    COMPLEX_JSON_FIELDS = {"F_YBPFB", "F_JZJ", "F_XHMSY"}  # 评分表、基质胶明细、消化酶使用等
    
    # ✅ 定义单个文件ID字段（需要将文件ID转换为静态地址）
    SINGLE_FILE_ID_FIELDS = {
        "F_IMG_D1M4", "F_IMG_D1M20",  # 预处理：第1天图片
        "F_IMG_D3M4", "F_IMG_D3M20",  # 预处理：第3天图片
        "F_IMG_D7M4", "F_IMG_D7M20",  # 预处理：第7天图片
    }

    # ✅ 定义文件ID数组字段（存储为 JSON 数组字符串，如 "[101, 102]"）
    FILE_ID_ARRAY_FIELDS = {
        "F_FJ",        # 培养工单附件数组
        "F_ZQTYS_ID",  # 登记接收：知情同意书ID数组（JSON数组）
        "F_SYTP",      # 登记接收：收样图片（已改为JSON数组）
        "F_YBYCL",     # 预处理：样本原材料图片（已改为JSON数组）
    }
    
    def _convert_file_id_to_url(file_id):
        """将文件ID转换为静态地址（相对路径）"""
        if file_id is None or file_id == "" or file_id == "null":
            return ""
        try:
            from app.crud.file_center import get_file_by_id
            import os
            
            file_id_int = int(file_id)
            file_info = get_file_by_id(file_id_int)
            if file_info:
                # 获取F_PATH并清理为相对路径
                path = file_info.get("F_PATH", "")
                
                # 如果是Windows绝对路径，提取相对路径部分
                if "\\" in path or ":" in path:
                    if "uploads" in path:
                        parts = path.split("uploads")
                        if len(parts) > 1:
                            path = parts[-1].lstrip("\\/")
                    else:
                        path = os.path.basename(path)
                
                # 移除开头的斜杠和 uploads/ 前缀
                path = path.lstrip("/\\")
                if path.startswith("uploads/") or path.startswith("uploads\\"):
                    path = path[8:]
                
                # 统一使用正斜杠
                path = path.replace("\\", "/")
                
                # 返回包含文件信息的对象
                return {
                    "id": file_id_int,
                    "path": path,
                    "name": file_info.get("F_ORIGINAL_NAME") or ""
                }
            return f"[文件ID: {file_id}]"
        except Exception:
            return f"[文件ID: {file_id}]"
    
    def _enhance_attachment_array(value):
        """增强附件数组，为每个附件项添加F_URL字段
        
        支持两种格式：
        1. 纯ID数组：[123, 456] (培养工单格式)
        2. 对象数组：[{"F_WJ_ID": 123}, {"id": 456}] (观察记录格式)
        """
        import json
        import os
        
        # 如果是字符串，尝试解析为JSON
        if isinstance(value, str):
            try:
                value = json.loads(value)
            except:
                return value
        
        # 如果是列表，为每个附件项添加F_URL
        if isinstance(value, list):
            enhanced = []
            for item in value:
                if isinstance(item, dict):
                    # 对象数组格式：[{"F_WJ_ID": 123, "F_BS": 4}]
                    enhanced_item = item.copy()
                    file_id = item.get("F_WJ_ID") or item.get("id")
                    if file_id:
                        try:
                            from app.crud.file_center import get_file_by_id
                            file_id_int = int(file_id)
                            file_info = get_file_by_id(file_id_int)
                            if file_info:
                                # 获取F_PATH并清理为相对路径
                                path = file_info.get("F_PATH", "")
                                
                                # 如果是Windows绝对路径，提取相对路径部分
                                if "\\" in path or ":" in path:
                                    if "uploads" in path:
                                        parts = path.split("uploads")
                                        if len(parts) > 1:
                                            path = parts[-1].lstrip("\\/")
                                    else:
                                        path = os.path.basename(path)
                                
                                # 移除开头的斜杠和 uploads/ 前缀
                                path = path.lstrip("/\\")
                                if path.startswith("uploads/") or path.startswith("uploads\\"):
                                    path = path[8:]
                                
                                # 统一使用正斜杠
                                path = path.replace("\\", "/")
                                
                                # 添加静态地址和文件名
                                enhanced_item["F_URL"] = path
                                enhanced_item["F_NAME"] = file_info.get("F_ORIGINAL_NAME") or ""
                        except Exception:
                            pass
                    enhanced.append(enhanced_item)
                elif isinstance(item, (int, str)):
                    # 纯ID数组格式：[123, 456] (培养工单格式)
                    try:
                        from app.crud.file_center import get_file_by_id
                        file_id_int = int(item)
                        file_info = get_file_by_id(file_id_int)
                        if file_info:
                            # 获取F_PATH并清理为相对路径
                            path = file_info.get("F_PATH", "")
                            
                            # 如果是Windows绝对路径，提取相对路径部分
                            if "\\" in path or ":" in path:
                                if "uploads" in path:
                                    parts = path.split("uploads")
                                    if len(parts) > 1:
                                        path = parts[-1].lstrip("\\/")
                                else:
                                    path = os.path.basename(path)
                            
                            # 移除开头的斜杠和 uploads/ 前缀
                            path = path.lstrip("/\\")
                            if path.startswith("uploads/") or path.startswith("uploads\\"):
                                path = path[8:]
                            
                            # 统一使用正斜杠
                            path = path.replace("\\", "/")
                            
                            # 转换为对象格式
                            enhanced.append({
                                "id": file_id_int,
                                "path": path,
                                "name": file_info.get("F_ORIGINAL_NAME") or ""
                            })
                        else:
                            # 文件不存在，保留原ID
                            enhanced.append({"id": file_id_int, "path": "", "name": ""})
                    except Exception:
                        # 转换失败，保留原值
                        enhanced.append(item)
                else:
                    enhanced.append(item)
            return enhanced
        
        return value
    
    def _summarize_complex_value(value, field_name):
        """为复杂JSON字段生成摘要"""
        if value is None or value == "":
            return ""
        
        # 病理/测序/其他：{bz, ids} 或 {bz, files} → {bz, files:[{id,path,name}]}
        if field_name in ATTACHMENT_BZ_FILES_FIELDS:
            return enhance_attachment_bz_files(value)
        
        # ✅ 对于文件ID数组字段，增强附件信息（添加静态地址和文件名）
        if field_name in FILE_ID_ARRAY_FIELDS:
            return _enhance_attachment_array(value)
        
        # ✅ 对于单个文件ID字段，转换为静态地址
        if field_name in SINGLE_FILE_ID_FIELDS:
            return _convert_file_id_to_url(value)
        
        # 对于评分表，只记录总分和计算时间
        if field_name == "F_YBPFB":
            if isinstance(value, str):
                import json
                try:
                    value = json.loads(value)
                except:
                    return "[评分表数据]"
            if isinstance(value, dict):
                total = value.get("total", "未知")
                calc_time = value.get("calculated_at", "未知")
                return f"总分: {total}, 计算时间: {calc_time}"
            return "[评分表数据]"
        
        # 对于数组型字段，只记录数量
        if isinstance(value, list):
            return f"共{len(value)}项"
        if isinstance(value, dict):
            return f"共{len(value)}个字段"
        return str(value)
    
    # 修改操作：记录变更字段
    for field, new_value in new_data.items():
        old_value = old_data.get(field)
        
        # 跳过内部字段
        if field in ["F_ID", "F_DJRQ", "F_DJR"]:
            continue
        
        # 智能比较：尝试数值比较，失败则使用字符串比较
        is_changed = False
        old_value_str = str(old_value) if old_value is not None else ""
        new_value_str = str(new_value) if new_value is not None else ""
        
        # 尝试数值比较（处理 5.0 vs 5 的情况）
        try:
            # 如果两者都能转换为数值，则进行数值比较
            if old_value is not None and new_value is not None:
                old_num = float(old_value)
                new_num = float(new_value)
                is_changed = (old_num != new_num)
            else:
                # 一个为None，一个不为None，则认为有变化
                is_changed = (old_value != new_value)
        except (ValueError, TypeError):
            # 无法转换为数值，使用字符串比较
            is_changed = (old_value_str != new_value_str)
        
        # 跳过未变化的字段
        if not is_changed:
            continue
        
        # ✅ 构建变更记录（对复杂字段、文件ID字段使用特殊处理）
        if (
            field in COMPLEX_JSON_FIELDS
            or field in SINGLE_FILE_ID_FIELDS
            or field in FILE_ID_ARRAY_FIELDS
            or field in ATTACHMENT_BZ_FILES_FIELDS
        ):
            old_summary = _summarize_complex_value(old_value, field)
            new_summary = _summarize_complex_value(new_value, field)
            
            # 如果返回的是列表或字典（如增强后的附件数组），保持原样
            # 否则转换为字符串
            change_item = {
                "field": field,
                "old_value": old_summary,
                "new_value": new_summary
            }
        else:
            change_item = {
                "field": field,
                "old_value": old_value_str,
                "new_value": new_value_str
            }
        
        # 只在有映射时才添加 label 字段（避免冗余）
        if field_mapping and field in field_mapping:
            change_item["label"] = field_mapping[field]
        
        changes.append(change_item)
    
    return {"变更字段": changes}


def create_qc_audit_record(
    F_ZKJD: str,
    F_BGLX: str,
    F_CHANGES: Dict[str, Any],
    F_JLR: str,
    F_ZZID: str = "",
    F_PDF: Optional[int] = None,
    F_GDID: Optional[int] = None,
    F_XM: Optional[str] = None,
    F_BRBH: Optional[str] = None,
    F_ZZBW: Optional[str] = None,
    F_YBLY: Optional[str] = None,
    F_SHBZ: Optional[str] = None,
    F_GDBH: Optional[str] = None,
    F_GDZT: Optional[str] = None,
    F_DCID: Optional[str] = None,
    F_DC: Optional[str] = None,
    F_PYSJ: Optional[datetime] = None,
    F_SQR: Optional[str] = None,
    F_PYTS: Optional[int] = None,
    F_OLD_DATA: Optional[Dict[str, Any]] = None,  # ✅ 新增参数
    from_pipeline_template: bool = False,
) -> int:
    """
    创建质控审核记录
    
    Args:
        F_ZKJD: 质控阶段（登记接收/分发/工单创建/传代培养/铺板/质控检测等）
        from_pipeline_template: 为 True 时在 F_ZKJD 末尾追加「（模板）」后缀
        F_BGLX: 变更类型（新增/修改/删除）
        F_ZZID: 组织ID（标本号）
        F_CHANGES: 变更内容JSON
        F_JLR: 记录人（操作人员）
        F_PDF: PDF文件ID（可选）
        F_GDID: 工单ID（可选，关联 T_PYGD.F_ID）
        F_XM: 姓名（可选）
        F_BRBH: 病人编号（可选）
        F_ZZBW: 组织部位（可选）
        F_YBLY: 样本来源（可选）
        F_SHBZ: 审核备注（可选）
        F_GDBH: 培养工单编号（可选，从工单创建阶段开始记录）
        F_GDZT: 工单状态（不存储，通过 F_GDBH 关联 T_PYGD 查询）
        F_DCID: 代次ID（可选，从工单创建阶段开始记录）
        F_DC: 代次（可选，从工单创建阶段开始记录）
        F_PYSJ: 开始培养日期（可选，对应表字段 F_KSPYRQ）
        F_SQR: 培养人/申请人（可选，对应表字段 F_PYR）
        F_PYTS: 培养天数（可选，观察阶段通过回收时间计算）
        F_OLD_DATA: 修改前的完整页面详情数据（可选，仅修改操作时有值）
    
    Returns:
        新创建的审核记录ID
    """
    if from_pipeline_template:
        F_ZKJD = template_zkjd(F_ZKJD)

    adapter = get_adapter()
    
    # 准备数据（不包含F_OLD_DATA和F_CHANGES，避免行长度超限）
    data = {
        "F_ZKBH": _generate_zkbh(),
        "F_ZKJD": F_ZKJD,
        "F_BGLX": F_BGLX,
        "F_SHZT": "待审核",
        "F_ZZID": F_ZZID,
        "F_JLR": F_JLR,
        "F_JLSJ": datetime.now()
    }
    
    # ✅ 序列化F_CHANGES和F_OLD_DATA（稍后单独UPDATE以避免行长度超限）
    changes_json = json.dumps(F_CHANGES, ensure_ascii=False, default=str)
    old_data_json = None
    if F_OLD_DATA is not None:
        old_data_json = json.dumps(F_OLD_DATA, ensure_ascii=False, default=str)
    
    # 添加可选字段
    if F_PDF is not None:
        data["F_PDF"] = F_PDF
    if F_GDID is not None:
        data["F_GDID"] = F_GDID
    if F_XM:
        data["F_XM"] = F_XM
    if F_BRBH:
        data["F_BRBH"] = F_BRBH
    if F_ZZBW:
        data["F_ZZBW"] = F_ZZBW
    if F_YBLY:
        data["F_YBLY"] = F_YBLY
    if F_SHBZ:
        data["F_SHBZ"] = F_SHBZ
    
    # 添加工单相关字段（从工单创建阶段开始需要记录）
    # 注意：只使用 T_ZKSH 表中实际存在的字段
    if F_GDBH:
        data["F_GDBH"] = F_GDBH  # 工单编号
    if F_DCID:
        data["F_DCID"] = F_DCID  # 代次ID
    if F_DC:
        data["F_DC"] = F_DC  # 代次
    if F_PYSJ is not None:
        data["F_KSPYRQ"] = F_PYSJ  # 开始培养日期
    if F_SQR:
        data["F_PYR"] = F_SQR  # 培养人
    if F_PYTS is not None:
        data["F_PYTS"] = F_PYTS  # 培养天数
    if F_GDID is not None:
        data["F_GDID"] = F_GDID  # 工单ID
    # F_GDZT (工单状态) 不存储在 T_ZKSH 中，而是通过 F_GDBH 关联 T_PYGD 查询
    
    # 插入记录
    with adapter.connect() as conn:
        cur = conn.cursor()
        
        # 构建INSERT语句（不包含F_CHANGES和F_OLD_DATA）
        fields = list(data.keys())
        placeholders = ", ".join(["?" for _ in fields])
        values = [data[f] for f in fields]
        
        sql = f"INSERT INTO T_ZKSH ({', '.join(fields)}) VALUES ({placeholders})"
        cur.execute(sql, tuple(values))
        
        # 获取插入的ID
        cur.execute("SELECT MAX(F_ID) FROM T_ZKSH WHERE F_ZKBH = ?", (data["F_ZKBH"],))
        result = cur.fetchone()
        new_id = result[0] if result else None
        
        # ✅ 单独UPDATE F_CHANGES和F_OLD_DATA（避免行长度超限）
        if new_id is not None:
            try:
                cur.execute(
                    "UPDATE T_ZKSH SET F_CHANGES = ? WHERE F_ID = ?",
                    (changes_json, new_id)
                )
            except Exception as e:
                import logging
                logging.getLogger(__name__).error(
                    f"更新F_CHANGES失败 (ID={new_id}), 数据长度={len(changes_json)}: {e}"
                )
            if old_data_json is not None:
                try:
                    cur.execute(
                        "UPDATE T_ZKSH SET F_OLD_DATA = ? WHERE F_ID = ?",
                        (old_data_json, new_id)
                    )
                except Exception as e:
                    import logging
                    logging.getLogger(__name__).error(
                        f"更新F_OLD_DATA失败 (ID={new_id}), 数据长度={len(old_data_json)}: {e}"
                    )
        
        conn.commit()
    
    return new_id


# 四个模块"标为不合格"对应的质控阶段
_UNQUALIFIED_ZKJD_SAMPLE = "标为不合格"
_UNQUALIFIED_ZKJD_CULTURE = "培养工单-标为不合格"
_UNQUALIFIED_ZKJD_DRUG = "药敏实验-标为不合格"
_UNQUALIFIED_ZKJD_ANIMAL = "动物实验-标为不合格"
_UNQUALIFIED_ZKJD_SET = {
    _UNQUALIFIED_ZKJD_SAMPLE,
    _UNQUALIFIED_ZKJD_CULTURE,
    _UNQUALIFIED_ZKJD_DRUG,
    _UNQUALIFIED_ZKJD_ANIMAL,
}

_DRUG_REPORT_ZKJD_SET = {"药物实验报告", "药物试验报告"}


def _extract_syjh_id_from_old_data(old_data_json: Optional[str]) -> Optional[int]:
    """从质控快照 F_OLD_DATA 中解析实验计划 ID。"""
    if not old_data_json:
        return None
    try:
        old_data = json.loads(old_data_json) if isinstance(old_data_json, str) else old_data_json
    except Exception:
        return None
    if not isinstance(old_data, dict):
        return None
    ep = old_data.get("endpoint") or {}
    syjh_id = old_data.get("F_SYJH_ID") or old_data.get("实验计划ID") or ep.get("F_SYJH_ID")
    if syjh_id is None:
        return None
    try:
        return int(syjh_id)
    except (TypeError, ValueError):
        return None


def apply_drug_report_review(
    cur,
    zkjd: str,
    shzt: str,
    old_data_json: Optional[str],
    logger=None,
) -> Dict[str, int]:
    """药物实验报告审核通过后，将实验计划状态更新为「已完成」。"""
    import logging
    if logger is None:
        logger = logging.getLogger(__name__)

    if normalize_zkjd(zkjd) not in _DRUG_REPORT_ZKJD_SET or shzt != "审核通过":
        return {}

    syjh_id = _extract_syjh_id_from_old_data(old_data_json)
    if syjh_id is None:
        logger.warning("[药物实验报告审核] 无法从 F_OLD_DATA 获取实验计划ID，跳过状态回写")
        return {}

    cur.execute("UPDATE T_SYJH SET F_ZT = ? WHERE F_ID = ?", ("已完成", syjh_id))
    affected = cur.rowcount
    if affected:
        logger.info(f"[药物实验报告审核] 实验计划状态已更新为已完成: 计划ID={syjh_id}")
    else:
        logger.warning(f"[药物实验报告审核] 未找到实验计划，计划ID={syjh_id}")
    return {"syjh_affected": affected}


def apply_unqualified_review(
    cur,
    zkjd: str,
    shzt: str,
    zzid: Optional[str],
    gdid: Optional[int],
    changes_json: Optional[str],
    logger=None,
) -> Dict[str, int]:
    """
    处理"标为不合格"类质控记录的审核通过/不通过回调。

    审核通过：正式标记为不合格
    审核不通过：恢复至 F_CHANGES.原状态（标本登记恢复 F_BHG='false'）
    """
    import logging
    if logger is None:
        logger = logging.getLogger(__name__)

    zkjd = normalize_zkjd(zkjd)
    if zkjd not in _UNQUALIFIED_ZKJD_SET:
        return {}

    changes: Dict[str, Any] = {}
    if changes_json:
        try:
            changes = json.loads(changes_json)
        except Exception:
            pass

    original_status = changes.get("原状态") or "已创建"
    key_info = changes.get("关键信息") or {}
    result: Dict[str, int] = {}

    if shzt == "审核通过":
        if zkjd == _UNQUALIFIED_ZKJD_SAMPLE:
            if zzid:
                cur.execute("UPDATE T_YBZJ SET F_BHG = 'true' WHERE F_ZZID = ?", (zzid,))
                result["ybzj_affected"] = cur.rowcount
                logger.info(f"[不合格审核] 标本登记审核通过，F_ZZID={zzid}")
        elif zkjd == _UNQUALIFIED_ZKJD_CULTURE:
            target_gdid = gdid or key_info.get("工单ID")
            if target_gdid:
                cur.execute(
                    "UPDATE T_PYGD SET F_BHG = 'true', F_GDZT = '不合格' WHERE F_ID = ?",
                    (int(target_gdid),),
                )
                result["pygd_affected"] = cur.rowcount
                logger.info(f"[不合格审核] 培养工单审核通过，GDID={target_gdid}")
        elif zkjd == _UNQUALIFIED_ZKJD_DRUG:
            plan_id = key_info.get("实验计划ID")
            if plan_id:
                cur.execute(
                    "UPDATE T_SYJH SET F_BHG = 'true', F_ZT = '不合格' WHERE F_ID = ?",
                    (int(plan_id),),
                )
                result["syjh_affected"] = cur.rowcount
                logger.info(f"[不合格审核] 药敏实验审核通过，计划ID={plan_id}")
        elif zkjd == _UNQUALIFIED_ZKJD_ANIMAL:
            plan_id = key_info.get("实验计划ID")
            if plan_id:
                cur.execute(
                    "UPDATE T_DWSY_JH SET F_BHG = 'true', F_ZT = '不合格' WHERE F_ID = ?",
                    (int(plan_id),),
                )
                result["dwsy_affected"] = cur.rowcount
                logger.info(f"[不合格审核] 动物实验审核通过，计划ID={plan_id}")

    elif shzt in ("审核不通过", "审核拒绝"):
        if zkjd == _UNQUALIFIED_ZKJD_SAMPLE:
            if zzid:
                cur.execute(
                    "UPDATE T_YBZJ SET F_BHG = 'false', F_BHGYY = '' WHERE F_ZZID = ?",
                    (zzid,),
                )
                result["ybzj_affected"] = cur.rowcount
                logger.info(f"[不合格审核] 标本登记审核不通过，恢复合格，F_ZZID={zzid}")
        elif zkjd == _UNQUALIFIED_ZKJD_CULTURE:
            target_gdid = gdid or key_info.get("工单ID")
            if target_gdid:
                cur.execute(
                    "UPDATE T_PYGD SET F_BHGYY = '', F_BHGLX = '', F_GDZT = ? WHERE F_ID = ?",
                    (original_status, int(target_gdid)),
                )
                result["pygd_affected"] = cur.rowcount
                logger.info(f"[不合格审核] 培养工单审核不通过，恢复状态={original_status}，GDID={target_gdid}")
        elif zkjd == _UNQUALIFIED_ZKJD_DRUG:
            plan_id = key_info.get("实验计划ID")
            if plan_id:
                cur.execute(
                    "UPDATE T_SYJH SET F_BHGYY = '', F_ZT = ? WHERE F_ID = ?",
                    (original_status, int(plan_id)),
                )
                result["syjh_affected"] = cur.rowcount
                logger.info(f"[不合格审核] 药敏实验审核不通过，恢复状态={original_status}，计划ID={plan_id}")
        elif zkjd == _UNQUALIFIED_ZKJD_ANIMAL:
            plan_id = key_info.get("实验计划ID")
            if plan_id:
                cur.execute(
                    "UPDATE T_DWSY_JH SET F_BHGYY = '', F_ZT = ? WHERE F_ID = ?",
                    (original_status, int(plan_id)),
                )
                result["dwsy_affected"] = cur.rowcount
                logger.info(f"[不合格审核] 动物实验审核不通过，恢复状态={original_status}，计划ID={plan_id}")

    return result


def get_sample_basic_info(F_ZZID: str) -> Dict[str, Any]:
    """
    获取样本的基本信息（用于填充质控审核记录）
    
    Args:
        F_ZZID: 标本号
    
    Returns:
        样本基本信息字典
        注意：只返回 T_YBZJ 表中实际存在的字段
        - F_XM: 姓名
        - F_BRBH: 病人编号
        - F_ZZBW: 组织部位（来自 F_CJBW 采集部位字段）
        - 不包含 F_YBLY（样本来源字段在 T_PYGD 培养工单表中，而不是 T_YBZJ 表）
    """
    adapter = get_adapter()
    
    with adapter.connect() as conn:
        cur = conn.cursor()
        
        # 查询样本基本信息（只查询 T_YBZJ 表中实际存在的字段）
        # F_CJBW: 采集部位（肝、肺等）
        sql = """
            SELECT F_XM, F_BRBH, F_CJBW
            FROM T_YBZJ
            WHERE F_ZZID = ?
        """
        cur.execute(sql, (F_ZZID,))
        row = cur.fetchone()
        
        if not row:
            return {}
        
        return {
            "F_XM": row[0],
            "F_BRBH": row[1],
            "F_ZZBW": row[2],  # 返回时用 F_ZZBW 作为key，实际查询的是 F_CJBW（采集部位）
        }


def get_old_data_for_comparison(F_ZZID: str, table_name: str = "T_YBZJ") -> Optional[Dict[str, Any]]:
    """
    获取修改前的数据（用于变更对比）
    
    Args:
        F_ZZID: 标本号
        table_name: 表名（默认T_YBZJ）
    
    Returns:
        原始数据字典，如果不存在返回None
    """
    adapter = get_adapter()
    
    with adapter.connect() as conn:
        cur = conn.cursor()
        
        # 查询现有数据
        sql = f"SELECT * FROM {table_name} WHERE F_ZZID = ?"
        cur.execute(sql, (F_ZZID,))
        row = cur.fetchone()
        
        if not row:
            return None
        
        # 获取列名
        columns = [desc[0] for desc in cur.description]
        
        # 转换为字典
        return dict(zip(columns, row))

