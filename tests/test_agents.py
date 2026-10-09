"""Unit tests for Phase 4 LibHippo agents: BookKeeper, Curator, Verifier, and TaskSolver."""

from unittest.mock import AsyncMock

import pytest
from autogen_core.models import CreateResult, RequestUsage

from libhippo.agents.book_keeper import BookKeeperAgent
from libhippo.agents.checker import CheckerAgent
from libhippo.agents.curator import CuratorAgent
from libhippo.agents.manager import AgentManager
from libhippo.agents.task_solver import TaskSolverAgent
from libhippo.agents.verifier import VerifierAgent
from libhippo.models.audit import JevAuditReport
from libhippo.models.knowledge import KnowledgeCandidate, reconcile_candidate_frontmatter
from libhippo.orchestration.maker_checker import MakerCheckerOrchestrator
from libhippo.storage.mount import MountConfig
from libhippo.storage.store import KnowledgeStore
from libhippo.tools.retrieval import KnowledgeDispatcher


def make_mock_client(content: str) -> AsyncMock:
    """Helper to create a mock ChatCompletionClient returning a specific text response."""
    client = AsyncMock()
    client.create.return_value = CreateResult(
        finish_reason="stop",
        content=content,
        usage=RequestUsage(prompt_tokens=25, completion_tokens=15),
        cached=False,
    )
    return client


# --- BookKeeperAgent Tests ---

def test_book_keeper_parsing():
    """Verify BookKeeperAgent output parsing across HIT and MISS tags."""
    hit_text = """
STATUS: [HIT]
PATH: common/web/html/button.md
CONFIDENCE: 0.95
TITLE: Button Accessibility Rules
KEYWORDS: button, keyboard accessibility, native button
RATIONALE: Directly answers button interaction guidelines.
"""
    parsed_hit = BookKeeperAgent.parse_output(hit_text)
    assert parsed_hit["status"] == "[HIT]"
    assert parsed_hit["path"] == "common/web/html/button.md"
    assert parsed_hit["confidence"] == 0.95
    assert parsed_hit["title"] == "Button Accessibility Rules"
    assert "keyboard accessibility" in parsed_hit["keywords"]
    assert "Directly answers" in parsed_hit["rationale"]

    miss_text = """
STATUS: [MISS:MANDATORY]
PATH: NONE
CONFIDENCE: 0.0
TITLE: NONE
KEYWORDS: 
RATIONALE: No custom crypto encryption standard found in repository.
"""
    parsed_miss = BookKeeperAgent.parse_output(miss_text)
    assert parsed_miss["status"] == "[MISS:MANDATORY]"
    assert parsed_miss["path"] is None


@pytest.mark.asyncio
async def test_book_keeper_lookup_no_candidates():
    """Verify BookKeeper skips execution when candidates list is empty, and correctly selects leaf candidate among multiple candidates."""
    agent = BookKeeperAgent()
    result = await agent.lookup(query="test query", candidates=[])
    assert result["status"] == "[MISS:FALLBACK]"
    assert result["path"] is None
    assert result["confidence"] == 0.0

    # Test multi-candidate selection using mock client: parent overview vs leaf candidate
    class MockCandidate:
        def __init__(self, path: str, title: str, content: str, confidence: float = 0.70):
            self.path = path
            self.title = title
            self.content = content
            self.confidence = confidence

    class MockTypeSafeClient:
        async def system_one(self, *, state: dict, questions: dict, model: str | None = None, **kwargs):
            # Assert that up to 3 candidates were structured with top_p and low max_tokens
            assert len(state["candidates"]) == 2
            assert "fit_0" in questions
            assert "fit_1" in questions
            assert kwargs.get("top_p") == 0.65
            assert kwargs.get("max_tokens") == 64
            # Candidate 0 is too broad parent; Candidate 1 is optimal leaf
            return {
                "fit_0": "too_broad",
                "fit_1": "optimal",
                "should_specialize_leaf": 0.1,
                "should_record_alias": 0.8,
            }

    multi_agent = BookKeeperAgent(client=MockTypeSafeClient())
    candidates = [
        MockCandidate("common/react.md", "React Overview", "# React\nGeneral overview of UI components."),
        MockCandidate("common/react/use_effect.md", "useEffect Parameters", "# useEffect\nDetailed parameters and cleanup function syntax."),
    ]
    multi_res = await multi_agent.lookup(query="react useEffect cleanup parameter", candidates=candidates)
    assert multi_res["status"] == "[HIT]"
    assert multi_res["path"] == "common/react/use_effect.md"
    assert "common/react/use_effect.md" in multi_res["rationale"]


