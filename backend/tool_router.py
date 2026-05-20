import sys

import ollama as ol


def _last_user_message(messages: list[dict]) -> str:
    return next((m["content"] for m in reversed(messages) if m["role"] == "user"), "")


def reformulate_query(messages: list[dict], host: str, model: str) -> str:
    """
    Παίρνει το conversation context και επιστρέφει ένα self-contained search
    query. Fail-safe: επιστρέφει το τελευταίο user message σε error.
    """
    if not messages:
        return ""
    fallback = _last_user_message(messages)
    try:
        recent = messages[-6:]
        client = ol.Client(host=host)
        response = client.chat(
            model=model,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are a search query writer. Given a conversation, "
                        "write a single concise web search query that retrieves the "
                        "information needed to answer the user's most recent message. "
                        "RULES: "
                        "(1) Make the query SELF-CONTAINED — resolve pronouns and references "
                        "using earlier context (entities, locations, dates, topics). "
                        "(2) Include DOMAIN keywords that match the user's intent, to avoid "
                        "irrelevant matches on proper nouns: "
                        "'weather forecast' for weather, 'news' or specific event type for current events, "
                        "'review' for products, 'recipe' for food, 'lyrics' for songs, "
                        "'results' or 'standings' for sports, 'price' for shopping. "
                        "(3) Prefer English keywords for global topics; keep proper nouns in the "
                        "original script. "
                        "(4) Output ONLY the query text — no quotes, no prefix, no explanation, "
                        "no newlines."
                    ),
                },
                *recent,
                {
                    "role": "user",
                    "content": "Write the search query for my most recent message above.",
                },
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
    """
    Διαβάζει την τελευταία ανταλλαγή και επιστρέφει νέα persistent facts για
    τον χρήστη. Επιστρέφει [] σε error ή αν δεν υπάρχει κάτι νέο.
    """
    if not messages:
        return []
    try:
        existing_block = (
            "\n".join(f"- {f}" for f in existing_facts) if existing_facts else "(none yet)"
        )
        client = ol.Client(host=host)
        response = client.chat(
            model=model,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are a memory extractor for a chat app. Read the conversation "
                        "below and extract any NEW persistent facts the user revealed about "
                        "THEMSELVES: name, location, age, work, family, hobbies, preferences, "
                        "dietary needs, opinions, ongoing projects. "
                        "STRICT RULES:\n"
                        "1. Skip ephemeral things (current mood, today's plans, one-off questions, "
                        "weather they asked about, news they read).\n"
                        "2. Skip anything already in 'Already known' below.\n"
                        "3. Only facts about the USER, not about external entities they discussed.\n"
                        "4. Each fact: one short declarative sentence in the user's language.\n"
                        "5. If nothing genuinely new and persistent, output exactly: NONE\n\n"
                        "Already known facts:\n" + existing_block
                    ),
                },
                *messages[-6:],
                {"role": "user", "content": "Extract new persistent facts about me now. Output bullet list or NONE."},
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
            if line and line.upper() != "NONE" and len(line) < 300:
                facts.append(line)
        return facts
    except Exception as e:
        print(f"[tool_router] extract_facts error: {e}", file=sys.stderr)
        sys.stderr.flush()
        return []


def needs_search(user_message: str, host: str, model: str) -> bool:
    """
    Ρωτά το LLM αν χρειάζεται web search. Fail-safe: επιστρέφει False σε error.
    """
    try:
        client = ol.Client(host=host)
        response = client.chat(
            model=model,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are a routing assistant for a chat app. "
                        "The main assistant ALREADY KNOWS: the current date and time, "
                        "the user's location, and general knowledge up to its training cutoff. "
                        "Reply YES only if the user's message needs FRESH information from the web "
                        "that the assistant cannot already answer — for example: today's news, "
                        "live scores, current prices, recent events, specific facts that "
                        "post-date the model's training, or content from a specific webpage. "
                        "Reply NO for: simple time/date/location questions, general knowledge, "
                        "coding help, definitions, opinions, math, translation, summarization "
                        "of provided text, or anything the assistant can answer from its training. "
                        "Reply with exactly one word: YES or NO."
                    ),
                },
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
