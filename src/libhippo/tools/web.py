"""Web scraping and documentation fetch tools for CuratorAgent."""

from __future__ import annotations

import asyncio
import os
import re
from typing import Any

import bs4
import httpx
from duckduckgo_search import DDGS


async def fetch_web(
    url: str,
    timeout: float = 10.0,
    headers: dict[str, str] | None = None,
) -> str:
    """Fetch URL and extract clean text content from HTML."""
    default_headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) LibHippoBot/1.0",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    }
    if headers:
        default_headers.update(headers)

    try:
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
            resp = await client.get(url, headers=default_headers)
            resp.raise_for_status()
            html_text = resp.text

        soup = bs4.BeautifulSoup(html_text, "html.parser")

        # Strip scripts, styles, navigations, footers
        for tag in soup(["script", "style", "nav", "footer", "header", "aside"]):
            tag.decompose()

        # Find main article or body
        main_content = soup.find("main") or soup.find("article") or soup.body or soup
        text = main_content.get_text(separator="\n", strip=True)

        # Normalize redundant blank lines
        clean_text = re.sub(r"\n{3,}", "\n\n", text)
        return clean_text[:8000]  # Cap snippet to fit context limits
    except Exception as e:  # noqa: BLE001
        return f"[ERROR: Failed to fetch {url}: {e}]"


def _search_ddgs_sync(query: str, max_results: int = 5) -> list[dict[str, Any]]:
    """Synchronous call to DuckDuckGo search."""
    with DDGS() as ddgs:
        results = list(ddgs.text(query, max_results=max_results))
        return [
            {
                "title": r.get("title", ""),
                "url": r.get("href", ""),
                "snippet": r.get("body", ""),
            }
            for r in results
        ]


async def _search_tavily(
    query: str, api_key: str, max_results: int = 5, timeout: float = 10.0
) -> list[dict[str, Any]]:
    """Query Tavily Search API."""
    url = "https://api.tavily.com/search"
    payload = {"api_key": api_key, "query": query, "max_results": max_results}
    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.post(url, json=payload)
        resp.raise_for_status()
        data = resp.json()
        results = data.get("results", [])
        return [
            {
                "title": r.get("title", ""),
                "url": r.get("url", ""),
                "snippet": r.get("content", ""),
            }
            for r in results
        ]


async def _search_brave(
    query: str, api_key: str, max_results: int = 5, timeout: float = 10.0
) -> list[dict[str, Any]]:
    """Query Brave Search API."""
    url = "https://api.search.brave.com/res/v1/web/search"
    headers = {"Accept": "application/json", "X-Subscription-Token": api_key}
    params = {"q": query, "count": max_results}
    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.get(url, headers=headers, params=params)
        resp.raise_for_status()
        data = resp.json()
        results = data.get("web", {}).get("results", [])
        return [
            {
                "title": r.get("title", ""),
                "url": r.get("url", ""),
                "snippet": r.get("description", ""),
            }
            for r in results
        ]


async def search_web(
    search_query: str,
    doc_url: str | None = None,
    max_results: int = 5,
    timeout: float = 10.0,
) -> dict[str, Any]:
    """Search for technical documentation or fetch a specific documentation URL.

    If doc_url is given, fetches that specific page.
    Otherwise, queries Tavily, Brave, or DuckDuckGo (zero-key default).
    """
    if doc_url:
        content = await fetch_web(doc_url, timeout=timeout)
        return {
            "query": search_query,
            "url": doc_url,
            "content": content,
            "status": "fetched",
            "provider": "direct_fetch",
            "results": [
                {
                    "title": search_query,
                    "url": doc_url,
                    "snippet": content[:300],
                }
            ],
        }

    provider = "duckduckgo"
    raw_results: list[dict[str, Any]] = []

    try:
        tavily_key = os.environ.get("TAVILY_API_KEY")
        brave_key = os.environ.get("BRAVE_API_KEY")

        if tavily_key:
            provider = "tavily"
            raw_results = await _search_tavily(
                search_query, tavily_key, max_results=max_results, timeout=timeout
            )
        elif brave_key:
            provider = "brave"
            raw_results = await _search_brave(
                search_query, brave_key, max_results=max_results, timeout=timeout
            )
        else:
            provider = "duckduckgo"
            raw_results = await asyncio.to_thread(
                _search_ddgs_sync, search_query, max_results=max_results
            )
    except Exception as e:  # noqa: BLE001
        return {
            "query": search_query,
            "url": None,
            "content": f"[Search failed via {provider}: {e}]",
            "status": "error",
            "provider": provider,
            "results": [],
        }

    formatted_snippets = []
    for idx, item in enumerate(raw_results, 1):
        formatted_snippets.append(
            f"[{idx}] {item['title']}\nURL: {item['url']}\nSnippet: {item['snippet']}"
        )

    content = (
        "\n\n".join(formatted_snippets)
        if formatted_snippets
        else f"No results found for '{search_query}'."
    )

    return {
        "query": search_query,
        "url": raw_results[0]["url"] if raw_results else None,
        "content": content,
        "status": "searched",
        "provider": provider,
        "results": raw_results,
    }

