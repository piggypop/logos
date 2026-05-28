"""Obsidian Daily Note sync.

Reads chats from a given day and writes a summary into the user's Obsidian
Daily Note for the day after, under a configurable section header
(default: "## About Logos").

Design contract (see roadmap-v1.5-obsidian.md §1):
- P-O1: Idempotent. Running twice for the same target_date yields a
        byte-identical file (the section body is replaced in place).
- P-O2: Never destructive. We only touch one section of one file. If the
        file or section can't be located cleanly, we either create it
        fresh (file) or skip with a logged reason (config/vault).
- P-O3: Legacy header `## About Aya` is auto-renamed to the configured
        header on first encounter, but only if the new header is absent.
- P-O4: Days with zero chats are skipped — no empty section is written.
- P-O5: stdlib only. No new dependencies.
- P-O6: Never raises into the caller. Returns a result dict.

Public API:
    sync_date(target_date, dry_run=False) -> dict

Internal helpers are not part of the public contract.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from datetime import date, datetime, timedelta
from pathlib import Path

import chats as chats_store
import config as cfg

# Legacy section headers that predate this feature. When encountered we
# rename them to the configured header so the user's daily notes converge.
# Supports both plain and emoji-prefixed variants (e.g. "## 🤖 About Aya").
LEGACY_HEADERS = ["## About Aya", "## 🤖 About Aya"]

# Excerpt length for "excerpts" digest format. Greek characters are 2 bytes
# in UTF-8 but ~1 visual char; this bound is in characters, not bytes.
EXCERPT_CHARS = 240


# ── Config glue ────────────────────────────────────────────


def _read_config() -> dict:
    """Pull just the Obsidian-related keys with safe defaults."""
    c = cfg.load()
    return {
        "vault_path": (c.get("obsidian_vault_path") or "").strip(),
        "daily_note_path": (
            c.get("obsidian_daily_note_path") or "Daily Notes/{date}.md"
        ).strip(),
        "section_header": (
            c.get("obsidian_section_header") or "## About Logos"
        ).strip(),
        "digest_format": (c.get("obsidian_digest_format") or "excerpts").strip(),
    }


# ── Chat collection ────────────────────────────────────────


def _parse_chat_dt(s: str | None) -> datetime | None:
    """Best-effort ISO8601 parser. Returns None on any failure."""
    if not s:
        return None
    try:
        # `datetime.fromisoformat` handles `2026-05-23T14:22:01` and
        # `2026-05-23T14:22:01+03:00`. It does NOT handle a trailing Z.
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None


def _local_date_of(dt: datetime | None) -> date | None:
    """Return the local-timezone date of a datetime. None passes through."""
    if dt is None:
        return None
    # Naive datetimes are assumed local — same convention as chats.py.
    if dt.tzinfo is None:
        return dt.date()
    return dt.astimezone().date()


def _collect_chats_for_date(target_date: date) -> list[dict]:
    """Return full chat objects whose updated_at (fallback: created_at) is
    on target_date in local time. Sorted oldest-first for stable digests.

    Each item is the FULL chat dict (with messages). Caller decides how
    to format.
    """
    out: list[dict] = []
    for meta in chats_store.list_chats():
        chat_dt = _parse_chat_dt(meta.get("updated_at") or meta.get("created_at"))
        if _local_date_of(chat_dt) != target_date:
            continue
        full = chats_store.get(meta["id"])
        if full:
            out.append(full)
    # Sort by created_at (oldest first) so digest reads chronologically.
    out.sort(key=lambda c: c.get("created_at") or "")
    return out


# ── Formatting helpers ─────────────────────────────────────


def _first_user_message(chat: dict) -> str:
    """Return the user prompt that started this chat. '' if absent."""
    for m in chat.get("messages", []):
        if m.get("role") == "user":
            return (m.get("content") or "").strip()
    return ""


def _chat_time_label(chat: dict) -> str:
    """Render the chat's creation time as HH:MM in local zone."""
    dt = _parse_chat_dt(chat.get("created_at"))
    if dt is None:
        return "--:--"
    if dt.tzinfo is None:
        return dt.strftime("%H:%M")
    return dt.astimezone().strftime("%H:%M")


