"""GeneralAgentHarness: Headless, event-driven autonomous software engineering engine."""

from __future__ import annotations

import asyncio
import datetime
import getpass
import json
import platform
import subprocess
import uuid
from typing import Any, AsyncIterator

from autogen_core import FunctionCall
from autogen_core.models import (
    AssistantMessage,
    ChatCompletionClient,
    FunctionExecutionResult,
    FunctionExecutionResultMessage,
    LLMMessage,
    SystemMessage,
    UserMessage,
)
from autogen_core.tools import ToolSchema


from libhippo.runner.config import HarnessConfig, SkillDefinition
from libhippo.runner.discovery import ResourceDiscovery
from libhippo.runner.governor import WorkloadGovernor
from libhippo.runner.memory import ContextMemory
from libhippo.runner.persistence import ConversationSession
from libhippo.runner.project import ProjectManager
from libhippo.runner.sandbox import BubblewrapSandboxRunner, SandboxRunner
from libhippo.runner.sidecar import KnowledgeHarvestSidecar, SidecarExecutor
from libhippo.runner.subagents import SubagentManager
from libhippo.runner.tools import CodingToolSuite
from libhippo.runner.transport import OpenAIResponsesWebSocketClient
from libhippo.runner.types import (
    HarnessEvent,
    KnowledgeAgentEvent,
    PhaseTransitionEvent,
    TokenChunkEvent,
    ToolCallResultEvent,
    ToolCallStartEvent,
    TurnCompletedEvent,
)
from libhippo.agents.harvest_observer import KnowledgeHarvestObserver
from libhippo.agents.manager import AgentManager
from libhippo.agents.system_prompts.template import (
    assemble_harness_system_prompt,
    load_prompt_template,
)
from libhippo.models.llm import create_chat_client
from libhippo.models.logging_client import wrap_client_if_logging_enabled
from libhippo.storage.mount import create_default_mounts
from libhippo.storage.store import KnowledgeStore
from libhippo.tools.retrieval import KnowledgeDispatcher

