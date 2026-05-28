"""Unified search dispatcher across multiple providers.

Each backend returns the canonical shape:
    [{"title": str, "url": str, "content": str}, ...]

Selected via config key `search_provider`: 'ddg' (default) | 'brave' | 'searxng'.
All backends are fail-safe — return [] on any error.
"""

import re
import sys

import httpx

# Characters from scripts that should never appear in LLM-facing snippet content.
# Brave (and other engines) occasionally return results from Cyrillic/Arabic/CJK
# pages; those characters can bleed into model output even when LANGUAGE_RULE
# forbids it.  Strip them at the data layer so they never reach the LLM (M13).
_FOREIGN_SCRIPT_RE = re.compile(
    r"["
    r"Ѐ-ԯ"  # Cyrillic + Cyrillic Supplement
    r"֐-׿"  # Hebrew
    r"؀-ۿ"  # Arabic
    r"܀-ݏ"  # Syriac
    r"ऀ-ॿ"  # Devanagari
    r"฀-๿"  # Thai
    r"　-〿"  # CJK Symbols and Punctuation
    r"぀-ヿ"  # Hiragana + Katakana
    r"一-鿿"  # CJK Unified Ideographs
    r"가-힯"  # Hangul Syllables
    r"]+",
    re.UNICODE,
)


def _clean_snippet(text: str) -> str:
    """Strip foreign-script characters from a search result snippet (M13).

    Cyrillic/Arabic/CJK characters in provider results can bleed into model
    output even when LANGUAGE_RULE instructs otherwise.  Removing them at the
    data layer is more reliable than relying solely on the prompt.
    Title and URL fields are intentionally left untouched — only the content
    field (which is injected verbatim into the LLM context) is cleaned.
    """
    if not text:
        return text
    cleaned = _FOREIGN_SCRIPT_RE.sub(" ", text)
    return re.sub(r" {2,}", " ", cleaned).strip()


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
    """Canonical context formatter used for both URL fetches and search results.

    Sources with content shorter than THIN_THRESHOLD characters are tagged
    as thin so the model knows they are snippets, not full articles.
    """
    THIN_THRESHOLD = 300  # chars; below this = snippet, not article body

    if not results:
        return ""
    lines = ["Web search results:\n"]
    for i, r in enumerate(results, 1):
        content = _clean_snippet(r.get("content", "") or "")
        is_thin = len(content) < THIN_THRESHOLD
        tag = " [THIN — snippet only, no article body]" if is_thin else ""
        lines.append(f"[{i}] {r.get('title', '')}{tag}")
        lines.append(f"    URL: {r.get('url', '')}")
        lines.append(f"    {content}\n")
    return "\n".join(lines)


def source_quality_summary(results: list[dict]) -> str:
    """Return a pre-source quality block, or empty string if all sources are substantial.

    Tells the model how many sources are thin vs substantial BEFORE it reads them.
    """
    THIN_THRESHOLD = 300
    if not results:
        return ""

    thin = sum(1 for r in results if len(r.get("content", "")) < THIN_THRESHOLD)
    total = len(results)
    substantial = total - thin

    if thin == 0:
        return ""  # All good, no warning needed

    return (
        f"## SOURCE QUALITY\n"
        f"{thin} of {total} sources below are THIN (headlines/snippets only, "
        f"no full article body). {substantial} have substantial content. "
        f"Thin sources may not contain enough detail to answer the question. "
        f"Do NOT fabricate facts that are not explicitly in the sources."
    )
