"""SQLite notes store with FTS5 search.

Provides CRUD operations for capturing assistant Q+A pairs as permanent notes.

Database location: ~/.local/share/logos/notes.db
Schema: notes table + notes_fts virtual table with unicode61+remove_diacritics 2 tokenizer.
"""

import json
import re
import sqlite3
import textwrap
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path

NOTES_DB = Path.home() / ".local" / "share" / "logos" / "notes.db"
_lock = threading.Lock()


def _migrate_legacy():
    """No legacy migration needed for notes."""
    pass


def _ensure_dir():
    _migrate_legacy()
    NOTES_DB.parent.mkdir(parents=True, exist_ok=True)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(sep=" ", timespec="seconds")


_VALID_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,80}$")


def _is_valid_id(note_id: str) -> bool:
    return bool(note_id) and bool(_VALID_ID_RE.fullmatch(note_id))


def _get_conn() -> sqlite3.Connection:
    return sqlite3.connect(NOTES_DB, check_same_thread=False)


def _create_schema():
    """Create notes table and FTS5 virtual table with triggers."""
    conn = _get_conn()
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS notes (
                id            TEXT PRIMARY KEY,
                created_at    TEXT NOT NULL,
                chat_id       TEXT,
                chat_title    TEXT,
                user_message  TEXT NOT NULL,
                assistant_message TEXT NOT NULL,
                sources_json  TEXT DEFAULT '[]',
                model         TEXT
            )
        """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS notes_created_at_idx ON notes(created_at DESC)"
        )

        # FTS5 with unicode61 tokenizer and remove_diacritics 2
        # This lets users search "λογος" and match "λόγος", "Λόγος", "λογοσ", etc.
        conn.execute(
            """
            CREATE VIRTUAL TABLE IF NOT EXISTS notes_fts USING fts5(
                user_message, assistant_message, chat_title,
                content='notes', content_rowid='rowid',
                tokenize="unicode61 remove_diacritics 2"
            )
        """
        )

        conn.execute(
            """
            CREATE TRIGGER IF NOT EXISTS notes_ai AFTER INSERT ON notes BEGIN
                INSERT INTO notes_fts(rowid, user_message, assistant_message, chat_title)
                    VALUES (new.rowid, new.user_message, new.assistant_message, new.chat_title);
            END
        """
        )
        conn.execute(
            """
            CREATE TRIGGER IF NOT EXISTS notes_ad AFTER DELETE ON notes BEGIN
                INSERT INTO notes_fts(notes_fts, rowid, user_message, assistant_message, chat_title)
                    VALUES('delete', old.rowid, old.user_message, old.assistant_message, old.chat_title);
            END
        """
        )
        conn.commit()
    finally:
        conn.close()


def _ensure_schema():
    """Create database and schema if missing."""
    _ensure_dir()
    if not NOTES_DB.exists():
        _create_schema()


def _row_to_dict(row: sqlite3.Row, add_snippet: bool = False) -> dict:
    """Convert a sqlite3.Row to a dict with optional snippet field."""
    d = dict(row)
    # Sources stored as JSON string
    d["sources"] = (
        [] if d.get("sources_json") in (None, "[]") else json.loads(d["sources_json"])
    )
    # Remove raw JSON string from output
    if "sources_json" in d:
        del d["sources_json"]

    if add_snippet:
        # textwrap.shorten respects word boundaries and Unicode characters
        body = d.get("assistant_message", "")
        if len(body) <= 200:
            d["snippet"] = body
        else:
            d["snippet"] = textwrap.shorten(body, width=200, placeholder="…")

    return d


def create(
    *,
    user_message: str,
    assistant_message: str,
    sources: list[dict] | None = None,
    chat_id: str | None = None,
    chat_title: str | None = None,
    model: str | None = None,
) -> dict:
    """Create a new note and return its record.

    Args:
        user_message: The prompt text
        assistant_message: The assistant's markdown reply
        sources: List of {title, url, category} dicts (optional)
        chat_id: Original chat UUID (optional, may be None if chat deleted)
        chat_title: Snapshot of chat title at capture time (optional)
        model: Model name at capture time (optional)

    Returns:
        dict with all fields including 'id' (uuid4 hex, no dashes)
    """
    _ensure_schema()
    note_id = str(uuid.uuid4()).replace("-", "")[:32]  # 32 hex chars, no dashes

    # Ensure sources is a list
    sources_list = sources or []

    with _lock:
        conn = _get_conn()
        try:
            conn.execute(
                """
                INSERT INTO notes (id, created_at, chat_id, chat_title,
                                   user_message, assistant_message, sources_json, model)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
                (
                    note_id,
                    _now(),
                    chat_id,
                    chat_title,
                    user_message,
                    assistant_message,
                    json.dumps(sources_list),  # JSON string, safe
                    model,
                ),
            )
            conn.commit()
            return get(note_id) or {}
        finally:
            conn.close()


