import re
import sys

import httpx
import trafilatura

URL_RE = re.compile(r"https?://[^\s<>\"'`]+", re.IGNORECASE)
TRAILING_PUNCT = ".,;:!?)]}'\"»"

YOUTUBE_RE = re.compile(
    r"(?:youtube\.com/(?:watch\?v=|embed/|shorts/|v/)|youtu\.be/)([A-Za-z0-9_-]{11})",
    re.IGNORECASE,
)
TRANSCRIPT_LANGS = ["el", "en"]  # preference order
USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) chat-app/0.1"
)


def extract_urls(text: str) -> list[str]:
    if not text:
        return []
    seen: set[str] = set()
    out: list[str] = []
    for raw in URL_RE.findall(text):
        url = raw.rstrip(TRAILING_PUNCT)
        if url and url not in seen:
            seen.add(url)
            out.append(url)
    return out


def _youtube_video_id(url: str) -> str | None:
    m = YOUTUBE_RE.search(url)
    return m.group(1) if m else None


def _youtube_title(video_id: str) -> str | None:
    """Fetch title via YouTube oEmbed (no API key needed)."""
    try:
        r = httpx.get(
            "https://www.youtube.com/oembed",
            params={
                "url": f"https://www.youtube.com/watch?v={video_id}",
                "format": "json",
            },
            timeout=5.0,
        )
        if r.status_code == 200:
            data = r.json()
            title = data.get("title", "").strip()
            author = data.get("author_name", "").strip()
            if title and author:
                return f"{title} — {author}"
            return title or None
    except Exception:
        pass
    return None


def _format_timestamp(seconds: float) -> str:
    t = int(seconds)
    mm, ss = divmod(t, 60)
    hh, mm = divmod(mm, 60)
    return f"{hh:d}:{mm:02d}:{ss:02d}" if hh else f"{mm:d}:{ss:02d}"


def _youtube_transcript(video_id: str, max_chars: int = 12000) -> str | None:
    try:
        from youtube_transcript_api import YouTubeTranscriptApi
    except ImportError:
        return None

    ytt = YouTubeTranscriptApi()
    fetched = None
    last_err: Exception | None = None

    try:
        fetched = ytt.fetch(video_id, languages=TRANSCRIPT_LANGS)
    except Exception as e:
        last_err = e
        try:
            tl = ytt.list(video_id)
            first = next(iter(tl))
            fetched = first.fetch()
        except Exception as e2:
            last_err = e2

    if not fetched:
        print(
            f"[url_fetcher] no transcript for {video_id}: {last_err}",
            file=sys.stderr,
        )
        sys.stderr.flush()
        return None

    lines = []
    for snip in fetched:
        text = (getattr(snip, "text", "") or "").strip().replace("\n", " ")
        if text:
            lines.append(f"[{_format_timestamp(snip.start)}] {text}")

    out = "\n".join(lines)
    if len(out) > max_chars:
        out = out[:max_chars] + "\n\n[…truncated…]"
    return out


def _fetch_youtube(video_id: str, original_url: str) -> dict | None:
    title = _youtube_title(video_id) or f"YouTube video {video_id}"
    transcript = _youtube_transcript(video_id)
    if transcript:
        content = f"YouTube video transcript (with timestamps):\n\n{transcript}"
    else:
        content = f"YouTube video: {title}\n[No transcript available for this video.]"
    return {"url": original_url, "title": title, "content": content}


def _fetch_generic(url: str, timeout: float, max_chars: int) -> dict | None:
    try:
        r = httpx.get(
            url,
            timeout=timeout,
            follow_redirects=True,
            headers={"User-Agent": USER_AGENT},
        )
        r.raise_for_status()
        html = r.text
        text = trafilatura.extract(html, include_comments=False, include_tables=True) or ""
        if not text.strip():
            return None
        title = url
        try:
            meta = trafilatura.extract_metadata(html)
            if meta and meta.title:
                title = meta.title
        except Exception:
            pass
        if len(text) > max_chars:
            text = text[:max_chars] + "\n\n[…truncated…]"
        return {"url": url, "title": title, "content": text}
    except Exception as e:
        print(f"[url_fetcher] {url}: {e}", file=sys.stderr)
        sys.stderr.flush()
        return None


def fetch(url: str, timeout: float = 10.0, max_chars: int = 8000) -> dict | None:
    """
    Route URL to specialized fetcher (YouTube) or generic webpage extractor.
    Returns {"url", "title", "content"} or None.
    """
    vid = _youtube_video_id(url)
    if vid:
        return _fetch_youtube(vid, url)
    return _fetch_generic(url, timeout, max_chars)


def fetch_many(urls: list[str]) -> list[dict]:
    results = []
    for u in urls:
        item = fetch(u)
        if item:
            results.append(item)
    return results
