import json
import os
import socket
import sys
import threading
import time

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


def wait_for_port(host: str, port: int, timeout: float = 5.0) -> bool:
    start = time.time()
    while time.time() - start < timeout:
        try:
            with socket.create_connection((host, port), timeout=0.2):
                return True
        except OSError:
            time.sleep(0.05)
    return False


def run_flask(port: int):
    app.run(host="127.0.0.1", port=port, debug=False, threaded=True, use_reloader=False)


def main():
    port = cfg.load().get("port", 17842)
    threading.Thread(target=run_flask, args=(port,), daemon=True).start()
    wait_for_port("127.0.0.1", port)
    webview.create_window(
        "Logos",
        f"http://127.0.0.1:{port}/",
        width=960,
        height=700,
        min_size=(600, 400),
        js_api=Api(),
    )
    webview.start()


if __name__ == "__main__":
    main()
