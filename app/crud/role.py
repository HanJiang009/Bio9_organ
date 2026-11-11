import json
import sys
from pathlib import Path
from typing import Optional, Dict, Any, List, Tuple
from app.db.factory import get_adapter
from app.schemas.role import RoleCreate, RoleUpdate
from app.crud.user import get_user
from app.db.adapters.dameng import DamengAdapter


def _rows_to_dicts(rows: List[tuple], cols: List[str]) -> List[Dict[str, Any]]:
    return [_normalize_role_record(dict(zip(cols, r))) for r in rows]


def _column_value(record: Dict[str, Any], name: str) -> Any:
    if name in record:
        return record.get(name)
    for key in record.keys():
        if str(key).upper() == name.upper():
            return record.get(key)
    return None


_pseudonym_column_ready = False


def ensure_pseudonym_column() -> bool:
    """T_JS.F_WSMH 不存在时补上。每种角色一行 JSON：总开关 + 勾选字段。"""
    global _pseudonym_column_ready
    if _pseudonym_column_ready:
        return True
    adapter = get_adapter()
    with adapter.connect() as conn:
        cur = conn.cursor()
        if adapter.column_exists(cur, "T_JS", "F_WSMH"):
            _pseudonym_column_ready = True
            return True
        if isinstance(adapter, DamengAdapter):
            ddl = "ALTER TABLE T_JS ADD F_WSMH VARCHAR(4000)"
        else:
            name = type(adapter).__name__.lower()
            if "sqlserver" in name:
                ddl = "ALTER TABLE T_JS ADD F_WSMH NVARCHAR(MAX) NULL"
            else:
                ddl = "ALTER TABLE T_JS ADD COLUMN F_WSMH TEXT NULL COMMENT '角色伪名化配置'"
        cur.execute(ddl)
        conn.commit()
    _pseudonym_column_ready = True
    return True


def load_role_pseudonym(role_name: str) -> Dict[str, Any]:
    """按角色名读伪名化。无记录或未配置时总开关为关。"""
    from app.core.pseudonym import parse_pseudonym_value
    name = str(role_name or "").strip()
    if not name:
        return parse_pseudonym_value(None)
    adapter = get_adapter()
    try:
        rows, cols = adapter.fetch_all(
            "SELECT F_WSMH FROM T_JS WHERE F_JS = ?",
            [name],
        )
    except Exception:
        try:
            ensure_pseudonym_column()
            rows, cols = adapter.fetch_all(
                "SELECT F_WSMH FROM T_JS WHERE F_JS = ?",
                [name],
            )
        except Exception:
            return parse_pseudonym_value(None)
    if not rows:
        return parse_pseudonym_value(None)
    record = dict(zip(cols, rows[0]))
    return parse_pseudonym_value(_column_value(record, "F_WSMH"))


def _normalize_role_record(record: Dict[str, Any]) -> Dict[str, Any]:
    if not record:
        return record
    from app.core.data_scope import parse_scope_map
    from app.core.pseudonym import parse_pseudonym_value, public_pseudonym
    raw = _column_value(record, "F_SJQX")
    record["F_SJQX"] = parse_scope_map(raw)
    parsed = parse_pseudonym_value(_column_value(record, "F_WSMH"))
    record["F_WSMH"] = public_pseudonym(parsed, str(record.get("F_JS") or ""))
    return record


def get_role(role_id: int) -> Optional[Dict[str, Any]]:
    try:
        rows, cols = get_adapter().fetch_all("SELECT * FROM T_JS WHERE F_ID = ?", [role_id])
    except Exception:
        rows, cols = get_adapter().fetch_all(
            "SELECT F_ID, F_JSBH, F_JS, F_QSS, F_ZT FROM T_JS WHERE F_ID = ?", [role_id]
        )
    return _rows_to_dicts(rows, cols)[0] if rows else None


def get_role_by_name(role_name: str) -> Optional[Dict[str, Any]]:
    try:
        rows, cols = get_adapter().fetch_all("SELECT * FROM T_JS WHERE F_JS = ?", [role_name])
    except Exception:
        rows, cols = get_adapter().fetch_all(
            "SELECT F_ID, F_JSBH, F_JS, F_QSS, F_ZT FROM T_JS WHERE F_JS = ?", [role_name]
        )
    return _rows_to_dicts(rows, cols)[0] if rows else None


