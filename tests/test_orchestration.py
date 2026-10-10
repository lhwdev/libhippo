"""Unit and workflow tests for Phase 5 LibHippo orchestration and Maker-Checker governance."""

from unittest.mock import AsyncMock

import pytest
from autogen_agentchat.conditions import MaxMessageTermination
from autogen_agentchat.teams import RoundRobinGroupChat
from autogen_core.models import CreateResult, RequestUsage

from libhippo.agents.checker import CheckerAgent
from libhippo.agents.curator import CuratorAgent
from libhippo.agents.task_solver import TaskSolverAgent
from libhippo.agents.verifier import VerifierAgent
from libhippo.models.audit import JevAuditReport
from libhippo.orchestration.maker_checker import (
    GovernanceTurn,
    MakerCheckerOrchestrator,
    RefactoringContextManager,
)
from libhippo.orchestration.retrieval_flow import AdaptiveRetrievalWorkflow
from libhippo.orchestration.teams import (
    create_governance_team,
    create_solver_team,
)
from libhippo.storage.mount import MountConfig
from libhippo.storage.store import KnowledgeStore
from libhippo.tools.retrieval import KnowledgeDispatcher


def make_mock_client(content: str) -> AsyncMock:
    """Helper to create a mock ChatCompletionClient returning a specific text response."""
    client = AsyncMock()
    client.create.return_value = CreateResult(
        finish_reason="stop",
        content=content,
        usage=RequestUsage(prompt_tokens=30, completion_tokens=20),
        cached=False,
    )
    return client


# --- RefactoringContextManager Tests ---

def test_context_manager_head_compact_tail():
    """Verify that RefactoringContextManager compresses intermediate turns when exceeding token limit."""
    mgr = RefactoringContextManager(token_threshold=200, keep_tail_turns=2)

    # Add 5 turns
    mgr.add_turn(GovernanceTurn(round_index=1, actor="User", action="start", summary="Initial goal", token_count=50))
    mgr.add_turn(GovernanceTurn(round_index=2, actor="Checker", action="audit1", summary="Failed schema", token_count=70))
    mgr.add_turn(GovernanceTurn(round_index=3, actor="Curator", action="revise1", summary="Fixed title", token_count=80))
    mgr.add_turn(GovernanceTurn(round_index=4, actor="Checker", action="audit2", summary="Failed size", token_count=90))
    mgr.add_turn(GovernanceTurn(round_index=5, actor="Curator", action="revise2", summary="Split subtopic", token_count=100))

    # Total tokens was 390 > 200, should have compacted intermediate turns 2 and 3
    assert len(mgr.turns) <= 4
    # Turn 0 should still be head
    assert mgr.turns[0].round_index == 1
    # Middle turn should be compacted summary
    assert mgr.turns[1].actor == "Orchestrator:Compactor"
    assert "compacted_intermediate_history" in mgr.turns[1].action
    # Tail should be preserved
    assert mgr.turns[-1].round_index == 5
    assert mgr.turns[-2].round_index == 4


# --- MakerCheckerOrchestrator Tests ---

@pytest.mark.asyncio
async def test_maker_checker_routine_pass(tmp_path):
    """Verify routine PASS reviews bypass LLM reasoning and commit immediately."""
    valid_doc = """---
title: "React 19 Server Components"
namespace: "common"
nature: "critical_rule"
importance: 0.90
---
## Summary (Coarse View)
Server components render on the server and reduce bundle size.
## Detailed Rules & Edge Cases (Fine View)
- Never use useState in server components.
- Async await is directly supported.
"""
    async with KnowledgeStore(root_dir=tmp_path / "knowledge") as store:
        mock_checker = AsyncMock(spec=CheckerAgent)
        mock_checker.count_tokens.return_value = 600
        mock_checker.check.return_value = JevAuditReport(
            verdict="PASS",
            size_status="optimal",
            taxonomy_fit="optimal",
            token_count=600,
            effective_token_count=600,
            importance_score=0.90,
        )

        orchestrator = MakerCheckerOrchestrator(store=store, checker=mock_checker)
        result = await orchestrator.run_governance(
            candidate=valid_doc,
            target_path="common/react/rsc.md",
        )

        assert result.status == "COMMITTED"
        assert result.verdict == "PASS"
        assert "common/react/rsc.md" in result.committed_paths

        # Verify disk commit
        node = await store.get_node("common/react/rsc.md")
        assert node is not None
        assert node.frontmatter.title == "React 19 Server Components"