# @pytest.mark.asyncio
# async def test_book_keeper_lookup_mock(tmp_path):
#     """Test BookKeeperAgent.lookup with mock client and KnowledgeStore tools."""
#     mock_response = """STATUS: [HIT]
# PATH: common/web/html/button.md
# CONFIDENCE: 0.92
# TITLE: Button Rules
# SNIPPET:
# ```markdown
# Use button tags.
# ```
# RATIONALE: Found in common store.
# """
#     client = make_mock_client(mock_response)
#     async with KnowledgeStore(root_dir=tmp_path / "knowledge") as store:
#         agent = BookKeeperAgent(model_client=client, store=store)

#         assert agent.name == "BookKeeperAgent"
#         assert len(agent._tools) >= 2  # search_knowledge and read_knowledge

#         res = await agent.lookup(query="accessible button keyboard")
#         assert res["status"] == "[HIT]"
#         assert res["path"] == "common/web/html/button.md"
#         assert res["confidence"] == 0.92
#         assert "Use button tags." in res["snippet"]


# --- CuratorAgent Tests ---

def test_curator_markdown_extraction():
    """Verify CuratorAgent draft extraction and path inference."""
    raw_output = """Here is the drafted knowledge node based on official docs:

```markdown
---
title: "React 19 Form Actions"
namespace: "common"
nature: "critical_rule"
importance: 0.90
---

## Summary (Coarse View)
React 19 replaces useFormState with useActionState.

## Detailed Rules & Edge Cases (Fine View)
- Always import useActionState from react.
- Pass action and initial state.
```

Hope this helps!
"""
    draft = CuratorAgent.extract_markdown_draft(raw_output)
    assert draft.startswith("---")
    assert "title: \"React 19 Form Actions\"" in draft
    assert "useActionState" in draft

    inferred = CuratorAgent.infer_path(draft, "react form actions")
    assert inferred == "common/react_19_form_actions"


@pytest.mark.asyncio
async def test_curator_curate_and_revise_mock():
    """Test CuratorAgent.curate and .revise methods with mock client."""
    curate_response = """```markdown
---
title: "Tailwind v4 CSS"
namespace: "common"
nature: "foundation"
importance: 0.85
---
## Summary (Coarse View)
Tailwind v4 uses CSS-first configuration.
## Detailed Rules & Edge Cases (Fine View)
- Use @import "tailwindcss"; in main CSS.
```"""
    client = make_mock_client(curate_response)
    agent = CuratorAgent(model_client=client)

    assert agent.name == "CuratorAgent"
    assert len(agent._tools) == 9  # write_knowledge, read_knowledge, list_knowledge, search_knowledge, commit, commit_all, run_command, search_web, fetch_web

    result = await agent.curate(topic_or_query="Tailwind v4")
    assert result["path"] == "common/tailwind_v4_css"
    assert "Tailwind v4 uses CSS-first" in result["draft"]
    assert result["nature"] == "foundation"

    # Test revise
    revise_response = """```markdown
---
title: "Tailwind v4 CSS Revised"
namespace: "common"
nature: "foundation"
importance: 0.85
---
## Summary (Coarse View)
Tailwind v4 revised summary.
## Detailed Rules & Edge Cases (Fine View)
- Fixed rules.
```"""
    client.create.return_value = CreateResult(
        finish_reason="stop",
        content=revise_response,
        usage=RequestUsage(prompt_tokens=20, completion_tokens=10),
        cached=False,
    )

    rev_res = await agent.revise(
        candidate_markdown=result["draft"],
        feedback="Title must be more descriptive",
        target_path="common/tailwind.md",
    )
    assert rev_res["path"] == "common/tailwind.md"
    assert "Tailwind v4 revised summary." in rev_res["draft"]


