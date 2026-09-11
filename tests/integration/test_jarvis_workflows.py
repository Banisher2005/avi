"""Integration tests for AVI Phase 9 Jarvis-level computer agent workflows.

Validates:
1. Multi-step download -> search -> rename -> move -> open pipeline (Acceptance Test 1).
2. Browser research -> extract -> save title and URL to text file in Documents (Acceptance Test 2).
3. Concurrent non-blocking fast-path conversation during running agent tasks.
"""

from pathlib import Path
from unittest.mock import MagicMock, patch

from avi.agent.events import EventDispatcher
from avi.agent.executor import AgentExecutor
from avi.agent.orchestrator import AgentOrchestrator
from avi.agent.planner import AgentPlanner
from avi.agent.runtime import AgentRuntime
from avi.capabilities.models import CapabilityResult, ExecutionStatus
from avi.capabilities.registry import create_default_capability_registry
from avi.config import Config
from avi.core.router import Router
from avi.memory.manager import MemoryManager
from avi.memory.retriever import MemoryRetriever
from avi.orchestrator.orchestrator import AssistantOrchestrator
from avi.storage.database import Database


class TestJarvisWorkflows:
    def setup_method(self):
        self.registry = create_default_capability_registry()
        self.planner = AgentPlanner(max_steps=6)
        self.executor = AgentExecutor(registry=self.registry)

    def test_acceptance_workflow_1_pdf_pipeline(self, tmp_path: Path):
        """Acceptance Test 1:

        'Find the latest PDF I downloaded, rename it to project-report.pdf, move it to Documents, and open it.'
        """
        downloads_dir = tmp_path / "Downloads"
        docs_dir = tmp_path / "Documents"
        downloads_dir.mkdir(parents=True)
        docs_dir.mkdir(parents=True)

        # Create dummy downloaded files with different timestamps
        old_pdf = downloads_dir / "old_manual.pdf"
        old_pdf.write_text("old pdf content")

        latest_pdf = downloads_dir / "downloaded_raw_export.pdf"
        latest_pdf.write_text("fresh project report content")

        prompt = (
            f"Find the latest PDF in {downloads_dir}, rename it to project-report.pdf, "
            f"move it to {docs_dir}, and open it"
        )

        plan = self.planner.create_plan(prompt)
        assert plan is not None
        assert len(plan.steps) == 4

        assert plan.steps[0].capability_name == "filesystem.search"
        assert plan.steps[0].arguments["extension"] == "pdf"
        assert plan.steps[0].arguments["path"] == str(downloads_dir)

        assert plan.steps[1].capability_name == "filesystem.rename"
        assert plan.steps[1].arguments["new_name"] == "project-report.pdf"
        assert plan.steps[1].pipe_from_step == 1

        assert plan.steps[2].capability_name == "filesystem.move"
        assert plan.steps[2].arguments["destination"] == str(docs_dir)
        assert plan.steps[2].pipe_from_step == 2

        assert plan.steps[3].capability_name == "desktop.open_file"
        assert plan.steps[3].pipe_from_step == 3

        # Mock desktop open file to avoid launching xdg-open on host
        with patch("subprocess.Popen") as mock_popen:
            mock_popen.return_value = MagicMock(pid=12345)
            exec_result = self.executor.execute_plan(plan)

        assert exec_result.success is True
        assert exec_result.status == ExecutionStatus.SUCCESS

        # Verification of environment state
        final_file = docs_dir / "project-report.pdf"
        assert final_file.exists()
        assert final_file.read_text() == "fresh project report content"
        assert not latest_pdf.exists()

    def test_acceptance_workflow_2_browser_research_and_save(self, tmp_path: Path):
        """Acceptance Test 2:

        'Open Chrome, search for the latest NVIDIA news, find the official announcement,
        and save the title and URL into a text file in Documents.'
        """
        docs_dir = tmp_path / "Documents"
        docs_dir.mkdir(parents=True)

        target_file = docs_dir / "nvidia_news.txt"

        prompt = (
            "Open Chrome, search for the latest NVIDIA news, find the official announcement, "
            f"and save the title and URL into a text file called nvidia_news.txt in {docs_dir}."
        )

        plan = self.planner.create_plan(prompt)
        assert plan is not None
        assert len(plan.steps) == 3

        assert plan.steps[0].capability_name == "browser.navigate"
        assert "nvidia" in plan.steps[0].arguments["url"].lower()

        assert plan.steps[1].capability_name == "browser.extract"

        assert plan.steps[2].capability_name == "filesystem.write_file"
        assert plan.steps[2].arguments["path"] == str(target_file)
        assert plan.steps[2].arguments.get("format") == "title_and_url"
        assert plan.steps[2].pipe_from_step == 2
        assert plan.steps[2].pipe_arg_name == "content"

        # Mock browser capabilities for hermetic offline execution
        mock_extract_data = {
            "title": "NVIDIA Blackwell Architecture Official Announcement",
            "url": "https://nvidianews.nvidia.com/news/blackwell-superchip-architecture",
            "text": "NVIDIA announces next-generation AI computing platform.",
        }

        with patch.object(
            self.registry.get("browser.navigate"),
            "execute",
            return_value=CapabilityResult(
                success=True,
                status=ExecutionStatus.SUCCESS,
                data={"url": plan.steps[0].arguments["url"]},
                message="Navigated successfully",
            ),
        ), patch.object(
            self.registry.get("browser.extract"),
            "execute",
            return_value=CapabilityResult(
                success=True,
                status=ExecutionStatus.SUCCESS,
                data=mock_extract_data,
                message="Extracted news content",
            ),
        ):
            exec_result = self.executor.execute_plan(plan)

        assert exec_result.success is True
        assert target_file.exists()
        content = target_file.read_text()
        assert "Title: NVIDIA Blackwell Architecture Official Announcement" in content
        assert "URL: https://nvidianews.nvidia.com/news/blackwell-superchip-architecture" in content

    def test_concurrent_fastpath_during_agent_execution(self, tmp_path: Path):
        """Verify conversational fast paths run instantaneously through runtime dispatch."""
        config = Config()
        db = Database(db_path=tmp_path / "test_jw.db")
        retriever = MemoryRetriever(database=db)
        memory_mgr = MemoryManager(database=db)
        events = EventDispatcher()

        agent_orch = AgentOrchestrator(
            registry=self.registry,
            database=db,
            memory_retriever=retriever,
            event_dispatcher=events,
        )

        router = MagicMock(spec=Router)
        router.check_fast_path.return_value = None

        assistant_orch = AssistantOrchestrator(
            config=config,
            router=router,
            database=db,
            memory=memory_mgr,
            agent_orchestrator=agent_orch,
        )

        runtime = AgentRuntime(
            config=config,
            router=router,
            assistant_orchestrator=assistant_orch,
            agent_orchestrator=agent_orch,
            events=events,
        )

        # Fast paths must return responses without creating background task or blocking
        queries = ["hello", "hi", "hey", "how are you?", "what can you do?", "how much RAM is free?"]
        for q in queries:
            resp = runtime.dispatch(q)
            assert resp is not None
            assert resp.text is not None
            assert len(resp.text.strip()) > 0
            assert resp.is_background is False
            assert len(runtime.task_registry.active_tasks()) == 0
