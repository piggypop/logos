# Λ Logos

> Minimal desktop chat app for local LLMs via Ollama — with web search, URL & YouTube reading, file attachments, and persistent memory.

**Click an icon, get a chat window.** No accounts, no cloud, no telemetry. Your config, chats, and memory live on your machine.

---

## Features

- **Local LLMs via Ollama** — any model you've pulled, including `:cloud` models. Auto-detected capabilities.
- **Real web search** through your local SearXNG instance, with **auto-routing** (the model decides when to search) and **context-aware query reformulation** (so follow-ups like "και στη Μαδρίτη;" actually work).
- **URL reading** — paste any URL and Logos fetches & extracts clean text via `trafilatura`. YouTube URLs route through `youtube-transcript-api` for full timestamped transcripts.
- **File attachments** — text, code, PDF, DOCX always; images if the model has `vision` capability. Audio/video extensions are recognized but require model support.
- **Persistent memory** — automatic background extraction of facts about you after each chat, plus manual triggers ("να θυμάσαι ότι ..." / `/remember ...`). View & delete from Settings.
- **Chat history sidebar** — grouped by date, rename, export as JSON, delete.
- **Per-message actions** — copy markdown, regenerate from any point.
- **Sources panel** — every reply with web/URL context shows clickable footnotes.
- **Streaming responses** — SSE token-by-token, UTF-8-safe (Greek, emoji, anything multi-byte).
- **Dark / terminal aesthetic** — `JetBrains Mono` accents, gold (#c8a96e) highlight on near-black.

---

## Install (Debian / LMDE / Ubuntu)

```bash
sudo apt install ./logos_1.0.0_all.deb
```

The package handles GTK/WebKit system deps via `Depends`. Python packages are pip-installed by the `postinst` script (uses `--break-system-packages`).

After install: open your application menu → **Logos**.

---

## Prerequisites

- **Ollama** running locally with at least one pulled model — https://ollama.com
- **SearXNG** (optional, for web search) — https://docs.searxng.org/admin/installation.html
  - Default endpoint: `http://localhost:8081`
  - Must have JSON format enabled in `settings.yml`:
    ```yaml
    search:
      formats:
        - html
        - json
    ```

Both are configurable from the Settings panel.

---

## First-run setup

1. Open Logos.
2. Click ⚙ (top-right) → **Settings**.
3. Set your model from the dropdown (loaded from your local Ollama).
4. Set **Your Location** (used in system prompt so the model knows where you are — e.g., "Chalkis, Greece").
5. Verify SearXNG URL if you have it. Disable **Auto Search** if you don't.
6. Save.

That's it. Click ⊕ (top-right) for a new chat, type, hit Enter.

---

## Usage

| Action | How |
|---|---|
| Send message | Type → Enter |
| Newline in input | Shift+Enter |
| Force web search | 🔍 button (instead of Send) |
| Attach files | 📎 button → native picker (multi-select) |
| New chat | ⊕ in header |
| Open history | ☰ in header (slide-in panel) |
| Rename chat | ✎ on hover in history |
| Export chat | ↓ on hover in history (native save dialog) |
| Delete chat | ✕ on hover in history |
| Copy a reply | ⎘ Copy under any assistant message |
| Regenerate | ↻ Regenerate (truncates from that point, regenerates) |
| Manual memory save | Type `να θυμάσαι ότι ...` or `/remember ...` |
| View/edit memory | Settings → Memory section |

---

## Data locations

| What | Where |
|---|---|
| Config | `~/.config/logos/config.json` |
| Chats | `~/.local/share/logos/chats/<uuid>.json` |
| Memory | `~/.local/share/logos/memory.json` |

If you previously used the development name (`chat_app`), Logos auto-migrates your data on first run.

---

## Architecture (one-line)

Python Flask backend + vanilla HTML/JS frontend, wrapped in a native window via `pywebview` (GTK+WebKit2). One process. No build step for the UI.

For details, contributing, or asking an LLM to extend the app, see [developers.md](developers.md).

---

## License

MIT.

---

## Credits

- Web extraction: [trafilatura](https://github.com/adbar/trafilatura)
- YouTube transcripts: [youtube-transcript-api](https://github.com/jdepoix/youtube-transcript-api)
- PDF parsing: [pypdf](https://github.com/py-pdf/pypdf)
- DOCX parsing: [python-docx](https://github.com/python-openxml/python-docx)
- Native window: [pywebview](https://pywebview.flowrl.com/)
- Markdown rendering: [marked.js](https://marked.js.org/), [highlight.js](https://highlightjs.org/)
- Local LLM runtime: [Ollama](https://ollama.com)