@pytest.mark.asyncio
async def test_maker_checker_force_keep_exemption(tmp_path):
    """Verify force_keep=True documents are unconditionally committed as standalone exemptions."""
    pinned_doc = """---
title: "Pinned Legacy Convention"
namespace: "common"
force_keep: true
importance: 0.80
---
## Summary
Immutable legacy convention.
## Detailed Rules
Do not merge or refactor this file.
"""
    async with KnowledgeStore(root_dir=tmp_path / "knowledge") as store:
        mock_checker = AsyncMock(spec=CheckerAgent)
        mock_checker.count_tokens.return_value = 200
        # Checker would normally flag undersized MERGE_REQUIRED
        mock_checker.check.return_value = JevAuditReport(
            verdict="MERGE_REQUIRED",
            size_status="undersized",
            taxonomy_fit="optimal",
            token_count=200,
            effective_token_count=200,
            importance_score=0.80,
            merge_recommendation="merge_into_sibling",
        )

        orchestrator = MakerCheckerOrchestrator(store=store, checker=mock_checker)
        result = await orchestrator.run_governance(
            candidate=pinned_doc,
            target_path="common/pinned.md",
        )

        assert result.status == "COMMITTED"
        assert "force_keep" in result.verdict
        assert await store.get_node("common/pinned.md") is not None


@pytest.mark.asyncio
async def test_maker_checker_sibling_coalescence(tmp_path):
    """Verify MERGE_REQUIRED coalesces sparse nodes into target sibling."""
    async with KnowledgeStore(root_dir=tmp_path / "knowledge") as store:
        # Pre-populate sibling on disk
        await store.save_node(
            "common/web/forms/input.md",
            """---
title: "Input Controls"
namespace: "common"
---
## Summary
Input guidelines.
## Detailed Rules
Text inputs.
""",
        )

        stub_doc = """---
title: "Textarea Control"
namespace: "common"
---
## Summary
Textarea guidelines.
## Detailed Rules
Multiline inputs.
"""
        mock_checker = AsyncMock(spec=CheckerAgent)
        mock_checker.count_tokens.return_value = 150
        mock_checker.check.return_value = JevAuditReport(
            verdict="MERGE_REQUIRED",
            size_status="undersized",
            taxonomy_fit="optimal",
            token_count=150,
            effective_token_count=150,
            importance_score=0.50,
            merge_candidate_siblings=["common/web/forms/input.md"],
            merge_recommendation="merge_into_sibling",
        )

        orchestrator = MakerCheckerOrchestrator(store=store, checker=mock_checker)
        result = await orchestrator.run_governance(
            candidate=stub_doc,
            target_path="common/web/forms/textarea.md",
        )

        assert result.status == "MERGED"
        assert result.path == "common/web/forms/input.md"
        assert "common/web/forms/input.md" in result.committed_paths


@pytest.mark.asyncio
async def test_maker_checker_escalate_refactor(tmp_path):
    """Verify ESCALATE_REFACTOR invokes VerifierAgent to resolve overgrown branches."""
    oversized_doc = """---
title: "Monolithic CSS Guide"
namespace: "common"
---
## Summary
A huge guide.
## Detailed Rules
Over 2000 tokens of CSS rules.
"""
    async with KnowledgeStore(root_dir=tmp_path / "knowledge") as store:
        mock_checker = AsyncMock(spec=CheckerAgent)
        mock_checker.count_tokens.return_value = 2200
        mock_checker.check.return_value = JevAuditReport(
            verdict="ESCALATE_REFACTOR",
            size_status="oversized",
            taxonomy_fit="optimal",
            token_count=2200,
            effective_token_count=2400,
            bloatedness_score=2.5,
            content_errors=["Effective token count exceeds 1800"],
        )

        mock_verifier = AsyncMock(spec=VerifierAgent)
        mock_verifier.resolve_escalation.return_value = {
            "status": "MUTATION_EXECUTED",
            "action": "split",
            "parent_hub": "common/css/monolithic.md",
            "children": [{"path": "common/css/flexbox.md"}, {"path": "common/css/grid.md"}],
            "rationale": "Partitioned into flexbox and grid child leaves.",
        }

        orchestrator = MakerCheckerOrchestrator(
            store=store,
            checker=mock_checker,
            verifier=mock_verifier,
        )
        result = await orchestrator.run_governance(
            candidate=oversized_doc,
            target_path="common/css/monolithic.md",
        )

        assert result.status == "COMMITTED"
        assert result.verdict == "ESCALATE_REFACTOR"
        assert "common/css/flexbox.md" in result.committed_paths
        assert mock_verifier.resolve_escalation.await_count == 1


