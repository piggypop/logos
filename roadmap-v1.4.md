# Logos v1.4.0 — Notes Feature [COMPLETED]

> **Status:** COMPLETED — All phases (A-F) implemented and reviewed.
**Authored:** 2026-05-22, after v1.3.0 shipped.
**Target version:** v1.4.0
**Owner:** Architect (you, returning for review). Implementer: any LLM coder following this doc.
**Note on file naming:** This is a separate file from `roadmap.md` (which now archives the v1.3.0 cycle). When v1.4 ships, the architect can decide on a long-term convention (e.g. move both to `docs/roadmaps/`).

---

## How to use this document

Same workflow as `roadmap.md` (v1.3.0):

1. Read this document fully before touching any code.
2. Read `developers.md` (single source of truth for current architecture) and the files named in each milestone.
3. Implement one milestone at a time. Each milestone has an **Acceptance criteria** section — verify those before marking it done.
4. **Stop and return to the architect** at the end of each phase (A, B, C, D, E). Do not begin the next phase autonomously.
5. Each milestone has a **Risks / open questions** section. If anything there is unresolved by the time you reach the milestone, stop and ask before guessing.
6. Update the **Status** field at the top of this document when you start work (`In progress — currently on Bx`) and after each phase review.

**Conventions** — same as v1.3.0 roadmap. `S / M / L` complexity: S = one function and < ~30 lines, M = one module, L = multi-module.

---

## 0. Background — what we are building

After running v1.3.0 in real use, the user wants a way to **capture interesting assistant responses** as notes, organize them in a panel, search across the archive over time, and export individual notes for sharing or filing.

The feature is a "Take Note" affordance under every assistant bubble, a left-side drawer that lists every note (with search), and per-note export to TXT and PDF.

**Concretely:**

1. A **"Take note" button** appears under every assistant message, alongside the existing `⎘ Copy` and `↻ Regenerate` buttons. Clicking it saves the note and flashes a `✓ Noted` confirmation.
2. A note captures **the user's question, the assistant's response, and any web/notebook source citations** that were attached. Plus metadata: timestamp, originating chat id + title, model name.
3. A **left-anchored drawer** (`#notes-sidebar`) mirrors the existing right-anchored chat history sidebar. Toggle button in the header (`🗒` or similar). Contains a search input at the top and a scrollable list of notes (snippet + date + originating chat title).
4. Clicking a note expands an inline detail view (or modal) with the full Q + A + sources.
5. Each note has a `Delete` and an `Export ▾` control. Export menu offers `Text (.txt)` and `PDF (.pdf)`.
6. Search is **full-text**, fast, handles Greek with diacritic-insensitive matching.

**Storage:** SQLite database at `~/.local/share/logos/notes.db`, with FTS5 virtual table for search. SQLite is in the Python stdlib — zero new dependency for storage.

**PDF export:** the only new dependency this feature requires. See E2 for the trade-off and the recommended choice (`fpdf2`).

---

## 1. Design principles for this roadmap

The principles from `roadmap.md` (v1.3.0) still apply. Repeated here for the implementer:

**P1. Prompts live in `prompts.py`.** This feature doesn't touch prompts, but if any user-facing string ends up "prompt-like" (e.g. an LLM-generated note summary in a later iteration), it goes in `prompts.py`.

**P2. Schema discipline.** Chats stay in JSON. Memory stays in JSON. **Notes introduce SQLite** — a deliberate, scoped exception, justified by the search requirement. Notes do not migrate any existing data; they live in a separate file and can be deleted independently.

**P3. Fail-safe preserved.** Note operations **never** abort or interfere with the chat stream. If the SQLite write fails for any reason, the chat continues, the button shows a transient error, and the rest of the app is unaffected.

**P4. Backwards compat.** Existing chat JSONs continue to load unchanged. The notes feature is additive.

**P5. Config backward compat.** Any new config keys go into `config.DEFAULTS` with sensible defaults. (Notes are not expected to need config; flag if a config option emerges.)

**P6. Minimal dependencies.** SQLite is stdlib — free. PDF export needs `fpdf2` (~250 KB, pure Python). Phase F adds `pystray` + `Pillow` for the system-tray icon (~5 MB combined; the user explicitly chose tray-with-Quit over a Ctrl+Q shortcut). These three are the only new deps v1.4.0 introduces; no others without architect approval.

**P7. Out of scope** for v1.4.0:
- Note editing (user chose `view + delete + export`; not editing).
- Tags / folders / categorization (user chose against; defer to v1.5+).
- Cross-device sync, cloud backup (no infra; the file is local).
- Auto-generated note titles via the LLM (defer).
- Search highlight rendering (nice-to-have; see D2, optional).
- Bulk export (`Export all notes`) — defer.

**P8. Every milestone has acceptance criteria** that the implementer can verify by interacting with the running app.

---

## 2. Phase overview

| Phase | Theme                  | Milestones      | Blocks downstream? |
|-------|------------------------|-----------------|--------------------|
| **A** | Storage foundation     | A1, A2          | Yes — B, C, D, E all need A |
| **B** | Take-Note UX           | B1, B2          | C needs B for the visual proof loop |
| **C** | Notes panel + viewer   | C1, C2, C3, C4  | D extends C's search field; E adds buttons inside the viewer |
| **D** | Search                 | D1, D2          | — |
| **E** | Export                 | E1, E2          | — |
| **F** | Window management      | F1, F2, F3      | F2 unblocks F3 (need Quit path before trapping close) |

**Recommended order:** A1 → A2 → **stop for review** → B1 → B2 → **stop for review** → C1 → C2 → C3 → C4 → **stop for review** → D1 → D2 → **stop for review** → E1 → E2 → **stop for review** → F1 → F2 → F3 → final review.

