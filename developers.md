# Logos — Developer & LLM Reference

> Complete architecture and API reference for anyone (human or LLM) modifying, fixing, or extending Logos.

This document is the **single source of truth**. If you change something material, update this file in the same commit.

---

## 1. High-level architecture

```
┌────────────────────────────────────────────────────┐
│         pywebview window (GTK + WebKit2)           │
│                                                    │
│  ┌──────────────────────────────────────────────┐  │
│  │   Vanilla HTML/JS frontend (frontend/)        │  │
│  │   index.html, style.css, app.js               │  │
│  │                                                │  │
│  │   - fetch() against /api/...                  │  │
│  │   - SSE streaming for /api/chat               │  │
│  │   - window.pywebview.api.* for native dialogs │  │
│  └────────────────┬────────────────────┬─────────┘  │
│                   │ HTTP localhost     │ JS bridge   │
│  ┌────────────────▼────────────────┐  │             │
│  │  Flask app (backend/server.py)  │  │             │
│  │  - serves frontend/ at /        │  │             │
│  │  - /api/* endpoints             │  │             │
│  │  - SSE stream for /api/chat     │  │             │
│  └─┬───────┬───────┬───────┬───────┘  │             │
│    │       │       │       │          │             │
│    ▼       ▼       ▼       ▼          ▼             │
│  Ollama  SearXNG  Disk   Web      app.py Api        │
│  :11434  :8081    JSON   pages    pick_files()      │
│                          /YT      export_chat()     │
└────────────────────────────────────────────────────┘
```

- **One process.** Flask runs in a daemon thread; pywebview takes the main thread.
- **One language for business logic** (Python). The frontend is dumb — it sends HTTP and renders SSE.
- **Local-only network.** Bound to `127.0.0.1`, default port `17842`.

---

## 2. File / directory layout

```
logos/
├── app.py                  # entry point: starts Flask thread + pywebview window
├── icon.svg                # source icon (Λ on dark)
├── app-icon.png            # 512×512 PNG (used by .desktop in dev)
├── icons/                  # rendered sizes for deb install
│   ├── logos-16.png .. logos-512.png
├── logos.desktop           # XDG entry installed by deb
├── build_deb.sh            # produces logos_1.0.0_all.deb
├── README.md               # user-facing
├── developers.md           # this file
├── backend/
│   ├── version.py          # VERSION, APP_NAME
│   ├── server.py           # Flask app, all /api endpoints, SSE stream
│   ├── config.py           # JSON config persistence + legacy migration
│   ├── chats.py            # chat archive (one JSON per chat)
│   ├── memory.py           # persistent user facts + manual-trigger detection
│   ├── ollama_client.py    # ollama wrapper: stream_chat, list_models, get_capabilities
│   ├── searxng_client.py   # SearXNG search + result formatting
│   ├── tool_router.py      # needs_search, reformulate_query, extract_facts (LLM calls)
│   ├── url_fetcher.py      # URL extraction, generic webpage fetch, YouTube specialized fetch
│   ├── file_extractor.py   # categorize + extract text/PDF/DOCX/image; ollama-message builder
│   └── requirements.txt
└── frontend/
    ├── index.html          # static markup
    ├── style.css           # dark/terminal theme
    └── app.js              # all client logic (state, fetch, SSE parse, UI render)
```

---

## 3. Data storage

| File | Owner module | Schema |
|---|---|---|
| `~/.config/logos/config.json` | `config.py` | flat dict (see DEFAULTS) |
| `~/.local/share/logos/chats/<uuid>.json` | `chats.py` | `{id, title, created_at, updated_at, messages[]}` |
| `~/.local/share/logos/memory.json` | `memory.py` | `{facts: [{id, text, source, created_at}]}` |

All three modules implement **one-time auto-migration** from the legacy `chat_app` paths. See `_migrate_legacy()` in each.

### Message schema (inside chat JSON)

```json
{
  "role": "user" | "assistant",
  "content": "string",

  // user only, optional:
  "attachments": [
    {
      "filename": "report.pdf",
      "type": "pdf",
      "category": "text" | "image" | "audio" | "video",
      "size": 12345,
      "mime": "application/pdf",
      "content": "extracted text...",         // text path
      "data_base64": "...",                   // image/audio/video path
      "truncated": false,                     // text path only
      "error": "..."                          // present iff extraction failed
    }
  ],

  // assistant only, optional:
  "sources": [
    {"title": "...", "url": "...", "content": "..."}
  ]
}
```

