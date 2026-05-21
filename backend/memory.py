import json
import re
import shutil
import threading
import uuid
from datetime import datetime
from pathlib import Path

MEMORY_PATH = Path.home() / ".local" / "share" / "logos" / "memory.json"
_LEGACY_PATH = Path.home() / ".local" / "share" / "chat_app" / "memory.json"
_lock = threading.Lock()


def _migrate_legacy():
    if MEMORY_PATH.exists() or not _LEGACY_PATH.exists():
        return
    MEMORY_PATH.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(_LEGACY_PATH, MEMORY_PATH)


REMEMBER_PATTERNS = [
    re.compile(
        r"^\s*(?:να\s+(?:το\s+)?θυμάσαι|θυμήσου|να\s+θυμάμαι|θυμάσαι\s+ότι)"
        r"\s*[:\-,]?\s*(.+)",
        re.IGNORECASE | re.DOTALL,
    ),
    re.compile(r"^\s*/remember\s+(.+)", re.IGNORECASE | re.DOTALL),
    re.compile(r"^\s*remember\s*[:\-,]\s*(.+)", re.IGNORECASE | re.DOTALL),
]

LEADING_PARTICLES = re.compile(
    r"^\s*(?:ότι|πως|πάντα|σχετικά\s+με|γενικά|that)\s+",
    re.IGNORECASE,
)


def _ensure():
    _migrate_legacy()
    MEMORY_PATH.parent.mkdir(parents=True, exist_ok=True)
    if not MEMORY_PATH.exists():
        with open(MEMORY_PATH, "w") as f:
            json.dump({"facts": []}, f)


def load() -> list[dict]:
    _ensure()
    with _lock:
        with open(MEMORY_PATH) as f:
            data = json.load(f)
    return data.get("facts", [])


def _save_all(facts: list[dict]):
    _ensure()
    with _lock:
        with open(MEMORY_PATH, "w") as f:
            json.dump({"facts": facts}, f, indent=2, ensure_ascii=False)


def add(text: str, source: str = "auto") -> bool:
    """Add a fact if not a duplicate. Returns True if added."""
    text = (text or "").strip().rstrip(".")
    if not text or len(text) > 500:
        return False
    facts = load()
    lower = text.lower()
    for f in facts:
        if f["text"].lower().rstrip(".") == lower:
            return False
    facts.append(
        {
            "id": str(uuid.uuid4())[:8],
            "text": text,
            "source": source,
            "created_at": datetime.now().isoformat(timespec="seconds"),
        }
    )
    _save_all(facts)
    return True


def remove(fact_id: str) -> bool:
    facts = load()
    new = [f for f in facts if f.get("id") != fact_id]
    if len(new) == len(facts):
        return False
    _save_all(new)
    return True


def detect_remember(text: str) -> str | None:
    """Detect manual 'remember X' triggers in user message."""
    if not text:
        return None
    for pat in REMEMBER_PATTERNS:
        m = pat.match(text.strip())
        if m:
            fact = m.group(1).strip()
            # Strip leading filler particles ("that", "ότι", "πως", "πάντα", ...) iteratively
            while True:
                stripped = LEADING_PARTICLES.sub("", fact, count=1)
                if stripped == fact:
                    break
                fact = stripped
            return fact.strip() or None
    return None


# Prompt formatting lives in prompts.py (memory_block). This module now only
# handles storage and detection.
