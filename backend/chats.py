import json
import re
import shutil
import uuid
from datetime import datetime
from pathlib import Path

CHATS_DIR = Path.home() / ".local" / "share" / "logos" / "chats"
_LEGACY_DIR = Path.home() / ".local" / "share" / "chat_app" / "chats"

# Accept UUID-ish ids: hex characters, dashes, length 1..80.
# Anything else (path separators, dots, slashes, control chars) is rejected.
_VALID_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,80}$")


def is_valid_id(chat_id: str) -> bool:
    return bool(chat_id) and bool(_VALID_ID_RE.fullmatch(chat_id))


def _migrate_legacy():
    if CHATS_DIR.exists() or not _LEGACY_DIR.exists():
        return
    CHATS_DIR.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(_LEGACY_DIR, CHATS_DIR)


def _ensure_dir():
    _migrate_legacy()
    CHATS_DIR.mkdir(parents=True, exist_ok=True)


def _path(chat_id: str) -> Path:
    return CHATS_DIR / f"{chat_id}.json"


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


# Keys preserved on saved sources. The heavy `content` field is intentionally
# stripped — it's re-fetched live from the relevant backend (notebook, URL,
# search provider) on demand. Saved sources are for display attribution only.
_SOURCE_KEEP_KEYS = ("title", "url", "category", "notebook_id", "source_id")


def _slim_sources(sources: list[dict] | None) -> list[dict]:
    if not sources:
        return []
    out = []
    for s in sources:
        if not isinstance(s, dict):
            continue
        out.append({k: s[k] for k in _SOURCE_KEEP_KEYS if k in s and s[k] is not None})
    return out


def _slim_messages(messages: list[dict]) -> list[dict]:
    """Return messages with assistant `sources[*].content` removed, leaving
    only display metadata. Avoids multi-MB chat files when notebooks are
    active. User attachments and assistant `image` blocks are kept intact."""
    out = []
    for m in messages or []:
        if m.get("role") == "assistant" and m.get("sources"):
            m = {**m, "sources": _slim_sources(m["sources"])}
        out.append(m)
    return out


def _auto_title(messages: list[dict], max_len: int = 50) -> str:
    for m in messages:
        if m.get("role") == "user":
            text = (m.get("content") or "").strip().split("\n")[0]
            if len(text) > max_len:
                text = text[:max_len].rstrip() + "…"
            return text or "New chat"
    return "New chat"


def list_chats() -> list[dict]:
    _ensure_dir()
    result = []
    for f in CHATS_DIR.glob("*.json"):
        try:
            with open(f) as fp:
                data = json.load(fp)
            result.append(
                {
                    "id": data["id"],
                    "title": data.get("title", "Untitled"),
                    "created_at": data.get("created_at"),
                    "updated_at": data.get("updated_at"),
                }
            )
        except Exception:
            continue
    result.sort(key=lambda c: c.get("updated_at") or "", reverse=True)
    return result


def get(chat_id: str) -> dict | None:
    if not is_valid_id(chat_id):
        return None
    _ensure_dir()
    p = _path(chat_id)
    if not p.exists():
        return None
    with open(p) as f:
        return json.load(f)


def save(chat_id: str | None, messages: list[dict], title: str | None = None) -> dict | None:
    _ensure_dir()
    now = _now()
    if chat_id and not is_valid_id(chat_id):
        return None
    chat_id = chat_id or str(uuid.uuid4())
    p = _path(chat_id)
    slim = _slim_messages(messages)
    if p.exists():
        with open(p) as f:
            data = json.load(f)
        data["messages"] = slim
        data["updated_at"] = now
        if title is not None:
            data["title"] = title
    else:
        data = {
            "id": chat_id,
            "title": title or _auto_title(slim),
            "created_at": now,
            "updated_at": now,
            "messages": slim,
        }
    with open(p, "w") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    return data


def rename(chat_id: str, title: str) -> dict | None:
    if not is_valid_id(chat_id):
        return None
    data = get(chat_id)
    if not data:
        return None
    data["title"] = title.strip() or data.get("title", "Untitled")
    data["updated_at"] = _now()
    with open(_path(chat_id), "w") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    return data


def delete(chat_id: str) -> bool:
    if not is_valid_id(chat_id):
        return False
    p = _path(chat_id)
    if p.exists():
        p.unlink()
        return True
    return False
