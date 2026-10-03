"""Unit tests for LibHippo core tools and query_knowledge dispatcher."""

from unittest.mock import AsyncMock

import pytest

from libhippo.storage.store import KnowledgeStore
from libhippo.tools.registry import ToolRegistry
from libhippo.tools.retrieval import KnowledgeDispatcher
from libhippo.tools.web import fetch_web, search_web


async def create_populated_store(tmp_path):
    store = KnowledgeStore(root_dir=tmp_path / "knowledge")
    await store.initialize()

    # Seed a high-importance document
    await store.save_node(
        "common/web/html/accessibility/aria_button.md",
        """---
title: "Button Accessibility with ARIA"
namespace: "common"
importance: 0.90
---
## Summary (Coarse View)
Use native button elements for full accessibility and keyboard interaction.

## Detailed Rules & Edge Cases (Fine View)
- When using div with role="button", ensure tabindex="0" and handle Enter/Space keys.
- Call preventDefault() on Space key to avoid page scrolling.
""",
    )
    return store


@pytest.mark.asyncio
async def test_read_and_search_tools(tmp_path):
    """Verify read_knowledge and search_knowledge tools."""
    populated_store = await create_populated_store(tmp_path)
    try:
        registry = ToolRegistry(store=populated_store)
        read_tool = registry.get_read_knowledge_tool()
        search_tool = registry.get_search_knowledge_tool()

        # Read summary
        summary = await read_tool("common/web/html/accessibility/aria_button.md", section="summary")
        assert "Use native button elements" in summary

        # Search
        search_res = await search_tool(query="keyboard interaction accessibility")
        assert len(search_res) >= 1
        assert search_res[0]["path"] == "common/web/html/accessibility/aria_button.md"
    finally:
        await populated_store.close()


@pytest.mark.asyncio
async def test_query_knowledge_low_effort(tmp_path):
    """Test 3-tier retrieval on low effort tier."""
    populated_store = await create_populated_store(tmp_path)
    try:
        dispatcher = KnowledgeDispatcher(store=populated_store, threshold_low=0.70)
        query_tool = ToolRegistry(store=populated_store, dispatcher=dispatcher).get_query_knowledge_tool()

        # 1. Matching query with high confidence
        hit = await query_tool(
            query="native button elements accessibility keyboard",
            effort="low",
            criticality="preferred",
        )
        assert hit["status"] == "HIT"
        assert hit["source"] == "fast_path"
        assert hit["confidence"] >= 0.70

        # 2. Obscure query failing low threshold
        miss = await query_tool(
            query="completely unrelated quantum electrodynamics",
            effort="low",
            criticality="preferred",
        )
        assert miss["status"] == "MISS:FALLBACK"
        assert miss["source"] == "none"
    finally:
        await populated_store.close()


@pytest.mark.asyncio
async def test_query_knowledge_medium_and_bookkeeper_escalation(tmp_path):
    """Test medium effort optimistic fast-path and BookKeeper escalation on miss."""
    populated_store = await create_populated_store(tmp_path)
    try:
        mock_bookkeeper = AsyncMock()
        mock_bookkeeper.lookup.return_value = {
            "status": "[HIT]",
            "path": "common/web/html/accessibility/aria_button.md",
            "title": "Button Accessibility with ARIA",
            "snippet": "Expanded rules for keyboard buttons.",
            "confidence": 0.88,
        }

        # Set threshold_medium high (0.95) to force escalation to BookKeeper
        dispatcher = KnowledgeDispatcher(
            store=populated_store,
            book_keeper=mock_bookkeeper,
            threshold_medium=0.99,
        )
        query_tool = ToolRegistry(store=populated_store, dispatcher=dispatcher).get_query_knowledge_tool()

        res = await query_tool(
            query="button keydown Enter handler",
            effort="medium",
            criticality="preferred",
        )
        assert res["status"] == "HIT"
        assert res["source"] == "book_keeper"
        mock_bookkeeper.lookup.assert_called_once()
    finally:
        await populated_store.close()


