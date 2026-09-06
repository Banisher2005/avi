"""Unit tests for native assistant actions."""

from unittest.mock import MagicMock, patch

from avi.actions.system import OpenAppAction, OpenDirAction, OpenFileAction, OpenUrlAction
from avi.actions.timer import TimerAction, format_duration
from avi.apps.models import ApplicationResolution
from avi.safety.models import ActionCategory


class TestTimerAction:
    def test_format_duration(self):
        assert format_duration(2) == "2 seconds"
        assert format_duration(1) == "1 second"
        assert format_duration(60) == "1 minute"
        assert format_duration(90) == "1 min 30 sec"

    def test_timer_start_message(self):
        timer = TimerAction(duration_seconds=5, label="tea")
        assert timer.start_message == "Timer set for 5 seconds (tea)."

        timer_unlabeled = TimerAction(duration_seconds=2)
        assert timer_unlabeled.start_message == "Timer set for 2 seconds."

    def test_timer_execution_invokes_sleep_fn(self):
        mock_sleep = MagicMock()
        timer = TimerAction(duration_seconds=1.5, label="eggs", sleep_fn=mock_sleep)
        res = timer.execute()

        assert res.success is True
        assert "Time's up: eggs." in res.message
        mock_sleep.assert_called_once_with(1.5)

    def test_timer_category_is_low_risk(self):
        timer = TimerAction(duration_seconds=10)
        assert timer.category == ActionCategory.LOW_RISK_ACTION
        assert timer.requires_confirmation is False


class TestOpenUrlAction:
    def test_url_normalization(self):
        action1 = OpenUrlAction("youtube.com")
        assert action1.url == "https://youtube.com"

        action2 = OpenUrlAction("http://localhost:8080")
        assert action2.url == "http://localhost:8080"

    def test_open_url_execution_uses_browser(self):
        action = OpenUrlAction("https://github.com")
        with patch("webbrowser.open", return_value=True) as mock_open:
            res = action.execute()
            assert res.success is True
            assert "Opening https://github.com" in res.message
            mock_open.assert_called_once_with("https://github.com")

    def test_open_url_category_is_external(self):
        action = OpenUrlAction("https://example.com")
        assert action.category == ActionCategory.EXTERNAL_ACTION
        assert action.requires_confirmation is False


class TestOpenAppAction:
    def test_open_app_execution(self):
        res = ApplicationResolution(
            requested_name="brave",
            canonical_name="Brave",
            executable="/usr/bin/brave",
            desktop_entry=None,
            platform="Linux",
            installed=True,
            confidence=0.9,
        )
        mock_resolver = MagicMock()
        mock_resolver.launch.return_value = (True, "Opening Brave.")

        action = OpenAppAction(resolution=res, resolver=mock_resolver)
        act_res = action.execute()

        assert act_res.success is True
        assert act_res.message == "Opening Brave."
        mock_resolver.launch.assert_called_once_with(res, extra_args=[])


class TestOpenFileAndDirActions:
    def test_open_dir_action(self, tmp_path):
        target_dir = tmp_path / "my_folder"
        target_dir.mkdir()

        action = OpenDirAction(target_dir)
        with patch("subprocess.Popen") as mock_popen:
            res = action.execute()
            assert res.success is True
            assert "Opening folder my_folder" in res.message
            mock_popen.assert_called_once()
            args, kwargs = mock_popen.call_args
            assert args[0] == ["xdg-open", str(target_dir)]
            assert kwargs.get("shell") is False

    def test_open_file_action(self, tmp_path):
        test_file = tmp_path / "notes.txt"
        test_file.write_text("hello")

        action = OpenFileAction(test_file)
        with patch("subprocess.Popen") as mock_popen:
            res = action.execute()
            assert res.success is True
            assert "Opening file notes.txt" in res.message
            mock_popen.assert_called_once()
            args, kwargs = mock_popen.call_args
            assert args[0] == ["xdg-open", str(test_file)]
            assert kwargs.get("shell") is False

    def test_open_missing_file_fails(self, tmp_path):
        action = OpenFileAction(tmp_path / "does_not_exist.txt")
        res = action.execute()
        assert res.success is False
        assert "does not exist" in res.message
