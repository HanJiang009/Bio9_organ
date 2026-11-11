"""
编码规则管理模块 CRUD
"""
from typing import Dict, Any, List, Optional
from app.db.factory import get_adapter
from datetime import datetime
import json
import logging
import re

logger = logging.getLogger(__name__)


def parse_rule_rec(raw: Any) -> Optional[List[Any]]:
    """出参统一为数组；库内 / 入参兼容 JSON 字符串。"""
    if raw is None or raw == "":
        return None
    if isinstance(raw, list):
        return raw
    if isinstance(raw, (bytes, bytearray)):
        try:
            raw = raw.decode("utf-8")
        except Exception:
            return None
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
        except Exception:
            return None
        return parsed if isinstance(parsed, list) else None
    return None


def dump_rule_rec(raw: Any) -> Optional[str]:
    """入库统一写成 JSON 字符串。"""
    if raw is None or raw == "":
        return None
    if isinstance(raw, str):
        s = raw.strip()
        return s or None
    try:
        return json.dumps(raw, ensure_ascii=False)
    except Exception:
        return None


def _attach_parsed_rule_rec(row: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not row:
        return row
    row["F_RULE_REC"] = parse_rule_rec(row.get("F_RULE_REC"))
    return row


# ============================================
# 编码规则相关函数
# ============================================

def list_code_rules(query: Dict[str, Any]) -> Dict[str, Any]:
    """
    查询编码规则列表
    
    Args:
        query: 查询条件
    
    Returns:
        {items: List[dict], total: int}
    """
    adapter = get_adapter()
    
    where: List[str] = []
    params: List[Any] = []
    
    # 启用状态过滤
    if query.get("F_ENABLED") is not None:
        where.append("F_ENABLED = ?")
        params.append(query["F_ENABLED"])
    
    where_sql = (" WHERE " + " AND ".join(where)) if where else ""
    
    base_sql = f"""
        SELECT F_ID, F_CODE, F_NAME, F_RULE_TEMPLATE, F_DESC, F_SAMPLE, F_RULE_REC, F_ENABLED,
               F_CREATE_TIME, F_UPDATE_TIME
        FROM T_DICT_CODE_RULE
        {where_sql}
        ORDER BY F_ID DESC
    """
    
    # 统计总数
    count_sql = f"SELECT COUNT(1) FROM T_DICT_CODE_RULE{where_sql}"
    
    # 分页查询
    sql, extra = adapter.paginate(base_sql, skip=query.get("skip", 0), limit=query.get("limit", 100))
    
    with adapter.connect() as conn:
        cur = conn.cursor()
        
        # 查询总数
        cur.execute(count_sql, tuple(params))
        total = int(cur.fetchone()[0])
        
        # 查询列表
        cur.execute(sql, tuple(params + extra))
        rows = cur.fetchall()
        cols = [c[0] for c in cur.description]
        items: List[Dict[str, Any]] = [
            _attach_parsed_rule_rec(dict(zip(cols, r))) for r in rows
        ]

        return {"items": items, "total": total}


def get_code_rule_by_id(rule_id: int) -> Optional[Dict[str, Any]]:
    """根据ID查询编码规则"""
    adapter = get_adapter()
    sql = """
        SELECT F_ID, F_CODE, F_NAME, F_RULE_TEMPLATE, F_DESC, F_SAMPLE, F_RULE_REC, F_ENABLED,
               F_CREATE_TIME, F_UPDATE_TIME
        FROM T_DICT_CODE_RULE WHERE F_ID = ?
    """
    with adapter.connect() as conn:
        cur = conn.cursor()
        cur.execute(sql, (rule_id,))
        row = cur.fetchone()
        if row:
            cols = [c[0] for c in cur.description]
            return _attach_parsed_rule_rec(dict(zip(cols, row)))
    return None


# 字典页「引用类型」的通用下拉。各规则在此基础上按代码追加。
_GENERIC_REF_FIELDS: List[Dict[str, str]] = [
    {"label": "病人编号", "value": "F_BRBH"},
    {"label": "标本号", "value": "F_ZZID"},
    {"label": "代次", "value": "F_DC"},
    {"label": "标本类型", "value": "F_BBLX"},
    {"label": "病种", "value": "F_AZ"},
    {"label": "取材部位", "value": "F_QCBW"},
]
_EXTRA_REF_FIELDS: Dict[str, List[Dict[str, str]]] = {
    "F_MCTP": [
        {"label": "培养天数", "value": "F_PYTS"},
        {"label": "细胞倍数", "value": "F_BS"},
        {"label": "培养工单号", "value": "F_GDBH"},
        {"label": "代次ID", "value": "F_DCID"},
    ],
}
_REF_PLACEHOLDER = re.compile(r"\{(F_\w+)\}")


def list_reference_fields(rule_code: str) -> Dict[str, Any]:
    """按规则代码返回可引用字段。未知规则只返回通用列表。"""
    code = (rule_code or "").strip()
    fields = list(_GENERIC_REF_FIELDS)
    fields.extend(_EXTRA_REF_FIELDS.get(code, []))
    return {"F_CODE": code, "fields": fields}


def _referenced_fields(rule_rec: Any, template: Optional[str]) -> List[str]:
    """引用类型段，以及模板里的 {F_*} 占位（不含字典 *_CODE）。"""
    found: List[str] = []
    seen = set()
    for seg in parse_rule_rec(rule_rec) or []:
        if not isinstance(seg, dict) or str(seg.get("type") or "") != "linkType":
            continue
        value = str(seg.get("value") or "").strip()
        if value and value not in seen:
            seen.add(value)
            found.append(value)
    for match in _REF_PLACEHOLDER.finditer(template or ""):
        name = match.group(1)
        if name.endswith("_CODE") or name in seen:
            continue
        seen.add(name)
        found.append(name)
    return found


def _load_rule_reference_graph() -> Dict[str, List[str]]:
    adapter = get_adapter()
    graph: Dict[str, List[str]] = {}
    with adapter.connect() as conn:
        cur = conn.cursor()
        cur.execute("SELECT F_CODE, F_RULE_TEMPLATE, F_RULE_REC FROM T_DICT_CODE_RULE")
        for code, template, rec in cur.fetchall():
            if code:
                graph[str(code)] = _referenced_fields(rec, template)
    return graph


def _validate_rule_references(rule_code: str, rule_rec: Any, template: Optional[str]) -> None:
    """引用字段必须在该规则允许列表内，且沿引用走回当前规则时拒绝。"""
    code = (rule_code or "").strip()
    refs = _referenced_fields(rule_rec, template)
    allowed = {item["value"] for item in list_reference_fields(code)["fields"]}
    for ref in refs:
        if ref not in allowed:
            raise ValueError(f"引用字段 {ref} 不在规则 {code} 的可引用范围内")

    graph = _load_rule_reference_graph()
    graph[code] = refs

    def walk(node: str, path: List[str]) -> None:
        for nxt in graph.get(node, []):
            if nxt == code:
                chain = " → ".join(path + [nxt])
                raise ValueError(f"编码规则不能引用自身或形成闭环：{chain}")
            if nxt not in graph or nxt in path:
                continue
            walk(nxt, path + [nxt])

    walk(code, [code])


def get_code_rule_by_code(rule_code: str) -> Optional[Dict[str, Any]]:
    """根据代码查询编码规则"""
    adapter = get_adapter()
    sql = """
        SELECT F_ID, F_CODE, F_NAME, F_RULE_TEMPLATE, F_DESC, F_SAMPLE, F_RULE_REC, F_ENABLED,
               F_CREATE_TIME, F_UPDATE_TIME
        FROM T_DICT_CODE_RULE WHERE F_CODE = ?
    """
    with adapter.connect() as conn:
        cur = conn.cursor()
        cur.execute(sql, (rule_code,))
        row = cur.fetchone()
        if row:
            cols = [c[0] for c in cur.description]
            return _attach_parsed_rule_rec(dict(zip(cols, row)))
    return None


def create_code_rule(data: Dict[str, Any]) -> int:
    """
    创建编码规则
    
    Args:
        data: 规则数据
    
    Returns:
        新创建的规则ID
    """
    adapter = get_adapter()
    
    # 检查代码是否已存在
    existing = get_code_rule_by_code(data["F_CODE"])
    if existing:
        raise ValueError(f"规则代码 {data['F_CODE']} 已存在")

    _validate_rule_references(data["F_CODE"], data.get("F_RULE_REC"), data.get("F_RULE_TEMPLATE"))
    
    sql = """
        INSERT INTO T_DICT_CODE_RULE (
            F_CODE, F_NAME, F_RULE_TEMPLATE, F_DESC, F_SAMPLE, F_RULE_REC, F_ENABLED
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
    """
    
    with adapter.connect() as conn:
        cur = conn.cursor()
        cur.execute(sql, (
            data["F_CODE"],
            data["F_NAME"],
            data["F_RULE_TEMPLATE"],
            data.get("F_DESC"),
            data.get("F_SAMPLE"),
            dump_rule_rec(data.get("F_RULE_REC")),
            data.get("F_ENABLED", 1)
        ))
        
        # 获取新插入的ID
        cur.execute("SELECT MAX(F_ID) FROM T_DICT_CODE_RULE")
        new_id = cur.fetchone()[0]
        
        conn.commit()
        return int(new_id)


def update_code_rule(rule_id: int, data: Dict[str, Any]) -> bool:
    """
    更新编码规则
    
    Args:
        rule_id: 规则ID
        data: 更新数据
    
    Returns:
        是否更新成功
    """
    adapter = get_adapter()
    
    # 检查是否存在
    existing = get_code_rule_by_id(rule_id)
    if not existing:
        raise ValueError(f"规则ID {rule_id} 不存在")

    # 只在本次改了模板或引用段时校验。已保存的错误规则，仅改名称时不拦截。
    touches_refs = (
        ("F_RULE_TEMPLATE" in data and data["F_RULE_TEMPLATE"] is not None)
        or ("F_RULE_REC" in data and data["F_RULE_REC"] is not None)
    )
    if touches_refs:
        template = data["F_RULE_TEMPLATE"] if "F_RULE_TEMPLATE" in data and data["F_RULE_TEMPLATE"] is not None else existing.get("F_RULE_TEMPLATE")
        rec = data["F_RULE_REC"] if "F_RULE_REC" in data and data["F_RULE_REC"] is not None else existing.get("F_RULE_REC")
        _validate_rule_references(existing["F_CODE"], rec, template)
    
    updates: List[str] = []
    params: List[Any] = []
    
    # 构建更新字段
    for field in ["F_NAME", "F_RULE_TEMPLATE", "F_DESC", "F_SAMPLE", "F_RULE_REC", "F_ENABLED"]:
        if field in data and data[field] is not None:
            updates.append(f"{field} = ?")
            val = dump_rule_rec(data[field]) if field == "F_RULE_REC" else data[field]
            params.append(val)
    
    if not updates:
        return True  # 没有需要更新的字段
    
    # 添加更新时间
    updates.append(f"F_UPDATE_TIME = {adapter.now()}")
    
    params.append(rule_id)
    sql = f"UPDATE T_DICT_CODE_RULE SET {', '.join(updates)} WHERE F_ID = ?"
    
    with adapter.connect() as conn:
        cur = conn.cursor()
        cur.execute(sql, tuple(params))
        conn.commit()
        return True


def delete_code_rule(rule_id: int) -> bool:
    """
    删除编码规则
    
    Args:
        rule_id: 规则ID
    
    Returns:
        是否删除成功
    """
    adapter = get_adapter()
    
    # 检查是否存在
    existing = get_code_rule_by_id(rule_id)
    if not existing:
        raise ValueError(f"规则ID {rule_id} 不存在")
    
    with adapter.connect() as conn:
        cur = conn.cursor()
        
        # 删除相关序列号记录
        cur.execute("DELETE FROM T_DICT_CODE_SEQUENCE WHERE F_RULE_CODE = ?", (existing["F_CODE"],))
        
        # 删除规则
        cur.execute("DELETE FROM T_DICT_CODE_RULE WHERE F_ID = ?", (rule_id,))
        
        conn.commit()
        return True


# ============================================
# 编码生成核心函数
# ============================================

def generate_code(rule_code: str, context: Dict[str, Any] = None) -> str:
    """
    根据规则代码和上下文数据生成编码
    
    Args:
        rule_code: 规则代码
        context: 上下文数据，包含业务字段值
    
    Returns:
        生成的编码字符串
    """
    context = context or {}
    
    # 获取规则
    rule = get_code_rule_by_code(rule_code)
    if not rule:
        raise ValueError(f"规则代码 {rule_code} 不存在")
    
    if rule["F_ENABLED"] != 1:
        raise ValueError(f"规则 {rule_code} 未启用")

    template = rule["F_RULE_TEMPLATE"]
    if not template or not template.strip():
        raise ValueError(f"规则 {rule_code} 的模板（F_RULE_TEMPLATE）为空，请先在字典编码规则中配置模板")

    # 1. 替换日期时间占位符
    template = _replace_datetime_placeholders(template)
    
    # 2. 替换业务字段占位符
    template = _replace_field_placeholders(template, context)
    
    # 3. 处理字典映射占位符（如 {F_BBLX_CODE}）
    template = _replace_dict_code_placeholders(template, context)
    
    # 4. 处理序列号占位符（最后处理，因为需要生成前缀）
    template = _replace_sequence_placeholders(template, rule_code, context)
    
    return template


def peek_code(rule_code: str, context: Dict[str, Any] = None, seq_offset: int = 0) -> str:
    """
    窥探下一个编码（不消耗序列号，仅用于预览展示）。

    与 generate_code 使用相同的模板与 context 解析逻辑，
    但序列号部分只读取 T_DICT_CODE_SEQUENCE 的当前值 + 1，不执行 UPDATE。
    seq_offset：在「下一个号」基础上再往后看几位（用于预览跳过已占用号）。
    """
    context = context or {}

    rule = get_code_rule_by_code(rule_code)
    if not rule:
        raise ValueError(f"规则代码 {rule_code} 不存在")

    if rule["F_ENABLED"] != 1:
        raise ValueError(f"规则 {rule_code} 未启用")

    template = rule["F_RULE_TEMPLATE"]
    if not template or not template.strip():
        raise ValueError(f"规则 {rule_code} 的模板（F_RULE_TEMPLATE）为空，请先在字典编码规则中配置模板")

    template = _replace_datetime_placeholders(template)
    template = _replace_field_placeholders(template, context)
    template = _replace_dict_code_placeholders(template, context)
    template = _peek_sequence_placeholders(template, rule_code, context, seq_offset=seq_offset)

    return template


def preview_code(rule_template: str, context: Dict[str, Any] = None) -> str:
    """
    预览编码（不保存序列号）
    
    Args:
        rule_template: 规则模板
        context: 上下文数据
    
    Returns:
        预览的编码字符串
    """
    context = context or {}
    template = rule_template
    
    # 1. 替换日期时间占位符
    template = _replace_datetime_placeholders(template)
    
    # 2. 替换业务字段占位符
    template = _replace_field_placeholders(template, context)
    
    # 3. 处理字典映射占位符
    template = _replace_dict_code_placeholders(template, context)
    
    # 4. 序列号用示例值替换（不实际生成）
    for match in re.finditer(r'\{SEQ:(\d+)\}', template):
        width = int(match.group(1))
        template = template.replace(match.group(0), "1".zfill(width))
    
    return template


def _gbk_safe(value: str) -> str:
    """将字符串中无法用 GBK 编码的字符替换为下划线，保证生成的编码可写入 GBK 数据库。"""
    result = []
    for ch in value:
        try:
            ch.encode('gbk')
            result.append(ch)
        except UnicodeEncodeError:
            result.append('_')
    return ''.join(result)


def _replace_datetime_placeholders(template: str) -> str:
    """替换日期时间占位符"""
    now = datetime.now()
    
    replacements = {
        '{YYYY}': now.strftime('%Y'),
        '{YY}': now.strftime('%y'),
        '{MM}': now.strftime('%m'),
        '{DD}': now.strftime('%d'),
        '{YYYYMMDD}': now.strftime('%Y%m%d'),
        '{MMDD}': now.strftime('%m%d'),
        '{HHmmss}': now.strftime('%H%M%S'),
        '{HH}': now.strftime('%H'),
        '{mm}': now.strftime('%M'),
        '{ss}': now.strftime('%S'),
    }
    
    for placeholder, value in replacements.items():
        template = template.replace(placeholder, value)
    
    return template


# 明场图片编码里的细胞倍数用 F_BS，字典类别是 F_XBBS（4倍 → 4x）。
_FIELD_DICT_ALIASES: Dict[str, List[str]] = {
    "F_BS": ["F_XBBS", "XBBS"],
}


def _category_code_candidates(field_name: str) -> List[str]:
    """
    业务字段名 → 可能的字典类别代码列表。

    兼容两种配置：
    - 标准：T_DICT_CATEGORY.F_CODE = BBLX（字段 F_BBLX 去 F_ 前缀）
    - 扩展：T_DICT_ITEM.F_CATEGORY_CODE = F_BBLX（与业务字段同名）
    """
    name = (field_name or "").strip()
    if not name:
        return []
    candidates: List[str] = []
    if name not in candidates:
        candidates.append(name)
    if name.startswith("F_"):
        stripped = name.replace("F_", "", 1)
        if stripped and stripped not in candidates:
            candidates.append(stripped)
    for alias in _FIELD_DICT_ALIASES.get(name, []):
        if alias not in candidates:
            candidates.append(alias)
    return candidates


def _field_to_category_code(field_name: str) -> str:
    """业务字段名 → 默认字典类别代码（优先无 F_ 前缀）。"""
    candidates = _category_code_candidates(field_name)
    if len(candidates) >= 2:
        return candidates[1]
    return candidates[0] if candidates else ""


def _dict_items_for_field(
    field_name: str,
    dict_items: Dict[str, List[Dict[str, Any]]],
) -> List[Dict[str, Any]]:
    """合并字段所有候选类别下的字典项（去重）。"""
    merged: List[Dict[str, Any]] = []
    seen_ids: set[Any] = set()
    for category_code in _category_code_candidates(field_name):
        for item in dict_items.get(category_code) or []:
            fid = item.get("F_ID")
            if fid in seen_ids:
                continue
            seen_ids.add(fid)
            merged.append(item)
    return merged


def _resolve_dict_item_code(
    field_value: Any,
    items: List[Dict[str, Any]],
) -> Optional[str]:
    """
    将 context 中的字典项取值解析为 T_DICT_ITEM.F_CODE。
    支持 context 传入 F_VALUE（显示值）、F_CODE 或 F_ID。
    """
    if field_value is None:
        return None
    val_str = str(field_value).strip()
    if not val_str:
        return None

    for item in items:
        code = item.get("F_CODE")
        value = item.get("F_VALUE")
        code_str = str(code).strip() if code is not None else ""
        value_str = str(value).strip() if value is not None else ""

        if value_str and value_str == val_str:
            return code_str or val_str
        if code_str and code_str == val_str:
            return code_str
        fid = item.get("F_ID")
        if fid is not None and str(fid) == val_str:
            return code_str or val_str
    return None


def _collect_dict_category_codes(template: str) -> List[str]:
    """从模板中收集可能需要字典映射的类别代码。"""
    codes: set[str] = set()
    for match in re.finditer(r"\{(F_\w+)\}", template):
        field_name = match.group(1)
        base_field = field_name[:-5] if field_name.endswith("_CODE") else field_name
        for category_code in _category_code_candidates(base_field):
            codes.add(category_code)
    return list(codes)


def _replace_field_placeholders(template: str, context: Dict[str, Any]) -> str:
    """替换业务字段占位符；引用类型字段优先输出 T_DICT_ITEM.F_CODE。"""
    from app.crud.dict import get_dict_items_batch

    category_codes = _collect_dict_category_codes(template)
    dict_items = get_dict_items_batch(category_codes, enabled_only=True) if category_codes else {}

    for match in re.finditer(r"\{(F_\w+)\}", template):
        field_name = match.group(1)
        if field_name.endswith("_CODE"):
            continue

        field_value = context.get(field_name, "")
        if field_value is None or field_value == "":
            replacement = ""
        else:
            items = _dict_items_for_field(field_name, dict_items)
            resolved = _resolve_dict_item_code(field_value, items)
            if resolved is not None:
                replacement = _gbk_safe(resolved)
            elif items:
                # 有字典类别但未匹配到项，仍用原值（兼容非字典字段误配）
                replacement = _gbk_safe(str(field_value))
            else:
                replacement = _gbk_safe(str(field_value))
        template = template.replace(match.group(0), replacement, 1)

    return template


def _replace_dict_code_placeholders(template: str, context: Dict[str, Any]) -> str:
    """替换显式字典映射占位符（如 {F_BBLX_CODE}），输出 T_DICT_ITEM.F_CODE。"""
    from app.crud.dict import get_dict_items_batch

    matches = list(re.finditer(r"\{(F_\w+)_CODE\}", template))
    if not matches:
        return template

    category_codes: set[str] = set()
    for match in matches:
        for category_code in _category_code_candidates(match.group(1)):
            category_codes.add(category_code)
    dict_items = get_dict_items_batch(list(category_codes), enabled_only=True)

    for match in matches:
        base_field = match.group(1)
        field_value = context.get(base_field)
        if field_value is None or field_value == "":
            replacement = ""
        else:
            items = _dict_items_for_field(base_field, dict_items)
            resolved = _resolve_dict_item_code(field_value, items)
            replacement = _gbk_safe(str(resolved if resolved is not None else field_value))
        template = template.replace(match.group(0), replacement, 1)

    return template


def _peek_sequence_placeholders(
    template: str, rule_code: str, context: Dict[str, Any], seq_offset: int = 0
) -> str:
    """替换序列号占位符（窥探模式，不消耗序列号）"""
    extra = max(int(seq_offset or 0), 0)
    for match in re.finditer(r'\{SEQ:(\d+)\}', template):
        width = int(match.group(1))
        prefix = template[:match.start()]
        seq = _peek_next_sequence(rule_code, prefix, extra=extra)
        template = template.replace(match.group(0), str(seq).zfill(width), 1)
    return template


def _replace_sequence_placeholders(template: str, rule_code: str, context: Dict[str, Any]) -> str:
    """替换序列号占位符"""
    # 匹配所有 {SEQ:n} 模式
    for match in re.finditer(r'\{SEQ:(\d+)\}', template):
        width = int(match.group(1))
        
        # 生成前缀（用于分组，当前模板截止到序列号之前的部分）
        prefix = template[:match.start()]
        
        # 获取下一个序列号
        seq = _get_next_sequence(rule_code, prefix)
        
        template = template.replace(match.group(0), str(seq).zfill(width), 1)
    
    return template


def _peek_next_sequence(rule_code: str, prefix: str, extra: int = 0) -> int:
    """
    窥探下一个序列号（只读，不更新数据库）。

    Args:
        rule_code: 规则代码
        prefix: 前缀（用于分组）
        extra: 在下一号基础上再偏移几位

    Returns:
        下一个将要分配的序列号
    """
    prefix = _gbk_safe(prefix)
    extra = max(int(extra or 0), 0)
    adapter = get_adapter()

    with adapter.connect() as conn:
        cur = conn.cursor()
        cur.execute("""
            SELECT F_CURRENT_VALUE
            FROM T_DICT_CODE_SEQUENCE
            WHERE F_RULE_CODE = ? AND F_PREFIX = ?
        """, (rule_code, prefix))
        row = cur.fetchone()
        if row:
            return int(row[0]) + 1 + extra
        return 1 + extra


def _get_next_sequence(rule_code: str, prefix: str) -> int:
    """
    获取下一个序列号（线程安全）
    
    Args:
        rule_code: 规则代码
        prefix: 前缀（用于分组）
    
    Returns:
        下一个序列号
    """
    # prefix 会写入数据库（GBK），确保安全
    prefix = _gbk_safe(prefix)
    adapter = get_adapter()
    
    with adapter.connect() as conn:
        cur = conn.cursor()
        
        # 尝试获取现有序列
        cur.execute("""
            SELECT F_ID, F_CURRENT_VALUE 
            FROM T_DICT_CODE_SEQUENCE 
            WHERE F_RULE_CODE = ? AND F_PREFIX = ?
        """, (rule_code, prefix))
        
        row = cur.fetchone()
        
        if row:
            # 更新现有序列
            seq_id, current_value = row
            next_value = current_value + 1
            
            # 更新时间字段
            time_sql = adapter.now()
            
            cur.execute(f"""
                UPDATE T_DICT_CODE_SEQUENCE 
                SET F_CURRENT_VALUE = ?, F_LAST_UPDATE = {time_sql}
                WHERE F_ID = ?
            """, (next_value, seq_id))
            
            conn.commit()
            return next_value
        else:
            # 创建新序列
            cur.execute("""
                INSERT INTO T_DICT_CODE_SEQUENCE (
                    F_RULE_CODE, F_PREFIX, F_CURRENT_VALUE, F_RESET_TYPE
                ) VALUES (?, ?, ?, ?)
            """, (rule_code, prefix, 1, 'NEVER'))
            
            conn.commit()
            return 1


# ============================================
# 序列号管理函数
# ============================================

def list_code_sequences(query: Dict[str, Any]) -> Dict[str, Any]:
    """
    查询编码序列列表
    
    Args:
        query: 查询条件
    
    Returns:
        {items: List[dict], total: int}
    """
    adapter = get_adapter()
    
    where: List[str] = []
    params: List[Any] = []
    
    # 规则代码过滤
    if query.get("F_RULE_CODE"):
        where.append("F_RULE_CODE = ?")
        params.append(query["F_RULE_CODE"])
    
    where_sql = (" WHERE " + " AND ".join(where)) if where else ""
    
    base_sql = f"""
        SELECT F_ID, F_RULE_CODE, F_PREFIX, F_CURRENT_VALUE, F_RESET_TYPE, F_LAST_UPDATE
        FROM T_DICT_CODE_SEQUENCE
        {where_sql}
        ORDER BY F_LAST_UPDATE DESC
    """
    
    # 统计总数
    count_sql = f"SELECT COUNT(1) FROM T_DICT_CODE_SEQUENCE{where_sql}"
    
    # 分页查询
    sql, extra = adapter.paginate(base_sql, skip=query.get("skip", 0), limit=query.get("limit", 100))
    
    with adapter.connect() as conn:
        cur = conn.cursor()
        
        # 查询总数
        cur.execute(count_sql, tuple(params))
        total = int(cur.fetchone()[0])
        
        # 查询列表
        cur.execute(sql, tuple(params + extra))
        rows = cur.fetchall()
        cols = [c[0] for c in cur.description]
        items: List[Dict[str, Any]] = [dict(zip(cols, r)) for r in rows]
        
        return {"items": items, "total": total}
