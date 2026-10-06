"""Unit tests for CheckerAgent and TypeSafe Jev integration."""

from unittest.mock import MagicMock

import pytest
from typesafe_sdk import SystemOneResponse

from libhippo.agents.checker import CheckerAgent, TokenCountBoundary
from libhippo.models.knowledge import (
    HubReference,
    KnowledgeCandidate,
    KnowledgeContext,
    SiblingReference,
)


class MockTypeSafeClient:
    """Mock client for testing CheckerAgent without making live API calls."""

    def __init__(
        self,
        taxonomy: str = "optimal",
        bloat: float = 1.0,
        importance: float = 1.8,
        effectiveness: float = 0.0,
        coherence: float = 2.0,
        grammar: float = 2.0,
        markdown_quality: float = 2.0,
        practical_utility: float = 2.0,
        coalescence: float = 0.1,
        redundancy: float = 0.05,
    ) -> None:
        self.taxonomy = taxonomy
        self.bloat = bloat
        self.importance = importance
        self.effectiveness = effectiveness
        self.coherence = coherence
        self.grammar = grammar
        self.markdown_quality = markdown_quality
        self.practical_utility = practical_utility
        self.coalescence = coalescence
        self.redundancy = redundancy
        self.tag_quality = 2.0
        self.has_tag_bloat = 0.05

    async def system_one(self, *, state: dict, questions: dict, model: str | None = None) -> SystemOneResponse:
        # Mock answers
        return MagicMock(
            choices={"taxonomy_fit": MagicMock(choice=self.taxonomy)},
            scores={
                "bloatedness": MagicMock(score=self.bloat),
                "importance": MagicMock(score=self.importance),
                "rule_effectiveness": MagicMock(score=self.effectiveness),
                "coherence": MagicMock(score=self.coherence),
                "grammar_and_clarity": MagicMock(score=self.grammar),
                "markdown_format_quality": MagicMock(score=self.markdown_quality),
                "practical_utility": MagicMock(score=self.practical_utility),
                "tag_quality": MagicMock(score=self.tag_quality),
            },
            nouls={
                "is_coalescence_candidate": MagicMock(noul=self.coalescence),
                "has_redundancy_or_conflict": MagicMock(noul=self.redundancy),
                "has_tag_bloat_or_generic_noise": MagicMock(noul=self.has_tag_bloat),
            },
        )


@pytest.mark.asyncio
async def test_checker_routine_pass():
    """Test Case 1: Standard clean leaf passes with zero LLM overhead."""
    raw_md = """---
title: "Secure External Links"
namespace: "common"
version: "1.0.0"
status: "active"
nature: "critical_rule"
importance: 0.85
---

## Summary (Coarse View)
Always use rel="noopener noreferrer" on target="_blank" links to prevent reverse tabnabbing and window.opener security vulnerabilities.

## Detailed Rules & Edge Cases (Fine View)
- Modern Chromium and WebKit browsers implicitly set rel="noopener" on target="_blank" anchors, but explicit declaration remains necessary for legacy browsers, Firefox configurations, and cross-platform webview runtimes.
- If your application explicitly requires access to the originating window context via JavaScript (e.g., OAuth authentication popups or payment gateway redirects), specify rel="opener" instead, and validate the target window origin via window.addEventListener("message").
- Combining noreferrer with noopener ensures that the Referer header is omitted during navigation, preserving user privacy when directing traffic to untrusted external domains.
- Avoid using JavaScript void(0) or hash anchors (#) in place of semantic navigation elements; utilize HTML buttons with appropriate ARIA attributes for interactive controls.
- Audit external links across dynamic user-submitted markdown content by sanitizing HTML inputs and automatically injecting secure rel attributes before rendering to DOM.
"""
    candidate = KnowledgeCandidate.from_markdown("common/web/html/syntax/links.md", raw_md)
    context = KnowledgeContext(
        parent=HubReference(path="common/web/html.md", title="HTML Standards"),
        siblings=[SiblingReference(path="common/web/html/syntax/semantic_tags.md", title="Semantic Tags")],
    )

    mock_client = MockTypeSafeClient(taxonomy="optimal", bloat=1.0, importance=1.8, coherence=2.0)
    agent = CheckerAgent(client=mock_client)

    report = await agent.check(candidate, context)

    assert report.verdict == "PASS"
    assert report.schema_valid is True
    assert report.size_status == "optimal"
    assert report.importance_score == 0.90
    assert report.taxonomy_fit == "optimal"


