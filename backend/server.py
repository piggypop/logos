import hashlib
import json
import os
import sys
import threading
from datetime import datetime
from pathlib import Path

import chats as chats_store
import comfyui_client
import config as cfg
import file_extractor
import image_storage
import image_workflows
import memory as mem
import notes as notes_store
import ollama_client
import open_notebook_client
import prompts
import search_providers
import tool_router
import url_fetcher
import version as version_mod
from flask import (
    Flask,
    Response,
    jsonify,
    request,
    send_file,
    send_from_directory,
    stream_with_context,
)
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


def _detect_language(text: str) -> str:
    """Simple heuristic: if Greek characters present, return 'Greek', else 'English'."""
    greek_chars = set("αβγδεζηθικλμνξοπρστυφχψωάέήίόύώΑΒΓΔΕΖΗΘΙΚΛΜΝΞΟΠΡΣΤΥΦΧΨΩΆΈΉΊΌΎΏ")
    if any(c in greek_chars for c in text):
        return "Greek"
    return "English"


def _build_system_prompt(
    c: dict,
    sources_block: str = "",
    has_notebook: bool = False,
    user_message: str = "",
    source_quality_block: str = "",
) -> str:
    """Compose the final system message via the central prompt module."""
    now = datetime.now().astimezone()
    date_info = {
        "iso": now.isoformat(),
        "human": now.strftime("%A, %d %B %Y, %H:%M"),
        "tz": now.strftime("%Z"),
    }
    detected_language = _detect_language(user_message)
    return prompts.compose_system_prompt(
        user_system_prompt=c.get("system_prompt") or "",
        date_info=date_info,
        location=(c.get("user_location") or "").strip(),
        memory_facts=mem.load(),
        has_sources=bool(sources_block),
        has_notebook=has_notebook,
        sources_block=sources_block,
        detected_language=detected_language,
        source_quality_block=source_quality_block,
    )


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
    results = search_providers.search(query, c)
    return jsonify({"results": results, "provider": c.get("search_provider", "ddg")})


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
    if not chats_store.is_valid_id(chat_id):
        return jsonify({"error": "invalid chat id"}), 400
    body = request.json or {}
    messages = body.get("messages", [])
    title = body.get("title")
    data = chats_store.save(chat_id, messages, title)
    if data is None:
        return jsonify({"error": "save failed"}), 400
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
    # Best-effort cleanup of any generated images attached to this chat
    try:
        image_storage.delete_for_chat(chat_id)
    except Exception as e:
        print(f"[server] image cleanup error: {e}", file=sys.stderr)
        sys.stderr.flush()
    return jsonify({"ok": True})


# ── Open Notebook ────────────────────────────────────────


@app.get("/api/notebooks")
def list_notebooks_endpoint():
    c = cfg.load()
    url = c.get("open_notebook_url") or ""
    ok = open_notebook_client.ping(url)
    if not ok:
        return jsonify(
            {"ok": False, "error": "Open Notebook unreachable", "notebooks": []}
        )
    notebooks = open_notebook_client.list_notebooks(url)
    return jsonify({"ok": True, "notebooks": notebooks})


@app.get("/api/notebooks/<path:notebook_id>/preview")
def notebook_preview_endpoint(notebook_id):
    """Fetch notebook content (cached) and return size info — used by UI to
    show 'X sources, ~Y tokens' next to the dropdown."""
    c = cfg.load()
    nb = open_notebook_client.get_notebook_with_content(
        c.get("open_notebook_url") or "", notebook_id
    )
    if not nb:
        return jsonify({"ok": False, "error": "could not load notebook"}), 404
    return jsonify(
        {
            "ok": True,
            "id": nb["id"],
            "name": nb["name"],
            "source_count": len(nb.get("sources", [])),
            "total_chars": nb.get("total_chars", 0),
            "total_tokens_est": nb.get("total_tokens_est", 0),
        }
    )


@app.post("/api/notebooks/refresh")
def notebook_refresh_endpoint():
    """Invalidate the in-memory notebook cache so the next preview / chat call
    re-fetches sources from Open Notebook. Use after editing the notebook in
    Open Notebook's UI."""
    open_notebook_client.invalidate_cache()
    return jsonify({"ok": True})


