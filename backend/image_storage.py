"""Disk storage for generated images.

Layout:
  ~/.local/share/logos/images/<chat_id_or_none>/<timestamp>_<seed>.<ext>

Returns absolute paths that the frontend can fetch via /api/images/<path>.
"""
import shutil
from datetime import datetime
from pathlib import Path

IMAGES_ROOT = Path.home() / ".local" / "share" / "logos" / "images"


def _safe_chat_id(chat_id: str | None) -> str:
    if not chat_id:
        return "scratch"
    return "".join(c for c in chat_id if c.isalnum() or c in "-_")[:64] or "scratch"


def save(data: bytes, chat_id: str | None, seed: int, ext: str = "png") -> Path:
    """Write image bytes, return the saved path."""
    folder = IMAGES_ROOT / _safe_chat_id(chat_id)
    folder.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = folder / f"{ts}_{seed}.{ext}"
    path.write_bytes(data)
    return path


def is_safe_path(p: str | Path) -> bool:
    """Reject anything outside IMAGES_ROOT (path traversal guard for HTTP serving)."""
    try:
        Path(p).resolve().relative_to(IMAGES_ROOT.resolve())
        return True
    except Exception:
        return False


def delete_for_chat(chat_id: str | None) -> int:
    """Remove the per-chat images folder. Returns number of files deleted (0 if none)."""
    if not chat_id:
        return 0
    folder = IMAGES_ROOT / _safe_chat_id(chat_id)
    if not folder.exists() or not is_safe_path(folder):
        return 0
    n = sum(1 for _ in folder.glob("*"))
    shutil.rmtree(folder, ignore_errors=True)
    return n
