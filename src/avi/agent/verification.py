"""Verification and state change detection subsystem for agent execution."""

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from avi.capabilities.models import CapabilityResult

logger = logging.getLogger("avi.agent.verification")


@dataclass
class StateChangeResult:
    """Outcome of evaluating environmental state change before and after an action."""

    changed: bool
    description: str
    details: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "changed": self.changed,
            "description": self.description,
            "details": self.details,
        }


class StateChangeDetector:
    """Detects whether an environment state change actually occurred across an action."""

    READ_ONLY_CAPABILITIES = {
        "browser.observe",
        "browser.state",
        "observe_browser",
        "browser.extract",
        "browser.read",
        "read_page",
        "filesystem.search",
        "desktop.screenshot",
        "desktop.get_clipboard",
    }

    def detect_change(
        self,
        capability_name: str,
        arguments: dict[str, Any],
        result: CapabilityResult,
        pre_observation: dict[str, Any] | None = None,
        post_observation: dict[str, Any] | None = None,
    ) -> StateChangeResult:
        """Compare before and after observations to verify real environmental effect."""
        cap = capability_name.lower().strip()

        # 1. Read-only capabilities do not mutate environment; success with data is valid
        if cap in self.READ_ONLY_CAPABILITIES:
            return StateChangeResult(
                changed=True,
                description=f"Read-only capability '{cap}' succeeded without mutation.",
                details={"read_only": True},
            )

        # 2. Browser navigation
        if "navigate" in cap or "open_url" in cap:
            target_url = arguments.get("url", "")
            pre_url = (pre_observation or {}).get("url", "")
            post_url = (post_observation or {}).get("url", "") or (result.data or {}).get("url", "")
            if post_url and (post_url != pre_url or target_url in post_url):
                return StateChangeResult(
                    changed=True,
                    description=f"Navigation changed URL to {post_url}",
                    details={"pre_url": pre_url, "post_url": post_url},
                )
            if result.success and (result.data or {}).get("url"):
                return StateChangeResult(
                    changed=True,
                    description="Navigation completed successfully.",
                    details={"url": (result.data or {}).get("url")},
                )

        # 3. Browser scrolling
        if "scroll" in cap:
            pre_vp = (pre_observation or {}).get("viewport", {})
            post_vp = (post_observation or {}).get("viewport", {}) or (result.data or {}).get(
                "viewport", {}
            )
            pre_y = pre_vp.get("scroll_y", 0)
            post_y = post_vp.get("scroll_y", 0)
            if post_y != pre_y or (result.data or {}).get("direction"):
                return StateChangeResult(
                    changed=True,
                    description=f"Scroll altered viewport position (y: {pre_y} -> {post_y})",
                    details={"pre_y": pre_y, "post_y": post_y},
                )

        # 4. Browser typing / input
        if "type" in cap or "input" in cap:
            typed_text = arguments.get("text", "")
            if result.success:
                return StateChangeResult(
                    changed=True,
                    description=f"Typed input '{typed_text[:20]}' into element.",
                    details={"typed": typed_text},
                )

        # 5. Browser click
        if "click" in cap:
            pre_url = (pre_observation or {}).get("url", "")
            post_url = (post_observation or {}).get("url", "") or (result.data or {}).get(
                "observation", {}
            ).get("url", "")
            pre_count = len((pre_observation or {}).get("interactive_elements", []))
            post_count = len((post_observation or {}).get("interactive_elements", []))

            # Click changed URL or changed DOM interactive elements
            if (post_url and pre_url and post_url != pre_url) or (
                post_count != pre_count and pre_count > 0
            ):
                return StateChangeResult(
                    changed=True,
                    description="Click mutated page state or triggered navigation.",
                    details={
                        "pre_url": pre_url,
                        "post_url": post_url,
                        "elements_delta": post_count - pre_count,
                    },
                )
            if result.success:
                return StateChangeResult(
                    changed=True,
                    description="Click dispatched successfully.",
                    details={"clicked": True},
                )

        # 6. Filesystem operations (move, copy, write, delete, create_directory)
        if "filesystem" in cap or "file" in cap:
            target_path = (
                arguments.get("destination") or arguments.get("path") or arguments.get("target")
            )
            if target_path:
                try:
                    p = Path(target_path).expanduser()
                    if "delete" in cap or "trash" in cap:
                        if not p.exists():
                            return StateChangeResult(
                                changed=True,
                                description=f"Target {target_path} was removed.",
                                details={"path": str(p)},
                            )
                    elif p.exists():
                        return StateChangeResult(
                            changed=True,
                            description=f"Target {target_path} exists and is verified.",
                            details={"path": str(p)},
                        )
                except Exception:
                    pass

        # 7. Desktop clipboard
        if "clipboard" in cap and "set" in cap:
            text = arguments.get("text", "")
            return StateChangeResult(
                changed=True,
                description="Clipboard content updated.",
                details={"text": text},
            )

        # Default fallback: check result success
        return StateChangeResult(
            changed=result.success,
            description="Capability executed with success."
            if result.success
            else "Capability failed.",
            details={"success": result.success},
        )
