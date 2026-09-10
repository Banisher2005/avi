"""Data models for AVI memory management."""

import uuid
from dataclasses import dataclass, field
from typing import Any

from avi.storage.models import MemoryRecord, utc_now_iso


@dataclass
class Memory:
    """A discrete unit of knowledge, preference, or learned behavior."""

    id: str = field(default_factory=lambda: f"mem_{uuid.uuid4().hex[:12]}")
    content: str = ""
    category: str = "general"
    created_at: str = field(default_factory=utc_now_iso)
    last_used_at: str = field(default_factory=utc_now_iso)
    confidence: float = 1.0
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_record(cls, record: MemoryRecord) -> "Memory":
        """Convert a low-level MemoryRecord to a high-level Memory object."""
        return cls(
            id=record.id,
            content=record.content,
            category=record.category,
            created_at=record.created_at,
            last_used_at=record.last_used_at,
            confidence=record.confidence,
            metadata=record.metadata or {},
        )

    def to_record(self) -> MemoryRecord:
        """Convert this Memory into a database MemoryRecord."""
        return MemoryRecord(
            id=self.id,
            content=self.content,
            category=self.category,
            created_at=self.created_at,
            last_used_at=self.last_used_at,
            confidence=self.confidence,
            metadata=self.metadata,
        )

    def to_dict(self) -> dict[str, Any]:
        """Convert memory to serializable dictionary."""
        return {
            "id": self.id,
            "content": self.content,
            "category": self.category,
            "created_at": self.created_at,
            "last_used_at": self.last_used_at,
            "confidence": self.confidence,
            "metadata": self.metadata,
        }

    def format_summary(self) -> str:
        """User-friendly summary of this memory."""
        cat_badge = f"[{self.category}] " if self.category != "general" else ""
        return f"{cat_badge}{self.content}"
