# Logos v1.5 — Obsidian Daily-Note Sync

> **Status:** Design pending architect review. **Authored:** 2026-05-23.
> **Target version:** v1.5.0
> **Owner:** Architect (you). Implementer: any LLM coder following this doc.

---

## How to use this document

Same workflow as the v1.4 roadmap. Read it fully, resolve open questions before coding, implement one milestone at a time, stop for review at the end of each phase.

---

## 0. Background — what we are building

After running Logos in daily use, the user wants their conversations to surface in their Obsidian Daily Note the next morning. Specifically: every morning, the previous day's chats should appear in `Daily Notes/YYYY-MM-DD.md` under a section called `## About Logos` (this section was previously named `## About Aya` and is being renamed as part of this feature).

This makes Logos a part of the user's note-taking rhythm: conversations don't die in a sidebar, they migrate into the user's permanent journaling system where they can be re-read, linked, and built upon.

**Concretely:**

1. A new backend module `backend/obsidian_sync.py` owns all Obsidian-related I/O.
2. A new endpoint `POST /api/obsidian/sync` runs the digest on demand. Body optionally specifies the target date; defaults to "yesterday".
3. New config keys for the vault path, daily-note path template, section header, and digest format.
4. A new "Obsidian" tab in Settings exposing these keys + a "Sync now" button.
5. (Optional, Phase B) Auto-sync on app launch — first launch of the day runs yesterday's digest silently.

**Storage:** No new database. Reads `~/.local/share/logos/chats/*.json` (already exists). Writes to the user's Obsidian vault on disk (path from config).

---

## 1. Design principles

P1–P8 from `roadmap-v1.4.md` continue to apply. Specific to this feature:

**P-O1. Idempotent writes.** Running the sync twice for the same day must not duplicate content. The `## About Logos` section gets the same body each time — replace-in-place, not append.

**P-O2. Vault is sacred — never destructive.** This feature only ever writes to one specific section of one specific file per day. It never touches other sections, other files, frontmatter, attachments, or the vault index. If the section header can't be located cleanly, the sync aborts with a logged warning rather than guessing.

**P-O3. Section header rename is automatic but reversible.** When syncing, if a daily note contains the legacy `## About Aya` heading but no `## About Logos`, rename it before writing. Log the rename. Never rename a section that has both (in case the user manually created both during the transition).

**P-O4. Empty days are skipped, not blanked.** If yesterday had zero chats, do nothing — don't write an empty section, don't remove an existing section. The user might have manually added content there.

**P-O5. No new heavyweight dependencies.** Stdlib only. Reading and writing markdown files is `pathlib.Path.read_text` / `write_text`. Parsing the section is a small regex.

**P-O6. Failures never affect chat.** This module runs in a background thread (when auto-triggered) or in a direct endpoint call (when manual). Either way, it never blocks the chat pipeline.

---

## 2. Open questions — REQUIRES ARCHITECT INPUT before Phase A

These are the 3 decisions I cannot make for you. I've put my **proposed defaults** in brackets so the implementer has something to build against if you confirm the defaults verbatim.