load_harness_template = load_prompt_template


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
        self.is_running: bool = False
        self._interrupt_event = asyncio.Event()
        self._is_paused: bool = False
        self._current_gen_task: asyncio.Task[Any] | None = None

        # 0. Discovery & Environment loading
        self.discovery = ResourceDiscovery(workspace_root=self.workspace_root, config=self.config)
        self.discovery.load_env()

        # Knowledge store & dispatcher auto-initialization
        if store is not None:
            self.store = store
        elif dispatcher is not None:
            self.store = dispatcher.store
        else:
            libhippo_dir = self.discovery.discover_libhippo_dir()
            mounts = create_default_mounts(
                workspace_root=self.workspace_root,
                user_root=self.config.user_config_dir / "knowledge",
            )
            cache_dir = (libhippo_dir / "cache") if libhippo_dir else (self.workspace_root / ".libhippo" / "cache")
            self.store = KnowledgeStore(mounts=mounts, cache_dir=cache_dir)

        # Knowledge Agent Manager: manages & reuses knowledge agents
        self.agent_manager = AgentManager(store=self.store)
        self.dispatcher = dispatcher or self.agent_manager.create_dispatcher()

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
            self.model_client = wrap_client_if_logging_enabled(model_client, agent_role="TaskSolverAgent")
        else:
            client_kwargs: dict[str, Any] = {}

            if self.config.transport_mode == "websocket":
                self.model_client = wrap_client_if_logging_enabled(
                    OpenAIResponsesWebSocketClient(
                        model=self.config.model,
                        enable_http_fallback=self.config.enable_http_fallback,
                        **client_kwargs,
                    ),
                    agent_role="TaskSolverAgent",
                )
            else:
                self.model_client = wrap_client_if_logging_enabled(
                    create_chat_client(
                        "task_solver",
                        model=self.config.model,
                        **client_kwargs,
                    ),
                    agent_role="TaskSolverAgent",
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
        # 6. Subagents & Sidecars
        self.subagents = SubagentManager(model_client=self.model_client, session=self.session)
        self.subagents.memory = self.memory
        self.tools.subagent_manager = self.subagents
        self.tools.memory = self.memory
        self.registered_tools = self.tools.get_tool_definitions()
        self.sidecar = SidecarExecutor(model_client=self.model_client)
        self.harvest_observer = KnowledgeHarvestObserver()
        self.on_event_broadcast: Callable[[KnowledgeAgentEvent], Any] | None = None
        self._active_stream_queue: asyncio.Queue[KnowledgeAgentEvent] | None = None

        def _on_knowledge_event(evt: KnowledgeAgentEvent) -> None:
            if self._active_stream_queue is not None:
                self._active_stream_queue.put_nowait(evt)
            elif self.on_event_broadcast is not None:
                try:
                    res = self.on_event_broadcast(evt)
                    if asyncio.iscoroutine(res):
                        asyncio.create_task(res)
                except Exception:
                    pass

        self.agent_manager.orchestrator.on_event = _on_knowledge_event
        if self.dispatcher and hasattr(self.dispatcher, "on_event"):
            self.dispatcher.on_event = _on_knowledge_event

        self.harvest_sidecar = KnowledgeHarvestSidecar(
            model_client=self.model_client,
            store=self.store,
            orchestrator=self.agent_manager.orchestrator,
            on_event=_on_knowledge_event,
        )
        self._active_sidecar_tasks: set[asyncio.Task[Any]] = set()

        # 7. Resource Discovery & Extensibility
        self.skills: dict[str, SkillDefinition] = {}
        self.auto_discover()

        # Initialize Zone 1 static prefix
        self.init_prefix()

    def auto_discover(self) -> None:
        """Automatically discover project AGENTS.md, skills, and MCP configurations."""
        self.skills = self.discovery.discover_skills()

    def assemble_system_prompt(self) -> str:
        """Assemble complete Zone 1 system prompt using template engine."""
        agents_rules = self.discovery.discover_agents_markdown()
        return assemble_harness_system_prompt(
            workspace_root=self.workspace_root,
            registered_tools=self.registered_tools,
            agents_rules=agents_rules,
        )

    def init_prefix(self) -> None:
        """Initialize Zone 1 static prefix guaranteed to remain bitwise invariant."""
        system_prompt = self.assemble_system_prompt()
        self.memory.set_zone1_prefix(system_persona=system_prompt)

    def get_tool_schemas(self) -> list[ToolSchema]:
        """Convert registered tools into AutoGen ToolSchema objects."""
        return [
            ToolSchema(
                name=td.name,
                description=td.description,
                parameters=td.parameters_schema,
            )
            for td in self.registered_tools.values()
        ]

    def _build_llm_messages(self) -> list[LLMMessage]:
        """Convert ContextMemory messages into typed AutoGen LLMMessage sequence."""
        all_msgs = self.memory.get_all_messages()
        llm_messages: list[LLMMessage] = []
        for m in all_msgs:
            if m.role == "system":
                llm_messages.append(SystemMessage(content=m.content))
            elif m.role == "user":
                llm_messages.append(UserMessage(content=m.content, source="user"))
            elif m.role == "assistant":
                tool_calls_meta = m.metadata.get("tool_calls")
                if tool_calls_meta:
                    fcs = [
                        FunctionCall(
                            id=tc["id"],
                            name=tc["name"],
                            arguments=tc["arguments"] if isinstance(tc["arguments"], str) else json.dumps(tc["arguments"]),
                        )
                        for tc in tool_calls_meta
                    ]
                    llm_messages.append(
                        AssistantMessage(
                            content=fcs,
                            thought=m.metadata.get("thought"),
                            source="assistant",
                        )
                    )
                else:
                    llm_messages.append(AssistantMessage(content=m.content, source="assistant"))
            elif m.role == "tool":
                fer = FunctionExecutionResult(
                    call_id=m.tool_call_id or "call_unknown",
                    content=m.content,
                    is_error=bool(m.metadata.get("is_error", False)),
                    name=m.metadata.get("tool_name", "tool"),
                )
                if llm_messages and isinstance(llm_messages[-1], FunctionExecutionResultMessage):
                    llm_messages[-1].content.append(fer)
                else:
                    llm_messages.append(
                        FunctionExecutionResultMessage(content=[fer])
                    )
        return llm_messages

    def transition_phase(self, new_phase: str) -> PhaseTransitionEvent:
        """Legacy helper for backwards compatibility."""
        return PhaseTransitionEvent(from_phase="idle", to_phase=new_phase)

    async def step(self, user_input: str) -> str:
        """Execute one conversational round collecting all streamed events."""
        final_answer = ""
        async for event in self.stream(user_input):
            if isinstance(event, TokenChunkEvent):
                final_answer += event.delta
            elif isinstance(event, TurnCompletedEvent):
                pass
        return final_answer

    def _resolve_git_branch(self) -> str:
        """Resolve current git branch name, falling back to 'main'."""
        try:
            res = subprocess.run(
                ["git", "branch", "--show-current"],
                cwd=self.workspace_root,
                capture_output=True,
                text=True,
                timeout=1.0,
            )
            if res.returncode == 0 and res.stdout.strip():
                return res.stdout.strip()
        except Exception:
            pass
        return "main"

    async def stream(self, user_input: str, continue_mode: bool = False) -> AsyncIterator[HarnessEvent]:
        """Stream asynchronous execution events for autonomous software engineering."""
        if self.is_running:
            # If already running, treat incoming turn as mid-turn steering
            await self.steer(user_input)
            yield TokenChunkEvent(delta=f"\n[Steered: {user_input}]\n")
            return

        self.is_running = True
        queue: asyncio.Queue[KnowledgeAgentEvent] = asyncio.Queue()
        self._active_stream_queue = queue
        try:
            if self._interrupt_event.is_set():
                self._interrupt_event.clear()
                self._is_paused = False

            start_time = datetime.datetime.now(datetime.timezone.utc)
            self.governor.start_turn()

            is_initial_turn = len(self.memory.zone2_history) == 0
            branch = self._resolve_git_branch()
            now = datetime.datetime.now()
            local_str = now.strftime("%A, %Y-%m-%d %H:%M:%S")
            tz_offset = now.astimezone().strftime("%z")

            # In normal conversational turns, prune past turns' bulky tool outputs from active context
            if not continue_mode and not is_initial_turn:
                self.memory.prune_past_tool_outputs()

            if is_initial_turn:
                ambient_header = (
                    f"<session_context>\n"
                    f"- Current Date & Time: {local_str} (UTC: {start_time.isoformat()}, Timezone: {tz_offset})\n"
                    f"- Workspace: {self.workspace_root.resolve()} ({self.workspace_root.name})\n"
                    f"- Active Git Branch: {branch}\n"
                    f"- User & Platform: {getpass.getuser()} on {platform.system()} ({platform.machine()})\n"
                    f"</session_context>\n\n"
                )
                turn_content = f"{ambient_header}{user_input}"
            else:
                turn_content = user_input

            u_msg = self.memory.append_user_turn(
                user_content=turn_content,
                timestamp=start_time.isoformat(),
                branch=branch,
            )
            await self.session.append_message(u_msg)

            # Evaluate Context Budgeting & Compaction
            compaction_res = self.governor.check_context_and_compact()
            if compaction_res["status"] == "compacted":
                yield TokenChunkEvent(delta=f"\n[Context compacted: {compaction_res['evicted_tokens']} tokens reclaimed]\n")

            tool_schemas = self.get_tool_schemas()
            max_tool_iterations = 10
            current_iteration = 0
            out_content = ""
            turn_cached_tokens = 0
            turn_prompt_tokens = 0

            while current_iteration < max_tool_iterations:
                if self._interrupt_event.is_set():
                    out_content = "[Execution interrupted by user]"
                    yield TokenChunkEvent(delta=f"\n{out_content}\n")
                    break

                current_iteration += 1
                llm_messages = self._build_llm_messages()

                extra_args: dict[str, Any] = {}

                self._current_gen_task = asyncio.current_task()
                try:
                    res = await self.model_client.create(
                        messages=llm_messages,
                        tools=tool_schemas,
                        extra_create_args=extra_args,
                    )
                    if hasattr(res, "usage") and res.usage:
                        turn_prompt_tokens += getattr(res.usage, "prompt_tokens", 0) or 0
                        turn_cached_tokens += int(getattr(res, "cached_tokens", 0) or getattr(res.usage, "cached_tokens", 0) or 0)
                except asyncio.CancelledError:
                    out_content = "[Generation cancelled by user interrupt]"
                    yield TokenChunkEvent(delta=f"\n{out_content}\n")
                    break
                except Exception as e:
                    err_str = str(e)
                    if "reasoning_effort" in err_str and "set reasoning_effort to 'none'" in err_str:
                        extra_args["reasoning_effort"] = "none"
                        try:
                            res = await self.model_client.create(
                                messages=llm_messages,
                                tools=tool_schemas,
                                extra_create_args=extra_args,
                            )
                        except Exception as retry_err:
                            out_content = f"Execution error: {retry_err}"
                            yield TokenChunkEvent(delta=f"\n{out_content}\n")
                            break
                    else:
                        out_content = f"Execution error: {e}"
                        yield TokenChunkEvent(delta=f"\n{out_content}\n")
                        break
                finally:
                    self._current_gen_task = None

                tool_calls = None
                if isinstance(res.content, list) and len(res.content) > 0 and all(hasattr(c, "name") for c in res.content):
                    tool_calls = res.content
                elif hasattr(res, "tool_calls") and getattr(res, "tool_calls"):
                    tool_calls = getattr(res, "tool_calls")

                if tool_calls:
                    tool_calls_meta = [
                        {
                            "id": getattr(call, "id", None) or f"call_{uuid.uuid4().hex[:8]}",
                            "name": getattr(call, "name", ""),
                            "arguments": getattr(call, "arguments", "{}"),
                        }
                        for call in tool_calls
                    ]
                    as_summary = f"[Tool calls: {', '.join(tc['name'] for tc in tool_calls_meta)}]"
                    as_msg = self.memory.append_assistant_turn(
                        content=as_summary,
                        tool_calls=tool_calls_meta,
                        thought=getattr(res, "thought", None),
                    )
                    await self.session.append_message(as_msg)

                    for tc in tool_calls_meta:
                        call_id = tc["id"]
                        tool_name = tc["name"]
                        raw_args = tc["arguments"]
                        try:
                            args = json.loads(raw_args) if isinstance(raw_args, str) else raw_args
                            if not isinstance(args, dict):
                                args = {}
                        except Exception:
                            args = {}

                        yield ToolCallStartEvent(tool_call_id=call_id, name=tool_name, arguments=args)

                        tool_def = self.registered_tools.get(tool_name)
                        if tool_def is None:
                            err_msg = f"Unknown tool: '{tool_name}'."
                            yield ToolCallResultEvent(tool_call_id=call_id, name=tool_name, result=None, error=err_msg)
                            t_msg = self.memory.append_tool_output(
                                tool_name=tool_name,
                                content=err_msg,
                                tool_call_id=call_id,
                                is_evictable=True,
                                is_error=True,
                            )
                            await self.session.append_message(t_msg)
                            continue

                        try:
                            handler_task = asyncio.create_task(tool_def.handler(**args))
                            while not handler_task.done():
                                try:
                                    k_evt = await asyncio.wait_for(queue.get(), timeout=0.08)
                                    yield k_evt
                                    summary = k_evt.output_summary or k_evt.input_summary
                                    yield TokenChunkEvent(delta=f"\n[Knowledge: {k_evt.agent} {k_evt.action} ({k_evt.status}) - {summary}]\n")
                                except asyncio.TimeoutError:
                                    pass
                            while not queue.empty():
                                k_evt = queue.get_nowait()
                                yield k_evt
                                summary = k_evt.output_summary or k_evt.input_summary
                                yield TokenChunkEvent(delta=f"\n[Knowledge: {k_evt.agent} {k_evt.action} ({k_evt.status}) - {summary}]\n")

                            result = await handler_task
                            yield ToolCallResultEvent(tool_call_id=call_id, name=tool_name, result=result, error=None)
                            result_str = str(result) if not isinstance(result, str) else result
                            t_msg = self.memory.append_tool_output(
                                tool_name=tool_name,
                                content=result_str,
                                tool_call_id=call_id,
                                is_evictable=True,
                                is_error=False,
                            )
                            await self.session.append_message(t_msg)
                        except Exception as exc:
                            err_msg = f"Tool execution failed: {exc}"
                            yield ToolCallResultEvent(tool_call_id=call_id, name=tool_name, result=None, error=err_msg)
                            t_msg = self.memory.append_tool_output(
                                tool_name=tool_name,
                                content=err_msg,
                                tool_call_id=call_id,
                                is_evictable=True,
                                is_error=True,
                            )
                            await self.session.append_message(t_msg)

                    compaction_res = self.governor.check_context_and_compact()
                    if compaction_res["status"] == "compacted":
                        yield TokenChunkEvent(delta=f"\n[Context compacted: {compaction_res['evicted_tokens']} tokens reclaimed]\n")

                    continue
                else:
                    out_content = res.content if isinstance(res.content, str) else str(res.content)
                    yield TokenChunkEvent(delta=out_content)

                    as_msg = self.memory.append_assistant_turn(out_content)
                    await self.session.append_message(as_msg)
                    break
            else:
                if not out_content:
                    out_content = "[Maximum tool iterations reached]"
                    yield TokenChunkEvent(delta=f"\n{out_content}\n")
                    as_msg = self.memory.append_assistant_turn(out_content)
                    await self.session.append_message(as_msg)

            elapsed = (datetime.datetime.now(datetime.timezone.utc) - start_time).total_seconds()
            hit_rate = (turn_cached_tokens / turn_prompt_tokens) if turn_prompt_tokens > 0 else 0.0
            yield TurnCompletedEvent(
                turn_index=self.governor.current_turns,
                total_tokens=self.memory.get_total_tokens(),
                duration_seconds=elapsed,
                response=out_content,
                cached_tokens=turn_cached_tokens,
                cache_hit_rate=round(hit_rate, 4),
            )

            # Trigger background maintenance sidecar without delaying turn delivery
            bg_task = asyncio.create_task(self.post_task_maintenance())
            self._active_sidecar_tasks.add(bg_task)
            bg_task.add_done_callback(self._active_sidecar_tasks.discard)
        finally:
            self._active_stream_queue = None
            self.is_running = False

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

        # 4. Cancel active background sidecar maintenance tasks
        for bg_t in list(self._active_sidecar_tasks):
            if not bg_t.done():
                bg_t.cancel()

        # 5. Record interrupt event in memory and durable session
        int_msg = self.memory.append_assistant_turn(f'<interrupt_event status="paused_by_user" reason="{reason}"/>')
        asyncio.create_task(self.session.append_message(int_msg))

    def compact_context(self) -> int:
        """Trigger explicit context compaction (Zone 3 eviction and Zone 2 summarization)."""
        return self.memory.compact_memory(self.config.compaction_target_tokens)

    async def post_task_maintenance(self) -> None:
        """Execute background maintenance (knowledge harvesting and vector compaction)."""
        # 1. Harvest explicit learnings queued during the turn
        had_explicit = False
        if hasattr(self.tools, "harvest_queue") and self.tools.harvest_queue:
            while self.tools.harvest_queue:
                item = self.tools.harvest_queue.pop(0)
                had_explicit = True
                try:
                    await self.harvest_sidecar.harvest_from_context(
                        parent_memory=self.memory,
                        scope=item.get("scope", "project"),
                        nature="critical_rule",
                        topic_hint=item.get("topic"),
                    )
                except Exception:
                    pass

        # 2. Autonomous knowledge harvest sidecar on warm KV cache
        # Unconditionally inspect context on sidecar after turn completion
        if not had_explicit:
            try:
                await self.harvest_sidecar.harvest_from_context(
                    parent_memory=self.memory,
                    scope="project",
                    nature="critical_rule",
                )
            except Exception:
                pass

        # 3. Store maintenance (vector compaction & freshness)
        if self.store is not None:
            try:
                if hasattr(self.store, "post_task_maintenance"):
                    await self.store.post_task_maintenance()
                elif hasattr(self.store, "compact_if_needed"):
                    await self.store.compact_if_needed()
            except Exception:
                logger.warning("Error during post_task_maintenance", exc_info=True)
