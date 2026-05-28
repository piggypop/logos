"""LLM-driven routing and extraction helpers.

All prompt strings live in `prompts.py`. This module only orchestrates the
LLM calls and parses their output. Every function is fail-safe and never
raises into the chat endpoint.
"""
import re
import sys
from datetime import datetime

import ollama as ol

import prompts


def _last_user_message(messages: list[dict]) -> str:
    return next((m["content"] for m in reversed(messages) if m["role"] == "user"), "")


# Keywords that should ALWAYS trigger a web search regardless of what the
# small local router model thinks. These are unambiguously live/fresh-data
# signals (sports standings, today's news, current prices, etc.) where the
# 7B-class router has been observed to return NO on Greek/multilingual input.
#
# We match case-insensitively against the raw user message. Hits short-circuit
# the LLM call in needs_search() — see Phase D (Giro d'Italia regression).
_LIVE_DATA_PATTERNS = [
    # English temporal / liveness cues
    r"\btoday\b", r"\btonight\b", r"\bcurrent(?:ly)?\b", r"\blatest\b",
    r"\bnow\b", r"\blive\b", r"\bbreaking\b", r"\brecent(?:ly)?\b",
    r"\bthis (?:week|morning|afternoon|evening)\b",
    # English sports / results cues
    r"\bscore[s]?\b", r"\bresult[s]?\b", r"\bstanding[s]?\b",
    r"\bgc\b", r"\bgeneral classification\b", r"\bstage \d+\b",
    # Greek temporal / liveness cues
    r"σήμερα", r"τώρα", r"σημεριν", r"τρέχουσ", r"τρέχων",
    r"τελευταί", r"πρόσφατ", r"εφέτος", r"φέτος",
    # Greek past-recent and near-future cues (added v1.6.0 after the
    # "με ποιον επαίζε ο Ολυμπιακός εχθές" regression — the router LLM
    # missed it and no search was attempted).
    r"χθες", r"εχθές", r"εχθες", r"χτες",
    r"προχθές", r"προχτές",
    r"αύριο", r"αυριο", r"μεθαύριο",
    r"\bαπόψε\b", r"\bαποψε\b",
    # Greek sports / results cues
    r"σκορ", r"κατάταξη", r"βαθμολογί", r"αποτέλεσμα", r"εταπ", r"ετάπ",
    # Currency / price cues
    r"\bprice\b", r"\bexchange rate\b", r"\bcrypto\b",
    r"τιμή", r"ισοτιμί",
]
_LIVE_DATA_RE = re.compile("|".join(_LIVE_DATA_PATTERNS), re.IGNORECASE)


def _looks_like_live_question(user_message: str) -> bool:
    """Deterministic fast-path: True if the message contains any keyword that
    unambiguously requires fresh web data. Lets us bypass an unreliable LLM
    routing call for the most common false-negative case."""
    if not user_message:
        return False
    return bool(_LIVE_DATA_RE.search(user_message))


def _maybe_attach_today(user_message: str, query: str) -> str:
    """If the user's message implies 'today', make sure today's ISO date
    appears in the query. Search engines pin much harder to fresh results
    when the date is in the query string."""
    if not query:
        return query
    today_iso = datetime.now().astimezone().date().isoformat()
    if today_iso in query:
        return query
    msg = (user_message or "").lower()
    today_triggers = (
        "today", "tonight", "σήμερα", "σημεριν",
        "now", "τώρα", "live", "latest", "πρόσφατ", "τελευταί",
        # Past-recent / near-future Greek cues — when present, pinning
        # today's date into the query still improves freshness (search
        # engines bias toward the past week from a dated query).
        "χθες", "εχθές", "εχθες", "χτες", "προχθές",
        "αύριο", "αυριο", "απόψε", "αποψε",
    )
    if any(t in msg for t in today_triggers):
        return f"{query} {today_iso}"
    return query


def reformulate_query(messages: list[dict], host: str, model: str) -> str:
    """Returns a self-contained web search query for the last user message.
    On any error, falls back to the last user message verbatim."""
    if not messages:
        return ""
    fallback = _last_user_message(messages)
    try:
        client = ol.Client(host=host, timeout=30.0)
        response = client.chat(
            model=model,
            messages=[
                {"role": "system", "content": prompts.QUERY_REFORMULATOR},
                *messages[-6:],
                {"role": "user", "content": prompts.REFORMULATOR_USER_HINT},
            ],
            stream=False,
            options={"temperature": 0},
        )
        q = response["message"]["content"].strip().strip('"').strip("'")
        q = q.split("\n")[0].strip()
        q = q or fallback
        return _maybe_attach_today(fallback, q)
    except Exception as e:
        print(f"[tool_router] reformulate_query error: {e}", file=sys.stderr)
        sys.stderr.flush()
        return _maybe_attach_today(fallback, fallback)


def extract_facts(
    messages: list[dict],
    host: str,
    model: str,
    existing_facts: list[str],
) -> list[str]:
    """Returns NEW persistent facts about the user, or []."""
    if not messages:
        return []
    try:
        client = ol.Client(host=host, timeout=30.0)
        response = client.chat(
            model=model,
            messages=[
                {
                    "role": "system",
                    "content": prompts.fact_extractor_system(existing_facts),
                },
                *messages[-6:],
                {"role": "user", "content": prompts.FACT_EXTRACTOR_USER_HINT},
            ],
            stream=False,
            options={"temperature": 0},
        )
        text = (response["message"]["content"] or "").strip()
        if not text or text.upper().startswith("NONE"):
            return []
        facts = []
        for line in text.splitlines():
            line = line.strip().lstrip("-•*").strip()
            # Filter out empty, NONE, banal echoes, or absurdly long lines
            if line and line.upper() != "NONE" and 3 < len(line) < 300:
                facts.append(line)
        return facts
    except Exception as e:
        print(f"[tool_router] extract_facts error: {e}", file=sys.stderr)
        sys.stderr.flush()
        return []


def needs_search(user_message: str, host: str, model: str) -> bool:
    """Returns True if a web search is needed for this turn.

    Deterministic fast-path first (live-data keywords like 'today',
    'σήμερα', 'standings', 'βαθμολογία', ...) — small router models have
    been observed to miss obvious live questions in non-English input.
    Falls back to the LLM router for ambiguous cases. False on any error.
    """
    if _looks_like_live_question(user_message):
        return True
    try:
        client = ol.Client(host=host, timeout=30.0)
        response = client.chat(
            model=model,
            messages=[
                {"role": "system", "content": prompts.ROUTER_NEEDS_SEARCH},
                {"role": "user", "content": user_message},
            ],
            stream=False,
            options={"temperature": 0},
        )
        answer = response["message"]["content"].strip().upper()
        return "YES" in answer
    except Exception as e:
        print(f"[tool_router] needs_search error: {e}", file=sys.stderr)
        sys.stderr.flush()
        return False
