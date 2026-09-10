"""Browser page text and content extraction capability."""

from __future__ import annotations

import logging
from typing import Any

from avi.browser.controller import BrowserController
from avi.browser.extractor import (
    ExtractedPageContent,
    fetch_and_extract_url,
)
from avi.capabilities.browser.navigate import sanitize_and_validate_url
from avi.capabilities.models import (
    BaseCapability,
    CapabilityResult,
    DataClassification,
    ExecutionStatus,
)
from avi.safety.models import ActionCategory

logger = logging.getLogger(__name__)


class BrowserExtractCapability(BaseCapability):
    """Extract readable text content, title, headings, and links from a webpage."""

    name = "browser.extract"
    description = (
        "Extract clean, readable text content, title, headings, and links from a specified "
        "URL or the currently active browser page."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "url": {
                "type": "string",
                "description": "The URL to extract content from. If omitted, the currently active page is read.",
            },
            "max_length": {
                "type": "integer",
                "description": "Maximum character length of extracted text content (default 4000).",
                "default": 4000,
            },
            "include_links": {
                "type": "boolean",
                "description": "Whether to include extracted hyperlinks.",
                "default": True,
            },
        },
    }
    risk_category = ActionCategory.READ_ONLY
    data_classification = DataClassification.LOCAL_ONLY
    requires_confirmation = False

    def __init__(self, controller: BrowserController | None = None) -> None:
        self.controller = controller or BrowserController()
        self.tags = ("browser", "web", "extract", "read", "scrape", "dom")
        self.aliases = [
            "browser.read",
            "read_page",
            "extract_page",
            "browser.read_page",
            "get_page_text",
        ]

    def execute(self, **kwargs: Any) -> CapabilityResult:
        """Execute page text and content extraction."""
        raw_url = kwargs.get("url")
        max_length = int(kwargs.get("max_length", 4000))
        include_links = bool(kwargs.get("include_links", True))

        target_url = None
        if raw_url:
            try:
                target_url = sanitize_and_validate_url(str(raw_url))
            except ValueError as val_err:
                return CapabilityResult(
                    success=False,
                    status=ExecutionStatus.FAILED,
                    error=str(val_err),
                    message=f"Invalid URL: {val_err}",
                )
        else:
            # Check currently observed state
            state = self.controller.observe()
            if state.url:
                target_url = state.url
            elif self.controller._last_navigated_url:
                target_url = self.controller._last_navigated_url

        # Check if live CDP tab is available and can provide live DOM text
        if self.controller.cdp.is_available() and (
            not target_url or (self.controller.observe().url == target_url)
        ):
            try:
                js_extract = (
                    "(() => ({"
                    "  url: window.location.href,"
                    "  title: document.title,"
                    "  text: (document.body ? document.body.innerText : ''),"
                    "  headings: Array.from(document.querySelectorAll('h1, h2, h3'))"
                    "               .map(h => h.innerText.trim()).filter(Boolean).slice(0, 15),"
                    "  links: Array.from(document.querySelectorAll('a[href]'))"
                    "              .map(a => ({text: a.innerText.trim(), href: a.href}))"
                    "              .filter(l => l.text && !l.href.startsWith('javascript:'))"
                    "              .slice(0, 20)"
                    "}))()"
                )
                res = self.controller.cdp.evaluate(js_extract)
                if isinstance(res, dict) and res.get("text"):
                    extracted_text = res.get("text", "")
                    is_truncated = False
                    if len(extracted_text) > max_length:
                        extracted_text = extracted_text[:max_length] + " ... [TRUNCATED]"
                        is_truncated = True

                    content = ExtractedPageContent(
                        url=res.get("url") or target_url or "",
                        title=res.get("title", ""),
                        text=extracted_text,
                        headings=res.get("headings", []),
                        links=res.get("links", []) if include_links else [],
                        is_truncated=is_truncated,
                    )
                    return CapabilityResult(
                        success=True,
                        status=ExecutionStatus.SUCCESS,
                        message=f"Extracted {len(content.text)} characters from live page '{content.title}'",
                        data=content.to_dict(),
                    )
            except Exception as cdp_err:
                logger.debug("Live CDP extraction failed, falling back to HTTP: %s", cdp_err)

        if not target_url:
            return CapabilityResult(
                success=False,
                status=ExecutionStatus.FAILED,
                error="No active page found and no URL was specified.",
                message="Cannot extract content: no URL provided and no browser page is open.",
            )

        try:
            content = fetch_and_extract_url(
                target_url,
                max_length=max_length,
                include_links=include_links,
            )
            return CapabilityResult(
                success=True,
                status=ExecutionStatus.SUCCESS,
                message=f"Extracted {len(content.text)} characters from '{content.title}' ({target_url})",
                data=content.to_dict(),
            )
        except Exception as fetch_err:
            return CapabilityResult(
                success=False,
                status=ExecutionStatus.FAILED,
                error=str(fetch_err),
                message=f"Failed to fetch and extract content from {target_url}: {fetch_err}",
            )
