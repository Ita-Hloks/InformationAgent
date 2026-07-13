from __future__ import annotations

import re

from ..contracts import Analysis, Evaluation, Evidence


def _terms(text: str) -> set[str]:
    latin = set(re.findall(r"[A-Za-z0-9_-]{2,}", text.casefold()))
    chinese: set[str] = set()
    for value in re.findall(r"[\u4e00-\u9fff]{2,}", text):
        chinese.update(value[index : index + 2] for index in range(len(value) - 1))
    return latin | chinese


def evaluate_analysis(analysis: Analysis, evidence: list[Evidence]) -> Evaluation:
    if not analysis.claims:
        return Evaluation(0.0, 0.0, 0.0, ["没有可评估的分析结论"])

    evidence_by_id = {item.id: item for item in evidence if item.id is not None}
    cited_claims = 0
    citation_count = 0
    valid_citations = 0
    supported_claims = 0
    issues: list[str] = []

    for index, claim in enumerate(analysis.claims, start=1):
        citation_count += len(claim.evidence_ids)
        cited_claims += bool(claim.evidence_ids)
        valid_sources = [
            evidence_by_id[evidence_id]
            for evidence_id in claim.evidence_ids
            if evidence_id in evidence_by_id
        ]
        valid_citations += len(valid_sources)
        if len(valid_sources) != len(claim.evidence_ids):
            issues.append(f"结论 {index} 引用了不存在的证据")

        source_text = " ".join(f"{item.title} {item.content}" for item in valid_sources)
        if _terms(claim.text) & _terms(source_text):
            supported_claims += 1
        else:
            issues.append(f"结论 {index} 缺少可检测的文字支持")

    claim_count = len(analysis.claims)
    return Evaluation(
        citation_coverage=round(cited_claims / claim_count, 4),
        citation_validity=round(valid_citations / citation_count, 4) if citation_count else 0.0,
        lexical_support=round(supported_claims / claim_count, 4),
        issues=issues,
    )
