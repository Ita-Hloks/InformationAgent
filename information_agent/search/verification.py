from __future__ import annotations

from urllib.parse import urlparse

from ..investigation import QuestionKind, SearchPlan, SearchQuery
from .hosted import HostedSearchAnswerer
from .models import SearchAnswer, SearchAnswerStatus
from .service import SearchAnswerer

VERIFICATION_FAILURE_ANSWER = "未找到 Python 官方文档首页的可验证官方来源。"

_VERIFICATION_PLAN = SearchPlan(
    evidence_id=0,
    trigger_quote="联网搜索连通性验证",
    question="Python 官方文档的首页是什么？",
    kind=QuestionKind.ATTRIBUTION_CLAIM,
    priority=1,
    queries=(
        SearchQuery(
            query='site:docs.python.org/3 "Python documentation" homepage',
            purpose="验证联网搜索请求、最终答案和 Python 官方来源返回",
        ),
    ),
)


def verify_connection(timeout: float, answerer: SearchAnswerer | None = None) -> SearchAnswer:
    active_answerer = answerer or HostedSearchAnswerer()
    result = active_answerer.answer(_VERIFICATION_PLAN, timeout)
    if result.status is SearchAnswerStatus.ANSWERED and _has_official_python_source(result):
        return result
    return SearchAnswer(
        evidence_id=result.evidence_id,
        question=result.question,
        answer=VERIFICATION_FAILURE_ANSWER,
        status=SearchAnswerStatus.INSUFFICIENT_EVIDENCE,
        sources=result.sources,
    )


def _has_official_python_source(result: SearchAnswer) -> bool:
    for source in result.sources:
        parsed = urlparse(source.url)
        hostname = (parsed.hostname or "").casefold()
        path_segments = [segment for segment in parsed.path.split("/") if segment]
        if hostname == "docs.python.org" and any(
            _is_python_three_version(segment) for segment in path_segments[:2]
        ):
            return True
    return False


def _is_python_three_version(segment: str) -> bool:
    parts = segment.split(".")
    return parts[0] == "3" and all(part.isdigit() for part in parts[1:])
