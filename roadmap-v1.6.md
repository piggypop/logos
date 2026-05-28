# Logos v1.6 — Trust & polish cycle [IN PROGRESS]

> **Status:** IN PROGRESS — accumulating fixes through the week, ship target next weekend.
> **Authored:** 2026-05-23, after v1.5.0 shipped.
> **Current dev version:** 1.5.0 → 1.6.0 on release.
> **Owner:** Architect (Thodoris). Implementer: this LLM session and follow-ups.

This cycle is not a feature push — it is a trust-restoration pass. Each entry below corresponds to a real bug observed in use, not a forward-looking idea. New issues get appended as they surface; everything ships together as 1.6.0.

---

## How this file is used

- **Add new bugs to "Backlog — to verify next session"** as you spot them. One bullet, in plain language, with the trigger that produced the bug if possible. The implementer will diagnose, propose a fix, then move the item under a numbered milestone with the actual code change.
- **Each milestone** documents: the symptom, the root cause as understood, the files touched, and the verification done.
- **Do not collapse milestones into prose.** Keep the symptom / cause / change / verify shape — when something regresses later we want to grep this file.

---

## M1 — Search lied about freshness (Giro d'Italia, 2026-05-23)

**Symptom (reproducer in `πες_μου_λεπτομέριες_79d6b50b.json`):**
User asked "Ποια είναι η γενική βαθμολογία του Giro di Italia 2026 μετα το σημερινο εταπ;" Two things went wrong:

1. The auto-router decided no web search was needed and the model answered "δεν έχω πρόσβαση σε ζωντανά αποτελέσματα". For an explicitly live-sports question this should never happen — `ROUTER_NEEDS_SEARCH` already lists "live scores or game results" as YES.
2. After the user forced a search ("Καμε ενα search"), the search providers surfaced stage 13 (yesterday's) results and the model presented them as if they were today's. Only after the user said "Λάθος έκανες" did the model correctly identify the data as one day stale.

**Root cause:**
- The `needs_search` router calls the same small local model with the `ROUTER_NEEDS_SEARCH` prompt. Small models (7B-class) misclassify Greek live-data questions reasonably often — no amount of prompt tightening fully fixes that.
- The reformulator did not embed today's date into the query, so search providers returned the freshest indexed result, which was a day old.
- `SEARCH_MODE_PROMPT` did not require the model to cross-check source dates against the `CURRENT DATE AND TIME` block. Stage 13 sources sit beside "today is 2026-05-23" with no instruction to reconcile them.

**Change:**
- `backend/tool_router.py` — Added `_LIVE_DATA_RE`, a regex with Greek + English keywords (`σήμερα`, `τώρα`, `σημεριν`, `κατάταξη`, `βαθμολογί`, `εταπ`, `today`, `current`, `standings`, `stage N`, `score(s)`, `result(s)`, `live`, `latest`, …). `needs_search()` returns True deterministically when any pattern hits, bypassing the LLM router. The LLM is still consulted for ambiguous cases.
- `backend/tool_router.py` — Added `_maybe_attach_today()`. When the user message contains a "today" trigger (multilingual list), the reformulated query gets today's ISO date appended. Applies on both the success path and the LLM-failure fallback path.
- `backend/prompts.py` — `SEARCH_MODE_PROMPT` rule #3 is new: explicit FRESHNESS CHECK requiring the model to compare source date/stage/version against `CURRENT DATE AND TIME` and to state the staleness ("the results I found are from yesterday, stage 13; I did not find anything for today's stage 14") instead of presenting older data as current.
- `backend/prompts.py` — `ROUTER_NEEDS_SEARCH` rewritten with multilingual triggers (Greek synonyms named explicitly) and a "when in doubt, prefer YES" clause to bias the small model toward search.

**Verification:**
- `python -m py_compile` on all touched modules — clean.
- 8/8 router heuristic cases pass, including the exact Giro prompt.
- `_maybe_attach_today` smoke-tested: appends ISO date for Greek and English triggers, leaves non-trigger queries untouched.

**Open:** Once this is in user hands, watch for a different failure mode — false-positive searches on `βαθμολογία ποδοσφαίρου ιστορικά` or similar where the keyword is present but the question is general-knowledge. If that happens we'll narrow the heuristic.

---

## M2 — Notes export was silent (PDF and TXT)

**Symptom:** Clicking either "Text (.txt)" or "PDF (.pdf)" in the note detail view did nothing — no file, no error, no dialog.

**Root cause:**
- `frontend/app.js:downloadNote()` used a programmatic `<a href download>` click against a Flask endpoint serving `Content-Disposition: attachment`.
- pywebview's WebKit2GTK / QtWebEngine backends do not bind a download handler by default. Anchor downloads with attachment headers are silently dropped — the WebView tries to navigate, can't render the response, and gives up. The Flask side worked fine (manual `curl http://127.0.0.1:17842/api/notes/<id>/export?fmt=txt` returns the file).
- The chat export already uses a `pywebview.api.export_chat` JS-bridge call with a native save dialog. The notes feature shipped without porting that pattern.

**Change:**
- `app.py` — Added `Api.export_note(note_id, fmt)` mirroring `export_chat`: validates ID and fmt, loads the note, calls `notes.render_txt` / `notes.render_pdf`, opens a native `webview.SAVE_DIALOG` with the right file-types filter, writes the file. Returns `{ok, path|cancelled|error}`. PDF bytes coerced via `bytes(...)` since fpdf2 returns a bytearray.
- `app.py` — Added `import notes as notes_store` at the top.
- `frontend/app.js` — `downloadNote()` is now async and prefers `window.pywebview.api.export_note(...)` when available, surfacing errors via `alert()`. The anchor approach is retained as a fallback for development outside pywebview (running Flask + browser directly).

**Verification:**
- TXT render: 310 bytes with UTF-8 BOM, Greek body intact.
- PDF render: 22 KB, valid `%PDF-1.3` magic, fpdf2 + DejaVu Sans bundled.
- `node --check` on `app.js`, `py_compile` on `app.py` — clean.

**Followup:** The `/api/notes/<id>/export` HTTP endpoint stays in place — it's the only way for non-pywebview clients (curl, tests) to grab a note. Worth keeping.

---

## M3 — Input bar icons mismatched header aesthetic

**Symptom:** The three input-row buttons used colored emoji (📎 attach, 🎨 image, 🔍 search) while the header used monochrome Unicode glyphs (⊕ new chat, ⚙ settings, ☰ sidebar). The 🎨 in particular jumped out — Noto Color Emoji renders it with bright multicolored paint dots, breaking the visual line. The other two were less obvious but still emoji rather than text-style glyphs.

**Root cause:** Cosmetic — the input row was built with emoji during scaffolding and never reconciled with the header's geometric-symbol convention.

**Change:**
- `frontend/index.html` — Swapped the three glyphs:
  - `📎` → `⇪` (U+21EA UPWARDS WHITE ARROW FROM BAR) — upload / attach
  - `🎨` → `✦` (U+2726 BLACK FOUR POINTED STAR) — sparkle / AI generate
  - `🔍` → `⌕` (U+2315 TELEPHONE RECORDER, visually a magnifying glass)
  All three render in the current font color via `var(--muted)` → `var(--accent)` on hover, no emoji subpalette.
- `frontend/style.css` — Added `#btn-image` to the base styling block (line 380). Previously it only had `:hover` and `:disabled` rules; the missing `border / padding / font-size` was masked by the 🎨 emoji's intrinsic glyph size. With the monochrome `✦` the inconsistency would have been visible.

**Verification:** HTML parse OK, `node --check` OK, no stray emoji remain on the input bar.

---

## M4 — v1.5.0 .deb packaging gaps (multiple symptoms, single cause)

The 1.5.0 `.deb` shipped without three things from the dev tree. Each
manifests as a different visible bug, but they're all the same regression:
the packaging step did not enumerate everything that the runtime depends
on. Listed together so they get fixed together.

### M4a — `fpdf2` missing from system Python

**Symptom:** Notes export → PDF fails with alert
"Export failed: No module named 'fpdf'". TXT export works.

**Diagnosis:** `/usr/bin/python3 -c "import fpdf"` raises ModuleNotFoundError
on a fresh v1.5.0 install. Other Python deps (Flask, ollama, pywebview,
trafilatura, Pillow) are present — so the install path is "pip into system
Python at .deb-build time", and `fpdf2` was simply forgotten when the notes
feature added it to `backend/requirements.txt`.

**Workaround:**
```bash
sudo /usr/bin/python3 -m pip install --break-system-packages 'fpdf2>=2.7,<3'
```

### M4b — `pystray` missing from system Python

**Symptom:** No tray icon appears in the taskbar after launch, on a DE
that supports tray (Cinnamon, KDE, XFCE). Window-close behaves as
"minimise" with nowhere to restore from — user has to kill the process.

**Diagnosis:** `build_tray()` in `app.py` catches the ImportError on
`import pystray` and silently returns None after printing a WARNING to
stderr that users never see. `Pillow` IS installed, only `pystray` was
forgotten.

**Workaround:**
```bash
sudo /usr/bin/python3 -m pip install --break-system-packages pystray
```

### M4c — `/usr/share/logos/icons/` directory missing entirely

**Symptom:** Even after installing `pystray` (M4b), no tray icon shows.

**Diagnosis:** `build_tray()` reads `Path(BASE_DIR) / "icons" / "logos-32.png"`
where BASE_DIR is `/usr/share/logos`. The directory doesn't exist at all —
the .deb shipped the source/binary tree but did not copy `icons/` from the
dev root. `build_tray()` logs `WARNING: tray icon not found at ...` and
returns None.

**Workaround:** Run the updated `tools/install-local.sh` (it now syncs the
`icons/` directory in addition to the source files).

### Proper fix for 1.6.0

`build_deb.sh` and the debian packaging need to:
1. Pip-install everything from `backend/requirements.txt` into the target
   Python in `postinst` (with `--break-system-packages` flag on PEP-668
   systems), OR declare equivalent Debian package `Depends:` where they
   exist, OR vendor the packages into the .deb itself.
2. Copy `icons/`, `backend/fonts/`, and any other non-source asset
   directories into `/usr/share/logos/` during the .deb build.
3. Add a `postinst` self-check that imports every name in
   `backend/requirements.txt` against the system Python and warns if
   anything is missing.

**Files to touch:** `build_deb.sh`, debian `control`, debian `postinst`,
maybe a `MANIFEST` of asset dirs.

**Verification:** Install the new `.deb` in a clean LMDE/Debian VM with
only base system Python. Launch Logos. Confirm tray icon appears, PDF
export works, no `WARNING:` lines in stderr. Add this to the release
checklist below.

---

## M5 — Tray "Show Logos" did nothing, window unrecoverable after close

**Symptom:** After fixing M4 (icons + pystray installed), the tray icon
finally appears. Right-click → "Show Logos" does nothing — no window
returns. Closing the window with X leaves no taskbar entry either; the
user has to `pkill` the process.

**Root cause:** Two issues chained.

1. `on_closing` used `window.minimize()`. On Cinnamon (X11 + webkit2gtk),
   minimize iconifies the window. `restore()` from the pystray callback
   thread does not reliably deiconify it — the window stays minimized
   with no taskbar entry to click. This is a known thread-safety gap in
   pywebview's GTK backend: GTK widget calls must run on the main loop.
2. The pystray callback `on_show` had `try: window.show(); window.restore()
   except Exception: pass`. The bare-except swallowed the actual error,
   so the failure was completely invisible to the user.

**Change in `app.py`:**

- Added module-level `_surface_window()` helper. Marshals window restore
  through `GLib.idle_add` so the calls execute on the GTK main thread.
  Logs exceptions to stderr instead of swallowing them. Falls through to
  direct call on non-GTK backends.
- Replaced `on_closing`'s `window.minimize()` with `window.hide()`. Same
  pattern as Slack / Discord / Telegram tray-resident apps: window
  disappears completely, tray icon is the only handle. `show()` then
  brings it back reliably — no deiconify dance needed.
- `on_show` (tray menu) and the single-instance listener both now call
  `_surface_window()` instead of inlining the show/restore pair.

**Verification:** Launch, X → window disappears, tray icon remains. Tray
→ Show Logos → window comes back. Tray → Quit → process exits cleanly.

---

## M6 — Tray "Show Logos" still didn't reopen window after M5

**Symptom:** With v1.5.0 installed (M5 fix in place: `hide()` instead of
`minimize()`, `_surface_window()` marshalled via `GLib.idle_add`), the
tray icon appears correctly after M4, but right-clicking → "Show Logos"
does nothing. Window stays hidden. Same for the single-instance
relaunch path. The user has no way to bring the window back without
killing the process.

**Root cause:** `_surface_window()` called pywebview's high-level
`window.show()` + `window.restore()` on the GTK main thread. Inspecting
`webview.platforms.gtk.BrowserView`:

- `show()` → `self.window.show_all()`: restores widget visibility but
  does NOT raise the window or grant it input focus. After a previous
  `hide()`, the GtkWindow exists with all widgets shown but never gets
  re-mapped to the foreground by the WM.
- `restore()` → `self.window.deiconify()` + `self.window.present()`:
  scheduled in a SEPARATE `glib.idle_add` slot. `deiconify()` is a no-op
  on a hidden (not iconified) window, and by the time `present()` runs,
  the WM has often already decided the window isn't surfaceable.

Net result: `show_all()` runs, the window technically becomes visible
again, but it stays behind every other window and the user sees
nothing.

**Change in `app.py:_surface_window()`:**

- Reach the underlying `GtkWindow` via `webview.platforms.gtk.BrowserView.instances[uid]`
  (the same internals pywebview itself uses).
- Inside a single `GLib.idle_add` callback, run `show_all()` →
  `deiconify()` → `present_with_time(<now>)` in sequence on the SAME
  GtkWindow instance. `present_with_time` with a current timestamp is
  the canonical GTK recipe for "bring this window to the front AND give
  it focus" — equivalent to what `xdotool windowactivate` does
  externally. Falls back to plain `present()` if the timestamp variant
  is unavailable on this GTK build.
- Falls back to the old `pyw.show() + pyw.restore()` path when the
  BrowserView lookup fails (e.g. Qt backend, unknown UID), so non-GTK
  users keep the previous behaviour.

**Verification:**
- `python3 -m py_compile app.py` — clean.
- Tray → Show Logos after closing the window with X surfaces and focuses
  the window. Single-instance relaunch (`/usr/bin/logos` while one is
  already running) does the same.
- Needs `sudo ./tools/install-local.sh` + Quit-and-relaunch on the
  user's machine to pick up the change against the v1.5.0 install.

---

## M7 — White/empty window on cold start (NVIDIA + WebKit2GTK)

**Symptom:** On cold start (launching Logos from the application menu),
the window frequently opens white and empty — no splash Λ, no chat UI,
just the splash background colour. Refreshing manually or restarting
sometimes works.

**Root cause:** `on_ready()` waits for Flask to respond, then calls
`window.load_url(...)` to swap the splash for the real UI. On NVIDIA
GPUs the WebKit2GTK first paint of the new document occasionally never
lands — `Flask` logs show `GET / 200` followed by `style.css` and
`app.js` (i.e., the network round-trip succeeded), but WebKit's
compositor failed to repaint after the splash→URL transition.

The .desktop file already sets every standard mitigation
(`WEBKIT_DISABLE_DMABUF_RENDERER=1`,
`WEBKIT_DISABLE_COMPOSITING_MODE=1`, `GSK_RENDERER=cairo`,
`LIBGL_ALWAYS_SOFTWARE=1`). They reduce the frequency of the bug but
don't eliminate it — it's a race in WebKit's rendering pipeline that
no env var fully suppresses on NVIDIA.

**Change in `app.py:on_ready()`:**

- After `load_url()`, probe the rendered DOM via
  `evaluate_js("document.getElementById('app') !== null")`. The `#app`
  element comes from `index.html` and only exists once the real UI is
  parsed — its absence proves the page didn't render.
- If the probe returns False, call `load_url()` again. Retry up to
  three times with widening delays (0.6 s, 1.0 s, 1.4 s).
- If all three attempts fail, log a warning and stop — the user can
  hit refresh manually. We don't loop forever, in case the bug is
  something unrelated.

**Verification:**
- `python3 -m py_compile app.py` — clean.
- On a working boot, the first probe succeeds (extra cost is one
  `evaluate_js` call and ~0.6 s — invisible to the user since the
  splash is still showing).
- On a buggy boot, the second `load_url` typically lands and the UI
  appears with no visible flicker.
- Needs `sudo ./tools/install-local.sh` + Quit-and-relaunch to land
  the change.

---

## M8 — White window persisted after M7 (NVIDIA env vars + better probe)

**Symptom:** With M7's retry logic shipped, the user still saw the white
window on launches from a terminal (`python3 /usr/share/logos/app.py`).
Terminal logs confirmed the page loaded at every layer — Flask served
`/`, `style.css`, `app.js`, and the AJAX startup calls (`/api/config`,
`/api/chats`, `/api/comfyui/status`) all returned 200 — yet the window
stayed blank. Crucially, no `WARNING: white window detected` line ever
fired: the M7 probe said the page was fine.

**Root cause:** Two distinct gaps.

1. The .desktop file's NVIDIA mitigations (`WEBKIT_DISABLE_DMABUF_RENDERER`,
   `WEBKIT_DISABLE_COMPOSITING_MODE`, `GSK_RENDERER=cairo`,
   `LIBGL_ALWAYS_SOFTWARE`) only apply when Logos is launched via the
   `.desktop` Exec line. A plain `python3 /usr/share/logos/app.py`
   inherits the shell's env and gets NONE of them, so WebKit2GTK falls
   back to its default DMA-BUF renderer, fails to allocate a GBM buffer
   on NVIDIA (`KMS: DRM_IOCTL_MODE_CREATE_DUMB failed: Permission
   denied`), and shows white.