@pytest.mark.asyncio
async def test_query_knowledge_criticality_routing(tmp_path):
    """Test criticality routing: optional vs preferred vs mandatory curation."""
    populated_store = await create_populated_store(tmp_path)
    try:
        mock_curator = AsyncMock()
        mock_curator.curate.return_value = {
            "path": "common/web/html/canvas.md",
            "draft": """---
title: "HTML5 Canvas"
namespace: "common"
importance: 0.80
---
## Summary
Canvas drawing APIs.
## Detailed Rules
Use requestAnimationFrame.
""",
        }

        dispatcher = KnowledgeDispatcher(
            store=populated_store,
            curator=mock_curator,
            threshold_medium=0.99,  # Force miss
        )
        query_tool = ToolRegistry(store=populated_store, dispatcher=dispatcher).get_query_knowledge_tool()

        # 1. Optional criticality miss
        opt_miss = await query_tool(
            query="obscure helper",
            effort="medium",
            criticality="optional",
        )
        assert opt_miss["status"] == "MISS:OPTIONAL"

        # 2. Preferred criticality miss
        pref_miss = await query_tool(
            query="obscure helper",
            effort="medium",
            criticality="preferred",
        )
        assert pref_miss["status"] == "MISS:FALLBACK"

        # 3. Mandatory criticality miss triggers CuratorAgent
        mand_miss = await query_tool(
            query="HTML5 Canvas",
            effort="medium",
            criticality="mandatory",
        )
        assert mand_miss["source"] == "curator"
        mock_curator.curate.assert_called_once()
    finally:
        await populated_store.close()


@pytest.mark.asyncio
async def test_web_tools(monkeypatch):
    """Verify search_web (DDGS, Tavily, Brave) and fetch_web tools."""
    from unittest.mock import patch

    # 1. Direct fetch via doc_url
    with patch("libhippo.tools.web.fetch_web", new_callable=AsyncMock) as mock_fetch:
        mock_fetch.return_value = "Detailed React 19 documentation content."
        res_fetch = await search_web("React 19", doc_url="https://react.dev/blog/react-19")
        assert res_fetch["status"] == "fetched"
        assert res_fetch["provider"] == "direct_fetch"
        assert "React 19 documentation" in res_fetch["content"]

    # 2. DuckDuckGo search (default zero-key)
    with patch("libhippo.tools.web._search_ddgs_sync") as mock_ddgs:
        mock_ddgs.return_value = [
            {"title": "React 19 Actions", "url": "https://react.dev/actions", "snippet": "useActionState overview"}
        ]
        search_res = await search_web("React 19 actions")
        assert search_res["status"] == "searched"
        assert search_res["provider"] == "duckduckgo"
        assert search_res["url"] == "https://react.dev/actions"
        assert "useActionState overview" in search_res["content"]

    # 3. Tavily search routing when TAVILY_API_KEY is present
    monkeypatch.setenv("TAVILY_API_KEY", "tvly-test-token")
    with patch("libhippo.tools.web._search_tavily", new_callable=AsyncMock) as mock_tavily:
        mock_tavily.return_value = [
            {"title": "Tavily Doc Result", "url": "https://example.com/doc", "snippet": "API reference"}
        ]
        tavily_res = await search_web("API query")
        assert tavily_res["provider"] == "tavily"
        assert tavily_res["url"] == "https://example.com/doc"
        mock_tavily.assert_called_once()
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)

    # 4. Brave search routing when BRAVE_API_KEY is present
    monkeypatch.setenv("BRAVE_API_KEY", "brave-test-token")
    with patch("libhippo.tools.web._search_brave", new_callable=AsyncMock) as mock_brave:
        mock_brave.return_value = [
            {"title": "Brave Doc Result", "url": "https://brave.com/doc", "snippet": "Brave snippet"}
        ]
        brave_res = await search_web("Brave query")
        assert brave_res["provider"] == "brave"
        assert brave_res["url"] == "https://brave.com/doc"
        mock_brave.assert_called_once()
    monkeypatch.delenv("BRAVE_API_KEY", raising=False)

    # 5. fetch_web on invalid or unreachable host returns handled error string
    fetch_err = await fetch_web("http://unreachable.nonexistent.domain.local/doc", timeout=1.0)
    assert "[ERROR:" in fetch_err