def _truncate(text: str, limit: int) -> str:
    text = text.replace("\r", "").strip()
    # Collapse hard newlines so the excerpt fits one quote line cleanly.
    text = " ".join(text.split())
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def _format_digest_titles(chats: list[dict]) -> str:
    """Option A — titles only, one bullet per chat."""
    lines: list[str] = []
    for c in chats:
        title = (c.get("title") or "Untitled").strip()
        lines.append(f"- {_chat_time_label(c)} · {title}")
    return "\n".join(lines)


def _format_digest_excerpts(chats: list[dict]) -> str:
    """Option B — titles + truncated first user message, blank line between."""
    blocks: list[str] = []
    for c in chats:
        title = (c.get("title") or "Untitled").strip()
        prompt = _truncate(_first_user_message(c), EXCERPT_CHARS)
        if prompt:
            blocks.append(
                f"- **{_chat_time_label(c)} · {title}**\n  > {prompt}"
            )
        else:
            blocks.append(f"- **{_chat_time_label(c)} · {title}**")
    return "\n\n".join(blocks)


def _format_digest(chats: list[dict], digest_format: str) -> str:
    if digest_format == "titles":
        return _format_digest_titles(chats)
    if digest_format == "summaries":
        # Reserved for v1.5.1 (LLM summary per chat). Fall back to excerpts.
        return _format_digest_excerpts(chats)
    # Default
    return _format_digest_excerpts(chats)


# ── Daily-note path resolution ─────────────────────────────


def _resolve_daily_note_path(
    vault_path: Path, template: str, the_date: date
) -> Path:
    """Substitute {date} into the template and join with the vault root.

    Future placeholders ({year}, {month}, ...) are intentionally not
    supported in v1.5.0 — the roadmap notes this.
    """
    relative = template.format(date=the_date.isoformat())
    # Forbid escaping the vault root via "../" tricks in the template.
    full = (vault_path / relative).resolve()
    try:
        full.relative_to(vault_path.resolve())
    except ValueError as e:
        raise ValueError(
            f"daily_note_path template escapes vault root: {template!r}"
        ) from e
    return full


# ── Section update — the core of P-O1 / P-O3 ───────────────


def _update_section(file_path: Path, header: str, body: str) -> dict:
    """Insert or replace one `##` section in a markdown file.

    Args:
        file_path: target daily note. Created if missing.
        header: full header line, e.g. "## About Logos". MUST start with #.
        body: section body, no trailing newline.

    Returns:
        dict with keys:
          - created: bool (the file did not exist before)
          - replaced: bool (an existing section's body was replaced)
          - appended: bool (a fresh section was added to an existing file)
          - renamed_legacy_header: bool
          - bytes_written: int
    """
    if not header.startswith("#"):
        raise ValueError("header must start with #")
    file_path.parent.mkdir(parents=True, exist_ok=True)

    new_block = f"{header}\n\n{body}\n"

    # Case 1: file does not exist — create with just our section.
    if not file_path.exists():
        _atomic_write(file_path, new_block)
        return {
            "created": True,
            "replaced": False,
            "appended": False,
            "renamed_legacy_header": False,
            "bytes_written": len(new_block.encode("utf-8")),
        }

    text = file_path.read_text(encoding="utf-8")
    lines = text.splitlines(keepends=False)

    # Legacy rename: if our header is absent AND any legacy header is present,
    # rewrite the line in place. Only the heading text is changed; we then
    # treat that as the existing section to update.
    renamed = False
    if header not in text:
        for legacy in LEGACY_HEADERS:
            if legacy in text:
                lines = [LEGACY_HEADER_SUB(line, header, legacy) for line in lines]
                renamed = True
                break

    # Locate the section header line.
    header_idx = -1
    for i, line in enumerate(lines):
        if line.strip() == header:
            header_idx = i
            break

    if header_idx == -1:
        # Section not present — append at EOF with a leading blank.
        sep = "" if text.endswith("\n") else "\n"
        appended_text = text + sep + "\n" + new_block
        _atomic_write(file_path, appended_text)
        return {
            "created": False,
            "replaced": False,
            "appended": True,
            "renamed_legacy_header": renamed,
            "bytes_written": len(appended_text.encode("utf-8")),
        }

    # Section found — find the end (next `##` header or EOF).
    end_idx = len(lines)
    for j in range(header_idx + 1, len(lines)):
        s = lines[j].lstrip()
        if s.startswith("##") and not s.startswith("###"):
            end_idx = j
            break
        # Also stop at a top-level `#` heading (shouldn't normally exist
        # mid-file, but be defensive).
        if s.startswith("# ") and not s.startswith("## "):
            end_idx = j
            break

    # Replace [header_idx, end_idx) with the new section. Preserve a
    # trailing blank line before the next section if there was one.
    new_section_lines = [header, "", *body.splitlines()]
    # Ensure a blank line between our section and what follows.
    if end_idx < len(lines):
        new_section_lines.append("")
    out_lines = lines[:header_idx] + new_section_lines + lines[end_idx:]
    out_text = "\n".join(out_lines)
    if text.endswith("\n") and not out_text.endswith("\n"):
        out_text += "\n"
    _atomic_write(file_path, out_text)
    return {
        "created": False,
        "replaced": True,
        "appended": False,
        "renamed_legacy_header": renamed,
        "bytes_written": len(out_text.encode("utf-8")),
    }