@pytest.mark.asyncio
async def test_maker_checker_revision_loop_and_max_retries(tmp_path):
    """Verify iterative revision with CuratorAgent and termination upon max retries."""
    flawed_doc = "Missing frontmatter completely."

    async with KnowledgeStore(root_dir=tmp_path / "knowledge") as store:
        mock_checker = AsyncMock(spec=CheckerAgent)
        mock_checker.count_tokens.return_value = 50
        # Always fail with REVISE_SCHEMA
        mock_checker.check.return_value = JevAuditReport(
            verdict="REVISE_SCHEMA",
            schema_valid=False,
            schema_errors=["Missing required YAML frontmatter delimiters"],
        )

        mock_curator = AsyncMock(spec=CuratorAgent)
        mock_curator.revise.return_value = {
            "draft": "---\ntitle: Attempt 1\n---\nBody",
            "path": "common/doc.md",
        }

        orchestrator = MakerCheckerOrchestrator(
            store=store,
            checker=mock_checker,
            curator=mock_curator,
        )

        # Run with max_retries=3
        result = await orchestrator.run_governance(
            candidate=flawed_doc,
            target_path="common/doc.md",
            max_retries=3,
        )

        assert result.status == "REVISE_FAILED"
        assert result.retries_used == 3
        assert mock_curator.revise.await_count == 3


@pytest.mark.asyncio
async def test_maker_checker_read_only_rejection(tmp_path):
    """Verify that attempts to commit to read-only mounts result in REJECTED status."""
    comm_dir = tmp_path / "comm"
    comm_dir.mkdir(parents=True, exist_ok=True)
    mounts = [
        MountConfig(namespace_prefix="common", physical_path=comm_dir, read_only=True),
    ]

    async with KnowledgeStore(mounts=mounts, cache_dir=tmp_path / "cache") as store:
        mock_checker = AsyncMock(spec=CheckerAgent)
        mock_checker.count_tokens.return_value = 500
        mock_checker.check.return_value = JevAuditReport(
            verdict="PASS",
            size_status="optimal",
            taxonomy_fit="optimal",
            token_count=500,
        )

        orchestrator = MakerCheckerOrchestrator(store=store, checker=mock_checker)
        result = await orchestrator.run_governance(
            candidate="---\ntitle: RO Test\nnamespace: common\n---\n## Summary\nRO test.",
            target_path="common/ro_test.md",
        )

        assert result.status == "REJECTED"
        assert "read-only" in result.message.lower()


# --- AdaptiveRetrievalWorkflow Tests ---

@pytest.mark.asyncio
async def test_adaptive_retrieval_mandatory_miss_curation(tmp_path):
    """Verify that a mandatory miss in AdaptiveRetrievalWorkflow triggers curation and governance."""
    async with KnowledgeStore(root_dir=tmp_path / "knowledge") as store:
        dispatcher = KnowledgeDispatcher(store=store)

        mock_checker = AsyncMock(spec=CheckerAgent)
        mock_checker.count_tokens.return_value = 600
        mock_checker.check.return_value = JevAuditReport(
            verdict="PASS",
            size_status="optimal",
            taxonomy_fit="optimal",
            token_count=600,
        )

        mock_curator = AsyncMock(spec=CuratorAgent)
        mock_curator.curate.return_value = {
            "path": "common/web/crypto_aes.md",
            "draft": """---
title: "Web Crypto AES-GCM"
namespace: "common"
importance: 0.95
---
## Summary (Coarse View)
Use window.crypto.subtle.encrypt with AES-GCM 256.
## Detailed Rules & Edge Cases (Fine View)
- Never use Math.random for IVs.
""",
        }

        orchestrator = MakerCheckerOrchestrator(
            store=store,
            checker=mock_checker,
            curator=mock_curator,
        )

        workflow = AdaptiveRetrievalWorkflow(
            dispatcher=dispatcher,
            orchestrator=orchestrator,
        )

        # Query a non-existent topic with mandatory criticality
        result = await workflow.query(
            query="AES-GCM Web Crypto encryption requirements",
            effort="medium",
            criticality="mandatory",
        )

        assert result.status == "HIT"
        assert result.source == "curator"
        assert result.path == "common/web/crypto_aes.md"
        assert "AES-GCM" in result.content
        assert await store.get_node("common/web/crypto_aes.md") is not None