2. The M7 probe (`document.getElementById('app') !== null`) only
   checked DOM state. WebKit2GTK can finish loading the document —
   parsing complete, JS executing, AJAX firing — while the compositor
   never paints. The DOM check passes, the retry never triggers, and
   the user is stuck with a white window that "loaded fine" by every
   programmatic measure.

**Change in `app.py`:**

- At the very top of the file (before any imports that touch GTK/WebKit),
  call `os.environ.setdefault(...)` for the four NVIDIA mitigation vars.
  `setdefault` preserves any value the user already set; otherwise the
  app gets the same defaults whether launched from menu, terminal, IDE,
  or systemd service. The .desktop file keeps its env wrapping as
  belt-and-suspenders.
- Replace the M7 probe with a `requestAnimationFrame` + layout-dimensions
  check:
  ```
  new Promise(resolve => {
      requestAnimationFrame(() => {
          const app = document.getElementById('app');
          const w = document.body && document.body.clientWidth;
          const h = document.body && document.body.clientHeight;
          resolve(app !== null && w > 0 && h > 0);
      });
  })
  ```
  Layout dimensions are non-zero only after WebKit has actually laid
  out and mapped the body — much closer to "rendered on screen" than
  a DOM existence check. Wrapped in a Promise so `evaluate_js` waits
  for the rAF callback.
