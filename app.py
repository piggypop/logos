import os

# ── NVIDIA / WebKit2GTK rendering mitigations ─────────────────────────
#
# WebKit2GTK on NVIDIA tries to allocate render buffers via DMA-BUF/GBM,
# which fails on most NVIDIA setups (KMS permission denied → "Failed to
# create GBM buffer" → white window with the DOM loaded but no pixels
# painted). These env vars force CPU/cairo compositing instead. The
# .desktop file sets the same vars, but we duplicate them here so the
# app behaves identically when launched directly with
# `python3 /usr/share/logos/app.py` from a terminal (no .desktop wrapping
# = no env vars = white window). setdefault preserves any user override
# already in the environment.
os.environ.setdefault("WEBKIT_DISABLE_DMABUF_RENDERER", "1")
os.environ.setdefault("WEBKIT_DISABLE_COMPOSITING_MODE", "1")
os.environ.setdefault("GSK_RENDERER", "cairo")
os.environ.setdefault("LIBGL_ALWAYS_SOFTWARE", "1")

import json
import socket
import sys
import threading
import time
import traceback
import urllib.parse
import urllib.request
import webbrowser
from pathlib import Path

BASE_DIR = (
    sys._MEIPASS
    if getattr(sys, "frozen", False)
    else os.path.dirname(os.path.abspath(__file__))
)
sys.path.insert(0, os.path.join(BASE_DIR, "backend"))

import chats as chats_store
import config as cfg
import file_extractor
import image_storage
import notes as notes_store
import obsidian_sync
import ollama_client
import webview
from server import app
from version import VERSION as APP_VERSION

# ── Single-instance constants ──────────────────────────────
SINGLE_INSTANCE_ADDR = "\0logos-single-instance"


