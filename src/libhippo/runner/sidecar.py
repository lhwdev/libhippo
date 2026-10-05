"""Ephemeral sidecar agent executing concurrent mid-run /btw queries against warm KV cache."""

from __future__ import annotations

from typing import Any

from autogen_core.models import AssistantMessage, ChatCompletionClient, SystemMessage, UserMessage

from libhippo.agents.curator import CuratorAgent
from libhippo.agents.draftsman import KnowledgeDraftSession
from libhippo.agents.prompts import load_prompt_file
from libhippo.runner.memory import ContextMemory


class SidecarExecutor:
    """Executes read-only Q&A queries without disturbing primary agent execution."""

    def __init__(self, model_client: ChatCompletionClient) -> None:
        self.model_client = model_client

    async def ask(self, query: str, parent_memory: ContextMemory) -> str:
        """Execute query using snapshot of warm parent KV-cache context."""
        # 1. Snapshot parent context up to current turn
        parent_messages = parent_memory.get_all_messages()

        llm_messages: list[Any] = []
        for m in parent_messages:
            if m.role == "system":
                llm_messages.append(SystemMessage(content=m.content))
            elif m.role in ("user", "tool"):
                llm_messages.append(UserMessage(content=m.content, source="user"))
            elif m.role == "assistant":
                llm_messages.append(AssistantMessage(content=m.content, source="assistant"))

        # 2. Append sidecar instruction and user question
        sidecar_prompt = (
            "<SIDECAR>\n"
            "You are an ephemeral sidecar assistant. Provide a concise, direct answer "
            "to the user's question based strictly on the current context snapshot. "
            "Do not modify state or execute actions.\n\n"
            f"User Question: <USER_PROMPT>\n{query}\n"
            "</USER_PROMPT>\n</SIDECAR>"
        )
        llm_messages.append(UserMessage(content=sidecar_prompt, source="sidecar_user"))

        # 3. Call model (benefits from 100% KV-cache read hit on parent prefix)
        try:
            res = await self.model_client.create(messages=llm_messages)
            return res.content if isinstance(res.content, str) else str(res.content)
        except Exception as e:
            return f"Sidecar error: {e}"


class KnowledgeHarvestSidecar:
    """Background sidecar synthesizing context learnings and submitting to Maker-Checker loop."""

    def __init__(
        self,
        model_client: ChatCompletionClient,
        store: Any,
        orchestrator: Any | None = None,
        on_event: Any | None = None,
    ) -> None:
        self.model_client = model_client
        self.store = store
        self._orchestrator = orchestrator
        self.on_event = on_event

    @property
    def orchestrator(self) -> Any:
        return self._get_orchestrator()

    def _get_orchestrator(self) -> Any:
        if self._orchestrator is not None:
            return self._orchestrator
        from libhippo.agents.manager import AgentManager

        self._orchestrator = AgentManager(store=self.store).orchestrator
        return self._orchestrator

    async def harvest_from_context(
        self,
        parent_memory: ContextMemory,
        scope: str = "project",
        nature: str = "critical_rule",
        topic_hint: str | None = None,
    ) -> Any:
        """Synthesize Hub/Leaf knowledge node from warm context and run Maker-Checker governance."""
        session = KnowledgeDraftSession(store=self.store)
        try:
            parent_messages = parent_memory.get_all_messages()
            llm_messages: list[Any] = []
            for m in parent_messages:
                if m.role == "system":
                    llm_messages.append(SystemMessage(content=m.content))
                elif m.role in ("user", "tool"):
                    llm_messages.append(UserMessage(content=m.content, source="user"))
                elif m.role == "assistant":
                    llm_messages.append(AssistantMessage(content=m.content, source="assistant"))

            harvest_template = load_prompt_file("harvest_sidecar")
            harvest_prompt = harvest_template.format(
                scope=scope,
                nature=nature,
                topic_hint=topic_hint or "Derive from context",
            )
            llm_messages.append(UserMessage(content=harvest_prompt, source="harvest_sidecar"))

            tools = [
                session.write_knowledge,
                session.read_knowledge,
                session.list_knowledge,
                session.search_knowledge,
                session.commit,
                session.commit_all,
            ]

            extra_args: dict[str, Any] = {"reasoning_effort": "low"}
            try:
                res = await self.model_client.create(messages=llm_messages, tools=tools, extra_create_args=extra_args)
            except Exception:
                try:
                    res = await self.model_client.create(messages=llm_messages, tools=tools)
                except Exception:
                    res = await self.model_client.create(messages=llm_messages)

            raw_text = res.content if isinstance(res.content, str) else str(res.content)
            cleaned = raw_text.strip()

            orchestrator = self._get_orchestrator()

            # If multi-draft session has validated drafts, commit each to governance
            if session.drafts:
                results: list[Any] = []
                for p, draft_path in session.drafts.items():
                    if draft_path.exists():
                        content = draft_path.read_text(encoding="utf-8")
                        gov_res = await orchestrator.run_governance(
                            candidate=content,
                            target_path=p,
                            on_event=self.on_event,
                        )
                        results.append(gov_res)
                if len(results) == 1:
                    return results[0]
                elif results:
                    return {"status": "success", "multi_draft_results": results}

            if not cleaned or "NO_HARVEST" in cleaned or cleaned == "NONE":
                return None

            draft = CuratorAgent.extract_markdown_draft(raw_text)
            if "---" not in draft:
                return None

            path = CuratorAgent.infer_path(draft, topic_hint or "context_learning")
            if not path.startswith(f"{scope}/"):
                clean_slug = path.split("/")[-1]
                path = f"{scope}/{clean_slug}"

            return await orchestrator.run_governance(
                candidate=draft,
                target_path=path,
                on_event=self.on_event,
            )
        finally:
            session.cleanup()