def list_roles(skip: int = 0, limit: int = 20, name: str | None = None, code: str | None = None, status: str | None = None) -> List[Dict[str, Any]]:
    try:
        ensure_pseudonym_column()
    except Exception:
        pass
    base = ["SELECT F_ID, F_JSBH, F_JS, F_ZT, F_SJQX, F_WSMH FROM T_JS"]
    where: List[str] = []
    params: List[Any] = []

    if name and name.strip():
        where.append("F_JS LIKE ?")
        params.append(f"%{name.strip()}%")
    if code and code.strip():
        where.append("F_JSBH LIKE ?")
        params.append(f"%{code.strip()}%")
    if status and status.strip():
        st_norm = status.strip().lower()
        if st_norm in ("是", "true", "1", "yes", "y", "ys"):
            vals = ("true", "启用", "1", "yes", "y", "ys")
        elif st_norm in ("否", "false", "0", "no", "n"):
            vals = ("false", "停用", "0", "no", "n")
        else:
            vals = None
        if vals:
            where.append("LOWER(COALESCE(F_ZT,'')) IN (" + ",".join(["?"] * len(vals)) + ")")
            params.extend([v.lower() for v in vals])

    if where:
        base.append("WHERE "+" AND ".join(where))
    base_sql = "\n".join(base)

    adapter = get_adapter()
    sql, extra = adapter.paginate(base_sql, skip=skip, limit=limit)
    try:
        rows, cols = adapter.fetch_all(sql, params + extra)
    except Exception:
        base[0] = "SELECT F_ID, F_JSBH, F_JS, F_ZT, F_SJQX FROM T_JS"
        base_sql = "\n".join(base)
        sql, extra = adapter.paginate(base_sql, skip=skip, limit=limit)
        try:
            rows, cols = adapter.fetch_all(sql, params + extra)
        except Exception:
            base[0] = "SELECT F_ID, F_JSBH, F_JS, F_ZT FROM T_JS"
            base_sql = "\n".join(base)
            sql, extra = adapter.paginate(base_sql, skip=skip, limit=limit)
            rows, cols = adapter.fetch_all(sql, params + extra)
    result = _rows_to_dicts(rows, cols)

    # 追加 F_GLYH（关联用户数量，统计启用中的用户）
    disabled_vals = {"false", "停用", "0", "no", "n"}
    for r in result:
        role_name = r.get("F_JS")
        if not role_name:
            r["F_GLYH"] = 0
            continue
        pred, p = adapter.csv_contains("F_JS", role_name)
        # 仅统计启用用户
        st_filter = "LOWER(COALESCE(F_ZT,'')) NOT IN (" + ",".join(["?"] * len(disabled_vals)) + ")"
        sql_cnt = f"SELECT COUNT(1) FROM T_USER WHERE {pred} AND {st_filter}"
        rows_cnt, _ = adapter.fetch_all(sql_cnt, p + [v for v in (v.lower() for v in disabled_vals)])
        r["F_GLYH"] = int(rows_cnt[0][0]) if rows_cnt else 0

    return result


def get_permission_keys_for_role_names(role_names: List[str]) -> List[str]:
    """合并多个角色的权限集合。

    支持两种结构：
    1) 扁平：{root: {perm_key: true/false, ...}}
    2) 树：[{ id,label,children?, hasPermission, shortcut }]
    仅收集允许项（扁平取键名；树取叶子 id）。
    """
    if not role_names:
        return []
    perms: set[str] = set()
    adapter = get_adapter()
    for name in role_names:
        if not name:
            continue
        rows, _ = adapter.fetch_all("SELECT F_QSS FROM T_JS WHERE F_JS = ?", [name])
        if not rows:
            continue
        raw = rows[0][0]
        if raw is None:
            continue
        try:
            data = raw if isinstance(raw, (list, dict)) else json.loads(raw)
        except Exception:
            continue
        data = ensure_backfill_modules(ensure_home_in_permission_tree(data))
        def walk(node: Any):
            if isinstance(node, dict):
                # 若是树节点，优先遍历 children；只有叶子才取 id
                if "children" in node or "id" in node:
                    children = node.get("children")
                    if isinstance(children, list) and children:
                        for ch in children:
                            walk(ch)
                    else:
                        node_id = str(node.get("id") or "").strip()
                        if node_id and bool(node.get("hasPermission")):
                            perms.add(node_id)
                    return
                # 否则视作扁平根：{key: bool}
                for k, v in node.items():
                    if bool(v):
                        perms.add(str(k))
                return
            if isinstance(node, list):
                for ch in node:
                    walk(ch)

        walk(data)
    keys = sorted(perms)
    try:
        from app.utils.license_ui_modules import filter_module_ids
        return filter_module_ids(keys)
    except Exception:
        return keys


def permission_names_for_keys(keys: List[str]) -> List[str]:
    """把模块标识换成 permission_labels.json 中的中文名。没有配置时保留原标识。"""
    labels = _load_labels_map()
    return [labels.get(k) or k for k in keys]


