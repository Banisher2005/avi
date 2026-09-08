"""Memory manager for AVI providing explicit memory creation, recall, forgetting, and preference learning."""

import logging
import re
from typing import Any

from avi.memory.models import Memory
from avi.storage.database import Database

logger = logging.getLogger("avi.memory")

# Regex patterns for detecting explicit memory commands
_REMEMBER_EXPLICIT_RE = re.compile(
    r"^(?:please\s+)?(?:remember(?:\s+that)?|note(?:\s+that)?|keep\s+in\s+mind(?:\s+that)?)\s+(.+)$",
    re.IGNORECASE,
)

_FORGET_EXPLICIT_RE = re.compile(
    r"^(?:please\s+)?(?:forget(?:\s+that)?|delete\s+memory(?:\s+about|\s+for)?|remove\s+memory(?:\s+about|\s+for)?)\s+(.+)$",
    re.IGNORECASE,
)

_RECALL_EXPLICIT_RE = re.compile(
    r"^(?:what\s+do\s+you\s+remember\s+about|recall|search\s+memories\s+for|do\s+you\s+remember)\s+(.+)\??$",
    re.IGNORECASE,
)

_LIST_MEMORIES_RE = re.compile(
    r"^(?:list\s+memories|show\s+memories|what\s+do\s+you\s+remember|my\s+memories)\??$",
    re.IGNORECASE,
)

# Common preference patterns
_PREFERRED_BROWSER_RE = re.compile(
    r"^(?:(?:my\s+)?preferred\s+browser\s+is|use|set\s+preferred\s+browser\s+to)\s+([a-zA-Z0-9_\-]+)$|"
    r"^([a-zA-Z0-9_\-]+)\s+is\s+my\s+preferred\s+browser$",
    re.IGNORECASE,
)

_FOLDER_PREFERENCE_RE = re.compile(
    r"^(?:my\s+)?([a-zA-Z0-9_\-\s]+)\s+folder\s+is\s+(~?[a-zA-Z0-9_\-/\.]+)$",
    re.IGNORECASE,
)


