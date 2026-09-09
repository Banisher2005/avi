"""Unit tests for Browser Download Detection Capability."""

import time
from pathlib import Path
from unittest.mock import MagicMock

from avi.browser.downloads import DownloadInfo, DownloadsWatcher
from avi.capabilities.browser.download import BrowserDownloadCapability
from avi.capabilities.models import ExecutionStatus
from avi.capabilities.registry import create_default_capability_registry


class TestDownloadsWatcher:
    def test_filter_partial_downloads(self, tmp_path: Path):
        watcher = DownloadsWatcher(downloads_dir=tmp_path)

        # Create complete and partial files
        complete_file = tmp_path / "report.pdf"
        complete_file.write_bytes(b"PDF content 12345")

        crdownload = tmp_path / "video.mp4.crdownload"
        crdownload.write_bytes(b"partial video data")

        part_file = tmp_path / "archive.zip.part"
        part_file.write_bytes(b"partial zip data")

        tmp_file = tmp_path / "data.tmp"
        tmp_file.write_bytes(b"tmp data")

        downloads = watcher.get_recent_downloads(max_age_seconds=60.0)
        assert len(downloads) == 1
        assert downloads[0].filename == "report.pdf"
        assert downloads[0].size_bytes == 17
        assert downloads[0].extension == ".pdf"
        assert downloads[0].is_complete is True

    def test_filter_by_pattern(self, tmp_path: Path):
        watcher = DownloadsWatcher(downloads_dir=tmp_path)

        f1 = tmp_path / "data.csv"
        f1.write_text("a,b,c\n1,2,3")
        f2 = tmp_path / "notes.txt"
        f2.write_text("some notes")

        csv_downloads = watcher.get_recent_downloads(pattern="*.csv")
        assert len(csv_downloads) == 1
        assert csv_downloads[0].filename == "data.csv"


class TestBrowserDownloadCapability:
    def test_immediate_check_empty(self, tmp_path: Path):
        watcher = DownloadsWatcher(downloads_dir=tmp_path)
        cap = BrowserDownloadCapability(watcher=watcher)

        res = cap.execute()
        assert res.success is True
        assert res.status == ExecutionStatus.SUCCESS
        assert res.data["count"] == 0
        assert "No recent" in res.message

    def test_immediate_check_with_files(self, tmp_path: Path):
        watcher = DownloadsWatcher(downloads_dir=tmp_path)
        test_file = tmp_path / "whitepaper.pdf"
        test_file.write_bytes(b"1234567890")

        cap = BrowserDownloadCapability(watcher=watcher)
        res = cap.execute()

        assert res.success is True
        assert res.status == ExecutionStatus.SUCCESS
        assert res.data["count"] == 1
        assert res.data["downloads"][0]["filename"] == "whitepaper.pdf"

    def test_wait_for_download_success(self):
        mock_watcher = MagicMock(spec=DownloadsWatcher)
        mock_watcher.wait_for_download.return_value = DownloadInfo(
            path="/home/user/Downloads/invoice.pdf",
            filename="invoice.pdf",
            size_bytes=4096,
            extension=".pdf",
            modified_at=time.time(),
            is_complete=True,
        )

        cap = BrowserDownloadCapability(watcher=mock_watcher)
        res = cap.execute(filename="invoice.pdf", timeout=2.0)

        assert res.success is True
        assert res.status == ExecutionStatus.SUCCESS
        assert res.data["filename"] == "invoice.pdf"
        assert res.data["size_bytes"] == 4096
        assert "Verified completed download" in res.message

    def test_wait_for_download_timeout(self):
        mock_watcher = MagicMock(spec=DownloadsWatcher)
        mock_watcher.wait_for_download.return_value = None

        cap = BrowserDownloadCapability(watcher=mock_watcher)
        res = cap.execute(filename="large_file.iso", timeout=1.0)

        assert res.success is False
        assert res.status == ExecutionStatus.FAILED
        assert "No completed download" in res.error

    def test_registry_integration(self):
        reg = create_default_capability_registry()
        cap = reg.get("browser.download")
        assert cap is not None
        assert reg.get("browser.downloads") is cap
        assert reg.get("detect_download") is cap
        assert reg.get("check_downloads") is cap
