"""HTML content and text extractor for browser computer use."""

from __future__ import annotations

import logging
import re
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from html.parser import HTMLParser
from typing import Any

from avi.browser.models import InteractiveElement

logger = logging.getLogger(__name__)

USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36 AVI/1.0"


@dataclass
class ExtractedPageContent:
    """Structured, bounded content extracted from a webpage."""

    url: str
    title: str = ""
    text: str = ""
    headings: list[str] = field(default_factory=list)
    links: list[dict[str, str]] = field(default_factory=list)
    elements: list[InteractiveElement] = field(default_factory=list)
    meta_description: str = ""
    is_truncated: bool = False
    status_code: int = 200

    def to_dict(self) -> dict[str, Any]:
        """Serialize extracted content to dictionary."""
        return {
            "url": self.url,
            "title": self.title,
            "text": self.text,
            "headings": self.headings,
            "links": self.links,
            "elements": [el.to_dict() for el in self.elements],
            "meta_description": self.meta_description,
            "is_truncated": self.is_truncated,
            "status_code": self.status_code,
        }


class SimpleHtmlParser(HTMLParser):
    """Parses HTML into clean readable text, headings, links, and interactive elements."""

    def __init__(self, base_url: str = "", max_links: int = 20, max_elements: int = 50) -> None:
        super().__init__()
        self.base_url = base_url
        self.max_links = max_links
        self.max_elements = max_elements
        self.title = ""
        self.meta_description = ""
        self.headings: list[str] = []
        self.links: list[dict[str, str]] = []
        self.elements: list[InteractiveElement] = []
        self.text_blocks: list[str] = []

        self._in_title = False
        self._in_heading = False
        self._current_heading_text = ""
        self._in_link = False
        self._current_link_text = ""
        self._current_link_href = ""
        self._in_button = False
        self._current_button_text = ""
        self._current_button_attrs: dict[str, str] = {}
        self._ignore_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag_lower = tag.lower()
        attr_dict = {k.lower(): (v or "") for k, v in attrs}

        if tag_lower in ("script", "style", "noscript", "svg", "header", "footer"):
            self._ignore_depth += 1
            return

        if self._ignore_depth > 0:
            return

        if tag_lower == "title":
            self._in_title = True
        elif tag_lower == "meta":
            name = attr_dict.get("name", "").lower()
            prop = attr_dict.get("property", "").lower()
            if name == "description" or prop == "og:description":
                self.meta_description = attr_dict.get("content", "").strip()
        elif tag_lower in ("h1", "h2", "h3"):
            self._in_heading = True
            self._current_heading_text = ""
        elif tag_lower == "a" and len(self.links) < self.max_links:
            href = attr_dict.get("href", "").strip()
            if href and not href.startswith(("#", "javascript:", "mailto:")):
                self._in_link = True
                self._current_link_text = ""
                self._current_link_href = urllib.parse.urljoin(self.base_url, href)
        elif tag_lower == "button" and len(self.elements) < self.max_elements:
            self._in_button = True
            self._current_button_text = ""
            self._current_button_attrs = attr_dict
        elif tag_lower in ("input", "textarea", "select") and len(self.elements) < self.max_elements:
            elem_type = attr_dict.get("type", "text").lower()
            if elem_type != "hidden":
                eid = len(self.elements) + 1
                name = attr_dict.get("name")
                ph = attr_dict.get("placeholder")
                val = attr_dict.get("value")
                label = attr_dict.get("aria-label") or ph or name or ""
                role = "searchbox" if elem_type == "search" else ("button" if elem_type in ("submit", "button") else "textbox")
                sel = f"#{attr_dict['id']}" if attr_dict.get("id") else (f"{tag_lower}[name='{name}']" if name else f"{tag_lower}[type='{elem_type}']")
                self.elements.append(
                    InteractiveElement(
                        element_id=eid,
                        tag=tag_lower,
                        element_type=elem_type,
                        role=role,
                        text=label,
                        placeholder=ph,
                        name=name,
                        selector=sel,
                        value=val,
                    )
                )

    def handle_endtag(self, tag: str) -> None:
        tag_lower = tag.lower()
        if tag_lower in ("script", "style", "noscript", "svg", "header", "footer"):
            if self._ignore_depth > 0:
                self._ignore_depth -= 1
            return

        if self._ignore_depth > 0:
            return

        if tag_lower == "title":
            self._in_title = False
        elif tag_lower in ("h1", "h2", "h3"):
            self._in_heading = False
            heading_cleaned = self._current_heading_text.strip()
            if heading_cleaned and heading_cleaned not in self.headings:
                self.headings.append(heading_cleaned)
        elif tag_lower == "a" and self._in_link:
            self._in_link = False
            link_text = self._current_link_text.strip()
            if link_text and self._current_link_href:
                self.links.append({"text": link_text, "href": self._current_link_href})
                if len(self.elements) < self.max_elements:
                    eid = len(self.elements) + 1
                    self.elements.append(
                        InteractiveElement(
                            element_id=eid,
                            tag="a",
                            role="link",
                            text=link_text,
                            href=self._current_link_href,
                            selector=f"a[href='{self._current_link_href}']",
                        )
                    )
        elif tag_lower == "button" and self._in_button:
            self._in_button = False
            btn_text = self._current_button_text.strip()
            eid = len(self.elements) + 1
            btn_id = self._current_button_attrs.get("id")
            sel = f"#{btn_id}" if btn_id else f"button"
            self.elements.append(
                InteractiveElement(
                    element_id=eid,
                    tag="button",
                    role="button",
                    text=btn_text or self._current_button_attrs.get("aria-label", ""),
                    selector=sel,
                )
            )

    def handle_data(self, data: str) -> None:
        if self._ignore_depth > 0:
            return

        clean = data.strip()
        if not clean:
            return

        if self._in_title:
            self.title += f" {clean}"
        elif self._in_heading:
            self._current_heading_text += f" {clean}"
            self.text_blocks.append(clean)
        elif self._in_link:
            self._current_link_text += f" {clean}"
            self.text_blocks.append(clean)
        elif self._in_button:
            self._current_button_text += f" {clean}"
            self.text_blocks.append(clean)
        else:
            self.text_blocks.append(clean)


