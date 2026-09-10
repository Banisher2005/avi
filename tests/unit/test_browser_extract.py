"""Unit tests for Browser Page Text and Content Extraction."""

from unittest.mock import MagicMock, patch

from avi.browser.controller import BrowserController
from avi.browser.extractor import extract_html_content
from avi.capabilities.browser.extract import BrowserExtractCapability
from avi.capabilities.models import ExecutionStatus
from avi.capabilities.registry import create_default_capability_registry

SAMPLE_HTML = """
<!DOCTYPE html>
<html>
<head>
    <title>Sample Documentation - AVI</title>
    <meta name="description" content="Official documentation for AVI computer use agent.">
    <style>body { font-family: sans-serif; }</style>
    <script>console.log("ignore me");</script>
</head>
<body>
    <header><h1>Ignore Header</h1></header>
    <main>
        <h1>AVI Browser Computer Use</h1>
        <p>AVI can now observe browser state, navigate to URLs, and read page content.</p>
        <h2>Features</h2>
        <p>Supports Chrome DevTools Protocol and zero-dependency fallbacks.</p>
        <p>Visit the <a href="/docs/guide">Online Guide</a> or <a href="https://github.com/Banisher2005/avi">GitHub Repository</a>.</p>
    </main>
</body>
</html>
"""


class TestHtmlExtraction:
    def test_extract_html_content_basic(self):
        content = extract_html_content(
            SAMPLE_HTML,
            url="https://avi.dev/docs",
            max_length=5000,
            include_links=True,
        )
        assert content.title == "Sample Documentation - AVI"
        assert content.meta_description == "Official documentation for AVI computer use agent."
        assert "AVI Browser Computer Use" in content.headings
        assert "Features" in content.headings
        assert "AVI can now observe browser state" in content.text
        assert "ignore me" not in content.text
        assert len(content.links) == 2
        assert content.links[0]["href"] == "https://avi.dev/docs/guide"
        assert content.links[1]["href"] == "https://github.com/Banisher2005/avi"
        assert content.is_truncated is False

    def test_extract_html_content_truncation(self):
        content = extract_html_content(
            SAMPLE_HTML,
            url="https://avi.dev/docs",
            max_length=50,
            include_links=False,
        )
        assert len(content.text) > 50  # includes suffix
        assert content.is_truncated is True
        assert "[TRUNCATED]" in content.text
        assert content.links == []


class TestBrowserExtractCapability:
    def test_missing_url_and_no_active_page(self):
        ctrl = BrowserController()
        cap = BrowserExtractCapability(controller=ctrl)
        res = cap.execute()
        assert res.success is False
        assert res.status == ExecutionStatus.FAILED

    def test_invalid_url_rejected(self):
        cap = BrowserExtractCapability()
        res = cap.execute(url="javascript:alert(1)")
        assert res.success is False
        assert res.status == ExecutionStatus.FAILED
        assert "disallowed" in res.error

    @patch("avi.capabilities.browser.extract.fetch_and_extract_url")
    def test_extract_explicit_url(self, mock_fetch):
        from avi.browser.extractor import ExtractedPageContent

        mock_fetch.return_value = ExtractedPageContent(
            url="https://example.com",
            title="Example Domain",
            text="This domain is for use in illustrative examples.",
            headings=["Example Domain"],
            links=[],
        )

        ctrl = BrowserController()
        cap = BrowserExtractCapability(controller=ctrl)
        res = cap.execute(url="https://example.com")

        assert res.success is True
        assert res.status == ExecutionStatus.SUCCESS
        assert res.data["title"] == "Example Domain"
        assert "Example Domain" in res.message

    def test_extract_via_cdp_live_tab(self):
        ctrl = BrowserController()
        ctrl.cdp = MagicMock()
        ctrl.cdp.is_available.return_value = True
        ctrl.cdp.evaluate.return_value = {
            "url": "https://news.ycombinator.com",
            "title": "Hacker News",
            "text": "Top stories today on tech and startups.",
            "headings": ["Hacker News"],
            "links": [{"text": "Comments", "href": "https://news.ycombinator.com/item?id=1"}],
        }

        cap = BrowserExtractCapability(controller=ctrl)
        res = cap.execute()

        assert res.success is True
        assert res.status == ExecutionStatus.SUCCESS
        assert res.data["title"] == "Hacker News"
        assert "Top stories" in res.data["text"]

    def test_registry_integration(self):
        reg = create_default_capability_registry()
        cap = reg.get("browser.extract")
        assert cap is not None
        assert reg.get("browser.read") is cap
        assert reg.get("read_page") is cap
