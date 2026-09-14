"""Lightweight local usage history and frequency ranking for the Command Palette."""

from __future__ import annotations

import json
import logging
import time
from dataclasses import asdict, dataclass
from pathlib import Path

logger = logging.getLogger("avi.commands.history")

DEFAULT_HISTORY_FILE = Path.home() / ".config" / "avi" / "palette_usage.json"
MAX_HISTORY_ENTRIES = 100


@dataclass
class UsageRecord:
    """Frequency and recency tracking record for a specific command."""

    command_id: str
    count: int = 1
    last_used: float = 0.0


class UsageHistory:
    """Tracks local execution frequency and recency to boost relevant command palette results."""

    _instance: UsageHistory | None = None

    def __init__(self, history_file: Path | None = None) -> None:
        self.history_file = history_file or DEFAULT_HISTORY_FILE
        self._records: dict[str, UsageRecord] = {}
        self._loaded = False

    @classmethod
    def get_instance(cls) -> UsageHistory:
        """Singleton accessor for usage history tracker."""
        if cls._instance is None:
            cls._instance = UsageHistory()
        return cls._instance

    def _load(self) -> None:
        """Load history from disk safely."""
        if self._loaded:
            return
        self._loaded = True

        if not self.history_file.is_file():
            return

        try:
            with open(self.history_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                for k, v in data.items():
                    if isinstance(v, dict):
                        self._records[k] = UsageRecord(
                            command_id=v.get("command_id", k),
                            count=int(v.get("count", 1)),
                            last_used=float(v.get("last_used", 0.0)),
                        )
        except Exception as exc:
            logger.debug("Failed to load palette usage history: %s", exc)

    def _save(self) -> None:
        """Persist bounded history to disk safely."""
        try:
            self.history_file.parent.mkdir(parents=True, exist_ok=True)
            # Sort by last_used descending and truncate to MAX_HISTORY_ENTRIES
            sorted_items = sorted(
                self._records.items(),
                key=lambda item: item[1].last_used,
                reverse=True,
            )[:MAX_HISTORY_ENTRIES]
            data = {k: asdict(v) for k, v in sorted_items}
            with open(self.history_file, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
        except Exception as exc:
            logger.debug("Failed to save palette usage history: %s", exc)

    def record(self, command_id: str) -> None:
        """Record an execution of a command to bump its frequency and recency."""
        self._load()
        clean_id = command_id.strip().lower()
        now = time.time()
        if clean_id in self._records:
            rec = self._records[clean_id]
            rec.count += 1
            rec.last_used = now
        else:
            self._records[clean_id] = UsageRecord(
                command_id=clean_id,
                count=1,
                last_used=now,
            )
        self._save()

    def get_score_boost(self, command_id: str) -> float:
        """Calculate a bounded score boost (0.0 to 0.15) for a command based on past usage."""
        self._load()
        clean_id = command_id.strip().lower()
        if clean_id not in self._records:
            return 0.0

        rec = self._records[clean_id]
        now = time.time()
        elapsed_hours = (now - rec.last_used) / 3600.0

        # Frequency factor (up to 0.08 for 10+ uses)
        freq_factor = min(0.08, rec.count * 0.008)

        # Recency factor: decays over 7 days (168 hours)
        recency_factor = max(0.0, 0.07 * (1.0 - min(1.0, elapsed_hours / 168.0)))

        return round(freq_factor + recency_factor, 4)

    def clear(self) -> None:
        """Clear all usage records and remove persisted file."""
        self._records.clear()
        self._loaded = True
        try:
            if self.history_file.is_file():
                self.history_file.unlink()
        except Exception as exc:
            logger.debug("Failed to delete usage history file: %s", exc)
