import sys

import httpx


def search(query: str, base_url: str, results_count: int = 5) -> list[dict]:
    """
    Returns list of {"title": str, "url": str, "content": str}
    Επιστρέφει [] σε οποιοδήποτε error (με log στο stderr).
    """
    try:
        r = httpx.get(
            f"{base_url}/search",
            params={"q": query, "format": "json", "categories": "general"},
            timeout=8.0,
        )
        r.raise_for_status()
        data = r.json()
        results = data.get("results", [])[:results_count]
        return [
            {
                "title": item.get("title", ""),
                "url": item.get("url", ""),
                "content": item.get("content", ""),
            }
            for item in results
        ]
    except Exception as e:
        print(f"[searxng_client] Search error: {e}", file=sys.stderr)
        sys.stderr.flush()
        return []


def format_as_context(results: list[dict]) -> str:
    if not results:
        return ""
    lines = ["Web search results:\n"]
    for i, r in enumerate(results, 1):
        lines.append(f"[{i}] {r['title']}")
        lines.append(f"    URL: {r['url']}")
        lines.append(f"    {r['content']}\n")
    return "\n".join(lines)
