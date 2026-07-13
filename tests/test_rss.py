from information_agent.collection.rss import _plain_text


def test_plain_text_removes_html_and_decodes_entities() -> None:
    assert _plain_text("<p>Agent &amp; RSS</p>") == "Agent & RSS"