Phase F is independent of A–E and can also run in parallel if a separate implementer takes it. The "stop for review" before F still applies.

Stop-for-review can also happen at the end of any individual milestone if the implementer is uncertain — preferred over silent guessing.

---

## 3. Phase A — Storage foundation

### A1. Create `backend/notes.py` with SQLite schema and CRUD helpers

**Goal:** Introduce a new `notes` module that owns the SQLite database, exposes a small Python API (`create`, `get`, `list`, `delete`, `search`), and handles schema setup / migrations.

**Motivation:** Mirror the role of `chats.py` and `memory.py` — one module per data type, all storage details encapsulated. Avoid scattering SQL strings across `server.py`.

**Affected files:**
- New: `backend/notes.py`
- `backend/requirements.txt` — no change (SQLite is stdlib).

**Database location:** `Path.home() / ".local" / "share" / "logos" / "notes.db"`. Create parents if missing. The path mirrors `chats.py` and `memory.py`.

**Schema:**

```sql
CREATE TABLE IF NOT EXISTS notes (
  id            TEXT PRIMARY KEY,        -- uuid4 hex (no dashes)
  created_at    TEXT NOT NULL,           -- ISO8601 with seconds, with tz offset
  chat_id       TEXT,                    -- nullable; chat may be deleted later
  chat_title    TEXT,                    -- snapshot at capture time
  user_message  TEXT NOT NULL,           -- the prompt that produced this answer
  assistant_message TEXT NOT NULL,       -- the answer (full markdown)
  sources_json  TEXT,                    -- JSON array of {title, url, category}; '[]' if none
  model         TEXT                     -- the model name at capture time
);

CREATE INDEX IF NOT EXISTS notes_created_at_idx ON notes(created_at DESC);

CREATE VIRTUAL TABLE IF NOT EXISTS notes_fts USING fts5(
  user_message, assistant_message, chat_title,
  content='notes', content_rowid='rowid',
  tokenize="unicode61 remove_diacritics 2"
);

CREATE TRIGGER IF NOT EXISTS notes_ai AFTER INSERT ON notes BEGIN
  INSERT INTO notes_fts(rowid, user_message, assistant_message, chat_title)
    VALUES (new.rowid, new.user_message, new.assistant_message, new.chat_title);
END;
CREATE TRIGGER IF NOT EXISTS notes_ad AFTER DELETE ON notes BEGIN
  INSERT INTO notes_fts(notes_fts, rowid, user_message, assistant_message, chat_title)
    VALUES('delete', old.rowid, old.user_message, old.assistant_message, old.chat_title);
END;
```

`remove_diacritics 2` is critical: it lets the user search `λογος` and match `λόγος`, `Λόγος`, `λογοσ`, etc. Verify on a real Greek note in the acceptance step.

**Python API (function signatures only — implementer decides bodies):**

```python
def create(*, user_message: str, assistant_message: str,
           sources: list[dict] | None, chat_id: str | None,
           chat_title: str | None, model: str | None) -> dict
def get(note_id: str) -> dict | None
def list_all(limit: int = 500, offset: int = 0) -> list[dict]
def delete(note_id: str) -> bool
def search(query: str, limit: int = 200) -> list[dict]
```

`list_all` and `search` return note rows ordered by `created_at DESC`. Search uses the FTS5 `MATCH` operator; sanitize the query to avoid syntax errors (FTS5 is picky about special characters). The simplest safe approach: wrap each word in double quotes and AND them together — `'"foo" "bar"'`. Document this in the module docstring.

Each returned dict has the columns plus a `snippet` field for list views (~200 chars of `assistant_message`, ending at a word boundary, with `…` if truncated).

**Concurrency:** the existing modules use a single in-process Flask app. Use `sqlite3.connect(..., check_same_thread=False)` and a module-level `threading.Lock()` around writes. Reads can run lock-free under the default isolation. Pattern: same shape as `memory.py`'s `_lock`.

**ID safety:** the public `delete` and `get` take a `note_id`; validate it with the same regex pattern as `chats.is_valid_id` (`^[A-Za-z0-9_-]{1,80}$`). Reject anything else.

**Acceptance criteria:**
- Importing `notes` from a fresh Python shell creates `~/.local/share/logos/notes.db` with the four tables/triggers above.
- `notes.create(...)` returns a dict with a new uuid `id`, and `notes.get(id)` returns the same data.
- `notes.list_all()` returns newest first.
- `notes.search("λογος")` returns a note whose body contains `λόγος` (diacritic-insensitive match — this is the FTS5 unicode61 + `remove_diacritics 2` proof).
- `notes.delete(id)` removes the row from `notes` AND from `notes_fts` (verify by querying both directly).
- Calling any function with an invalid id returns `None` / `False` cleanly, never crashes.

**Risks / open questions:**
- **FTS5 availability.** SQLite ships with FTS5 in all modern Linux distros, but if Logos ever runs on an environment without it, `CREATE VIRTUAL TABLE` will fail. Wrap schema creation in a try/except that logs a warning and falls back to a non-search-enabled mode (search returns LIKE-based results from the main table). Document this fallback in the module.
- **Snippet generation.** Naive `body[:200]` can cut a Greek combining mark mid-character. Prefer `textwrap.shorten` with `width=200, placeholder="…"`, which respects word boundaries and full Unicode characters.
- **Schema migrations beyond v1.4.0.** If the schema ever changes, the implementer in that future cycle adds an `ALTER TABLE` block guarded by a `user_version` pragma check. Not relevant for the v1.4.0 cut.

**Complexity:** M.

---

### A2. Expose `/api/notes` endpoints in `server.py`

**Goal:** REST endpoints over the `notes` module so the frontend can interact with notes from the browser/pywebview context. Mirror the URL shape of `/api/chats` and `/api/memory`.

