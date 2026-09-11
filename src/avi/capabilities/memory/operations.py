"""Memory storage and recall capabilities for persistent agent intelligence."""

from typing import Any

from avi.capabilities.models import (
    BaseCapability,
    CapabilityCategory,
    CapabilityResult,
    DataClassification,
    ExecutionStatus,
)
from avi.memory.manager import MemoryManager
from avi.safety.models import ActionCategory
from avi.storage.database import Database


class RememberCapability(BaseCapability):
    """Store an explicit user preference, fact, project location, or workflow convention."""

    name = "memory.remember"
    description = "Remember a user preference, folder location, project path, or fact for future tasks."
    category = CapabilityCategory.MEMORY
    input_schema = {
        "type": "object",
        "properties": {
            "content": {
                "type": "string",
                "description": "The fact, preference, or convention to remember (e.g. 'My AVI project is in ~/projects/avi')",
            },
            "category": {
                "type": "string",
                "description": "Category: 'preference', 'project', 'directory', 'workflow', or 'general'",
                "default": "general",
            },
            "key": {
                "type": "string",
                "description": "Optional specific key name (e.g. 'avi_project', 'preferred_browser')",
            },
        },
        "required": ["content"],
    }
    risk_category = ActionCategory.READ_ONLY
    data_classification = DataClassification.LOCAL_ONLY
    requires_confirmation = False
    side_effects = True
    supports_observation = True
    tags = ("memory", "remember", "preference", "fact", "save")

    def __init__(
        self,
        manager: MemoryManager | None = None,
        database: Database | None = None,
        retriever: Any | None = None,
    ) -> None:
        self.db = database or Database()
        self.manager = manager or retriever or MemoryManager(database=self.db)

    def execute(self, content: str = "", category: str = "general", key: str = "", **kwargs: Any) -> CapabilityResult:
        text = (content or kwargs.get("fact") or kwargs.get("text") or "").strip()
        if not text:
            return CapabilityResult(
                success=False,
                status=ExecutionStatus.FAILED,
                error="Content parameter is required to remember a fact.",
                message="Cannot remember empty content.",
            )

        metadata: dict[str, Any] = {}
        if key:
            metadata["key"] = key

        try:
            if hasattr(self.manager, "remember"):
                mem = self.manager.remember(
                    content=text,
                    category=category or "general",
                    metadata=metadata,
                )
                mem_id = getattr(mem, "id", "mem_1")
                mem_content = getattr(mem, "content", text)
                mem_cat = getattr(mem, "category", category)
            else:
                mem_id = self.manager.store(
                    content=text,
                    category=category or "general",
                    metadata=metadata,
                )
                mem_content = text
                mem_cat = category or "general"

            return CapabilityResult(
                success=True,
                status=ExecutionStatus.SUCCESS,
                data={"memory_id": mem_id, "content": mem_content, "category": mem_cat},
                message=f"I've remembered: '{text}'.",
                classification=self.data_classification,
            )
        except Exception as err:
            return CapabilityResult(
                success=False,
                status=ExecutionStatus.FAILED,
                error=str(err),
                message=f"Failed to save memory: {err}",
            )


class RecallCapability(BaseCapability):
    """Search and retrieve stored preferences, project paths, and memories."""

    name = "memory.recall"
    description = "Search and retrieve stored user preferences, project paths, and memories."
    category = CapabilityCategory.MEMORY
    input_schema = {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Search keyword or phrase to recall relevant memories",
            },
            "category": {
                "type": "string",
                "description": "Optional category filter",
            },
            "limit": {
                "type": "integer",
                "description": "Maximum number of memories to return (default 5)",
                "default": 5,
            },
        },
        "required": ["query"],
    }
    risk_category = ActionCategory.READ_ONLY
    data_classification = DataClassification.LOCAL_ONLY
    requires_confirmation = False
    side_effects = False
    supports_observation = True
    tags = ("memory", "recall", "search", "preferences")

    def __init__(
        self,
        manager: MemoryManager | None = None,
        database: Database | None = None,
        retriever: Any | None = None,
    ) -> None:
        self.db = database or Database()
        self.manager = manager or retriever or MemoryManager(database=self.db)

    def execute(self, query: str = "", category: str | None = None, limit: int = 5, **kwargs: Any) -> CapabilityResult:
        search_q = (query or kwargs.get("key") or kwargs.get("text") or "").strip()
        if not search_q:
            return CapabilityResult(
                success=False,
                status=ExecutionStatus.FAILED,
                error="Query parameter is required to search memories.",
                message="Query parameter is required.",
            )

        try:
            if hasattr(self.manager, "recall"):
                memories = self.manager.recall(
                    query=search_q,
                    category=category or None,
                    limit=max(1, min(20, int(limit))),
                )
            else:
                memories = self.manager.retrieve(
                    query=search_q,
                    limit=max(1, min(20, int(limit))),
                )
            count = len(memories)
            if count == 0:
                return CapabilityResult(
                    success=True,
                    status=ExecutionStatus.SUCCESS,
                    data={"memories": [], "count": 0},
                    message=f"No memories found matching '{search_q}'.",
                )

            formatted_memories = []
            for m in memories:
                if hasattr(m, "to_dict"):
                    formatted_memories.append(m.to_dict())
                elif isinstance(m, dict):
                    formatted_memories.append(m)
                else:
                    formatted_memories.append({
                        "id": getattr(m, "memory_id", getattr(m, "id", "")),
                        "text": getattr(m, "text", getattr(m, "content", str(m))),
                        "category": getattr(m, "category", "general"),
                    })

            top_content = ""
            if memories:
                top_m = memories[0]
                top_content = getattr(top_m, "content", getattr(top_m, "text", str(top_m)))

            summary = "; ".join(f"'{getattr(m, 'content', getattr(m, 'text', str(m)))}'" for m in memories[:3])
            return CapabilityResult(
                success=True,
                status=ExecutionStatus.SUCCESS,
                data={
                    "memories": formatted_memories,
                    "count": count,
                    "top_content": top_content,
                },
                message=f"Found {count} memory/preference item(s): {summary}.",
            )
        except Exception as err:
            return CapabilityResult(
                success=False,
                status=ExecutionStatus.FAILED,
                error=str(err),
                message=f"Failed to recall memories: {err}",
            )
