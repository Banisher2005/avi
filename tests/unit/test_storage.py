"""Unit tests for Phase 15 SQLite persistence layer."""

import pytest
from pathlib import Path

from avi.storage.database import Database, screen_for_sensitive_data
from avi.storage.models import (
    ActionRecord,
    AliasRecord,
    MemoryRecord,
    PreferenceRecord,
    TaskRecord,
)


class TestSensitiveDataScreening:
    """Test sensitive data screening to prevent storing credentials."""

    def test_screen_plain_text_allowed(self):
        screen_for_sensitive_data("Google Chrome is my preferred browser")
        screen_for_sensitive_data("dark")

    def test_screen_rejects_passwords(self):
        with pytest.raises(ValueError, match="Cannot persist content: security policy blocks storing sensitive credentials"):
            screen_for_sensitive_data("user_password: supersecret123")

        with pytest.raises(ValueError, match="Cannot persist content: security policy blocks storing sensitive credentials"):
            screen_for_sensitive_data("my password: hunter24567")

    def test_screen_rejects_api_tokens(self):
        with pytest.raises(ValueError, match="Cannot persist content: security policy blocks storing sensitive credentials"):
            screen_for_sensitive_data("sk-1234567890abcdefghijklmnop")

        with pytest.raises(ValueError, match="Cannot persist content: security policy blocks storing sensitive credentials"):
            screen_for_sensitive_data("ghp_1234567890abcdefghijklmnopqrstuvwxyz")


class TestStorageDatabase:
    """Test Database CRUD operations, migrations, and WAL persistence."""

    @pytest.fixture
    def db(self, tmp_path):
        db_path = tmp_path / "avi_test.db"
        return Database(db_path=db_path)

    def test_database_initialization(self, db):
        assert db.db_path.exists()

    def test_preferences_crud(self, db):
        # Set preference
        db.set_preference("browser.preferred", "google-chrome")
        db.set_preference("theme.mode", "dark")

        # Get preference
        assert db.get_preference("browser.preferred") == "google-chrome"
        assert db.get_preference("theme.mode") == "dark"
        assert db.get_preference("nonexistent") is None
        assert db.get_preference("nonexistent", default="val") == "val"

        # List preferences
        all_prefs = db.list_preferences()
        assert len(all_prefs) == 2
        assert "browser.preferred" in all_prefs
        assert all_prefs["browser.preferred"] == "google-chrome"

        # Delete preference
        assert db.delete_preference("browser.preferred") is True
        assert db.get_preference("browser.preferred") is None
        assert db.delete_preference("browser.preferred") is False

    def test_memories_crud(self, db):
        rec1 = MemoryRecord(
            id="mem-1",
            content="preferred browser is chrome",
            category="preference",
            confidence=1.0,
        )
        rec2 = MemoryRecord(
            id="mem-2",
            content="project folder is ~/code/avi",
            category="workflow",
            metadata={"tags": ["work", "avi"]},
        )
        db.save_memory(rec1)
        db.save_memory(rec2)

        # List memories
        memories = db.list_memories()
        assert len(memories) == 2

        # Search memories
        chrome_mem = db.search_memories("chrome")
        assert len(chrome_mem) == 1
        assert "chrome" in chrome_mem[0].content

        # Delete memory by id
        assert db.delete_memory("mem-1") is True
        assert len(db.list_memories()) == 1

        # Delete memory by query
        deleted = db.delete_memories_matching("project folder")
        assert deleted == 1
        assert len(db.list_memories()) == 0

    def test_aliases_crud(self, db):
        # Set alias
        db.set_alias("yt", "open https://youtube.com", category="web")
        assert db.get_alias("yt") is not None
        assert db.get_alias("yt").target == "open https://youtube.com"

        # List aliases
        aliases = db.list_aliases()
        assert len(aliases) == 1
        assert aliases[0].alias == "yt"

        # Delete alias
        assert db.delete_alias("yt") is True
        assert db.get_alias("yt") is None

    def test_task_history_and_action_logging(self, db):
        task = TaskRecord(
            task_id="task-123",
            goal="Take screenshot and search youtube",
            status="success",
            plan_data={"steps": 2},
            result_data={"summary": "Done"},
        )
        db.record_task(task)

        retrieved = db.get_task("task-123")
        assert retrieved is not None
        assert retrieved.goal == "Take screenshot and search youtube"
        assert retrieved.status == "success"

        # Log actions
        action = ActionRecord(
            action_id="act-1",
            task_id="task-123",
            action_name="desktop.screenshot",
            target="screen",
            success=True,
            duration_ms=45.2,
        )
        db.record_action(action)

        actions = db.list_actions(task_id="task-123")
        assert len(actions) == 1
        assert actions[0].action_name == "desktop.screenshot"
        assert actions[0].duration_ms == 45.2
