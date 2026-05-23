import json
import os
import signal
import socket
import sys
import threading
import time
import urllib.request
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
import obsidian_sync
import ollama_client
import webview
from server import app

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

    def quit_app(self):
        """Called from JS frontend as fallback Quit (Ctrl+Q / Settings button)."""
        _shutdown()


# ── Single-instance ────────────────────────────────────────


def acquire_lock_or_signal() -> socket.socket | None:
    """Bind the abstract UNIX socket. Return listener if we're first;
    return None if another instance is already running (after asking it
    to surface)."""
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
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
        except OSError:
            pass
        finally:
            c.close()
        return None


def _single_instance_listener(listener: socket.socket):
    """Daemon thread: accept connections on the abstract socket and
    surface the window when a 'show' message arrives."""
    while True:
        try:
            conn, _ = listener.accept()
            data = conn.recv(256)
            conn.close()
            if data.startswith(b"show"):
                # Marshal to main thread via pywebview's evaluate_js
                try:
                    w = webview.windows[0]
                    w.show()
                    w.restore()
                except Exception:
                    pass
        except Exception:
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
        try:
            window.show()
            window.restore()
        except Exception:
            pass

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
    # ── F1: Single-instance check (before anything else) ──
    listener = acquire_lock_or_signal()
    if listener is None:
        # Another instance is running — we signalled it, now exit
        sys.exit(0)

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

    # ── F3: Close → minimize, with a fail-open escape hatch ────
    # If minimize() raises (some pywebview/GTK combos do under Wayland),
    # we let the close proceed instead of cancelling it. Without this
    # fallback a broken minimize() leaves the user with a window that
    # neither closes nor reappears in the taskbar.
    def on_closing():
        try:
            window.minimize()
            return False  # minimize succeeded → cancel the close
        except Exception as e:
            print(f"WARNING: window.minimize() failed ({e}) — allowing close")
            return True  # let the close proceed so the user isn't stuck

    try:
        window.events.closing += on_closing
    except Exception:
        # Fallback: if window.events.closing is not available, log and continue
        print("WARNING: window.events.closing not available — close will quit")

    # ── F1: Start the single-instance listener thread ──────
    threading.Thread(
        target=_single_instance_listener, args=(listener,), daemon=True
    ).start()

    # ── F2: Build tray icon ────────────────────────────────
    build_tray(window)

    # ── Obsidian: sync yesterday into today's daily note ──
    # Runs at most once per local day. Silent no-op if vault path is unset.
    start_obsidian_auto_sync()

    # ── on_ready: switch from splash to real UI ────────────
    def on_ready():
        if wait_for_http("127.0.0.1", port):
            window.load_url(f"http://127.0.0.1:{port}/")
        else:
            window.evaluate_js(
                "document.getElementById('status').innerHTML = "
                "'<div class=e>Backend failed to start within 15s.<br>"
                "Run <code>/usr/bin/python3 /usr/share/logos/app.py</code> "
                "from a terminal to see the error.</div>'"
            )

    webview.start(on_ready)


if __name__ == "__main__":
    main()