# ── ComfyUI (image generation) ───────────────────────────


@app.get("/api/comfyui/status")
def comfyui_status():
    c = cfg.load()
    url = c.get("comfyui_url") or ""
    # ?refresh=1 forces a fresh /object_info fetch (bypassing the 30s cache)
    if request.args.get("refresh") == "1":
        comfyui_client.invalidate_object_info_cache(url)
    ok = comfyui_client.ping(url)
    if not ok:
        return jsonify({"ok": False, "error": "ComfyUI unreachable"})
    d = comfyui_client.discover(url)
    if not d.get("ok"):
        return jsonify({"ok": True, "discovered": False, "error": d.get("error", "")})
    return jsonify(
        {
            "ok": True,
            "discovered": True,
            "checkpoints": d["checkpoints"],
            "samplers": d["samplers"],
            "schedulers": d["schedulers"],
        }
    )


@app.post("/api/comfyui/generate")
def comfyui_generate():
    """SSE stream:
        {type: 'queued', prompt_id}
        {type: 'progress', value, max, node}
        {type: 'image', path, prompt, params}
        {type: 'error', message}
    Body: {prompt, chat_id?, overrides?: {checkpoint, steps, width, height, ...}}
    """
    body = request.json or {}
    user_prompt = (body.get("prompt") or "").strip()
    chat_id = body.get("chat_id") or None
    overrides = body.get("overrides") or {}

    if not user_prompt:
        return jsonify({"error": "prompt required"}), 400

    c = cfg.load()
    url = c.get("comfyui_url") or ""
    wf_name = c.get("comfyui_workflow") or "sdxl-default"
    template = image_workflows.get_template(
        wf_name, c.get("comfyui_custom_workflow") or ""
    )
    if template is None:
        return jsonify({"error": f"workflow '{wf_name}' invalid"}), 400

    seed = overrides.get("seed") or comfyui_client.new_seed()
    params = {
        "prompt": user_prompt,
        "negative": overrides.get("negative", c.get("comfyui_negative_prompt", "")),
        "checkpoint": overrides.get("checkpoint", c.get("comfyui_checkpoint", "")),
        "seed": int(seed),
        "steps": int(overrides.get("steps", c.get("comfyui_steps", 30))),
        "cfg": float(overrides.get("cfg", c.get("comfyui_cfg", 7.5))),
        "sampler": overrides.get("sampler", c.get("comfyui_sampler", "euler")),
        "scheduler": overrides.get("scheduler", c.get("comfyui_scheduler", "normal")),
        "width": int(overrides.get("width", c.get("comfyui_width", 1024))),
        "height": int(overrides.get("height", c.get("comfyui_height", 1024))),
    }
    workflow = image_workflows.render(template, params)
    client_id = comfyui_client.new_client_id()

    def generate():
        try:
            prompt_id = comfyui_client.submit_prompt(url, workflow, client_id)
            if not prompt_id:
                yield f"data: {json.dumps({'type': 'error', 'message': 'ComfyUI rejected the workflow (check checkpoint name and that all required custom nodes are installed)'})}\n\n"
                sys.stdout.flush()
                return
            yield f"data: {json.dumps({'type': 'queued', 'prompt_id': prompt_id})}\n\n"
            sys.stdout.flush()

            image_outputs = []
            for ev in comfyui_client.stream_progress(url, client_id, prompt_id):
                if ev["type"] == "progress":
                    yield f"data: {json.dumps(ev)}\n\n"
                    sys.stdout.flush()
                elif ev["type"] == "executed":
                    out = ev.get("output", {}) or {}
                    for img in out.get("images", []) or []:
                        image_outputs.append(img)
                elif ev["type"] == "error":
                    yield f"data: {json.dumps(ev)}\n\n"
                    sys.stdout.flush()
                    return
                elif ev["type"] == "done":
                    break

            if not image_outputs:
                yield f"data: {json.dumps({'type': 'error', 'message': 'workflow completed but no images returned'})}\n\n"
                sys.stdout.flush()
                return

            for img in image_outputs:
                data = comfyui_client.fetch_image(
                    url,
                    filename=img.get("filename", ""),
                    subfolder=img.get("subfolder", ""),
                    folder_type=img.get("type", "output"),
                )
                if not data:
                    continue
                ext = (img.get("filename", "").rsplit(".", 1) + ["png"])[-1].lower()
                saved = image_storage.save(data, chat_id, seed, ext=ext)
                payload = {
                    "type": "image",
                    "path": str(saved),
                    "filename": saved.name,
                    "prompt": user_prompt,
                    "params": {
                        "seed": params["seed"],
                        "steps": params["steps"],
                        "width": params["width"],
                        "height": params["height"],
                        "checkpoint": params["checkpoint"],
                        "sampler": params["sampler"],
                        "cfg": params["cfg"],
                        "workflow": wf_name,
                    },
                }
                yield f"data: {json.dumps(payload)}\n\n"
                sys.stdout.flush()
        except Exception as e:
            yield f"data: {json.dumps({'type': 'error', 'message': str(e)})}\n\n"
            sys.stdout.flush()

    return Response(
        stream_with_context(generate()),
        mimetype="text/event-stream",
        headers={"X-Accel-Buffering": "no", "Cache-Control": "no-cache"},
    )


