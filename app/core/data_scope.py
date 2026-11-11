"""角色数据权限：按模块设置个人数据 / 全部数据。

取值：
- all  : 全部数据（缺省、NULL、未知值、未配置的模块均视为 all）
- self : 仅当前用户作为归属人的数据

F_SJQX 存 JSON 映射 {菜单id: all|self}。旧数据若是标量 all/self，视为 {"*": 该值}，未点过的模块仍沿用。
一次只认当前会话角色；每次请求查 T_JS，不写入 JWT。
"""
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional, Sequence

SCOPE_ALL = "all"
SCOPE_SELF = "self"
SCOPE_WILDCARD = "*"

_SELF_VALUES = {"self", "own", "mine", "个人数据", "本人数据"}
_DISABLED_ROLE_STATUS = {"false", "停用", "0", "no", "n"}

# 后端实际按归属人过滤的菜单。其它菜单也可保存设置，但查询层不会套用。
DATA_SCOPE_MODULES = frozenset({
    "home",
    "sample_receive",
    "organoid_culture_tissue",
    "organoid_qc",
    "drug_assay",
    "animal_experiment",
    "qc_review",
    "sample_linkage",
    "transport_mgmt",
    "outbound_mgmt",
    "unified_search",
    "op_log",
})


def normalize_scope(raw: Any) -> str:
    """把入库/入参规范化为 all 或 self。"""
    if raw is None:
        return SCOPE_ALL
    s = str(raw).strip()
    if not s:
        return SCOPE_ALL
    if s in _SELF_VALUES or s.lower() in _SELF_VALUES:
        return SCOPE_SELF
    return SCOPE_ALL


def parse_role_names(raw: Any) -> List[str]:
    if isinstance(raw, list):
        return [str(r).strip() for r in raw if str(r).strip()]
    if isinstance(raw, str):
        return [r.strip() for r in raw.split(",") if r.strip()]
    return []


def parse_scope_map(raw: Any) -> Dict[str, str]:
    """把 T_JS.F_SJQX 解析为 {module: all|self}。"""
    if raw is None or raw == "":
        return {}
    if isinstance(raw, dict):
        out: Dict[str, str] = {}
        for k, v in raw.items():
            key = str(k).strip()
            if key:
                out[key] = normalize_scope(v)
        return out
    s = str(raw).strip()
    if not s:
        return {}
    if s[0] in "{[":
        try:
            return parse_scope_map(json.loads(s))
        except Exception:
            return {}
    return {SCOPE_WILDCARD: normalize_scope(s)}


def serialize_scope_map(scope_map: Optional[Dict[str, str]]) -> str:
    clean: Dict[str, str] = {}
    for k, v in (scope_map or {}).items():
        key = str(k).strip()
        if key:
            clean[key] = normalize_scope(v)
    return json.dumps(clean, ensure_ascii=False)


def scope_for_module(scope_map: Optional[Dict[str, str]], module: Optional[str]) -> str:
    m = scope_map or {}
    key = str(module or "").strip()
    if key and key in m:
        return normalize_scope(m[key])
    return normalize_scope(m.get(SCOPE_WILDCARD))


def session_role_names(user: Optional[Dict[str, Any]]) -> List[str]:
    if not isinstance(user, dict):
        return []
    selected = str(user.get("F_JS_SELECTED") or "").strip()
    if selected:
        return [selected]
    return parse_role_names(user.get("F_JS"))


def owner_name_idents(user: Optional[Dict[str, Any]]) -> List[str]:
    """姓名类归属比对值：F_Ktype 与 F_YSM（保存时常用 F_Ktype || F_YSM）。"""
    if not user:
        return []
    idents: List[str] = []
    for key in ("F_Ktype", "F_YSM"):
        v = user.get(key)
        if v is None:
            continue
        s = str(v).strip()
        if s and s not in idents:
            idents.append(s)
    return idents