def get(note_id: str) -> dict | None:
    """Fetch a single note by ID, or None if not found/invalid."""
    if not _is_valid_id(note_id):
        return None
    _ensure_schema()
    conn = _get_conn()
    try:
        conn.row_factory = sqlite3.Row
        cur = conn.execute("SELECT * FROM notes WHERE id = ?", (note_id,))
        row = cur.fetchone()
        return _row_to_dict(row) if row else None
    finally:
        conn.close()


def list_all(limit: int = 500, offset: int = 0) -> list[dict]:
    """Return newest notes first, with snippet for list views."""
    _ensure_schema()
    conn = _get_conn()
    try:
        conn.row_factory = sqlite3.Row
        cur = conn.execute(
            """
            SELECT * FROM notes
            ORDER BY created_at DESC
            LIMIT ? OFFSET ?
            """,
            (limit, offset),
        )
        return [_row_to_dict(r, add_snippet=True) for r in cur.fetchall()]
    finally:
        conn.close()


def delete(note_id: str) -> bool:
    """Delete a note. Returns True if deleted, False if not found/invalid."""
    if not _is_valid_id(note_id):
        return False
    _ensure_schema()
    with _lock:
        conn = _get_conn()
        try:
            # First verify the note exists and get its rowid for FTS5 delete
            cur = conn.execute("SELECT rowid FROM notes WHERE id = ?", (note_id,))
            row = cur.fetchone()
            if not row:
                return False

            rowid = row[0]

            # Delete from FTS5 first, then main table
            conn.execute("DELETE FROM notes_fts WHERE rowid = ?", (rowid,))
            conn.execute("DELETE FROM notes WHERE id = ?", (note_id,))
            conn.commit()
            return True
        finally:
            conn.close()


def _sanitize_fts_query(query: str) -> str:
    """Sanitize a query for FTS5 MATCH: wrap each word in double quotes.

    Double-quoted terms are treated as literals by FTS5, so operators
    like AND/OR/NOT/NEAR inside quotes are safe.
    """
    tokens = query.split()
    if not tokens:
        return ""
    # Escape embedded double quotes and wrap each token
    safe_tokens = [f'"{t.replace(chr(34), chr(34) + chr(34))}"' for t in tokens]
    return " ".join(safe_tokens)


def search(query: str, limit: int = 200) -> list[dict]:
    """Search notes using FTS5 MATCH.

    Sanitizes the query to avoid syntax errors.
    Returns notes ordered by created_at DESC.
    """
    _ensure_schema()
    if not query:
        return []

    safe_query = _sanitize_fts_query(query)
    if not safe_query:
        return []

    conn = _get_conn()
    try:
        conn.row_factory = sqlite3.Row
        # FTS5 MATCH on the virtual table, join back to main table
        cur = conn.execute(
            """
            SELECT n.* FROM notes n
            INNER JOIN notes_fts f ON n.rowid = f.rowid
            WHERE f.notes_fts MATCH ?
            ORDER BY n.created_at DESC
            LIMIT ?
            """,
            (safe_query, limit),
        )
        return [_row_to_dict(r, add_snippet=True) for r in cur.fetchall()]
    finally:
        conn.close()
