from __future__ import annotations

import time
from collections.abc import Callable
from typing import Protocol

from ..analysis import LLMAnalyst, evaluate_analysis
from ..collection import fetch_feed, normalize_evidence
from ..contracts import Analysis, Evidence, Report, RunStatus
from ..processing import filter_evidence

Collector = Callable[[str, float], list[Evidence]]


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
) -> Report:
    _validate_input(topic, feeds, timeout_seconds, limit)
    deadline = time.monotonic() + timeout_seconds
    errors: list[str] = []
    collected: list[Evidence] = []

    for feed_url in feeds:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            errors.append("任务在完成 RSS 采集前超时")
            break
        try:
            collected.extend(collector(feed_url, min(15.0, remaining)))
        except Exception as exc:
            errors.append(f"{feed_url}：{exc}")

    normalized = normalize_evidence(collected)
    evidence = filter_evidence(topic, normalized, limit=limit)
    if not evidence:
        analysis = Analysis(
            summary="没有找到与主题匹配的 RSS 内容。",
            claims=[],
            uncertainties=["没有证据，因此未调用模型。"],
        )
        evaluation = evaluate_analysis(analysis, [])
        return Report(topic, RunStatus.PARTIAL, analysis, [], evaluation, errors)

    remaining = deadline - time.monotonic()
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


def _validate_input(topic: str, feeds: list[str], timeout_seconds: float, limit: int) -> None:
    if not topic.strip():
        raise ValueError("研究主题不能为空")
    if not feeds:
        raise ValueError("至少需要一个 RSS 地址")
    if timeout_seconds <= 0:
        raise ValueError("任务时限必须大于 0 秒")
    if limit <= 0:
        raise ValueError("证据数量上限必须大于 0")


def _failed_analysis(message: str) -> Analysis:
    return Analysis(summary=message, claims=[], uncertainties=[message])
