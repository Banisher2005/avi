"""Capability-aware tool selection and catalog filtering for agent tasks."""

import logging
import re
from typing import Any, Sequence

from avi.capabilities.registry import CapabilityRegistry
from avi.memory.models import Memory

logger = logging.getLogger("avi.agent.tool_selection")

_STOPWORDS = {
    "a", "an", "the", "in", "on", "at", "to", "for", "of", "with", "by", "from",
    "is", "are", "was", "were", "be", "been", "being", "have", "has", "had",
    "do", "does", "did", "can", "could", "will", "would", "should",
    "i", "me", "my", "myself", "we", "our", "you", "your", "it", "its",
    "please", "avi", "help", "want", "need",
}


class ToolSelector:
    """Selects and filters relevant capabilities from the catalog for a given prompt and context."""

    def __init__(
        self,
        registry: CapabilityRegistry,
        model_client: Any | None = None,
    ) -> None:
        self.registry = registry
        self.model_client = model_client

    def extract_keywords(self, text: str) -> list[str]:
        """Extract alphanumeric search tokens from text."""
        clean = re.sub(r"[^\w\s]", " ", text.lower())
        tokens = [t.strip() for t in clean.split() if len(t.strip()) > 1]
        return [t for t in tokens if t not in _STOPWORDS]

    def select_capabilities(
        self,
        prompt: str,
        memories: Sequence[Memory] = (),
        limit: int = 6,
    ) -> list[dict[str, Any]]:
        """Select top relevant capabilities matching the prompt and memory context."""
        catalog = self.registry.get_model_catalog(enabled_only=True)
        if not prompt.strip():
            return catalog[:limit]

        scored = self.score_catalog(prompt, catalog, memories=memories)
        # Sort descending by score
        scored.sort(key=lambda x: x[0], reverse=True)
        return [meta for score, meta in scored[:limit] if score > 0] or catalog[:limit]

    def score_catalog(
        self,
        prompt: str,
        catalog: Sequence[dict[str, Any]],
        memories: Sequence[Memory] = (),
    ) -> list[tuple[float, dict[str, Any]]]:
        """Score each capability against prompt tokens and memory context."""
        clean_p = prompt.lower().strip()
        tokens = set(self.extract_keywords(clean_p))

        # Memory tokens to boost domain relevance
        mem_tokens = set()
        for m in memories:
            mem_tokens.update(self.extract_keywords(m.content))

        scored: list[tuple[float, dict[str, Any]]] = []

        for meta in catalog:
            score = 0.0
            name = meta.get("name", "")
            description = meta.get("description", "")
            tags = meta.get("tags", [])

            name_parts = set(name.lower().replace(".", " ").replace("_", " ").split())
            desc_tokens = set(self.extract_keywords(description))
            tag_tokens = {str(t).lower() for t in tags}

            # Exact name or keyword matches
            if any(token in name_parts for token in tokens):
                score += 8.0
            if any(token in tag_tokens for token in tokens):
                score += 6.0

            # Token overlap in description
            overlap = tokens.intersection(desc_tokens)
            score += len(overlap) * 2.0

            # Memory relevance boost
            mem_overlap = mem_tokens.intersection(name_parts.union(tag_tokens))
            score += len(mem_overlap) * 1.5

            # Common action phrase heuristics
            if "search" in tokens or "find" in tokens or "google" in tokens:
                if "web" in name or "search" in tag_tokens:
                    score += 5.0
                if "filesystem" in name and "file" in tokens:
                    score += 5.0
            if "screenshot" in tokens and "screenshot" in name:
                score += 15.0
            if any(v in tokens for v in ("volume", "mute", "unmute", "sound")) and "volume" in name:
                score += 15.0
            if any(w in tokens for w in ("window", "workspace", "minimize", "maximize")) and "window" in name:
                score += 12.0
            if any(f in tokens for f in ("file", "folder", "directory", "move", "copy", "delete")) and "filesystem" in name:
                score += 10.0
            if any(a in tokens for a in ("open", "launch", "start", "run", "app", "application")) and "apps" in name:
                score += 8.0
            if any(b in tokens for b in ("browser", "webpage", "website", "url", "navigate", "page", "tab")) and "browser" in name:
                score += 12.0
            if any(s in tokens for s in ("scroll", "scrolling")) and "scroll" in name:
                score += 15.0
            if any(c in tokens for c in ("click", "button", "link")) and "click" in name:
                score += 14.0
            if any(d in tokens for d in ("download", "downloads", "downloaded")) and "download" in name:
                score += 15.0

            scored.append((score, meta))

        return scored