# --- VerifierAgent Tests ---

def test_verifier_directive_parsing():
    """Verify VerifierAgent structured refactoring output parsing."""
    directive_text = """
STATUS: REFACTOR_PLANNED
PARENT_HUB: common/web/html/button.md
CHILD_NODES:
  - PATH: common/web/html/button/accessibility.md
    SCOPE: ARIA keyboard handling and focus traps
    TARGET_SIZE: 600
  - PATH: common/web/html/button/styling.md
    SCOPE: CSS reset and active states
    TARGET_SIZE: 500
RATIONALE: Document exceeded 1800 tokens; separating styling and accessibility.
"""
    parsed = VerifierAgent.parse_directive(directive_text)
    assert parsed["status"] == "REFACTOR_PLANNED"
    assert parsed["action"] == "split"
    assert parsed["parent_hub"] == "common/web/html/button.md"
    assert len(parsed["children"]) == 2
    assert parsed["children"][0]["path"] == "common/web/html/button/accessibility.md"
    assert parsed["children"][1]["path"] == "common/web/html/button/styling.md"


@pytest.mark.asyncio
async def test_verifier_split_and_merge_mutations(tmp_path):
    """Test VerifierAgent split_oversized_node and merge_nodes against KnowledgeStore."""
    async with KnowledgeStore(root_dir=tmp_path / "knowledge") as store:
        await store.initialize()

        agent = VerifierAgent(store=store)

        # 1. Split oversized node
        hub_content = """---
title: "HTML Buttons"
namespace: "common"
---
## Summary (Coarse View)
Hub for button patterns.
## Detailed Rules & Edge Cases (Fine View)
- See child nodes for details.
"""
        child_nodes = [
            {
                "path": "common/web/button/aria.md",
                "content": """---
title: "Button ARIA"
namespace: "common"
---
## Summary
ARIA button guidelines.
## Detailed Rules
Handle Space key.
""",
            },
            {
                "path": "common/web/button/styles.md",
                "content": """---
title: "Button Styles"
namespace: "common"
---
## Summary
Style guidelines.
## Detailed Rules
No outline: none.
""",
            },
        ]

        split_res = await agent.split_oversized_node(
            path="common/web/button.md",
            store=store,
            hub_content=hub_content,
            child_nodes=child_nodes,
        )
        assert split_res["status"] == "success"
        assert len(split_res["children"]) == 2

        # Verify nodes exist in store
        assert await store.get_node("common/web/button.md") is not None
        assert await store.get_node("common/web/button/aria.md") is not None
        assert await store.get_node("common/web/button/styles.md") is not None

        # 2. Merge nodes
        merged_content = """---
title: "Consolidated Button Rules"
namespace: "common"
---
## Summary
Consolidated button rules.
## Detailed Rules
Consolidated details.
"""
        merge_res = await agent.merge_nodes(
            target_path="common/web/button/consolidated.md",
            extra_paths=["common/web/button/aria.md", "common/web/button/styles.md"],
            content=merged_content,
            store=store,
        )
        assert merge_res["status"] == "success"
        assert await store.get_node("common/web/button/consolidated.md") is not None
        assert await store.get_node("common/web/button/aria.md") is None


