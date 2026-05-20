import json
import os
import sys
import threading
import time
import urllib.request

BASE_DIR = sys._MEIPASS if getattr(sys, "frozen", False) else os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(BASE_DIR, "backend"))

import webview
from server import app

import chats as chats_store
import config as cfg
import file_extractor
import ollama_client


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
        safe = "".join(
            c if c.isalnum() or c in "-_" else "_"
            for c in (data.get("title") or "chat")
        )[:60].strip("_") or "chat"
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
    port = cfg.load().get("port", 17842)
    threading.Thread(target=run_flask, args=(port,), daemon=True).start()

    window = webview.create_window(
        "Logos",
        html=SPLASH_HTML,
        width=960,
        height=700,
        min_size=(600, 400),
        maximized=True,
        js_api=Api(),
    )

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
