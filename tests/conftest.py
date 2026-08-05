from __future__ import annotations

import pytest

from information_agent.selection import SelectedEvidence


class TestRelevanceSelector:
    """隔离采集类测试中的外部 LLM，语义筛选专用测试另行覆盖真实解析器。"""

    def select(self, topic, items, *, limit, timeout):
        topic_text = topic.casefold()
        selected = []
        seen_urls = set()
        for item in items:
            if item.source_url in seen_urls:
                continue
            if topic_text not in f"{item.title}\n{item.content}".casefold():
                continue
            seen_urls.add(item.source_url)
            selected.append(item)
        return [
            SelectedEvidence(article=item, evidence_id=index)
            for index, item in enumerate(selected[:limit], start=1)
        ]


@pytest.fixture(autouse=True)
def patch_collection_selector(monkeypatch):
    import information_agent.orchestration.collection as collection_module

    real_select = collection_module.select_evidence

    def select_for_test(topic, items, *, limit, timeout, selector=None):
        if selector is not None:
            return real_select(topic, items, limit=limit, timeout=timeout, selector=selector)
        return TestRelevanceSelector().select(topic, items, limit=limit, timeout=timeout)

    monkeypatch.setattr(collection_module, "select_evidence", select_for_test)