@pytest.mark.asyncio
async def test_verifier_resolve_escalation_read_only(tmp_path):
    """Verify VerifierAgent escalation respects read-only mounts."""
    comm_dir = tmp_path / "comm"
    comm_dir.mkdir(parents=True, exist_ok=True)
    mounts = [
        MountConfig(namespace_prefix="common", physical_path=comm_dir, read_only=True),
    ]

    mock_text = """{
  "status": "REFACTOR_PLANNED",
  "action": "split",
  "parent_hub": "common/oversized.md",
  "children": [{"path": "common/part1.md", "scope": "Part 1"}],
  "rationale": "Split needed"
}"""
    client = make_mock_client(mock_text)
    async with KnowledgeStore(mounts=mounts, cache_dir=tmp_path / "cache") as store:
        await store.initialize()
        agent = VerifierAgent(model_client=client, store=store)

        candidate = KnowledgeCandidate.from_markdown("common/oversized.md", "---\ntitle: T\n---\nBody")
        report = JevAuditReport(
            verdict="ESCALATE_REFACTOR",
            size_status="oversized",
            taxonomy_fit="optimal",
            token_count=2000,
            effective_token_count=2000,
            bloatedness_score=2.5,
            content_errors=["Document exceeds 1800 tokens"],
        )

        res = await agent.resolve_escalation(candidate=candidate, report=report, store=store)
        # Since mount is read-only, split mutation should be rejected with ReadOnlyMountError
        assert res["status"] == "REVISE_REJECTED"
        assert "ReadOnlyMountError" in res.get("error", "")


# --- TaskSolverAgent Tests ---

@pytest.mark.asyncio
async def test_task_solver_mock(tmp_path):
    """Test TaskSolverAgent initialization with dispatcher and solution execution."""
    client = make_mock_client("Here is the solution implementing keyboard accessible buttons with preventDefault().")
    async with KnowledgeStore(root_dir=tmp_path / "knowledge") as store:
        dispatcher = KnowledgeDispatcher(store=store)
        agent = TaskSolverAgent(model_client=client, dispatcher=dispatcher, store=store)
        assert agent.name == "TaskSolverAgent"
        assert len(agent._tools) >= 1  # query_knowledge tool

        solution = await agent.solve("Implement accessible custom button component in HTML/TS")
        assert "keyboard accessible buttons" in solution


# --- AgentManager Tests ---

@pytest.mark.asyncio
async def test_agent_manager_lifecycle_and_reuse(tmp_path):
    """Verify AgentManager coordinates and reuses framework knowledge agents and orchestrator."""
    async with KnowledgeStore(root_dir=tmp_path / "knowledge") as store:
        manager = AgentManager(store=store)
        assert manager.curator is not None
        assert manager.checker is not None
        assert manager.verifier is not None
        assert manager.book_keeper is not None
        assert manager.orchestrator is not None

        # Verify orchestrator reuses the same agent instances
        assert manager.orchestrator.curator is manager.curator
        assert manager.orchestrator.checker is manager.checker
        assert manager.orchestrator.verifier is manager.verifier

        # Verify dispatcher is created and wired
        dispatcher = manager.create_dispatcher()
        assert dispatcher.curator is manager.curator
        assert dispatcher.checker is manager.checker
        assert dispatcher.book_keeper is manager.book_keeper
        assert dispatcher.orchestrator is manager.orchestrator

        # Reusing dispatcher returns singleton
        assert manager.create_dispatcher() is dispatcher


@pytest.mark.asyncio
async def test_knowledge_dispatcher_mandatory_miss_creates_content(tmp_path):
    """Verify KnowledgeDispatcher creates and commits new content on mandatory miss via orchestrator."""
    async with KnowledgeStore(root_dir=tmp_path / "knowledge") as store:
        mock_checker = AsyncMock(spec=CheckerAgent)
        mock_checker.count_tokens.return_value = 500
        mock_checker.check.return_value = JevAuditReport(
            verdict="PASS",
            size_status="optimal",
            taxonomy_fit="optimal",
            token_count=500,
        )

        mock_curator = AsyncMock(spec=CuratorAgent)
        mock_curator.curate.return_value = {
            "path": "common/web/react_actions.md",
            "draft": """---
title: "React Actions"
namespace: "common"
---
## Summary
React 19 Server Actions.
## Detailed Rules
Use useActionState.
""",
        }

        orchestrator = MakerCheckerOrchestrator(
            store=store,
            checker=mock_checker,
            curator=mock_curator,
        )
        dispatcher = KnowledgeDispatcher(
            store=store,
            orchestrator=orchestrator,
        )
        assert dispatcher.curator is mock_curator

        res = await dispatcher.query_knowledge(
            query="React 19 Server Actions",
            effort="medium",
            criticality="mandatory",
        )

        assert res.status == "HIT"
        assert res.path == "common/web/react_actions.md"
        assert res.source == "curator"
        mock_curator.curate.assert_called_once()

        # Node should be committed into store
        node = await store.get_node("common/web/react_actions.md")
        assert node is not None
        assert "useActionState" in node.body


