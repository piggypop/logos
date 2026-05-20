import json
import shutil
import uuid
from datetime import datetime
from pathlib import Path

CHATS_DIR = Path.home() / ".local" / "share" / "logos" / "chats"
_LEGACY_DIR = Path.home() / ".local" / "share" / "chat_app" / "chats"


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
    _ensure_dir()
    p = _path(chat_id)
    if not p.exists():
        return None
    with open(p) as f:
        return json.load(f)


def save(chat_id: str | None, messages: list[dict], title: str | None = None) -> dict:
    _ensure_dir()
    now = _now()
    chat_id = chat_id or str(uuid.uuid4())
    p = _path(chat_id)
    if p.exists():
        with open(p) as f:
            data = json.load(f)
        data["messages"] = messages
        data["updated_at"] = now
        if title is not None:
            data["title"] = title
    else:
        data = {
            "id": chat_id,
            "title": title or _auto_title(messages),
            "created_at": now,
            "updated_at": now,
            "messages": messages,
        }
    with open(p, "w") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    return data


def rename(chat_id: str, title: str) -> dict | None:
    data = get(chat_id)
    if not data:
        return None
    data["title"] = title.strip() or data.get("title", "Untitled")
    data["updated_at"] = _now()
    with open(_path(chat_id), "w") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    return data


def delete(chat_id: str) -> bool:
    p = _path(chat_id)
    if p.exists():
        p.unlink()
        return True
    return False
