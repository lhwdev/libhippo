"""GeneralAgentHarness: Headless, event-driven autonomous software engineering engine."""

from __future__ import annotations

import asyncio
import datetime
import uuid
from typing import Any, AsyncIterator

from autogen_core.models import (
    AssistantMessage,
    ChatCompletionClient,
    SystemMessage,
    UserMessage,
)

from libhippo.models.llm import create_chat_client
from libhippo.runner.config import HarnessConfig, SkillDefinition
from libhippo.runner.discovery import ResourceDiscovery
from libhippo.runner.governor import WorkloadGovernor
from libhippo.runner.memory import ContextMemory
from libhippo.runner.persistence import ConversationSession
from libhippo.runner.project import ProjectManager
from libhippo.runner.sandbox import BubblewrapSandboxRunner, SandboxRunner
from libhippo.runner.sidecar import SidecarExecutor
from libhippo.runner.subagents import SubagentManager
from libhippo.runner.tools import CodingToolSuite
from libhippo.runner.transport import OpenAIResponsesWebSocketClient
from libhippo.runner.types import (
    ApprovalRequestEvent,
    HarnessEvent,
    InterruptEvent,
    ModalQuestionEvent,
    PhaseTransitionEvent,
    TokenChunkEvent,
    ToolCallResultEvent,
    ToolCallStartEvent,
    ToolDefinition,
    TurnCompletedEvent,
)
from libhippo.storage.store import KnowledgeStore
from libhippo.tools.retrieval import KnowledgeDispatcher