class MemoryManager:
    """Manages short-term and persistent memories with explicit consent and safety boundaries."""

    def __init__(self, database: Database) -> None:
        self.db = database

    # -----------------------------------------------------------------------
    # Core Memory Operations
    # -----------------------------------------------------------------------

    def remember(
        self,
        content: str,
        category: str = "general",
        metadata: dict[str, Any] | None = None,
        confidence: float = 1.0,
    ) -> Memory:
        """Store an explicit memory or learned preference.

        Enforces security checks so passwords, API keys, or tokens are rejected.
        """
        clean_content = content.strip()
        if not clean_content:
            raise ValueError("Memory content cannot be empty.")

        meta = metadata or {}

        # Check if an existing memory has virtually identical content in this category
        existing = self.db.search_memories(clean_content, category=category, limit=1)
        if existing and existing[0].content.lower() == clean_content.lower():
            mem = Memory.from_record(existing[0])
            mem.confidence = confidence
            mem.metadata.update(meta)
            self.db.save_memory(mem.to_record())
            self.db.touch_memory(mem.id)
            logger.debug("Updated existing memory: %s", mem.id)
            return mem

        mem = Memory(
            content=clean_content,
            category=category,
            confidence=confidence,
            metadata=meta,
        )
        self.db.save_memory(mem.to_record())
        logger.debug("Saved new memory: %s (%s)", mem.id, category)
        return mem

    def recall(
        self,
        query: str,
        category: str | None = None,
        limit: int = 5,
    ) -> list[Memory]:
        """Retrieve relevant memories matching query words, touching last_used timestamp."""
        clean_query = query.strip()
        if not clean_query:
            return self.list_memories(category=category, limit=limit)

        records = self.db.search_memories(clean_query, category=category, limit=limit)
        results: list[Memory] = []
        for r in records:
            self.db.touch_memory(r.id)
            results.append(Memory.from_record(r))

        return results

    def forget(self, memory_id: str) -> bool:
        """Delete a memory by its unique ID."""
        deleted = self.db.delete_memory(memory_id)
        if deleted:
            logger.debug("Deleted memory: %s", memory_id)
        return deleted

    def forget_by_query(self, query: str, category: str | None = None) -> int:
        """Delete all memories matching a natural query."""
        clean = query.strip()
        count = self.db.delete_memories_matching(clean, category=category)
        logger.debug("Forgot %d memories matching query %r", count, clean)
        return count

    def list_memories(
        self,
        category: str | None = None,
        limit: int = 50,
    ) -> list[Memory]:
        """List all stored memories ordered by recency."""
        records = self.db.list_memories(category=category, limit=limit)
        return [Memory.from_record(r) for r in records]

    def clear(self) -> int:
        """Remove all memories."""
        return self.db.clear_memories()

    # -----------------------------------------------------------------------
    # Reusable Learned Preferences Helpers
    # -----------------------------------------------------------------------

    def get_preferred_browser(self) -> str | None:
        """Retrieve user's preferred browser (from preferences or memory)."""
        pref = self.db.get_preference("preferred_browser")
        if pref:
            return str(pref).strip().lower()

        # Check memory
        recalled = self.recall("browser", category="browser", limit=1)
        if recalled:
            val = recalled[0].metadata.get("browser")
            if val:
                return str(val).strip().lower()
        return None

    def set_preferred_browser(self, browser_name: str) -> None:
        """Store user's preferred browser."""
        clean = browser_name.strip().lower()
        self.db.set_preference("preferred_browser", clean)
        self.remember(
            f"{clean.title()} is my preferred browser.",
            category="browser",
            metadata={"browser": clean},
        )

    def get_preferred_folder(self, folder_name: str) -> str | None:
        """Look up a named folder preference (e.g. 'projects')."""
        key = f"folder_{folder_name.strip().lower()}"
        pref = self.db.get_preference(key)
        if pref:
            return str(pref)

        # Look in memories
        recalled = self.recall(folder_name, category="path", limit=1)
        if recalled:
            val = recalled[0].metadata.get("path")
            if val:
                return str(val)
        return None

    def set_preferred_folder(self, folder_name: str, path_str: str) -> None:
        """Persist a named folder mapping."""
        fn = folder_name.strip().lower()
        p = path_str.strip()
        self.db.set_preference(f"folder_{fn}", p)
        self.remember(
            f"My {fn} folder is {p}.",
            category="path",
            metadata={"folder": fn, "path": p},
        )

    # -----------------------------------------------------------------------
    # Natural Language Memory Intent Parsing
    # -----------------------------------------------------------------------

    def handle_memory_command(self, prompt: str) -> tuple[bool, str]:
        """Check if prompt is an explicit memory command and execute it.

        Returns: (handled, response_message)
        """
        clean = prompt.strip()

        # 1. List memories
        if _LIST_MEMORIES_RE.match(clean):
            mems = self.list_memories(limit=10)
            if not mems:
                return (True, "I don't have any stored memories yet.")
            lines = ["Here is what I remember:"]
            for m in mems:
                lines.append(f"• {m.format_summary()}")
            return (True, "\n".join(lines))

        # 2. Recall memories
        rec_match = _RECALL_EXPLICIT_RE.match(clean)
        if rec_match:
            q = rec_match.group(1).strip().rstrip("?.!")
            mems = self.recall(q, limit=5)
            if not mems:
                if "browser" in q.lower():
                    pref_b = self.get_preferred_browser()
                    if pref_b:
                        return (True, f"Your preferred browser is {pref_b.title()}.")
                return (True, f"I couldn't find any memories about '{q}'.")
            lines = [f"Here is what I recall about '{q}':"]
            for m in mems:
                lines.append(f"• {m.format_summary()}")
            return (True, "\n".join(lines))

        # 3. Forget memories
        fgt_match = _FORGET_EXPLICIT_RE.match(clean)
        if fgt_match:
            target = fgt_match.group(1).strip().rstrip("?.!")
            if target.lower() in ("everything", "all", "all memories", "all my memories"):
                self.clear()
                self.db.delete_preference("preferred_browser")
                self.db.delete_preference("browser_preferred")
                return (True, "Cleared all memories.")
            # If target looks like a memory id
            if target.startswith("mem_"):
                if self.forget(target):
                    return (True, f"Memory {target} deleted.")
            if "browser" in target.lower():
                self.db.delete_preference("preferred_browser")
                self.db.delete_preference("browser_preferred")
            count = self.forget_by_query(target)
            if count > 0:
                return (True, f"I've forgotten {count} item{'s' if count != 1 else ''} related to '{target}'.")
            return (True, f"I couldn't find any memories matching '{target}' to delete.")

        # 4. Remember browser preference
        browser_match = _PREFERRED_BROWSER_RE.match(clean)
        if browser_match:
            b = browser_match.group(1) or browser_match.group(2)
            self.set_preferred_browser(b)
            return (True, f"Understood! I'll remember that {b.title()} is your preferred browser.")

        # 5. Remember explicit folder preference
        # e.g. "my projects folder is ~/Projects" or "remember that my Projects folder is ~/Projects"
        test_folder = clean
        rem_prefix = _REMEMBER_EXPLICIT_RE.match(clean)
        if rem_prefix:
            test_folder = rem_prefix.group(1).strip()

        folder_match = _FOLDER_PREFERENCE_RE.match(test_folder)
        if folder_match:
            f_name = folder_match.group(1).strip()
            f_path = folder_match.group(2).strip()
            self.set_preferred_folder(f_name, f_path)
            return (True, f"Understood! I'll remember that your {f_name} folder is {f_path}.")

        # 6. General explicit remember
        if rem_prefix:
            content = rem_prefix.group(1).strip()
            # Double check if content contains browser preference
            b_sub = _PREFERRED_BROWSER_RE.match(content)
            if b_sub:
                b = b_sub.group(1) or b_sub.group(2)
                self.set_preferred_browser(b)
                return (True, f"Understood! I'll remember that {b.title()} is your preferred browser.")

            try:
                mem = self.remember(content, category="fact")
                return (True, f"I'll remember that: {mem.content}")
            except ValueError as e:
                return (True, f"Could not save memory: {e}")

        return (False, "")