def get_data_scope_maps_for_role_names(role_names: List[str]) -> List[Dict[str, str]]:
    """返回启用角色的模块数据权限映射。列不存在或查询失败时返回空列表。"""
    from app.core.data_scope import parse_scope_map
    names = [str(n).strip() for n in (role_names or []) if str(n).strip()]
    if not names:
        return []
    adapter = get_adapter()
    ph = ",".join(["?"] * len(names))
    sql = f"SELECT F_JS, F_ZT, F_SJQX FROM T_JS WHERE F_JS IN ({ph})"
    try:
        rows, cols = adapter.fetch_all(sql, names)
    except Exception:
        return []
    if not rows:
        return []
    items = [dict(zip(cols, r)) for r in rows]
    maps: List[Dict[str, str]] = []
    for item in items:
        status = str(item.get("F_ZT") or "").strip().lower()
        if status in {"false", "停用", "0", "no", "n"}:
            continue
        raw = item.get("F_SJQX")
        if raw is None:
            for key in item.keys():
                if str(key).upper() == "F_SJQX":
                    raw = item.get(key)
                    break
        maps.append(parse_scope_map(raw))
    return maps


def create_role(data: RoleCreate) -> Optional[Dict[str, Any]]:
    if get_role_by_name(data.F_JS):
        return None
    f_qss: Optional[str] = None
    if data.F_QSS is not None:
        f_qss = data.F_QSS if isinstance(data.F_QSS, str) else json.dumps(data.F_QSS, ensure_ascii=False)
    else:
        # 默认使用配置文件中的新权限树，原样写入
        try:
            p = _permission_template_path()
            f_qss = p.read_text(encoding='utf-8-sig')
        except Exception:
            # 兜底：使用旧的扁平全 true 结构
            f_qss = json.dumps(_default_permissions_all_true(), ensure_ascii=False)
    # 插入
    adapter = get_adapter()
    with adapter.connect() as conn:
        cur = conn.cursor()
        # 重试以处理并发唯一冲突
        max_attempts = 10
        attempt = 0
        last_num = None
        while attempt < max_attempts:
            attempt += 1
            code = _next_role_code(cur, prefix="A", width=4) if last_num is None else _code_from_num("A", 4, last_num + 1)
            try:
                from app.core.data_scope import parse_scope_map, serialize_scope_map
                f_sjqx = serialize_scope_map(parse_scope_map(getattr(data, "F_SJQX", None)))
                cur.execute(
                    "INSERT INTO T_JS (F_JSBH, F_JS, F_QSS, F_ZT, F_SJQX) VALUES (?, ?, ?, ?, ?)",
                    (code, data.F_JS, f_qss, data.F_ZT or "true", f_sjqx),
                )
                conn.commit()
                break
            except Exception as e:
                err_msg = str(e).lower()
                if "f_sjqx" in err_msg or "unknown column" in err_msg or "invalid column" in err_msg:
                    try:
                        cur.execute(
                            "INSERT INTO T_JS (F_JSBH, F_JS, F_QSS, F_ZT) VALUES (?, ?, ?, ?)",
                            (code, data.F_JS, f_qss, data.F_ZT or "true"),
                        )
                        conn.commit()
                        break
                    except Exception:
                        pass
                # 可能是唯一约束冲突，增大序号重试
                try:
                    num_part = int(str(code)[1:])
                except Exception:
                    num_part = 1
                last_num = num_part
                if attempt >= max_attempts:
                    raise
        # 达梦支持 identity，返回新ID
        new_id = None
        try:
            cur.execute("SELECT MAX(F_ID) FROM T_JS")
            new_id = cur.fetchone()[0]
        except Exception:
            pass
    if new_id:
        return get_role(int(new_id))
    return get_role_by_name(data.F_JS)


def update_role(role_id: int, data: RoleUpdate) -> Optional[Dict[str, Any]]:
    existing = get_role(role_id)
    if not existing:
        return None
    if data.F_JS != existing.get("F_JS") and get_role_by_name(data.F_JS):
        return None
    f_qss: Optional[str] = None
    if data.F_QSS is not None:
        f_qss = data.F_QSS if isinstance(data.F_QSS, str) else json.dumps(data.F_QSS, ensure_ascii=False)
    adapter = get_adapter()
    with adapter.connect() as conn:
        cur = conn.cursor()
        # 动态字段：F_JS、F_QSS、F_ZT
        sets = ["F_JS = ?"]
        params: list[Any] = [data.F_JS]
        # 仅在显式传入 F_QSS 时才更新权限树，避免只改状态时覆盖快捷键配置
        if f_qss is not None:
            sets.append("F_QSS = ?")
            params.append(f_qss)
        if data.F_ZT is not None:
            sets.append("F_ZT = ?")
            params.append(data.F_ZT)
        if getattr(data, "F_SJQX", None) is not None:
            from app.core.data_scope import parse_scope_map, serialize_scope_map
            sets.append("F_SJQX = ?")
            params.append(serialize_scope_map(parse_scope_map(data.F_SJQX)))
        params.append(role_id)
        sql = f"UPDATE T_JS SET {', '.join(sets)} WHERE F_ID = ?"
        cur.execute(sql, params)
        conn.commit()
    return get_role(role_id)