@app.get("/api/images/<path:rel_path>")
def serve_image(rel_path):
    """Serve a generated image from IMAGES_ROOT. Path traversal-protected."""
    full = image_storage.IMAGES_ROOT / rel_path
    if not image_storage.is_safe_path(full) or not full.exists():
        return jsonify({"error": "not found"}), 404
    return send_file(str(full))


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


# ── Notes ──────────────────────────────────────────────────


@app.get("/api/notes")
def api_notes_list():
    """GET /api/notes?q=search_term → list all notes, optionally search."""
    q = request.args.get("q", "").strip()
    if q:
        results = notes_store.search(q)
    else:
        results = notes_store.list_all()
    return jsonify(results)


@app.post("/api/notes")
def api_notes_create():
    """POST /api/notes → create a new note.

    Body: {question, answer, sources?, chat_id?, model?}
    Returns: the created note dict with 201 status.
    """
    data = request.get_json(silent=True) or {}
    question = data.get("question", "").strip()
    answer = data.get("answer", "").strip()
    if not question or not answer:
        return jsonify({"error": "question and answer are required"}), 400

    note = notes_store.create(
        user_message=question,
        assistant_message=answer,
        sources=data.get("sources") or [],
        chat_id=data.get("chat_id", ""),
        chat_title=None,
        model=data.get("model", ""),
    )
    return jsonify(note), 201


@app.get("/api/notes/\u003cnote_id\u003e")
def api_notes_get(note_id):
    """GET /api/notes/\u003cid\u003e → full note dict or 404."""
    if not notes_store._is_valid_id(note_id):
        return jsonify({"error": "invalid note id"}), 400
    note = notes_store.get(note_id)
    if note is None:
        return jsonify({"error": "note not found"}), 404
    return jsonify(note)


@app.delete("/api/notes/\u003cnote_id\u003e")
def api_notes_delete(note_id):
    """DELETE /api/notes/\u003cid\u003e → delete a note."""
    if not notes_store._is_valid_id(note_id):
        return jsonify({"error": "invalid note id"}), 400
    ok = notes_store.delete(note_id)
    if not ok:
        return jsonify({"error": "note not found"}), 404
    return jsonify({"deleted": note_id})


