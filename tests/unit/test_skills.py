"""Unit tests for Phase 15 Modular Skills Framework."""

import pytest
from unittest.mock import MagicMock, patch

from avi.skills.builtin import (
    ApplicationSkill,
    BrowserSkill,
    FilesystemSkill,
    MediaSkill,
    ScreenshotSkill,
    SystemControlsSkill,
    TerminalSkill,
)
from avi.skills.registry import SkillRegistry, create_default_skill_registry


class TestSkillRegistry:
    """Test registry initialization, discovery, and dispatch."""

    def test_default_registry_has_builtins(self):
        registry = create_default_skill_registry()

        skills = registry.list_skills()
        skill_names = {s.name for s in skills}
        expected = {
            "browser",
            "applications",
            "filesystem",
            "system_controls",
            "screenshots",
            "terminal",
            "media",
        }
        assert expected.issubset(skill_names)

    def test_get_and_has_skill(self):
        registry = SkillRegistry()
        registry.register(BrowserSkill())

        assert registry.get("browser") is not None
        assert registry.get("nonexistent") is None


class TestBrowserSkill:
    """Test BrowserSkill operations."""

    @patch("webbrowser.open")
    def test_browser_open_url(self, mock_open):
        skill = BrowserSkill()
        res = skill.execute("open_url", {"url": "https://example.com"})
        assert res.success is True
        assert res.target == "https://example.com"
        mock_open.assert_called_once_with("https://example.com")

    @patch("webbrowser.open")
    def test_browser_search(self, mock_open):
        skill = BrowserSkill()
        res = skill.execute("search", {"query": "python tutorial"})
        assert res.success is True
        assert "google.com" in res.target


class TestFilesystemSkill:
    """Test FilesystemSkill CRUD and security guards."""

    def test_read_write_directory_lifecycle(self, tmp_path):
        skill = FilesystemSkill()

        # Create directory
        sub_dir = tmp_path / "subdir"
        res_mkdir = skill.execute("create_dir", {"path": str(sub_dir)})
        assert res_mkdir.success is True
        assert sub_dir.is_dir()

        # Search
        res_search = skill.execute("search", {"query": "subdir", "directory": str(tmp_path)})
        assert res_search.success is True

        # Copy directory / file
        test_file = sub_dir / "test.txt"
        test_file.write_text("Hello World AVI")
        copy_file = sub_dir / "test_copy.txt"
        res_copy = skill.execute("copy", {"source": str(test_file), "destination": str(copy_file)})
        assert res_copy.success is True
        assert copy_file.is_file()

        # Move
        moved_file = sub_dir / "test_moved.txt"
        res_move = skill.execute("move", {"source": str(copy_file), "destination": str(moved_file)})
        assert res_move.success is True
        assert moved_file.is_file()
        assert not copy_file.exists()

        # Delete
        res_del = skill.execute("delete", {"path": str(moved_file)})
        assert res_del.success is True
        assert not moved_file.exists()


class TestTerminalSkill:
    """Test TerminalSkill command execution without shell=True."""

    def test_run_command_echo(self):
        skill = TerminalSkill()
        res = skill.execute("run_command", {"command": "echo 'test command'"})
        assert res.success is True
        assert "test command" in res.data["stdout"]

    def test_run_empty_command(self):
        skill = TerminalSkill()
        res = skill.execute("run_command", {"command": ""})
        assert res.success is False
        assert "Empty command" in res.error


class TestSystemControlsSkill:
    """Test SystemControlsSkill volume and audio actions."""

    @patch("subprocess.run")
    def test_set_volume(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0)
        skill = SystemControlsSkill()
        res = skill.execute("set_volume", {"level": 75})
        assert res.success is True
        assert "75%" in res.message


class TestMediaSkill:
    """Test MediaSkill playback controls."""

    @patch("shutil.which", return_value="/usr/bin/playerctl")
    @patch("subprocess.run")
    def test_media_play_pause(self, mock_run, mock_which):
        mock_run.return_value = MagicMock(returncode=0)
        skill = MediaSkill()
        res = skill.execute("control", {"action": "play_pause"})
        assert res.success is True
        assert "play_pause" in res.message
