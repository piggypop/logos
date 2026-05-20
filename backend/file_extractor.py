import base64
import mimetypes
import sys
from pathlib import Path

MAX_TEXT_CHARS = 50_000
MAX_IMAGE_BYTES = 8 * 1024 * 1024  # 8 MB
MAX_AV_BYTES = 50 * 1024 * 1024  # 50 MB (audio/video, future)

PDF_EXT = {".pdf"}
DOCX_EXT = {".docx"}
IMAGE_EXT = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp"}
AUDIO_EXT = {".mp3", ".wav", ".ogg", ".m4a", ".flac"}
VIDEO_EXT = {".mp4", ".mov", ".webm", ".mkv", ".avi"}

# Per-category capability requirement (from Ollama model capabilities).
CATEGORY_CAPABILITY = {
    "text": None,        # always allowed
    "image": "vision",
    "audio": "audio",    # not yet widely supported
    "video": "vision",   # frames via vision models
}


def categorize(file_path: str) -> str:
    ext = Path(file_path).suffix.lower()
    if ext in IMAGE_EXT:
        return "image"
    if ext in AUDIO_EXT:
        return "audio"
    if ext in VIDEO_EXT:
        return "video"
    return "text"  # pdf, docx, and anything else routed via text path


def is_supported(file_path: str, capabilities: list[str]) -> tuple[bool, str]:
    """Returns (allowed, reason). reason is empty if allowed."""
    cat = categorize(file_path)
    needed = CATEGORY_CAPABILITY.get(cat)
    if needed is None:
        return True, ""
    if needed in (capabilities or []):
        return True, ""
    return False, f"{cat} files require a model with '{needed}' capability"


def extract(file_path: str, capabilities: list[str] | None = None) -> dict | None:
    """
    Extract attachment info from a file path. Returns:
      {filename, type, category, size, content?, data_base64?, truncated?, mime?}
    or None on failure.
    """
    p = Path(file_path)
    if not p.exists() or not p.is_file():
        return None

    ext = p.suffix.lower()
    cat = categorize(file_path)
    size = p.stat().st_size
    mime = mimetypes.guess_type(str(p))[0] or "application/octet-stream"
    base = {
        "filename": p.name,
        "type": ext.lstrip(".") or "text",
        "category": cat,
        "size": size,
        "mime": mime,
    }

    try:
        if cat == "image":
            if size > MAX_IMAGE_BYTES:
                return {**base, "error": f"Image too large (> {MAX_IMAGE_BYTES // 1024 // 1024} MB)"}
            data = p.read_bytes()
            base["data_base64"] = base64.b64encode(data).decode("ascii")
            return base

        if cat in ("audio", "video"):
            if size > MAX_AV_BYTES:
                return {**base, "error": f"Media too large (> {MAX_AV_BYTES // 1024 // 1024} MB)"}
            data = p.read_bytes()
            base["data_base64"] = base64.b64encode(data).decode("ascii")
            return base

        # text path
        if ext in PDF_EXT:
            content = _extract_pdf(p)
        elif ext in DOCX_EXT:
            content = _extract_docx(p)
        else:
            content = _extract_text(p)

        if not content or not content.strip():
            return {**base, "error": "No extractable text content"}

        truncated = False
        if len(content) > MAX_TEXT_CHARS:
            content = content[:MAX_TEXT_CHARS] + "\n\n[…truncated…]"
            truncated = True

        base["content"] = content
        base["truncated"] = truncated
        return base

    except Exception as e:
        print(f"[file_extractor] {file_path}: {e}", file=sys.stderr)
        sys.stderr.flush()
        return {**base, "error": str(e)}


def _extract_text(p: Path) -> str:
    try:
        return p.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return p.read_text(encoding="latin-1", errors="replace")


def _extract_pdf(p: Path) -> str:
    from pypdf import PdfReader

    reader = PdfReader(str(p))
    parts = []
    for page in reader.pages:
        text = page.extract_text() or ""
        if text.strip():
            parts.append(text)
    return "\n\n".join(parts)


def _extract_docx(p: Path) -> str:
    import docx

    doc = docx.Document(str(p))
    return "\n".join(par.text for par in doc.paragraphs if par.text.strip())


def build_ollama_messages(messages: list[dict]) -> list[dict]:
    """
    Transform stored messages (with attachments field) into Ollama format:
    - Text/PDF/DOCX attachments → prepended to message content as inline blocks
    - Image attachments → added to 'images' field as base64 (Ollama format)
    - Audio/video → currently passed through 'images' too if no other path; safer
      to drop and add a placeholder note (most models won't accept).
    """
    out = []
    for m in messages:
        role = m.get("role")
        content = m.get("content", "") or ""
        attachments = m.get("attachments") or []
        text_blocks = []
        images: list[str] = []

        for a in attachments:
            if a.get("error"):
                text_blocks.append(f"[Attachment '{a.get('filename')}' could not be processed: {a['error']}]")
                continue
            cat = a.get("category", "text")
            if cat == "image" and a.get("data_base64"):
                images.append(a["data_base64"])
            elif cat in ("audio", "video"):
                # Models that accept these need different APIs; leave a note for the LLM.
                text_blocks.append(
                    f"[User attached {cat} file '{a.get('filename')}' "
                    f"({a.get('size', 0) // 1024} KB) — current model cannot process it.]"
                )
            else:
                fname = a.get("filename", "file")
                body = a.get("content", "")
                trunc = " [truncated]" if a.get("truncated") else ""
                text_blocks.append(f"[Attached file: {fname}{trunc}]\n{body}")

        if text_blocks:
            content = "\n\n".join(text_blocks) + ("\n\n" + content if content else "")

        msg = {"role": role, "content": content}
        if images:
            msg["images"] = images
        out.append(msg)
    return out