@pytest.mark.asyncio
async def test_draftsman_session_and_sanity_checks(tmp_path):
    """Verify KnowledgeDraftSession: reading, writing, sanity checks, and multi-draft commits."""
    from libhippo.agents.draftsman import KnowledgeDraftSession, run_draft_sanity_checks

    # 1. Test run_draft_sanity_checks
    invalid_md = "# No Frontmatter\nSome content without YAML."
    res_inv = run_draft_sanity_checks(invalid_md)
    assert not res_inv["passed"]
    assert any("frontmatter" in e.lower() for e in res_inv["errors"])

    # Properties in categories 2~4 are NOT rejected in sanity checks (to avoid burden on modifying drafts)
    draft_with_extra_md = """---
title: "Pinned Node"
force_keep: true
importance: 0.95
status: "deprecated"
nature: "hub"
---
## Rules
Content with code:
```python
print("ok")
```
"""
    res_extra = run_draft_sanity_checks(draft_with_extra_md)
    assert res_extra["passed"]

    # When modifying existing unpinned doc (with previous force_keep=False):
    prev_unpinned = KnowledgeCandidate.from_markdown(
        "common/pinned.md",
        """---
title: "Unpinned Node"
force_keep: false
importance: 0.60
status: "active"
---
## Old Rules
""",
    )
    cand_mod1 = KnowledgeCandidate.from_markdown("common/pinned.md", draft_with_extra_md)
    reconciled_mod1 = reconcile_candidate_frontmatter(cand_mod1, previous_candidate=prev_unpinned)
    assert reconciled_mod1.frontmatter.force_keep is False
    assert reconciled_mod1.frontmatter.importance == 0.60

    # When modifying existing pinned doc (with previous force_keep=True):
    prev_node = KnowledgeCandidate.from_markdown(
        "common/pinned.md",
        """---
title: "Pinned Node"
force_keep: true
importance: 0.85
status: "active"
---
## Old Rules
""",
    )
    # Even if draftsman attempted to unpin or omit force_keep:
    draft_unpin_attempt = """---
title: "Pinned Node"
force_keep: false
---
## Rules
"""
    cand_mod2 = KnowledgeCandidate.from_markdown("common/pinned.md", draft_unpin_attempt)
    reconciled_mod2 = reconcile_candidate_frontmatter(cand_mod2, previous_candidate=prev_node)
    assert reconciled_mod2.frontmatter.force_keep is True
    assert reconciled_mod2.frontmatter.importance == 0.85

    # Valid agent draft without namespace, version, or nature
    valid_md = """---
title: "React 19 Form Actions"
source:
  - "https://react.dev/reference/react-dom/components/form"
---
## Summary
Use React 19 form actions and useActionState for pending states.

## Detailed Rules & Edge Cases
- Always bind actions directly to the <form action={...}> attribute.
- Use useActionState hook to manage submission state and error feedback.
```tsx
const [state, formAction, isPending] = useActionState(fn, initialState);
```
"""
    res_val = run_draft_sanity_checks(valid_md)
    assert res_val["passed"]
    assert res_val["frontmatter"]["title"] == "React 19 Form Actions"
    assert res_val["code_blocks"] == "PASS: all code blocks closed"

    # 2. Test KnowledgeDraftSession multi-draft workflow
    session = KnowledgeDraftSession(workspace_root=tmp_path)
    try:
        # Write whole document
        write_out1 = await session.write_knowledge("common/web/react_actions.md", valid_md)
        assert "Successfully updated draft" in write_out1
        assert "READY_TO_COMMIT" in write_out1

        # Read line-addressed slice
        slice_out = await session.read_knowledge(path="common/web/react_actions.md", start_line=1, end_line=5)
        assert "1: ---" in slice_out
        assert "2: title:" in slice_out

        # Surgical target search-and-replace
        write_out2 = await session.write_knowledge(
            path="common/web/react_actions.md",
            content="Use React 19 form actions and useActionState for optimal async handling.",
            target="Use React 19 form actions and useActionState for pending states.",
        )
        assert "READY_TO_COMMIT" in write_out2

        # Verify updated text
        updated_read = await session.read_knowledge("common/web/react_actions.md")
        assert "optimal async handling" in updated_read

        # Write second draft in same session (multi-draft)
        second_md = """---
title: "Next.js App Router"
namespace: "common"
version: "1.0.0"
nature: "leaf"
source:
  - "https://nextjs.org/docs"
---
## Summary
Next.js App Router conventions.
"""
        await session.write_knowledge("common/web/nextjs.md", second_md)
        assert len(session.drafts) == 2

        # Commit single draft (isolated mock session without store/orch)
        commit_res = await session.commit("common/web/react_actions.md")
        assert commit_res["status"] == "ready"
        assert commit_res["path"] == "common/web/react_actions"
        assert "content" not in commit_res

        # Commit all remaining drafts
        commit_all_res = await session.commit_all()
        assert commit_all_res["status"] == "ready"
        assert len(commit_all_res["drafts"]) == 1
        assert len(session.drafts) == 0

        # Verify rejection of invalid namespace (e.g. knowledge/...)
        bad_write = await session.write_knowledge("knowledge/react.md", valid_md)
        assert "[ERROR: Invalid namespace 'knowledge'" in bad_write

        bad_commit = await session.commit("knowledge/react")
        assert bad_commit["status"] == "error"
        assert "Invalid namespace 'knowledge'" in bad_commit["message"]
    finally:
        session.cleanup()