class GeneralAgentHarness:
    """Production-grade execution harness coordinating autonomous coding phases."""

    def __init__(
        self,
        config: HarnessConfig | None = None,
        model_client: ChatCompletionClient | None = None,
        store: KnowledgeStore | None = None,
        dispatcher: KnowledgeDispatcher | None = None,
        sandbox: SandboxRunner | None = None,
        session: ConversationSession | None = None,
    ) -> None:
        self.config = config or HarnessConfig()
        self.workspace_root = self.config.workspace_root.resolve()
        self.store = store or (dispatcher.store if dispatcher else None)
        self.dispatcher = dispatcher
        self.current_phase: str = "alignment"
        self._interrupt_event = asyncio.Event()
        self._is_paused: bool = False
        self._current_gen_task: asyncio.Task[Any] | None = None

        # 1. Project & Sandbox
        self.project_manager = ProjectManager(workspace_root=self.workspace_root, config=self.config)
        self.sandbox = sandbox or BubblewrapSandboxRunner(
            workspace_root=self.workspace_root,
            project_manager=self.project_manager,
            tasks_dir=self.workspace_root / "tasks",
        )

        # 2. Persistence Session
        conv_id = f"conv-{uuid.uuid4().hex[:8]}"
        self.session = session or ConversationSession(
            project_id=self.config.get_project_id(),
            conversation_id=conv_id,
            config=self.config,
        )

        # 3. Model Client & Transport
        if model_client is not None:
            self.model_client = model_client
        else:
            if self.config.transport_mode == "websocket":
                self.model_client = OpenAIResponsesWebSocketClient(
                    model=self.config.model,
                    temperature=self.config.temperature,
                    enable_http_fallback=self.config.enable_http_fallback,
                )
            else:
                self.model_client = create_chat_client(
                    "task_solver",
                    model=self.config.model,
                    temperature=self.config.temperature,
                )

        # 4. Context Memory & Workload Governor
        self.memory = ContextMemory(model_name=self.config.model)
        self.governor = WorkloadGovernor(config=self.config, memory=self.memory)

        # 5. Tool Suite
        self.tools = CodingToolSuite(
            workspace_root=self.workspace_root,
            sandbox=self.sandbox,
            project_manager=self.project_manager,
            store=self.store,
            dispatcher=self.dispatcher,
        )
        self.registered_tools = self.tools.get_tool_definitions()

        # 6. Subagents & Sidecars
        self.subagents = SubagentManager(model_client=self.model_client, session=self.session)
        self.tools.subagent_manager = self.subagents
        self.sidecar = SidecarExecutor(model_client=self.model_client)

        # 7. Resource Discovery & Extensibility
        self.discovery = ResourceDiscovery(workspace_root=self.workspace_root, config=self.config)
        self.skills: dict[str, SkillDefinition] = {}
        self.auto_discover()

        # Initialize Zone 1 static prefix
        self.init_prefix()

    def auto_discover(self) -> None:
        """Automatically discover project AGENTS.md, skills, and MCP configurations."""
        self.skills = self.discovery.discover_skills()

    def init_prefix(self) -> None:
        """Initialize Zone 1 static prefix guaranteed to remain bitwise invariant."""
        agents_rules = self.discovery.discover_agents_markdown()
        rule_texts: list[str] = []
        if "global" in agents_rules:
            rule_texts.append(f"## Developer Global Guidelines\n{agents_rules['global']}")
        if "project" in agents_rules:
            rule_texts.append(f"## Project Repository Guidelines\n{agents_rules['project']}")

        combined_rules = "\n\n".join(rule_texts) if rule_texts else "Follow clean code and test-driven standards."

        persona = (
            "You are LibHippo's autonomous software engineering agent. "
            "You write robust code, execute sandboxed terminal commands, "
            "adhere strictly to repository conventions, and verify code using tests.\n\n"
            f"{combined_rules}"
        )
        repo_profile = f"Workspace Root: {self.workspace_root.name}"

        tool_defs = [
            {"name": t.name, "description": t.description}
            for t in self.registered_tools.values()
        ]
        self.memory.set_zone1_prefix(
            system_persona=persona,
            repo_profile=repo_profile,
            tool_definitions=tool_defs,
        )

    def transition_phase(self, new_phase: str) -> PhaseTransitionEvent:
        """Transition lifecycle state machine."""
        old_phase = self.current_phase
        self.current_phase = new_phase
        return PhaseTransitionEvent(from_phase=old_phase, to_phase=new_phase)

    async def step(self, user_input: str) -> str:
        """Execute one conversational round collecting all streamed events."""
        final_answer = ""
        async for event in self.stream(user_input):
            if isinstance(event, TokenChunkEvent):
                final_answer += event.delta
            elif isinstance(event, TurnCompletedEvent):
                pass
        return final_answer

    async def stream(self, user_input: str) -> AsyncIterator[HarnessEvent]:
        """Stream asynchronous execution events through the 5-phase harness."""
        if self._interrupt_event.is_set():
            self._interrupt_event.clear()
            self._is_paused = False

        start_time = datetime.datetime.now(datetime.timezone.utc)
        self.governor.start_turn()

        # Phase 1: Alignment & User Turn Injection
        yield self.transition_phase("alignment")
        u_msg = self.memory.append_user_turn(
            user_content=user_input,
            timestamp=start_time.isoformat(),
            branch="main",
        )
        await self.session.append_message(u_msg)

        # Evaluate Context Budgeting & Compaction
        compaction_res = self.governor.check_context_and_compact()
        if compaction_res["status"] == "compacted":
            yield TokenChunkEvent(delta=f"\n[Context compacted: {compaction_res['evicted_tokens']} tokens reclaimed]\n")

        # Phase 2: Planning & Execution
        yield self.transition_phase("planning")
        yield self.transition_phase("implementation")

        # Prepare messages for LLM completion
        all_msgs = self.memory.get_all_messages()
        llm_messages: list[Any] = []
        for m in all_msgs:
            if m.role == "system":
                llm_messages.append(SystemMessage(content=m.content))
            elif m.role in ("user", "tool"):
                llm_messages.append(UserMessage(content=m.content, source="user"))
            elif m.role == "assistant":
                llm_messages.append(AssistantMessage(content=m.content, source="assistant"))

        # Generate response from model
        out_content = ""
        self._current_gen_task = asyncio.current_task()
        try:
            res = await self.model_client.create(messages=llm_messages)
            out_content = res.content if isinstance(res.content, str) else str(res.content)
            yield TokenChunkEvent(delta=out_content)

            # Record assistant turn
            as_msg = self.memory.append_assistant_turn(out_content)
            await self.session.append_message(as_msg)
        except asyncio.CancelledError:
            out_content = "[Generation cancelled by user interrupt]"
            yield TokenChunkEvent(delta=f"\n{out_content}\n")
        except Exception as e:
            out_content = f"Execution error: {e}"
            yield TokenChunkEvent(delta=f"\n{out_content}\n")
        finally:
            self._current_gen_task = None

        # Phase 4 & 5: Review & Post-Task Maintenance
        yield self.transition_phase("review")
        yield self.transition_phase("maintenance")
        await self.post_task_maintenance()

        elapsed = (datetime.datetime.now(datetime.timezone.utc) - start_time).total_seconds()
        yield TurnCompletedEvent(
            turn_index=self.governor.current_turns,
            total_tokens=self.memory.get_total_tokens(),
            duration_seconds=elapsed,
            response=out_content,
        )

    run = stream

    async def ask_sidecar(self, query: str) -> str:
        """Execute concurrent /btw query against warm parent KV cache."""
        return await self.sidecar.ask(query=query, parent_memory=self.memory)

    async def invoke_skill(self, name: str, args: dict[str, Any]) -> Any:
        """Execute a discovered skill."""
        if name not in self.skills:
            raise KeyError(f"Skill '{name}' not found. Available: {list(self.skills.keys())}")
        skill = self.skills[name]
        return await self.step(f"[Invoking Skill: {skill.name}]\n{skill.system_prompt}\nArguments: {args}")

    async def steer(self, guidance: str) -> dict[str, Any]:
        """Send mid-turn steering message over WebSocket without tearing down connection."""
        result: dict[str, Any] = {"status": "steered"}
        if hasattr(self.model_client, "steer"):
            result = await self.model_client.steer(guidance)

        steer_tag = f'<steer_event guidance="{guidance}"/>'
        st_msg = self.memory.append_user_turn(steer_tag)
        await self.session.append_message(st_msg)
        return result

    def interrupt(self, reason: str = "paused_by_user", steer_text: str | None = None) -> None:
        """Signal cancellation token to pause harness, steer or cancel WebSocket, and kill active subprocesses."""
        self._interrupt_event.set()
        self._is_paused = True

        # 1. Steer or cancel model client
        if steer_text is not None and hasattr(self.model_client, "steer"):
            asyncio.create_task(self.steer(steer_text))
        elif hasattr(self.model_client, "cancel"):
            asyncio.create_task(self.model_client.cancel())

        # 2. Cancel in-flight generation task if active
        if self._current_gen_task is not None and not self._current_gen_task.done():
            self._current_gen_task.cancel()

        # 3. Kill active sandbox background processes if any
        if hasattr(self.sandbox, "active_tasks"):
            for tid in list(self.sandbox.active_tasks.keys()):
                asyncio.create_task(self.sandbox.manage_task("kill", task_id=tid))

        # 4. Record interrupt event in memory and durable session
        int_msg = self.memory.append_assistant_turn(f'<interrupt_event status="paused_by_user" reason="{reason}"/>')
        asyncio.create_task(self.session.append_message(int_msg))

    def compact_context(self) -> int:
        """Trigger explicit Zone 3 tool output eviction."""
        return self.memory.compact_zone3(self.config.compaction_target_tokens)

    async def post_task_maintenance(self) -> None:
        """Execute background maintenance (e.g. vector compaction)."""
        if self.store is not None:
            if hasattr(self.store, "post_task_maintenance"):
                await self.store.post_task_maintenance()
            elif hasattr(self.store, "compact_if_needed"):
                await self.store.compact_if_needed()