class Api:
    def pick_files(self) -> list[dict]:
        """Open native file picker (multi-select). Extracts each file via
        file_extractor and returns a list of attachment dicts ready for the
        chat request body. Filters by current model capabilities."""
        window = webview.windows[0]
        result = window.create_file_dialog(
            webview.OPEN_DIALOG,
            allow_multiple=True,
        )
        if not result:
            return []

        c = cfg.load()
        caps = ollama_client.get_capabilities(c["ollama_host"], c["ollama_model"])

        files = []
        for path in result:
            ok, reason = file_extractor.is_supported(path, caps)
            if not ok:
                files.append(
                    {
                        "filename": path.split("/")[-1],
                        "category": file_extractor.categorize(path),
                        "error": reason,
                    }
                )
                continue
            info = file_extractor.extract(path, caps)
            if info:
                files.append(info)
        return files

    def export_chat(self, chat_id: str) -> dict:
        data = chats_store.get(chat_id)
        if not data:
            return {"ok": False, "error": "not found"}
        safe = (
            "".join(
                c if c.isalnum() or c in "-_" else "_"
                for c in (data.get("title") or "chat")
            )[:60].strip("_")
            or "chat"
        )
        default_name = f"{safe}_{chat_id[:8]}.json"
        window = webview.windows[0]
        result = window.create_file_dialog(
            webview.SAVE_DIALOG,
            save_filename=default_name,
            file_types=("JSON files (*.json)", "All files (*.*)"),
        )
        if not result:
            return {"ok": False, "cancelled": True}
        path = result if isinstance(result, str) else result[0]
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            return {"ok": True, "path": path}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def save_image(self, rel_path: str, filename: str) -> dict:
        """Save a generated image to a user-chosen location via native dialog.

        rel_path is the path relative to image_storage.IMAGES_ROOT
        (e.g. "<chat_id>/<file>.png"). Uses SAVE_DIALOG so the user
        picks the destination — same pattern as export_chat/export_note.
        """
        import shutil
        src = image_storage.IMAGES_ROOT / rel_path
        if not image_storage.is_safe_path(src) or not src.exists():
            return {"ok": False, "error": "image not found"}
        ext = src.suffix.lstrip(".") or "png"
        safe_name = filename or src.name
        window = webview.windows[0]
        result = window.create_file_dialog(
            webview.SAVE_DIALOG,
            save_filename=safe_name,
            file_types=(f"Image files (*.{ext})", "All files (*.*)"),
        )
        if not result:
            return {"ok": False, "cancelled": True}
        dest = result if isinstance(result, str) else result[0]
        try:
            shutil.copy2(src, dest)
            return {"ok": True, "path": dest}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def export_note(self, note_id: str, fmt: str) -> dict:
        """Export a single note as txt or pdf via native save dialog.

        Returns {ok: bool, path?: str, cancelled?: bool, error?: str}.

        The browser-style <a download> path silently fails inside pywebview
        (no download handler bound), so notes export funnels through this
        js_api method instead. Mirrors export_chat above.
        """
        fmt = (fmt or "").lower()
        if fmt not in ("txt", "pdf"):
            return {"ok": False, "error": "fmt must be txt or pdf"}
        if not notes_store._is_valid_id(note_id):
            return {"ok": False, "error": "invalid note id"}

        note = notes_store.get(note_id)
        if note is None:
            return {"ok": False, "error": "note not found"}

        default_name = notes_store._fmt_export_filename(note, fmt)
        if fmt == "txt":
            file_types = ("Text files (*.txt)", "All files (*.*)")
        else:
            file_types = ("PDF files (*.pdf)", "All files (*.*)")

        window = webview.windows[0]
        result = window.create_file_dialog(
            webview.SAVE_DIALOG,
            save_filename=default_name,
            file_types=file_types,
        )
        if not result:
            return {"ok": False, "cancelled": True}
        path = result if isinstance(result, str) else result[0]

        try:
            if fmt == "txt":
                content = notes_store.render_txt(note)
                with open(path, "w", encoding="utf-8") as f:
                    f.write(content)
            else:
                pdf_bytes = notes_store.render_pdf(note)
                # fpdf2 returns a bytearray; coerce to bytes for write().
                with open(path, "wb") as f:
                    f.write(bytes(pdf_bytes))
            return {"ok": True, "path": path}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def pick_folder(self) -> dict:
        """Open native folder picker (single-select). Used by Settings →
        Obsidian to choose the vault directory."""
        window = webview.windows[0]
        result = window.create_file_dialog(webview.FOLDER_DIALOG)
        if not result:
            return {"ok": False, "cancelled": True}
        # Pywebview returns a tuple/list even for single-select; take the first.
        path = result[0] if isinstance(result, (list, tuple)) else result
        return {"ok": True, "path": path}

    def open_external(self, url: str) -> dict:
        """Open a URL in the user's default system browser, not in the
        embedded pywebview WebView.

        Source links and any anchor with target="_blank" used to navigate
        in-place inside pywebview, which broke real-world flows (Google
        sign-in on speakleash.org.pl refused to render in the WebKit2GTK
        WebView, 2026-05-23). Frontend now intercepts external anchor
        clicks and routes them through this bridge.

        Only http(s) URLs are honoured. Anything else is rejected so a
        compromised page (or a stale clipboard entry) can't ask us to
        launch `file://`, `javascript:`, or custom-scheme handlers.
        """
        try:
            parsed = urllib.parse.urlsplit((url or "").strip())
        except ValueError as e:
            return {"ok": False, "error": f"invalid url: {e}"}

        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            return {"ok": False, "error": "only http(s) urls allowed"}

        try:
            # new=2 → open in a new tab/window of the user's default browser.
            ok = webbrowser.open(url, new=2)
        except Exception as e:
            print(f"open_external: webbrowser.open raised: {e}", file=sys.stderr)
            return {"ok": False, "error": str(e)}

        if not ok:
            # On Linux webbrowser.open returns False when no handler is
            # registered (xdg-open missing). Surface that to the user
            # instead of silently doing nothing.
            return {"ok": False, "error": "no system browser handler"}

        return {"ok": True}

    def quit_app(self):
        """Called from JS frontend as fallback Quit (Ctrl+Q / Settings button)."""
        _shutdown()


# ── Single-instance ────────────────────────────────────────

_SENTINEL_FALLBACK = object()  # distinct from None → "mechanism broken, launch anyway"