def delete_role(role_id: int) -> tuple[bool, str | None]:
    role = get_role(role_id)
    if not role:
        return False, "角色不存在"

    role_name = role.get("F_JS")
    if role_name:
        adapter = get_adapter()
        cond, params = adapter.csv_contains("F_JS", role_name)
        sql = f"SELECT COUNT(1) FROM T_USER WHERE {cond}"
        rows, _ = adapter.fetch_all(sql, params)
        cnt = int(rows[0][0]) if rows else 0
        if cnt > 0:
            return False, "角色存在关联用户，无法删除"

    adapter = get_adapter()
    with adapter.connect() as conn:
        cur = conn.cursor()
        cur.execute("DELETE FROM T_JS WHERE F_ID = ?", (role_id,))
        affected = cur.rowcount
        conn.commit()
    return (affected > 0), (None if affected > 0 else "删除失败")


def list_users_by_role_id(role_id: int) -> List[Dict[str, Any]]:
    """根据角色ID返回该角色下的用户工号和姓名。

    兼容当前将角色名存于 T_USER.F_JS（逗号串）的做法：先取角色名，再匹配用户。
    """
    role = get_role(role_id)
    if not role:
        return []
    role_name = role.get("F_JS")
    if not role_name:
        return []
    adapter = get_adapter()
    from app.crud.user import _name_column
    col = _name_column(adapter)
    predicate, params = adapter.csv_contains("F_JS", str(role_name))
    sql = f"""
    SELECT F_ID, F_YSM, {col} AS F_Ktype
    FROM T_USER
    WHERE {predicate}
    ORDER BY F_ID
    """
    rows, cols = adapter.fetch_all(sql, params)
    return _rows_to_dicts(rows, cols)


def _default_permissions_all_true() -> Dict[str, Any]:
    """根据 config/permission_labels.json 生成全 true 的权限树。"""
    # 统一从程序根目录的 config/ 读取
    if getattr(sys, 'frozen', False):
        # 打包后：从可执行文件同级的 config/ 目录读取
        base_dir = Path(sys.executable).parent
    else:
        # 开发环境：app/crud/role.py -> parents[1] 是 app 目录
        base_dir = Path(__file__).resolve().parents[1]
    
    cfg_path = base_dir / "config" / "permission_labels.json"
    
    try:
        data = json.loads(cfg_path.read_text(encoding='utf-8-sig'))
        root_id = data.get("root_id", "organbank")
        labels: Dict[str, str] = data.get("labels", {})
        # 过滤掉根本身，仅对其余键置 true
        leaves = {k: True for k in labels.keys() if k != root_id}
        return {root_id: leaves}
    except Exception:
        # 兜底：若文件缺失，给出固定集
        leaves = {
            "home": True,
            "sample_receive": True,
            "organoid_culture": True,
            "organoid_qc": True,
            "organoid_identification": True,
            "drug_assay": True,
            "reports": True,
            "qc_review": True,
            "sample_linkage": True,
            "drug_mgmt": True,
            "antibody_mgmt": True,
            "user_mgmt": True,
            "role_mgmt": True,
            "unified_search": True,
            "automated_dashboard": True,
        }
        return {"organbank": leaves}


def _next_role_code(cur, prefix: str = "A", width: int = 4) -> str:
    """在事务中基于现有 F_JSBH 生成下一个编号，格式 prefix + zero-padded number。

    依赖：F_JSBH 统一格式，如 A0001。若列不存在将抛异常，应先添加列并建立唯一索引。
    """
    # 优先使用标准 OFFSET/FETCH，其次 LIMIT，最后 TOP 1
    sql = "SELECT F_JSBH FROM T_JS WHERE F_JSBH LIKE ? ORDER BY F_JSBH DESC"
    paginated_sql, extra = get_adapter().paginate(sql, 0, 1)
    try:
        cur.execute(paginated_sql, (f"{prefix}%",) + tuple(extra))
    except Exception:
        return f"{prefix}{1:0{width}d}"
    row = cur.fetchone()
    if not row or not row[0]:
        return f"{prefix}{1:0{width}d}"
    last = str(row[0])
    if not last.upper().startswith(prefix.upper()):
        return f"{prefix}{1:0{width}d}"
    num_part = last[len(prefix):]
    try:
        n = int(num_part)
    except Exception:
        n = 0
    return f"{prefix}{(n+1):0{width}d}"


