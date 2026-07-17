import os
import sys
from typing import Any, Awaitable, Callable

import logging

RunResearchTask = Callable[..., Awaitable[Any]]

logger = logging.getLogger(__name__)


def _ensure_repo_root_on_path() -> None:
    """Ensure top-level repo root is importable for multi-agent modules."""
    logger.info("▶ _ensure_repo_root_on_path — 确保顶层repo根目录可导入多智能体模块")
    repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
    if repo_root not in sys.path:
        sys.path.insert(0, repo_root)


def _resolve_run_research_task() -> RunResearchTask:
    logger.info("▶ _resolve_run_research_task — 解析并返回多智能体研究任务函数")
    _ensure_repo_root_on_path()

    try:
        from multi_agents.main import run_research_task
        return run_research_task
    except Exception:
        try:
            from multi_agents.ag2.main import run_research_task
            return run_research_task
        except Exception as ag2_error:
            raise ImportError(
                "Could not import run_research_task from multi_agents or multi_agents/ag2"
            ) from ag2_error


async def run_multi_agent_task(*args, **kwargs) -> Any:
    logger.info("▶ run_multi_agent_task — 运行多智能体研究任务 | 入参: task=%s, report_type=%s", 
                 args[0] if len(args) > 0 else kwargs.get('query', 'N/A'),
                 kwargs.get('report_type', 'N/A'))
    run_research_task = _resolve_run_research_task()
    return await run_research_task(*args, **kwargs)
