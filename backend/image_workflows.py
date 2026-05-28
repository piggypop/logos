"""ComfyUI workflow templates with placeholder substitution.

We bundle safe defaults for SDXL, Flux, and img2img families, and accept
user-pasted custom workflows for everything else.

Supported placeholders, replaced before submitting to ComfyUI:
  {{PROMPT}}        — positive prompt (str)
  {{NEGATIVE}}      — negative prompt (str)
  {{CHECKPOINT}}    — checkpoint filename (str, no path)
  {{SEED}}          — int
  {{STEPS}}         — int
  {{CFG}}           — float
  {{SAMPLER}}       — str
  {{SCHEDULER}}     — str
  {{WIDTH}}         — int
  {{HEIGHT}}        — int
  {{GUIDANCE}}      — float  (Flux guidance scale, replaces CFG for Flux)
  {{DENOISE}}       — float  (img2img denoise strength, 0.0–1.0)
  {{INPUT_IMAGE}}   — str    (uploaded image filename for img2img/LoadImage nodes)

Strings are substituted as JSON-correct values: numeric placeholders inside
numeric fields become numbers, string placeholders inside string fields become
strings. We do this by walking the dict, not by string templating, so JSON
structure stays intact.

LoRA injection (inject_lora):
  Works generically on any CheckpointLoaderSimple-based workflow. Inserts a
  LoraLoader node between the checkpoint and downstream consumers.
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
    "10": {
        "inputs": {"images": ["8", 0]},
        "class_type": "PreviewImage",
    },
}


# ── Flux txt2img ────────────────────────────────────────────────────────────
# Works with Flux.1-dev and Flux.1-schnell loaded via CheckpointLoaderSimple.
# Key differences from SDXL:
#   • CLIPTextEncodeFlux takes clip_l + t5xxl separately + guidance float
#   • CFG must be 1.0 (guidance is baked in via CLIPTextEncodeFlux)
#   • Recommended: euler sampler + simple scheduler; schnell: 4 steps, dev: 20+
FLUX_DEFAULT: dict = {
    "1": {
        "inputs": {"ckpt_name": "{{CHECKPOINT}}"},
        "class_type": "CheckpointLoaderSimple",
    },
    "2": {
        "inputs": {
            "clip_l": "{{PROMPT}}",
            "t5xxl": "{{PROMPT}}",
            "guidance": "{{GUIDANCE}}",
            "clip": ["1", 1],
        },
        "class_type": "CLIPTextEncodeFlux",
    },
    "3": {
        "inputs": {
            "clip_l": "",
            "t5xxl": "",
            "guidance": "{{GUIDANCE}}",
            "clip": ["1", 1],
        },
        "class_type": "CLIPTextEncodeFlux",
    },
    "4": {
        "inputs": {
            "width": "{{WIDTH}}",
            "height": "{{HEIGHT}}",
            "batch_size": 1,
        },
        "class_type": "EmptyLatentImage",
    },
    "5": {
        "inputs": {
            "seed": "{{SEED}}",
            "steps": "{{STEPS}}",
            "cfg": 1.0,
            "sampler_name": "{{SAMPLER}}",
            "scheduler": "{{SCHEDULER}}",
            "denoise": 1.0,
            "model": ["1", 0],
            "positive": ["2", 0],
            "negative": ["3", 0],
            "latent_image": ["4", 0],
        },
        "class_type": "KSampler",
    },
    "6": {
        "inputs": {"samples": ["5", 0], "vae": ["1", 2]},
        "class_type": "VAEDecode",
    },
    "7": {
        "inputs": {"filename_prefix": "logos", "images": ["6", 0]},
        "class_type": "SaveImage",
    },
    "8": {
        "inputs": {"images": ["6", 0]},
        "class_type": "PreviewImage",
    },
}


# ── img2img (SD / SDXL / Pony / Illustrious) ────────────────────────────────
# The input image must be uploaded first via comfyui_client.upload_image().
# {{INPUT_IMAGE}} must be set to the filename returned by upload_image().
# {{DENOISE}} controls how much the input image changes (0.0 = no change, 1.0 = full noise).
IMG2IMG_DEFAULT: dict = {
    "10": {
        "inputs": {"ckpt_name": "{{CHECKPOINT}}"},
        "class_type": "CheckpointLoaderSimple",
    },
    "11": {
        "inputs": {"image": "{{INPUT_IMAGE}}", "upload": "image"},
        "class_type": "LoadImage",
    },
    "12": {
        "inputs": {"pixels": ["11", 0], "vae": ["10", 2]},
        "class_type": "VAEEncode",
    },
    "13": {
        "inputs": {"text": "{{PROMPT}}", "clip": ["10", 1]},
        "class_type": "CLIPTextEncode",
    },
    "14": {
        "inputs": {"text": "{{NEGATIVE}}", "clip": ["10", 1]},
        "class_type": "CLIPTextEncode",
    },
    "15": {
        "inputs": {
            "seed": "{{SEED}}",
            "steps": "{{STEPS}}",
            "cfg": "{{CFG}}",
            "sampler_name": "{{SAMPLER}}",
            "scheduler": "{{SCHEDULER}}",
            "denoise": "{{DENOISE}}",
            "model": ["10", 0],
            "positive": ["13", 0],
            "negative": ["14", 0],
            "latent_image": ["12", 0],
        },
        "class_type": "KSampler",
    },
    "16": {
        "inputs": {"samples": ["15", 0], "vae": ["10", 2]},
        "class_type": "VAEDecode",
    },
    "17": {
        "inputs": {"filename_prefix": "logos-img2img", "images": ["16", 0]},
        "class_type": "SaveImage",
    },
    "18": {
        "inputs": {"images": ["16", 0]},
        "class_type": "PreviewImage",
    },
}


PRESETS: dict[str, dict] = {
    "sdxl-default": SDXL_DEFAULT,
    "flux-default": FLUX_DEFAULT,
    "img2img-default": IMG2IMG_DEFAULT,
}


# Per-placeholder type so we can convert "{{STEPS}}" → int(steps), not "30" str.
_PLACEHOLDER_TYPES = {
    "{{PROMPT}}": str,
    "{{NEGATIVE}}": str,
    "{{CHECKPOINT}}": str,
    "{{SAMPLER}}": str,
    "{{SCHEDULER}}": str,
    "{{INPUT_IMAGE}}": str,
    "{{SEED}}": int,
    "{{STEPS}}": int,
    "{{WIDTH}}": int,
    "{{HEIGHT}}": int,
    "{{CFG}}": float,
    "{{GUIDANCE}}": float,
    "{{DENOISE}}": float,
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
    """Return node id of the SaveImage (or compatible) node, for output retrieval.

    Prefers SaveImage (writes to disk, retrievable via /view) over PreviewImage
    (WS-only, not retrievable). Falls back to any WS-compatible node if needed.
    """
    fallback: str | None = None
    for nid, node in workflow.items():
        ct = node.get("class_type", "")
        if ct == "SaveImage":
            return nid
        if ct in ("SaveImageWebsocket", "PreviewImage"):
            fallback = nid
    return fallback


def inject_lora(
    workflow: dict,
    lora_name: str,
    strength_model: float = 0.8,
    strength_clip: float = 0.8,
) -> dict:
    """Insert a LoraLoader node into any CheckpointLoaderSimple-based workflow.

    Finds the CheckpointLoaderSimple node, wraps it with a LoraLoader, and
    reroutes all downstream model (slot 0) and clip (slot 1) references so
    they pass through the LoRA before reaching KSampler / CLIPTextEncode.

    Returns a deep copy of the workflow with the LoRA injected.
    Returns the original (unmodified copy) if no CheckpointLoaderSimple is found.
    """
    wf = copy.deepcopy(workflow)

    # Find CheckpointLoaderSimple node id
    ckpt_id: str | None = None
    for nid, node in wf.items():
        if node.get("class_type") == "CheckpointLoaderSimple":
            ckpt_id = nid
            break

    if ckpt_id is None:
        return wf  # can't inject without a checkpoint loader

    # Pick a node id that doesn't collide
    lora_id = "lora_inject"
    while lora_id in wf:
        lora_id += "_"

    wf[lora_id] = {
        "inputs": {
            "lora_name": lora_name,
            "strength_model": strength_model,
            "strength_clip": strength_clip,
            "model": [ckpt_id, 0],
            "clip": [ckpt_id, 1],
        },
        "class_type": "LoraLoader",
    }

    # Reroute: any node (other than the LoraLoader itself) that references
    # [ckpt_id, 0] (model) or [ckpt_id, 1] (clip) → point to lora_id instead.
    def _reroute(node: object) -> object:
        if isinstance(node, dict):
            return {k: _reroute(v) for k, v in node.items()}
        if isinstance(node, list) and len(node) == 2:
            if node[0] == ckpt_id and node[1] in (0, 1):
                return [lora_id, node[1]]
        if isinstance(node, list):
            return [_reroute(x) for x in node]
        return node

    for nid, node in wf.items():
        if nid == lora_id:
            continue
        wf[nid] = _reroute(node)

    return wf


def list_preset_names() -> list[str]:
    """Return the names of built-in presets (for the frontend dropdown)."""
    return list(PRESETS.keys())