@pytest.mark.asyncio
async def test_checker_undersized_merge_coalescence():
    """Test Case 2: Undersized micro-rule triggers MERGE_REQUIRED."""
    raw_md = """---
title: "Tiny Aria Tip"
namespace: "common"
status: "active"
nature: "transient_tip"
---

Tiny tip about aria.
"""
    candidate = KnowledgeCandidate.from_markdown("common/web/html/accessibility/aria_tip.md", raw_md)
    context = KnowledgeContext(
        parent=HubReference(path="common/web/html.md"),
        siblings=[SiblingReference(path="common/web/html/accessibility/aria_button.md")],
    )

    # Bloat 0.0 (sparse stub), coalescence 0.85 (should be merged with sibling)
    mock_client = MockTypeSafeClient(taxonomy="optimal", bloat=0.0, coalescence=0.85)
    agent = CheckerAgent(client=mock_client)

    report = await agent.check(candidate, context)

    assert report.verdict == "MERGE_REQUIRED"
    assert report.size_status == "undersized"
    assert report.merge_recommendation == "merge_into_sibling"
    assert "common/web/html/accessibility/aria_button.md" in report.merge_candidate_siblings
    assert report.details["token_count_bound"]["lower_trigger"] == 300.0
    assert report.details["token_count_bound"]["lower_target"] == 500.0
    assert "Lower hysteresis triggered" in report.details["token_count_bound"]["instruction"]


@pytest.mark.asyncio
async def test_checker_oversized_split_escalation():
    """Test Case 3: Oversized / bloated document triggers ESCALATE_REFACTOR."""
    # Create large content
    raw_md = """---
title: "Massive React Guide"
namespace: "common"
status: "active"
nature: "foundation"
---

""" + ("## Section\nContent explaining architecture and state.\n" * 150)

    candidate = KnowledgeCandidate.from_markdown("common/web/react.md", raw_md)
    context = KnowledgeContext(parent=HubReference(path="common/web.md"))

    # Bloat 2.8 (severe monolithic overload)
    mock_client = MockTypeSafeClient(taxonomy="optimal", bloat=2.8)
    agent = CheckerAgent(client=mock_client)

    report = await agent.check(candidate, context)

    assert report.verdict == "ESCALATE_REFACTOR"
    assert report.size_status == "oversized"
    assert report.effective_token_count >= 1800.0
    assert report.details["token_count_bound"]["upper_trigger"] == 1800.0
    assert report.details["token_count_bound"]["upper_target"] == 1000.0
    assert "Upper hysteresis triggered" in report.details["token_count_bound"]["instruction"]


@pytest.mark.asyncio
async def test_checker_schema_error_rejection():
    """Test Case 4: Broken YAML frontmatter triggers REVISE_SCHEMA."""
    raw_md = """---
title: "Bad Schema Node"
status: "invalid_status"
---

Content without coarseness or namespace.
"""
    candidate = KnowledgeCandidate.from_markdown("common/web/bad.md", raw_md)
    agent = CheckerAgent(client=MockTypeSafeClient())

    report = await agent.check(candidate)

    assert report.verdict == "REVISE_SCHEMA"
    assert report.schema_valid is False
    assert len(report.schema_errors) > 0


@pytest.mark.asyncio
async def test_checker_as_tool_callable():
    """Test Case 5: CheckerAgent.as_tool() returns modular callable dictionary."""
    agent = CheckerAgent(client=MockTypeSafeClient(taxonomy="optimal"))
    tool_fn = agent.as_tool()

    raw_md = """---
title: "Tool Check"
namespace: "common"
status: "active"
nature: "foundation"
---
## Summary
Summary text.
## Detailed Rules
Rules text.
"""
    result = await tool_fn(
        path="common/web/tool.md",
        content=raw_md,
        parent_path="common/web.md",
        sibling_paths=["common/web/other.md"],
    )

    assert isinstance(result, dict)
    assert result["verdict"] == "PASS"
    assert result["taxonomy_fit"] == "optimal"


@pytest.mark.asyncio
async def test_checker_canonical_naming_and_dynamic_modulation():
    """Test Case 6: Verify canonical hysteresis naming and dynamic modulation."""
    mock_client = MockTypeSafeClient(taxonomy="optimal", bloat=1.0, importance=1.8, coherence=2.0, redundancy=0.0)
    agent = CheckerAgent(
        client=mock_client,
        token_count_bound=TokenCountBoundary(
            lower_trigger=300.0,
            lower_target=500.0,
            upper_target=1000.0,
            upper_trigger=1800.0,
        )
    )

    assert agent.token_count_bound.lower_trigger == 300.0
    assert agent.token_count_bound.lower_target == 500.0
    assert agent.token_count_bound.upper_target == 1000.0
    assert agent.token_count_bound.upper_trigger == 1800.0

    raw_md = """---
title: "Coherent Guide"
namespace: "common"
status: "active"
nature: "foundation"
---
## Summary
Summary.
## Detailed Rules
""" + ("- Rule detail.\n" * 160)

    candidate = KnowledgeCandidate.from_markdown("common/web/coherent.md", raw_md)
    context = KnowledgeContext(
        parent=HubReference(path="common/web.md"),
        siblings=[SiblingReference(path="common/web/other.md")],
    )

    report = await agent.check(candidate, context)
    token_count_bound = report.details["token_count_bound"]

    # Canonical naming in report details
    assert token_count_bound["lower_trigger"] == 300.0
    assert token_count_bound["upper_trigger"] == 1800.0
    # Coherence modulation (coherence=1.0 raises upper trigger to 2200)
    assert token_count_bound["effective_upper_trigger"] == 2200.0
    # Sibling diversity modulation (redundancy=0.0 lowers lower trigger down to 200)
    assert token_count_bound["effective_lower_trigger"] == 200.0


