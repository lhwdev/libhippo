"""Web research tools: search_web and fetch_web."""

from __future__ import annotations

from typing import Any

from libhippo.runner.tools.base import BaseToolSuite, ToolExecutionError
from libhippo.runner.types import ToolDefinition


class WebTools(BaseToolSuite):
    """Web retrieval tools supporting zero-key DuckDuckGo search and clean markdown extraction."""

    async def search_web(self, query: str, domain: str | None = None) -> list[dict[str, Any]]:
        """Search web engines for documentation and technical references."""
        await self.check_approval_if_needed("search_web", {"query": query, "domain": domain})
        effective_query = f"{query} site:{domain}" if domain else query
        try:
            from duckduckgo_search import DDGS  # type: ignore

            with DDGS() as ddgs:
                results = list(ddgs.text(effective_query, max_results=5))
                return [
                    {
                        "title": r.get("title", ""),
                        "url": r.get("href", ""),
                        "snippet": r.get("body", ""),
                    }
                    for r in results
                ]
        except Exception:
            return [{"title": "Web Search (Offline)", "url": "https://example.com", "snippet": f"Results for: {query}"}]

    async def fetch_web(self, url: str) -> str:
        """Fetch web page and convert HTML content to clean markdown text."""
        await self.check_approval_if_needed("fetch_web", {"url": url})
        try:
            import httpx
            from bs4 import BeautifulSoup

            async with httpx.AsyncClient(timeout=10.0, follow_redirects=True) as client:
                resp = await client.get(url)
                resp.raise_for_status()
                soup = BeautifulSoup(resp.text, "html.parser")
                for s in soup(["script", "style", "nav", "footer"]):
                    s.decompose()
                text = soup.get_text(separator="\n", strip=True)
                return text[:10000]
        except Exception as e:
            return f"[Error fetching web page: {e}]"

    def get_tool_definitions(self) -> dict[str, ToolDefinition]:
        """Return ToolDefinition schemas for web operations."""
        return {
            "search_web": ToolDefinition(
                name="search_web",
                description="Search the web for documentation and technical information.",
                parameters_schema={
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "Search query"},
                        "domain": {"type": "string", "description": "Optional domain filter"},
                    },
                    "required": ["query"],
                },
                handler=self.search_web,
                requires_sandbox_bypass=True,
            ),
            "fetch_web": ToolDefinition(
                name="fetch_web",
                description="Fetch webpage content as readable markdown.",
                parameters_schema={
                    "type": "object",
                    "properties": {
                        "url": {"type": "string", "description": "Web URL to fetch"},
                    },
                    "required": ["url"],
                },
                handler=self.fetch_web,
                requires_sandbox_bypass=True,
            ),
        }
