from __future__ import annotations

from ..investigation import QuestionKind, SearchPlan, SearchQuery
from .hosted import HostedSearchAnswerer
from .models import SearchAnswer
from .service import SearchAnswerer

_VERIFICATION_PLAN = SearchPlan(
    evidence_id=0,
    trigger_quote="联网搜索连通性验证",
    question="Python 官方文档的首页是什么？",
    kind=QuestionKind.ATTRIBUTION_CLAIM,
    priority=1,
    queries=(
        SearchQuery(
            query="Python official documentation",
            purpose="验证联网搜索请求、回答和来源返回",
        ),
    ),
)


def verify_connection(timeout: float, answerer: SearchAnswerer | None = None) -> SearchAnswer:
    active_answerer = answerer or HostedSearchAnswerer()
    return active_answerer.answer(_VERIFICATION_PLAN, timeout)
