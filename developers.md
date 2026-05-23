# Logos — Developer & LLM Reference

> Complete architecture and API reference for anyone (human or LLM) modifying, fixing, or extending Logos. Version 1.5.

This document is the **single source of truth**. If you change something material, update this file in the same commit.

---

## 1. High-level architecture

```
┌────────────────────────────────────────────────────────────┐
│              pywebview window (GTK + WebKit2)              │
│                                                            │
│  ┌──────────────────────────────────────────────────────┐  │
│  │   Vanilla HTML/JS frontend (frontend/)                │  │
│  │   index.html, style.css, app.js                       │  │
│  │   - fetch() against /api/...                          │  │
│  │   - SSE streaming for /api/chat and /api/comfyui/gen  │  │
│  │   - window.pywebview.api.* for native dialogs         │  │
│  └────────────────┬────────────────────────┬────────────┘  │
│                   │ HTTP localhost         │ JS bridge      │
│  ┌────────────────▼────────────────┐       │                │
│  │  Flask app (backend/server.py)  │       │                │
│  │  - serves frontend/ at /        │       │                │
│  │  - /api/* endpoints             │       │                │
│  │  - SSE chat + image streams     │       │                │
│  └──┬──────┬──────┬──────┬──────┬──┘       │                │
│     │      │      │      │      │          │                │
│     ▼      ▼      ▼      ▼      ▼          ▼                │
│  Ollama  Search  Pages  Open    ComfyUI    app.py Api       │
│  :11434  (DDG /  /YT    Notebook (REST +   pick_files()     │
│          Brave /        :5055    WS) :8188 export_chat()    │
│          SearXNG)                                           │
└────────────────────────────────────────────────────────────┘
```

- **One process.** Flask runs in a daemon thread; pywebview takes the main thread.
- **One language for business logic** (Python). The frontend is dumb — it sends HTTP and renders SSE.
- **Local-only network.** Flask binds to `127.0.0.1`, default port `17842`. Outbound calls go to: Ollama, the selected search provider, user-pasted URLs, YouTube oembed (only when YouTube URL is pasted), Open Notebook, ComfyUI.
- **Startup**: a splash HTML is shown immediately; the actual URL is only loaded once Flask responds 200 to `GET /` (with a 15s deadline). Avoids the "blank white window" race.

---

## 2. File / directory layout

```
logos/
├── app.py                  # entry point: starts Flask thread + pywebview window
├── icon.svg                # source icon (Λ glyph on dark)
├── app-icon.png            # 512×512 PNG (used by .desktop in dev)
├── icons/                  # rendered sizes for deb install
│   ├── logos-16.png .. logos-512.png
├── logos.desktop           # XDG entry installed by deb
├── build_deb.sh            # produces logos_<version>_all.deb
├── README.md               # user-facing
├── developers.md           # this file
├── LICENSE                 # MIT
├── backend/
│   ├── version.py          # VERSION, APP_NAME
│   ├── server.py           # Flask app, ALL /api endpoints, SSE streams
│   ├── config.py           # JSON config persistence + legacy migration
│   ├── chats.py            # chat archive (one JSON per chat) + id validation
│   ├── memory.py           # persistent user facts + manual-trigger detection
│   ├── ollama_client.py    # ollama wrapper: stream_chat, list_models, get_capabilities
│   ├── prompts.py          # SINGLE source for every string sent to an LLM
│   ├── tool_router.py      # LLM-driven: needs_search, reformulate_query, extract_facts
│   ├── search_providers.py # unified dispatch: DDG, Brave, SearXNG
│   ├── url_fetcher.py      # extract URLs, generic webpage fetch, YouTube transcript
│   ├── file_extractor.py   # categorize + extract text/PDF/DOCX/image; ollama-msg builder
│   ├── open_notebook_client.py  # REST client for Open Notebook
│   ├── comfyui_client.py   # ComfyUI REST + WebSocket client
│   ├── image_workflows.py  # built-in templates + placeholder substitution
│   ├── image_storage.py    # disk storage for generated images
│   ├── notes.py            # SQLite + FTS5 store for "Take note" feature
│   ├── obsidian_sync.py    # Obsidian Daily-Note digest writer (no new deps)
│   └── requirements.txt
└── frontend/
    ├── index.html          # static markup (header, chat area, sidebar, settings modal, image modal)
    ├── style.css           # dark/terminal theme
    └── app.js              # all client logic (state, fetch, SSE parse, UI render)
```

---

## 3. Data storage

| File / dir | Owner module | Schema |
|---|---|---|
| `~/.config/logos/config.json` | `config.py` | flat dict (see `DEFAULTS`) |
| `~/.local/share/logos/chats/<uuid>.json` | `chats.py` | `{id, title, created_at, updated_at, messages[]}` |
| `~/.local/share/logos/memory.json` | `memory.py` | `{facts: [{id, text, source, created_at}]}` |
| `~/.local/share/logos/images/<chat_id>/<ts>_<seed>.<ext>` | `image_storage.py` | binary image files |
| `~/.local/share/logos/notes.db` | `notes.py` | SQLite (notes table + notes_fts FTS5 virtual table) |
| `~/.local/share/logos/obsidian_last_sync.txt` | `app.py` (auto-sync) | one line: ISO date of last successful auto-sync |
| `<vault>/Daily Notes/YYYY-MM-DD.md` | `obsidian_sync.py` | user's Obsidian daily note; we only touch one `##` section |

