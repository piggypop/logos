"""Client for ComfyUI REST + WebSocket API.

ComfyUI does not have a generic "generate from prompt" endpoint. You submit a
complete workflow JSON (a graph of nodes) via POST /prompt, then receive a
prompt_id back. Progress updates flow over WebSocket. Final images are fetched
from /view by filename.

For Logos integration we keep one or more workflow templates (with placeholders
like {{PROMPT}}, {{CHECKPOINT}}, {{SEED}}, etc.) and substitute before submit.

WebSocket binary frame format (ComfyUI preview images):
  bytes 0-3  : event type (uint32 big-endian); 1 = PREVIEW_IMAGE
  bytes 4-7  : image format (uint32 big-endian); 1 = JPEG, 2 = PNG
  bytes 8+   : raw image bytes (JPEG or PNG)
"""
import base64
import io
import json
import random
import struct
import sys
import time
from urllib.parse import urlencode, urlparse

import httpx


def _normalize(base_url: str) -> str:
    return (base_url or "").rstrip("/")


def _ws_url(base_url: str, client_id: str) -> str:
    """Convert http(s)://host:port → ws(s)://host:port/ws?clientId=..."""
    p = urlparse(_normalize(base_url))
    scheme = "wss" if p.scheme == "https" else "ws"
    return f"{scheme}://{p.netloc}/ws?clientId={client_id}"


def ping(base_url: str, timeout: float = 3.0) -> bool:
    if not base_url:
        return False
    try:
        r = httpx.get(f"{_normalize(base_url)}/system_stats", timeout=timeout)
        return r.status_code == 200
    except Exception:
        return False


_OBJECT_INFO_CACHE: dict[str, tuple[float, dict]] = {}
_OBJECT_INFO_TTL = 30.0  # seconds


def object_info(base_url: str, timeout: float = 8.0) -> dict | None:
    """Returns ComfyUI's node registry. Cached for 30s — the registry is large
    (~5MB JSON) and rarely changes during a session. Used to discover available
    checkpoints, samplers, schedulers, and detect missing custom nodes."""
    key = _normalize(base_url)
    now = time.time()
    cached = _OBJECT_INFO_CACHE.get(key)
    if cached and now - cached[0] < _OBJECT_INFO_TTL:
        return cached[1]
    try:
        r = httpx.get(f"{key}/object_info", timeout=timeout)
        r.raise_for_status()
        data = r.json()
        _OBJECT_INFO_CACHE[key] = (now, data)
        return data
    except Exception as e:
        print(f"[comfyui_client] object_info error: {e}", file=sys.stderr)
        sys.stderr.flush()
        return None


def invalidate_object_info_cache(base_url: str = ""):
    """Clear discovery cache. Pass base_url to clear one entry; empty to clear all."""
    global _OBJECT_INFO_CACHE
    if not base_url:
        _OBJECT_INFO_CACHE = {}
    else:
        _OBJECT_INFO_CACHE.pop(_normalize(base_url), None)


def list_checkpoints(base_url: str) -> list[str]:
    """Returns list of installed checkpoint filenames."""
    info = object_info(base_url)
    if not info:
        return []
    try:
        return list(
            info["CheckpointLoaderSimple"]["input"]["required"]["ckpt_name"][0]
        )
    except Exception:
        return []


def list_samplers(base_url: str) -> list[str]:
    info = object_info(base_url)
    if not info:
        return []
    try:
        return list(info["KSampler"]["input"]["required"]["sampler_name"][0])
    except Exception:
        return []


def list_schedulers(base_url: str) -> list[str]:
    info = object_info(base_url)
    if not info:
        return []
    try:
        return list(info["KSampler"]["input"]["required"]["scheduler"][0])
    except Exception:
        return []


def list_loras(base_url: str) -> list[str]:
    """Returns list of installed LoRA filenames."""
    info = object_info(base_url)
    if not info:
        return []
    try:
        return list(info["LoraLoader"]["input"]["required"]["lora_name"][0])
    except Exception:
        return []


def discover(base_url: str) -> dict:
    """Convenience: bundle all discovery results."""
    info = object_info(base_url)
    if not info:
        return {"ok": False, "error": "ComfyUI unreachable or not responding"}

    def _safe(key, path):
        try:
            ref = info[key]
            for p in path:
                ref = ref[p]
            return list(ref[0]) if ref else []
        except Exception:
            return []

    return {
        "ok": True,
        "checkpoints": _safe("CheckpointLoaderSimple", ["input", "required", "ckpt_name"]),
        "samplers": _safe("KSampler", ["input", "required", "sampler_name"]),
        "schedulers": _safe("KSampler", ["input", "required", "scheduler"]),
        "loras": _safe("LoraLoader", ["input", "required", "lora_name"]),
        "node_types": sorted(info.keys()),
    }


def submit_prompt(
    base_url: str, workflow: dict, client_id: str, timeout: float = 15.0
) -> str | None:
    """Submit a workflow JSON. Returns prompt_id or None on error."""
    try:
        r = httpx.post(
            f"{_normalize(base_url)}/prompt",
            json={"prompt": workflow, "client_id": client_id},
            timeout=timeout,
        )
        r.raise_for_status()
        return r.json().get("prompt_id")
    except httpx.HTTPStatusError as e:
        body = e.response.text[:500] if e.response is not None else ""
        print(f"[comfyui_client] submit_prompt {e}: {body}", file=sys.stderr)
        sys.stderr.flush()
        return None
    except Exception as e:
        print(f"[comfyui_client] submit_prompt error: {e}", file=sys.stderr)
        sys.stderr.flush()
        return None


