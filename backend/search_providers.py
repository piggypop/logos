"""Unified search dispatcher across multiple providers.

Each backend returns the canonical shape:
    [{"title": str, "url": str, "content": str}, ...]

Selected via config key `search_provider`: 'ddg' (default) | 'brave' | 'searxng'.
All backends are fail-safe — return [] on any error.
"""
import sys

import httpx


def search(query: str, c: dict) -> list[dict]:
    if not query:
        return []
    provider = (c.get("search_provider") or "ddg").lower()
    # Clamp into a sane range regardless of what the config (or a stale frontend) sent.
    count = max(1, min(20, int(c.get("search_results_count") or 5)))
    if provider == "brave":
        return _brave(query, count, c)
    if provider == "searxng":
        return _searxng(query, count, c)
    return _ddg(query, count, c)


def _ddg(query: str, count: int, c: dict) -> list[dict]:
    try:
        from ddgs import DDGS
    except ImportError:
        try:
            from duckduckgo_search import DDGS  # legacy package name
        except ImportError:
            print("[search_providers] ddgs not installed", file=sys.stderr)
            sys.stderr.flush()
            return []
    try:
        safesearch = c.get("ddg_safesearch", "moderate")
        region = c.get("ddg_region") or "wt-wt"
        results = []
        with DDGS() as ddgs:
            for r in ddgs.text(
                query,
                max_results=count,
                safesearch=safesearch,
                region=region,
            ):
                results.append(
                    {
                        "title": r.get("title", "") or "",
                        "url": r.get("href", "") or r.get("url", "") or "",
                        "content": r.get("body", "") or r.get("description", "") or "",
                    }
                )
        return results[:count]
    except Exception as e:
        print(f"[search_providers] DDG error: {e}", file=sys.stderr)
        sys.stderr.flush()
        return []


def _brave(query: str, count: int, c: dict) -> list[dict]:
    api_key = (c.get("brave_api_key") or "").strip()
    if not api_key:
        print("[search_providers] Brave selected but no API key set", file=sys.stderr)
        sys.stderr.flush()
        return []
    try:
        r = httpx.get(
            "https://api.search.brave.com/res/v1/web/search",
            params={"q": query, "count": min(max(count, 1), 20)},
            headers={
                "X-Subscription-Token": api_key,
                "Accept": "application/json",
            },
            timeout=8.0,
        )
        r.raise_for_status()
        data = r.json()
        items = (data.get("web") or {}).get("results", []) or []
        return [
            {
                "title": it.get("title", "") or "",
                "url": it.get("url", "") or "",
                "content": it.get("description", "") or "",
            }
            for it in items[:count]
        ]
    except Exception as e:
        print(f"[search_providers] Brave error: {e}", file=sys.stderr)
        sys.stderr.flush()
        return []


def _searxng(query: str, count: int, c: dict) -> list[dict]:
    base_url = c.get("searxng_url") or "http://localhost:8081"
    try:
        r = httpx.get(
            f"{base_url}/search",
            params={"q": query, "format": "json", "categories": "general"},
            timeout=8.0,
        )
        r.raise_for_status()
        data = r.json()
        items = data.get("results", [])[:count]
        return [
            {
                "title": it.get("title", "") or "",
                "url": it.get("url", "") or "",
                "content": it.get("content", "") or "",
            }
            for it in items
        ]
    except Exception as e:
        print(f"[search_providers] SearXNG error: {e}", file=sys.stderr)
        sys.stderr.flush()
        return []


def format_as_context(results: list[dict]) -> str:
    """Canonical context formatter used for both URL fetches and search results."""
    if not results:
        return ""
    lines = ["Web search results:\n"]
    for i, r in enumerate(results, 1):
        lines.append(f"[{i}] {r.get('title', '')}")
        lines.append(f"    URL: {r.get('url', '')}")
        lines.append(f"    {r.get('content', '')}\n")
    return "\n".join(lines)
