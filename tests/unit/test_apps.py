"""Unit tests for ApplicationResolver and application models."""

import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from avi.apps.models import ApplicationResolution
from avi.apps.resolver import ApplicationResolver


class TestApplicationResolutionModel:
    def test_is_resolved_true_when_installed_and_executable(self):
        res = ApplicationResolution(
            requested_name="chrome",
            canonical_name="Google Chrome",
            executable="/usr/bin/google-chrome",
            desktop_entry=None,
            platform="Linux",
            installed=True,
            confidence=0.9,
        )
        assert res.is_resolved is True

    def test_is_resolved_false_when_uninstalled(self):
        res = ApplicationResolution(
            requested_name="nonexistent_xyz",
            canonical_name="Nonexistent_Xyz",
            executable=None,
            desktop_entry=None,
            platform="Linux",
            installed=False,
            confidence=0.0,
        )
        assert res.is_resolved is False


class TestApplicationResolverNormalization:
    def setup_method(self):
        self.resolver = ApplicationResolver()

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("open chrome", "chrome"),
            ("open the app brave", "brave"),
            ("launch google chrome", "google chrome"),
            ("start firefox browser", "firefox"),
            ("run code application", "code"),
            ("open antigravity", "antigravity"),
        ],
    )
    def test_normalize_app_name(self, raw, expected):
        assert self.resolver.normalize_app_name(raw) == expected


class TestApplicationResolverDesktopEntries:
    def test_parses_desktop_entry_files(self, tmp_path):
        desktop_file = tmp_path / "test-app.desktop"
        desktop_file.write_text(
            "[Desktop Entry]\n"
            "Name=Test Browser\n"
            "Exec=/usr/bin/test-browser-bin %U\n"
            "Type=Application\n"
        )

        resolver = ApplicationResolver(desktop_dirs=[tmp_path])
        entries = resolver._index_desktop_entries()

        assert "test-app" in entries or "test browser" in entries
        key = "test-app" if "test-app" in entries else "test browser"
        assert entries[key]["exec"] == "/usr/bin/test-browser-bin"
        assert entries[key]["name"] == "Test Browser"

    def test_ignores_hidden_and_nodisplay_entries(self, tmp_path):
        hidden_file = tmp_path / "hidden.desktop"
        hidden_file.write_text(
            "[Desktop Entry]\n"
            "Name=Hidden Tool\n"
            "Exec=hidden-tool\n"
            "NoDisplay=true\n"
        )

        resolver = ApplicationResolver(desktop_dirs=[tmp_path])
        entries = resolver._index_desktop_entries()
        assert "hidden" not in entries


class TestApplicationResolverResolution:
    def test_resolves_alias_via_path(self):
        resolver = ApplicationResolver(desktop_dirs=[])
        with patch("shutil.which", side_effect=lambda bin_name: f"/usr/bin/{bin_name}" if bin_name == "brave-origin" else None):
            res = resolver.resolve("open brave")
            assert res.installed is True
            assert res.executable == "/usr/bin/brave-origin"

    def test_resolves_antigravity_to_agy(self):
        resolver = ApplicationResolver()
        with patch("shutil.which", side_effect=lambda bin_name: f"/home/user/.local/bin/{bin_name}" if bin_name == "agy" else None):
            res = resolver.resolve("open antigravity")
            assert res.installed is True
            assert res.executable == "/home/user/.local/bin/agy"

    def test_uninstalled_app_returns_clean_unresolved(self):
        resolver = ApplicationResolver(desktop_dirs=[])
        with patch("shutil.which", return_value=None):
            res = resolver.resolve("open unknown_app_12345")
            assert res.installed is False
            assert res.executable is None
            assert res.is_resolved is False


class TestApplicationResolverLaunch:
    def test_launch_unresolved_returns_false(self):
        resolver = ApplicationResolver()
        res = ApplicationResolution(
            requested_name="foo",
            canonical_name="Foo",
            executable=None,
            desktop_entry=None,
            platform="Linux",
            installed=False,
            confidence=0.0,
        )
        ok, msg = resolver.launch(res)
        assert ok is False
        assert "not installed" in msg

    def test_launch_valid_invokes_popen_shell_false(self):
        resolver = ApplicationResolver()
        res = ApplicationResolution(
            requested_name="brave",
            canonical_name="Brave",
            executable="/usr/bin/brave-origin",
            desktop_entry=None,
            platform="Linux",
            installed=True,
            confidence=0.9,
        )

        with patch("subprocess.Popen") as mock_popen:
            ok, msg = resolver.launch(res, extra_args=["--incognito"])
            assert ok is True
            assert "Opening Brave" in msg
            mock_popen.assert_called_once()
            args, kwargs = mock_popen.call_args
            assert args[0] == ["/usr/bin/brave-origin", "--incognito"]
            assert kwargs.get("shell") is False
            assert kwargs.get("start_new_session") is True