@pytest.mark.asyncio
async def test_draftsman_commit_with_orchestrator(tmp_path):
    """Verify in-tool commit triggers Maker-Checker governance and disk save."""
    from libhippo.agents.draftsman import KnowledgeDraftSession
    from libhippo.orchestration.maker_checker import MakerCheckerResult

    mock_orch = AsyncMock()
    mock_orch.run_governance.return_value = MakerCheckerResult(
        status="COMMITTED",
        path="common/web/react",
        verdict="PASS",
        message="Audited and committed successfully.",
    )

    session = KnowledgeDraftSession(workspace_root=tmp_path, orchestrator=mock_orch)
    try:
        md = """---
title: "React Components"
version: "1.0.0"
---
## Summary
React component rules.
"""
        await session.write_knowledge("common/web/react", md)
        res = await session.commit("common/web/react")

        assert res["status"] == "committed"
        assert res["path"] == "common/web/react"
        assert res["verdict"] == "PASS"
        assert "content" not in res
        assert session.is_committed is True
        assert mock_orch.run_governance.called
    finally:
        session.cleanup()


@pytest.mark.asyncio
async def test_curator_stops_after_successful_commit():
    """Verify CuratorAgent halts turn iteration once commit succeeds without extra LLM turns."""
    from autogen_core import FunctionCall
    from autogen_core.models import ChatCompletionClient, ModelCapabilities
    from autogen_agentchat.messages import TextMessage
    from libhippo.orchestration.maker_checker import MakerCheckerResult

    class FakeClient(ChatCompletionClient):
        def __init__(self):
            self.call_count = 0

        @property
        def model_info(self):
            return {"vision": False, "function_calling": True, "json_output": False, "family": "unknown"}

        @property
        def capabilities(self):
            return ModelCapabilities(vision=False, function_calling=True, json_output=False)

        async def create(self, messages, **kwargs):
            self.call_count += 1
            if self.call_count == 1:
                return CreateResult(
                    finish_reason="function_calls",
                    content=[
                        FunctionCall(
                            id="call_1",
                            name="write_knowledge",
                            arguments='{"path": "common/react", "content": "---\\ntitle: React\\n---\\n## Summary\\nRules."}',
                        ),
                        FunctionCall(
                            id="call_2",
                            name="commit",
                            arguments='{"path": "common/react"}',
                        ),
                    ],
                    usage=RequestUsage(prompt_tokens=10, completion_tokens=10),
                    cached=False,
                )
            return CreateResult(
                finish_reason="stop",
                content="Extra turn that should never happen!",
                usage=RequestUsage(prompt_tokens=10, completion_tokens=10),
                cached=False,
            )

        async def create_stream(self, messages, **kwargs):
            pass

        def actual_usage(self): return RequestUsage(prompt_tokens=0, completion_tokens=0)
        def total_usage(self): return RequestUsage(prompt_tokens=0, completion_tokens=0)
        def count_tokens(self, messages, **kwargs): return 1
        def remaining_tokens(self, messages, **kwargs): return 1000
        async def close(self): pass

    mock_orch = AsyncMock()
    mock_orch.run_governance.return_value = MakerCheckerResult(
        status="COMMITTED",
        path="common/react",
        verdict="PASS",
        message="Saved to disk.",
    )

    client = FakeClient()
    agent = CuratorAgent(model_client=client, orchestrator=mock_orch, max_tool_iterations=5)
    res = await agent.curate(topic_or_query="React")

    assert client.call_count == 1  # Exactly 1 LLM call; stopped right after commit!
    assert res.get("gov_result") is not None
    assert res["gov_result"].status == "COMMITTED"