def _code_from_num(prefix: str, width: int, n: int) -> str:
    return f"{prefix}{n:0{width}d}"


def get_role_names() -> List[str]:
    adapter = get_adapter()
    rows, _ = adapter.fetch_all("SELECT F_JS, F_ZT FROM T_JS ORDER BY F_JS", [])
    disabled_vals = {"false", "停用", "0", "no", "n"}
    result: List[str] = []
    for r in rows:
        name = r[0] if len(r) > 0 else None
        status = (str(r[1]).strip().lower() if len(r) > 1 and r[1] is not None else "")
        if not name:
            continue
        # 仅当显式处于停用集合时过滤；空/缺省视为启用
        if status in disabled_vals:
            continue
        result.append(name)
    return result
def get_permissions_for_user_id(user_id: int) -> List[Dict[str, str]]:
    """根据用户ID返回有权限的模块列表（叶子节点的 id 与 label）。"""
    from app.crud.user import get_user  # 延迟导入避免循环
    u = get_user(user_id)
    if not u:
        return []
    role_names = [x.strip() for x in str(u.get("F_JS") or "").split(",") if x.strip()]
    keys = get_permission_keys_for_role_names(role_names)
    if not keys:
        return []
    labels = _load_labels_map()
    return [{"id": k, "label": labels.get(k, k)} for k in keys]



# ====== 新增：树模板与读写、节点修改 ======

def _permission_template_path() -> Path:
    """获取权限树模板文件路径，支持 PyInstaller 打包环境"""
    # 统一从程序根目录的 config/ 读取
    if getattr(sys, "frozen", False):
        # 打包后：从可执行文件同级的 config/ 目录读取
        base_dir = Path(sys.executable).parent
    else:
        # 开发环境：app/crud/role.py -> parents[1] 是 app 目录
        base_dir = Path(__file__).resolve().parents[1]
    
    return base_dir / "config" / "permission_tree.json"


def _load_permission_tree_template() -> Any:
    p = _permission_template_path()
    try:
        return json.loads(p.read_text(encoding='utf-8-sig'))
    except Exception:
        return [
            {"id": "organbank", "label": "类器官样本库管理系统", "children": [
                {"id": "system", "label": "系统管理", "children": [
                    {"id": "role_management", "label": "角色管理", "hasPermission": False, "shortcut": False},
                    {"id": "account_management", "label": "账号管理", "hasPermission": False, "shortcut": False}
                ]}
            ]}
        ]


def _clone_tree_all_true(node: Any) -> Any:
    if isinstance(node, dict):
        new_node = dict(node)
        children = new_node.get("children")
        if isinstance(children, list) and children:
            new_node["children"] = [_clone_tree_all_true(ch) for ch in children]
        else:
            new_node["hasPermission"] = True
            new_node["shortcut"] = bool(new_node.get("shortcut", False))
        return new_node
    if isinstance(node, list):
        return [_clone_tree_all_true(x) for x in node]
    return node


def _default_permission_tree_all_true() -> Any:
    tpl = _load_permission_tree_template()
    return _clone_tree_all_true(tpl)


HOME_MODULE_ID = "home"
HOME_MODULE_LABEL = "首页"
HOME_MODULE_NODE = {
    "id": HOME_MODULE_ID,
    "label": HOME_MODULE_LABEL,
    "hasPermission": True,
    "shortcut": False,
}
# 后加的模块。老角色的权限树里没有时补上，默认不开通。
_BACKFILL_MODULE_NODES = (
    {"id": "sample_inbound", "label": "样本入库", "hasPermission": False, "shortcut": False},
    {"id": "container_mgmt", "label": "容器管理", "hasPermission": False, "shortcut": False},
    {"id": "stock_audit", "label": "出入库审核", "hasPermission": False, "shortcut": False},
)


def _tree_has_module_id(node: Any, module_id: str) -> bool:
    if isinstance(node, dict):
        children = node.get("children")
        if isinstance(children, list) and children:
            return any(_tree_has_module_id(ch, module_id) for ch in children)
        if "id" in node or "children" in node:
            return str(node.get("id") or "").strip() == module_id
        return module_id in node
    if isinstance(node, list):
        return any(_tree_has_module_id(ch, module_id) for ch in node)
    return False


