"""TaskSolverAgent: Primary problem-solving and code generation agent with gpt-6.1-sol."""

from __future__ import annotations

from typing import Any

from autogen_agentchat.agents import AssistantAgent
from autogen_core.models import ChatCompletionClient

from libhippo.agents.base import BaseHippoAgent
from libhippo.agents.prompts import get_agent_system_prompt
from libhippo.models.llm import create_chat_client
from libhippo.storage.store import KnowledgeStore
from libhippo.tools.registry import ToolRegistry
from libhippo.tools.retrieval import KnowledgeDispatcher

# TODO: will be implemented later, as agent harness

class TaskSolverAgent(AssistantAgent, BaseHippoAgent):
    """Primary problem-solving agent in LibHippo.

    Equipped with query_knowledge supporting parallel tool calling. Formulates
    unambiguous, self-contained queries before executing tasks.
    """

    def __init__(
        self,
        name: str = "TaskSolverAgent",
        description: str = "Primary software engineering problem solver adhering to retrieved knowledge.",
        model_client: ChatCompletionClient | None = None,
        dispatcher: KnowledgeDispatcher | None = None,
        store: KnowledgeStore | None = None,
        tools: list[Any] | None = None,
        system_message: str | None = None,
        max_tool_iterations: int = 10,
    ) -> None:
        client = model_client or create_chat_client("task_solver")
        sys_msg = system_message or get_agent_system_prompt("task_solver")

        all_tools = list(tools) if tools else []
        if not all_tools and (dispatcher or store):
            effective_store = store or (dispatcher.store if dispatcher else None)
            if effective_store:
                reg = ToolRegistry(store=effective_store, dispatcher=dispatcher)
                all_tools.append(reg.get_query_knowledge_tool())

        AssistantAgent.__init__(
            self,
            name=name,
            model_client=client,
            tools=all_tools,
            system_message=sys_msg,
            description=description,
            max_tool_iterations=max_tool_iterations,
        )
        BaseHippoAgent.__init__(self, name=name, description=description)
        self.dispatcher = dispatcher
        self.store = store

    async def solve(self, task: str) -> str:
        """Execute a coding task through the AutoGen tool loop and return final solution."""
        result = await self.run(task=task)
        if not result.messages:
            return ""
        last_message = result.messages[-1].content
        return last_message if isinstance(last_message, str) else str(last_message)
