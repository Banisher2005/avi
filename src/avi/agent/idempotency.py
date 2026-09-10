"""Idempotency checking for agent actions to prevent duplicate environmental mutations."""

import logging
from pathlib import Path
from typing import Any

from avi.capabilities.models import CapabilityResult, ExecutionStatus

logger = logging.getLogger("avi.agent.idempotency")


class IdempotencyChecker:
    """Detects if an action's desired state is already satisfied before execution."""

    def check_already_satisfied(
        self,
        capability_name: str,
        arguments: dict[str, Any],
        current_observation: dict[str, Any] | None = None,
    ) -> CapabilityResult | None:
        """Check if capability can be safely short-circuited as already satisfied.

        Returns CapabilityResult if already satisfied, or None if action needs execution.
        """
        cap = capability_name.lower().strip()
        current_observation = current_observation or {}

        # 1. Directory creation
        if "create_directory" in cap or "mkdir" in cap:
            path = arguments.get("path") or arguments.get("destination")
            if path:
                p = Path(path).expanduser()
                if p.is_dir():
                    logger.info("Idempotency: directory '%s' already exists.", path)
                    return CapabilityResult(
                        success=True,
                        status=ExecutionStatus.SUCCESS,
                        data={"path": str(p), "already_satisfied": True},
                        message=f"Directory '{path}' already exists.",
                    )

        # 2. File writing with identical content
        if "write" in cap and "file" in cap:
            path = arguments.get("path")
            content = arguments.get("content")
            if path and content is not None:
                p = Path(path).expanduser()
                if p.is_file():
                    try:
                        if p.read_text(encoding="utf-8") == content:
                            logger.info("Idempotency: file '%s' already has target content.", path)
                            return CapabilityResult(
                                success=True,
                                status=ExecutionStatus.SUCCESS,
                                data={"path": str(p), "already_satisfied": True},
                                message=f"File '{path}' already has identical content.",
                            )
                    except Exception:
                        pass

        # 3. File move where destination already exists and source is gone
        if "move" in cap and "file" in cap:
            source = arguments.get("source") or arguments.get("path")
            dest = arguments.get("destination") or arguments.get("target")
            if source and dest:
                src_p = Path(source).expanduser()
                dest_p = Path(dest).expanduser()
                if dest_p.exists() and not src_p.exists():
                    logger.info("Idempotency: '%s' is already moved to '%s'.", source, dest)
                    return CapabilityResult(
                        success=True,
                        status=ExecutionStatus.SUCCESS,
                        data={"source": str(src_p), "destination": str(dest_p), "already_satisfied": True},
                        message=f"File already moved to '{dest}'.",
                    )

        # 4. Browser URL navigation
        if "navigate" in cap or "open_url" in cap:
            target_url = arguments.get("url", "").rstrip("/")
            obs_url = current_observation.get("url", "").rstrip("/")
            if target_url and obs_url and (target_url == obs_url or obs_url.startswith(target_url)):
                logger.info("Idempotency: browser already at target URL '%s'.", obs_url)
                return CapabilityResult(
                    success=True,
                    status=ExecutionStatus.SUCCESS,
                    data={"url": obs_url, "already_satisfied": True},
                    message=f"Browser already at {obs_url}",
                )

        return None
