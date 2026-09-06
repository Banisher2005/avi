"""Unit tests for screenshot capability, dimensions parser, and local privacy boundaries."""

import struct
from pathlib import Path
from unittest.mock import MagicMock, patch

from avi.capabilities.desktop.screenshot import (
    ScreenshotCapability,
    detect_display_session,
    get_png_dimensions,
)
from avi.capabilities.models import DataClassification, ExecutionStatus


def _create_minimal_png(width: int, height: int) -> bytes:
    """Construct a minimal valid PNG binary header with an IHDR chunk for testing."""
    signature = b"\x89PNG\r\n\x1a\n"
    ihdr_data = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    ihdr_length = len(ihdr_data).to_bytes(4, "big")
    ihdr_chunk = ihdr_length + b"IHDR" + ihdr_data + b"\x00\x00\x00\x00"  # mock CRC
    return signature + ihdr_chunk


class TestScreenshotCapability:
    def test_get_png_dimensions(self, tmp_path):
        png_path = tmp_path / "test.png"
        png_path.write_bytes(_create_minimal_png(1920, 1080))

        dims = get_png_dimensions(png_path)
        assert dims == (1920, 1080)

    def test_get_png_dimensions_invalid_file(self, tmp_path):
        txt_path = tmp_path / "test.txt"
        txt_path.write_text("not a png")
        assert get_png_dimensions(txt_path) is None

    def test_display_session_detection(self):
        with patch.dict("os.environ", {"WAYLAND_DISPLAY": "wayland-0"}, clear=True):
            session = detect_display_session()
            assert session["display_server"] == "wayland"

        with patch.dict("os.environ", {"DISPLAY": ":0"}, clear=True):
            session = detect_display_session()
            assert session["display_server"] == "x11"

        with patch.dict("os.environ", {}, clear=True):
            session = detect_display_session()
            assert session["display_server"] == "unknown"

    def test_screenshot_execution_with_mock_runner(self, tmp_path):
        dest_dir = tmp_path / "Screenshots"
        mock_runner = MagicMock()

        def fake_runner(cmd, timeout):
            # cmd is e.g. ["grim", "/path/to/screenshot.png"]
            out_file = Path(cmd[-1])
            out_file.parent.mkdir(parents=True, exist_ok=True)
            out_file.write_bytes(_create_minimal_png(2560, 1440))
            return True

        mock_runner.side_effect = fake_runner

        cap = ScreenshotCapability(destination_dir=dest_dir, backend_runner=mock_runner)

        # Mock shutil.which so 'grim' appears available
        with patch("shutil.which", side_effect=lambda x: f"/usr/bin/{x}" if x == "grim" else None):
            res = cap.execute()

        assert res.success is True
        assert res.status == ExecutionStatus.SUCCESS
        assert res.classification == DataClassification.LOCAL_ONLY
        assert res.data["dimensions"] == "2560x1440"
        assert res.data["backend"] == "grim"
        assert Path(res.data["path"]).exists()
        assert "Captured screenshot" in res.message

    def test_screenshot_privacy_is_local_only(self):
        cap = ScreenshotCapability()
        assert cap.data_classification == DataClassification.LOCAL_ONLY
        metadata = cap.to_metadata()
        assert metadata["data_classification"] == "LOCAL_ONLY"

    def test_screenshot_failure_when_no_backend_installed(self, tmp_path):
        cap = ScreenshotCapability(destination_dir=tmp_path)
        with patch("shutil.which", return_value=None):
            res = cap.execute()

        assert res.success is False
        assert "No supported screenshot tool found" in res.error