@pytest.mark.asyncio
async def test_draftsman_commit_and_commit_all_return_revise_tag_on_audit_failure(tmp_path):
    """Verify commit and commit_all return structured <revise> tag as tool output on audit failure."""
    from libhippo.agents.draftsman import KnowledgeDraftSession
    from libhippo.models.audit import JevAuditReport
    from libhippo.orchestration.maker_checker import MakerCheckerResult

    report = JevAuditReport(
        verdict="REVISE_CONTENT",
        size_status="undersized",
        taxonomy_fit="optimal",
        token_count=150,
        effective_token_count=150,
        importance_score=0.25,
        content_errors=["Practical utility score below threshold (0.25 < 0.30)"],
    )

    mock_orch = AsyncMock()
    mock_orch.run_governance.return_value = MakerCheckerResult(
        status="REVISE_FAILED",
        path="common/law",
        verdict="REVISE_CONTENT",
        report=report,
        message="Audit verdict: REVISE_CONTENT\nSize status: undersized",
    )

    session = KnowledgeDraftSession(workspace_root=tmp_path, orchestrator=mock_orch)
    try:
        md = """---
title: "Law Basics"
version: "1.0.0"
---
## Summary
Law summary.
"""
        await session.write_knowledge("common/law", md)

        # Single commit test
        res_single = await session.commit("common/law")
        assert isinstance(res_single, str)
        assert '<revise path="common/law">' in res_single
        assert "<revise:AUDIT_FEEDBACK>" in res_single
        assert "REVISE_CONTENT" in res_single
        assert "Practical utility score below threshold" in res_single
        assert "</revise>" in res_single
        assert session.is_committed is False

        # Verify auto_revise was False
        mock_orch.run_governance.assert_called_with(
            candidate=md,
            target_path="common/law",
            auto_revise=False,
        )

        # commit_all test
        res_all = await session.commit_all()
        assert isinstance(res_all, str)
        assert '<revise path="common/law">' in res_all
        assert "<revise:AUDIT_FEEDBACK>" in res_all
        assert "</revise>" in res_all
        assert session.is_committed is False
    finally:
        session.cleanup()


