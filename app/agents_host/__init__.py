from __future__ import annotations

import logging
import sys
from pathlib import Path
from app.core.settings import settings

logger = logging.getLogger(__name__)

_runtime = None


def is_enabled() -> bool:
    return bool(getattr(settings, "agent_enabled", False))


def _ensure_organ_agent_importable() -> None:
    """开发态：工作区根目录加入 sys.path，以便 import organ_agent。"""
    host_file = Path(__file__).resolve()
    # app/agents_host -> Bio9_organ -> 工作区
    workspace = host_file.parents[3]
    organ_pkg = workspace / "organ_agent"
    if organ_pkg.is_dir():
        root = str(workspace)
        if root not in sys.path:
            sys.path.insert(0, root)
        return
    # 打包或 organ_agent 与 Bio9_organ 同级的兜底
    sibling = host_file.parents[2].parent / "organ_agent"
    if sibling.is_dir():
        root = str(sibling.parent)
        if root not in sys.path:
            sys.path.insert(0, root)


def _specs_dir() -> Path:
    host_file = Path(__file__).resolve()
    workspace = host_file.parents[3]
    candidate = workspace / "docs" / "agent-specs"
    if candidate.is_dir():
        return candidate
    return host_file.parents[2] / "docs" / "agent-specs"


def get_runtime():
    global _runtime
    if _runtime is not None:
        return _runtime
    if not is_enabled():
        raise RuntimeError("智能体未启用")
    _ensure_organ_agent_importable()
    from organ_agent import Runtime
    from organ_agent.extractors import build_extractor
    from organ_agent.llm import LlmConfig

    from .tools import OrganHostTools

    data_dir = Path(settings.agent_data_dir)
    data_dir.mkdir(parents=True, exist_ok=True)
    llm = LlmConfig(
        api_key=settings.agent_llm_api_key,
        base_url=settings.agent_llm_base_url,
        model=settings.agent_llm_model,
        timeout=float(settings.agent_llm_timeout),
    )
    extractor = build_extractor(kind=settings.agent_extractor, llm=llm)
    _runtime = Runtime(
        data_dir=data_dir,
        specs_dir=_specs_dir(),
        tools=OrganHostTools(),
        extractor=extractor,
    )
    logger.info(
        "organ_agent Runtime 已初始化 data_dir=%s extractor=%s model=%s",
        data_dir,
        type(extractor).__name__,
        settings.agent_llm_model if llm.enabled() else "(none)",
    )
    return _runtime


def mount_agent(app) -> None:
    if not is_enabled():
        return
    from app.routers.agent import router

    app.include_router(router)
    logger.info("已挂载 /agent 路由")