def ensure_home_in_permission_tree(tree: Any) -> Any:
    """老角色 F_QSS 缺首页节点时补入，默认开启。不覆盖已有 hasPermission。"""
    if tree is None:
        return dict(HOME_MODULE_NODE)
    if _tree_has_module_id(tree, HOME_MODULE_ID):
        return tree

    node = dict(HOME_MODULE_NODE)
    if isinstance(tree, list):
        if tree and isinstance(tree[0], dict) and isinstance(tree[0].get("children"), list):
            new_root = dict(tree[0])
            new_root["children"] = [node] + list(new_root.get("children") or [])
            return [new_root] + list(tree[1:])
        return [node] + list(tree)
    if isinstance(tree, dict):
        children = tree.get("children")
        if isinstance(children, list):
            new_tree = dict(tree)
            new_tree["children"] = [node] + list(children)
            return new_tree
        if "id" in tree:
            return [node, dict(tree)]
        new_tree = dict(tree)
        root_id = next(iter(new_tree), "organbank")
        leaves = new_tree.get(root_id)
        if isinstance(leaves, dict):
            merged_leaves = dict(leaves)
            merged_leaves[HOME_MODULE_ID] = True
            new_tree[root_id] = merged_leaves
            return new_tree
        new_tree[HOME_MODULE_ID] = True
        return new_tree
    return tree


def _append_leaf(tree: Any, node: Dict[str, Any]) -> Any:
    """把叶子加到权限树根的 children 末尾。"""
    if isinstance(tree, list):
        if tree and isinstance(tree[0], dict) and isinstance(tree[0].get("children"), list):
            new_root = dict(tree[0])
            new_root["children"] = list(new_root.get("children") or []) + [node]
            return [new_root] + list(tree[1:])
        return list(tree) + [node]
    if isinstance(tree, dict):
        children = tree.get("children")
        if isinstance(children, list):
            new_tree = dict(tree)
            new_tree["children"] = list(children) + [node]
            return new_tree
    return tree


def ensure_backfill_modules(tree: Any) -> Any:
    """老角色权限树缺后加模块时补入，默认关闭，不覆盖已有勾选。"""
    if tree is None:
        return tree
    for spec in _BACKFILL_MODULE_NODES:
        if _tree_has_module_id(tree, spec["id"]):
            continue
        tree = _append_leaf(tree, dict(spec))
    return tree


def get_permission_tree(role_id: int) -> Optional[Any]:
    r = get_role(role_id)
    if not r:
        return None
    raw = r.get("F_QSS")
    try:
        tree = raw if isinstance(raw, (list, dict)) else json.loads(raw)
    except Exception:
        tree = _default_permission_tree_all_true()
    return ensure_backfill_modules(ensure_home_in_permission_tree(tree))


def annotate_permission_tree(tree: Any, scope_map: Optional[Dict[str, str]] = None) -> Any:
    """给权限树叶子挂上 F_SJQX / supports_data_scope（拷贝，不写回 F_QSS）。"""
    from app.core.data_scope import DATA_SCOPE_MODULES, scope_for_module
    m = scope_map or {}

    def walk(node: Any) -> Any:
        if isinstance(node, dict):
            new = dict(node)
            children = new.get("children")
            if isinstance(children, list) and children:
                new["children"] = [walk(ch) for ch in children]
            else:
                nid = str(new.get("id") or "").strip()
                if nid:
                    new["F_SJQX"] = scope_for_module(m, nid)
                    new["supports_data_scope"] = nid in DATA_SCOPE_MODULES
            return new
        if isinstance(node, list):
            return [walk(x) for x in node]
        return node

    return walk(tree)


def get_permission_tree_with_data_scope(role_id: int) -> Optional[Any]:
    role = get_role(role_id)
    if not role:
        return None
    from app.core.data_scope import parse_scope_map
    raw_scope = role.get("F_SJQX")
    scope_map = raw_scope if isinstance(raw_scope, dict) else parse_scope_map(raw_scope)
    tree = annotate_permission_tree(get_permission_tree(role_id), scope_map)
    try:
        from app.utils.license_ui_modules import prune_permission_tree
        return prune_permission_tree(tree)
    except Exception:
        return tree


def _save_role_qss(role_id: int, qss: Any) -> None:
    adapter = get_adapter()
    with adapter.connect() as conn:
        cur = conn.cursor()
        cur.execute("UPDATE T_JS SET F_QSS = ? WHERE F_ID = ?", (json.dumps(qss, ensure_ascii=False), role_id))
        conn.commit()