All four modules implement **one-time auto-migration** from the legacy `chat_app` paths where applicable.

### Chat ID safety

`chats.is_valid_id(s)` requires `[A-Za-z0-9_-]{1,80}`. Used in every endpoint that touches a chat file to prevent path traversal. Frontend generates UUIDs via `crypto.randomUUID()`.

### Message schema (inside a chat JSON)

```json
{
  "role": "user" | "assistant",
  "content": "string",

  // user only, optional — see file_extractor.py
  "attachments": [
    {
      "filename": "report.pdf",
      "type": "pdf",
      "category": "text" | "image" | "audio" | "video",
      "size": 12345,
      "mime": "application/pdf",
      "content": "extracted text...",   // text path
      "data_base64": "...",             // image/audio/video path
      "truncated": false,               // text path only
      "error": "..."                    // present iff extraction failed
    }
  ],

  // assistant only, optional — content is intentionally stripped before save
  // (re-fetched live on demand); only display metadata is persisted
  "sources": [
    {"title": "...", "url": "...", "category": "notebook"?, "notebook_id"?: "...", "source_id"?: "..."}
  ],
  "image": {                            // assistant only, when produced by ComfyUI
    "path": "/abs/path/.png",
    "url": "/api/images/<rel>",
    "filename": "20260520_153012_42.png",
    "prompt": "user prompt",
    "params": {
      "seed": 42, "steps": 30, "width": 1024, "height": 1024,
      "checkpoint": "sd_xl_base_1.0.safetensors",
      "sampler": "euler", "cfg": 7.5,
      "workflow": "sdxl-default"
    }
  }
}
```

`messages` always alternates user/assistant. First message is user (except image-only chats where assistant is first).

---

## 4. HTTP API contract

All endpoints JSON in / JSON out unless noted. Base: `http://127.0.0.1:<port>`.

### Config / models / version

| Method | Path | Body | Response |
|---|---|---|---|
| GET | `/api/config` | — | full config dict (with defaults merged) |
| POST | `/api/config` | partial dict | `{ok: true}` (merged + saved) |
| GET | `/api/models` | — | `{models: [string]}` (from Ollama) |
| GET | `/api/capabilities` | — | `{model, capabilities: [string], supports_images, supports_audio, accept: [string]}` |
| GET | `/api/version` | — | `{app: "Logos", version: "1.2.0"}` |

### Search (direct invocation; bypasses LLM routing)

| Method | Path | Body | Response |
|---|---|---|---|
| POST | `/api/search` | `{query}` | `{results: [{title, url, content}], provider}` |

### Chats archive

| Method | Path | Body | Response |
|---|---|---|---|
| GET | `/api/chats` | — | `{chats: [{id, title, created_at, updated_at}]}` (sorted desc) |
| GET | `/api/chats/<id>` | — | full chat |
| PUT | `/api/chats/<id>` | `{messages, title?}` | upsert — saved chat dict, or 400 on invalid id |
| POST | `/api/chats/<id>/rename` | `{title}` | renamed chat dict |
| DELETE | `/api/chats/<id>` | — | `{ok: true}` — also cleans up the chat's images folder |

### Memory

| Method | Path | Body | Response |
|---|---|---|---|
| GET | `/api/memory` | — | `{facts: [{id, text, source, created_at}]}` |
| POST | `/api/memory` | `{text}` | `{ok, added: bool}` |
| DELETE | `/api/memory/<id>` | — | `{ok: true}` |

### Obsidian sync

| Method | Path | Body | Response |
|---|---|---|---|
| POST | `/api/obsidian/sync` | `{date?: "YYYY-MM-DD", dry_run?: bool}` | Result dict (see below). Defaults to yesterday. |
| GET | `/api/obsidian/preview?date=YYYY-MM-DD` | — | Same as POST with `dry_run=true`. |

Result dict shape:

```json
{
  "ok": true,
  "target_date": "2026-05-22",          // the chat day we summarised
  "daily_note_date": "2026-05-23",      // the daily note we wrote to
  "daily_note_path": "/abs/.../2026-05-23.md",
  "chats_count": 3,
  "bytes_written": 412,
  "renamed_legacy_header": false,       // true if "## About Aya" was renamed
  "skipped_reason": null,               // "config_incomplete" | "vault_missing" | "empty" | null
  "error": null,
  "dry_run": false,
  "preview": null                       // populated only when dry_run=true
}
```

`ok: true` does NOT mean a write happened — check `skipped_reason`. A missing vault path is a state, not an error.

### Open Notebook

| Method | Path | Body | Response |
|---|---|---|---|
| GET | `/api/notebooks` | — | `{ok, notebooks: [{id, name, source_count, note_count, ...}], error?}` |
| GET | `/api/notebooks/<id>/preview` | — | `{ok, id, name, source_count, total_chars, total_tokens_est}` |
| POST | `/api/notebooks/refresh` | — | `{ok: true}` — invalidates client cache |

