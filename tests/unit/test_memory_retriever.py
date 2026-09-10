"""Unit tests for MemoryRetriever."""

import pytest

from avi.memory.manager import MemoryManager
from avi.memory.models import Memory
from avi.memory.retriever import MemoryRetriever
from avi.storage.database import Database


@pytest.fixture
def temp_db(tmp_path):
    db_path = tmp_path / "test_avi.db"
    return Database(db_path=db_path)


@pytest.fixture
def memory_manager(temp_db):
    return MemoryManager(database=temp_db)


@pytest.fixture
def memory_retriever(temp_db, memory_manager):
    return MemoryRetriever(database=temp_db, memory_manager=memory_manager)


def test_keyword_extraction(memory_retriever):
    keywords = memory_retriever.extract_keywords("Please find my projects in the python folder")
    assert "projects" in keywords
    assert "python" in keywords
    assert "folder" in keywords
    assert "please" in keywords or "find" not in keywords


def test_search_and_ranking(memory_retriever, memory_manager):
    memory_manager.remember("Working on the Avi autonomous agent project", category="projects")
    memory_manager.remember("Likes dark mode themes", category="preference")
    memory_manager.remember("Python workspace is located in /home/user/workspace/avi", category="workspace")

    # Search for agent
    results = memory_retriever.search("autonomous agent")
    assert len(results) >= 1
    assert "autonomous agent" in results[0].content

    # Search for theme
    theme_results = memory_retriever.search("dark mode")
    assert len(theme_results) >= 1
    assert "dark mode" in theme_results[0].content


def test_preference_injection_browser(memory_retriever, memory_manager):
    memory_manager.set_preferred_browser("firefox")

    results = memory_retriever.search("open the browser and search for cats")
    assert any("firefox" in m.content.lower() for m in results)


def test_preference_injection_folder(memory_retriever, memory_manager):
    memory_manager.set_preferred_folder("projects", "/home/user/my_projects")

    results = memory_retriever.search("navigate to my project folder")
    assert any("/home/user/my_projects" in m.content for m in results)


def test_format_context(memory_retriever):
    mems = [
        Memory(id="1", content="Preferred editor is VS Code", category="preference"),
        Memory(id="2", content="Primary project is avi", category="project"),
    ]
    ctx = memory_retriever.format_context(mems)
    assert "User Knowledge & Preferences:" in ctx
    assert "- [preference] Preferred editor is VS Code" in ctx
    assert "- [project] Primary project is avi" in ctx


def test_empty_search(memory_retriever):
    assert memory_retriever.format_context([]) == ""
    assert memory_retriever.search("") == []