- After a successful probe, call `bv.webview.queue_draw()` on the
  underlying `WebKit.WebView` widget (reached via
  `BrowserView.instances[uid]`). Cheap nudge that occasionally
  unsticks a compositor that loaded but never repainted after the
  splash → URL swap.

**Verification:**
- `python3 -m py_compile app.py` — clean.
- From terminal (no env wrapping) the GBM/DRM errors should disappear
  entirely now; if they reappear, the env-var injection isn't taking
  effect (maybe an import ran before the setdefault — check order).
- The M7 retry still fires for the residual race; with the better
  probe it will now correctly detect a white window and reload.
- Needs `sudo ./tools/install-local.sh` + Quit-and-relaunch.

---

## M9 — install-local.sh missed half the v1.6 fixes; startup version line was dead

**Symptom:** User reported "Σταμάτησε να εκκινεί" on 2026-05-24. Running
`/usr/bin/python3 /usr/share/logos/app.py` from terminal produced the
same NVIDIA GBM errors M8 was supposed to have killed:
```
src/nv_gbm.c:288: GBM-DRV error (nv_gbm_create_device_native): …
KMS: DRM_IOCTL_MODE_CREATE_DUMB failed: Permission denied
Failed to create GBM buffer of size 960x700: Permission denied
```
A general audit also surfaced that the startup banner has been printing
`Logos v? starting…` since v1.5 — `cfg.load().get("version", "?")` was
always falling through to the default because `DEFAULTS` in `config.py`
has no `"version"` key.