def LEGACY_HEADER_SUB(line: str, new_header: str, legacy_header: str = None) -> str:
    """Substitute a legacy header on one line (only).
    Returns the line unchanged if it isn't the legacy header.
    If legacy_header is None, tries all LEGACY_HEADERS."""
    stripped = line.strip()
    if legacy_header and stripped == legacy_header:
        # Preserve any leading whitespace (rare in markdown, but be safe).
        prefix_len = len(line) - len(line.lstrip())
        return line[:prefix_len] + new_header
    if legacy_header is None:
        for lh in LEGACY_HEADERS:
            if stripped == lh:
                prefix_len = len(line) - len(line.lstrip())
                return line[:prefix_len] + new_header
    return line


def _atomic_write(file_path: Path, text: str) -> None:
    """Write text to file_path atomically: tmpfile + os.replace.

    Preserves the permissions of the existing file (so Obsidian's 0664
    isn't clobbered by mkstemp's default 0600). For new files, uses
    0644 — world-readable, matching the convention of daily-note plugins.
    """
    file_path.parent.mkdir(parents=True, exist_ok=True)

    # Determine target permissions: keep existing file's mode, or 0644 for new.
    if file_path.exists():
        target_mode = file_path.stat().st_mode & 0o7777
    else:
        target_mode = 0o644

    tmp_fd, tmp_path = tempfile.mkstemp(
        dir=str(file_path.parent), prefix=".obsidian_sync.", suffix=".tmp"
    )
    try:
        # Set permissions on the temp file BEFORE writing, so os.replace()
        # moves a file with the correct mode into place.
        os.chmod(tmp_fd, target_mode)
        with os.fdopen(tmp_fd, "w", encoding="utf-8") as f:
            f.write(text)
        os.replace(tmp_path, file_path)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


# ── Public API ─────────────────────────────────────────────


def sync_date(target_date: date, *, dry_run: bool = False) -> dict:
    """Sync chats from target_date into the daily note for target_date + 1.

    See module docstring for the full contract.
    """
    result: dict = {
        "ok": False,
        "target_date": target_date.isoformat(),
        "daily_note_date": (target_date + timedelta(days=1)).isoformat(),
        "daily_note_path": None,
        "chats_count": 0,
        "bytes_written": 0,
        "renamed_legacy_header": False,
        "skipped_reason": None,
        "error": None,
        "dry_run": dry_run,
        "preview": None,
    }

    try:
        conf = _read_config()
        if not conf["vault_path"]:
            result["skipped_reason"] = "config_incomplete"
            result["ok"] = True
            return result

        vault = Path(conf["vault_path"]).expanduser()
        if not vault.exists() or not vault.is_dir():
            result["skipped_reason"] = "vault_missing"
            result["ok"] = True  # Not an error — feature simply not active.
            return result

        chats = _collect_chats_for_date(target_date)
        result["chats_count"] = len(chats)

        if not chats:
            result["skipped_reason"] = "empty"
            result["ok"] = True
            return result

        digest_body = _format_digest(chats, conf["digest_format"])

        daily_note_date = target_date + timedelta(days=1)
        daily_note_path = _resolve_daily_note_path(
            vault, conf["daily_note_path"], daily_note_date
        )
        result["daily_note_path"] = str(daily_note_path)

        if dry_run:
            result["ok"] = True
            result["preview"] = f"{conf['section_header']}\n\n{digest_body}\n"
            return result

        upd = _update_section(daily_note_path, conf["section_header"], digest_body)
        result["bytes_written"] = upd["bytes_written"]
        result["renamed_legacy_header"] = upd["renamed_legacy_header"]
        result["ok"] = True
        return result

    except Exception as e:
        # Never raise into the caller.
        result["error"] = f"{type(e).__name__}: {e}"
        print(f"[obsidian_sync] {result['error']}", file=sys.stderr)
        sys.stderr.flush()
        return result