def owner_user_id(user: Optional[Dict[str, Any]]) -> Optional[int]:
    if not user:
        return None
    uid = user.get("F_ID")
    if uid is None or uid == "":
        return None
    try:
        return int(uid)
    except (TypeError, ValueError):
        return None


def resolve_data_scope_map(user: Optional[Dict[str, Any]]) -> Dict[str, str]:
    """当前会话角色的模块数据权限映射（含旧数据的 *）。"""
    names = session_role_names(user)
    if not names:
        return {}
    from app.crud.role import get_data_scope_maps_for_role_names
    maps = get_data_scope_maps_for_role_names(names)
    if not maps:
        return {}
    if len(maps) == 1:
        return dict(maps[0])
    keys: set[str] = set()
    for item in maps:
        keys.update(item)
    merged: Dict[str, str] = {}
    for key in keys:
        if any(normalize_scope(item.get(key)) == SCOPE_ALL for item in maps if key in item):
            merged[key] = SCOPE_ALL
        elif all(normalize_scope(item.get(key)) == SCOPE_SELF for item in maps if key in item):
            merged[key] = SCOPE_SELF
        else:
            merged[key] = SCOPE_ALL
    return merged


def resolve_data_scope(user: Optional[Dict[str, Any]], module: Optional[str] = None) -> str:
    """按当前会话角色 + 模块取数据范围。未传模块或未配置 → all（旧标量走 *）。"""
    if not isinstance(user, dict):
        return SCOPE_ALL
    return scope_for_module(resolve_data_scope_map(user), module)


def is_self_scope(scope: Optional[str]) -> bool:
    return normalize_scope(scope) == SCOPE_SELF


def owner_predicate(
    column: str,
    user: Optional[Dict[str, Any]],
    match: str = "name",
) -> tuple[str, List[Any]]:
    """返回 (SQL片段, 参数)。身份为空时为 ('1=0', [])。"""
    if match == "id":
        uid = owner_user_id(user)
        if uid is None:
            return "1=0", []
        return f"{column} = ?", [uid]
    idents = owner_name_idents(user)
    if not idents:
        return "1=0", []
    if len(idents) == 1:
        return f"{column} = ?", [idents[0]]
    ph = ",".join(["?"] * len(idents))
    return f"{column} IN ({ph})", list(idents)


def apply_owner_filter(
    where: List[str],
    params: List[Any],
    *,
    scope: Optional[str],
    user: Optional[Dict[str, Any]],
    column: str,
    match: str = "name",
) -> None:
    """在 where/params 上追加个人数据条件。scope 非 self 时不改写。

    match:
      - name: column IN (F_Ktype, F_YSM)
      - id  : column = F_ID（T_FFJL.F_PYRY）
    self 且身份为空时追加 1=0，避免误放出全表。
    """
    if not is_self_scope(scope):
        return
    pred, extra = owner_predicate(column, user, match=match)
    where.append(pred)
    params.extend(extra)


def apply_owner_or_filter(
    where: List[str],
    params: List[Any],
    *,
    scope: Optional[str],
    user: Optional[Dict[str, Any]],
    columns: Sequence[str],
    match: str = "name",
) -> None:
    """self 时按多个归属列 OR 过滤。"""
    if not is_self_scope(scope):
        return
    if not columns:
        where.append("1=0")
        return
    parts: List[str] = []
    extra: List[Any] = []
    for col in columns:
        pred, p = owner_predicate(col, user, match=match)
        parts.append(pred)
        extra.extend(p)
    where.append("(" + " OR ".join(parts) + ")")
    params.extend(extra)


def is_owner(
    user: Optional[Dict[str, Any]],
    owner_value: Any,
    match: str = "name",
) -> bool:
    if match == "id":
        uid = owner_user_id(user)
        if uid is None or owner_value is None or owner_value == "":
            return False
        try:
            return int(owner_value) == uid
        except (TypeError, ValueError):
            return str(owner_value).strip() == str(uid)
    idents = owner_name_idents(user)
    if not idents:
        return False
    return str(owner_value or "").strip() in idents