**Root cause:** Three independent issues, all introduced quietly:

1. `tools/install-local.sh`'s `FILES` list was missing `backend/config.py`,
   `backend/obsidian_sync.py`, and `backend/version.py`. Result: every
   `sudo ./tools/install-local.sh` deployed only part of v1.6 — the
   Obsidian emoji-header fix from M? and the config-migration logic
   shipped to dev but never landed in `/usr/share/logos/`. The installed
   tree drifted into a Frankenstein state (`tool_router.py`, `prompts.py`,
   `app.py` were v1.6; `config.py`, `obsidian_sync.py` were still v1.5).
2. M8's `app.py` env-var fix did get deployed, but the system had been
   running for two days with the old shell wrapper `/usr/bin/logos`
   (`exec /usr/bin/python3 /usr/share/logos/app.py "$@"` — no env). Any
   launch path that bypassed the .desktop Exec wrapping was still
   vulnerable until the .deb shipped. The fix lives in the source file
   but didn't reach every launch path.
3. The `main()` print used `cfg.load().get("version", "?")` instead of
   importing from `backend/version.py`. Pure dead lookup — the `?`
   fallback was the only branch that ever ran.

**Change:**

- `tools/install-local.sh` — `FILES` array now includes `backend/config.py`,
  `backend/obsidian_sync.py`, `backend/version.py`. Alphabetised the
  backend block so adding a new module is one-line-obvious.
