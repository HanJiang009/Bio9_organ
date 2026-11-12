"""授权 ui_config.modules：中文名给前端；英文 id 给权限接口。"""
from pathlib import Path
import sys
from typing import Any, Dict, Iterable, List, Optional, Set

# 与 bio9_admin/config/modules.json 的 Bio9_organ 段保持一致
ALWAYS_KEEP_MODULE_IDS = frozenset({"home"})

ID_TO_LABEL = {
    "home": "首页",
    "home_layout1": "首页/版式1",
    "sample_receive": "样本接收",
    "organoid_culture_tissue": "组织细胞培养",
    "sample_inbound": "样本入库",
    "organoid_culture_blood": "血液细胞培养",
    "organoid_qc": "类器官质控",
    "organoid_identification": "类器官鉴定",
    "shortcut_workbenches": "快捷工位",
    "drug_assay": "药物实验",
    "animal_experiment": "动物实验",
    "reports": "统计报表",
    "qc_review": "质控审核",
    "sample_linkage": "样本关联",
    "transport_mgmt": "运输管理",
    "drug_mgmt": "药物管理",
    "antibody_mgmt": "抗体管理",
    "unified_search": "综合查询",
    "wax_block_mgmt": "蜡块管理",
    "pipeline": "流水线",
    "pipeline_mgmt": "流水线管理",
    "outbound_mgmt": "出库管理",
    "container_mgmt": "容器管理",
    "stock_audit": "出入库审核",
    "shortcut_workstation": "快捷工作台",
    "box_mgmt": "盒子管理",
    "sampling_standard": "采集标准",
    "user_mgmt": "用户管理",
    "role_mgmt": "角色管理",
    "dict_mgmt": "字典值管理",
    "field_config": "字段配置",
    "op_log": "操作日志",
    "automated_dashboard": "自动化看板",
}


HOME_LAYOUT_LABEL = "首页/版式1"
LABEL_TO_ID = {v: k for k, v in ID_TO_LABEL.items()}


def _get_ui_config() -> Optional[Dict[str, Any]]:
    try:
        root = str(Path(__file__).resolve().parents[3])
        if root not in sys.path:
            sys.path.insert(0, root)
        from bio9_license import LicenseGuard
        return LicenseGuard().get_ui_config()
    except Exception:
        return None


def get_licensed_module_ids() -> Optional[Set[str]]:
    """当前授权允许的模块英文 id。None 表示不限制；空集合表示一个都不授权。"""
    ui = _get_ui_config()
    if ui is None or not isinstance(ui, dict):
        return None
    modules = ui.get("modules")
    if modules is None:
        return None
    if modules == "*" or modules == ["*"]:
        return None
    if isinstance(modules, str):
        modules = [modules]
    if not isinstance(modules, list):
        return None
    ids: Set[str] = set()
    for raw in modules:
        name = str(raw).strip()
        if not name:
            continue
        if name == "*":
            return None
        if name in ID_TO_LABEL:
            ids.add(name)
        elif name in LABEL_TO_ID:
            ids.add(LABEL_TO_ID[name])
    ids.update(ALWAYS_KEEP_MODULE_IDS)
    return ids


def filter_module_ids(ids: Iterable[str]) -> List[str]:
    """角色权限 ∩ 授权上限。授权未限制时原样返回。home 始终保留。"""
    kept = [str(x).strip() for x in ids if str(x).strip()]
    allowed = get_licensed_module_ids()
    if allowed is None:
        return kept
    return [x for x in kept if x in allowed or x in ALWAYS_KEEP_MODULE_IDS]


def prune_permission_tree(tree: Any) -> Any:
    """权限树只保留授权内的叶子，不写回数据库。"""
    allowed = get_licensed_module_ids()
    if allowed is None:
        return tree

    def walk(node: Any) -> Any:
        if isinstance(node, dict):
            children = node.get("children")
            if isinstance(children, list) and children:
                new_children = [walk(ch) for ch in children]
                new_children = [c for c in new_children if c is not None]
                if not new_children:
                    return None
                new = dict(node)
                new["children"] = new_children
                return new
            nid = str(node.get("id") or "").strip()
            if nid and nid not in allowed and nid not in ALWAYS_KEEP_MODULE_IDS:
                return None
            return node
        if isinstance(node, list):
            return [x for x in (walk(ch) for ch in node) if x is not None]
        return node

    return walk(tree)


def modules_as_legacy_labels(modules: Any) -> Any:
    if not isinstance(modules, list):
        return modules
    out: List[str] = []
    seen = set()
    for raw in modules:
        if not isinstance(raw, str):
            continue
        name = raw.strip()
        if not name:
            continue
        label = ID_TO_LABEL.get(name, name)
        if label not in seen:
            seen.add(label)
            out.append(label)
    # 前端靠该名称进入首页，授权列表里没有时补上
    if HOME_LAYOUT_LABEL not in seen:
        out.insert(0, HOME_LAYOUT_LABEL)
    return out


def features_with_legacy_module_labels(features: Any) -> Any:
    if not isinstance(features, dict):
        return features
    ui_config = features.get("ui_config")
    if not isinstance(ui_config, dict):
        return features
    mapped = dict(features)
    mapped_ui = dict(ui_config)
    mapped_ui["modules"] = modules_as_legacy_labels(ui_config.get("modules"))
    mapped["ui_config"] = mapped_ui
    return mapped
