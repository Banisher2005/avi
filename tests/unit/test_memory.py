"""Unit tests for Phase 15 Memory Manager and conversational memory interface."""

import pytest
from pathlib import Path

from avi.memory.manager import MemoryManager
from avi.storage.database import Database


class TestMemoryManager:
    """Test MemoryManager capabilities, preferred defaults, and natural language parser."""

    @pytest.fixture
    def manager(self, tmp_path):
        db = Database(db_path=tmp_path / "memory_test.db")
        return MemoryManager(database=db)

    def test_remember_and_recall(self, manager):
        entry = manager.remember("Chrome is my favorite browser", category="preference")
        assert entry.content == "Chrome is my favorite browser"
        assert entry.category == "preference"

        results = manager.recall("browser")
        assert len(results) == 1
        assert "Chrome" in results[0].content

    def test_forget_and_clear(self, manager):
        manager.remember("Chrome is my favorite browser")
        manager.remember("Dark mode is enabled")

        assert len(manager.list_memories()) == 2

        count = manager.forget_by_query("Chrome")
        assert count == 1
        assert len(manager.list_memories()) == 1

        manager.clear()
        assert len(manager.list_memories()) == 0

    def test_preferred_browser_helpers(self, manager):
        assert manager.get_preferred_browser() is None
        manager.set_preferred_browser("chrome")
        assert manager.get_preferred_browser() == "chrome"

    def test_preferred_folder_helpers(self, manager):
        assert manager.get_preferred_folder("notes") is None
        manager.set_preferred_folder("notes", "/home/user/Documents/Notes")
        assert manager.get_preferred_folder("notes") == "/home/user/Documents/Notes"

    def test_handle_natural_language_remember(self, manager):
        handled, msg = manager.handle_memory_command("remember that Chrome is my preferred browser")
        assert handled is True
        assert "Chrome" in msg
        assert "preferred browser" in msg

        # Preferred browser preference should also be updated
        assert manager.get_preferred_browser() == "chrome"

    def test_handle_natural_language_recall(self, manager):
        manager.remember("Chrome is my preferred browser")
        handled, msg = manager.handle_memory_command("what do you remember about browser?")
        assert handled is True
        assert "Chrome is my preferred browser" in msg

    def test_handle_natural_language_forget(self, manager):
        manager.handle_memory_command("remember that Chrome is my preferred browser")
        assert manager.get_preferred_browser() == "chrome"

        handled, msg = manager.handle_memory_command("forget that Chrome is my preferred browser")
        assert handled is True
        assert "forgotten" in msg
        assert manager.get_preferred_browser() is None

    def test_handle_natural_language_list_and_clear(self, manager):
        manager.remember("Theme is dark")
        handled, msg = manager.handle_memory_command("what do you remember?")
        assert handled is True
        assert "Theme is dark" in msg

        clear_handled, clear_msg = manager.handle_memory_command("forget everything")
        assert clear_handled is True
        assert "Cleared all memories" in clear_msg
        assert len(manager.list_memories()) == 0

    def test_handle_sensitive_data_refusal(self, manager):
        handled, msg = manager.handle_memory_command("remember that my password is supersecret123")
        assert handled is True
        assert "security policy blocks storing sensitive credentials" in msg
        assert len(manager.list_memories()) == 0