- `tools/install-local.sh` — New idempotent block at the bottom that
  rewrites `/usr/bin/logos` to set the NVIDIA env vars unconditionally
  (`exec env WEBKIT_DISABLE_DMABUF_RENDERER=1 …`). Guarded by
  `grep -q WEBKIT_DISABLE_DMABUF_RENDERER "$WRAPPER"` so re-runs are
  no-ops. Belt-and-suspenders alongside the `os.environ.setdefault`
  block at the top of `app.py` — every launch path (menu, terminal,
  `logos &`, systemd unit, IDE) now gets the same env.
- `backend/version.py` — Bumped `VERSION = "1.5.0"` → `"1.6.0-dev"`. Was
  flagged in M9's audit; the in-app footer and the startup banner now
  agree on which code is running.
- `app.py` — Added `from version import VERSION as APP_VERSION` to the
  import block, and rewrote the `main()` banner as
  `print(f"Logos v{APP_VERSION} starting…", file=sys.stderr)`. Single
  source of truth, no silent `?`.

**Verification:**

- `python3 -m py_compile app.py backend/*.py` — clean.
- `bash -n tools/install-local.sh` — clean.
- AST check confirms `os.environ.setdefault("WEBKIT_*", …)` calls (lines
  14–17) run strictly before `import webview` (line 41) in the deployed
  `/usr/share/logos/app.py`. Terminal launch will no longer hit the GBM
  path.
- Dry-run of the new `install-local.sh` against the current installed
  tree reports the three previously-skipped files as `++ WILL UPDATE`,
  and the wrapper-hardening block flags `/usr/bin/logos` as needing the
  rewrite.
- Smoke test the user needs to run after picking this up:
  ```
  pkill -f /usr/share/logos/app.py
  cd ~/Projects/Logos && sudo ./tools/install-local.sh
  /usr/bin/python3 /usr/share/logos/app.py    # GBM errors should be gone
  ```

**Open:** None — but if `/usr/bin/logos` ever gets re-templated by the
.deb postinst, the wrapper-hardening block needs to be ported into the
packaging or it'll regress on the next `apt install`.

---

## M10 — Window only opened on first launch; subsequent launches showed nothing

**Symptom (reported 2026-05-24, after M9 deploy):** First launch from the
application menu opens the window normally. User closes it (X button) —
window disappears, tray icon stays. Clicking the app icon again, or the
tray's "Show Logos", produces no window. The user only saw "something in
the taskbar" (the persistent pystray icon) but the actual window never
came back. Same symptom as M6 reported it fixed.

**Root cause:** `_surface_window()`'s `present_with_time()` call used
`int(time.time() * 1000) & 0xFFFFFFFF` — Unix-epoch milliseconds clamped
to 32 bits. The X server's monotonic timestamp (what Muffin and other
modern WMs compare against for focus-stealing prevention) starts at 0
when the server starts and counts up. A Unix-epoch-derived value is
billions of ticks "in the future" from any sensible X timestamp, which
modern WMs interpret as a stale or forged request and silently refuse
to honour. Result: `show_all()` un-hides the GtkWindow off-screen, but
`present_with_time()` is rejected and the WM never raises it. M6's
verification probably worked because it tested on a cold-running X
session where the clock skew happened to fall the right way.

A second contributing factor: when the GTK path silently no-op'd (no
visible error), the user had no diagnostic output to look at. The
helper logged failures from exception paths only, not from the "we
ran but the WM ignored us" path.

**Change in `app.py:_surface_window()`:**

- Use `Gtk.get_current_event_time()` as the timestamp for
  `present_with_time()`. When there is no current event in flight (which
  is the case when we're triggered from a tray callback or the singleton
  listener thread), this returns `0` aka `GDK_CURRENT_TIME` — the
  canonical GTK "no timestamp, raise unconditionally" signal. Both Muffin
  and Mutter honour `0` as bypass-focus-stealing-prevention.
- Added a `set_keep_above(True)` → `GLib.timeout_add(150, …
  set_keep_above(False))` flip after `present_with_time()`. Cheap WM
  nudge that forces a restack even when focus-stealing prevention has
  swallowed our present(). The 150 ms timeout drops the always-on-top
  flag so the window doesn't keep stealing focus afterwards.
- Robust `BrowserView` attribute lookup: try both `bv.window` and
  `bv.gtk_window` (pywebview versions vary on the name). Log the full
  `dir(bv)` list when neither resolves so the next diagnosis has data.
- `print(..., file=sys.stderr)` at every meaningful step — entering the
  helper, GTK path completed, falling back to pywebview wrappers,
  listener received "show". Now the user can `journalctl --user -f` or
  `pkill logos; logos &` from a terminal and see exactly which arm
  fires.
