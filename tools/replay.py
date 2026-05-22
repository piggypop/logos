#!/usr/bin/env python3
"""Replay a saved chat against the current system prompt.

Reads a chat JSON from ~/.local/share/logos/chats/<id>.json, replays each
user turn through the live Logos instance, and writes a side-by-side
comparison file.

Usage:
    python tools/replay.py <chat_id> [--host http://localhost:17842]

Output: tools/replay_output/<chat_id>.md
"""

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx

CHATS_DIR = Path.home() / ".local" / "share" / "logos" / "chats"
OUTPUT_DIR = Path(__file__).resolve().parent / "replay_output"


def stream_chat(host: str, messages: list[dict], force_search: bool = False):
    """Send POST /api/chat and collect assistant response."""
    assistant_text = ""
    sources: list[dict] = []
    sse_events: list[str] = []

    with httpx.stream(
        "POST",
        f"{host}/api/chat",
        json={"messages": messages, "force_search": force_search},
        timeout=httpx.Timeout(300.0, connect=10.0),
    ) as response:
        response.raise_for_status()
        for line in response.iter_lines():
            if line.startswith("data: "):
                try:
                    event = json.loads(line[6:])
                except json.JSONDecodeError:
                    continue
                etype = event.get("type", "")
                sse_events.append(etype)
                if etype == "token":
                    assistant_text += event.get("content", "")
                elif etype == "sources":
                    sources = event.get("sources", [])
                elif etype == "error":
                    return {
                        "assistant_text": f"[ERROR: {event.get('message', 'unknown')}]",
                        "sources": sources,
                        "sse_events": sse_events,
                    }

    return {
        "assistant_text": assistant_text,
        "sources": sources,
        "sse_events": sse_events,
    }


def main():
    parser = argparse.ArgumentParser(description="Replay a Logos chat")
    parser.add_argument(
        "chat_id", help="Chat ID (UUID from ~/.local/share/logos/chats/)"
    )
    parser.add_argument("--host", default="http://localhost:17842")
    args = parser.parse_args()

    # Load chat
    chat_path = CHATS_DIR / f"{args.chat_id}.json"
    if not chat_path.exists():
        print(f"ERROR: Chat not found: {chat_path}")
        sys.exit(1)

    with open(chat_path, encoding="utf-8") as f:
        chat_data = json.load(f)

    messages = chat_data.get("messages", [])
    user_turns = [m for m in messages if m["role"] == "user"]

    print(f"Chat: {chat_data.get('title', args.chat_id)}")
    print(f"User turns: {len(user_turns)}")
    print(f"Replaying against {args.host}...")

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = OUTPUT_DIR / f"{args.chat_id}.md"

    with open(out_path, "w", encoding="utf-8") as out:
        out.write(f"# Replay: {chat_data.get('title', args.chat_id)}\n\n")
        out.write(f"**Date:** {datetime.now(timezone.utc).isoformat()}\n")
        out.write(f"**Chat ID:** {args.chat_id}\n")
        out.write(f"**Turns:** {len(user_turns)}\n\n")
        out.write("---\n\n")

        context: list[dict] = []
        user_count = 0
        for i, msg in enumerate(messages):
            if msg["role"] == "user":
                context.append(msg)
                user_count += 1
                print(f"  Turn {user_count}/{len(user_turns)}...", end=" ", flush=True)

                result = stream_chat(args.host, list(context))
                new_reply = result["assistant_text"]

                # Find original assistant reply (next message after this user)
                original_reply = ""
                try:
                    idx = messages.index(msg)
                    if (
                        idx + 1 < len(messages)
                        and messages[idx + 1]["role"] == "assistant"
                    ):
                        original_reply = messages[idx + 1].get("content", "")
                except ValueError:
                    pass

                out.write(f"## Turn {user_count}\n\n")
                preview = (
                    msg["content"][:200] + "..."
                    if len(msg["content"]) > 200
                    else msg["content"]
                )
                out.write(f"**User:** {preview}\n\n")
                out.write("### Original\n\n")
                out.write(original_reply or "(no original reply found)")
                out.write("\n\n### New\n\n")
                out.write(new_reply)
                out.write("\n\n---\n\n")

                # Add the new reply to context for next turns
                context.append({"role": "assistant", "content": new_reply})
                print("done")
            elif msg["role"] == "assistant":
                # Skip original assistant replies; we use our own
                pass

    print(f"\nOutput: {out_path}")


if __name__ == "__main__":
    main()
