"""ComfyUI workflow templates with placeholder substitution.

We bundle one safe default (SDXL family) and accept user-pasted custom workflows
for everything else (Flux, SD3, custom-node graphs).

Supported placeholders, replaced before submitting to ComfyUI:
  {{PROMPT}}      — positive prompt
  {{NEGATIVE}}    — negative prompt
  {{CHECKPOINT}}  — checkpoint filename (str, no path)
  {{SEED}}        — int
  {{STEPS}}       — int
  {{CFG}}         — float
  {{SAMPLER}}     — str
  {{SCHEDULER}}   — str
  {{WIDTH}}       — int
  {{HEIGHT}}      — int

Strings are substituted as JSON-correct values: numeric placeholders inside
numeric fields become numbers, string placeholders inside string fields become
strings. We do this by walking the dict, not by string templating, so JSON
structure stays intact.
"""
import copy
import json


# Standard txt2img workflow for SDXL / SD 1.5 / SD 2 / Pony / Illustrious /
# any checkpoint loaded via CheckpointLoaderSimple.
SDXL_DEFAULT: dict = {
    "3": {
        "inputs": {
            "seed": "{{SEED}}",
            "steps": "{{STEPS}}",
            "cfg": "{{CFG}}",
            "sampler_name": "{{SAMPLER}}",
            "scheduler": "{{SCHEDULER}}",
            "denoise": 1.0,
            "model": ["4", 0],
            "positive": ["6", 0],
            "negative": ["7", 0],
            "latent_image": ["5", 0],
        },
        "class_type": "KSampler",
    },
    "4": {
        "inputs": {"ckpt_name": "{{CHECKPOINT}}"},
        "class_type": "CheckpointLoaderSimple",
    },
    "5": {
        "inputs": {
            "width": "{{WIDTH}}",
            "height": "{{HEIGHT}}",
            "batch_size": 1,
        },
        "class_type": "EmptyLatentImage",
    },
    "6": {
        "inputs": {"text": "{{PROMPT}}", "clip": ["4", 1]},
        "class_type": "CLIPTextEncode",
    },
    "7": {
        "inputs": {"text": "{{NEGATIVE}}", "clip": ["4", 1]},
        "class_type": "CLIPTextEncode",
    },
    "8": {
        "inputs": {"samples": ["3", 0], "vae": ["4", 2]},
        "class_type": "VAEDecode",
    },
    "9": {
        "inputs": {"filename_prefix": "logos", "images": ["8", 0]},
        "class_type": "SaveImage",
    },
}


PRESETS: dict[str, dict] = {
    "sdxl-default": SDXL_DEFAULT,
}


# Per-placeholder type so we can convert "{{STEPS}}" → int(steps), not "30" str.
_PLACEHOLDER_TYPES = {
    "{{PROMPT}}": str,
    "{{NEGATIVE}}": str,
    "{{CHECKPOINT}}": str,
    "{{SAMPLER}}": str,
    "{{SCHEDULER}}": str,
    "{{SEED}}": int,
    "{{STEPS}}": int,
    "{{WIDTH}}": int,
    "{{HEIGHT}}": int,
    "{{CFG}}": float,
}


def _substitute(node: object, params: dict):
    """Recursively replace placeholders in any JSON value."""
    if isinstance(node, dict):
        return {k: _substitute(v, params) for k, v in node.items()}
    if isinstance(node, list):
        return [_substitute(x, params) for x in node]
    if isinstance(node, str):
        # Exact placeholder match → keep type
        if node in _PLACEHOLDER_TYPES:
            key = node.strip("{}").lower()
            val = params.get(key)
            if val is None:
                return node  # leave for ComfyUI to error helpfully
            try:
                return _PLACEHOLDER_TYPES[node](val)
            except Exception:
                return val
        # Embedded placeholders → simple str.replace (keeps as string)
        out = node
        for ph in _PLACEHOLDER_TYPES:
            if ph in out:
                key = ph.strip("{}").lower()
                out = out.replace(ph, str(params.get(key, "")))
        return out
    return node


def render(template: dict, params: dict) -> dict:
    """Return a deep-copied workflow with all placeholders substituted."""
    return _substitute(copy.deepcopy(template), params)


def from_custom_json(custom_json: str) -> dict | None:
    """Parse a user-pasted workflow JSON. Accepts both ComfyUI 'API format' and
    the raw API-style dict. Returns None on parse error."""
    try:
        data = json.loads(custom_json)
    except Exception:
        return None
    # Some exports wrap in {"prompt": {...}}; some are the raw dict.
    if isinstance(data, dict) and "prompt" in data and isinstance(data["prompt"], dict):
        return data["prompt"]
    return data if isinstance(data, dict) else None


def get_template(name: str, custom_json: str = "") -> dict | None:
    if name == "custom":
        return from_custom_json(custom_json)
    return PRESETS.get(name)


def find_save_image_node(workflow: dict) -> str | None:
    """Return node id of the SaveImage (or compatible) node, for output retrieval."""
    for nid, node in workflow.items():
        ct = node.get("class_type", "")
        if ct in ("SaveImage", "SaveImageWebsocket", "PreviewImage"):
            return nid
    return None
