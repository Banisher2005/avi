"""Unit tests for AVI unified capability runtime and registry."""

from pathlib import Path
from unittest.mock import MagicMock, patch

from avi.capabilities.desktop.app_launcher import (
    LaunchAppCapability,
    OpenDirectoryCapability,
    OpenFileCapability,
    OpenUrlCapability,
)
from avi.capabilities.desktop.clipboard import (
    ClipboardGetCapability,
    ClipboardSetCapability,
)
from avi.capabilities.desktop.input import (
    PressKeyCapability,
    TypeTextCapability,
)
from avi.capabilities.desktop.notification import NotificationCapability
from avi.capabilities.desktop.system_controls import (
    MediaControlCapability,
    VolumeGetCapability,
    VolumeSetCapability,
)
from avi.capabilities.desktop.window import (
    WindowFocusCapability,
    WindowListCapability,
)
from avi.capabilities.filesystem.operations import (
    CopyFileCapability,
    CreateDirectoryCapability,
    DeleteFileCapability,
    MoveFileCapability,
)
from avi.capabilities.filesystem.search import FilesystemSearchCapability
from avi.capabilities.models import (
    DataClassification,
    ExecutionStatus,
    ToolCapabilityAdapter,
)
from avi.capabilities.registry import CapabilityRegistry, create_default_capability_registry
from avi.safety.models import ActionCategory
from avi.tools.base import BaseTool, ToolResult


class DummyEchoTool(BaseTool):
    name = "dummy.echo"
    description = "Echoes input back"

    def execute(self, text: str = "hello") -> ToolResult:
        return ToolResult(success=True, data={"echo": text}, message=f"Echo: {text}")


class TestCapabilityRegistry:
    def test_register_and_get(self):
        reg = CapabilityRegistry()
        tool = DummyEchoTool()
        adapter = ToolCapabilityAdapter(tool)
        reg.register(adapter, aliases=["echo"])

        assert reg.get("dummy.echo") is adapter
        assert reg.get("echo") is adapter
        assert "dummy.echo" in reg
        assert "echo" in reg
        assert len(reg) == 1

    def test_list_capabilities_and_schemas(self):
        reg = CapabilityRegistry()
        reg.register(NotificationCapability(), aliases=["notify"])
        reg.register(VolumeGetCapability())

        caps = reg.list_capabilities()
        assert "desktop.notification" in caps
        assert "system.volume.get" in caps

        schemas = reg.get_schemas()
        assert len(schemas) == 2
        schema_names = [s["name"] for s in schemas]
        assert "desktop.notification" in schema_names
        assert "system.volume.get" in schema_names

    def test_execute_unknown_capability_fails_cleanly(self):
        reg = CapabilityRegistry()
        res = reg.execute("nonexistent.capability")
        assert res.success is False
        assert res.status == ExecutionStatus.FAILED
        assert "Unknown capability" in res.message

    def test_default_registry_creation(self):
        reg = create_default_capability_registry()
        assert len(reg) >= 15
        assert "desktop.screenshot" in reg
        assert "desktop.open_app" in reg
        assert "desktop.clipboard.get" in reg
        assert "desktop.clipboard.set" in reg
        assert "clipboard.get" in reg
        assert "clipboard.set" in reg
        assert "desktop.window.list" in reg
        assert "desktop.window.focus" in reg
        assert "window.list" in reg
        assert "window.focus" in reg
        assert "list_windows" in reg
        assert "focus_window" in reg
        assert "desktop.input.type_text" in reg
        assert "desktop.input.press_key" in reg
        assert "type_text" in reg
        assert "press_key" in reg
        assert "filesystem.search" in reg
        assert "system.volume.set" in reg
        assert "system.volume.get" in reg
        assert "system.media" in reg
        assert "filesystem.delete" in reg