### ComfyUI

| Method | Path | Body | Response |
|---|---|---|---|
| GET | `/api/comfyui/status[?refresh=1]` | — | `{ok, discovered, checkpoints[], samplers[], schedulers[], error?}` |
| POST | `/api/comfyui/generate` | `{prompt, chat_id?, overrides?}` | SSE stream (see below) |
| GET | `/api/images/<path>` | — | binary image file (path-traversal protected) |

### Chat stream

`POST /api/chat`

Request:
```json
{
  "messages": [{"role":"user","content":"...","attachments":[...]}, ...],
  "force_search": false
}
```

Response: `text/event-stream`. Each event is one JSON object after `data: `, terminated by `\n\n`.

| `type` | Fields | Meaning |
|---|---|---|
| `remembered` | `fact` | Manual remember trigger fired; fact saved to memory |
| `loading_notebook` | `notebook_id` | About to fetch active notebook content |
| `fetching_urls` | `urls: [string]` | About to fetch user-provided URLs |
| `searching` | — | Reformulated query is about to hit the search provider |
| `sources` | `query?, sources: [{title, url, content, category?}]` | All sources used as context (notebook + URLs + search) |
| `token` | `content` | Single Ollama token (chunked text) |
| `done` | — | Stream complete |
| `error` | `message` | Fatal error inside the stream |

Order guarantees per request:
- `remembered` fires first if matched
- `loading_notebook` (if active notebook set)
- `fetching_urls` (if URLs found)
- `searching` (if web search runs)
- `sources` (if any sources at all)
- `token` × N
- `done` OR `error`

Token text may include partial multi-byte UTF-8 characters — the **client buffers** with `TextDecoder({stream:true})`. Each SSE message also may not be a complete line — the **client buffers** incomplete `data:` lines across `read()` calls.

### Image generation stream

`POST /api/comfyui/generate`

Request:
```json
{
  "prompt": "a fluffy dog in space",
  "chat_id": "abc-123" | null,
  "overrides": {
    "negative": "...", "checkpoint": "...", "seed": 42,
    "steps": 30, "cfg": 7.5, "sampler": "euler", "scheduler": "normal",
    "width": 1024, "height": 1024
  }
}
```

SSE events:

| `type` | Fields | Meaning |
|---|---|---|
| `queued` | `prompt_id` | ComfyUI accepted the workflow |
| `progress` | `value`, `max`, `node` | Per-step progress (KSampler etc.) |
| `image` | `path`, `filename`, `prompt`, `params` | Image saved; absolute disk path |
| `error` | `message` | ComfyUI rejected the workflow or execution failed |

---

## 5. pywebview JS bridge

`app.py` defines an `Api` class. Each public method becomes `window.pywebview.api.<method>(...)` in the frontend, returning a Promise.

| Method | Returns | Used for |
|---|---|---|
| `pick_files()` | `[{filename, category, size, content?/data_base64?, error?}, ...]` | Native multi-select dialog. Filters by current model capabilities. |
| `pick_folder()` | `{ok, path?, cancelled?}` | Native folder picker. Used by Settings → Obsidian → vault path. |
| `export_chat(chat_id)` | `{ok, path?, cancelled?, error?}` | Native save dialog → writes chat JSON |
| `quit_app()` | (no return) | Fully exit the process (close-button only minimises). Wired to Settings → Quit Logos. |

The bridge runs methods on a pywebview-managed thread. Heavy I/O (PDF parse, large image read, native dialog blocking) happens here, not in Flask.

---

## 6. Request lifecycle (chat with everything enabled)

User sends a message containing a URL and an attached PDF, with auto-search on, an active notebook, and memory facts known.

1. **Frontend** `streamResponse(forceSearch=false)` POSTs `/api/chat` with `messages` (incl. `attachments`).
2. **Server** `chat()` generator:
   1. Detect manual remember trigger on last user message → if matched, `memory.add()` and emit `remembered` event.
   2. Build `sys_ctx` from `_system_context()`: date/time + location + memory facts.
   3. If `active_notebook_id` set: emit `loading_notebook`, call `open_notebook_client.get_notebook_with_content()`, convert via `as_chat_sources()` → `notebook_sources`.
   4. Extract URLs from last user message. If any: emit `fetching_urls`, call `url_fetcher.fetch_many()` → `url_contents`.
   5. URL fetcher routes per-URL: YouTube → `_fetch_youtube` (oembed title + `youtube-transcript-api`); generic → `_fetch_generic` (`httpx` + `trafilatura.extract`).
   6. Decide web search: `force_search` always wins; else `auto_search_enabled and not url_contents and not notebook_sources` → call `tool_router.needs_search()`.
   7. If searching: emit `searching`, call `tool_router.reformulate_query()` then `search_providers.search()` (dispatches to DDG / Brave / SearXNG per config).
   8. Combine `notebook_sources + url_contents + search_results` → emit `sources`.
   9. Build final system prompt: `sys_ctx + "\n\n" + (search_system_prompt + context_str if any else system_prompt)`.
   10. Transform messages: `file_extractor.build_ollama_messages()` inlines text attachments into content and pushes image attachments into Ollama's `images` field.
   11. Stream from `ollama_client.stream_chat()`. Yield `token` events; buffer all tokens.
   12. Yield `done`.
   13. Spawn background thread for `_extract_facts_bg()` → `tool_router.extract_facts()` (LLM call) → `memory.add()` for each new fact.
