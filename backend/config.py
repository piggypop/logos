import json
import shutil
from pathlib import Path

CONFIG_DIR = Path.home() / ".config" / "logos"
CONFIG_PATH = CONFIG_DIR / "config.json"
_LEGACY_PATH = Path.home() / ".config" / "chat_app" / "config.json"

DEFAULTS = {
    "ollama_host": "http://localhost:11434",
    "ollama_model": "qwen3:8b",
    "searxng_url": "http://localhost:8081",
    "searxng_results_count": 5,
    "auto_search_enabled": True,
    "system_prompt": "You are a helpful assistant. Answer in the same language the user writes in.",
    "search_system_prompt": (
        "You have REAL-TIME web search results below. "
        "You MUST use them to answer the user's question. "
        "NEVER say you cannot access the internet or don't have real-time data — you DO have fresh search results right now. "
        "Base your answer primarily on the search results, not on your training data. "
        "Cite sources with [1], [2] etc. for each piece of information you use. "
        "Answer in the same language the user writes in."
    ),
    "temperature": 0.7,
    "port": 17842,
    "user_location": "",
}


def _migrate_legacy():
    """One-time migration from chat_app paths."""
    if CONFIG_PATH.exists() or not _LEGACY_PATH.exists():
        return
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    shutil.copy2(_LEGACY_PATH, CONFIG_PATH)


def load() -> dict:
    _migrate_legacy()
    if CONFIG_PATH.exists():
        with open(CONFIG_PATH) as f:
            data = json.load(f)
        return {**DEFAULTS, **data}
    return DEFAULTS.copy()


def save(data: dict):
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(CONFIG_PATH, "w") as f:
        json.dump(data, f, indent=2)