def _find_node_by_id(node: Any, node_id: str) -> Optional[Dict[str, Any]]:
    if isinstance(node, dict):
        if str(node.get("id") or "") == node_id:
            return node
        children = node.get("children")
        if isinstance(children, list):
            for ch in children:
                found = _find_node_by_id(ch, node_id)
                if found:
                    return found
    elif isinstance(node, list):
        for ch in node:
            found = _find_node_by_id(ch, node_id)
            if found:
                return found
    return None


def set_permission(role_id: int, node_id: str, has_permission: bool) -> bool:
    tree = get_permission_tree(role_id)
    if tree is None:
        return False
    node = _find_node_by_id(tree, node_id)
    if not node:
        return False
    node["hasPermission"] = bool(has_permission)
    if not has_permission:
        node["shortcut"] = False
    _save_role_qss(role_id, tree)
    return True


def set_shortcut(role_id: int, node_id: str, shortcut: bool) -> tuple[bool, str | None]:
    tree = get_permission_tree(role_id)
    if tree is None:
        return False, "角色不存在"
    node = _find_node_by_id(tree, node_id)
    if not node:
        return False, "节点不存在"
    if shortcut and not bool(node.get("hasPermission")):
        return False, "请先开启权限"
    node["shortcut"] = bool(shortcut)
    _save_role_qss(role_id, tree)
    return True, None


def _iter_menu_leaves(node: Any):
    if isinstance(node, dict):
        children = node.get("children")
        if isinstance(children, list) and children:
            for ch in children:
                yield from _iter_menu_leaves(ch)
        else:
            if str(node.get("id") or "").strip():
                yield node
    elif isinstance(node, list):
        for ch in node:
            yield from _iter_menu_leaves(ch)


def list_role_menus(role: Dict[str, Any], only_permitted: bool = True) -> List[Dict[str, Any]]:
    """右侧菜单表：已开通权限的叶子菜单 + 该菜单的数据权限。"""
    from app.core.data_scope import DATA_SCOPE_MODULES, parse_scope_map, scope_for_module
    raw = role.get("F_QSS")
    try:
        tree = raw if isinstance(raw, (list, dict)) else json.loads(raw)
    except Exception:
        tree = _default_permission_tree_all_true()
    tree = ensure_backfill_modules(ensure_home_in_permission_tree(tree))
    raw_scope = role.get("F_SJQX")
    scope_map = raw_scope if isinstance(raw_scope, dict) else parse_scope_map(raw_scope)
    items: List[Dict[str, Any]] = []
    for node in _iter_menu_leaves(tree):
        nid = str(node.get("id") or "").strip()
        hp = bool(node.get("hasPermission"))
        if only_permitted and not hp:
            continue
        try:
            from app.utils.license_ui_modules import get_licensed_module_ids
            allowed = get_licensed_module_ids()
            if allowed is not None and nid not in allowed:
                continue
        except Exception:
            pass
        items.append({
            "id": nid,
            "label": node.get("label") or nid,
            "hasPermission": hp,
            "F_SJQX": scope_for_module(scope_map, nid),
            "supports_data_scope": nid in DATA_SCOPE_MODULES,
        })
    return items


def get_role_pseudonym(role_id: int) -> Optional[Dict[str, Any]]:
    role = get_role(role_id)
    if not role:
        return None
    cfg = role.get("F_WSMH") or {}
    return {
        "F_ID": role_id,
        "F_JS": role.get("F_JS"),
        "enabled": bool(cfg.get("enabled")),
        "role_name": role.get("F_JS") or "",
        "fields": cfg.get("fields") or [],
    }


def save_role_pseudonym(role_id: int, enabled: bool, fields: Any = None) -> Tuple[bool, Optional[str], Optional[Dict[str, Any]]]:
    from app.core.pseudonym import build_pseudonym_selection, parse_pseudonym_value, serialize_pseudonym
    role = get_role(role_id)
    if not role:
        return False, "角色不存在", None
    try:
        ensure_pseudonym_column()
    except Exception as e:
        return False, f"角色表缺少伪名化字段且自动补列失败: {e}", None
    current = parse_pseudonym_value(None)
    raw_fields = (role.get("F_WSMH") or {}).get("fields") or []
    if isinstance(raw_fields, list):
        current["fields"] = {
            str(item.get("key")): bool(item.get("checked"))
            for item in raw_fields
            if isinstance(item, dict) and item.get("key")
        }
    elif isinstance(raw_fields, dict):
        current["fields"] = raw_fields
    try:
        selected = build_pseudonym_selection(enabled, fields, current["fields"])
    except ValueError as e:
        return False, str(e), None
    adapter = get_adapter()
    with adapter.connect() as conn:
        cur = conn.cursor()
        cur.execute(
            "UPDATE T_JS SET F_WSMH = ? WHERE F_ID = ?",
            (serialize_pseudonym(selected["enabled"], selected["fields"]), role_id),
        )
        conn.commit()
    saved = get_role_pseudonym(role_id)
    return True, None, saved