`messages` always alternates user/assistant. The first message is user.

---

## 4. HTTP API contract

All endpoints are JSON in / JSON out unless noted. Base: `http://127.0.0.1:<port>`.

### Config

| Method | Path | Body | Response |
|---|---|---|---|
| GET | `/api/config` | — | full config dict (with defaults) |
| POST | `/api/config` | partial dict | `{ok: true}` (merged + saved) |

### Models / capabilities / version

| Method | Path | Response |
|---|---|---|
| GET | `/api/models` | `{models: [string]}` (from Ollama) |
| GET | `/api/capabilities` | `{model, capabilities: [string], supports_images, supports_audio, accept: [string]}` |
| GET | `/api/version` | `{app: "Logos", version: "1.0.0"}` |

### Search (direct, bypasses LLM routing)

| Method | Path | Body | Response |
|---|---|---|---|
| POST | `/api/search` | `{query}` | `{results: [{title, url, content}]}` |

### Chats archive

| Method | Path | Body | Response |
|---|---|---|---|
| GET | `/api/chats` | — | `{chats: [{id, title, created_at, updated_at}]}` (sorted desc) |
| GET | `/api/chats/<id>` | — | full chat |
| PUT | `/api/chats/<id>` | `{messages, title?}` | upsert — saved chat dict |
| POST | `/api/chats/<id>/rename` | `{title}` | renamed chat dict |
| DELETE | `/api/chats/<id>` | — | `{ok: true}` |

### Memory

| Method | Path | Body | Response |
|---|---|---|---|
| GET | `/api/memory` | — | `{facts: [{id, text, source, created_at}]}` |
| POST | `/api/memory` | `{text}` | `{ok, added: bool}` |
| DELETE | `/api/memory/<id>` | — | `{ok: true}` |

### Chat stream

`POST /api/chat`

Request:
```json
{
  "messages": [{"role":"user","content":"...","attachments":[...]}, ...],
  "force_search": false
}
```

Response: `text/event-stream`. Events are JSON after `data: `. Always one event per SSE message, terminated by `\n\n`.

| `type` | Fields | Meaning |
|---|---|---|
| `remembered` | `fact` | Manual `να θυμάσαι ...` trigger fired; fact saved to memory |
| `fetching_urls` | `urls: [string]` | About to fetch user-provided URLs |
| `searching` | — | Reformulated query is about to hit SearXNG |
| `sources` | `query?, sources: [{title, url, content}]` | All sources used as context (URLs + search) |
| `token` | `content` | Single Ollama token (chunked text) |
| `done` | — | Stream complete |
| `error` | `message` | Fatal error inside the stream |

Order guarantees:
- `remembered` fires before any context work
- `fetching_urls` before `searching`
- `sources` (if any) before the first `token`
- `done` after the last `token`
- `error` may replace `done` if something blows up

Token text may include partial multi-byte UTF-8 characters — the **client buffers** with `TextDecoder({stream:true})`. Each SSE message also may not be a complete line — the **client buffers** incomplete lines across `read()` calls.

---

## 5. pywebview JS bridge

`app.py` defines an `Api` class. Each public method becomes `window.pywebview.api.<method>(...)` in the frontend, returning a Promise.

| Method | Returns | Used for |
|---|---|---|
| `pick_files()` | `[{filename, category, size, content?/data_base64?, error?}, ...]` | Native multi-select dialog. Filters by current model capabilities. |
| `export_chat(chat_id)` | `{ok, path?, cancelled?, error?}` | Native save dialog → writes chat JSON |

The bridge runs methods on a pywebview-managed thread. Heavy I/O (PDF parse, large image read) happens here, not in Flask.

---

## 6. Request lifecycle (chat with everything)

User sends a message containing a URL and an attached PDF, with auto-search on.

