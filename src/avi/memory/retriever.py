"""Memory retriever abstraction for ranking, scoring, and formatting agent context."""

import logging
import re
from typing import Any, Sequence

from avi.memory.manager import MemoryManager
from avi.memory.models import Memory
from avi.storage.database import Database

logger = logging.getLogger("avi.memory.retriever")

_STOPWORDS = {
    "a", "an", "the", "in", "on", "at", "to", "for", "of", "with", "by", "from",
    "is", "are", "was", "were", "be", "been", "being", "have", "has", "had",
    "do", "does", "did", "can", "could", "will", "would", "should",
    "i", "me", "my", "myself", "we", "our", "you", "your", "it", "its",
    "what", "which", "who", "whom", "this", "that", "these", "those",
    "about", "remember", "recall", "find", "show", "tell",
}


class MemoryRetriever:
    """Retrieval and relevance ranking layer for long-term agent memory."""

    def __init__(
        self,
        database: Database | None = None,
        memory_manager: MemoryManager | None = None,
    ) -> None:
        self.db = database or (memory_manager.db if memory_manager else Database())
        self.manager = memory_manager or MemoryManager(database=self.db)

    def extract_keywords(self, text: str) -> list[str]:
        """Extract search tokens excluding common conversational stopwords."""
        clean = re.sub(r"[^\w\s]", " ", text.lower())
        tokens = [t.strip() for t in clean.split() if len(t.strip()) > 1]
        return [t for t in tokens if t not in _STOPWORDS]

    def search(
        self,
        query: str,
        category: str | None = None,
        limit: int = 5,
    ) -> list[Memory]:
        """Search and rank memories relevant to query."""
        clean_q = query.strip()
        if not clean_q:
            records = self.db.list_memories(category=category, limit=limit)
            return [Memory.from_record(r) for r in records]

        keywords = self.extract_keywords(clean_q)
        search_terms = [clean_q] + keywords

        candidates: dict[str, Memory] = {}

        # 1. Direct query search
        direct_records = self.db.search_memories(clean_q, category=category, limit=limit * 2)
        for r in direct_records:
            candidates[r.id] = Memory.from_record(r)

        # 2. Keyword searches
        for kw in keywords:
            if len(kw) >= 3:
                kw_records = self.db.search_memories(kw, category=category, limit=limit)
                for r in kw_records:
                    if r.id not in candidates:
                        candidates[r.id] = Memory.from_record(r)

        # 3. Preferences injection for common topics
        lower_q = clean_q.lower()
        if "browser" in lower_q or any(b in lower_q for b in ("chrome", "firefox", "brave", "edge", "safari")):
            pref_b = self.manager.get_preferred_browser()
            if pref_b and not any("browser" in m.content.lower() for m in candidates.values()):
                synthetic_mem = Memory(
                    id="pref_browser",
                    content=f"Preferred browser is {pref_b}.",
                    category="preference",
                    confidence=1.0,
                    metadata={"preferred_browser": pref_b},
                )
                candidates[synthetic_mem.id] = synthetic_mem

        if any(f in lower_q for f in ("folder", "directory", "project", "code", "notes")):
            # Inject relevant folder mappings if present
            for folder_type in ("projects", "notes", "documents", "downloads"):
                p = self.manager.get_preferred_folder(folder_type)
                if p and not any(folder_type in m.content.lower() for m in candidates.values()):
                    synthetic_mem = Memory(
                        id=f"pref_folder_{folder_type}",
                        content=f"Preferred {folder_type} folder is {p}.",
                        category="path",
                        confidence=1.0,
                        metadata={"folder": folder_type, "path": p},
                    )
                    candidates[synthetic_mem.id] = synthetic_mem

        # 4. Rank and truncate
        ranked = self.rank(list(candidates.values()), clean_q)
        return ranked[:limit]

    def rank(self, memories: Sequence[Memory], query: str) -> list[Memory]:
        """Score and sort memories by relevance, confidence, and recency."""
        clean_q = query.lower().strip()
        keywords = set(self.extract_keywords(clean_q))

        def score_memory(mem: Memory) -> float:
            score = 0.0
            content_lower = mem.content.lower()

            # Exact phrase match
            if clean_q in content_lower:
                score += 10.0

            # Keyword matches
            for kw in keywords:
                if kw in content_lower:
                    score += 3.0

            # Tag / metadata matches
            for val in mem.metadata.values():
                val_str = str(val).lower()
                if clean_q in val_str:
                    score += 4.0
                for kw in keywords:
                    if kw in val_str:
                        score += 2.0

            # Confidence weighting
            score *= max(0.1, mem.confidence)
            return score

        scored = [(score_memory(m), m) for m in memories]
        scored.sort(key=lambda x: x[0], reverse=True)
        return [m for _, m in scored]

    def format_context(self, memories: Sequence[Memory], max_tokens: int = 500) -> str:
        """Format memories into a concise text block for agent context injection."""
        if not memories:
            return ""

        lines = ["User Knowledge & Preferences:"]
        for m in memories:
            summary = m.content.strip()
            cat = m.category.strip()
            lines.append(f"- [{cat}] {summary}")

        return "\n".join(lines)