@pytest.mark.asyncio
async def test_maker_checker_real_time_observability_events(tmp_path):
    """Verify that MakerCheckerOrchestrator emits KnowledgeAgentEvents at every phase."""
    from libhippo.models.knowledge import KnowledgeAgentEvent

    async with KnowledgeStore(root_dir=tmp_path / "knowledge") as store:
        events: list[KnowledgeAgentEvent] = []

        def on_event(evt: KnowledgeAgentEvent):
            events.append(evt)

        mock_checker = AsyncMock(spec=CheckerAgent)
        mock_checker.count_tokens.return_value = 550
        mock_checker.check.return_value = JevAuditReport(
            verdict="PASS",
            size_status="optimal",
            taxonomy_fit="optimal",
            token_count=550,
            importance_score=0.90,
        )

        mock_curator = AsyncMock(spec=CuratorAgent)
        mock_curator.curate.return_value = {
            "path": "common/web/react19.md",
            "draft": "---\ntitle: React 19\nnamespace: common\n---\n## Summary\nReact 19 Actions.",
        }

        orchestrator = MakerCheckerOrchestrator(
            store=store,
            checker=mock_checker,
            curator=mock_curator,
            on_event=on_event,
        )

        result = await orchestrator.curate_and_govern(
            topic="React 19 Actions",
            target_path="common/web/react19.md",
        )

        assert result.status == "COMMITTED"
        assert len(events) >= 4

        # 1. Curator drafting start & completed
        drafting_events = [e for e in events if e.agent == "CuratorAgent" and e.action == "drafting"]
        assert len(drafting_events) >= 2
        assert drafting_events[0].status == "running"
        assert drafting_events[1].status == "completed"

        # 2. Checker audit start & verdict
        checker_events = [e for e in events if e.agent == "CheckerAgent"]
        assert any(e.action == "audit" and e.status == "running" for e in checker_events)
        verdict_evt = next(e for e in checker_events if e.action == "audit_verdict")
        assert verdict_evt.status == "passed"
        assert "PASS" in verdict_evt.output_summary

        # 3. MakerChecker commit
        commit_evt = next(e for e in events if e.agent == "MakerChecker" and e.action == "commit")
        assert commit_evt.status == "completed"
        assert "common/web/react19.md" in commit_evt.target_path


# --- AutoGen 0.4 Team Configuration Tests ---

def test_team_assembly_and_termination():
    """Verify AutoGen 0.4 team construction with MaxMessageTermination(max_messages=16)."""
    mock_curator_client = make_mock_client("Curator response")
    mock_verifier_client = make_mock_client("Verifier response")
    mock_solver_client = make_mock_client("Solver response")

    curator = CuratorAgent(model_client=mock_curator_client)
    verifier = VerifierAgent(model_client=mock_verifier_client)
    solver = TaskSolverAgent(model_client=mock_solver_client)

    gov_team = create_governance_team(curator=curator, verifier=verifier, max_messages=16)
    assert isinstance(gov_team, RoundRobinGroupChat)
    assert len(gov_team._participants) == 2
    assert isinstance(gov_team._termination_condition, MaxMessageTermination)

    solver_team = create_solver_team(solver=solver, verifier=verifier, max_messages=16)
    assert isinstance(solver_team, RoundRobinGroupChat)
    assert len(solver_team._participants) == 2
    assert isinstance(solver_team._termination_condition, MaxMessageTermination)
