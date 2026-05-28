# Changelog

All notable changes to Logos are documented in this file.

## [1.6.0] — 2026-05-24

### Fixed: Foreign-script characters from search snippets contaminated model output (M13)

When Brave Search returned a result from a Cyrillic-script (or Arabic/CJK/etc.)
page, those characters were injected verbatim into the LLM context. The model
could reproduce them in its response despite the LANGUAGE_RULE prompt — because
the rule contained an exception for "source material in that language", which
the model interpreted as permission to quote snippets.

Fix in two layers (belt-and-suspenders):

- `backend/search_providers.py` — New `_clean_snippet(text)` helper using a
  compiled regex that strips runs of Cyrillic, Arabic, Hebrew, Devanagari,
  Thai, CJK, and Hangul characters from the `content` field of every search
  result before it reaches `format_as_context()`. Title and URL fields are
  left untouched. Multiple spaces left by the stripping are collapsed. Applied
  to all three provider backends (DDG, Brave, SearXNG) through the shared
  formatter.

- `backend/prompts.py` — `_LANGUAGE_RULE_TEMPLATE` rewritten to close the
  "source material" loophole: the model is now explicitly forbidden from
  reproducing non-Latin/Greek-script characters even when they appear in
  search snippets, with a narrow exception only for turns where the user
  explicitly requests translation into or quotation from that script.

### Fixed: Source links hijacked the embedded WebView (M12)

Clicking a `[N]` source link in chat — or any external link rendered inside a
note's detail view — used to navigate the embedded pywebview WebView itself
instead of opening the user's default browser. WebKit2GTK has no
window-open handler bound by default, so `target="_blank"` falls back to
in-place navigation and the Logos UI got replaced with the link target.
Google sign-in flows on linked sources became completely unreachable
because Google blocks OAuth inside embedded WebViews.

Fix in two parts, mirroring how M2 solved the notes-export silent failure:

- `app.py` — New `Api.open_external(url)` JS-bridge method. Validates that
  the URL parses as `http` or `https` with a non-empty netloc (so a
  compromised page can't ask us to launch `javascript:`, `file://`,
  `mailto:`, custom schemes, etc.), then calls `webbrowser.open(url,
  new=2)` to launch the user's actual default browser. Surfaces "no
  handler registered" instead of silently swallowing the failure.
- `frontend/app.js` — Installed a delegated capture-phase click handler on
  `document` that catches anchors with `http(s)` hrefs (`closest("a[href]")`,
  regex match), prevents default, and routes through `open_external`. Hash
  links, relative paths, `mailto:`, and dev-mode (no pywebview bridge) are
  all left alone.

### Fixed: No window opened at all on launch (M11)

After M10's diagnostic logs went in, the first terminal launch revealed
the real root cause that M5/M6/M10 were all chasing the symptoms of:

```
GLib-GIO-CRITICAL: g_application_run() cannot acquire the default main
    context because it is already acquired by another thread!
```

`main()` was calling `build_tray()` BEFORE `webview.start()`. pystray's
icon thread acquired the default GTK main context first, pywebview's
GApplication.run() couldn't acquire it, the main window was never
built, and `on_ready` raised `WebViewException: Main window failed to
start`. The "tray only" symptom from the previous report was exactly
this — pywebview never started, so the user only saw pystray.

Fix: defer `build_tray()` until inside `on_ready`, which runs only
after pywebview's GTK loop is up. Wrap it in try/except so a tray
failure can't abort the splash→UI transition. Revert `on_closing`
from `hide()` to `minimize()` — without a guaranteed tray, the
taskbar entry is the user's recovery path. Restoring hide-to-tray
is parked in M11's "Open" item for v1.6 follow-up.

### Fixed: Window only opened on first launch; subsequent launches were invisible (M10)

After M6 nominally fixed the "tray Show does nothing" bug, the symptom
came back on the user's Cinnamon/Muffin session: first launch opened
the window, but after X-close the tray icon stayed and no path —
neither relaunch from the menu nor "Show Logos" from the tray —
brought the window back.

