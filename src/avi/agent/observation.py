"""Unified observation abstraction providing structured, bounded environment snapshots for the agent loop."""

import logging
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger("avi.agent.observation")


@dataclass
class FilesystemObservation:
    """Bounded, structured snapshot of a filesystem target."""

    path: str
    exists: bool
    is_file: bool = False
    is_dir: bool = False
    size_bytes: int = 0
    modified_at: str = ""
    entries_count: int = 0
    sample_entries: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class BrowserObservation:
    """Bounded snapshot of current browser state."""

    url: str = ""
    title: str = ""
    visible_text_summary: str = ""
    interactive_elements: list[dict[str, Any]] = field(default_factory=list)
    open_tabs_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class DesktopObservation:
    """Bounded snapshot of desktop window and application state."""

    active_window: str | None = None
    open_windows: list[str] = field(default_factory=list)
    last_screenshot_path: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class SystemObservation:
    """Bounded snapshot of system metrics and resource status."""

    ram_free_gb: float | None = None
    cpu_percent: float | None = None
    running_apps: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class TaskObservation:
    """Snapshot of active task progress and artifact collection."""

    task_id: str = ""
    step_index: int = 0
    completed_steps: list[str] = field(default_factory=list)
    last_result_summary: str = ""
    artifacts: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class UnifiedObservation:
    """Unified container for multi-domain observations captured during an agent turn."""

    timestamp: str
    summary: str = ""
    filesystem: FilesystemObservation | None = None
    browser: BrowserObservation | None = None
    desktop: DesktopObservation | None = None
    system: SystemObservation | None = None
    task: TaskObservation | None = None

    def to_dict(self) -> dict[str, Any]:
        res: dict[str, Any] = {
            "timestamp": self.timestamp,
            "summary": self.summary,
        }
        if self.filesystem:
            res["filesystem"] = self.filesystem.to_dict()
        if self.browser:
            res["browser"] = self.browser.to_dict()
        if self.desktop:
            res["desktop"] = self.desktop.to_dict()
        if self.system:
            res["system"] = self.system.to_dict()
        if self.task:
            res["task"] = self.task.to_dict()
        return res

    def format_for_context(self, max_chars: int = 500) -> str:
        """Format a compact summary of this observation for LLM context inclusion."""
        parts: list[str] = []
        if self.summary:
            parts.append(self.summary)
        if self.filesystem:
            f = self.filesystem
            status = "file" if f.is_file else ("dir" if f.is_dir else "absent")
            parts.append(f"FS: {f.path} ({status}, {f.size_bytes}B)")
        if self.browser and self.browser.url:
            b = self.browser
            parts.append(f"Browser: {b.title} ({b.url})")
        if self.desktop and self.desktop.active_window:
            parts.append(f"Window: {self.desktop.active_window}")
        if self.task and self.task.artifacts:
            parts.append(f"Artifacts: {', '.join(self.task.artifacts[-3:])}")

        joined = " | ".join(parts)
        if len(joined) > max_chars:
            return joined[: max_chars - 3] + "..."
        return joined


