"""LLM-driven routing and extraction helpers.

All prompt strings live in `prompts.py`. This module only orchestrates the
LLM calls and parses their output. Every function is fail-safe and never
raises into the chat endpoint.
"""
import sys

import ollama as ol

import prompts


def _last_user_message(messages: list[dict]) -> str:
    return next((m["content"] for m in reversed(messages) if m["role"] == "user"), "")


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
        return q or fallback
    except Exception as e:
        print(f"[tool_router] reformulate_query error: {e}", file=sys.stderr)
        sys.stderr.flush()
        return fallback


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
    """Asks the LLM whether web search is needed. False on any error."""
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
