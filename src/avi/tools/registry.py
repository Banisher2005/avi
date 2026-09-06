"""Central registry for discovering and accessing read-only tools."""

from typing import Iterator

from avi.tools.base import BaseTool
from avi.tools.filesystem import FileMetadataTool, ListDirectoryTool
from avi.tools.git import GitBranchTool, GitLogTool, GitStatusTool
from avi.tools.system import DiskUsageTool, ProcessesTool, SystemInfoTool


class ToolRegistry:
    """Registry maintaining available tools."""

    def __init__(self) -> None:
        self._tools: dict[str, BaseTool] = {}

    def register(self, tool: BaseTool) -> None:
        """Register a new tool instance."""
        self._tools[tool.name] = tool

    def get(self, name: str) -> BaseTool | None:
        """Look up a tool by its unique name."""
        return self._tools.get(name)

    def list_tools(self) -> list[BaseTool]:
        """Return all registered tool instances."""
        return list(self._tools.values())

    def list_names(self) -> list[str]:
        """Return names of all registered tools."""
        return list(self._tools.keys())

    def __contains__(self, name: str) -> bool:
        return name in self._tools

    def __len__(self) -> int:
        return len(self._tools)

    def __iter__(self) -> Iterator[BaseTool]:
        return iter(self._tools.values())


def create_default_registry() -> ToolRegistry:
    """Instantiate and populate the default registry with all 8 read-only tools."""
    registry = ToolRegistry()
    registry.register(ListDirectoryTool())
    registry.register(FileMetadataTool())
    registry.register(ProcessesTool())
    registry.register(DiskUsageTool())
    registry.register(SystemInfoTool())
    registry.register(GitStatusTool())
    registry.register(GitBranchTool())
    registry.register(GitLogTool())
    return registry