def extract_html_content(
    html: str,
    url: str = "",
    max_length: int = 4000,
    include_links: bool = True,
    status_code: int = 200,
) -> ExtractedPageContent:
    """Extract bounded clean text, headings, and links from raw HTML."""
    parser = SimpleHtmlParser(base_url=url)
    parser.feed(html)

    full_text = " ".join(parser.text_blocks)
    full_text = re.sub(r"\s+", " ", full_text).strip()

    is_truncated = False
    if len(full_text) > max_length:
        full_text = full_text[:max_length] + " ... [TRUNCATED]"
        is_truncated = True

    return ExtractedPageContent(
        url=url,
        title=parser.title.strip(),
        text=full_text,
        headings=parser.headings[:15],
        links=parser.links if include_links else [],
        elements=parser.elements,
        meta_description=parser.meta_description,
        is_truncated=is_truncated,
        status_code=status_code,
    )


def fetch_and_extract_url(
    url: str,
    max_length: int = 4000,
    include_links: bool = True,
    timeout: float = 5.0,
) -> ExtractedPageContent:
    """Fetch HTML via HTTP and extract bounded readable text."""
    req = urllib.request.Request(
        url,
        headers={"User-Agent": USER_AGENT, "Accept": "text/html,application/xhtml+xml"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        status_code = getattr(resp, "status", 200)
        content_bytes = resp.read(500_000)  # Read at most 500KB to prevent memory exhaustion
        charset = resp.headers.get_content_charset() or "utf-8"
        html = content_bytes.decode(charset, errors="replace")

    return extract_html_content(
        html=html,
        url=url,
        max_length=max_length,
        include_links=include_links,
        status_code=status_code,
    )