1. **Frontend** `streamResponse(forceSearch=false)` POSTs `/api/chat` with `messages` (incl. attachments).
2. **Server** `chat()` generator:
   - Detect manual remember trigger on last user message → if matched, `memory.add()` and emit `remembered` event.
   - Build `sys_ctx` from `_system_context()`: date/time + location + memory facts.
   - Extract URLs from last user message. If any: emit `fetching_urls`, call `url_fetcher.fetch_many()`.
   - URL fetcher routes per-URL: YouTube → `_fetch_youtube()` (oembed title + `youtube-transcript-api`); generic → `_fetch_generic()` (`httpx` + `trafilatura.extract`).
   - Decide web search: `force_search` always wins; else `auto_search_enabled and not url_contents` → call `tool_router.needs_search()`.
   - If searching: emit `searching`, call `tool_router.reformulate_query()` then `searxng_client.search()`.
   - Combine `url_contents + search_results` → emit `sources`.
   - Build final system prompt: `sys_ctx + "\n\n" + (search_system_prompt + context if any else system_prompt)`.
   - Transform messages: `file_extractor.build_ollama_messages()` inlines text attachments into content and pushes image attachments into the `images` field of each Ollama message.
   - Stream from `ollama_client.stream_chat()`. Yield `token` events; buffer all tokens.
   - Yield `done`.
   - Spawn background thread for `_extract_facts_bg()` → `tool_router.extract_facts()` (LLM call) → `memory.add()` for each new fact.
3. **Frontend** parses SSE, updates DOM, attaches sources + action buttons, calls `saveCurrentChat()` (PUT to `/api/chats/<uuid>`).

---

## 7. Module deep dives

### `config.py`
Flat dict, JSON on disk. `load()` merges saved data over `DEFAULTS`. `_migrate_legacy()` copies from `~/.config/chat_app/config.json` on first call if new file missing.

### `chats.py`
One JSON per chat. UUID-keyed. `list_chats()` returns metadata only (no messages), sorted by `updated_at` desc. `save()` is upsert — keeps `created_at` if file exists, always updates `updated_at`.

### `memory.py`
Single JSON. `detect_remember(text)` matches Greek (`να θυμάσαι`, `θυμήσου`, `να θυμάμαι`) and English (`/remember`, `remember:`) triggers, then strips leading particles (`ότι`, `πως`, `πάντα`, `that`). `add()` does case-insensitive dedupe. `format_for_prompt(facts)` produces the bullet block that's appended to system context.

### `ollama_client.py`
Thin wrapper. `stream_chat()` is a generator yielding token strings. `list_models()` and `get_capabilities()` both fail-safe to `[]` on connection errors.

### `searxng_client.py`
`search(query, base_url, n)` hits `{base_url}/search?format=json&categories=general` with 8s timeout. Always returns a list (empty on any failure). `format_as_context(results)` produces the `[1] Title / URL / content` block injected into the system prompt.

### `tool_router.py`
Three LLM-powered helpers, all temperature=0, all fail-safe:
- `needs_search(message, host, model) → bool` — single YES/NO. Router prompt explicitly tells the model the main assistant already knows date/time/location, so it doesn't trigger search for trivia.
- `reformulate_query(messages, host, model) → str` — produces self-contained query. Adds domain keywords for common intents (weather, news, reviews, lyrics, sports, prices). Fallback: last user message verbatim.
- `extract_facts(messages, host, model, existing) → [str]` — bullet-list extraction, dedupes against `existing`. Returns `[]` on NONE/error.

### `url_fetcher.py`
- `extract_urls(text) → [str]` — regex with trailing-punctuation strip + dedupe.
- `fetch(url) → dict | None` — routes YouTube URLs to `_fetch_youtube`, everything else to `_fetch_generic`. Output shape always `{url, title, content}` (or None).
- YouTube: oembed for title (no API key), `youtube-transcript-api 1.x` (`YouTubeTranscriptApi().fetch()`) with language preference `["el", "en"]` → fallback to first available.
- Generic: `httpx.get` with browser UA, `trafilatura.extract` for clean body, `trafilatura.extract_metadata` for title. Max 8KB chars per page.

### `file_extractor.py`
- `categorize(path) → "text"|"image"|"audio"|"video"` (PDF/DOCX/code/csv/etc. all categorize as "text").
- `is_supported(path, capabilities) → (bool, reason)` — gates non-text categories against required Ollama capabilities (`image` → `vision`, `audio` → `audio`, `video` → `vision`).
- `extract(path, capabilities?) → dict | None` — full attachment dict (see schema in §3). Text path: PDF via `pypdf`, DOCX via `python-docx`, anything else as UTF-8 text with latin-1 fallback. 50K char cap. Image/audio/video: base64 with size caps (8MB images, 50MB media).
- `build_ollama_messages(messages) → [dict]` — the single place where attachments meet the Ollama API. Text/PDF/DOCX content gets prepended as `[Attached file: NAME]\nBODY\n\n` blocks. Images go into Ollama's `images` field. Audio/video produce a placeholder note that the model can see.

---

## 8. Frontend state machine

```
conversationHistory : [{role, content, attachments?, sources?}, ...]
currentChatId       : string | null
pendingAttachments  : [{filename, category, ...}]
isStreaming         : bool
currentConfig       : {...}
```