def sync_yesterday(*, dry_run: bool = False) -> dict:
    """Convenience wrapper: target_date = (today - 1 day) in local zone."""
    today = datetime.now().astimezone().date()
    return sync_date(today - timedelta(days=1), dry_run=dry_run)


# ── Smoke test ─────────────────────────────────────────────


def _smoke_test() -> None:
    """Self-contained smoke test for the section-update logic.

    Run with: python3 -m obsidian_sync   (from the backend dir)
    """
    import tempfile as _tf

    with _tf.TemporaryDirectory() as td:
        td_path = Path(td)
        header = "## About Logos"
        body_v1 = "- 10:00 · One\n- 11:00 · Two"
        body_v2 = "- 10:00 · Updated"

        # 1. Create from scratch
        f = td_path / "Daily Notes" / "2026-05-23.md"
        r = _update_section(f, header, body_v1)
        assert r["created"] and not r["replaced"]
        assert header in f.read_text(encoding="utf-8")

        # 2. Idempotent replace
        r = _update_section(f, header, body_v1)
        assert r["replaced"] and not r["created"]
        text = f.read_text(encoding="utf-8")
        # Run again with the same body; file should be byte-identical
        _update_section(f, header, body_v1)
        assert f.read_text(encoding="utf-8") == text, "not idempotent"

        # 3. Replace existing body
        r = _update_section(f, header, body_v2)
        assert "Updated" in f.read_text(encoding="utf-8")
        assert "10:00 · One" not in f.read_text(encoding="utf-8")

        # 4. Append section to a note that exists but lacks our header
        f2 = td_path / "2026-05-24.md"
        f2.write_text("# Frontmatter-like\n\nsome content\n\n## Other\n\nstuff\n")
        r = _update_section(f2, header, body_v1)
        assert r["appended"]
        out = f2.read_text(encoding="utf-8")
        assert "## Other" in out and header in out
        assert "stuff" in out  # didn't clobber the other section

        # 5. Legacy rename
        f3 = td_path / "2026-05-25.md"
        f3.write_text("# Day\n\n## About Aya\n\nold body\n\n## Tasks\n\n- foo\n")
        r = _update_section(f3, header, body_v1)
        assert r["renamed_legacy_header"]
        out = f3.read_text(encoding="utf-8")
        assert "## About Aya" not in out
        assert header in out
        assert "## Tasks" in out and "- foo" in out

        # 6. Coexisting both headers — never touch the legacy one
        f4 = td_path / "2026-05-26.md"
        f4.write_text(
            "## About Aya\n\nlegacy\n\n## About Logos\n\nold logos body\n"
        )
        r = _update_section(f4, header, body_v1)
        assert not r["renamed_legacy_header"], "must not rename when both exist"
        out = f4.read_text(encoding="utf-8")
        assert "## About Aya" in out and "legacy" in out
        assert "10:00 · One" in out  # new body written under About Logos

        # 7. Legacy rename with emoji prefix (## 🤖 About Aya)
        f5 = td_path / "2026-05-27.md"
        f5.write_text(
            "# Day\n\n## 🤖 About Aya\n\nold body\n\n## Tasks\n\n- foo\n"
        )
        r = _update_section(f5, header, body_v1)
        assert r["renamed_legacy_header"]
        out = f5.read_text(encoding="utf-8")
        assert "## 🤖 About Aya" not in out
        assert "## About Aya" not in out
        assert header in out
        assert "## Tasks" in out and "- foo" in out

        # 8. Coexisting emoji-legacy and new header — don't rename
        f6 = td_path / "2026-05-28.md"
        f6.write_text(
            "## 🤖 About Aya\n\nlegacy\n\n## About Logos\n\nold logos body\n"
        )
        r = _update_section(f6, header, body_v1)
        assert not r["renamed_legacy_header"]
        out = f6.read_text(encoding="utf-8")
        assert "## 🤖 About Aya" in out and "legacy" in out
        assert "10:00 · One" in out

        # 9. Template escape protection
        try:
            _resolve_daily_note_path(td_path, "../escape.md", date.today())
        except ValueError:
            pass
        else:
            raise AssertionError("expected ValueError on escaping template")

    print("obsidian_sync smoke test: OK")


if __name__ == "__main__":
    _smoke_test()
