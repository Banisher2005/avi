"""Unit tests for AVI Phase 9 Jarvis-level superpower capabilities."""

import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

from avi.capabilities.desktop.app_launcher import CloseAppCapability
from avi.capabilities.desktop.window import WindowCloseCapability
from avi.capabilities.filesystem.operations import (
    FindDuplicateFilesCapability,
    LargestFilesCapability,
    OrganizeFilesCapability,
    ReadFileCapability,
    RenameFileCapability,
    SearchFileContentCapability,
    WriteFileCapability,
)
from avi.capabilities.memory.operations import RecallCapability, RememberCapability
from avi.capabilities.models import ExecutionStatus
from avi.capabilities.system.command import ExecuteCommandCapability


class TestFilesystemSuperpowers:
    def test_write_and_read_file(self, tmp_path: Path):
        test_file = tmp_path / "hello.txt"
        writer = WriteFileCapability()
        res_write = writer.execute(path=str(test_file), content="Hello, Jarvis!")
        assert res_write.success is True
        assert res_write.status == ExecutionStatus.SUCCESS
        assert test_file.exists()
        assert test_file.read_text() == "Hello, Jarvis!"

        reader = ReadFileCapability()
        res_read = reader.execute(path=str(test_file))
        assert res_read.success is True
        assert res_read.data["content"] == "Hello, Jarvis!"

    def test_search_file_content(self, tmp_path: Path):
        f1 = tmp_path / "doc1.txt"
        f2 = tmp_path / "doc2.txt"
        f1.write_text("The secret project codename is ProjectJarvis.")
        f2.write_text("Nothing to see here.")

        searcher = SearchFileContentCapability()
        res = searcher.execute(path=str(tmp_path), pattern="ProjectJarvis")
        assert res.success is True
        assert res.data["count"] == 1
        assert len(res.data["matches"]) == 1
        assert "ProjectJarvis" in res.data["matches"][0]["preview"]

    def test_find_duplicate_files(self, tmp_path: Path):
        f1 = tmp_path / "orig.txt"
        f2 = tmp_path / "copy.txt"
        f3 = tmp_path / "other.txt"
        f1.write_text("Identical content in both files.")
        f2.write_text("Identical content in both files.")
        f3.write_text("Different content.")

        dup_finder = FindDuplicateFilesCapability()
        res = dup_finder.execute(path=str(tmp_path))
        assert res.success is True
        assert res.data["count"] >= 1
        assert len(res.data["duplicates"]) >= 1

    def test_largest_files(self, tmp_path: Path):
        small = tmp_path / "small.dat"
        big = tmp_path / "big.dat"
        small.write_bytes(b"x" * 100)
        big.write_bytes(b"x" * 5000)

        largest_cap = LargestFilesCapability()
        res = largest_cap.execute(path=str(tmp_path), limit=2)
        assert res.success is True
        assert len(res.data["files"]) == 2
        assert res.data["files"][0]["name"] == "big.dat"
        assert res.data["files"][0]["size_bytes"] == 5000

    def test_organize_files_by_extension(self, tmp_path: Path):
        (tmp_path / "a.pdf").write_text("pdf content")
        (tmp_path / "b.txt").write_text("txt content")
        (tmp_path / "c.png").write_bytes(b"\x89PNG")

        organizer = OrganizeFilesCapability()
        res = organizer.execute(path=str(tmp_path), strategy="extension")
        assert res.success is True
        assert res.data["moved_count"] == 3
        assert (tmp_path / "pdf" / "a.pdf").exists()
        assert (tmp_path / "txt" / "b.txt").exists()
        assert (tmp_path / "png" / "c.png").exists()

    def test_rename_file(self, tmp_path: Path):
        old_f = tmp_path / "old.txt"
        old_f.write_text("rename me")

        renamer = RenameFileCapability()
        res = renamer.execute(path=str(old_f), new_name="new.txt")
        assert res.success is True
        assert not old_f.exists()
        new_f = tmp_path / "new.txt"
        assert new_f.exists()
        assert new_f.read_text() == "rename me"


class TestDesktopSuperpowers:
    def test_close_app_capability(self):
        closer = CloseAppCapability()
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = subprocess.CompletedProcess(
                args=["pkill", "-f", "vlc"], returncode=0, stdout="closed", stderr=""
            )
            res = closer.execute(app_name="vlc")
            assert res.success is True
            assert res.status == ExecutionStatus.SUCCESS

    def test_window_close_capability(self):
        win_closer = WindowCloseCapability()
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = subprocess.CompletedProcess(
                args=["wmctrl", "-c", "Firefox"], returncode=0, stdout="", stderr=""
            )
            res = win_closer.execute(title="Firefox")
            assert res.success is True


class TestSystemCommandSuperpower:
    def test_execute_safe_command(self):
        cmd_cap = ExecuteCommandCapability()
        # Confirmation required without flag
        res_unconf = cmd_cap.execute(command=f"{sys.executable} -c 'print(\"hello-avi\")'")
        assert res_unconf.status == ExecutionStatus.CONFIRMATION_REQUIRED

        # With confirmed=True executes successfully
        res = cmd_cap.execute(command=f"{sys.executable} -c 'print(\"hello-avi\")'", confirmed=True)
        assert res.success is True
        assert res.data["exit_code"] == 0
        assert "hello-avi" in res.data["stdout"]

    def test_blocks_dangerous_destructive_command(self):
        cmd_cap = ExecuteCommandCapability()
        res = cmd_cap.execute(command="rm -rf /")
        assert res.success is False
        assert "blocked" in res.error.lower() or "safety" in res.error.lower()


class TestMemorySuperpowers:
    def test_remember_and_recall(self):
        mock_retriever = MagicMock()
        mock_retriever.remember.return_value = MagicMock(
            id="m1",
            content="User prefers Python over Java",
            category="preference",
        )
        fake_mem = MagicMock(
            memory_id="m1",
            content="User prefers Python over Java",
            text="User prefers Python over Java",
            similarity=0.95,
            category="preference",
            created_at="2026-01-01",
        )
        fake_mem.to_dict.return_value = {
            "id": "m1",
            "text": "User prefers Python over Java",
            "category": "preference",
        }
        mock_retriever.retrieve.return_value = [fake_mem]
        mock_retriever.recall.return_value = [fake_mem]

        remember_cap = RememberCapability(retriever=mock_retriever)
        res_rem = remember_cap.execute(content="User prefers Python over Java", category="preference")
        assert res_rem.success is True
        mock_retriever.remember.assert_called_once()

        recall_cap = RecallCapability(retriever=mock_retriever)
        res_rec = recall_cap.execute(query="programming language preference")
        assert res_rec.success is True
        assert len(res_rec.data["memories"]) == 1
        assert "Python" in res_rec.data["memories"][0]["text"]