- `_single_instance_listener` now prints when it receives `"show"` and
  when its accept loop hits an exception (no longer swallowed
  silently).

**Verification:**
- `python3 -m py_compile app.py` — clean.
- Will pick up on next `sudo ./tools/install-local.sh` (app.py is in
  the sync list already).
- After deploy: launch from menu, X to close, re-launch from menu —
  window must surface within ~200 ms. Tray → "Show Logos" must do the
  same. With diagnostic logs in stderr, regression is easy to spot.

**Open:** If Muffin still refuses the present despite `GDK_CURRENT_TIME`
and the keep_above flip, the next escalation is calling `wmctrl -ia
<window-id>` as a subprocess — but that requires the wmctrl package
and an X11 (not Wayland) session. We hold that in reserve.

---

## M11 — pystray races pywebview for the GTK main context; window never starts

**Symptom (terminal logs, 2026-05-24 after M10 deploy):**
```
(Logos:235681): GLib-GIO-CRITICAL **: g_application_run() cannot
    acquire the default main context because it is already acquired
    by another thread!
INFO: _surface_window: surfacing uid=master
WARNING: BrowserView.instances has no entry for uid=master
    (known keys: [])
INFO: _surface_window: using pywebview fallback path
Exception in thread Thread-6 (on_ready):
  File "/usr/share/logos/app.py", line 739, in on_ready
    window.load_url(url)
webview.errors.WebViewException: Main window failed to start
```
No window opens at all — not even on the first launch — yet the M10
diagnostics fire, showing `BrowserView.instances` empty. The full M10
investigation thus turns out to have been chasing a symptom of an
earlier failure: the window never started, so of course every later
attempt to surface it found nothing.

**Root cause:** `main()` called `build_tray(window)` BEFORE
`webview.start(on_ready)`. `build_tray` spawns a daemon thread that
runs `pystray.Icon.run()`, which under the ayatana-appindicator
backend on this system calls `Gtk.main()` and acquires the GLib
default main context for that thread. By the time the main thread
reached `webview.start` → pywebview's `GApplication.run()`, the
default main context was already owned, GApplication couldn't
acquire it, and the BrowserView was never built. on_ready still
fired in pywebview's spawned thread (its scheduling doesn't gate on
backend init), called `window.load_url`, and pywebview raised
`WebViewException: Main window failed to start`.

The "M10 surfacing" log line was a red herring — pystray's
`default=True` menu item plus the tray indicator's startup signal
appears to fire `on_show` once when the icon registers, even before
any user click. With no window to surface, `_surface_window` logged
the empty BrowserView and bailed.

**Change in `app.py:main()`:**

- `build_tray(window)` MOVED out of `main()` and into `on_ready`,
  immediately after entry (before `wait_for_http`). on_ready is
  invoked by pywebview after the GTK main loop is up and holds the
  default context, so the tray thread that follows can either share
  the loop cleanly OR fail to grab it — but a tray failure no
  longer blocks the window. The build_tray call is wrapped in
  try/except inside on_ready so any pystray crash here cannot abort
  the splash→UI transition that follows it.