**Affected files:**
- `backend/server.py` — add routes, no changes elsewhere.

**Routes:**

| Method | Path | Body | Returns |
|--------|------|------|---------|
| `GET`    | `/api/notes`                 | —                            | `{"notes": [<note row + snippet>, ...]}` |
| `GET`    | `/api/notes/<note_id>`       | —                            | full note dict, or 404 |
| `POST`   | `/api/notes`                 | `{user_message, assistant_message, sources?, chat_id?, chat_title?, model?}` | created note dict (201), or 400 |
| `DELETE` | `/api/notes/<note_id>`       | —                            | `{"deleted": true}` or 404 |
| `GET`    | `/api/notes/search?q=<text>` | —                            | `{"notes": [...]}` (empty list if `q` is blank) |

Export endpoints come in Phase E. Don't add them here — they need PDF logic that doesn't exist yet.

**Request validation:**
- `POST /api/notes`: require non-empty strings for `user_message` and `assistant_message`. Reject `sources` if it's not a list of dicts. Cap `user_message` at 50 KB and `assistant_message` at 500 KB (generous; chat messages don't get that long even with attachments).
- `chat_id`, when present, must match `chats.is_valid_id`. If it doesn't, store `None` for `chat_id` rather than rejecting the whole request — the rest of the note is still useful.

**Error model:** all failures return `{"error": "<reason>"}` with appropriate HTTP status. Same shape as the existing `/api/memory` endpoints.

**Acceptance criteria:**
- `curl -X POST http://127.0.0.1:<port>/api/notes -d '{"user_message":"hi","assistant_message":"hello"}'` creates a note; subsequent `GET /api/notes` lists it.
- `GET /api/notes/<id>` returns 404 for unknown ids.
- `GET /api/notes/search?q=hello` returns the note above; `?q=ολα` matches nothing (because the note body is English) — this also confirms the search code path runs without errors on Greek queries.
- `DELETE /api/notes/<id>` removes the note; subsequent `GET` returns 404.
- A POST with a missing required field returns 400, not 500.

**Risks / open questions:**
- **Pagination.** With `LIMIT 500` we don't paginate. If a user accumulates >500 notes the UI shows the most recent 500 only. Decision: defer pagination to v1.5+; document the cap in the response (`{"notes": [...], "truncated": true}` when the limit hits).
- **CORS / auth.** Logos runs the Flask backend on localhost behind pywebview. Same trust model as existing endpoints — no auth, CORS already configured. No change needed.

**Complexity:** S.

---

## 4. Phase B — Take-Note UX

### B1. Add "Take note" button under each assistant bubble

**Goal:** A user can click `📌 Take note` under any finished assistant message and the note is persisted to the SQLite DB via `POST /api/notes`. The button flashes `✓ Noted` for ~1.2s on success.

**Affected files:**
- `frontend/app.js` — extend `attachMessageActions(msgEl)` (currently at ~line 203). It already builds `⎘ Copy` and `↻ Regenerate`; add a third button.
- `frontend/style.css` — no new classes; reuse `.msg-action-btn` and `.msg-action-btn.flash`.

**Change pattern:**

In `attachMessageActions`, after the regenerate button is created, build a `noteBtn` with class `msg-action-btn`, text `📌 Take note`. Its `onclick`:

