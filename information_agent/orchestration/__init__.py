"""信息工作流与受限 Agent 编排。"""

from typing import Any

__all__ = [
    "AgentTaskManager",
    "ArticleResearchTaskManager",
    "agent_run",
    "collect",
    "plan",
    "run",
    "search",
]


def __getattr__(name: str) -> Any:
    if name == "AgentTaskManager":
        from .agent_tasks import AgentTaskManager

        return AgentTaskManager
    if name == "ArticleResearchTaskManager":
        from .article_research_tasks import ArticleResearchTaskManager

        return ArticleResearchTaskManager
    if name == "agent_run":
        from .agent_workflow import agent_run

        return agent_run
    if name == "collect":
        from .collection import collect

        return collect
    if name == "run":
        from .workflow import run

        return run
    if name == "plan":
        from .planning import plan

        return plan
    if name == "search":
        from .search_workflow import search

        return search
    raise AttributeError(name)
