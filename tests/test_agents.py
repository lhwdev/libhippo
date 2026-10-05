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
from libhippo.models.knowledge import KnowledgeCandidate
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
SNIPPET:
```markdown
Use native button elements for keyboard accessibility.
```
RATIONALE: Directly answers button interaction guidelines.
"""
    parsed_hit = BookKeeperAgent.parse_output(hit_text)
    assert parsed_hit["status"] == "[HIT]"
    assert parsed_hit["path"] == "common/web/html/button.md"
    assert parsed_hit["confidence"] == 0.95
    assert parsed_hit["title"] == "Button Accessibility Rules"
    assert "native button elements" in parsed_hit["snippet"]
    assert "Directly answers" in parsed_hit["rationale"]

    miss_text = """
STATUS: [MISS:MANDATORY]
PATH: NONE
CONFIDENCE: 0.0
TITLE: NONE
SNIPPET:
```markdown
```
RATIONALE: No custom crypto encryption standard found in repository.
"""
    parsed_miss = BookKeeperAgent.parse_output(miss_text)
    assert parsed_miss["status"] == "[MISS:MANDATORY]"
    assert parsed_miss["path"] is None


@pytest.mark.asyncio
async def test_book_keeper_lookup_mock(tmp_path):
    """Test BookKeeperAgent.lookup with mock client and KnowledgeStore tools."""
    mock_response = """STATUS: [HIT]
PATH: common/web/html/button.md
CONFIDENCE: 0.92
TITLE: Button Rules
SNIPPET:
```markdown
Use button tags.
```
RATIONALE: Found in common store.
"""
    client = make_mock_client(mock_response)
    async with KnowledgeStore(root_dir=tmp_path / "knowledge") as store:
        agent = BookKeeperAgent(model_client=client, store=store)

        assert agent.name == "BookKeeperAgent"
        assert len(agent._tools) >= 2  # search_knowledge and read_knowledge

        res = await agent.lookup(query="accessible button keyboard")
        assert res["status"] == "[HIT]"
        assert res["path"] == "common/web/html/button.md"
        assert res["confidence"] == 0.92
        assert "Use button tags." in res["snippet"]


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
    assert inferred == "common/react_19_form_actions.md"


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
    assert len(agent._tools) == 2  # search_web and fetch_web

    result = await agent.curate(topic_or_query="Tailwind v4")
    assert result["path"] == "common/tailwind_v4_css.md"
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

    mock_text = """STATUS: REFACTOR_PLANNED
PARENT_HUB: common/oversized.md
CHILD_NODES:
  - PATH: common/part1.md
    SCOPE: Part 1
RATIONALE: Split needed
"""
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