3. **Frontend** parses SSE, updates DOM, attaches sources + action buttons, calls `saveCurrentChat()` (PUT to `/api/chats/<uuid>`).

---

## 7. Module deep dives

### `version.py`
Two constants: `VERSION` (semver string), `APP_NAME`. Exposed via `/api/version`.

### `config.py`
Flat dict, JSON on disk. `load()` merges saved data over `DEFAULTS`, runs `_migrate_legacy()` once, and performs a one-time rename of the legacy field `searxng_results_count → search_results_count`.

### `chats.py`
One JSON per chat. UUID-keyed. `is_valid_id()` gates every file op. `list_chats()` returns metadata only (no messages), sorted by `updated_at` desc. `save()` is upsert — keeps `created_at` if file exists, always updates `updated_at`. Returns `None` on invalid id.

**Source slimming on save**: `_slim_messages()` strips `sources[*].content` before persisting. Saved sources keep only `title, url, category, notebook_id, source_id` for display attribution. This keeps chat files small (a 10-source notebook turn drops from ~500 KB to ~2 KB). Content is re-fetched live from the relevant backend (notebook / URL / search) on the next turn.

### `memory.py`
Single JSON. `detect_remember(text)` matches English (`/remember`, `remember:`) and Greek (`να θυμάσαι`, `θυμήσου`, `να θυμάμαι`) triggers, then strips leading filler particles (`that`, `ότι`, `πως`, `πάντα`). `add()` does case-insensitive dedupe. Storage only — prompt formatting lives in `prompts.memory_block()`.

### `prompts.py`
**Single source of truth for every string sent to an LLM**. Defines:
- `MAIN_SYSTEM_PROMPT` — the hardened default for `config.system_prompt` (anti-sycophancy, ask-before-assume, proportional length, honest uncertainty).
- `SEARCH_MODE_PROMPT` — addendum injected when sources are present.
- `NOTEBOOK_PROMPT` — addendum when an active notebook is loaded; instructs the model to say so explicitly if the question is off-topic.
- `MEMORY_HEADER` + `memory_block()` — wraps user facts with "use ONLY if directly relevant; do not volunteer unprompted".
- `ROUTER_NEEDS_SEARCH`, `QUERY_REFORMULATOR`, `fact_extractor_system()` — system prompts for the three internal LLM helpers in `tool_router.py`.
- `compose_system_prompt(...)` — the single function that assembles the final system message from all the above plus runtime context.

If you change ANY string a model sees, do it here. Avoid inlining prompts in other modules.

### `ollama_client.py`
Thin wrapper. `stream_chat()` is a generator yielding token strings (no read timeout — streams can run minutes). `list_models()` and `get_capabilities()` use a **short HTTP timeout** (connect 2s, read 5s) so the Settings UI never hangs on an unreachable Ollama.

### `tool_router.py`
Three LLM-powered helpers, all `temperature=0`, all fail-safe, all with a **30s timeout**:
- `needs_search(message, host, model) → bool` — single YES/NO. Prompt explicitly tells the model the main assistant already knows date/time/location, so it doesn't trigger search for trivia.
- `reformulate_query(messages, host, model) → str` — produces self-contained query. Adds domain keywords for common intents (weather, news, reviews, lyrics, sports, prices). Fallback: last user message verbatim.
- `extract_facts(messages, host, model, existing) → [str]` — bullet-list extraction, dedupes against `existing`. Returns `[]` on NONE/error.

### `search_providers.py`
Unified `search(query, config) → [{title, url, content}]`. Dispatches by `config["search_provider"]` to `_ddg` (`ddgs` lib), `_brave` (Brave Search API with X-Subscription-Token), or `_searxng` (POST `/search?format=json`). Results count is **clamped to [1, 20]** server-side regardless of what config says. `format_as_context()` is the canonical context formatter used for ALL source types (notebook + URL + search).

### `url_fetcher.py`
- `extract_urls(text) → [str]` — regex with trailing-punctuation strip + dedupe.
- `fetch(url) → dict | None` — routes YouTube URLs to `_fetch_youtube`, everything else to `_fetch_generic`. Output shape always `{url, title, content}` (or None).
- YouTube: oembed for title (no API key), `youtube-transcript-api 1.x` (`YouTubeTranscriptApi().fetch()`) with language preference `["el", "en"]` → fallback to first available transcript.
- Generic: `httpx.get` with browser UA, `trafilatura.extract` for clean body, `trafilatura.extract_metadata` for title. Max 8KB chars per page.