def deny_if_not_owner(
    scope: Optional[str],
    user: Optional[Dict[str, Any]],
    owner_value: Any,
    match: str = "name",
) -> bool:
    """True 表示应拒绝访问。"""
    if not is_self_scope(scope):
        return False
    return not is_owner(user, owner_value, match)


def attach_data_scope(user: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not user:
        return user
    user["data_scopes"] = resolve_data_scope_map(user)
    return user


def access_denied_response():
    from app.core.response import err
    return err("无权查看该数据")


def _fetch_one(sql: str, params: List[Any]) -> Any:
    try:
        from app.db.factory import get_adapter
        rows, _ = get_adapter().fetch_all(sql, params)
    except Exception:
        return None
    if not rows:
        return None
    return rows[0][0]


def fetch_pygd_sqr(gdid: Any) -> Any:
    if gdid is None or gdid == "":
        return None
    try:
        gid = int(gdid)
    except (TypeError, ValueError):
        return None
    return _fetch_one("SELECT F_SQR FROM T_PYGD WHERE F_ID = ?", [gid])


def fetch_ybzj_djr_by_id(ybzj_id: Any) -> Any:
    if ybzj_id is None or ybzj_id == "":
        return None
    try:
        rid = int(ybzj_id)
    except (TypeError, ValueError):
        return None
    return _fetch_one("SELECT F_DJR FROM T_YBZJ WHERE F_ID = ?", [rid])


def fetch_ybzj_djr_by_zzid(zzid: Any) -> Any:
    zz = str(zzid or "").strip()
    if not zz:
        return None
    return _fetch_one(
        "SELECT F_DJR FROM T_YBZJ WHERE F_ID = (SELECT MAX(F_ID) FROM T_YBZJ WHERE F_ZZID = ?)",
        [zz],
    )


def deny_owner_access(
    user: Optional[Dict[str, Any]],
    owner_value: Any,
    match: str = "name",
    module: Optional[str] = None,
):
    """self 且非归属人时返回 err()，否则 None。"""
    scope = resolve_data_scope(user, module)
    if deny_if_not_owner(scope, user, owner_value, match=match):
        return access_denied_response()
    return None


def deny_pygd_access(user: Optional[Dict[str, Any]], gdid: Any, module: Optional[str] = None):
    if not is_self_scope(resolve_data_scope(user, module)):
        return None
    return deny_owner_access(user, fetch_pygd_sqr(gdid), match="name", module=module)


def deny_sample_access_by_zzid(user: Optional[Dict[str, Any]], zzid: Any, module: Optional[str] = None):
    if not is_self_scope(resolve_data_scope(user, module)):
        return None
    return deny_owner_access(user, fetch_ybzj_djr_by_zzid(zzid), match="name", module=module)


def deny_sample_access_by_id(user: Optional[Dict[str, Any]], ybzj_id: Any, module: Optional[str] = None):
    if not is_self_scope(resolve_data_scope(user, module)):
        return None
    return deny_owner_access(user, fetch_ybzj_djr_by_id(ybzj_id), match="name", module=module)


def fetch_syjh_syr(plan_id: Any) -> Any:
    if plan_id is None or plan_id == "":
        return None
    try:
        pid = int(plan_id)
    except (TypeError, ValueError):
        return None
    return _fetch_one("SELECT F_SYR FROM T_SYJH WHERE F_ID = ?", [pid])


def fetch_dwsy_syr(plan_id: Any) -> Any:
    if plan_id is None or plan_id == "":
        return None
    try:
        pid = int(plan_id)
    except (TypeError, ValueError):
        return None
    return _fetch_one("SELECT F_SYR FROM T_DWSY_JH WHERE F_ID = ?", [pid])
