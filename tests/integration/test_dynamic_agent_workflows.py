"""Integration tests for Phase 10: Dynamic Autonomous Tool Use & Unseen Novel Workflows."""

import time
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

import pytest

from avi.agent.context import TaskStatus
from avi.agent.orchestrator import AgentOrchestrator
from avi.agent.planner import AgentPlanner
from avi.agent.runtime import AgentRuntime
from avi.capabilities import create_default_capability_registry
from avi.safety.engine import SafetyEngine
from avi.storage.database import Database


@pytest.fixture
def test_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Set up isolated mock user environment for Downloads, Documents, Pictures."""
    downloads = tmp_path / "Downloads"
    documents = tmp_path / "Documents"
    pictures = tmp_path / "Pictures"
    projects = tmp_path / "Projects"
    downloads.mkdir(parents=True, exist_ok=True)
    documents.mkdir(parents=True, exist_ok=True)
    pictures.mkdir(parents=True, exist_ok=True)
    projects.mkdir(parents=True, exist_ok=True)

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)

    db_path = tmp_path / "test_dyn.db"
    db = Database(db_path=db_path)
    registry = create_default_capability_registry()
    safety = SafetyEngine()
    planner = AgentPlanner()
    orchestrator = AgentOrchestrator(registry=registry, safety_engine=safety, database=db, planner=planner)

    return {
        "tmp_path": tmp_path,
        "downloads": downloads,
        "documents": documents,
        "pictures": pictures,
        "projects": projects,
        "db": db,
        "registry": registry,
        "safety": safety,
        "planner": planner,
        "orchestrator": orchestrator,
    }


class TestNovelWorkflows:
    """Acceptance tests A, B, C, D, E without dedicated planner regexes."""

    def test_workflow_a_find_rename_move_open(self, test_env: dict) -> None:
        """TEST A:

        'Find the newest PDF in Downloads, rename it to report-<date>.pdf, move it to Documents, and open it.'
        Must NOT have a dedicated planner branch.
        """
        downloads = test_env["downloads"]
        documents = test_env["documents"]
        orchestrator: AgentOrchestrator = test_env["orchestrator"]
        planner: AgentPlanner = test_env["planner"]

        # Setup files with distinct timestamps
        old_pdf = downloads / "invoice_old.pdf"
        old_pdf.write_text("%PDF-1.4 old invoice content")
        time.sleep(0.05)
        new_pdf = downloads / "quarterly_results.pdf"
        new_pdf.write_text("%PDF-1.4 newest quarterly results")

        prompt = (
            "Find the newest PDF in Downloads, "
            "rename it to report-<date>.pdf, "
            "move it to Documents, "
            "and open it."
        )

        # 1. Verify that AgentPlanner has NO dedicated plan for this sentence
        deterministic_plan = planner.create_plan(prompt)
        assert deterministic_plan is None, "Deterministic planner should not contain a dedicated branch for Test A"

        # Mock Path.home to point to our tmp_path so ~/Downloads and ~/Documents resolve correctly
        with patch.object(Path, "home", return_value=test_env["tmp_path"]):
            ctx = orchestrator.run(prompt)

        assert ctx.status == TaskStatus.COMPLETED
        assert ctx.final_response != ""

        # Verify filesystem postcondition: target file exists in Documents with today's date
        today_str = datetime.now().strftime("%Y-%m-%d")
        expected_name = f"report-{today_str}.pdf"
        dest_file = documents / expected_name
        assert dest_file.exists(), f"Expected {dest_file} to exist after dynamic move"
        assert dest_file.read_text() == "%PDF-1.4 newest quarterly results"

    def test_workflow_b_readme_vscode_chrome(self, test_env: dict) -> None:
        """TEST B:

        'Find my AVI README, open it in VS Code, and open the AVI GitHub page in Chrome.'
        Must NOT have a dedicated planner branch.
        """
        downloads = test_env["downloads"]
        orchestrator: AgentOrchestrator = test_env["orchestrator"]
        planner: AgentPlanner = test_env["planner"]

        readme = downloads / "README_AVI.md"
        readme.write_text("# AVI Project Documentation")

        prompt = "Find my AVI README, open it in VS Code, and open the AVI GitHub page in Chrome."

        # Verify no dedicated planner rule
        assert planner.create_plan(prompt) is None

        with patch.object(Path, "home", return_value=test_env["tmp_path"]):
            ctx = orchestrator.run(prompt)

        assert ctx.status == TaskStatus.COMPLETED
        step_caps = [s.capability_name for s in ctx.steps]
        assert "filesystem.search" in step_caps
        assert any(c in step_caps for c in ("desktop.launch_app", "desktop.open_file", "apps.open"))
        assert any(c in step_caps for c in ("desktop.open_url", "browser.navigate"))

    def test_workflow_c_web_python_release_save(self, test_env: dict) -> None:
        """TEST C:

        'Search the web for the latest Python release, save its title and URL to Documents, and open the saved file.'
        Must NOT have a dedicated planner branch.
        """
        documents = test_env["documents"]
        orchestrator: AgentOrchestrator = test_env["orchestrator"]
        planner: AgentPlanner = test_env["planner"]

        prompt = (
            "Search the web for the latest Python release, "
            "save its title and URL to Documents, "
            "and open the saved file."
        )

        assert planner.create_plan(prompt) is None

        with patch.object(Path, "home", return_value=test_env["tmp_path"]):
            ctx = orchestrator.run(prompt)

        assert ctx.status == TaskStatus.COMPLETED
        # Verify artifact exists in Documents
        saved_file = documents / "python_release.txt"
        assert saved_file.exists()
        content = saved_file.read_text()
        assert "Python" in content or "URL" in content

    def test_workflow_d_duplicate_images_confirmation(self, test_env: dict) -> None:
        """TEST D:

        'Find duplicate images in Pictures, show me how many there are, and ask before deleting them.'
        Must NOT have a dedicated planner branch.
        Must pause/ask before deleting.
        """
        pictures = test_env["pictures"]
        orchestrator: AgentOrchestrator = test_env["orchestrator"]
        planner: AgentPlanner = test_env["planner"]

        # Create duplicate images with same bytes
        img1 = pictures / "photo1.jpg"
        img2 = pictures / "photo2.jpg"
        img1.write_bytes(b"\xff\xd8\xff\xe0" + b"image_data_12345")
        img2.write_bytes(b"\xff\xd8\xff\xe0" + b"image_data_12345")

        prompt = "Find duplicate images in Pictures, show me how many there are, and ask before deleting them."

        assert planner.create_plan(prompt) is None

        with patch.object(Path, "home", return_value=test_env["tmp_path"]):
            # Unconfirmed run: must detect duplicates and pause for confirmation before deleting
            ctx = orchestrator.run(prompt, confirmed=False)

        assert ctx.status in (TaskStatus.COMPLETED, TaskStatus.PAUSED_FOR_CONFIRMATION)
        # Original duplicates must NOT be deleted without confirmation
        assert img1.exists()
        assert img2.exists()

    def test_workflow_e_largest_files_report(self, test_env: dict) -> None:
        """TEST E:

        'Find the largest files in Downloads and create a report containing the top five.'
        Must NOT have a dedicated planner branch.
        """
        downloads = test_env["downloads"]
        documents = test_env["documents"]
        orchestrator: AgentOrchestrator = test_env["orchestrator"]
        planner: AgentPlanner = test_env["planner"]

        # Create 6 files of varying sizes
        for i in range(1, 7):
            f = downloads / f"data_{i}.bin"
            f.write_bytes(b"X" * (i * 1024))

        prompt = "Find the largest files in Downloads and create a report containing the top five."

        assert planner.create_plan(prompt) is None

        with patch.object(Path, "home", return_value=test_env["tmp_path"]):
            ctx = orchestrator.run(prompt)

        assert ctx.status == TaskStatus.COMPLETED
        report_file = documents / "largest_files_report.txt"
        assert report_file.exists()
        report_text = report_file.read_text()
        assert "Top Largest Files" in report_text


class TestAdversarialAdaptation:
    """Tests for changing environment and agent adaptation."""

    def test_target_folder_already_exists(self, test_env: dict) -> None:
        """If destination folder already exists, move succeeds safely without crashing."""
        downloads = test_env["downloads"]
        documents = test_env["documents"]
        orchestrator: AgentOrchestrator = test_env["orchestrator"]

        test_file = downloads / "doc.pdf"
        test_file.write_text("sample content")

        prompt = "Find the newest PDF in Downloads and move it to Documents."

        with patch.object(Path, "home", return_value=test_env["tmp_path"]):
            ctx = orchestrator.run(prompt)

        assert ctx.status == TaskStatus.COMPLETED
        assert (documents / "doc.pdf").exists()

    def test_missing_target_file_reports_safe_failure(self, test_env: dict) -> None:
        """If expected file is missing, agent fails gracefully without crashing."""
        orchestrator: AgentOrchestrator = test_env["orchestrator"]
        prompt = "Find the newest PDF in Downloads and move it to Documents."

        with patch.object(Path, "home", return_value=test_env["tmp_path"]):
            # Downloads is empty
            ctx = orchestrator.run(prompt)

        assert ctx.status in (TaskStatus.FAILED, TaskStatus.COMPLETED)
        assert "error" in ctx.final_response.lower() or "no" in ctx.final_response.lower() or ctx.status == TaskStatus.FAILED


class TestConcurrentInteractivity:
    """Verify that the dynamic agent runs in background and fast paths remain responsive."""

    def test_fast_paths_unblocked_during_runtime(self, test_env: dict) -> None:
        runtime = AgentRuntime(
            agent_orchestrator=test_env["orchestrator"],
        )

        # 1. Fast paths are instant and synchronous
        t0 = time.perf_counter()
        resp_hello = runtime.dispatch("hello")
        elapsed_ms = (time.perf_counter() - t0) * 1000.0

        assert not resp_hello.is_background
        assert "hello" in resp_hello.text.lower() or "how can i help" in resp_hello.text.lower() or "hey" in resp_hello.text.lower()
        assert elapsed_ms < 50.0

        # 2. Complex novel agent goal dispatches to background
        prompt = "Find the largest files in Downloads and create a report containing the top five."
        resp_agent = runtime.dispatch(prompt)

        assert resp_agent.is_background is True
        assert resp_agent.task_id is not None

        # 3. Fast path while background task is running still responds immediately
        t1 = time.perf_counter()
        resp_status = runtime.dispatch("what can you do?")
        elapsed2_ms = (time.perf_counter() - t1) * 1000.0

        assert not resp_status.is_background
        assert elapsed2_ms < 50.0