### `file_extractor.py`
- `categorize(path) → "text"|"image"|"audio"|"video"` (PDF/DOCX/code/csv/etc. all categorize as "text").
- `is_supported(path, capabilities) → (bool, reason)` — gates non-text categories against required Ollama capabilities (`image` → `vision`, `audio` → `audio`, `video` → `vision`).
- `extract(path, capabilities?) → dict | None` — full attachment dict. Text path: PDF via `pypdf`, DOCX via `python-docx`, anything else as UTF-8 text with latin-1 fallback. 50K char cap. Image/audio/video: base64 with size caps (8MB images, 50MB media).
- `build_ollama_messages(messages) → [dict]` — the single place where attachments meet the Ollama API. Text/PDF/DOCX content gets prepended as `[Attached file: NAME]\nBODY\n\n` blocks. Images go into Ollama's `images` field. Audio/video produce a placeholder note that the model can see.

### `open_notebook_client.py`
REST client for Open Notebook. `list_notebooks()`, `get_notebook_with_content()` (60s in-memory cache; calls `/api/notebooks/{id}/context` for source list then `/api/sources/{id}` per source to retrieve `full_text`), `as_chat_sources()` (formats as canonical source dicts with `category: "notebook"` and `📒 Name · Source` title), `invalidate_cache()`.

### `comfyui_client.py`
REST + WebSocket client. `ping()` via `/system_stats`, `object_info()` (30s cache, ~5MB JSON), `discover()` (extracts checkpoints/samplers/schedulers), `submit_prompt()` (POST `/prompt`), `stream_progress()` (WebSocket via `websocket-client`, polls `/history` as fallback), `fetch_image()`. `invalidate_object_info_cache()` for `?refresh=1` from the UI.

### `image_workflows.py`
Built-in templates with placeholder substitution. Currently ships `SDXL_DEFAULT` (works for SDXL / SD 1.5 / SD 2 / Pony / Illustrious / any `CheckpointLoaderSimple`-based workflow). `render(template, params)` deep-copies and substitutes placeholders (`{{PROMPT}}`, `{{NEGATIVE}}`, `{{CHECKPOINT}}`, `{{SEED}}`, `{{STEPS}}`, `{{CFG}}`, `{{SAMPLER}}`, `{{SCHEDULER}}`, `{{WIDTH}}`, `{{HEIGHT}}`) with type-preserving conversion. `from_custom_json(text)` accepts both raw API-format dicts and `{"prompt": {...}}`-wrapped exports.

### `image_storage.py`
Disk storage under `~/.local/share/logos/images/<chat_id>/`. `save()` writes bytes and returns a `Path`. `is_safe_path()` path-traversal guard for HTTP serving. `delete_for_chat()` removes a chat's image folder (called by `DELETE /api/chats/<id>`).

### `obsidian_sync.py`

Writes a digest of one day's chats into the user's Obsidian Daily Note for the following day. Pure stdlib — no new dependencies. All vault I/O lives in this single module; nothing else in the codebase opens vault files.

**Public API:**
- `sync_date(target_date: date, *, dry_run=False) -> dict` — summarise `target_date`'s chats into the daily note for `target_date + 1 day`.
- `sync_yesterday(*, dry_run=False) -> dict` — convenience wrapper. Used by both the auto-trigger in `app.py` and the manual "Sync yesterday now" button in Settings.

**Section-update algorithm** (`_update_section`): line-by-line scan, NOT regex. Three cases:

1. File doesn't exist → create with just our `##` section.
2. File exists with our header → replace the body (lines from after the header to the next `##` or EOF). **Idempotent** — same input produces byte-identical output.
3. File exists without our header → append the section at EOF with a leading blank line. Other sections never touched.

**Legacy header rename** (`LEGACY_HEADER = "## About Aya"`): if the new header is absent AND the legacy is present, the legacy header line is rewritten in-place to the configured header. Body is then replaced as usual. If both headers coexist, we never touch the legacy one — only the new one.

**Idempotency contract (P-O1):** running the sync twice for the same `target_date` writes the same bytes. The auto-trigger in `app.py` enforces at-most-once-per-day via `~/.local/share/logos/obsidian_last_sync.txt`, but the module itself is safe to call repeatedly.

**Path safety:** `_resolve_daily_note_path` rejects templates that would escape the vault root (`../escape.md`). Writes are atomic — temp file in the same directory + `os.replace`.

**Digest formats:**
- `titles` — one bullet per chat: `- 14:22 · <title>`
- `excerpts` (default) — title bold + first user message as a blockquote, truncated to 240 chars
- `summaries` — reserved for v1.5.1 (LLM-generated). Currently falls back to `excerpts`.

**Skip paths** (return `ok=True` with a `skipped_reason`, not an error):
- `config_incomplete` — `obsidian_vault_path` is empty.
- `vault_missing` — path is set but not a directory.
- `empty` — zero chats on the target date.

**Smoke test:** `python3 backend/obsidian_sync.py` runs an in-module test that exercises all seven edge cases (create, idempotent replace, body update, append-to-existing, legacy rename, dual-header protection, path-escape protection) against temp files. Should always print `obsidian_sync smoke test: OK` before commit.

**Auto-trigger** (in `app.py`): a daemon thread spawned just before `webview.start` calls `sync_yesterday()` at most once per local day. Marker file is updated **regardless of result** so a transient failure (e.g., vault temporarily unmounted) doesn't loop on every relaunch.

---

## 8. Frontend state machine