@app.get("/api/notes/<note_id>/export")
def api_notes_export(note_id):
    """GET /api/notes/<id>/export?fmt=txt|pdf → download note as file."""
    if not notes_store._is_valid_id(note_id):
        return jsonify({"error": "invalid note id"}), 400

    note = notes_store.get(note_id)
    if note is None:
        return jsonify({"error": "note not found"}), 404

    fmt = request.args.get("fmt", "txt").lower()
    if fmt not in ("txt", "pdf"):
        return jsonify({"error": "fmt must be txt or pdf"}), 400

    if fmt == "txt":
        content = notes_store.render_txt(note)
        filename = notes_store._fmt_export_filename(note, "txt")
        return Response(
            content,
            mimetype="text/plain; charset=utf-8",
            headers={
                "Content-Disposition": f'attachment; filename="{filename}"',
            },
        )

    if fmt == "pdf":
        try:
            pdf_bytes = notes_store.render_pdf(note)
        except Exception as e:
            return jsonify({"error": f"PDF generation failed: {e}"}), 500
        filename = notes_store._fmt_export_filename(note, "pdf")
        return Response(
            pdf_bytes,
            mimetype="application/pdf",
            headers={
                "Content-Disposition": f'attachment; filename="{filename}"',
            },
        )


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
            # ── Manual memory trigger ("remember ..." / "να θυμάσαι ...") ──
            remember_text = mem.detect_remember(last_user_message)
            if remember_text:
                if mem.add(remember_text, source="manual"):
                    yield f"data: {json.dumps({'type': 'remembered', 'fact': remember_text})}\n\n"
                    sys.stdout.flush()

            # ── Active notebook (Open Notebook) ──────────
            notebook_sources: list[dict] = []
            active_nb_id = c.get("active_notebook_id") or ""
            if active_nb_id:
                yield f"data: {json.dumps({'type': 'loading_notebook', 'notebook_id': active_nb_id})}\n\n"
                sys.stdout.flush()
                nb = open_notebook_client.get_notebook_with_content(
                    c.get("open_notebook_url") or "", active_nb_id
                )
                if nb:
                    notebook_sources = open_notebook_client.as_chat_sources(
                        nb, c.get("open_notebook_ui_url") or ""
                    )

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
            elif c["auto_search_enabled"] and not url_contents and not notebook_sources:
                do_web_search = tool_router.needs_search(
                    last_user_message, c["ollama_host"], c["ollama_model"]
                )

            if do_web_search:
                yield f"data: {json.dumps({'type': 'searching'})}\n\n"
                sys.stdout.flush()
                query = tool_router.reformulate_query(
                    messages, c["ollama_host"], c["ollama_model"]
                )
                search_results = search_providers.search(query, c)

            # ── Combine sources (notebook first, then URLs, then search) ─
            all_sources = notebook_sources + url_contents + search_results
            if all_sources:
                yield f"data: {json.dumps({'type': 'sources', 'query': query, 'sources': all_sources})}\n\n"
                sys.stdout.flush()

            # ── System prompt (composed via prompts module) ──
            sources_block = (
                search_providers.format_as_context(all_sources) if all_sources else ""
            )

            # ── Source quality summary (Phase C2) ──
            source_quality_block = (
                search_providers.source_quality_summary(all_sources)
                if all_sources
                else ""
            )

            system_prompt = _build_system_prompt(
                c,
                sources_block=sources_block,
                source_quality_block=source_quality_block,
                has_notebook=bool(notebook_sources),
                user_message=last_user_message,
            )

            # ── Debug: log assembled system prompt (Phase A1) ──
            if c.get("debug_log_prompts"):
                try:
                    debug_dir = Path.home() / ".local" / "share" / "logos" / "debug"
                    debug_dir.mkdir(parents=True, exist_ok=True)
                    log_path = debug_dir / "prompts.log"
                    # Rotate at ~5 MB: keep only the last ~1 MB
                    if log_path.exists() and log_path.stat().st_size > 5 * 1024 * 1024:
                        raw = log_path.read_bytes()
                        keep = raw[-1024 * 1024 :]  # last 1 MB
                        # Start at the next newline so we don't split a JSON line
                        nl = keep.find(b"\n")
                        log_path.write_bytes(keep[nl + 1 :] if nl != -1 else keep)
                    entry = {
                        "timestamp": datetime.now().astimezone().isoformat(),
                        "model": c["ollama_model"],
                        "last_user_hash": hashlib.sha256(
                            last_user_message.encode("utf-8", errors="replace")
                        ).hexdigest()[:12],
                        "system_prompt": system_prompt,
                    }
                    with open(log_path, "a", encoding="utf-8") as lf:
                        lf.write(json.dumps(entry, ensure_ascii=False) + "\n")
                except Exception:
                    pass  # debug logging must never break the chat stream

            # ── Inject attachments (text inline, images via Ollama images field)
            ollama_messages = file_extractor.build_ollama_messages(messages)

            # ── Stream tokens (character by character) ───
            assistant_buffer = []
            for token in ollama_client.stream_chat(
                ollama_messages,
                c["ollama_model"],
                system_prompt,
                c["ollama_host"],
                cfg.effective(c, "temperature", c["ollama_model"]),
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