def acquire_lock_or_signal():
    """Bind the abstract UNIX socket. Returns:

    - a `socket.socket` listener if we're the first instance
    - `None` if another instance is already running (caller should exit)
    - `_SENTINEL_FALLBACK` if the mechanism is broken (launch non-exclusively)

    The sentinel is necessary so the caller can distinguish "exit" from
    "launch anyway" without relying on side-channel output.
    """
    try:
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    except (AttributeError, OSError) as e:
        print(
            f"WARNING: UNIX sockets unavailable ({e}) — single-instance disabled",
            file=sys.stderr,
        )
        return _SENTINEL_FALLBACK

    try:
        s.bind(SINGLE_INSTANCE_ADDR)
        s.listen(1)
        return s
    except OSError:
        # Already running — send 'show' and exit
        c = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            c.connect(SINGLE_INSTANCE_ADDR)
            c.sendall(b"show\n")
            print("INFO: signalled existing instance to surface", file=sys.stderr)
        except OSError:
            # Stale socket or race — the old process may have died between
            # our bind attempt and this connect. Treat as "no instance running"
            # and let the caller try again.
            print(
                "WARNING: stale singleton socket detected, retrying…", file=sys.stderr
            )
            try:
                c.close()
            except Exception:
                pass
            try:
                s.close()
            except Exception:
                pass
            # Retry: create fresh socket and try to bind again
            try:
                s2 = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                s2.bind(SINGLE_INSTANCE_ADDR)
                s2.listen(1)
                print("INFO: single-instance lock acquired on retry", file=sys.stderr)
                return s2
            except OSError:
                # Still in use — another instance IS running, exit
                try:
                    s2.close()
                except Exception:
                    pass
                return None
            except Exception:
                try:
                    s2.close()
                except Exception:
                    pass
                return _SENTINEL_FALLBACK
        finally:
            try:
                c.close()
            except Exception:
                pass
        return None
    except Exception:
        # Unexpected failure (permission, system policy, etc.) — log and let
        # the app launch anyway so the user isn't locked out.
        print(
            f"WARNING: singleton socket bind failed:\n{traceback.format_exc()}",
            file=sys.stderr,
        )
        print(
            "WARNING: single-instance check skipped — app will launch non-exclusively",
            file=sys.stderr,
        )
        try:
            s.close()
        except Exception:
            pass
        return _SENTINEL_FALLBACK


