import json
import shutil
from pathlib import Path

import prompts as _prompts

CONFIG_DIR = Path.home() / ".config" / "logos"
CONFIG_PATH = CONFIG_DIR / "config.json"
_LEGACY_PATH = Path.home() / ".config" / "chat_app" / "config.json"

DEFAULTS = {
    "ollama_host": "http://localhost:11434",
    "ollama_model": "qwen3:8b",
    "search_provider": "ddg",
    "search_results_count": 5,
    "searxng_url": "http://localhost:8081",
    "brave_api_key": "",
    "ddg_safesearch": "moderate",
    "ddg_region": "wt-wt",
    "auto_search_enabled": True,
    # The user-editable main system prompt. Defaults to the carefully tuned
    # behavior rules in prompts.py — see that module before changing.
    "system_prompt": _prompts.MAIN_SYSTEM_PROMPT,
    "temperature": 0.7,
    "port": 17842,
    "user_location": "",
    "open_notebook_url": "http://localhost:5055",
    "open_notebook_ui_url": "http://localhost:8502",
    "active_notebook_id": "",
    "active_notebook_name": "",
    "comfyui_url": "http://localhost:8188",
    "comfyui_workflow": "sdxl-default",
    "comfyui_custom_workflow": "",
    "comfyui_checkpoint": "",
    "comfyui_steps": 30,
    "comfyui_cfg": 7.5,
    "comfyui_sampler": "euler",
    "comfyui_scheduler": "normal",
    "comfyui_width": 1024,
    "comfyui_height": 1024,
    "comfyui_negative_prompt": "blurry, low quality, watermark, text, signature",
    "comfyui_post_commentary": True,
    # Debug: when true, the full assembled system prompt is appended to
    # ~/.local/share/logos/debug/prompts.log on every chat turn (JSONL, rotated at ~5 MB).
    # Off by default — the log contains conversation history and memory facts.
    "debug_log_prompts": False,
}


def _migrate_legacy():
    """One-time migration from chat_app paths."""
    if CONFIG_PATH.exists() or not _LEGACY_PATH.exists():
        return
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    shutil.copy2(_LEGACY_PATH, CONFIG_PATH)


_OLD_DEFAULT_SYSTEM_PROMPT = (
    "You are a helpful assistant. Answer in the same language the user writes in."
)


def load() -> dict:
    _migrate_legacy()
    if CONFIG_PATH.exists():
        with open(CONFIG_PATH) as f:
            data = json.load(f)
        # one-time field rename
        if "searxng_results_count" in data and "search_results_count" not in data:
            data["search_results_count"] = data.pop("searxng_results_count")
        # Upgrade users on the well-known old default to the new hardened prompt.
        # Custom prompts are preserved untouched.
        if (data.get("system_prompt") or "").strip() == _OLD_DEFAULT_SYSTEM_PROMPT:
            data["system_prompt"] = _prompts.MAIN_SYSTEM_PROMPT
        return {**DEFAULTS, **data}
    return DEFAULTS.copy()


def save(data: dict):
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(CONFIG_PATH, "w") as f:
        json.dump(data, f, indent=2)
