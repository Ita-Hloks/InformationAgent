from __future__ import annotations

from typing import Protocol

from ..analysis import LLMAnalyst, evaluate_analysis
from ..collection import fetch_feed
from ..contracts import Analysis, Evidence, Report, RunStatus
from .collection import (
    DEFAULT_MAX_ATTEMPTS,
    DEFAULT_MAX_WORKERS,
    DEFAULT_SOURCE_TIMEOUT_SECONDS,
    Collector,
    _execute_collection,
)


class Analyst(Protocol):
    def analyze(self, topic: str, evidence: list[Evidence], timeout: float) -> Analysis: ...


def run(
    topic: str,
    feeds: list[str],
    *,
    timeout_seconds: float = 60,
    limit: int = 20,
    collector: Collector = fetch_feed,
    analyst: Analyst | None = None,
    max_workers: int = DEFAULT_MAX_WORKERS,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    source_timeout_seconds: float = DEFAULT_SOURCE_TIMEOUT_SECONDS,
) -> Report:
    execution = _execute_collection(
        topic,
        feeds,
        timeout_seconds=timeout_seconds,
        limit=limit,
        collector=collector,
        max_workers=max_workers,
        max_attempts=max_attempts,
        source_timeout_seconds=source_timeout_seconds,
    )
    collection_report = execution.report
    evidence = collection_report.articles
    errors = list(collection_report.errors)
    if not evidence:
        analysis = Analysis(
            summary="没有找到与主题匹配的 RSS 内容。",
            claims=[],
            uncertainties=["没有证据，因此未调用模型。"],
        )
        evaluation = evaluate_analysis(analysis, [])
        return Report(topic, RunStatus.PARTIAL, analysis, [], evaluation, errors)

    remaining = execution.remaining_seconds
    if remaining <= 0:
        errors.append("任务在模型分析前超时")
        analysis = _failed_analysis("已获得证据，但没有剩余时间调用模型。")
        evaluation = evaluate_analysis(analysis, evidence)
        return Report(topic, RunStatus.PARTIAL, analysis, evidence, evaluation, errors)

    try:
        active_analyst = analyst or LLMAnalyst()
        analysis = active_analyst.analyze(topic, evidence, remaining)
    except Exception as exc:
        errors.append(f"分析失败：{exc}")
        analysis = _failed_analysis("模型分析不可用，未生成事实结论。")

    status = RunStatus.COMPLETED if not errors and analysis.claims else RunStatus.PARTIAL
    evaluation = evaluate_analysis(analysis, evidence)
    return Report(topic, status, analysis, evidence, evaluation, errors)


def _failed_analysis(message: str) -> Analysis:
    return Analysis(summary=message, claims=[], uncertainties=[message])