def _surface_window():
    """Bring the main window back to the foreground from any thread.

    Pywebview's GTK backend is not thread-safe and its high-level show() /
    restore() wrappers are not enough on their own to bring a hide()-d
    window back to the foreground:

      - pywebview.show() → GtkWindow.show_all(): restores visibility but
        does NOT raise the window or give it focus.
      - pywebview.restore() → GtkWindow.deiconify() + present(): only
        affects iconified windows; deiconify() is a no-op on a hidden
        window, and present() runs in a separate idle slot from show_all,
        so the window can stay invisible.

    The reliable recipe on GTK3 + webkit2gtk is: marshal a single callback
    onto the GTK main loop that calls show_all() → deiconify() → present()
    with an X-server timestamp, plus a keep-above flip as a last-resort
    raise. We reach the GtkWindow via pywebview's BrowserView.instances
    dict (an implementation detail we already depend on for this
    platform).
    """
    if not webview.windows:
        print("WARNING: _surface_window called with no windows", file=sys.stderr)
        return

    pyw = webview.windows[0]
    print(f"INFO: _surface_window: surfacing uid={pyw.uid}", file=sys.stderr)

    def do_show():
        # Try the low-level GTK path first (works after hide()).
        gtk_win = None
        try:
            from webview.platforms.gtk import BrowserView

            bv = BrowserView.instances.get(pyw.uid)
            if bv is None:
                print(
                    f"WARNING: BrowserView.instances has no entry for uid={pyw.uid} "
                    f"(known keys: {list(BrowserView.instances)})",
                    file=sys.stderr,
                )
            else:
                # pywebview versions vary on the attribute name. Try both.
                gtk_win = getattr(bv, "window", None) or getattr(bv, "gtk_window", None)
                if gtk_win is None:
                    print(
                        f"WARNING: BrowserView has no .window/.gtk_window attr "
                        f"(attrs: {[a for a in dir(bv) if not a.startswith('_')]})",
                        file=sys.stderr,
                    )
        except Exception as e:
            print(f"WARNING: GTK BrowserView lookup failed: {e}", file=sys.stderr)

        if gtk_win is not None:
            # Sequence matters. show_all re-maps the widget, deiconify
            # un-iconifies if needed, present() with the right timestamp
            # raises + focuses, keep_above flip nudges focus-stealing-
            # prevention WMs (Cinnamon's Muffin in particular).
            try:
                gtk_win.show_all()
            except Exception as e:
                print(f"WARNING: gtk_win.show_all failed: {e}", file=sys.stderr)
            try:
                gtk_win.deiconify()
            except Exception as e:
                print(f"WARNING: gtk_win.deiconify failed: {e}", file=sys.stderr)

            # Use an X-server timestamp via Gtk.get_current_event_time().
            # The PREVIOUS implementation passed Unix-epoch ms clamped to
            # uint32, which is FAR in the future from the X server's
            # monotonic clock view — Muffin's focus-stealing prevention
            # interprets that as "stale request from another app" and
            # refuses to raise the window. get_current_event_time() returns
            # 0 (GDK_CURRENT_TIME) when there's no current event, which is
            # the canonical "no timestamp, just do it" signal.
            ts = 0
            try:
                from gi.repository import Gtk

                ts = Gtk.get_current_event_time() or 0
            except Exception:
                pass
            try:
                gtk_win.present_with_time(ts)
            except Exception as e:
                print(
                    f"WARNING: present_with_time({ts}) failed ({e}); "
                    f"falling back to present()",
                    file=sys.stderr,
                )
                try:
                    gtk_win.present()
                except Exception as e2:
                    print(f"WARNING: present() also failed: {e2}", file=sys.stderr)

            # Final nudge: toggle keep_above. Cheap and forces the WM to
            # restack even when focus-stealing prevention has eaten our
            # present(). The "off" call runs in a tiny GLib timeout so
            # the window doesn't stay always-on-top after surfacing.
            try:
                gtk_win.set_keep_above(True)

                def _drop_keep_above():
                    try:
                        gtk_win.set_keep_above(False)
                    except Exception:
                        pass
                    return False  # one-shot

                try:
                    from gi.repository import GLib

                    GLib.timeout_add(150, _drop_keep_above)
                except Exception:
                    _drop_keep_above()
            except Exception as e:
                print(f"WARNING: keep_above nudge failed: {e}", file=sys.stderr)

            print("INFO: _surface_window: GTK path completed", file=sys.stderr)
            return False  # GLib.idle_add: run once and stop

        # Fallback: pywebview's own wrappers (Qt backend, or unknown UID).
        print("INFO: _surface_window: using pywebview fallback path", file=sys.stderr)
        try:
            pyw.show()
        except Exception as e:
            print(f"WARNING: window.show() raised: {e}", file=sys.stderr)
        try:
            pyw.restore()
        except Exception as e:
            print(f"WARNING: window.restore() raised: {e}", file=sys.stderr)
        return False  # GLib.idle_add: run once and stop

    try:
        from gi.repository import GLib

        GLib.idle_add(do_show)
    except Exception:
        # Non-GTK backend (e.g. Qt). Fall back to direct call and hope.
        do_show()


def _single_instance_listener(listener: socket.socket):
    """Daemon thread: accept connections on the abstract socket and
    surface the window when a 'show' message arrives."""
    while True:
        try:
            conn, _ = listener.accept()
            data = conn.recv(256)
            conn.close()
            if data.startswith(b"show"):
                print(
                    "INFO: singleton listener received 'show' — surfacing window",
                    file=sys.stderr,
                )
                _surface_window()
        except Exception as e:
            print(
                f"WARNING: singleton listener error: {e!r} — sleeping 0.5s",
                file=sys.stderr,
            )
            time.sleep(0.5)


# ── Obsidian daily-note auto-sync ──────────────────────────


OBSIDIAN_LAST_SYNC_FILE = (
    Path.home() / ".local" / "share" / "logos" / "obsidian_last_sync.txt"
)


