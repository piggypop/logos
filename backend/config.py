import json
import shutil
from pathlib import Path
from typing import Any

import prompts as _prompts

CONFIG_DIR = Path.home() / ".config" / "logos"
CONFIG_PATH = CONFIG_DIR / "config.json"
_LEGACY_PATH = Path.home() / ".config" / "chat_app" / "config.json"

DEFAULTS = {
    "ollama_host": "http://localhost:11434",
    "ollama_model": "qwen3:8b",
    # Ollama context window (num_ctx). Default 8192 is enough for ~20+
    # turn conversations on 7-8B models without OOM on a 16GB machine.
    # Bump higher (e.g. 16384) if you have RAM/VRAM headroom and run
    # long sessions; lower if Ollama OOMs on smaller hardware.
    "num_ctx": 8192,
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
    # Preferred response language. Empty string = auto-detect from the user's
    # message. Set to a language name (e.g. "Greek", "English", "Spanish") to
    # always respond in that language, even when the user pastes a URL or image
    # without any text.
    "response_language": "auto",
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
    "comfyui_guidance": 3.5,      # Flux guidance scale (replaces CFG for Flux workflows)
    "comfyui_denoise": 0.75,       # img2img denoise strength (0.0–1.0)
    "comfyui_lora": "",            # LoRA filename (empty = no LoRA)
    "comfyui_lora_strength_model": 0.8,
    "comfyui_lora_strength_clip": 0.8,
    "comfyui_post_commentary": True,
    # Debug: when true, the full assembled system prompt is appended to
    # ~/.local/share/logos/debug/prompts.log on every chat turn (JSONL, rotated at ~5 MB).
    # Off by default — the log contains conversation history and memory facts.
    "debug_log_prompts": False,
    # Per-model configuration overrides. Keys: model name, values: dict with
    # override keys (e.g. {"temperature": 0.4}). Global config remains unchanged.
    "model_overrides": {},
    # ── Obsidian daily-note sync (see backend/obsidian_sync.py) ──
    # Empty vault path disables the feature. {date} in the path template
    # is rendered as YYYY-MM-DD; no other placeholders supported in v1.5.0.
    "obsidian_vault_path": "",
    "obsidian_daily_note_path": "Daily Notes/{date}.md",
    "obsidian_section_header": "## About Logos",
    # Digest format: "titles" (one bullet per chat) | "excerpts"
    # (titles + first user message, default) | "summaries" (LLM-generated
    # per chat, reserved for v1.5.1 — currently falls back to "excerpts").
    "obsidian_digest_format": "excerpts",
}


def _migrate_legacy():
    """One-time migration from chat_app paths."""
    if CONFIG_PATH.exists() or not _LEGACY_PATH.exists():
        return
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    shutil.copy2(_LEGACY_PATH, CONFIG_PATH)


# Every time MAIN_SYSTEM_PROMPT (prompts.py) changes, append the previous
# value to this list so existing users get auto-upgraded on next load().
# Custom (non-default) prompts are never touched.
_LEGACY_DEFAULT_SYSTEM_PROMPTS: list[str] = [
    # v1.1 era default (before v1.2.0 hardened prompt)
    "You are a helpful assistant. Answer in the same language the user writes in.",
    # v1.2.0 – v1.5.x default (before v1.6.0 added rules 7-9: unknown-terms,
    # no-invented-URLs, no-false-capability-denials). Users on this exact text
    # get auto-upgraded to the new hardened prompt on next load.
    """You are Logos, a precise local-LLM chat assistant.

Behavioral rules:
1. Be direct. Do not open replies with compliments or filler ("Great question",
   "Of course", "Sure!"). Start with the answer.
2. If the user's request is genuinely ambiguous (typos, missing referents,
   unclear intent), ask ONE short clarifying question before answering.
   Otherwise, answer immediately.
3. Match the user's language exactly. Match their register (casual ↔ formal).
4. Keep replies proportional to the question. A one-line question gets a
   one-line answer. Use markdown structure only when it actually helps.
5. When unsure, say "I don't know" or "I'm not sure" — do not fabricate.
6. Cite sources with [1], [2], etc. when they are provided in your context.""",
]


def load() -> dict:
    _migrate_legacy()
    if CONFIG_PATH.exists():
        with open(CONFIG_PATH) as f:
            data = json.load(f)
        # one-time field rename
        if "searxng_results_count" in data and "search_results_count" not in data:
            data["search_results_count"] = data.pop("searxng_results_count")
        # Upgrade users on known legacy defaults to the current hardened prompt.
        # Custom prompts are preserved untouched.
        if (data.get("system_prompt") or "").strip() in {
            p.strip() for p in _LEGACY_DEFAULT_SYSTEM_PROMPTS
        }:
            data["system_prompt"] = _prompts.MAIN_SYSTEM_PROMPT
        # v1.5→v1.6: auto-upgrade the legacy Obsidian section header.
        # If the user still has "## About Aya" (or its emoji variant), swap
        # it to the new default "## About Logos" so the daily-note sync
        # correctly renames existing sections.
        if data.get("obsidian_section_header", "").strip() in {
            "## About Aya",
            "## 🤖 About Aya",
        }:
            data["obsidian_section_header"] = "## About Logos"
        return {**DEFAULTS, **data}
    return DEFAULTS.copy()


def save(data: dict):
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(CONFIG_PATH, "w") as f:
        json.dump(data, f, indent=2)


def effective(c: dict, key: str, model: str) -> Any:
    """Return the per-model override for *key* if present, else the global value."""
    overrides = c.get("model_overrides", {})
    model_cfg = overrides.get(model, {})
    if key in model_cfg:
        return model_cfg[key]
    return c.get(key)
