# Logos — Small-Model Robustness Roadmap (v1.2 → v1.3)

> **Status:** Completed and shipped in v1.3.0 (2026-05-22). Kept in-repo as a historical record of the planning cycle.
> **Authored:** 2026-05-21, based on test sessions with `gemma3:12b` and `gemma3n:e4b` on Logos v1.2.0.
> **Target version:** v1.3.0
> **Owner:** Architect (you, returning for review). Implementer: any LLM coder following this doc.

---

## How to use this document

This roadmap is structured as a sequence of **milestones** (`B1`, `B2`, …, `E2`) grouped into **phases** (A–E). Each milestone is sized to fit one focused change and ships independently. The whole roadmap can be executed front-to-back, but phases A and B are the priority — they alone address the most visible problems from the test sessions.

**For the implementing model:**
1. Read this document fully before touching any code.
2. Read `developers.md` (single source of truth for current architecture) and the specific files named in each milestone.
3. Implement one milestone at a time. Each milestone has an **Acceptance criteria** section — verify those before marking it done.
4. **Stop and return to the architect** at the end of each phase (A, B, C, D, E). Do not begin the next phase autonomously. The architect reviews and either signs off or revises the roadmap.
5. Each milestone has a **Risks / open questions** section. If something there isn't resolved by the time you reach the milestone, stop and ask before guessing.
6. Update the **Status** field at the top of this document when you start work (`In progress — currently on Bx`) and after the phase review (`Phase B approved, moving to C`).

**Conventions in this document:**
- File paths are repo-relative (`backend/prompts.py`, not absolute).
- Code excerpts shown as “**Before**” are descriptive of the current state; “**After**” describes the target. Pseudo-code is allowed where it clarifies intent; do not copy-paste it literally.
- `S / M / L` complexity estimates: S = one function and < ~30 lines, M = one module, L = multi-module or new module.

---

## 0. Background — what we learned from testing

Two real chat sessions were run on v1.2.0 with small local models. Findings:

**Confirmed issues across both models:**

1. **Date injection ignored.** When asked "what is the date?", the model returned `23 Μαΐου 2024` despite the correct date `21 Μαΐου 2026` being placed in the system prompt by `_build_system_prompt()`. The model overrode the injection.

2. **Thin-source hallucination.** When asked for "today's news in Greece", web search returned five Greek news homepage URLs. `trafilatura` extracted only navigation/banner cruft (homepages have no article body). The model filled the void by inventing five generic news items (tax reform, EU meeting, weather warnings, flu, "ongoing case") with no basis in the sources, and cited the homepage URLs in support of these inventions.

3. **Citation fabrication.** When asked about Linux server market share, the model produced specific percentages ("Statista 73.6%", "Netcraft 86.4%", "W3Techs 83.9%") attributed via `[N]` to sources that were entirely different sites (Wikipedia, Statcounter, etc.). The model treated `[N]` as a stylistic flourish, not a semantic claim.

4. **Model also fabricated when sources looked thin** even when not strictly necessary — e.g. invented `Ollama v0.30.0-rc12, 10 May 2026` from sources that were Ollama download pages and release indexes.

**Issues only in the smaller model (`gemma3n:e4b`):**

5. **Multilingual token leakage.** When generating Greek, the model leaked tokens from other scripts: Vietnamese (`vận`, `nhớ`), Chinese (`俯瞰`, `握手`), Hindi (`पात्र`), Japanese (`される`), plus invented Greek-looking words (`ΣυΠυκνώματος`, `Επιδόccion`). The 12B model did not exhibit this.

6. **Broken code generation.** The smaller model produced a JSON→CSV script that defines a function but never calls it, contains a garbled section header, and has dead code. The larger model produced working code for the same prompt.

**Non-issues that initially looked like issues:**

- **YouTube summary framing** — initial impression was that the model hallucinated a Dark Souls summary for a Hollow Knight video. After reviewing the actual transcript, the video itself spends substantial time on Dark Souls / Soulslike mechanics. The model used real material but inverted the thesis hierarchy (presented Dark Souls as primary subject, Hollow Knight as comparison). This is a real but **subtle** framing problem, not fabrication. Tracked as B4.

---

## 1. Design principles for this roadmap

These principles bind every milestone. The implementing model **must not** violate them without architect approval.

**P1. Prompts live in `prompts.py`.** No prompt strings inline in `server.py`, `tool_router.py`, `url_fetcher.py`, etc. If you need a new prompt fragment, add it as a named constant in `prompts.py`. This rule is already in `developers.md §7`; the roadmap reinforces it.

**P2. No new schema migrations unless explicitly called for.** Chat JSON, config JSON, and memory JSON formats stay as in v1.2. If a milestone needs new config keys, they go into `config.DEFAULTS` with sensible defaults so existing config files merge cleanly; this is the existing pattern.