def set_role_data_scope(role_id: int, module_id: str, scope: Any) -> Tuple[bool, Optional[str], Optional[Dict[str, Any]]]:
    from app.core.data_scope import normalize_scope, parse_scope_map, serialize_scope_map, scope_for_module
    role = get_role(role_id)
    if not role:
        return False, "角色不存在", None
    module_id = str(module_id or "").strip()
    if not module_id:
        return False, "缺少菜单ID", None
    tree = get_permission_tree(role_id)
    if tree is None or not _find_node_by_id(tree, module_id):
        return False, "菜单不存在", None
    raw_scope = role.get("F_SJQX")
    scope_map = dict(raw_scope) if isinstance(raw_scope, dict) else parse_scope_map(raw_scope)
    scope_map[module_id] = normalize_scope(scope)
    adapter = get_adapter()
    with adapter.connect() as conn:
        cur = conn.cursor()
        try:
            cur.execute(
                "UPDATE T_JS SET F_SJQX = ? WHERE F_ID = ?",
                (serialize_scope_map(scope_map), role_id),
            )
            conn.commit()
        except Exception as e:
            return False, f"保存失败，请确认已执行 F_SJQX 加宽迁移: {e}", None
    updated = get_role(role_id)
    saved = updated.get("F_SJQX") if updated else scope_map
    saved_map = saved if isinstance(saved, dict) else parse_scope_map(saved)
    return True, None, {
        "F_ID": role_id,
        "node_id": module_id,
        "F_SJQX": scope_for_module(saved_map, module_id),
    }


def _load_labels_map() -> Dict[str, str]:
    """从 permission_labels.json 读取权限标签映射"""
    # 统一从程序根目录的 config/ 读取
    if getattr(sys, "frozen", False):
        # 打包后：从可执行文件同级的 config/ 目录读取
        base_dir = Path(sys.executable).parent
    else:
        # 开发环境：app/crud/role.py -> parents[1] 是 app 目录
        base_dir = Path(__file__).resolve().parents[1]
    
    cfg_path = base_dir / "config" / "permission_labels.json"
    
    try:
        if cfg_path.exists():
            data = json.loads(cfg_path.read_text(encoding='utf-8-sig'))
            labels: Dict[str, str] = data.get("labels", {})
            return labels
    except Exception:
        pass
    
    # 失败则返回空字典
    return {}


def _collect_shortcuts_from_qss(qss: Any) -> List[str]:
    """从角色权限结构中提取开启的快捷键ID（需 hasPermission=true 且 shortcut=true）。支持树结构。"""
    result: set[str] = set()
    def walk(node: Any):
        if isinstance(node, dict):
            # 树节点
            if "children" in node or "id" in node:
                children = node.get("children")
                if isinstance(children, list) and children:
                    for ch in children:
                        walk(ch)
                else:
                    node_id = str(node.get("id") or "").strip()
                    if node_id and bool(node.get("hasPermission")) and bool(node.get("shortcut")):
                        result.add(node_id)
                return
            # 扁平结构没有 shortcut 信息，这里跳过
            return
        if isinstance(node, list):
            for ch in node:
                walk(ch)
    walk(qss)
    return list(result)


def get_shortcuts_for_user_id(user_id: int) -> List[Dict[str, str]]:
    """根据用户ID合并其角色的权限树，返回启用的快捷键项（id, label）。"""
    u = get_user(user_id)
    if not u:
        return []
    role_names = [x.strip() for x in str(u.get("F_JS") or "").split(",") if x.strip()]
    if not role_names:
        return []
    adapter = get_adapter()
    enabled_ids: set[str] = set()
    for rn in role_names:
        rows, _ = adapter.fetch_all("SELECT F_QSS FROM T_JS WHERE F_JS = ?", [rn])
        if not rows:
            continue
        raw = rows[0][0]
        try:
            qss = raw if isinstance(raw, (list, dict)) else json.loads(raw)
        except Exception:
            continue
        for sid in _collect_shortcuts_from_qss(qss):
            enabled_ids.add(sid)
    if not enabled_ids:
        return []
    try:
        from app.utils.license_ui_modules import filter_module_ids
        enabled_ids = set(filter_module_ids(enabled_ids))
    except Exception:
        pass
    if not enabled_ids:
        return []
    labels = _load_labels_map()
    out: List[Dict[str, str]] = []
    for sid in sorted(enabled_ids):
        out.append({"id": sid, "label": labels.get(sid, sid)})
    return out