class TestDesktopCapabilities:
    def test_launch_app_success(self):
        mock_resolver = MagicMock()
        mock_resolution = MagicMock()
        mock_resolution.found = True
        mock_resolution.canonical_name = "Firefox"
        mock_resolver.resolve.return_value = mock_resolution
        mock_resolver.launch.return_value = (True, "Launched Firefox.")

        cap = LaunchAppCapability(resolver=mock_resolver)
        res = cap.execute(app_name="firefox")
        assert res.success is True
        assert res.status == ExecutionStatus.SUCCESS
        assert "Launched Firefox" in res.message
        mock_resolver.resolve.assert_called_once_with("firefox")

    def test_launch_app_not_found(self):
        mock_resolver = MagicMock()
        mock_resolution = MagicMock()
        mock_resolution.found = False
        mock_resolver.resolve.return_value = mock_resolution

        cap = LaunchAppCapability(resolver=mock_resolver)
        res = cap.execute(app_name="unknown-app-xyz")
        assert res.success is False
        assert res.status == ExecutionStatus.FAILED
        assert "Could not find application" in res.error

    def test_open_url_capability(self):
        cap = OpenUrlCapability()
        with patch("webbrowser.open", return_value=True) as mock_open:
            res = cap.execute(url="https://github.com")
            assert res.success is True
            assert res.status == ExecutionStatus.SUCCESS
            assert "Opening https://github.com" in res.message
            mock_open.assert_called_once_with("https://github.com")

    def test_open_file_capability_missing_file(self, tmp_path):
        cap = OpenFileCapability()
        missing = tmp_path / "nonexistent.txt"
        res = cap.execute(path=str(missing))
        assert res.success is False
        assert res.status == ExecutionStatus.FAILED
        assert "does not exist" in res.error

    def test_open_directory_capability_success(self, tmp_path):
        cap = OpenDirectoryCapability()
        with patch("subprocess.Popen") as mock_popen:
            res = cap.execute(path=str(tmp_path))
            assert res.success is True
            assert res.status == ExecutionStatus.SUCCESS
            mock_popen.assert_called_once()

    def test_notification_capability(self):
        cap = NotificationCapability()
        with patch("subprocess.run") as mock_run:
            mock_run.return_value.returncode = 0
            res = cap.execute(message="Test Notification", title="AVI")
            assert res.success is True
            assert res.status == ExecutionStatus.SUCCESS
            assert "Test Notification" in res.message
            mock_run.assert_called_once()
            called_cmd = mock_run.call_args[0][0]
            assert "notify-send" in called_cmd
            assert "Test Notification" in called_cmd

    def test_volume_capabilities(self):
        get_cap = VolumeGetCapability()
        set_cap = VolumeSetCapability()

        with patch("subprocess.run") as mock_run:
            mock_run.return_value.returncode = 0
            mock_run.return_value.stdout = "Volume: 0.65 [MUTED]"
            res = get_cap.execute()
            assert res.success is True
            assert res.data["level"] == 65
            assert res.data["muted"] is True

        with patch("subprocess.run") as mock_run:
            mock_run.return_value.returncode = 0
            res = set_cap.execute(level=80)
            assert res.success is True
            assert res.data["level"] == 80

    def test_media_control_capability(self):
        cap = MediaControlCapability()
        with patch("shutil.which", return_value="/usr/bin/playerctl"):
            with patch("subprocess.run") as mock_run:
                mock_run.return_value.returncode = 0
                res = cap.execute(command="play")
                assert res.success is True
                assert res.status == ExecutionStatus.SUCCESS
                assert res.data["command"] == "play"

    def test_clipboard_get_success(self):
        cap = ClipboardGetCapability()
        with patch("shutil.which", side_effect=lambda x: "/usr/bin/wl-paste" if x == "wl-paste" else None):
            with patch("subprocess.run") as mock_run:
                mock_run.return_value.returncode = 0
                mock_run.return_value.stdout = "copied text sample"
                res = cap.execute()
                assert res.success is True
                assert res.status == ExecutionStatus.SUCCESS
                assert res.data["text"] == "copied text sample"
                assert res.data["length"] == 18
                assert "copied text sample" in res.message

    def test_clipboard_get_xclip_fallback(self):
        cap = ClipboardGetCapability()
        with patch("shutil.which", side_effect=lambda x: "/usr/bin/xclip" if x == "xclip" else None):
            with patch("subprocess.run") as mock_run:
                mock_run.return_value.returncode = 0
                mock_run.return_value.stdout = "xclip text"
                res = cap.execute()
                assert res.success is True
                assert res.data["text"] == "xclip text"

    def test_clipboard_get_no_utility(self):
        cap = ClipboardGetCapability()
        with patch("shutil.which", return_value=None):
            res = cap.execute()
            assert res.success is False
            assert res.status == ExecutionStatus.FAILED
            assert "No supported clipboard utility found" in res.error

    def test_clipboard_set_success(self):
        cap = ClipboardSetCapability()
        with patch("shutil.which", side_effect=lambda x: "/usr/bin/wl-copy" if x == "wl-copy" else None):
            with patch("subprocess.run") as mock_run:
                mock_run.return_value.returncode = 0
                res = cap.execute(text="new clipboard text")
                assert res.success is True
                assert res.status == ExecutionStatus.SUCCESS
                assert res.data["text"] == "new clipboard text"
                mock_run.assert_called_once()
                assert mock_run.call_args[1]["input"] == "new clipboard text"

    def test_clipboard_set_xclip_fallback(self):
        cap = ClipboardSetCapability()
        with patch("shutil.which", side_effect=lambda x: "/usr/bin/xclip" if x == "xclip" else None):
            with patch("subprocess.run") as mock_run:
                mock_run.return_value.returncode = 0
                res = cap.execute(content="text from content param")
                assert res.success is True
                assert res.data["text"] == "text from content param"

    def test_clipboard_set_missing_text(self):
        cap = ClipboardSetCapability()
        res = cap.execute()
        assert res.success is False
        assert res.status == ExecutionStatus.FAILED
        assert "Parameter 'text' is required" in res.error

    def test_window_list_success_wmctrl(self):
        cap = WindowListCapability()
        mock_output = (
            "0x02800003  0 12345 org.gnome.Terminal.Gnome-terminal localhost Terminal - user@host: ~\n"
            "0x03a00001  1 12346 code.Code localhost test.py - Visual Studio Code\n"
        )
        with patch("shutil.which", side_effect=lambda x: "/usr/bin/wmctrl" if x == "wmctrl" else None):
            with patch("subprocess.run") as mock_run:
                mock_run.return_value.returncode = 0
                mock_run.return_value.stdout = mock_output
                res = cap.execute()
                assert res.success is True
                assert res.status == ExecutionStatus.SUCCESS
                assert res.data["count"] == 2
                assert len(res.data["windows"]) == 2
                w1, w2 = res.data["windows"]
                assert w1["id"] == "0x02800003"
                assert w1["pid"] == 12345
                assert w1["wm_class"] == "org.gnome.Terminal.Gnome-terminal"
                assert "Terminal" in w1["title"]
                assert w2["title"] == "test.py - Visual Studio Code"

    def test_window_list_filter_query(self):
        cap = WindowListCapability()
        mock_output = (
            "0x02800003  0 12345 org.gnome.Terminal.Gnome-terminal localhost Terminal\n"
            "0x03a00001  1 12346 code.Code localhost test.py - Visual Studio Code\n"
        )
        with patch("shutil.which", side_effect=lambda x: "/usr/bin/wmctrl" if x == "wmctrl" else None):
            with patch("subprocess.run") as mock_run:
                mock_run.return_value.returncode = 0
                mock_run.return_value.stdout = mock_output
                res = cap.execute(query="Visual Studio")
                assert res.success is True
                assert res.data["count"] == 1
                assert res.data["windows"][0]["title"] == "test.py - Visual Studio Code"

    def test_window_list_empty(self):
        cap = WindowListCapability()
        with patch("shutil.which", side_effect=lambda x: "/usr/bin/wmctrl" if x == "wmctrl" else None):
            with patch("subprocess.run") as mock_run:
                mock_run.return_value.returncode = 0
                mock_run.return_value.stdout = ""
                res = cap.execute()
                assert res.success is True
                assert res.data["count"] == 0
                assert res.data["windows"] == []
                assert "No open desktop windows detected" in res.message

    def test_window_focus_by_title_success(self):
        cap = WindowFocusCapability()
        with patch("shutil.which", side_effect=lambda x: "/usr/bin/wmctrl" if x == "wmctrl" else None):
            with patch("subprocess.run") as mock_run:
                mock_run.return_value.returncode = 0
                res = cap.execute(title="Terminal")
                assert res.success is True
                assert res.status == ExecutionStatus.SUCCESS
                assert "Focused window matching 'Terminal'" in res.message
                mock_run.assert_called_once_with(
                    ["wmctrl", "-a", "Terminal"],
                    stdout=-1,
                    stderr=-1,
                    text=True,
                    check=False,
                    timeout=3.0,
                )

    def test_window_focus_by_id_success(self):
        cap = WindowFocusCapability()
        with patch("shutil.which", side_effect=lambda x: "/usr/bin/wmctrl" if x == "wmctrl" else None):
            with patch("subprocess.run") as mock_run:
                mock_run.return_value.returncode = 0
                res = cap.execute(window_id="0x02800003")
                assert res.success is True
                assert res.status == ExecutionStatus.SUCCESS
                mock_run.assert_called_once_with(
                    ["wmctrl", "-i", "-a", "0x02800003"],
                    stdout=-1,
                    stderr=-1,
                    text=True,
                    check=False,
                    timeout=3.0,
                )

    def test_window_focus_missing_args(self):
        cap = WindowFocusCapability()
        res = cap.execute()
        assert res.success is False
        assert res.status == ExecutionStatus.FAILED
        assert "Either 'title', 'app_name', or 'window_id' must be specified" in res.error

    def test_window_focus_fallback_to_resolver(self):
        mock_resolver = MagicMock()
        resolution = MagicMock()
        resolution.found = True
        resolution.app = MagicMock(name="SlackApp")
        resolution.app.name = "Slack"
        mock_resolver.resolve.return_value = resolution
        mock_resolver.launch.return_value = (True, "Launched Slack")

        cap = WindowFocusCapability(resolver=mock_resolver)
        with patch("shutil.which", return_value=None):
            res = cap.execute(title="Slack")
            assert res.success is True
            assert res.status == ExecutionStatus.SUCCESS
            assert "Activated application 'Slack'" in res.message
            mock_resolver.resolve.assert_called_once_with("Slack")
            mock_resolver.launch.assert_called_once_with(resolution.app)

    def test_type_text_success_xdotool(self):
        cap = TypeTextCapability()
        with patch("shutil.which", side_effect=lambda x: "/usr/bin/xdotool" if x == "xdotool" else None):
            with patch("subprocess.run") as mock_run:
                mock_run.return_value.returncode = 0
                res = cap.execute(text="hello world")
                assert res.success is True
                assert res.status == ExecutionStatus.SUCCESS
                assert res.data["length"] == 11
                assert "hello world" in res.message
                mock_run.assert_called_once()
                assert mock_run.call_args[0][0][:2] == ["xdotool", "type"]

    def test_type_text_success_native(self):
        cap = TypeTextCapability()
        mock_backend = MagicMock()
        mock_backend.is_available = True
        mock_backend.type_ascii_text.return_value = True
        with patch("shutil.which", return_value=None):
            with patch("avi.capabilities.desktop.input.get_x11_backend", return_value=mock_backend):
                res = cap.execute(text="python code")
                assert res.success is True
                assert res.status == ExecutionStatus.SUCCESS
                assert "python code" in res.message
                mock_backend.type_ascii_text.assert_called_once()

    def test_type_text_multiline_clipboard(self):
        cap = TypeTextCapability()
        with patch("avi.capabilities.desktop.input._write_system_clipboard") as mock_clip:
            with patch("avi.capabilities.desktop.input.execute_press_key", return_value=(True, "ok")) as mock_key:
                res = cap.execute(text="line 1\nline 2")
                assert res.success is True
                assert res.status == ExecutionStatus.SUCCESS
                mock_clip.assert_called_once_with("line 1\nline 2")
                mock_key.assert_called_once_with("ctrl+v", timeout=5.0)

    def test_type_text_missing_param(self):
        cap = TypeTextCapability()
        res = cap.execute()
        assert res.success is False
        assert res.status == ExecutionStatus.FAILED
        assert "Parameter 'text' is required" in res.error

    def test_press_key_success_xdotool(self):
        cap = PressKeyCapability()
        with patch("shutil.which", side_effect=lambda x: "/usr/bin/xdotool" if x == "xdotool" else None):
            with patch("subprocess.run") as mock_run:
                mock_run.return_value.returncode = 0
                res = cap.execute(key="ctrl+c")
                assert res.success is True
                assert res.status == ExecutionStatus.SUCCESS
                assert res.data["key"] == "ctrl+c"
                assert "ctrl+c" in res.message
                mock_run.assert_called_once()
                assert mock_run.call_args[0][0] == ["xdotool", "key", "ctrl+c"]

    def test_press_key_success_native(self):
        cap = PressKeyCapability()
        mock_backend = MagicMock()
        mock_backend.is_available = True
        mock_backend.press_key_combination.return_value = True
        with patch("shutil.which", return_value=None):
            with patch("avi.capabilities.desktop.input.get_x11_backend", return_value=mock_backend):
                res = cap.execute(key="Return")
                assert res.success is True
                assert res.status == ExecutionStatus.SUCCESS
                assert "Return" in res.message
                mock_backend.press_key_combination.assert_called_once_with(["Return"])

    def test_press_key_missing_param(self):
        cap = PressKeyCapability()
        res = cap.execute()
        assert res.success is False
        assert res.status == ExecutionStatus.FAILED
        assert "Parameter 'key' is required" in res.error