**P3. Fail-safe preserved.** Every existing endpoint must keep its current behavior on the failure path. The chat stream **never aborts because a side feature failed** (developers.md §12). Any new failure mode must be caught and either swallowed silently or surfaced as a clearly-marked event in the SSE stream.

**P4. Backwards compat for stored chats.** Old chat JSONs must keep loading. If you change anything in `chats.py:_slim_messages` or the `Message` shape, document it and provide a migration path.

**P5. Backwards compat for stored configs.** Whenever `MAIN_SYSTEM_PROMPT` (or any other "well-known default") changes, append the old value to a list of "auto-upgradable old defaults" so that users who never touched the default get the new one transparently. The pattern in `config.py:_OLD_DEFAULT_SYSTEM_PROMPT` is the existing model; generalize it into a list (see C0 in Phase B).

**P6. No new heavyweight dependencies.** Logos prides minimal deps (developers.md §12). Stdlib first, then the already-installed set in `backend/requirements.txt`. Adding a new dep requires architect approval.

**P7. Out of scope.** The following are explicitly **not** in this roadmap and must not be attempted:
- Math/algorithm correctness in model outputs (model-level limitation; no prompt fix is reliable).
- Code-generation correctness (same).
- Vector store, semantic memory, or RAG (deliberately declined in developers.md §13).
- Agent loop / model-decided tool use (same).
- TTS / STT (out of scope).
- Major UI rework — settings tabs and chat layout stay as-is unless a milestone explicitly says otherwise.

**P8. Every milestone has acceptance criteria.** If you can't verify the criteria by sending a manual chat through the running app, the criteria are wrong — flag for the architect.

---

## 2. Phase overview

| Phase | Theme | Milestones | Blocks downstream? |
|-------|-------|------------|--------------------|
| **A** | Observability foundation | A1 | No — but A1 makes B easier to verify |
| **B** | Prompt-only hardening | B1, B2, B3, B4 | Phase D depends on B |
| **C** | Source-quality signals | C1, C2 | Phase D builds on C |
| **D** | Per-model tuning | D1, D2 | — |
| **E** | Test harness | E1, E2 | — |

**Recommended order:** A1 → B1 → B2 → B3 → B4 → **stop for review** → C1 → C2 → **stop for review** → D1 → D2 → **stop for review** → E1 → E2 → final review.

A single stop-for-review can also happen at the end of every milestone if the implementer feels uncertain — preferred over silent guessing.

---

## 3. Phase A — Observability foundation

### A1. Capture the assembled system prompt to disk on demand

**Goal:** Make it possible to see exactly what system prompt Logos sends to Ollama on any given turn, without re-instrumenting the code each time.

**Motivation:** Several of the B milestones modify how the system prompt is assembled. Without a way to inspect the actual prompt that reached the model, we cannot verify acceptance criteria — we'd be guessing whether the model "ignored" the date or whether the date was actually injected. The Phase B fixes are unverifiable without this.

**Affected files:**
- `backend/config.py` (add a debug flag to `DEFAULTS`)
- `backend/server.py` (add the logging at the point of assembly in `chat()` / `_build_system_prompt()`)
- *(optional)* `frontend/index.html` + `frontend/app.js` to expose the toggle in Settings → Model tab. **Defer to the architect** whether to expose this in the UI; for the first pass, a config-file-only flag is enough.