def _obsidian_auto_sync_once():
    """If we haven't synced today yet, sync yesterday's chats into today's
    daily note. Runs at most once per local day per machine.

    This is best-effort. Any failure (config not set, vault missing, parse
    error, anything) is logged and swallowed — never raises into the
    main thread.
    """
    from datetime import datetime  # local to keep the global import surface small

    try:
        today_iso = datetime.now().astimezone().date().isoformat()

        # Read last-sync marker (if it exists)
        try:
            last = OBSIDIAN_LAST_SYNC_FILE.read_text(encoding="utf-8").strip()
        except FileNotFoundError:
            last = ""
        except Exception:
            last = ""

        if last == today_iso:
            return  # already synced today

        # Run the sync. If config is incomplete (no vault path) this returns
        # ok=True with skipped_reason="config_incomplete" — still fine, we
        # mark today as done so we don't retry on every launch.
        result = obsidian_sync.sync_yesterday()
        if result.get("skipped_reason"):
            print(
                f"[obsidian_sync] auto: skipped ({result['skipped_reason']})",
                file=sys.stderr,
            )
        elif result.get("error"):
            print(f"[obsidian_sync] auto: error {result['error']}", file=sys.stderr)
        else:
            print(
                "[obsidian_sync] auto: wrote {bytes} bytes for {n} chat(s) to {path}".format(
                    bytes=result.get("bytes_written"),
                    n=result.get("chats_count"),
                    path=result.get("daily_note_path"),
                ),
                file=sys.stderr,
            )

        # Always update marker so a transient failure doesn't loop forever.
        try:
            OBSIDIAN_LAST_SYNC_FILE.parent.mkdir(parents=True, exist_ok=True)
            OBSIDIAN_LAST_SYNC_FILE.write_text(today_iso, encoding="utf-8")
        except Exception as e:
            print(f"[obsidian_sync] could not write marker: {e}", file=sys.stderr)
    except Exception as e:
        print(f"[obsidian_sync] auto-sync crashed: {e}", file=sys.stderr)


def start_obsidian_auto_sync():
    """Spawn the auto-sync as a daemon thread so it never blocks startup."""
    threading.Thread(target=_obsidian_auto_sync_once, daemon=True).start()


# ── System tray ────────────────────────────────────────────


def build_tray(window):
    """Create and start the system-tray icon with Show / Quit menu.
    Returns the pystray Icon or None if tray setup fails."""
    try:
        import pystray
        from PIL import Image
    except ImportError:
        print("WARNING: pystray or Pillow not installed — tray icon disabled")
        return None

    icon_path = Path(BASE_DIR) / "icons" / "logos-32.png"
    if not icon_path.exists():
        print(f"WARNING: tray icon not found at {icon_path}")
        return None

    try:
        image = Image.open(icon_path)
    except Exception as e:
        print(f"WARNING: failed to load tray icon: {e}")
        return None

    def on_show(icon, item):
        # Pystray runs this on its own thread. Marshal to the GTK main loop
        # via _surface_window() so show()/restore() actually take effect.
        _surface_window()

    def on_quit(icon, item):
        icon.stop()
        _shutdown()

    menu = pystray.Menu(
        pystray.MenuItem("Show Logos", on_show, default=True),
        pystray.MenuItem("Quit", on_quit),
    )
    icon = pystray.Icon("Logos", image, "Logos", menu)

    try:
        threading.Thread(target=icon.run, daemon=True).start()
        return icon
    except Exception as e:
        print(f"WARNING: tray icon failed to start: {e}")
        return None


# ── Shutdown ───────────────────────────────────────────────

_shutdown_called = False


def _shutdown():
    """Clean exit: close pywebview windows and terminate the process."""
    global _shutdown_called
    if _shutdown_called:
        return
    _shutdown_called = True
    try:
        for w in webview.windows:
            try:
                w.destroy()
            except Exception:
                pass
    except Exception:
        pass
    os._exit(0)


# ── Flask launcher ─────────────────────────────────────────


def wait_for_http(host: str, port: int, timeout: float = 15.0) -> bool:
    """Block until Flask actually serves HTTP 200 on /, not just accepts TCP."""
    deadline = time.time() + timeout
    url = f"http://{host}:{port}/"
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=0.5) as r:
                if r.status == 200:
                    return True
        except Exception:
            time.sleep(0.1)
    return False


def run_flask(port: int):
    app.run(host="127.0.0.1", port=port, debug=False, threaded=True, use_reloader=False)


SPLASH_HTML = """<!DOCTYPE html>
<html><head><meta charset="UTF-8"><title>Logos</title>
<style>
  html,body{margin:0;height:100%;background:#0d0d0d;color:#666;
    font-family:'JetBrains Mono','Fira Code',monospace;
    display:flex;align-items:center;justify-content:center;
    flex-direction:column;gap:14px;font-size:12px;letter-spacing:.08em}
  .l{font-size:42px;color:#c8a96e;animation:pulse 1.4s ease-in-out infinite}
  @keyframes pulse{0%,100%{opacity:.45}50%{opacity:1}}
  .e{color:#e06c75;font-size:11px;max-width:80%;text-align:center;line-height:1.6}
</style></head>
<body>
  <div class="l">Λ</div>
  <div id="status">Starting Logos…</div>
</body></html>"""