```
conversationHistory : [{role, content, attachments?, sources?, image?}, ...]
currentChatId       : string | null
pendingAttachments  : [{filename, category, ...}]
forceSearchMode     : bool          // 🔍 toggle persisted across messages
isStreaming         : bool
currentConfig       : {...}
imgGenerating       : bool          // image modal lock
```

- `newChat()` clears everything, sets `currentChatId=null`.
- First successful `saveCurrentChat()` mints a `crypto.randomUUID()` for `currentChatId` and PUTs to `/api/chats/<id>`.
- `loadChat(id)` GET → fills `conversationHistory` and re-renders every message (including sources, attachment chips, and inline images), then re-attaches `msg-actions` to each assistant bubble.
- `regenerateFromMessage(msgEl)` finds the message's position in `conversationHistory`, truncates from that index, removes the corresponding DOM nodes, calls `streamResponse()`.
- `loadMemory()` runs on every Settings → Memory open; chips are click-to-delete (with confirm).
- `refreshNotebooks(selectedId)` populates the active-notebook dropdown when Settings → Notebook opens.
- `refreshComfyui(c, {force})` populates checkpoint / sampler / scheduler dropdowns from `/api/comfyui/status` (optionally `?refresh=1`).
- `probeComfyuiOnce()` at init: pings ComfyUI and enables/disables the 🎨 button accordingly.
- `generateImage()` pins `currentChatId` at submit time so a mid-generation New Chat / chat switch doesn't poison the wrong conversation (`persistMessageToChat()` writes to the original).

---

## 9. Settings tabs (frontend)

The settings overlay is a single modal containing a tab bar plus 6 panel divs, only one visible (`.settings-tab-panel.active`). Active tab is persisted in `localStorage["logos.lastSettingsTab"]` and restored on next open.

| `data-tab` | Panel content |
|---|---|
| `model` | Ollama host, model dropdown, temperature, location |
| `search` | Provider, conditional Brave key / SearXNG URL, count, auto-search |
| `prompt` | Editable system prompt (full-height textarea) |
| `notebook` | Open Notebook API URL, UI URL, connect button, status, active notebook dropdown, refresh button, info line, large-notebook warning |
| `image` | ComfyUI URL, workflow dropdown + custom JSON textarea, checkpoint/sampler/scheduler (auto-populated), w/h/steps/cfg, negative prompt, post-commentary toggle |
| `obsidian` | Vault path + 📁 native folder picker, daily-note path template (with `{date}` placeholder), section header, digest-format dropdown, "Sync yesterday now" + "Preview…" buttons, inline status/preview area |
| `memory` | Auto-loaded list of fact chips with delete buttons + refresh button |

The settings footer also exposes **Quit Logos** — calls `Api.quit_app()` with a confirmation. This is the only reliable way to exit on desktops where the tray icon is not visible (notably vanilla GNOME without an AppIndicator extension).

---

## 10. Extension points

| Want to... | Touch these |
|---|---|
| Add a new search backend | `search_providers.py`: add an `_my_provider(query, count, c)` function, register branch in `search()`, expose option in HTML + load/save in `app.js`. |
| Add a new tool the model can use (URL fetcher style) | New module + integrate in `server.py`'s `generate()`. Emit your own SSE event before tokens. Frontend handles new event in `streamResponse`. Add as a source so it shows in the footer. |
| Support a new file type | `file_extractor.py`: add extension to category sets, write `_extract_<type>()`, route in `extract()`. If binary for a model API, set `data_base64` and update `build_ollama_messages` accordingly. |
| Add a new ComfyUI workflow preset | `image_workflows.py`: define the template dict, register in `PRESETS`. Use the supported `{{...}}` placeholders. Add `<option>` to the workflow dropdown in HTML. |
| Add a setting | `config.py` DEFAULTS, then a field in `index.html` in the appropriate tab, then read/write in `openSettings()` / `saveSettings()` in `app.js`. |
| Add a new HTTP endpoint | Decorate in `server.py`. If it needs native dialogs, add a method on `Api` in `app.py` instead. |
| Change the model's understanding of time/location/memory | `_system_context()` in `server.py` — this is the only place those get composed. |
| Migrate to a new storage layout | Bump module's `_LEGACY_*` path and write a new `_migrate_*` function. Keep the old one for one release cycle. |
| Add a new SSE event in chat | Yield it from `generate()` in `server.py`, then handle in `streamResponse()` in `app.js`. Document it in section 4. |

---

## 11. Build & release

```bash
./build_deb.sh                          # produces logos_<VERSION>_all.deb
sudo apt install ./logos_*.deb          # installs to /usr/share/logos + /usr/bin/logos + icons + .desktop
```

`build_deb.sh`:
- Stages files into `build/logos_<version>_all/`
- Drops `__pycache__/`, `dist/`, `build/`, `*.pyc`, `*.spec` from the staged tree
- Generates `DEBIAN/control`, `DEBIAN/postinst`, `DEBIAN/prerm`
- `postinst` pip-installs all of `backend/requirements.txt` to system Python with `--break-system-packages`
- Runs `dpkg-deb --build` and writes the .deb to the project root

Release: bump `VERSION` in `backend/version.py` → `./build_deb.sh` → commit + tag + push → GitHub release with the `.deb` attached.

---

## 12. Conventions