**Change pattern:**
1. Add a new config key `"debug_log_prompts": false` to `DEFAULTS` in `config.py`. (Per P5: also add the *previous* `DEFAULTS` size to the auto-upgrade list if applicable — for a pure addition like this, default merging handles it; no upgrade entry needed.)
2. In `server.py`, immediately after `system_prompt` is built (line ~503 in v1.2.0, inside `chat()`'s `generate()`), if `c.get("debug_log_prompts")` is truthy, append the prompt — plus a timestamp, model name, and a hash of the last user message — to a file under `~/.local/share/logos/debug/prompts.log`. One JSON object per line (JSONL).
3. Rotate the file at ~5 MB by truncating to the last 1 MB. No need for `logging.handlers.RotatingFileHandler` — a single try/except with a manual size check is enough and avoids a new dep.
4. Path safety: write to `Path.home() / ".local" / "share" / "logos" / "debug" / "prompts.log"`. Create parents.

**Acceptance criteria:**
- With `debug_log_prompts` set to `true` in `~/.config/logos/config.json`, send a chat message. A new line appears in `~/.local/share/logos/debug/prompts.log` containing the full assembled system prompt as a JSON string field.
- With the flag false (default), no file is created, no extra disk I/O.
- Disabling the flag mid-session stops appending (no lazy global state).
- Path traversal is impossible — the file path is hardcoded in code, not user-supplied.

**Risks / open questions:**
- Privacy: the log will contain the entire conversation history (via the messages) and the memory facts in the system prompt. **Documented behavior**: this is debug-only, off by default, on-disk only, never transmitted. State this clearly in `developers.md` when adding the flag.
- Should the prompt log also capture the *response*? **Decision (architect):** no — a turn's response is already in the chat JSON. Logging the prompt is the new information. Don't double-log.

**Complexity:** S.

---

## 4. Phase B — Prompt-only hardening

All Phase B milestones are edits to `backend/prompts.py` only (plus the auto-upgrade list in `config.py`). No changes to `server.py`, `tool_router.py`, `url_fetcher.py`, or any client code. This makes Phase B the safest, highest-impact phase.

### B0. (Prep) Generalize the "old default" auto-upgrade machinery

**Goal:** Before changing `MAIN_SYSTEM_PROMPT` in B3, refactor `config.py` so that an arbitrary number of historical defaults are recognized and auto-upgraded.

**Affected files:** `backend/config.py`.

**Change pattern:**
- Replace the single `_OLD_DEFAULT_SYSTEM_PROMPT` constant with a list `_LEGACY_DEFAULT_SYSTEM_PROMPTS: list[str]`. Pre-populate with the existing single value. The check in `load()` becomes `if (data.get("system_prompt") or "").strip() in {p.strip() for p in _LEGACY_DEFAULT_SYSTEM_PROMPTS}: data["system_prompt"] = _prompts.MAIN_SYSTEM_PROMPT`.
- Document at the top of the constant that **every time `MAIN_SYSTEM_PROMPT` changes**, the previous value must be appended to this list (with a comment saying which version it was the default in).

**Acceptance criteria:**
- A config file containing the v1.1-era prompt still auto-upgrades to the current `MAIN_SYSTEM_PROMPT` on next `load()`.
- A config with a custom (non-default) prompt is untouched.

**Risks / open questions:** None.

**Complexity:** S.

---

### B1. Strengthen date/time injection

**Goal:** Make the model always reply with the injected date when asked "what day/time is it?", and never override the year/month/day from its training data.

**Motivation:** Test session, log 1, turn 14 — model returned `23 Μαΐου 2024` despite system prompt containing `Current date and time: Thursday, 21 May 2026, ...`.

**Affected files:** `backend/prompts.py` only.

**Change pattern:**
1. Currently `compose_system_prompt()` (line ~180) puts the date as the second item of `parts`, formatted by `server.py` as `f"Current date and time: {now.strftime('%A, %d %B %Y, %H:%M %Z')}"`. The string is unmarked and easily lost among other content for a small model.
2. Wrap the date line with an explicit instruction block. The new wrapping template should:
   - Lead with a marker that's hard to ignore (suggested: `## CURRENT DATE AND TIME (authoritative — overrides training data)`).
   - Restate the date in two formats: ISO and human-readable.
   - Include a one-line rule: *"When the user asks about today's date, current time, or any 'now'-relative question, use exactly this value. Do not output a year or day from your training data."*
   - Be repeated **once more** near the end of the assembled prompt (just before any `sources_block`), so a small model with weak attention to the top of the system prompt sees it again close to the user turn.
3. Refactor `compose_system_prompt()` to take a more structured `date_info` parameter rather than a pre-formatted string. The server passes `{"iso": "...", "human": "...", "tz": "..."}` and `prompts.py` owns the wrapping. This keeps prompt text in `prompts.py` (P1).

**Acceptance criteria:**
- With `gemma3n:e4b` and `gemma3:12b`, the question "Τι ημερομηνία και ώρα έχουμε;" returns the actual current date.
- Verifiable using A1's prompt log: the assembled prompt contains the new marker block and the second-mention near the bottom.

**Risks / open questions:**
- Possible over-correction: model may now insert the date into unrelated replies. Mitigation: the rule must say *"when the user asks about ... use this value"*, not *"include the date in every reply"*.
- Refactoring `compose_system_prompt()`'s signature breaks any unknown caller. Currently only `server.py:_build_system_prompt()` calls it — grep to confirm before changing.

**Complexity:** S.

---

### B2. Anti-fabrication rules in `SEARCH_MODE_PROMPT`

**Goal:** Stop the model from inventing specific facts (numbers, dates, named entities) when sources are present but don't contain those facts, and stop it from citing `[N]` when N doesn't literally contain the claim.

**Motivation:** Test session, log 1, turns 6, 7, 8, 11 — invented news items, invented release numbers, invented market-share statistics, all with `[N]` citations to sources that didn't contain the claimed information.

**Affected files:** `backend/prompts.py` (`SEARCH_MODE_PROMPT`).

**Change pattern:**
Replace `SEARCH_MODE_PROMPT` with a more explicit ruleset. Suggested structure (final wording is the implementer's call; preserve the meaning):

```
You have REAL-TIME sources below.

RULES (in order of priority):

1. Use only the sources for any specific fact: numbers, dates, prices, named
   people, named events, statistics. If the sources do not contain such a
   fact, do NOT state it. Do NOT fill in plausible-sounding values from
   memory.

2. A citation [N] is a CLAIM that the exact statement appears in source N's
   text shown below. Before citing [N], confirm that the specific number,
   date, or name you're stating is literally present in source N. If it is
   not, do not add the citation, and consider whether you should state the
   claim at all.

3. If the sources are thin (only titles and short snippets, no article
   body), say so explicitly: "The search returned only headlines, no
   article content — I cannot give specific details from these." This is
   the correct answer. Do not invent details to compensate.

4. If the sources do not address the user's question at all, say so: "The
   search results don't cover this question." Then answer from your
   general knowledge if you can, and clearly label that part as "from
   general knowledge, not the sources".

5. Never claim "I cannot access the internet" or "I have no current data"
   when sources are present in your context.
```

**Acceptance criteria:**
- Repeat the test from log 1: "Τι έγινε στις ειδήσεις στην Ελλάδα σήμερα;" with the current `ddg` search provider. With the new prompt, the model should either (a) refuse to enumerate specific events because the sources are homepages without article content, or (b) only state things that are actually present in the snippets.
- "Linux server share" question: no invented Statista/Netcraft percentages unless one of those sources is actually returned and contains the percentage.
- Manual verification only; no automated check.

**Risks / open questions:**
- Risk of over-refusal: model might start refusing answerable questions. Mitigation: rule 4 explicitly licenses falling back to general knowledge when the question isn't covered.
- Small models may not fully follow ordered rules. Acceptance is "noticeably better", not "perfect". If the improvement is marginal, escalate to architect — may need to bias toward refusing answers when source quality is low (this is where C1+C2 help).

**Complexity:** S.

---

### B3. Language-consistency rule in `MAIN_SYSTEM_PROMPT`

**Goal:** Reduce multilingual token leakage in small-model Greek output.

**Motivation:** Test session, log 2 — `gemma3n:e4b` produced Greek text contaminated with Vietnamese, Chinese, Hindi, Japanese characters.

**Affected files:** `backend/prompts.py` (`MAIN_SYSTEM_PROMPT`) and the auto-upgrade list (B0).

**Change pattern:**
1. Add one rule (number 7) to the existing list in `MAIN_SYSTEM_PROMPT`:

```
7. Write in ONE script at a time. If your reply is in Greek, use only
   Greek letters and Latin letters (Latin only for code, brand names,
   loan words). Never insert Chinese, Japanese, Korean, Vietnamese,
   Hindi, Arabic, or Cyrillic characters into Greek text. If you don't
   know a Greek word for something, write the English word in
   parentheses instead.
```

2. Per B0/P5: copy the current `MAIN_SYSTEM_PROMPT` text into `_LEGACY_DEFAULT_SYSTEM_PROMPTS` in `config.py` **before** editing the prompt. Users on the v1.2 default get upgraded.

**Acceptance criteria:**
- Re-run a long Greek conversation with `gemma3n:e4b`. Manual visual inspection: the response contains no characters outside the Greek + Latin + standard punctuation ranges.
- If foreign-script characters still appear with the rule in place, this is a model-level limit — escalate to architect; the milestone is still considered "best-effort done" if the prompt rule is present and the model is the bottleneck.

**Risks / open questions:**
- Effect on multilingual chats (e.g., user mixes English and Greek): the rule says "one script at a time" but Latin in Greek is allowed. Re-read the wording and make sure code blocks, brand names, and URLs are not penalized.
- This is best-effort. Small models with broken multilingual tokenizers cannot be fully fixed by prompt rules. Document this in the milestone closeout note.

**Complexity:** S.

---

### B4. Summary-framing rule for URL / notebook content

**Goal:** When the model summarizes long source content (a fetched URL or a notebook source), prevent it from inverting thesis and supporting material — i.e. from promoting a sub-topic over the actual primary subject.

**Motivation:** Test session, log 2, YouTube turn. Video's primary subject was Hollow Knight; the video used Dark Souls heavily as comparative material. The model's summary inverted this, presenting Dark Souls as the primary subject. Subtle but real.

**Affected files:** `backend/prompts.py`. Add a new prompt fragment `SUMMARY_FRAMING_PROMPT`. Decide whether it's appended as part of `SEARCH_MODE_PROMPT` always, or only when there are URL/notebook sources (no plain web-search results). Architect's lean: append it whenever any source has `category != "web"` (URL fetches and notebook), since those are long-form documents where framing matters; for web search snippets, framing is less of a risk because the model has only headlines anyway.

**Change pattern:**
1. New constant in `prompts.py`:

```
SUMMARY_FRAMING_PROMPT = """One or more sources below contain long-form
content (a webpage, a video transcript, or a notebook source).

When you summarize or answer from such content:
1. First identify the author's CENTRAL THESIS — the single thing the
   author wants the reader/viewer to take away. Build your reply around
   that.
2. Comparisons, examples, and background material in the source are
   there to SUPPORT the thesis. Do not promote them above the thesis.
   If the author mentions Topic X heavily as a comparison to make a
   point about Topic Y, the reply is about Topic Y; X is mentioned only
   in the role the source uses it for.
3. The title of the source is usually a strong hint at the thesis. If
   the title says "I keep returning to Hollow Knight", the summary is
   about Hollow Knight, even if the video spends half its time on Dark
   Souls."""
```

2. In `compose_system_prompt()`, conditionally include this fragment when `has_long_form_sources` is true. The server passes a flag based on inspecting the sources list. Adding a flag to the function signature is acceptable — it's the central composer.

3. `server.py:_build_system_prompt` decides the flag:

```
has_long_form_sources = any(
    s.get("category") in ("notebook", "url") for s in all_sources
)
```

Note: current `url_fetcher` doesn't tag URL sources with `category: "url"`. Add that tag in `url_fetcher._fetch_generic`/`_fetch_youtube` returning `category: "url"` in the source dict, so the server can detect them. **Schema-compatible** — the existing consumers (`chats._slim_sources` keeps `category` already) handle this fine.

**Acceptance criteria:**
- Repeat the Hollow Knight YouTube test. The new summary should explicitly center Hollow Knight as the subject. Dark Souls / Soulslike content should be presented as the genre/comparative material the speaker uses, not as the video's topic.
- For a non-long-form chat (no notebook, no URL), the framing prompt does NOT appear in the system prompt (verify via A1 log).

**Risks / open questions:**
- Adding the `category: "url"` tag is a minor schema change to the SSE `sources` event consumers. Verify frontend (`app.js` `renderSources`) tolerates an unknown `category` — current code only uses `s.title` and `s.url`, so it should. Test by hand.

**Complexity:** M (touches prompts.py, server.py, url_fetcher.py).

---

### End of Phase B — STOP for architect review

Before starting Phase C: re-run both test sessions from the original gemma3:12b and gemma3n:e4b logs end-to-end. Capture the new outputs (use A1's log). Report to the architect with a summary of which issues B1–B4 visibly fixed, which were unaffected, and any new regressions. The architect signs off (or asks for revision) before Phase C.

---

## 5. Phase C — Source-quality signals

Phase B's `SEARCH_MODE_PROMPT` rule 3 ("if sources are thin, say so") asks the model to detect thinness from its context. Small models are unreliable at this. Phase C adds programmatic detection and surfaces it explicitly in the prompt.

### C1. Tag thin sources at fetch time

**Goal:** Add a `quality: "ok" | "thin" | "empty"` field to every source dict before it reaches the prompt assembler, computed from content length and basic content heuristics.

**Affected files:**
- `backend/search_providers.py` (web search results)
- `backend/url_fetcher.py` (URL fetches)
- `backend/open_notebook_client.py` (notebook sources)

**Change pattern:**
A small helper, **co-located** somewhere shared — most natural location is a new top-level helper in `search_providers.py` since it's already the canonical context-formatting module, or a new `backend/source_quality.py` if that feels cleaner. Architect's lean: put it in `search_providers.py` as `def assess_quality(content: str) -> str:` to avoid a new module.

Heuristic (start simple, tune from real data):
- `len(content.strip()) < 50` → `"empty"`
- `len(content.strip()) < 300` → `"thin"`
- otherwise → `"ok"`
- Additional signal for URL fetches: if `len(text)` is non-trivial but the page is a recognized homepage pattern (e.g. text is mostly link labels, no sentence-like structure), mark `"thin"`. Concrete signal: ratio of newlines to characters > 0.1 strongly suggests a navigation page. Optional refinement, add only if first heuristic isn't enough.

Each fetcher/searcher sets `result["quality"] = assess_quality(result["content"])` before returning.

**Acceptance criteria:**
- DDG search for "ειδήσεις Ελλάδα σήμερα" returns results where homepage URLs are tagged `"thin"`. Verify via A1's prompt log — the assembled prompt now contains quality information per source (see C2 for how).
- Notebook source with `full_text` of 10k chars is tagged `"ok"`.
- An empty/failed fetch is tagged `"empty"`.

**Risks / open questions:**
- Heuristics will misclassify edge cases. Acceptable — these are advisory, not enforcement.

**Complexity:** S–M depending on whether the homepage-detection refinement is included.

---

### C2. Surface quality in the context block

**Goal:** Make the model aware which sources are thin, so the rules in B2 actually have data to act on.

**Affected files:** `backend/search_providers.py` (`format_as_context`).

**Change pattern:**
Modify `format_as_context()` so each source entry includes its quality:

```
[1] (quality: ok) Title here
    URL: https://...
    <content>

[2] (quality: thin — headline only, no article body) Title here
    URL: https://...
    <content if any>

[3] (quality: empty — fetch failed) Title here
    URL: https://...
```

The exact phrasing of `(quality: ...)` is part of the prompt now and must be in `prompts.py` per P1. Refactor: pull the per-quality phrasing into a constant in `prompts.py` (e.g. `SOURCE_QUALITY_NOTES: dict[str, str]`) and have `format_as_context` import it.

**Acceptance criteria:**
- Verify via A1's prompt log: each source line in the context block is annotated with its quality.
- Re-run the "Greek news today" test: model now refuses to invent items (because every source is tagged `thin` and the B2 rules apply).
- Re-run a normal chat with one good URL: source is tagged `ok` and behavior is unchanged from Phase B.

**Risks / open questions:**
- Increases prompt length slightly. Acceptable — annotations are 5–10 tokens per source.

**Complexity:** S.

---

### End of Phase C — STOP for architect review

Confirm with the architect that quality tagging is behaving sensibly across a few real searches before Phase D.

---

## 6. Phase D — Per-model tuning

The two test models behave differently enough that one set of defaults can't be optimal for both. Phase D introduces lightweight per-model overrides.

### D1. Per-model config overrides

**Goal:** Let users set `temperature` (and a few other knobs) per model, with a fallback to a global default.

**Affected files:**
- `backend/config.py` (new key `model_overrides: dict[str, dict]`)
- `backend/server.py` (read the override when looking up `c["temperature"]`)
- `frontend/index.html` + `frontend/app.js` (Settings → Model tab: a "this model uses default / custom" toggle and a temperature slider that saves into `model_overrides[current_model]` instead of the global `temperature`)

**Change pattern:**
- New `DEFAULTS` entry: `"model_overrides": {}` (empty dict, merges cleanly).
- Helper in `config.py`: `def effective(c: dict, key: str, model: str)` returning the override if present, else the global. Initial scope: `temperature` only. Forward-compatible if we later want to override `system_prompt` or `auto_search_enabled` per model.
- In `server.py:chat()`, replace `c["temperature"]` with `cfg.effective(c, "temperature", c["ollama_model"])`.
- Frontend: when the user changes the model dropdown in Settings, show "uses global (0.7)" or "custom: 0.4" next to the model name. A pencil icon next to the temperature slider toggles between "this model only" and "global".

**Acceptance criteria:**
- Set `gemma3n:e4b` to `temperature: 0.4`, leave `gemma3:12b` on global `0.7`. Verify via A1's log (the request to Ollama uses the model-specific temperature).
- The current behavior — a single global `temperature` — is preserved when `model_overrides` is empty.

**Risks / open questions:**
- UI complexity in the Settings → Model tab. Keep it minimal — one extra row, not a full nested section. If it's getting bigger than that, architect approval needed.

**Complexity:** M (config + server + frontend).

---

### D2. Small-model preset

**Goal:** A one-click "I'm running a small model" preset that sets stricter defaults known to help small-model behavior.

**Affected files:** `backend/config.py`, `frontend/index.html`, `frontend/app.js`.

**Change pattern:**
- A "Small model mode" checkbox in Settings → Model. When checked, `model_overrides[current_model]` gets populated with:
  - `temperature: 0.4`
  - *(future)* `strict_anti_hallucination: true` — a flag the prompt assembler reads to include extra-strict variants of B2 rules. Initially no-op; reserved.
- When unchecked, the override entry is removed for that model.

**Acceptance criteria:**
- Toggle the checkbox while `gemma3n:e4b` is selected. Inspect `~/.config/logos/config.json`: `model_overrides["gemma3n:e4b"]` exists with `temperature: 0.4`.
- Untoggle: the entry is removed.

**Risks / open questions:**
- Naming: "Small model mode" may confuse users. Alt: "Conservative mode". Architect picks before implementation.

**Complexity:** S (after D1 is in place).

---

### End of Phase D — STOP for architect review

---

## 7. Phase E — Test harness

Currently Logos has no automated tests. We don't need a full pytest setup; we need enough to detect regressions in prompt and source-quality behavior.

### E1. Snapshot regression suite

**Goal:** A simple script that replays a fixed list of test prompts against a live Ollama and writes outputs to disk, so before/after diffs are possible across prompt changes.

**Existing dataset:** `tests/regression/test_cases.json` is already in the repo and contains 100 cases across 15 categories (γνώσεις, κώδικας, αναζήτηση, μετάφραση, δημιουργικό, system, roleplay, URL, μαθηματικά, επιστήμη, φιλοσοφία, debug, αφήγηση, ιστορία, tools). Each case has fields: `name`, `prompt`, optional `expect_search`, `expect_url_fetch`, `expect_sources`, `min_sources`, `min_tokens`, `force_search`, `skip`. This **is** the canonical regression dataset for v1.3 — do not invent a new format.

**Affected files:**
- Existing: `tests/regression/test_cases.json` (read-only for the runner; extend with new cases per below).
- New: `tests/regression/run.py`, `tests/regression/README.md`.

**Change pattern — runner (`run.py`):**
A stdlib + `httpx` script (no pytest needed) that:
1. Reads `tests/regression/test_cases.json`.
2. For each case (skipping ones with `skip: true` when `--quick`):
   - Sends `{messages: [{role: "user", content: case.prompt}], force_search: case.force_search}` to `POST /api/chat` of a running Logos instance.
   - Collects the SSE stream into: (a) the assistant text, (b) a list of SSE event types observed, (c) the sources list if any, (d) elapsed wall-clock time.
3. For each case, computes a `pass/fail` per assertion:
   - `expect_search` → was a `searching` SSE event seen?
   - `expect_url_fetch` → was a `fetching_urls` event seen?
   - `expect_sources` → was a `sources` event with at least `min_sources` (default 1) seen?
   - `min_tokens` → response length passes a rough token heuristic (e.g. word count × 1.3 ≥ min_tokens; acceptable approximation).
   - **NEW** `expect_honest_uncertainty` → response contains one of a small allowlist of phrases ("δεν ξέρω", "δεν έχω πρόσβαση", "δεν μπορώ", "I don't know", "I don't have access", "not in my training") — see B-extension below.
   - **NEW** `expect_script_consistency` → response contains no characters outside `[ -~Ͱ-Ͽἀ-῿\s\p{P}\p{S}]` (Greek + Latin + whitespace + standard punctuation). Implement with a Python regex; failures list the leaked codepoints.
4. Writes `tests/regression/output/<model>/<case_name>.md` with the response, and `<case_name>.meta.json` with `{passed: bool, assertions: {...}, sources: [...], elapsed: float, sse_events: [...]}`.
5. Writes a `tests/regression/output/<model>/_summary.md` aggregating pass/fail counts per category.

CLI flags:
- `--model <name>` — sets `ollama_model` via `POST /api/config` before running. Reverts at end.
- `--quick` — skips cases with `skip: true`.
- `--category <name>` — runs only cases whose `name` starts with `"<category> ·"`.
- `--host http://localhost:17842` — defaults to the local Logos port.

**Change pattern — dataset extensions:**
Add these cases to `test_cases.json` to close the coverage gaps identified vs the v1.2 test sessions:

1. **`script consistency`** category (3–4 cases) — Greek prompts that should produce only Greek/Latin output. Each with `expect_script_consistency: true`. Examples:
   - `script · technical explanation` — "Εξήγησε σε ένα παράγραφο πώς λειτουργεί το HTTPS handshake στα ελληνικά."
   - `script · creative` — "Γράψε ένα διήγημα 200 λέξεων για έναν αστρονόμο που χάνει την όρασή του."
   - `script · long context` — multi-turn conversation in Greek (3+ user turns).

2. **`αναζήτηση · thin sources`** subcategory (2 cases) — known to return homepages with no article body:
   - `αναζήτηση · ειδήσεις Ελλάδας σήμερα` (the prompt from log 1 §0) — `expect_search: true`, `expect_sources: true`, `force_search: true`, `expect_honest_uncertainty: true` (model should refuse to enumerate fabricated news).
   - `αναζήτηση · cybersecurity incidents σήμερα` — similar pattern.

3. **`system · honest uncertainty`** — explicitly tag `system · tokens` with `expect_honest_uncertainty: true` (the correct answer is "I don't have access to that count"). Same for any future cases where fabrication would be tempting.

4. **`URL · Hollow Knight`** (un-skipped) — the YouTube case from log 2, kept as a B4 framing regression test. `expect_url_fetch: true`, `min_sources: 1`.

These additions go into the existing `cases` array; preserve all current cases and only add. No schema break — `expect_honest_uncertainty` and `expect_script_consistency` are new optional fields, ignored by older runners.

**Acceptance criteria:**
- `python tests/regression/run.py --model gemma3:12b --quick` produces a populated `output/gemma3:12b/` directory with a `_summary.md`.
- `--category αναζήτηση` runs only the search cases.
- A failing `expect_script_consistency` case lists the foreign codepoints in its meta.json (e.g. `"failures": {"script_consistency": ["U+4E2D", "U+8001"]}`).
- The script does NOT require pytest, the Ollama client, or any new dep beyond stdlib + `httpx` (already in requirements).

**Risks / open questions:**
- "Snapshot tests" with LLM outputs aren't truly snapshot tests — outputs vary across runs. The intent is "human-eyes regression with programmatic floors": diffs are inspected, but the boolean assertions are reliable enough to catch regressions automatically. Document this in the README.
- `expect_honest_uncertainty` allowlist of phrases is brittle — a model might phrase "I don't know" in a way that bypasses the check. Initial allowlist is best-effort; tune from real outputs.
- The runner mutates the Logos config (model, temperature) during a run. Use a try/finally so the user's saved config is restored even on crash.

**Complexity:** M (runner). Dataset extensions are S.

---

### E2. "Replay last chat with different prompt" dev tool

**Goal:** A dev-only endpoint or CLI flag that takes an existing chat ID and re-runs its assistant turns with the current `MAIN_SYSTEM_PROMPT`, writing outputs for comparison.

**Affected files:** `backend/server.py` or a new `tools/replay.py`.

**Architect's preference:** Make it a CLI script under `tools/`, not a server endpoint — avoids exposing a powerful endpoint by accident, and avoids changing the API.

**Change pattern:**
- `tools/replay.py <chat_id>`:
  1. Read the chat JSON from `~/.local/share/logos/chats/<id>.json`.
  2. For each user turn, send the conversation up to that point to `/api/chat`, capture the assistant reply.
  3. Output a markdown file with original assistant reply vs. new assistant reply, side by side or one after another.

**Acceptance criteria:**
- Replay the two chat IDs from this roadmap's §0. Output files show side-by-side comparison.

**Risks / open questions:**
- Cost: replaying a 20-turn chat means 20 model calls. Document that this is an opt-in dev tool.

**Complexity:** M.

---

## 8. Out of scope (explicit non-goals for v1.3)

For clarity: these have been considered and deliberately deferred.

- **Vector-store memory or RAG.** Already declined in `developers.md §13`. Not revisited here.
- **Function-calling / agentic tool use.** Same.
- **Algorithm/math correctness in model outputs.** A model-level limitation; no prompt fix is reliable for this. The Dijkstra walkthroughs in the test logs are imperfect because small models lose track of state. We are not addressing this.
- **Code generation correctness.** Same reasoning. Better prompts marginally help; we don't pursue it as a milestone because we cannot reliably verify wins.
- **OCR for scanned PDFs.** Noted as a known caveat in `developers.md §14`. Not in this roadmap.

---

## 9. Glossary

- **`prompts.py`** — Central registry of LLM-facing strings. P1 rule: all prompt text lives here.
- **Assembled system prompt** — The final string `compose_system_prompt()` returns and `ollama_client.stream_chat` sends as the system role.
- **Source** — A dict in the form `{title, url, content, category?, quality?}` (latter two added in this roadmap), produced by `search_providers`, `url_fetcher`, or `open_notebook_client`, and shown in the assistant's footer as `[N]` references.
- **Thin source / quality: thin** — A source that came back from its fetcher but contains little or no article body text (e.g. a homepage scrape).
- **Auto-upgrade list** — The collection of historical `MAIN_SYSTEM_PROMPT` values that a v1.3 install will recognize and silently upgrade to the current default. Maintained in `config.py` (B0).

---

## 10. Implementer checklist (use this as your TODO)

Copy this list into a notes file. Tick as you go.

- [ ] Read this document.
- [ ] Read `developers.md`.
- [ ] **Phase A:** A1
- [ ] STOP — confirm A1 works by inspecting the log file after one chat.
- [ ] **Phase B:** B0 → B1 → B2 → B3 → B4
- [ ] STOP — re-run §0 test sessions, report to architect.
- [ ] **Phase C:** C1 → C2
- [ ] STOP — verify quality tagging behavior, report to architect.
- [ ] **Phase D:** D1 → D2
- [ ] STOP — verify per-model behavior, report to architect.
- [ ] **Phase E:** E1 → E2
- [ ] Final review — full re-run of §0 test sessions against latest builds, summary report.
- [ ] Bump `VERSION` in `backend/version.py` to `1.3.0`.
- [ ] Update `developers.md` to reflect any changes (P1, P2, P4).
- [ ] Update `README.md` user-facing notes only if a UI-visible change was made (D1, D2).

---

*End of roadmap.*