def stream_progress(base_url: str, client_id: str, prompt_id: str, timeout: float = 600.0):
    """
    Yields dicts with progress info until execution completes:
      {"type": "progress", "value": int, "max": int, "node": str}
      {"type": "executing", "node": str}
      {"type": "executed", "node": str, "output": dict}
      {"type": "done"}
      {"type": "error", "message": str}

    Uses websocket-client (sync). If not installed, falls back to polling /history.
    """
    try:
        from websocket import create_connection  # provided by websocket-client
    except ImportError:
        yield from _poll_progress(base_url, prompt_id, timeout)
        return

    try:
        ws = create_connection(_ws_url(base_url, client_id), timeout=10)
    except Exception as e:
        print(f"[comfyui_client] websocket connect error: {e}", file=sys.stderr)
        sys.stderr.flush()
        yield from _poll_progress(base_url, prompt_id, timeout)
        return

    deadline = time.time() + timeout
    try:
        while time.time() < deadline:
            try:
                ws.settimeout(5)
                raw = ws.recv()
            except Exception:
                continue
            if not isinstance(raw, str):
                # binary frame = preview image (ComfyUI sends these during sampling)
                # Format: 4-byte event type + 4-byte image format + raw image bytes
                try:
                    if len(raw) > 8:
                        event_type = struct.unpack(">I", raw[:4])[0]
                        img_format = struct.unpack(">I", raw[4:8])[0]
                        if event_type == 1:  # PREVIEW_IMAGE
                            mime = "image/png" if img_format == 2 else "image/jpeg"
                            b64 = base64.b64encode(raw[8:]).decode("ascii")
                            yield {"type": "preview", "data": b64, "mime": mime}
                except Exception:
                    pass
                continue
            try:
                msg = json.loads(raw)
            except Exception:
                continue
            mtype = msg.get("type")
            data = msg.get("data") or {}
            if data.get("prompt_id") and data.get("prompt_id") != prompt_id:
                continue
            if mtype == "progress":
                yield {
                    "type": "progress",
                    "value": data.get("value", 0),
                    "max": data.get("max", 0),
                    "node": data.get("node", ""),
                }
            elif mtype == "executing":
                node = data.get("node")
                if node is None:
                    yield {"type": "done"}
                    return
                yield {"type": "executing", "node": node}
            elif mtype == "executed":
                yield {
                    "type": "executed",
                    "node": data.get("node", ""),
                    "output": data.get("output", {}),
                }
            elif mtype == "execution_error":
                yield {
                    "type": "error",
                    "message": data.get("exception_message", "execution error"),
                }
                return
    finally:
        try:
            ws.close()
        except Exception:
            pass
    yield {"type": "error", "message": "timeout"}


def _poll_progress(base_url: str, prompt_id: str, timeout: float):
    """Fallback when no WebSocket: poll /history every second."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            r = httpx.get(
                f"{_normalize(base_url)}/history/{prompt_id}", timeout=5.0
            )
            if r.status_code == 200:
                data = r.json()
                if prompt_id in data:
                    entry = data[prompt_id]
                    status = entry.get("status", {})
                    if status.get("completed"):
                        # Synthesize an "executed" event for each output
                        outputs = entry.get("outputs", {})
                        for node_id, out in outputs.items():
                            yield {"type": "executed", "node": node_id, "output": out}
                        yield {"type": "done"}
                        return
                    if status.get("status_str") == "error":
                        yield {"type": "error", "message": "execution error"}
                        return
        except Exception:
            pass
        time.sleep(1.0)
    yield {"type": "error", "message": "timeout"}


def upload_image(
    base_url: str,
    image_bytes: bytes,
    filename: str = "input.png",
    overwrite: bool = True,
    timeout: float = 30.0,
) -> str | None:
    """Upload an image to ComfyUI's input/ directory via POST /upload/image.
    Returns the stored filename (as ComfyUI refers to it) or None on error.
    Use the returned name as the value of a LoadImage node's 'image' input.
    """
    try:
        files = {"image": (filename, io.BytesIO(image_bytes), "image/png")}
        data = {"type": "input", "overwrite": "true" if overwrite else "false"}
        r = httpx.post(
            f"{_normalize(base_url)}/upload/image",
            files=files,
            data=data,
            timeout=timeout,
        )
        r.raise_for_status()
        resp = r.json()
        # ComfyUI returns {"name": "...", "subfolder": "...", "type": "input"}
        name = resp.get("name") or filename
        subfolder = resp.get("subfolder", "")
        return f"{subfolder}/{name}" if subfolder else name
    except Exception as e:
        print(f"[comfyui_client] upload_image error: {e}", file=sys.stderr)
        sys.stderr.flush()
        return None


def fetch_image(base_url: str, filename: str, subfolder: str = "", folder_type: str = "output") -> bytes | None:
    """Download an image produced by SaveImage."""
    try:
        params = {"filename": filename, "subfolder": subfolder, "type": folder_type}
        r = httpx.get(
            f"{_normalize(base_url)}/view?{urlencode(params)}", timeout=30.0
        )
        r.raise_for_status()
        return r.content
    except Exception as e:
        print(f"[comfyui_client] fetch_image error: {e}", file=sys.stderr)
        sys.stderr.flush()
        return None


def new_client_id() -> str:
    return f"logos-{random.randint(10_000_000, 99_999_999)}"


def new_seed() -> int:
    return random.randint(0, 2**31 - 1)