- Python: stdlib + minimal deps. No frameworks beyond Flask. No async (single-user, threaded Flask is enough).
- JS: vanilla, no build step, no framework. ES2020+ (we target WebKitGTK 2.50+ which is modern).
- CSS: hand-written, custom properties for theme. No utility frameworks.
- All UI strings in English. The model responds in the user's language thanks to the system prompt.
- No telemetry, no analytics. The only outbound network calls are user-configured (Ollama, SearXNG, Brave, Open Notebook, ComfyUI), user-initiated (URLs they paste), or one-off CDN scripts loaded by `index.html` (marked.js, highlight.js).
- Fail-safe everywhere: every network/LLM/IO call has a try/except and a sensible empty/None fallback. The chat stream never aborts because of a side feature failing.

---

## 13. Things deliberately not done

- **No auth / multi-user.** Single-process desktop app.
- **No async / no websockets** in our own API. SSE over plain HTTP is enough. (We do use WS as a *client* to ComfyUI.)
- **No DB.** JSON files + per-file image storage. If you outgrow this, SQLite drop-in via `sqlite3` stdlib.
- **No vector store / embedding-based memory.** The memory module is fact-bullets injected into system prompt. If you need semantic recall over chat history, that's a new module — don't bolt it onto `memory.py`.
- **No agent loop / autonomous tool use.** The "tools" here (search, URL fetch, notebook, image gen) are orchestrated by the server or explicitly triggered by the user, NOT exposed to the model as function-calls. Deliberate simplification — easier to reason about, easier to debug, no schema dance with every model. If you want full agentic behavior, use a purpose-built agent (e.g. Hermes Agent) alongside Logos rather than inside it.
- **No image generation orchestration via the LLM.** The 🎨 button is explicit. The LLM cannot decide to generate an image on its own. Avoids surprise GPU usage and unpredictable workflows.
- **No TTS / STT.** Out of scope.

---

## 14. Known caveats

- **Search noise**: if `auto_search_enabled` and the LLM router returns YES on a question the model could have answered alone, you'll see unnecessary search. Tighten the router prompt in `tool_router.needs_search`.
- **Memory wrong facts**: the auto-extractor is conservative but can still capture ephemeral statements as "persistent". The fix is the user-facing delete button in Settings → Memory.
- **YouTube transcripts**: depends on caption availability. Auto-generated captions in the requested language usually work; some videos disable transcripts entirely.
- **PDFs that are scans** (no text layer): `pypdf` returns empty. OCR is not bundled.
- **Open Notebook large corpora**: the current `get_notebook_with_content` fetches ALL sources' `full_text`. Works fine for < ~30K tokens total. For larger notebooks, swap to a RAG approach (embeddings + retrieval) — see the migration note in §10.
- **ComfyUI custom workflows**: the placeholder mechanism only substitutes exact string matches like `"{{PROMPT}}"`. If your workflow node expects a number where you wrote a string placeholder, ComfyUI may reject it — use the right placeholder type or edit the workflow JSON.
- **Vision-model attachment routing**: image attachments are only sent via Ollama's `images` field when the model has `vision` capability. The auto-discovery relies on `ollama.show(model).capabilities`; if a model misreports, attach gating may be wrong.
- **Memory + notebook in same prompt**: when both are active, the system prompt can grow large fast. Keep an eye on your model's context window.

---

## Changelog

### v1.3.0 — Small-model robustness

**Phase B — Prompt hardening:**
- B1: Date/time injection with authoritative block (appears 2× in assembled prompt)
- B2: Anti-fabrication rules in `SEARCH_MODE_PROMPT` (5 rules: source-only facts, citation verification, thin-source honesty, "not covered" handling, no fake "I cannot access internet")
- B3: Language-consistency rule (Greek/English detection, no multilingual token leakage)
- B4: Summary framing rule (honest assessment of source completeness)

**Phase C — Source-quality signals:**
- C1: Thin-source tagging inline in `format_as_context()` (< 300 chars = `[THIN]`)
- C2: `## SOURCE QUALITY` summary block before sources (counts thin vs substantial)

**Phase D — Per-model tuning:**
- D1: `model_overrides` in config + `effective()` lookup (per-model temperature)
- D2: Conservative mode checkbox (temperature 0.4 preset for small models)

**Phase E — Test harness:**
- E1: Regression test runner (`tests/regression/run.py`) with snapshot + assertion suite
- E2: Replay tool (`tools/replay.py`) for side-by-side prompt comparison

**API changes (backwards compatible):**
- `compose_system_prompt()`: new params `date_info`, `detected_language`, `source_quality_block`
- `_build_system_prompt()`: new param `user_message`, `source_quality_block`
- `format_as_context()`: thin tag appended to titles (same signature)
- New: `date_block()`, `language_rule()`, `summary_framing_rule()`, `source_quality_summary()`
- New config key: `model_overrides: {}`

**New files:**
- `tests/regression/run.py` — regression test runner
- `tests/regression/README.md` — test suite documentation
- `tests/regression/test_cases.json` — test cases
- `tools/replay.py` — chat replay & comparison tool

### v1.4.0 — Notes feature + window management