`_surface_window()` was passing a Unix-epoch-ms timestamp to
`Gtk.Window.present_with_time()`. Modern WMs treat that as a stale
focus request and silently refuse to raise the window. The helper now
uses `Gtk.get_current_event_time()` (which returns `GDK_CURRENT_TIME`
when there's no event in flight — the canonical "bypass focus-stealing
prevention" signal), and follows up with a brief `set_keep_above`
flip as a belt-and-suspenders WM nudge. Diagnostic stderr logs were
added at every step so the next regression has a paper trail.

### Fixed: White-window NVIDIA bug on terminal launch (M9)

Launching Logos directly from a terminal (`python3 /usr/share/logos/app.py` or
`logos` from the shell, bypassing the `.desktop` Exec wrapping) skipped the
NVIDIA WebKit2GTK mitigations and produced a white window with `GBM-DRV error`
in stderr. The fix lives in two places now so every launch path is covered:

- `app.py` already sets `WEBKIT_DISABLE_DMABUF_RENDERER`,
  `WEBKIT_DISABLE_COMPOSITING_MODE`, `GSK_RENDERER=cairo`, and
  `LIBGL_ALWAYS_SOFTWARE` via `os.environ.setdefault` before importing
  `webview` (from M8).
- `/usr/bin/logos` shell wrapper is now rewritten by `tools/install-local.sh`
  to export the same env vars unconditionally. Belt-and-suspenders for any
  path that bypasses the .desktop file.

### Fixed: install-local.sh deployed only half the v1.6 fixes (M9)

The dev-sync script's `FILES` array was missing `backend/config.py`,
`backend/obsidian_sync.py`, and `backend/version.py`. Result: the Obsidian
emoji-header fix and the config migration shipped in dev but never landed in
`/usr/share/logos/`, leaving the installed tree in a mixed v1.5/v1.6 state.
The list is now complete and alphabetised.

### Fixed: Startup banner printed "Logos v? starting…" (M9)

`main()` was calling `cfg.load().get("version", "?")`, but `DEFAULTS` in
`config.py` never had a `"version"` key — the `?` fallback was the only
branch that ever ran. `app.py` now imports `VERSION` from `backend/version.py`
(the single source of truth used by the .deb packaging and the in-app footer)
and prints `f"Logos v{APP_VERSION} starting…"`.

### Changed

- `backend/version.py`: `1.5.0` → `1.6.0-dev`. Will be cut to `1.6.0` at
  release per the M-release checklist.
- `app.py`: New `from version import VERSION as APP_VERSION` import; banner
  print rewritten to use it.
- `tools/install-local.sh`: `FILES` list extended with the three missing
  modules; new idempotent block at the bottom rewrites `/usr/bin/logos`
  with the NVIDIA env-var prologue (no-op if the wrapper already has it).

### Fixed: Obsidian daily-note sync not working

The Obsidian integration (added in v1.5.0) was broken due to three issues:

1. **Wrong daily-note path in config** — The default `Daily Notes/{date}.md` did not
   match the user's actual Obsidian vault layout (`01 - Daily Notes/{date}.md`).
   The sync was writing to a non-existent folder, creating an orphan file at
   `~/Notes/Daily Notes/2026-05-24.md` instead of updating the real daily note at
   `~/Notes/01 - Daily Notes/2026-05-24.md`. The orphan file has been cleaned up.

2. **Legacy section header mismatch** — The config had `obsidian_section_header` set
   to `## About Aya` (the legacy name). Since the code's rename logic only triggers
   when the configured header is *absent* from the file, and the file contained
   `## 🤖 About Aya` (with emoji prefix), the section was never found or updated.
   The code now recognises both `## About Aya` and `## 🤖 About Aya` as legacy
   headers and auto-renames them to the configured header (`## About Logos`).

3. **Config migration** — On load, `config.py` now auto-upgrades
   `obsidian_section_header` from `## About Aya` or `## 🤖 About Aya` to
   `## About Logos`, matching the v1.5.0 roadmap spec.

### Changed

- `backend/obsidian_sync.py`: `LEGACY_HEADER` (single string) replaced with
  `LEGACY_HEADERS` (list) supporting emoji-prefixed variants. The rename logic
  iterates over all legacy headers and matches the first one found.
- `backend/obsidian_sync.py`: `LEGACY_HEADER_SUB()` now accepts an explicit
  `legacy_header` parameter instead of using a global constant.
- `backend/config.py`: Added migration rule that upgrades
  `obsidian_section_header` from legacy values to `## About Logos`.
- Smoke tests in `obsidian_sync.py` extended with cases for emoji-prefixed
  legacy headers and coexisting headers.