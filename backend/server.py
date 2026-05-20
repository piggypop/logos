import json
import os
import sys
import threading
from datetime import datetime

import chats as chats_store
import config as cfg
import file_extractor
import memory as mem
import ollama_client
import searxng_client
import tool_router
import url_fetcher
import version as version_mod
from flask import Flask, Response, jsonify, request, send_from_directory, stream_with_context
from flask_cors import CORS


def _extract_facts_bg(messages: list[dict], c: dict):
    """Background extraction of persistent user facts."""
    try:
        existing = [f["text"] for f in mem.load()]
        new_facts = tool_router.extract_facts(
            messages, c["ollama_host"], c["ollama_model"], existing
        )
        for f in new_facts:
            mem.add(f, source="auto")
    except Exception as e:
        print(f"[memory] auto-extract error: {e}", file=sys.stderr)
        sys.stderr.flush()


def _system_context(c: dict) -> str:
    now = datetime.now().astimezone()
    parts = [f"Current date and time: {now.strftime('%A, %d %B %Y, %H:%M %Z')}"]
    loc = (c.get("user_location") or "").strip()
    if loc:
        parts.append(f"User location: {loc}")
    mem_block = mem.format_for_prompt(mem.load())
    if mem_block:
        parts.append(mem_block)
    return "\n\n".join(parts)

if getattr(sys, "frozen", False):
    FRONTEND_DIR = os.path.join(sys._MEIPASS, "frontend")
else:
    FRONTEND_DIR = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "frontend",
    )

app = Flask(__name__, static_folder=FRONTEND_DIR, static_url_path="")
CORS(app)


@app.get("/")
def index():
    return send_from_directory(FRONTEND_DIR, "index.html")


@app.get("/api/version")
def get_version():
    return jsonify({"app": version_mod.APP_NAME, "version": version_mod.VERSION})

# ── Config ──────────────────────────────────────────────


@app.get("/api/config")
def get_config():
    return jsonify(cfg.load())


@app.post("/api/config")
def post_config():
    data = request.json
    current = cfg.load()
    current.update(data)
    cfg.save(current)
    return jsonify({"ok": True})


# ── Models ───────────────────────────────────────────────


@app.get("/api/models")
def get_models():
    c = cfg.load()
    models = ollama_client.list_models(c["ollama_host"])
    return jsonify({"models": models})


@app.get("/api/capabilities")
def get_capabilities():
    c = cfg.load()
    caps = ollama_client.get_capabilities(c["ollama_host"], c["ollama_model"])
    accept = ["text/*", ".pdf", ".docx", ".md", ".csv", ".json", ".log"]
    if "vision" in caps:
        accept.extend(["image/*"])
    return jsonify(
        {
            "model": c["ollama_model"],
            "capabilities": caps,
            "supports_images": "vision" in caps,
            "supports_audio": "audio" in caps,
            "accept": accept,
        }
    )


# ── Search ───────────────────────────────────────────────


@app.post("/api/search")
def do_search():
    data = request.json
    query = data.get("query", "")
    c = cfg.load()
    results = searxng_client.search(query, c["searxng_url"], c["searxng_results_count"])
    return jsonify({"results": results})


# ── Chats (archive) ──────────────────────────────────────


@app.get("/api/chats")
def list_chats_endpoint():
    return jsonify({"chats": chats_store.list_chats()})


@app.get("/api/chats/<chat_id>")
def get_chat_endpoint(chat_id):
    data = chats_store.get(chat_id)
    if not data:
        return jsonify({"error": "not found"}), 404
    return jsonify(data)


@app.put("/api/chats/<chat_id>")
def upsert_chat_endpoint(chat_id):
    body = request.json or {}
    messages = body.get("messages", [])
    title = body.get("title")
    data = chats_store.save(chat_id, messages, title)
    return jsonify(data)


@app.post("/api/chats/<chat_id>/rename")
def rename_chat_endpoint(chat_id):
    body = request.json or {}
    title = (body.get("title") or "").strip()
    if not title:
        return jsonify({"error": "title required"}), 400
    data = chats_store.rename(chat_id, title)
    if not data:
        return jsonify({"error": "not found"}), 404
    return jsonify(data)


@app.delete("/api/chats/<chat_id>")
def delete_chat_endpoint(chat_id):
    if not chats_store.delete(chat_id):
        return jsonify({"error": "not found"}), 404
    return jsonify({"ok": True})


# ── Memory ───────────────────────────────────────────────


@app.get("/api/memory")
def get_memory():
    return jsonify({"facts": mem.load()})


@app.delete("/api/memory/<fact_id>")
def delete_memory(fact_id):
    if not mem.remove(fact_id):
        return jsonify({"error": "not found"}), 404
    return jsonify({"ok": True})