**Phase A — Storage foundation:**
- A1: SQLite notes store with FTS5 full-text search (diacritic-insensitive for Greek)
- A2: REST API endpoints (`GET/POST/DELETE /api/notes`, `GET /api/notes/<id>`, `GET /api/notes/<id>/export`)

**Phase B — Take-Note UX:**
- B1: "📝 Note" button on every assistant message bubble (saves question + answer + sources + metadata)

**Phase C — Notes panel + viewer:**
- C1: Left drawer (`#notes-sidebar`) with slide animation, toggle via 🗒 header button
- C2: Notes list with snippets, timestamps, model info, auto-refresh
- C3: Detail modal with full Q+A+sources, markdown rendering, ESC/overlay close
- C4: Delete with confirmation dialog

**Phase E — Export:**
- E1: TXT export (UTF-8 with BOM, structured headers)
- E2: PDF export via fpdf2 (DejaVu Sans for Greek, clickable source URLs)

**Phase F — Window management:**
- F1: Single-instance via abstract UNIX socket (second launch surfaces existing window)
- F2: System tray icon with Show/Quit menu (pystray + Pillow)
- F3: Close (X) → minimize to tray instead of quit

**New dependencies:**
- `fpdf2` — PDF export
- `pystray` + `Pillow` — System tray icon

**New files:**
- `backend/notes.py` — SQLite notes store with FTS5
- `tests/regression/run.py` — Regression test runner (v1.3.0)
- `tests/regression/test_cases.json` — Test cases (v1.3.0)
- `tools/replay.py` — Chat replay tool (v1.3.0)
- `icons/logos-32.png` — Tray icon

### v1.5.0 — Obsidian sync, taskbar fix, test-bug cleanup

**Phase A — Obsidian backend:**
- A1: `backend/obsidian_sync.py` — stdlib-only digest writer. Section-update algorithm handles create / idempotent replace / append-to-existing / legacy `## About Aya` → `## About Logos` rename / dual-header protection / path-escape protection. In-module smoke test covers all seven cases.
- A2: `POST /api/obsidian/sync` and `GET /api/obsidian/preview` endpoints. Skip-reasons (`config_incomplete`, `vault_missing`, `empty`) returned as `ok=true` state, not errors.

**Phase B — Settings UI:**
- B1: New "Obsidian" Settings tab with 4 fields (vault path + 📁 picker, daily-note template, section header, digest format) plus "Sync yesterday now" and "Preview…" buttons. Preview renders inline.
- B2: This documentation update.

**Phase C — Auto-trigger:**
- C1: Daemon thread in `app.py:main()` calls `obsidian_sync.sync_yesterday()` at most once per local day, tracked via `~/.local/share/logos/obsidian_last_sync.txt`. Fail-open: marker is written regardless of result so a transient failure doesn't retry on every launch.

**Quit affordance + window-manager fix:**
- `GLib.set_prgname("Logos")` + `set_application_name` + default window icon are now set BEFORE `webview.create_window`, aligning `WM_CLASS` with the `.desktop` file's `StartupWMClass`. This fixes the "minimize disappears the window" symptom where the taskbar couldn't bind the iconified window to the desktop entry.
- `on_closing` handler is now fail-open: if `window.minimize()` raises, the close proceeds instead of being silently cancelled — so a broken minimize on some pywebview/Wayland combos no longer leaves the user with an invisible, un-closable process.
- New `Quit Logos` button in Settings footer (calls `Api.quit_app()` via the JS bridge) — primary path for desktops where the tray icon isn't visible.

**Notes button glyph:**
- Header icon changed from `🗎` (emoji, colored on most fonts) to `▤` (U+25A4, monochrome — same visual family as `⊕ ⚙ ☰`). Removed the `#btn-notes-toggle` CSS override that was killing the border and forcing 18px font-size; it now inherits the standard `header button` style.

**Test-harness fixes** (test-bug cleanup, not Logos bugs):
- `code · python list comprehension`: removed `def` from `expected_keywords` — the prompt explicitly asks for a list comprehension.
- `code · regex explanation`: removed `ηλεκτρονικ` from `expected_keywords` — the loanword "email" is acceptable Greek.
- `date · current` (test_cases.json): `min_tokens` lowered from 10 to 5; a one-line date question deserves a one-line answer.
- `date · current time` (test_cases_v2.json): replaced `expect_honest_uncertainty` with `expect_script_consistency` — Logos provides the current time via the system prompt, so the model SHOULD answer it.
- `run.py`: `UNCERTAINTY_PHRASES` allowlist extended with clarification-request phrases (`διευκρινίστε`, `ολοκληρώστε`, `τι εννοείς`, `ασαφές`, `αμφίσημο`, `please clarify`, `what do you mean`, …). Asking for clarification now counts as "honest uncertainty", alongside admitting lack of knowledge.

**New config keys** (additive, default-disabled):
- `obsidian_vault_path: ""` — empty disables the entire feature.
- `obsidian_daily_note_path: "Daily Notes/{date}.md"`
- `obsidian_section_header: "## About Logos"`
- `obsidian_digest_format: "excerpts"`

**New files:**
- `backend/obsidian_sync.py`
- `roadmap-v1.5-obsidian.md`

**No new dependencies.** Stdlib only.