@pytest.mark.asyncio
async def test_checker_as_tool_with_content_arg():
    """Test Case 7: audit_knowledge accepts content argument from architecture.md."""
    agent = CheckerAgent(client=MockTypeSafeClient(taxonomy="optimal"))
    tool_fn = agent.as_tool()

    raw_md = """---
title: "Tool Content Param Check"
namespace: "common"
status: "active"
nature: "foundation"
---
## Summary
Summary text.
## Detailed Rules
Rules text.
"""
    result = await tool_fn(
        path="common/web/tool.md",
        content=raw_md,
        parent_path="common/web.md",
    )

    assert isinstance(result, dict)
    assert result["verdict"] == "PASS"


@pytest.mark.asyncio
async def test_checker_post_merge_undersized_exception_vs_prune():
    """Test Case 8: Section 3.4.2 post-merge undersized standalone exception vs low-importance prune."""
    raw_md = """---
title: "Primitive Rule"
namespace: "common"
status: "active"
nature: "foundation"
---
Small primitive.
"""
    candidate = KnowledgeCandidate.from_markdown("common/web/primitive.md", raw_md)

    # 1. High importance, unmergeable (coalescence=0.1 <= 0.5) -> retained as standalone exception (PASS)
    client_important = MockTypeSafeClient(taxonomy="optimal", bloat=1.0, importance=1.8, coalescence=0.1)
    agent = CheckerAgent(client=client_important)
    report_important = await agent.check(candidate)
    assert report_important.size_status == "undersized"
    assert report_important.verdict == "PASS"

    # 2. Low importance (<0.30), unmergeable -> prune/reject (REVISE_SCHEMA)
    client_trivial = MockTypeSafeClient(taxonomy="optimal", bloat=1.0, importance=0.2, coalescence=0.1)
    agent_trivial = CheckerAgent(client=client_trivial)
    report_trivial = await agent_trivial.check(candidate)
    assert report_trivial.size_status == "undersized"
    assert report_trivial.verdict == "REVISE_SCHEMA"


@pytest.mark.asyncio
async def test_checker_strict_styling_rule_importance():
    """Test Case 9: Strict styling rule ('use single-quote') achieves final importance=1.0."""
    raw_md = """---
title: "Single Quote Conventions"
namespace: "project"
status: "active"
nature: "critical_rule"
---
## Summary (Coarse View)
Always use single-quotes instead of double-quotes across all JavaScript and TypeScript files.

## Detailed Rules & Edge Cases (Fine View)
- Enforce single quotes in JSX attributes and standard strings.
- Escape single quotes inside strings with backslash or use template literals if multiline.
"""
    candidate = KnowledgeCandidate.from_markdown("project/style/quotes.md", raw_md)
    # Content importance is cosmetic (0.2 -> 0.10), but rule effectiveness is mandatory (2.0 -> 1.0)
    mock_client = MockTypeSafeClient(
        taxonomy="optimal",
        bloat=1.0,
        importance=0.2,
        effectiveness=2.0,
        grammar=2.0,
        markdown_quality=2.0,
        practical_utility=2.0,
    )
    agent = CheckerAgent(client=mock_client)
    report = await agent.check(candidate)

    assert report.content_importance == 0.10
    assert report.effectiveness_score == 1.00
    # Final importance must reach 1.0
    assert report.importance_score == 1.00
    assert report.verdict == "PASS"