1. Determine the assistant's content via `getAssistantContentFor(msgEl)` (existing helper).
2. Look up the *immediately preceding user message* in `conversationHistory` by matching the same indexing technique used in `getAssistantContentFor` — walk forward through `conversationHistory`, count assistants to find the index of `msgEl`, then take the most recent `user` message before that assistant.
3. Look up sources from the same assistant entry in `conversationHistory` (the `sources` field, which is the slimmed `{title, url, category}` shape).
4. POST to `/api/notes` with `{user_message, assistant_message, sources, chat_id: currentChatId, chat_title: currentChatTitle, model: currentModel}`. The variables `currentChatId`, `currentChatTitle`, `currentModel` already exist in `app.js` (or are easy to derive — implementer checks and uses what's there; if a name is missing, add a `getCurrentChat*()` accessor rather than expanding globals).
5. On 201: button text → `✓ Noted`, add `.flash` class for 1.2s, then restore.
6. On error: button text → `✗ Failed` for 1.2s, then restore. Log details to console. **Never** alert/popup.

**Important — fail-safe (P3):** if the POST fails (server down, schema not migrated, etc.) the assistant message remains in the chat, the chat history stays intact, and the user can keep chatting. No exceptions propagate.

**Acceptance criteria:**
- After a chat exchange, hovering an assistant message reveals all three action buttons. `📌 Take note` is the rightmost.
- Clicking the button saves a note (verify by `GET /api/notes` returning it).
- The note's `user_message` is the user's prompt that immediately preceded this assistant response, **not** the most recent user message in the chat (matters when the user clicks Take Note on an older message in a long conversation).
- The note's `sources` is exactly the `[{title, url, category}, ...]` array that the assistant message has — empty when the response wasn't search/notebook-grounded.
- Clicking Take Note twice on the same message creates two notes. (No dedup in v1.4.0 — keep it simple. Document this in the user-visible README in B2 or accept as known behavior.)
- Stopping the Flask backend mid-session and clicking Take Note shows `✗ Failed`, the chat is otherwise unaffected, no console exception bubbles up.

**Risks / open questions:**
- **Streaming partial messages.** The user could click Take Note while the assistant is still streaming. Decision: **disable the button while the message is mid-stream**. The existing `isStreaming` global controls input lock; reuse it. Show the button as `disabled` (CSS opacity 0.4, cursor not-allowed) when `isStreaming === true` AND `msgEl` is the last assistant element. Re-enable when streaming completes (the `done` event handler in `streamResponse` already triggers `attachMessageActions`; ensure the button picks up the fresh state).
- **Markdown vs plain text in `assistant_message`.** The on-screen DOM contains rendered HTML; `conversationHistory[i].content` contains the markdown source. Store the **markdown source**, not the rendered HTML. This keeps notes portable and re-renderable.

**Complexity:** S.

---

### B2. Document the feature in `developers.md` and `README.md`

**Goal:** Update the docs so the next architect-coder cycle has a single source of truth for the notes feature.

**Affected files:**
- `developers.md` — add a new section "Notes" describing the storage model, endpoints, and frontend integration.
- `README.md` — add a one-line bullet under features: "Take notes on any answer; search and export them later."

**Acceptance criteria:**
- `developers.md` has a "Notes" section that explains: SQLite file path, schema, endpoints, where Take-Note button lives, where the panel lives, and the fail-safe behavior.
- `README.md` mentions the feature in the features list.
- No prompt changes (P1).

**Complexity:** S.

---

## 5. Phase C — Notes panel + viewer

### C1. Add a left-anchored notes drawer with toggle

**Goal:** Add `#notes-sidebar` to `index.html`, mirroring the chat-history sidebar's behavior but anchored on the left edge. A new header button (`🗒 Notes`) toggles it. Closed by default.

**Affected files:**
- `frontend/index.html` — add the `<aside>` markup and the toggle button.
- `frontend/style.css` — add styles, mirror `#sidebar` rules with `left: 0` and `translateX(-100%)`.
- `frontend/app.js` — add open/close handlers; do **not** fetch notes yet (that's C2).

**HTML pattern (paste in index.html, immediately after the existing `<aside id="sidebar">` block):**

```html
<aside id="notes-sidebar" class="closed">
  <div id="notes-sidebar-header">
    <span>Notes</span>
    <button id="btn-notes-close" title="Close">✕</button>
  </div>
  <div id="notes-search-row">
    <input id="notes-search" type="search" placeholder="Search notes…" autocomplete="off" />
  </div>
  <div id="notes-list"></div>
</aside>
```

**Toggle button:** add to `#header-right` in `index.html` next to the existing buttons. Use `🗒` (clipboard) or `📝` — implementer picks the one that visually balances best with `⊕ ⚙ ☰`.

**CSS pattern (in `style.css`):**

```css
#notes-sidebar {
  position: fixed;
  top: 0;
  left: 0;
  width: 320px;
  height: 100vh;
  background: var(--bg2);
  border-right: 1px solid var(--border);
  z-index: 50;
  display: flex;
  flex-direction: column;
  transform: translateX(-100%);
  transition: transform 0.2s ease;
}
#notes-sidebar.open { transform: translateX(0); }
```

…then mirror `#sidebar-header`, `#chat-list` rules into `#notes-sidebar-header`, `#notes-list`. Reuse `var(--bg2)`, `var(--border)`, etc. — no new colors.

**JS pattern:** in `app.js`, wire `btn-notes-toggle` and `btn-notes-close` analogously to the existing chat sidebar handlers. The drawer's open/closed state can persist via `localStorage.getItem("notes-sidebar-open")` — match whatever the existing chat sidebar does (check first; if chat sidebar doesn't persist, don't persist notes sidebar either, to stay consistent).

**Acceptance criteria:**
- Clicking `🗒` in the header opens the left drawer with a 200ms slide; clicking `✕` or the toggle again closes it.
- Drawer doesn't overlap the chat input area visually when open (z-index, layout sanity check).
- Drawer renders correctly when the chat-history sidebar is ALSO open on the right at the same time.
- Search input and notes list are visible and empty (no data wired yet — that's C2).

**Risks / open questions:**
- **Mobile / narrow window.** Logos is a desktop app via pywebview; not optimizing for narrow viewports. Don't add responsive breakpoints unless the existing sidebar has them.
- **Keyboard shortcut.** Should `Ctrl+N` open the notes drawer? Decision: **no, deferred**. `Ctrl+N` is conventionally "new" in many apps and conflicts with the existing `New Chat` action. Don't bind any global shortcut in v1.4.0.

**Complexity:** M.

---

### C2. Render the notes list

**Goal:** On drawer open, fetch `/api/notes` and render the list. Each item shows: the snippet (first ~140 chars of the assistant reply), the originating chat title (small, muted), and the creation date (formatted as `YYYY-MM-DD HH:MM`).

**Affected files:**
- `frontend/app.js` — new function `loadNotes()` and `renderNotesList(notes)`.
- `frontend/style.css` — add `.note-item`, `.note-snippet`, `.note-meta`.

**Behavior:**
- `loadNotes()` is called when the drawer opens AND once per Take-Note success (so the list stays fresh without a manual reload).
- If `/api/notes` returns `truncated: true`, show a small muted line at the bottom: "Showing latest 500 notes."
- Empty state: when there are zero notes, show a centered placeholder ("No notes yet. Click 📌 Take note under any answer to save it.").

**Acceptance criteria:**
- Opening the drawer after creating 3 notes shows 3 items, newest first.
- Clicking Take Note from the chat updates the drawer list within ~300ms without manual reload.
- Empty state renders correctly on first launch.
- Date is formatted in the user's local timezone.

**Risks / open questions:**
- **Snippet length on Greek text.** A 140-char snippet may end mid-word — acceptable, but use `textwrap`-style word-boundary truncation if a server-side `snippet` field is provided (per A1's plan). The frontend trusts the snippet from the server.

**Complexity:** M.

---

### C3. Note detail view

**Goal:** Clicking a note in the list expands or opens a detail view showing the full Q + A + sources. The detail view has `Delete` and `Export ▾` controls (Export is implemented in Phase E; in C3 the button exists but is disabled or hidden behind a feature flag).

**Affected files:**
- `frontend/app.js` — new `openNoteDetail(noteId)` and `renderNoteDetail(note)`.
- `frontend/index.html` — add a `<div id="note-detail-overlay" class="hidden">` modal scaffold next to the existing `#image-overlay` and `#settings-overlay`.
- `frontend/style.css` — modal styles mirroring the settings overlay (same modal pattern, different content).

**UX choice — modal vs inline:** the implementer should use a **modal** (centered, dimmed background, ESC to close) because:
- It reuses the existing overlay pattern (consistent with settings/image).
- The notes drawer is narrow (320px); expanding inline gets cramped.
- The note's markdown body can be long — a full-width modal renders it cleanly.

**Modal content layout:**

```
┌─────────────────────────────────────────┐
│ Note details                        ✕   │  header
├─────────────────────────────────────────┤
│ 2026-05-23 14:22 · gemma3:12b           │
│ From chat: "..."                        │
│                                         │
│ Question                                │
│ <user_message as markdown>              │
│                                         │
│ Answer                                  │
│ <assistant_message as markdown>         │
│                                         │
│ Sources                                 │
│ 1. [title] (url)                        │
│ 2. ...                                  │
├─────────────────────────────────────────┤
│ [🗑 Delete]      [⬇ Export ▾]           │  footer
└─────────────────────────────────────────┘
```

Render `assistant_message` and `user_message` using the existing `marked` library — it's already loaded for chat rendering. Verify code blocks and lists render correctly.

**Acceptance criteria:**
- Clicking a note item opens the modal with the note's full content.
- ESC and `✕` both close it.
- Sources render as a numbered list with clickable links that open in the user's browser (or pywebview's external handler — match the existing behavior of source links in chat messages).
- The body renders markdown identically to how it appeared in the chat (re-uses the same `marked` pipeline; if a renderer config exists, share it).

**Risks / open questions:**
- **Long bodies.** Some notes will have multi-thousand-word bodies. The modal must scroll internally, not push the page. Confirm with a 5000-word test note.
- **Re-opening the same note.** Clicking the same item should re-open (idempotent) rather than toggle-close. Match the settings overlay's behavior.

**Complexity:** M.

---

### C4. Delete a note (with confirmation)

**Goal:** The `🗑 Delete` button in the detail modal removes the note via `DELETE /api/notes/<id>`. A `confirm()` dialog gates the action ("Delete this note? This cannot be undone.").

**Affected files:**
- `frontend/app.js` — wire the Delete button; on success, close the modal and reload the list.

**Acceptance criteria:**
- Delete prompts confirmation; cancel does nothing.
- Confirming removes the note from the list within ~300ms and closes the modal.
- The note is verifiably gone from `~/.local/share/logos/notes.db` (`SELECT count(*) FROM notes WHERE id = ?` returns 0).

**Risks / open questions:**
- **Undo.** No undo. The confirmation dialog is the only safeguard. This is intentional for v1.4.0 (P7).

**Complexity:** S.

---

## 6. Phase D — Search

### D1. Wire the search input to `/api/notes/search`

**Goal:** The search input at the top of the drawer filters the list as the user types. Debounce input by ~200ms to avoid hammering the backend.

**Affected files:**
- `frontend/app.js` — input event listener with debounce; calls `/api/notes/search?q=<text>` or `/api/notes` when the input is empty.

**Behavior:**
- Empty input → show the full list.
- Non-empty input → call search endpoint; replace list contents with results.
- A spinner / "Searching…" line is *not* required (search will be sub-100ms on any realistic note count).
- An empty result set shows the placeholder: "No notes match \"<query>\"."

**Acceptance criteria:**
- Typing `λογος` matches notes containing `λόγος` (diacritic-insensitive — proves A1's `remove_diacritics 2` works end-to-end).
- Typing a multi-word phrase like `linux server` matches notes containing both words (any order).
- Clearing the input restores the full list.
- Backspacing quickly through a long query doesn't fire 10 requests — debounce holds firm.

**Risks / open questions:**
- **FTS5 syntax injection.** A raw `'` or `"` from the user can break the FTS5 query parser. The server (in A1's `notes.search`) already sanitizes by quoting each word — verify edge cases: empty string, all-whitespace, `"` only, very long input (>500 chars; truncate server-side).

**Complexity:** M.

---

### D2. Highlight matched terms in results (optional)

**Goal:** In each note item in the list, visually highlight the matching substrings.

**Affected files:**
- `frontend/app.js` — wrap matches in `<mark>` tags inside the snippet before injecting it.
- `frontend/style.css` — style `.note-item mark` (subtle, non-distracting, e.g. `background: var(--accent2); color: var(--bg);`).

**Acceptance criteria:**
- Searching `λογος` highlights `λόγος` inside snippets.
- HTML escaping is correct — `<script>` in a note body never executes.

**Risks / open questions:**
- **Skip if it's tricky.** Diacritic-insensitive substring highlighting in JavaScript is non-trivial (the match index in normalized text doesn't equal the index in the original). If it takes more than ~1 hour, drop the milestone and ship D1 alone. Flag for architect review.

**Complexity:** S (if you skip diacritic edge cases) / M (if you don't).

---

## 7. Phase E — Export

### E1. Export note as `.txt`

**Goal:** Add `GET /api/notes/<id>/export?fmt=txt` that returns the note as a plain-text file download. The frontend's `Export ▾` menu has a `Text (.txt)` option that triggers the download.

**Affected files:**
- `backend/server.py` — new route.
- `backend/notes.py` — new function `render_txt(note: dict) -> str` that produces the plain-text rendering. Pure function, easy to test.
- `frontend/app.js` — open the export menu when the `Export ▾` button is clicked; on `Text` selection, navigate to the download URL (browsers auto-download from `Content-Disposition: attachment`).

**TXT layout:**

```
Logos Note
Created: 2026-05-23 14:22 (+03:00)
Model: gemma3:12b
From chat: "Σχεδιασμός roadmap v1.4"

Question
--------
<user_message>

Answer
------
<assistant_message>

Sources
-------
[1] <title>
    <url>
[2] <title>
    <url>
```

Use a UTF-8 BOM at the start of the file so Notepad on Windows renders Greek correctly. The `Content-Disposition` filename: `logos-note-<short-id>-<YYYY-MM-DD>.txt`.

**Acceptance criteria:**
- `curl http://127.0.0.1:<port>/api/notes/<id>/export?fmt=txt -o note.txt` produces a UTF-8 file whose contents match the layout above.
- Greek text round-trips intact through the download.
- Frontend export menu's "Text" option downloads the same file with a sensible filename.
- Exporting a non-existent id returns 404.

**Risks / open questions:**
- **Code blocks in markdown.** TXT export strips markdown markers? Or keeps them? **Decision (architect):** keep them as-is. Notes are markdown source; the user can re-paste anywhere and re-render. Don't reinvent a markdown-to-text transformer.

**Complexity:** S.

---

### E2. Export note as `.pdf`

**Goal:** Add `GET /api/notes/<id>/export?fmt=pdf` that returns the note as a styled PDF. The frontend's `Export ▾` menu has a `PDF (.pdf)` option.

**Affected files:**
- `backend/server.py` — extend the export route.
- `backend/notes.py` — new function `render_pdf(note: dict) -> bytes`.
- `backend/requirements.txt` — add **`fpdf2`** (see analysis below).
- Bundle a Unicode-capable TTF font (one file, ~300 KB) under `backend/fonts/` so Greek renders. Recommended: **DejaVu Sans** (free, ships in most distros under `/usr/share/fonts/truetype/dejavu/`). Vendoring it inside the app makes the .deb self-contained.

**PDF library trade-off:**

| Lib       | Size  | System deps | Unicode | License | Verdict |
|-----------|-------|-------------|---------|---------|---------|
| `reportlab`  | ~5 MB | None | Yes (with TTF) | BSD-style | Heavyweight, mature, overkill |
| `fpdf2`      | ~300 KB | None | Yes (with TTF, via `add_font`) | LGPL | **Recommended** — small, pure Python |
| `weasyprint` | huge | cairo, pango | Excellent (full CSS) | BSD | Best output but unacceptable system deps for a .deb |
| Browser print via pywebview | 0 KB | — | Yes | — | Cannot trigger from server endpoint; only works on user click |

**Per P6** (minimal dependencies), `fpdf2` is the right choice: smallest single new dep, no system libs, pure Python. Note this is the **one** new dep this feature adds.

**PDF layout** (kept simple — this is v1, fancy typography deferred):

- Letter-size page, 1-inch margins.
- Header: app name (small, top-right): "Logos · Notes"
- Title: "Note from <chat title>" (16pt bold)
- Meta block: "Created: …  ·  Model: …" (10pt muted)
- "Question" heading (12pt bold), then the question body (11pt).
- "Answer" heading (12pt bold), then the answer body (11pt). Naive markdown handling — preserve line breaks and lists, don't try to render inline emphasis or code highlighting.
- "Sources" heading (12pt bold), then a numbered list with title + URL (URL clickable via `fpdf2`'s link support).
- Page numbers in footer: "page X of Y" (10pt muted, centered).

**Acceptance criteria:**
- `curl http://127.0.0.1:<port>/api/notes/<id>/export?fmt=pdf -o note.pdf` produces a valid PDF that opens in any reader.
- Greek text renders correctly (test specifically: `λόγος`, `Όλα` — accented forms must show, not boxes).
- A multi-page note paginates correctly with page numbers.
- Source URLs are clickable in the PDF.
- File size is reasonable (<200 KB for a normal-length note).
- The .deb build (`build_deb.sh`) still produces a clean package with the new dep and the bundled font.

**Risks / open questions:**
- **Font fallback.** If DejaVu isn't found at runtime, fall back to fpdf2's built-in core fonts (Helvetica), which lack Greek glyphs — better to **ship the TTF in the package** and fail loudly if it's missing. The implementer adds a startup check in `notes.py` that logs a warning if the font file is absent.
- **License compatibility.** fpdf2 is LGPL — dynamic linking is fine; we just import it. DejaVu Sans is a permissive bitstream license. Logos itself is under its existing license (check `LICENSE`); confirm no conflict. If LGPL is a concern, the alternative is reportlab (BSD) at 15× the size.
- **Page break inside a code block.** fpdf2 doesn't preserve fixed-width formatting unless you switch to a mono font for the block. v1.4.0 punts: code blocks render in the same font as body text, in a slightly indented gray block. Document as known limitation.

**Complexity:** M.

---

## 8. Phase F — Window management

This phase addresses two user-reported friction points in v1.3.0:

1. **Launching from the desktop icon spawns a new instance** instead of focusing the existing one — every click of the .desktop file forks another `python3 app.py`, complete with its own Flask backend, fighting over the same port. The user wanted a single-instance app where the icon brings the existing window forward.
2. **Closing the window quits the app entirely.** The user wants the close (X) button to **minimize the window**, keep the app running, and offer a way to actually quit when truly needed.

The user chose a **system tray icon with a Quit menu** as the Quit affordance (over Ctrl+Q + Settings). That decision pulls in `pystray` and `Pillow`. See F2 risks.

### F1. Single-instance enforcement via abstract UNIX socket

**Goal:** When `logos` is launched while an instance is already running, the new process detects this, sends a "show" signal to the existing instance, and exits. The existing instance receives the signal and raises/restores its window. Re-clicking the .desktop icon no longer spawns a second copy.

**Affected files:**
- `app.py` — main launcher logic.
- `build_deb.sh` — no change.
- `backend/requirements.txt` — no change (uses stdlib `socket`).

**Mechanism:** abstract UNIX socket (Linux-specific, fine since the .deb only targets Linux). Abstract sockets have a leading null byte and are auto-cleaned by the kernel when the holding process dies — no stale lock files to manage.

```python
SINGLE_INSTANCE_ADDR = "\0logos-single-instance"

def acquire_lock_or_signal() -> socket.socket | None:
    """Bind the abstract socket. Return listener if we're first; None if another
    instance is running (after asking it to surface)."""
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        s.bind(SINGLE_INSTANCE_ADDR)
        s.listen(1)
        return s
    except OSError:
        # Already running — send 'show' to the existing instance and bow out.
        c = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            c.connect(SINGLE_INSTANCE_ADDR)
            c.sendall(b"show\n")
        except OSError:
            pass
        finally:
            c.close()
        return None
```

Call this **at the very top of `main()`**, before Flask thread, before pywebview window creation. If it returns `None`, exit cleanly with status 0.

If it returns a listener socket, spawn a daemon thread that loops on `listener.accept()`, reads up to 256 bytes, and if the payload starts with `b"show"`, calls `webview.windows[0].show(); webview.windows[0].restore()`. Wrap in try/except — never let this thread die from a bad payload.

**Acceptance criteria:**
- Launch Logos. Window appears.
- Run `/usr/bin/logos` from a terminal while the first is still running. The second invocation prints nothing significant and exits within ~200 ms with status 0.
- The first window comes to the foreground and (if minimized) is restored.
- Kill the running instance with `kill -9`. Relaunch — works normally; no stale-lock error.
- `ss -xa | grep logos-single-instance` shows exactly one bound socket while the app is running, zero after it exits.

**Risks / open questions:**
- **pywebview thread safety.** Calling `window.show()` and `window.restore()` from a non-main thread may need to be marshalled through `webview` properly. On GTK/Qt backends, pywebview generally exposes these methods as thread-safe, but verify on first run. If marshalling is required, use `webview.create_window`'s event system or a small queue read by the main thread.
- **Race on first launch.** Two desktop double-clicks within ~50 ms could both pass the bind check if the first hasn't called `listen` yet. Acceptable race for v1.4.0 — the worst case is a brief flash, not data loss. Document and move on.
- **Headless / non-X environments.** If someone runs the app over SSH without a display, the show/restore calls fail silently. Fine; this is a desktop app, not a CLI tool.

**Complexity:** S.

---

### F2. System tray icon with Show / Quit menu

**Goal:** Add a persistent tray icon (using the existing Logos icon). Right-clicking it shows a menu: **Show** (restores the window) and **Quit** (truly exits the process and the tray icon). Left-clicking the icon also shows the window.

**Affected files:**
- `app.py` — tray-thread setup + lifecycle.
- `backend/requirements.txt` — add `pystray` and `Pillow`.
- `build_deb.sh` — no change (just picks up the new deps).
- Optional: bundle the existing `icons/logos-32.png` (already in repo) as the tray icon source.

**Library choice — pystray:**

| Option | Pros | Cons |
|--------|------|------|
| `pystray` + `Pillow` | Cross-DE on Linux (via AppIndicator or GTK backend), small API, well-maintained | ~5 MB combined (mostly Pillow) |
| Native PyGObject + AyatanaAppIndicator | Most "Linux-native" feel | Requires system libs at runtime, more setup, harder to package via pip |
| Qt tray via PyQt | Excellent UX | Massive dep (~50 MB) — rejected |

**Decision:** `pystray`. It's the smallest cross-DE choice that doesn't require system libraries (Pillow ships wheels; pystray is pure Python).

**Pattern (sketch):**

```python
import pystray
from PIL import Image

def build_tray(window):
    icon_path = Path(BASE_DIR) / "icons" / "logos-32.png"
    image = Image.open(icon_path)

    def on_show(icon, item):
        window.show()
        window.restore()

    def on_quit(icon, item):
        icon.stop()
        # Tell the main process to exit cleanly.
        webview.destroy_window()  # close all pywebview windows
        # Flask is a daemon thread; it'll die with the main process.
        os._exit(0)

    menu = pystray.Menu(
        pystray.MenuItem("Show Logos", on_show, default=True),  # default = left-click
        pystray.MenuItem("Quit", on_quit),
    )
    icon = pystray.Icon("Logos", image, "Logos", menu)
    threading.Thread(target=icon.run, daemon=True).start()
    return icon
```

Call `build_tray(window)` after `window = webview.create_window(...)` and before `webview.start(on_ready)`. The tray icon thread runs in parallel.

**Acceptance criteria:**
- Launch Logos. Tray icon appears in the system tray (top bar on GNOME, panel on KDE/XFCE).
- Right-clicking the tray icon shows a menu with "Show Logos" and "Quit".
- Left-clicking the tray icon raises the window (same as Show).
- Choosing Quit truly terminates the Python process. `ps aux | grep app.py` shows no leftover.
- The tray icon disappears on Quit.

**Risks / open questions:**
- **GNOME on Wayland.** GNOME removed legacy XEmbed tray support; users on stock GNOME may need the AppIndicator extension. pystray falls back to AppIndicator on Linux when available, but if the user's session lacks any tray host, the icon simply won't appear — the app must not crash and must remain controllable (the close button still minimizes, but they'd have no Quit affordance without the tray). **Mitigation:** If `icon.run()` raises or `icon.HAS_DEFAULT_ACTION` is False on this platform, fall back to wiring **Ctrl+Q** in the JS frontend as a backup Quit shortcut (call an `Api.quit()` method). Document this clearly in `README.md` under "System requirements / Tray".
- **Pillow dep size.** Pillow's manylinux wheel is ~3 MB. For the .deb, this is a meaningful percentage of total app size. Acceptable; flagged.
- **Tray icon color/contrast.** Logos's gold icon may be hard to see on a dark tray. The 32x32 PNG should be fine; if not, the implementer adjusts.

**Complexity:** M.

---

### F3. Trap the close button → minimize instead of quit

**Goal:** Clicking the window's X button (or the WM "close window" action) **minimizes** the window instead of terminating the app. The Flask backend keeps running. The user returns to the app via the tray icon, the .desktop launcher (which now signals the existing instance — F1), or the taskbar entry.

**Affected files:**
- `app.py` — wire `window.events.closing` to a handler that returns False after calling `window.minimize()`.

**Pattern:**

```python
def on_closing():
    window.minimize()
    return False  # cancel the close

window.events.closing += on_closing
```

The pywebview API for events varies slightly across versions. The implementer verifies the installed version and the exact subscription idiom (`window.events.closing += handler` vs `window.events.closing = handler`). If the event isn't available, fall back to `window.events.closed` to re-spawn (less ideal; flag).

**Acceptance criteria:**
- Click the X button. Window minimizes. Logos process keeps running. Flask still serves at `http://127.0.0.1:<port>`.
- Clicking the tray icon's "Show Logos" restores the window.
- Choosing "Quit" from the tray menu still exits cleanly (F2 path is unaffected).
- Re-launching the .desktop while minimized → F1 signal arrives → window restores.
- The "close window" keyboard shortcut for the WM (e.g. Alt+F4 on most Linux DEs) also goes through this handler — verify it minimizes, doesn't quit. (If a user really wants Alt+F4 to quit, that's what the tray's Quit is for.)

**Risks / open questions:**
- **WM diversity.** Some tiling WMs (i3, sway) don't render a title-bar X; close is purely keyboard or scripted. The `closing` event still fires. Should work, but flag for testing.
- **Update workflow.** When the user installs a new .deb, the running process will still be the old one. They must explicitly quit (tray → Quit) for the new code to load on next launch. Document this in `README.md` under "Updating".
- **Memory growth.** With the app always running, chat sessions and notes accumulate in memory. Logos's runtime memory is small (no model in-process), but if leaks emerge, they'll surface in v1.4.0. Add a small note in `developers.md` to keep an eye on the resident set after a few days uptime.

**Complexity:** S.

---



After E2, run this manual scenario to certify v1.4.0:

1. Launch Logos. Settings footer shows `Logos v1.4.0` (bump `backend/version.py`).
2. Start a new chat in Greek. Ask a question that triggers web search. Get a grounded answer with multiple sources.
3. Click `📌 Take note`. Confirm `✓ Noted` flash.
4. Open notes drawer (left). See one note with the snippet, chat title, and timestamp.
5. Click the note. Modal opens with full Q + A + sources. Source links are clickable.
6. Type a Greek word from the answer (without accents) in the search box. The note remains in the list.
7. Type a word that's not in the note. Empty-state placeholder appears.
8. Clear the search. List restores.
9. Click Export → Text. A `.txt` downloads, opens in any editor, Greek intact.
10. Click Export → PDF. A `.pdf` downloads, opens, Greek intact, source links clickable, page numbers present.
11. Click Delete, confirm. Note disappears. Modal closes. List shows empty-state.
12. Restart the app. Open notes drawer. Still empty. (Persistence proven by the previous note that was deleted; do the test again without deleting to prove storage across restarts.)
13. Inspect `~/.local/share/logos/notes.db` with `sqlite3` CLI. Confirm schema matches A1.
14. **Window mgmt** — minimize the window via the X button. Logos process still in `ps aux`. Flask still serving.
15. Launch Logos again from the .desktop launcher (or `/usr/bin/logos`). The existing window restores; no second process spawned.
16. Click the tray icon. Right-click → menu shows Show / Quit.
17. Right-click → Quit. Process terminates; tray icon disappears.

**Tag and release:**
- Commit message format matches the v1.3.0 cycle.
- After final review, tag `v1.4.0` annotated, push `main` + tag.

---

## 9. What this roadmap deliberately does NOT cover

Listed in P7 above. Repeated for emphasis since experienced implementers will be tempted:

- No note editing. (User chose against.)
- No tags or folders. (Defer to v1.5+.)
- No bulk export. (Defer.)
- No LLM-generated summaries / titles. (Defer.)
- No cloud sync. (Out of scope of Logos as a local-first app.)
- No re-engagement of an old note into a new chat as context. (Interesting future direction; not v1.4.)
- No notes-in-memory cross-pollination (e.g. auto-promote a note's content into `memory.json`). (Deliberately kept separate — memory facts are short and prompt-injected; notes are arbitrary-length and read on demand.)

---

## 10. For the architect — checkpoints

After each phase, the implementer pauses. The architect reviews:

- **End of Phase A:** Open a Python shell, exercise `notes.create / list / search / delete`. `curl` each endpoint. Confirm schema and FTS work.
- **End of Phase B:** Send a chat in the running app, click Take Note, confirm a row in the DB. Confirm the flash animation and the fail-safe (stop backend, click, no crash).
- **End of Phase C:** Visual review of the drawer, list, and modal. Tab key navigation works.
- **End of Phase D:** Search edge cases (Greek, multi-word, empty, special chars).
- **End of Phase E:** Open the .txt and .pdf in real readers. Confirm font rendering, source links, page numbers.
- **End of Phase F:** Exercise the full single-instance + minimize + tray + Quit lifecycle on the target Linux DE. Verify .desktop relaunch behavior and Quit path.

If any acceptance criterion fails, the phase is not complete. Roll back if necessary; do not paper over it.