def main():
    # Version comes from backend/version.py (single source of truth used by
    # both the .deb packaging and the in-app footer). The previous lookup
    # was cfg.load().get("version", "?"), which always returned "?" because
    # DEFAULTS in config.py has no "version" key — startup print was dead.
    print(f"Logos v{APP_VERSION} starting…", file=sys.stderr)

    # ── F1: Single-instance check (before anything else) ──
    listener = acquire_lock_or_signal()
    if listener is None:
        # Another instance is already running — we signalled it, now exit
        print("INFO: exiting (another instance already running)", file=sys.stderr)
        sys.exit(0)
    if listener is _SENTINEL_FALLBACK:
        # Single-instance mechanism is broken (already logged) — launch
        # non-exclusively without a listener
        print(
            "INFO: launching non-exclusively (single-instance unavailable)",
            file=sys.stderr,
        )
        listener = None
    else:
        print("INFO: single-instance lock acquired", file=sys.stderr)

    # ── GTK identity: align WM_CLASS with the .desktop StartupWMClass ──
    # Without this, pywebview's GTK backend sets WM_CLASS to its own default
    # ("MainWindow" / "pywebview") and the taskbar fails to associate the
    # window with /usr/share/applications/logos.desktop. The visible symptom
    # is that window.minimize() appears to "hide" the window — the window IS
    # iconified, but no taskbar entry exists to click it back. Setting the
    # program name BEFORE any GTK widget is created fixes this on GTK3.
    # Also sets the default window icon so the taskbar entry shows the logo.
    try:
        import gi

        gi.require_version("Gtk", "3.0")
        from gi.repository import GLib, Gtk  # noqa: E402

        GLib.set_prgname("Logos")  # → WM_CLASS instance
        GLib.set_application_name("Logos")  # → human-readable taskbar label

        icon_path = Path(BASE_DIR) / "icons" / "logos-128.png"
        if icon_path.exists():
            try:
                Gtk.Window.set_default_icon_from_file(str(icon_path))
            except Exception as e:
                print(f"WARNING: could not set default window icon: {e}")
    except (ImportError, ValueError) as e:
        # gi/Gtk not available (non-GTK backend, e.g. Qt on KDE). Skip silently.
        print(f"INFO: GTK identity setup skipped: {e}")

    # ── Start Flask ────────────────────────────────────────
    port = cfg.load().get("port", 17842)
    threading.Thread(target=run_flask, args=(port,), daemon=True).start()

    # ── Create window ──────────────────────────────────────
    window = webview.create_window(
        "Logos",
        html=SPLASH_HTML,
        width=960,
        height=700,
        min_size=(600, 400),
        maximized=True,
        js_api=Api(),
    )

    # ── F3: Close → minimize, with a fail-open escape hatch ───────
    # Previously this used window.hide() because hide+show is the pattern
    # tray-resident apps (Slack/Discord/Telegram) use. That worked when
    # the tray was up — but on this system pystray races pywebview for the
    # default GTK main context (see M11 below), and when the tray loses
    # the race the window also fails to start. Minimize() keeps the
    # window in the taskbar so the user always has a way to bring it
    # back, regardless of whether the tray is alive.
    #
    # Safety net: if minimize() raises (some pywebview/GTK combos do
    # under Wayland), let the close proceed instead of trapping the
    # user in a window that won't close and won't reappear.
    def on_closing():
        try:
            window.minimize()
            return False  # minimize succeeded → cancel the close
        except Exception as e:
            print(
                f"WARNING: window.minimize() failed ({e}) — allowing close",
                file=sys.stderr,
            )
            return True  # let the close proceed so the user isn't stuck

    try:
        window.events.closing += on_closing
    except Exception:
        # Fallback: if window.events.closing is not available, log and continue
        print("WARNING: window.events.closing not available — close will quit")

    # ── F1: Start the single-instance listener thread ──────
    if listener is not None:
        threading.Thread(
            target=_single_instance_listener, args=(listener,), daemon=True
        ).start()
        print("INFO: single-instance listener started", file=sys.stderr)

    # ── Obsidian: sync yesterday into today's daily note ──
    # Runs at most once per local day. Silent no-op if vault path is unset.
    start_obsidian_auto_sync()

    # ── on_ready: switch from splash to real UI ────────────
    def on_ready():
        # ── Tray icon (deferred from main() — see M11) ──────────
        # build_tray spawns a thread that calls pystray.Icon.run, which
        # under the appindicator backend calls Gtk.main() / acquires the
        # default GTK main context. If we did that BEFORE webview.start
        # the tray thread would steal the main context and pywebview's
        # GApplication.run would fail with
        #   GLib-GIO-CRITICAL: g_application_run() cannot acquire the
        #   default main context because it is already acquired by
        #   another thread!
        # → no window ever opens. Deferring to on_ready (which fires
        # only after pywebview's GTK loop is up and holding the default
        # context) means the tray thread either shares the loop cleanly
        # or fails to grab it — but in the latter case only the tray
        # icon is lost, not the whole window. Wrapped in try/except so
        # a tray failure here cannot abort the splash→UI transition
        # below.
        try:
            build_tray(window)
        except Exception as e:
            print(f"WARNING: build_tray crashed in on_ready: {e}", file=sys.stderr)

        if not wait_for_http("127.0.0.1", port):
            window.evaluate_js(
                "document.getElementById('status').innerHTML = "
                "'<div class=e>Backend failed to start within 15s.<br>"
                "Run <code>/usr/bin/python3 /usr/share/logos/app.py</code> "
                "from a terminal to see the error.</div>'"
            )
            return

        url = f"http://127.0.0.1:{port}/"

        # WebKit2GTK on NVIDIA occasionally renders a white window after the
        # splash → URL transition: load_url() succeeds at the HTTP level
        # (Flask logs show GET / 200), but WebKit's first paint of the new
        # document never lands and the user sees the white background of
        # the splash with no content. Even with the env-var mitigations in
        # logos.desktop (WEBKIT_DISABLE_DMABUF_RENDERER, GSK_RENDERER=cairo,
        # LIBGL_ALWAYS_SOFTWARE), the bug surfaces on some boots.
        #
        # Workaround: load the URL, then probe via evaluate_js whether the
        # real DOM (the #app element from index.html) is actually present.
        # If not, reload and probe again. Two extra reloads at most, with
        # widening delays, then give up and let the user F5 manually.
        window.load_url(url)

        # The probe below uses requestAnimationFrame + getBoundingClientRect
        # instead of a plain DOM check. WebKit2GTK on NVIDIA can finish
        # loading the document (DOM present, JS running, AJAX calls
        # firing) while the compositor never paints a single frame — the
        # window stays white. A bare `getElementById('app') !== null`
        # check passes in that state and misses the bug entirely (as we
        # saw in the wild). Waiting one animation frame and then asking
        # for layout dimensions is a much better proxy for "WebKit
        # actually rendered something on screen": the body has to be
        # mapped and laid out for clientWidth/Height to be non-zero.
        probe_js = """
            new Promise(resolve => {
                requestAnimationFrame(() => {
                    const app = document.getElementById('app');
                    const w = document.body && document.body.clientWidth;
                    const h = document.body && document.body.clientHeight;
                    resolve(app !== null && w > 0 && h > 0);
                });
            })
        """
        for attempt in range(3):
            time.sleep(0.6 + 0.4 * attempt)  # 0.6s, 1.0s, 1.4s
            try:
                rendered = window.evaluate_js(probe_js)
            except Exception as e:
                print(
                    f"WARNING: white-screen probe failed: {e}",
                    file=sys.stderr,
                )
                return
            if rendered:
                # As a belt-and-suspenders nudge, force a queue_draw on the
                # WebKit widget once the probe says we're good. Cheap, and
                # occasionally unsticks a compositor that loaded but never
                # repainted after the splash → URL swap.
                try:
                    from webview.platforms.gtk import BrowserView

                    bv = BrowserView.instances.get(window.uid)
                    if bv is not None and hasattr(bv, "webview"):
                        bv.webview.queue_draw()
                except Exception:
                    pass
                return
            print(
                f"WARNING: white window detected after load_url "
                f"(attempt {attempt + 1}/3) — reloading",
                file=sys.stderr,
            )
            try:
                window.load_url(url)
            except Exception as e:
                print(
                    f"WARNING: reload attempt {attempt + 1} raised: {e}",
                    file=sys.stderr,
                )
                return

        print(
            "WARNING: window still empty after 3 load_url attempts — "
            "user will need to refresh manually",
            file=sys.stderr,
        )

    webview.start(on_ready)


if __name__ == "__main__":
    main()