### Q1. Obsidian vault path
What is the absolute path to your Obsidian vault?
Proposed config default: `~/Documents/Obsidian Vault` (Obsidian's stock default).
**Decision needed:** [ ___________________________ ]

### Q2. Daily note path pattern (relative to vault root)
Common Obsidian Daily Notes plugin defaults:
- `Daily Notes/{date}.md`
- `Journal/{date}.md`
- `{date}.md` (root)
- Or a custom template like `Daily Notes/{year}/{month}/{date}.md`

The `{date}` placeholder is rendered as `YYYY-MM-DD` (no other formats supported in v1.5).

Proposed config default: `Daily Notes/{date}.md`
**Decision needed:** [ ___________________________ ]

### Q3. Digest format under `## About Logos`
Three options, from cheapest to most token-hungry:

**Option A — Titles only** (no LLM needed, instant):
```markdown
## About Logos

- 14:22 · [Σχεδιασμός roadmap v1.4](logos://chat/abc123)
- 16:05 · [Linux server market share](logos://chat/def456)
- 21:30 · [Hollow Knight όπλα](logos://chat/789abc)
```

**Option B — Titles + first user message** (no LLM, ~3 lines per chat):
```markdown
## About Logos

- **14:22 · Σχεδιασμός roadmap v1.4**
  > Θέλω να σχεδιάσουμε ένα roadmap για την έκδοση 1.4 με νέο feature Notes...

- **16:05 · Linux server market share**
  > Τι μερίδιο αγοράς έχει το Linux στους servers το 2026;
```

**Option C — LLM summary per chat** (~1 LLM call per chat, ~50–80 tokens output each):
```markdown
## About Logos

- **14:22 · Σχεδιασμός roadmap v1.4** — Σχεδιάσαμε το roadmap v1.4 με phases A–F: Notes feature (SQLite + FTS5), window management (tray, single-instance), και export σε TXT/PDF.

- **16:05 · Linux server market share** — Συζήτηση για το μερίδιο αγοράς του Linux στους servers το 2026. Δεν βρέθηκαν αξιόπιστες πηγές πέρα από Statcounter (~40% web servers).
```

Proposed config default: **Option B**. Reasoning: no LLM cost, more useful than just titles, doesn't bloat the daily note. Option C is a Phase B upgrade once user confirms they want richer summaries.

**Decision needed:** [ A / B / C ]

### Q4 (lighter — yes/no). Auto-trigger on app launch?
Should the sync run automatically the first time Logos launches each day (with a small status bar indicator), or strictly manual via the Settings button?

Proposed default: **manual only in v1.5.0**, auto-trigger in v1.5.1 once the manual path is proven. This avoids a surprising silent write on first install.

**Decision needed:** [ manual only / auto on launch ]

---

## 3. Phase overview

| Phase | Theme                          | Milestones | Blocks downstream? |
|-------|--------------------------------|------------|--------------------|
| **A** | Backend module + endpoint     | A1, A2     | Yes — B needs A    |
| **B** | Settings UI tab + Sync button | B1, B2     | —                  |
| **C** | Auto-trigger on launch (opt)  | C1         | Independent        |

**Recommended order:** A1 → A2 → review → B1 → B2 → review → (optionally) C1.

---

## 4. Phase A — Backend module + endpoint

### A1. Create `backend/obsidian_sync.py`

**Goal:** A self-contained module that, given a date, collects that day's chats, formats a digest, and updates the appropriate daily note. All Obsidian I/O lives here; nothing else in the codebase opens vault files.

**Affected files:**
- New: `backend/obsidian_sync.py`
- `backend/config.py` — add 4 new keys to DEFAULTS (see §6).
- `backend/requirements.txt` — no change (stdlib only).

**Public API:**

```python
def sync_date(target_date: date, *, dry_run: bool = False) -> dict:
    """Sync chats from target_date into the daily note for the day AFTER target_date.

    Args:
        target_date: The chat day to summarise (typically yesterday).
        dry_run: If True, compute what would be written and return it
                 without touching the vault.

    Returns:
        dict with keys:
          - ok: bool
          - target_date: ISO date string of the chats summarised
          - daily_note_date: ISO date string of the note that was/would-be updated
          - daily_note_path: absolute path to that note
          - chats_count: int
          - bytes_written: int (0 on dry_run)
          - renamed_legacy_header: bool (True if "About Aya" → "About Logos")
          - skipped_reason: str | None ("empty" / "vault_missing" / "config_incomplete" / None)
          - error: str | None
    """
```

Plus internal helpers (not part of public API):

```python
def _collect_chats_for_date(target_date: date) -> list[dict]
def _format_digest_titles(chats: list[dict]) -> str
def _format_digest_excerpts(chats: list[dict]) -> str         # Option B
def _format_digest_summaries(chats: list[dict]) -> str        # Option C, Phase B
def _resolve_daily_note_path(vault_path: Path, template: str, the_date: date) -> Path
def _update_section(file_path: Path, header: str, body: str) -> dict
```

**Section-update algorithm (the core logic):**

The daily note is a markdown file. We need to insert or replace one `##` section.

1. If the file doesn't exist: create it with just the header + body (no frontmatter).
2. If it exists, read it as text. Locate the section by exact header match (case-sensitive — Obsidian is case-sensitive).
3. **Legacy rename:** if `## About Logos` is not present but `## About Aya` is, replace the header text (in place, preserving surrounding whitespace) and log it. Treat the renamed section as the existing one.
4. If the section exists, find its body: everything from the line after the header to the next `##` heading (or EOF). Replace that body with the new body. Preserve the blank line after the header and before the next section.
5. If the section doesn't exist, append it to the end of the file with a leading blank line.

This is straightforward enough that a regex-free line-by-line scan is the cleanest implementation. The implementer must include a unit-test-style smoke test in the module's `if __name__ == "__main__":` block exercising all four cases above with temp files.

**Date semantics:**
- "Yesterday's chats" = chats whose `updated_at` (or `created_at` if updated_at missing) falls on `target_date` in the user's local timezone.
- The daily note we WRITE to is for `target_date + 1` (i.e., we summarise yesterday into today's note — the user sees yesterday's conversations when opening today's note).
- The endpoint may pass an explicit `target_date` to summarise any day, useful for backfill.

**Acceptance criteria:**
- Module imports without side effects (no I/O at import time).
- `sync_date(yesterday, dry_run=True)` on a setup with 3 chats yesterday returns `chats_count: 3` and a non-empty digest body.
- `sync_date(yesterday)` on the same setup writes to `<vault>/Daily Notes/YYYY-MM-DD.md` (today's note). Running it again produces identical output (idempotent).
- `sync_date(day_with_zero_chats)` returns `ok: True, skipped_reason: "empty"` and writes nothing.
- A note containing `## About Aya` gets its header renamed and its body replaced. The rest of the note is byte-identical.
- A note containing both `## About Aya` and `## About Logos` is NOT touched on the legacy header — only `## About Logos` is updated.
- Missing vault path → `ok: False, skipped_reason: "vault_missing"`. Empty/blank config keys → `skipped_reason: "config_incomplete"`.

**Risks / open questions:**
- **Timezone.** Logos already injects local TZ into the system prompt. Use `datetime.now().astimezone().tzinfo` for the local zone here as well.
- **Symlinks / non-vault folders.** No validation that the path is actually an Obsidian vault. If the user points at the wrong folder, we still write the file — at worst they get an orphan `.md` somewhere. Acceptable for v1.5.0.
- **Concurrent edits.** If the user has the daily note open in Obsidian during a sync, Obsidian may merge or warn. We write atomically: write to `<file>.tmp`, then `os.replace`. Obsidian's file watcher handles this correctly in current versions.
- **Markdown links to chats.** `logos://chat/<id>` is not a real URL scheme — Obsidian will render the text as a link but clicking it does nothing. Acceptable for v1.5.0; a future version could register a custom URL scheme handler. Alternative: plain text titles without links.

**Complexity:** M.

---

### A2. Expose `/api/obsidian/sync` in `server.py`

**Goal:** REST endpoint over `obsidian_sync.sync_date`. Default target is yesterday; optional explicit date in the request body.

**Affected files:**
- `backend/server.py` — add route, no changes elsewhere.

**Routes:**

| Method | Path | Body | Returns |
|--------|------|------|---------|
| `POST` | `/api/obsidian/sync` | `{date?: "YYYY-MM-DD", dry_run?: false}` | Result dict from `sync_date` |
| `GET`  | `/api/obsidian/preview` | `?date=YYYY-MM-DD` (optional) | Same as POST but always `dry_run: true` |

**Request validation:**
- If `date` is provided, parse as `%Y-%m-%d`; reject anything else with 400.
- If `date` is missing, use `(today - 1 day)` in local timezone.
- `dry_run` defaults to False on POST, always True on GET preview.

**Acceptance criteria:**
- `curl -X POST http://127.0.0.1:<port>/api/obsidian/sync` syncs yesterday into today's note.
- `curl http://127.0.0.1:<port>/api/obsidian/preview?date=2026-05-20` returns the formatted digest without writing.
- Invalid date format returns 400 with `{"error": "..."}`.
- Vault not configured returns 200 with `{"ok": false, "skipped_reason": "config_incomplete"}` — this is not an error, it's a state.

**Complexity:** S.

---

## 5. Phase B — Settings UI tab + Sync button

### B1. Add "Obsidian" Settings tab

**Goal:** A new tab in the settings overlay with four input fields (vault path, daily note template, section header, digest format) and one button ("Sync yesterday now"). Status of the last sync shown below the button.

**Affected files:**
- `frontend/index.html` — add the tab markup.
- `frontend/app.js` — wire form load/save and the sync button.
- `frontend/style.css` — no new patterns (reuse settings tab styles).

**Layout:**

```
Obsidian
─────────
Vault path           [ /home/.../Obsidian Vault          ] [ Browse… ]
Daily note path      [ Daily Notes/{date}.md             ]
Section header       [ ## About Logos                    ]
Digest format        ( ) Titles only
                     (•) Titles + first message
                     ( ) LLM summary per chat            (v1.5 ships A and B)

                     [ Sync yesterday now ]   [ Preview… ]
                     Last sync: 2026-05-23 09:14 · 3 chats · 412 bytes
```

The `Browse…` button uses pywebview's `create_file_dialog(FOLDER_DIALOG)` — same pattern as the existing file attach. The LLM-summary option is disabled with a tooltip "coming in v1.5.1".

**Acceptance criteria:**
- Opening Settings → Obsidian shows the four fields populated from config.
- Editing and saving updates `~/.config/logos/config.json`.
- "Browse…" opens a native folder picker (already supported by pywebview).
- "Preview…" opens a modal showing the markdown that WOULD be written (no actual write).
- "Sync yesterday now" calls the endpoint, updates the status line, and shows toast on completion / error.

**Complexity:** M.

---

### B2. Update `developers.md` and `README.md`

**Goal:** Document the feature. Section in `developers.md` covering: which module owns vault I/O, the config keys, the sync semantics (yesterday → today's note), the legacy header rename. One bullet in `README.md` features list.

**Affected files:**
- `developers.md`
- `README.md`

**Acceptance criteria:**
- `developers.md` has an "Obsidian sync" section.
- `README.md` lists the feature.

**Complexity:** S.

---

## 6. Config keys

Add to `DEFAULTS` in `backend/config.py`:

```python
"obsidian_vault_path": "",           # absolute path; "" disables the feature
"obsidian_daily_note_path": "Daily Notes/{date}.md",
"obsidian_section_header": "## About Logos",
"obsidian_digest_format": "excerpts",  # "titles" | "excerpts" (Phase A) | "summaries" (Phase B)
```

All four keys must merge cleanly with existing user configs (additive only; existing configs continue to load).

---

## 7. Phase C — Auto-trigger on launch (deferred unless Q4 = "auto")

### C1. Run sync on first launch of the day

**Goal:** On `app.py` startup, after the Flask thread is up and `webview.start` is about to be called, spawn a background thread that:

1. Reads the last-sync date from `~/.local/share/logos/obsidian_last_sync.txt` (creates if absent).
2. If `last_sync_date < today` AND `obsidian_vault_path` is non-empty, call `obsidian_sync.sync_date(yesterday)`.
3. Write today's date to the last-sync file regardless of result, so a failure isn't retried in a tight loop.

The thread is daemonic, never blocks startup, never propagates exceptions.

**Acceptance criteria:**
- Launch Logos. Yesterday's chats appear in today's daily note within a few seconds.
- Launch again in the same day — no second write (idempotent + skipped via last-sync file).
- Launch with an empty/unconfigured vault path — no action taken, no errors, last-sync file still updated.

**Complexity:** S.

---

## 8. Final acceptance scenario

After A2, run this manual check to certify v1.5.0:

1. Configure vault path and daily-note template in Settings.
2. Have at least 2 chats yesterday and 0 today (or use the explicit `date=` parameter).
3. Click "Sync yesterday now". Open the resulting daily note in Obsidian. Confirm the `## About Logos` section is present with both chats listed.
4. Click "Sync yesterday now" again. Open the note in Obsidian. The file is byte-identical (verify with `md5sum`).
5. Manually create `## About Aya` in a daily note for a different date. Sync that date. Confirm the header was renamed to `## About Logos` and the body updated.
6. Wipe the vault path. Click sync. Status line shows "config incomplete" — no error toast.

**Tag and release:**
- Commit format matches v1.3/v1.4 cycles.
- Bump `backend/version.py` to `1.5.0`.

---

## 9. What this roadmap deliberately does NOT cover

- LLM-generated chat summaries (Phase B candidate, not v1.5.0 unless Q3 = C).
- Cross-day backfill UI (the endpoint supports it via `date=`, but no UI in v1.5.0).
- Two-way sync (Obsidian → Logos is not in scope).
- Other note-taking apps (Roam, Logseq, Notion). The module is named `obsidian_sync` deliberately — generalising later means a new module, not parameterising this one.
- Frontmatter manipulation in daily notes (`date:`, `tags:`, etc.). We only touch one `##` section.
- The `logos://chat/<id>` URL scheme handler. Titles render as link-text only in v1.5.0.