class TestFilesystemCapabilities:
    def test_filesystem_search(self, tmp_path):
        # Create test directory structure
        docs = tmp_path / "Documents"
        docs.mkdir()
        f1 = docs / "report.pdf"
        f1.write_text("dummy pdf 1")
        f2 = docs / "notes.txt"
        f2.write_text("dummy notes")
        f3 = docs / "final_report.pdf"
        f3.write_text("dummy pdf 2")

        cap = FilesystemSearchCapability()
        # Search by extension
        res = cap.execute(path=str(docs), extension="pdf")
        assert res.success is True
        assert res.status == ExecutionStatus.SUCCESS
        assert len(res.data["matches"]) == 2
        names = [m["name"] for m in res.data["matches"]]
        assert "report.pdf" in names
        assert "final_report.pdf" in names

        # Search by query
        res2 = cap.execute(path=str(docs), query="notes")
        assert res2.success is True
        assert len(res2.data["matches"]) == 1
        assert res2.data["matches"][0]["name"] == "notes.txt"

    def test_create_directory_and_copy_file(self, tmp_path):
        mkdir_cap = CreateDirectoryCapability()
        new_dir = tmp_path / "archive"
        res = mkdir_cap.execute(path=str(new_dir))
        assert res.success is True
        assert new_dir.exists()
        assert new_dir.is_dir()

        src_file = tmp_path / "hello.txt"
        src_file.write_text("hello world")

        copy_cap = CopyFileCapability()
        res_copy = copy_cap.execute(source=str(src_file), destination=str(new_dir))
        assert res_copy.success is True
        assert (new_dir / "hello.txt").exists()
        assert (new_dir / "hello.txt").read_text() == "hello world"

    def test_move_file(self, tmp_path):
        src_file = tmp_path / "source.txt"
        src_file.write_text("move me")
        dest_file = tmp_path / "dest.txt"

        move_cap = MoveFileCapability()
        res = move_cap.execute(source=str(src_file), destination=str(dest_file))
        assert res.success is True
        assert not src_file.exists()
        assert dest_file.exists()
        assert dest_file.read_text() == "move me"

    def test_delete_file_safety_invariants(self, tmp_path):
        target = tmp_path / "deleteme.txt"
        target.write_text("secret")

        del_cap = DeleteFileCapability()
        assert del_cap.risk_category == ActionCategory.DESTRUCTIVE
        assert del_cap.requires_confirmation is True
        assert del_cap.data_classification == DataClassification.USER_CONFIRMATION_REQUIRED

        # Without confirmation: must return CONFIRMATION_REQUIRED
        res_unconfirmed = del_cap.execute(path=str(target), confirmed=False)
        assert res_unconfirmed.success is False
        assert res_unconfirmed.status == ExecutionStatus.CONFIRMATION_REQUIRED
        assert "permanently delete" in res_unconfirmed.message
        assert target.exists()  # Not deleted!

        # With explicit confirmation: deletes file
        res_confirmed = del_cap.execute(path=str(target), confirmed=True)
        assert res_confirmed.success is True
        assert res_confirmed.status == ExecutionStatus.SUCCESS
        assert not target.exists()  # Deleted!

    def test_delete_file_refuses_root_and_home(self):
        del_cap = DeleteFileCapability()
        res_root = del_cap.execute(path="/", confirmed=True)
        assert res_root.success is False
        assert res_root.status == ExecutionStatus.FAILED
        assert "Refusing to delete system root" in res_root.error

        res_home = del_cap.execute(path=str(Path.home()), confirmed=True)
        assert res_home.success is False
        assert res_home.status == ExecutionStatus.FAILED
        assert "Refusing to delete system root or user home" in res_home.error
