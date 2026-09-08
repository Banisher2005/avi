"""Unit tests for capability-aware ToolSelector."""

import pytest
from unittest.mock import MagicMock
from avi.agent.tool_selection import ToolSelector
from avi.capabilities.registry import CapabilityRegistry
from avi.memory.models import Memory


@pytest.fixture
def mock_registry():
    registry = MagicMock(spec=CapabilityRegistry)
    catalog = [
        {
            "name": "desktop.screenshot",
            "description": "Capture full screen or active window screenshot",
            "tags": ["desktop", "screenshot", "screen", "capture"],
            "parameters": {},
            "examples": ["take a screenshot"],
        },
        {
            "name": "system.volume",
            "description": "Get or set system volume",
            "tags": ["system", "volume", "sound", "audio"],
            "parameters": {},
            "examples": ["set volume to 50%"],
        },
        {
            "name": "filesystem.search",
            "description": "Find files and folders on the system",
            "tags": ["filesystem", "file", "search", "find"],
            "parameters": {},
            "examples": ["find files matching report"],
        },
        {
            "name": "web.search",
            "description": "Search the web using search engines",
            "tags": ["web", "search", "google", "browser"],
            "parameters": {},
            "examples": ["search web for news"],
        },
    ]
    registry.get_model_catalog.return_value = catalog
    return registry


def test_tool_selection_by_keywords(mock_registry):
    selector = ToolSelector(registry=mock_registry)
    selected = selector.select_capabilities("take a screenshot of my desktop")
    assert len(selected) > 0
    assert selected[0]["name"] == "desktop.screenshot"


def test_tool_selection_volume(mock_registry):
    selector = ToolSelector(registry=mock_registry)
    selected = selector.select_capabilities("mute the sound volume")
    assert len(selected) > 0
    assert selected[0]["name"] == "system.volume"


def test_tool_selection_with_memories(mock_registry):
    selector = ToolSelector(registry=mock_registry)
    memories = [
        Memory(id="1", content="Preferred search browser is firefox", category="preference")
    ]
    selected = selector.select_capabilities("search for python tutorials", memories=memories)
    assert len(selected) > 0
    assert selected[0]["name"] in ("web.search", "filesystem.search")