@app.post("/api/memory")
def add_memory():
    body = request.json or {}
    text = (body.get("text") or "").strip()
    if not text:
        return jsonify({"error": "text required"}), 400
    added = mem.add(text, source="manual")
    return jsonify({"ok": True, "added": added})


# ── Chat (SSE streaming) ─────────────────────────────────


@app.post("/api/chat")
def chat():
    """
    Request body:
    {
        "messages": [{"role": "user"|"assistant", "content": str}, ...],
        "force_search": bool   // optional, default false
    }

    Response: text/event-stream
    Events:
        data: {"type": "searching"}          // quando inizia la ricerca
        data: {"type": "token", "content": "..."}
        data: {"type": "done"}
        data: {"type": "error", "message": "..."}
    """
    body = request.json
    messages = body.get("messages", [])
    force_search = body.get("force_search", False)

    if not messages:
        return jsonify({"error": "no messages"}), 400

    last_user_message = next(
        (m["content"] for m in reversed(messages) if m["role"] == "user"), ""
    )

    def generate():
        c = cfg.load()

        try:
            # ── Manual memory trigger ("να θυμάσαι ...") ──
            remember_text = mem.detect_remember(last_user_message)
            if remember_text:
                if mem.add(remember_text, source="manual"):
                    yield f"data: {json.dumps({'type': 'remembered', 'fact': remember_text})}\n\n"
                    sys.stdout.flush()

            # Build context AFTER any manual memory add (so it's included)
            sys_ctx = _system_context(c)

            # ── URL fetching ─────────────────────────────
            url_contents: list[dict] = []
            urls = url_fetcher.extract_urls(last_user_message)
            if urls:
                yield f"data: {json.dumps({'type': 'fetching_urls', 'urls': urls})}\n\n"
                sys.stdout.flush()
                url_contents = url_fetcher.fetch_many(urls)

            # ── Search decision ──────────────────────────
            # If URLs were fetched successfully, skip auto-search to reduce noise.
            # force_search always runs.
            do_web_search = False
            search_results: list[dict] = []
            query = ""

            if force_search:
                do_web_search = True
            elif c["auto_search_enabled"] and not url_contents:
                do_web_search = tool_router.needs_search(
                    last_user_message, c["ollama_host"], c["ollama_model"]
                )

            if do_web_search:
                yield f"data: {json.dumps({'type': 'searching'})}\n\n"
                sys.stdout.flush()
                query = tool_router.reformulate_query(
                    messages, c["ollama_host"], c["ollama_model"]
                )
                search_results = searxng_client.search(
                    query, c["searxng_url"], c["searxng_results_count"]
                )

            # ── Combine sources (URLs first, then search) ─
            all_sources = url_contents + search_results
            if all_sources:
                yield f"data: {json.dumps({'type': 'sources', 'query': query, 'sources': all_sources})}\n\n"
                sys.stdout.flush()

            # ── System prompt ────────────────────────────
            if all_sources:
                context_str = searxng_client.format_as_context(all_sources)
                base = c["search_system_prompt"] + "\n\n" + context_str
            else:
                base = c["system_prompt"]
            system_prompt = sys_ctx + "\n\n" + base

            # ── Inject attachments (text inline, images via Ollama images field)
            ollama_messages = file_extractor.build_ollama_messages(messages)

            # ── Stream tokens (character by character) ───
            assistant_buffer = []
            for token in ollama_client.stream_chat(
                ollama_messages,
                c["ollama_model"],
                system_prompt,
                c["ollama_host"],
                c["temperature"],
            ):
                assistant_buffer.append(token)
                yield f"data: {json.dumps({'type': 'token', 'content': token})}\n\n"
                sys.stdout.flush()

            yield f"data: {json.dumps({'type': 'done'})}\n\n"
            sys.stdout.flush()

            # ── Background fact extraction ───────────────
            full_exchange = messages + [
                {"role": "assistant", "content": "".join(assistant_buffer)}
            ]
            threading.Thread(
                target=_extract_facts_bg,
                args=(full_exchange, c),
                daemon=True,
            ).start()

        except Exception as e:
            yield f"data: {json.dumps({'type': 'error', 'message': str(e)})}\n\n"
            sys.stdout.flush()

    return Response(
        stream_with_context(generate()),
        mimetype="text/event-stream",
        headers={"X-Accel-Buffering": "no", "Cache-Control": "no-cache"},
    )


# ── Entry point ──────────────────────────────────────────

if __name__ == "__main__":
    c = cfg.load()
    port = c.get("port", 17842)
    app.run(host="127.0.0.1", port=port, debug=False, threaded=True)