@pytest.mark.asyncio
async def test_checker_damped_max_blend_moderate():
    """Test Case 10: Moderate content and effectiveness (0.6, 0.6) yields damped synergy boost (~0.65)."""
    raw_md = """---
title: "Component Organization"
namespace: "common"
status: "active"
nature: "foundation"
---
## Summary (Coarse View)
Group related UI components by feature module.

## Detailed Rules & Edge Cases (Fine View)
- Keep co-located unit tests next to implementation files.
"""
    candidate = KnowledgeCandidate.from_markdown("common/structure/components.md", raw_md)
    # Importance = 1.2 (0.60), Effectiveness = 1.2 (0.60)
    mock_client = MockTypeSafeClient(
        taxonomy="optimal",
        bloat=1.0,
        importance=1.2,
        effectiveness=1.2,
    )
    agent = CheckerAgent(client=mock_client)
    report = await agent.check(candidate)

    assert report.content_importance == 0.60
    assert report.effectiveness_score == 0.60
    # Damped max-blend: 0.60 + 0.20 * 0.60 * 0.40 = 0.648 -> 0.65
    assert report.importance_score == 0.65


@pytest.mark.asyncio
async def test_checker_declarative_reference_passes():
    """Test Case 11: Declarative reference (syntax sheet/dictionary) with high practical utility passes."""
    raw_md = """---
title: "CSS Display Property Reference"
namespace: "common"
status: "active"
nature: "foundation"
---
## Summary (Coarse View)
Syntax reference and specification values for the CSS display property.

## Detailed Rules & Edge Cases (Fine View)
- `block`: Generates a block element box.
- `inline`: Generates one or more inline boxes.
- `flex`: Becomes a block-level flex container.
- `grid`: Becomes a block-level grid container.
"""
    candidate = KnowledgeCandidate.from_markdown("common/web/css/display.md", raw_md)
    mock_client = MockTypeSafeClient(
        taxonomy="optimal",
        bloat=1.0,
        importance=1.6,
        effectiveness=1.0,
        practical_utility=2.0,  # Reference-complete
    )
    agent = CheckerAgent(client=mock_client)
    report = await agent.check(candidate)

    assert report.practical_utility_score == 1.00
    assert report.verdict == "PASS"


@pytest.mark.asyncio
async def test_checker_content_error_revise_content():
    """Test Case 12: Poor grammar and clarity triggers REVISE_CONTENT verdict."""
    raw_md = """---
title: "Sloppy Guide"
namespace: "common"
status: "active"
nature: "foundation"
---
## Summary
Sure thing! Here is what I think about stuff maybe you do it.
"""
    candidate = KnowledgeCandidate.from_markdown("common/web/sloppy.md", raw_md)
    # Low grammar score (0.4 / 2.0 = 0.20 < 0.40)
    mock_client = MockTypeSafeClient(
        taxonomy="optimal",
        grammar=0.4,
        markdown_quality=2.0,
        practical_utility=2.0,
    )
    agent = CheckerAgent(client=mock_client)
    report = await agent.check(candidate)

    assert report.verdict == "REVISE_CONTENT"
    assert any("Grammar and clarity score below threshold" in err for err in report.content_errors)


@pytest.mark.asyncio
async def test_checker_unmatched_code_fence():
    """Test Case 13: Deterministic unmatched code fence triggers REVISE_CONTENT."""
    raw_md = """---
title: "Broken Fence Guide"
namespace: "common"
status: "active"
nature: "foundation"
---
## Summary
Broken code block below:
```python
def foo():
    pass
"""
    candidate = KnowledgeCandidate.from_markdown("common/web/fence.md", raw_md)
    agent = CheckerAgent(client=MockTypeSafeClient(taxonomy="optimal"))
    report = await agent.check(candidate)

    assert report.verdict == "REVISE_CONTENT"
    assert "Unmatched code fence (odd count of '```')" in report.content_errors


@pytest.mark.asyncio
async def test_checker_force_keep_preserves_stub():
    """Test Case 14: force_keep=True preserves undersized node and keeps suggested_path pinned."""
    raw_md = """---
title: "Pinned External Stub"
namespace: "common"
status: "active"
nature: "transient_tip"
force_keep: true
---
Tiny stub that is symlinked to another repository.
"""
    candidate = KnowledgeCandidate.from_markdown("common/web/pinned_stub.md", raw_md)
    context = KnowledgeContext(
        parent=HubReference(path="common/web.md"),
        siblings=[SiblingReference(path="common/web/sibling.md")],
    )

    # Even with bloat 0.0, coalescence 0.95 (normally MERGE_REQUIRED), low importance (normally REVISE_SCHEMA)
    # and misplaced taxonomy (normally suggests relocation)
    mock_client = MockTypeSafeClient(taxonomy="misplaced", bloat=0.0, importance=0.2, coalescence=0.95)
    agent = CheckerAgent(client=mock_client)


    report = await agent.check(candidate, context)

    # With force_keep, verdict must be PASS and suggested_path must be None (pinned in place)
    assert report.verdict == "PASS"
    assert report.suggested_path is None



