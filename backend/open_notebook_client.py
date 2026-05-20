"""Client for Open Notebook (lfnovo/open-notebook) REST API.

Reachable at <base_url> (default http://localhost:5055).
We use three endpoints:
  GET  /api/notebooks            → list notebooks
  GET  /api/notebooks/{id}       → notebook metadata + source list (ids/titles only)
  GET  /api/sources/{source_id}  → full source content (`full_text`)

For Logos integration we fetch ALL sources' full_text of the active notebook and
inject as additional grounding context per turn. Caching is in-memory by
(base_url, notebook_id) with a short TTL to avoid re-fetching identical content.
"""
import sys
import time

import httpx

_CACHE_TTL = 60.0  # seconds
_cache: dict[tuple[str, str], tuple[float, dict]] = {}


def _normalize(base_url: str) -> str:
    return (base_url or "").rstrip("/")


def ping(base_url: str, timeout: float = 3.0) -> bool:
    """Quick reachability check."""
    if not base_url:
        return False
    try:
        r = httpx.get(f"{_normalize(base_url)}/", timeout=timeout)
        return r.status_code == 200
    except Exception:
        return False


def list_notebooks(base_url: str, timeout: float = 5.0) -> list[dict]:
    """List notebooks. Returns [] on error."""
    if not base_url:
        return []
    try:
        r = httpx.get(f"{_normalize(base_url)}/api/notebooks", timeout=timeout)
        r.raise_for_status()
        data = r.json()
        if isinstance(data, list):
            return data
        return data.get("notebooks", []) or []
    except Exception as e:
        print(f"[open_notebook_client] list_notebooks error: {e}", file=sys.stderr)
        sys.stderr.flush()
        return []


def _fetch_source_full(base_url: str, source_id: str, timeout: float = 5.0) -> dict | None:
    try:
        r = httpx.get(
            f"{_normalize(base_url)}/api/sources/{source_id}", timeout=timeout
        )
        r.raise_for_status()
        return r.json()
    except Exception as e:
        print(
            f"[open_notebook_client] fetch source {source_id} error: {e}",
            file=sys.stderr,
        )
        sys.stderr.flush()
        return None


def get_notebook_with_content(
    base_url: str, notebook_id: str, max_chars_per_source: int = 50_000
) -> dict | None:
    """
    Returns {id, name, sources: [{id, title, url, content}], total_chars}
    or None on error. Caches result for _CACHE_TTL seconds.
    """
    if not base_url or not notebook_id:
        return None

    key = (_normalize(base_url), notebook_id)
    now = time.time()
    cached = _cache.get(key)
    if cached and now - cached[0] < _CACHE_TTL:
        return cached[1]

    try:
        meta = httpx.get(
            f"{_normalize(base_url)}/api/notebooks/{notebook_id}", timeout=5.0
        )
        meta.raise_for_status()
        notebook = meta.json()

        # The /api/notebooks/{id}/context endpoint gives the source list cheaply
        ctx_resp = httpx.post(
            f"{_normalize(base_url)}/api/notebooks/{notebook_id}/context",
            json={"notebook_id": notebook_id},
            timeout=5.0,
        )
        ctx_resp.raise_for_status()
        ctx = ctx_resp.json()
        source_refs = ctx.get("sources", []) or []

        sources_full = []
        total_chars = 0
        for ref in source_refs:
            sid = ref.get("id")
            if not sid:
                continue
            src = _fetch_source_full(_normalize(base_url), sid)
            if not src:
                continue
            full_text = (src.get("full_text") or "").strip()
            if not full_text:
                continue
            if len(full_text) > max_chars_per_source:
                full_text = full_text[:max_chars_per_source] + "\n\n[…truncated…]"
            asset = src.get("asset") or {}
            url = asset.get("url") or ""
            title = src.get("title") or sid
            sources_full.append(
                {
                    "id": sid,
                    "title": title,
                    "url": url,
                    "content": full_text,
                }
            )
            total_chars += len(full_text)

        result = {
            "id": notebook.get("id", notebook_id),
            "name": notebook.get("name", ""),
            "sources": sources_full,
            "total_chars": total_chars,
            "total_tokens_est": total_chars // 4,
        }
        _cache[key] = (now, result)
        return result
    except Exception as e:
        print(
            f"[open_notebook_client] get_notebook_with_content error: {e}",
            file=sys.stderr,
        )
        sys.stderr.flush()
        return None


def invalidate_cache(base_url: str = "", notebook_id: str = ""):
    """Clear cache. If args provided, clear only that key; otherwise clear all."""
    global _cache
    if not base_url and not notebook_id:
        _cache = {}
        return
    key = (_normalize(base_url), notebook_id)
    _cache.pop(key, None)


def as_chat_sources(notebook: dict, ui_base_url: str = "") -> list[dict]:
    """
    Convert a fetched notebook into the standard sources format used by the
    chat SSE flow ([{title, url, content, ...}]). `category` is set so the
    frontend can visually distinguish them.
    """
    if not notebook or not notebook.get("sources"):
        return []
    nb_name = notebook.get("name") or "Notebook"
    fallback_link = ui_base_url.rstrip("/") if ui_base_url else ""
    out = []
    for s in notebook["sources"]:
        url = s.get("url") or fallback_link or ""
        out.append(
            {
                "title": f"📒 {nb_name} · {s.get('title', '')}",
                "url": url,
                "content": s.get("content", ""),
                "category": "notebook",
                "notebook_id": notebook.get("id"),
                "source_id": s.get("id"),
            }
        )
    return out