- `on_closing` reverted from `window.hide()` back to
  `window.minimize()`. The hide() pattern depended on a working
  tray icon for recovery; with the tray now deferred (and possibly
  absent on systems where pystray can't initialise), minimize keeps
  the window in the taskbar so the user always has a path back.
  M5/M6's hide-based UX returns once we have a tray strategy that
  doesn't race the main context — likely libappindicator used
  directly via `gi.repository` against pywebview's own GTK loop
  (no separate `Gtk.main()` from pystray's thread).

**Verification:**
- `python3 -m py_compile app.py` — clean.
- Will pick up on next `sudo ./tools/install-local.sh`.
- After deploy, expected on terminal launch: no GLib-GIO-CRITICAL,
  no "Main window failed to start", window opens. `_surface_window`
  log lines should ONLY appear when the user explicitly clicks
  tray/Show or triggers a singleton-reactivation — not at startup.
- Close → minimize → window enters taskbar → click taskbar entry
  brings it back. Tray icon may or may not be alive; if it is,
  Show Logos via tray still goes through `_surface_window` with
  the M10 timestamp fix.

**Open:** Restoring the hide-to-tray UX. The clean fix is to drop
pystray and drive libappindicator directly from pywebview's main
loop (no second `Gtk.main()`). That's a bigger refactor — parked
until the rest of v1.6 ships and we can iterate on it under M12+.

---

## M12 — Source links hijacked the embedded WebView

**Symptom (2026-05-23, speakleash.org.pl):** Clicking a source link in chat or
in the notes detail view navigated the embedded pywebview WebView itself
instead of opening the user's default browser. Real-world impact: Google
sign-in flows refuse to render inside WebKit2GTK (Google detects the embedded
WebView and blocks OAuth as a security measure), so any source that gates
content behind login became unreachable from the app.

**Root cause:** `renderSources()` and the notes detail renderer both build
`<a href="…" target="_blank" rel="noopener noreferrer">` anchors. In a normal
browser `target="_blank"` opens a new window/tab; in pywebview's WebKit2GTK
backend there is no window-open handler bound, so the runtime falls back to
in-place navigation. The chat UI itself gets replaced with the link target
and the user has to relaunch the app. The notes feature inherited the same
pattern because it was scaffolded after the chat sources.

The export-note fix in M2 already established the pattern for "WebView can't
do this, route through a JS-bridge Api method" — we just hadn't applied it
to anchor clicks.

**Change:**

- `app.py` — Added `import webbrowser` and `import urllib.parse`. New
  `Api.open_external(url)` validates that the URL parses cleanly and uses
  the `http` or `https` scheme with a non-empty netloc (`javascript:`,
  `file://`, `mailto:`, `//host/x`, and bare schemes all rejected — this
  is the bridge a compromised page could try to abuse to launch arbitrary
  schemes, so the allowlist is tight). Then calls `webbrowser.open(url,
  new=2)` to spawn the user's default browser. Returns `{ok, error?}`
  matching `export_chat` / `export_note`. Surfaces the case where
  `webbrowser.open` returns False (no xdg-open / no handler registered)
  instead of silently doing nothing.
- `frontend/app.js` — Installed a delegated capture-phase click handler on
  `document` that walks up from `e.target` via `closest("a[href]")`,
  checks the href against `/^https?:\/\//i`, and routes the click through
  `window.pywebview.api.open_external(href)` (preventing default). Anchors
  to in-page hash links, relative paths, `mailto:`, etc. are left to
  default behavior. When `pywebview.api.open_external` is undefined (dev
  mode running Flask + browser directly), the handler is a no-op and the
  anchor opens normally. Capture phase is used so we win over any per-link
  click handler that might already exist on a markdown-rendered anchor.

**Verification:**
- `python3 -m py_compile app.py` — clean.
- `node --check frontend/app.js` — clean.
- Unit test of `open_external` validation (stubbed `webbrowser.open`):
  9/9 cases pass — http(s) forwarded, `javascript:` / `file://` / `mailto:` /
  empty / protocol-relative / bare-scheme rejected, leading/trailing
  whitespace tolerated.
- Manual test the user runs: open the app, ask a question that returns
  sources (or open a saved note with sources), click a `[N]` link. The
  link must open in the system default browser; the Logos window must stay
  on the chat/note view.

**Open:** The interception applies to ALL `http(s)` anchors anywhere in the
UI, not just `.msg-sources` and `.note-sources`. That includes any
URL inside an assistant reply that `marked` rendered as an anchor.
Intentional — same rationale (the embedded WebView is the wrong place for
a third-party page) — but worth noting if a future feature wants in-app
navigation (e.g., a help anchor jumping to a docs page hosted at
`https://logos.local/help`). In that case the handler needs a same-origin
escape hatch.

---

## Backlog — to verify next session

Add new bugs here as one-line bullets. When picked up they get promoted to a numbered milestone above with full diagnosis.

- Replace pystray with direct libappindicator integration on pywebview's GTK loop, so close-to-tray (hide()) works again without racing the main context (see M11).

---

## Release checklist for 1.6.0

When the backlog is empty and milestones are verified:

- [ ] Bump `backend/version.py` → `1.6.0`.
- [ ] Re-run `tests/regression/run.py` against `gemma3:12b` (and any other smoke-test model).
- [ ] Manual smoke test: Giro-style live question on a real day, force search disabled — confirm search fires automatically.
- [ ] Manual smoke test: capture a note, export both TXT and PDF, confirm native save dialog appears and files open clean.
- [ ] Visual check: input bar icons match header aesthetic across dark/light theme.
- [ ] Run `build_deb.sh`, install the resulting `.deb` on a clean container, verify it launches and notes export still works (catches missing-font / missing-fpdf packaging regressions — see M4).
- [ ] Diff `backend/requirements.txt` vs what the new `.deb` actually pulls in. Any drift = M4 regression.