- `newChat()` clears everything, sets `currentChatId=null`.
- First successful `saveCurrentChat()` mints a `crypto.randomUUID()` for `currentChatId` and PUTs to `/api/chats/<id>`.
- `loadChat(id)` GET → fills `conversationHistory` and re-renders every message (including sources and attachment chips), then re-attaches `msg-actions` to each assistant bubble.
- `regenerateFromMessage(msgEl)` finds the message's position in `conversationHistory`, truncates from that index, removes the corresponding DOM nodes, calls `streamResponse()`.
- `loadMemory()` runs on every Settings open; chips are click-to-delete (with confirm).

---

## 9. Extension points

| Want to... | Touch these |
|---|---|
| Add a new tool the model can use | New module + integrate in `server.py`'s `generate()`. Emit your own SSE event before tokens. Frontend handles new event in `streamResponse`. |
| Support a new file type | `file_extractor.py`: add extension to category sets, write `_extract_<type>()`, route in `extract()`. If it's binary for a model API, set `data_base64` and update `build_ollama_messages` accordingly. |
| Add a setting | `config.py` DEFAULTS, then a field in `index.html` settings panel, then read/write in `openSettings()` / `saveSettings()` in `app.js`. |
| Add a new endpoint | Decorate in `server.py`. If it needs to be exposed to the native dialog layer, add a method on `Api` in `app.py` instead. |
| Change the model's understanding of time/location/memory | `_system_context()` in `server.py` — this is the only place those get composed. |
| Migrate to a new storage layout | Bump module's `_LEGACY_*` path and write a new `_migrate_*` function. Keep the old one for one release cycle. |

---

## 10. Build & release

```bash
./build_deb.sh                  # produces logos_<version>_all.deb
sudo apt install ./logos_*.deb  # installs to /usr/share/logos + /usr/bin/logos + icons + .desktop
```

`build_deb.sh`:
- Stages files into `build/logos_<version>_all/`
- Generates `DEBIAN/control`, `DEBIAN/postinst`, `DEBIAN/prerm`
- `postinst` pip-installs `backend/requirements.txt` to system Python with `--break-system-packages`
- Runs `dpkg-deb --build` and writes the .deb to the project root

Version bump: edit `backend/version.py` → re-run `build_deb.sh`.

---

## 11. Conventions

- Python: stdlib + minimal deps. No frameworks beyond Flask. No async (it's single-user, threaded Flask is enough).
- JS: vanilla, no build step, no framework. ES2020+ (we target WebKitGTK 2.50+ which is modern).
- CSS: hand-written, custom properties for theme. Avoid utility frameworks.
- All user-facing text in the UI is currently English; the model responds in the user's language thanks to the system prompt.
- No telemetry, no analytics, no remote URLs except: user-pasted URLs, configured Ollama host, configured SearXNG host, YouTube oembed (only when user pastes a YouTube link), and CDN scripts for marked.js / highlight.js (loaded in `index.html`).

---

## 12. Things deliberately not done

- **No auth / multi-user.** Single-process desktop app.
- **No async / no websockets.** SSE over plain HTTP is enough.
- **No DB.** JSON files. If you outgrow this, SQLite drop-in via `sqlite3` stdlib.
- **No vector store / embedding-based memory.** The memory module is fact-bullets injected into system prompt. If you need semantic recall over chat history, that's a new module — don't bolt it onto `memory.py`.
- **No image generation / TTS / STT.** Out of scope for v1.
- **No tool calling via Ollama's `tools` API.** The "tools" here (search, URL fetch, memory) are orchestrated by the server, not exposed to the model as function-calls. That's a deliberate simplification — easier to reason about, easier to debug, no schema dance with every model.

---

## 13. Known caveats

- Search noise: if `auto_search_enabled` and the LLM router returns YES on a question the model could have answered alone, you'll see unnecessary search. Tighten the router prompt in `tool_router.needs_search`.
- Memory wrong facts: the auto-extractor is conservative but can still capture ephemeral statements as "persistent". The fix is the user-facing delete button in Settings.
- YouTube transcripts: depends on caption availability. Auto-generated captions in the requested language usually work; some videos disable transcripts entirely.
- PDFs that are scans (no text layer): `pypdf` returns empty. OCR is not bundled.
- The Ollama Python client surfaces `:cloud` models the same as local ones — capability detection still works because `ollama.show()` returns capabilities for both.
