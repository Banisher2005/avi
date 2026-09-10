"""Data models for AVI browser computer use."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any


@dataclass
class InteractiveElement:
    """Represents an interactive DOM or accessibility element with a stable observation ID."""

    element_id: int
    tag: str  # button, input, a, textarea, select, etc.
    element_type: str | None = None  # text, search, submit, checkbox, etc.
    role: str | None = None  # button, link, searchbox, textbox, etc.
    text: str = ""  # visible text or label
    placeholder: str | None = None
    name: str | None = None
    selector: str = ""  # CSS selector or unique path
    href: str | None = None
    value: str | None = None
    is_visible: bool = True
    is_disabled: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "element_id": self.element_id,
            "tag": self.tag,
            "element_type": self.element_type,
            "role": self.role,
            "text": self.text,
            "placeholder": self.placeholder,
            "name": self.name,
            "selector": self.selector,
            "href": self.href,
            "value": self.value,
            "is_visible": self.is_visible,
            "is_disabled": self.is_disabled,
            "metadata": dict(self.metadata),
        }

    def format_repr(self) -> str:
        """Compact string representation for agent reasoning."""
        tag_str = self.tag.lower()
        if self.element_type:
            tag_str += f":{self.element_type}"
        elif self.role and self.role != self.tag:
            tag_str += f":{self.role}"

        label = self.text or self.placeholder or self.name or ""
        if len(label) > 40:
            label = label[:37] + "..."
        label_part = f' "{label}"' if label else ""

        href_part = (
            f" -> {self.href}"
            if self.href and not self.href.startswith("javascript:")
            else ""
        )
        return f"[{self.element_id}] ({tag_str}){label_part}{href_part}"


@dataclass
class TabInfo:
    """Represents a browser tab."""

    tab_id: str
    title: str = ""
    url: str = ""
    is_active: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "tab_id": self.tab_id,
            "title": self.title,
            "url": self.url,
            "is_active": self.is_active,
        }


@dataclass
class BrowserState:
    """Represents the observed state of the browser, including DOM elements and tabs."""

    url: str | None = None
    title: str | None = None
    browser_name: str | None = None
    window_title: str | None = None
    is_running: bool = False
    is_active_window: bool = False
    cdp_connected: bool = False
    status: str = "idle"  # "idle", "loading", "ready", "unavailable"
    loading: bool = False
    visible_text: str = ""
    elements: list[InteractiveElement] = field(default_factory=list)
    tabs: list[TabInfo] = field(default_factory=list)
    downloads: list[dict[str, Any]] = field(default_factory=list)
    viewport: dict[str, Any] = field(default_factory=dict)
    tab_count: int = 0
    active_tab_id: str | None = None
    timestamp: float = field(default_factory=time.time)
    metadata: dict[str, Any] = field(default_factory=dict)

    def get_element(self, element_id: int | str) -> InteractiveElement | None:
        """Find element by observation-local integer or string ID."""
        try:
            # Handle formats like 12, "12", "button#12", "input#12", "#12"
            raw_id = str(element_id).strip()
            if "#" in raw_id:
                raw_id = raw_id.split("#")[-1].strip()
            target_int = int(raw_id)
            for el in self.elements:
                if el.element_id == target_int:
                    return el
        except (ValueError, TypeError):
            pass
        return None

    def find_elements(
        self,
        text: str | None = None,
        role: str | None = None,
        tag: str | None = None,
        element_type: str | None = None,
    ) -> list[InteractiveElement]:
        """Find matching elements by visible text, role, or tag."""
        matches = []
        lower_text = text.strip().lower() if text else None
        lower_role = role.strip().lower() if role else None
        lower_tag = tag.strip().lower() if tag else None
        lower_type = element_type.strip().lower() if element_type else None

        for el in self.elements:
            if lower_tag and el.tag.lower() != lower_tag:
                continue
            if lower_role and (el.role or "").lower() != lower_role:
                continue
            if lower_type and (el.element_type or "").lower() != lower_type:
                continue
            if lower_text:
                el_txt = (el.text or "").lower()
                el_ph = (el.placeholder or "").lower()
                el_name = (el.name or "").lower()
                if (
                    lower_text not in el_txt
                    and lower_text not in el_ph
                    and lower_text not in el_name
                ):
                    continue
            matches.append(el)
        return matches

    def formatted_summary(self, max_elements: int = 25, max_text_chars: int = 800) -> str:
        """Generate a compact structured summary for agent reasoning."""
        lines = []
        lines.append(f"Browser: {self.browser_name or 'unknown'} ({self.status})")
        lines.append(f"URL: {self.url or 'about:blank'}")
        lines.append(f"Title: {self.title or 'Untitled'}")

        if self.tabs:
            lines.append(f"Tabs ({len(self.tabs)}):")
            for t in self.tabs[:5]:
                active_mark = " [*]" if t.is_active else ""
                lines.append(f"  - {t.title[:40]} ({t.url[:50]}){active_mark}")

        if self.visible_text:
            v_text = self.visible_text.strip()
            if len(v_text) > max_text_chars:
                v_text = v_text[:max_text_chars] + "... [TRUNCATED]"
            lines.append(f"Page Text:\n{v_text}")

        if self.elements:
            lines.append(f"Interactive Elements ({len(self.elements)} total):")
            for el in self.elements[:max_elements]:
                lines.append(f"  {el.format_repr()}")
            if len(self.elements) > max_elements:
                lines.append(f"  ... and {len(self.elements) - max_elements} more elements.")

        if self.downloads:
            lines.append(f"Recent Downloads: {len(self.downloads)} file(s)")

        return "\n".join(lines)

    def to_dict(self) -> dict[str, Any]:
        """Convert state to serializable dictionary."""
        return {
            "url": self.url,
            "title": self.title,
            "browser_name": self.browser_name,
            "window_title": self.window_title,
            "is_running": self.is_running,
            "is_active_window": self.is_active_window,
            "cdp_connected": self.cdp_connected,
            "status": self.status,
            "loading": self.loading or (self.status == "loading"),
            "visible_text": self.visible_text,
            "elements": [el.to_dict() for el in self.elements],
            "tabs": [t.to_dict() for t in self.tabs],
            "downloads": list(self.downloads),
            "viewport": dict(self.viewport),
            "tab_count": self.tab_count or len(self.tabs),
            "active_tab_id": self.active_tab_id,
            "timestamp": self.timestamp,
            "metadata": dict(self.metadata),
        }


# Alias for BrowserState fulfilling the BrowserObservation specification
BrowserObservation = BrowserState

