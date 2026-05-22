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

    # ── F3: Close → minimize ───────────────────────────────
    def on_closing():
        try:
            window.minimize()
        except Exception:
            pass
        return False  # cancel the close

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
