from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

from ..contracts import RunStatus
from ..selection import SelectedEvidence


class QuestionKind(StrEnum):
    QUANTITATIVE_CLAIM = "quantitative_claim"
    CAUSAL_CLAIM = "causal_claim"
    ATTRIBUTION_CLAIM = "attribution_claim"
    TIME_SENSITIVE_CLAIM = "time_sensitive_claim"


@dataclass(frozen=True, slots=True)
class SearchQuery:
    query: str
    purpose: str


@dataclass(frozen=True, slots=True)
class SearchPlan:
    evidence_id: int
    trigger_quote: str
    question: str
    kind: QuestionKind
    priority: int
    queries: tuple[SearchQuery, ...]


OPINION_PLATFORM = "bilibili"
OPINION_WINDOW_HOURS = 72


@dataclass(frozen=True, slots=True)
class ArticleSnapshotIdentity:
    """标识一次运行所绑定的文章正文快照。"""

    article_id: str
    article_snapshot_id: str
    content_hash: str

    def __post_init__(self) -> None:
        for name in ("article_id", "article_snapshot_id", "content_hash"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{name} 不能为空")

    def matches(self, other: ArticleSnapshotIdentity) -> bool:
        return self == other


@dataclass(frozen=True, slots=True)
class OpinionPlan:
    """文章争议侧栏的按需舆情检索提示，不代表已经采集到舆情。"""

    evidence_id: int
    trigger_quote: str
    question: str
    queries: tuple[SearchQuery, ...]
    platform: str = OPINION_PLATFORM
    window_hours: int = OPINION_WINDOW_HOURS

    def __post_init__(self) -> None:
        if type(self.evidence_id) is not int or self.evidence_id <= 0:
            raise ValueError("evidence_id 必须是正整数")
        if not isinstance(self.trigger_quote, str) or not self.trigger_quote.strip():
            raise ValueError("trigger_quote 不能为空")
        if not isinstance(self.question, str) or not self.question.strip():
            raise ValueError("question 不能为空")
        if not 1 <= len(self.queries) <= 2:
            raise ValueError("每个舆情提示必须包含 1 到 2 条查询")
        if self.platform != OPINION_PLATFORM:
            raise ValueError(f"舆情平台固定为 {OPINION_PLATFORM}")
        if self.window_hours != OPINION_WINDOW_HOURS:
            raise ValueError(f"舆情时间窗固定为 {OPINION_WINDOW_HOURS} 小时")


@dataclass(frozen=True, slots=True)
class PlanningResult:
    """一次 Planner 调用的可持久化结果。"""

    raw_response: str
    plans: list[SearchPlan]
    opinion_plans: list[OpinionPlan] = field(default_factory=list)


@dataclass(slots=True)
class PlanningReport:
    topic: str
    status: RunStatus
    articles: list[SelectedEvidence]
    plans: list[SearchPlan]
    errors: list[str] = field(default_factory=list)
    opinion_plans: list[OpinionPlan] = field(default_factory=list)