class ObservationManager:
    """Observes and snapshots the host environment across domains in a bounded manner."""

    def __init__(self, registry: Any | None = None) -> None:
        self.registry = registry

    def observe(
        self,
        domain: str = "general",
        path: str | None = None,
        url: str | None = None,
        task_context: Any | None = None,
    ) -> UnifiedObservation:
        """Capture a multi-domain or domain-specific observation snapshot."""
        now_ts = datetime.now(timezone.utc).isoformat()
        fs_obs = self.observe_filesystem(path) if path else None
        br_obs = self.observe_browser(url=url) if url or domain == "browser" else None
        desk_obs = self.observe_desktop() if domain in ("desktop", "general") else None
        task_obs = self.observe_task(task_context) if task_context else None

        # Build human-readable summary
        summary_parts = []
        if fs_obs and fs_obs.exists:
            summary_parts.append(f"Target '{fs_obs.path}' exists ({'file' if fs_obs.is_file else 'directory'}).")
        elif fs_obs and not fs_obs.exists:
            summary_parts.append(f"Target '{fs_obs.path}' does not exist.")

        if br_obs and br_obs.url:
            summary_parts.append(f"Browser at '{br_obs.url}'.")

        if desk_obs and desk_obs.active_window:
            summary_parts.append(f"Active window: '{desk_obs.active_window}'.")

        summary = " ".join(summary_parts) or f"Environment observation at {now_ts}."

        return UnifiedObservation(
            timestamp=now_ts,
            summary=summary,
            filesystem=fs_obs,
            browser=br_obs,
            desktop=desk_obs,
            task=task_obs,
        )

    def observe_filesystem(self, target_path: str, max_samples: int = 5) -> FilesystemObservation:
        """Inspect a file or directory path safely and return bounded metadata."""
        p = Path(target_path).expanduser()
        if not p.exists():
            return FilesystemObservation(path=str(p), exists=False)

        try:
            stat = p.stat()
            mod_ts = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat()
            is_file = p.is_file()
            is_dir = p.is_dir()
            size = stat.st_size if is_file else 0
            entries_count = 0
            samples = []

            if is_dir:
                try:
                    entries = list(p.iterdir())
                    entries_count = len(entries)
                    for item in entries[:max_samples]:
                        samples.append(
                            {
                                "name": item.name,
                                "is_file": item.is_file(),
                                "size": item.stat().st_size if item.is_file() else 0,
                            }
                        )
                except PermissionError:
                    pass

            return FilesystemObservation(
                path=str(p),
                exists=True,
                is_file=is_file,
                is_dir=is_dir,
                size_bytes=size,
                modified_at=mod_ts,
                entries_count=entries_count,
                sample_entries=samples,
            )
        except Exception as exc:
            logger.debug("Filesystem observation error on %s: %s", target_path, exc)
            return FilesystemObservation(path=str(p), exists=True)

    def observe_browser(
        self,
        url: str | None = None,
        title: str | None = None,
        text_snippet: str | None = None,
    ) -> BrowserObservation:
        """Construct bounded observation of browser session."""
        summary = text_snippet or ""
        if len(summary) > 500:
            summary = summary[:497] + "..."
        return BrowserObservation(
            url=url or "",
            title=title or "",
            visible_text_summary=summary,
            open_tabs_count=1 if url else 0,
        )

    def observe_desktop(self) -> DesktopObservation:
        """Inspect open windows and desktop state."""
        # Safe fallback window observation
        open_wins: list[str] = []
        if self.registry:
            win_cap = self.registry.get("desktop.window_list")
            if win_cap:
                try:
                    res = win_cap.execute()
                    if res.success and isinstance(res.data, list):
                        open_wins = [
                            str(w.get("title") or w.get("app") or w)
                            for w in res.data[:8]
                            if w
                        ]
                except Exception:
                    pass

        return DesktopObservation(
            active_window=open_wins[0] if open_wins else None,
            open_windows=open_wins,
        )

    def observe_system(self) -> SystemObservation:
        """Inspect system metrics safely."""
        ram_gb = None
        try:
            import psutil
            mem = psutil.virtual_memory()
            ram_gb = round(mem.available / (1024**3), 2)
            cpu = psutil.cpu_percent(interval=None)
        except Exception:
            cpu = None

        return SystemObservation(
            ram_free_gb=ram_gb,
            cpu_percent=cpu,
        )

    def observe_task(self, context: Any) -> TaskObservation:
        """Extract structured task progress from task context."""
        t_id = getattr(context, "task_id", "")
        step_idx = getattr(context, "current_step_index", 0)
        completed = []
        if hasattr(context, "completed_steps"):
            completed = [
                s.capability_name if hasattr(s, "capability_name") else str(s)
                for s in context.completed_steps()
            ]
        artifacts = list(getattr(context, "artifacts", []))
        last_summary = getattr(context, "final_response", "")

        return TaskObservation(
            task_id=t_id,
            step_index=step_idx,
            completed_steps=completed,
            last_result_summary=last_summary,
            artifacts=artifacts,
        )
