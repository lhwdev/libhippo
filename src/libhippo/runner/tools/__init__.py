"""Unified coding tools package for GeneralAgentHarness."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Coroutine

from libhippo.runner.project import ProjectManager
from libhippo.runner.sandbox import SandboxRunner
from libhippo.runner.tools.base import BaseToolSuite, ToolExecutionError
from libhippo.runner.tools.exploration_tools import ExplorationTools
from libhippo.runner.tools.file_tools import FileTools
from libhippo.runner.tools.knowledge_tools import KnowledgeTools
from libhippo.runner.tools.status_tools import StatusTools
from libhippo.runner.tools.subagent_tools import SubagentTools
from libhippo.runner.tools.terminal_tools import TerminalTools
from libhippo.runner.tools.web_tools import WebTools
from libhippo.runner.types import ToolDefinition
from libhippo.storage.store import KnowledgeStore
from libhippo.tools.retrieval import KnowledgeDispatcher


class CodingToolSuite(
    FileTools,
    ExplorationTools,
    TerminalTools,
    StatusTools,
    SubagentTools,
    WebTools,
    KnowledgeTools,
):
    """Unified, production-grade tool registry coordinating file, terminal, exploration, and knowledge tools."""

    def __init__(
        self,
        workspace_root: Path,
        sandbox: SandboxRunner,
        project_manager: ProjectManager,
        store: KnowledgeStore | None = None,
        dispatcher: KnowledgeDispatcher | None = None,
        event_callback: Callable[[dict[str, Any]], Coroutine[Any, Any, None]] | None = None,
        subagent_manager: Any = None,
    ) -> None:
        BaseToolSuite.__init__(
            self,
            workspace_root=workspace_root,
            sandbox=sandbox,
            project_manager=project_manager,
            event_callback=event_callback,
        )
        StatusTools.__init__(
            self,
            workspace_root=workspace_root,
            sandbox=sandbox,
            project_manager=project_manager,
            event_callback=event_callback,
        )
        SubagentTools.__init__(
            self,
            workspace_root=workspace_root,
            sandbox=sandbox,
            project_manager=project_manager,
            event_callback=event_callback,
            subagent_manager=subagent_manager,
        )
        KnowledgeTools.__init__(
            self,
            workspace_root=workspace_root,
            sandbox=sandbox,
            project_manager=project_manager,
            event_callback=event_callback,
            store=store,
            dispatcher=dispatcher,
        )

    @property
    def tools(self) -> dict[str, ToolDefinition]:
        """Convenience property returning mapping of tool definitions."""
        return self.get_tool_definitions()

    def get_tool_definitions(self) -> dict[str, ToolDefinition]:
        """Return composite mapping of all registered tool definitions."""
        defs: dict[str, ToolDefinition] = {}
        defs.update(FileTools.get_tool_definitions(self))
        defs.update(ExplorationTools.get_tool_definitions(self))
        defs.update(TerminalTools.get_tool_definitions(self))
        defs.update(StatusTools.get_tool_definitions(self))
        defs.update(SubagentTools.get_tool_definitions(self))
        defs.update(WebTools.get_tool_definitions(self))
        defs.update(KnowledgeTools.get_tool_definitions(self))
        return defs


__all__ = [
    "BaseToolSuite",
    "CodingToolSuite",
    "ExplorationTools",
    "FileTools",
    "KnowledgeTools",
    "StatusTools",
    "SubagentTools",
    "TerminalTools",
    "ToolExecutionError",
    "WebTools",
]
