#!/usr/bin/env python3
"""Logos regression test runner.

Replays test cases from test_cases.json against a running Logos instance,
collects SSE output, and writes snapshot + metadata files.

Usage:
    python tests/regression/run.py --model gemma3:12b --quick
    python tests/regression/run.py --category αναζήτηση
    python tests/regression/run.py --host http://localhost:17842

Requirements: stdlib + httpx (already in requirements.txt). No pytest needed.
"""

import argparse
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx

# ── Paths ──────────────────────────────────────────────────
REPO_ROOT = Path(__file__).resolve().parent.parent.parent
CASES_PATH = Path(__file__).resolve().parent / "test_cases.json"
OUTPUT_DIR = Path(__file__).resolve().parent / "output"

# ── Honest-uncertainty allowlist ───────────────────────────
UNCERTAINTY_PHRASES = [
    "δεν ξέρω",
    "δεν γνωρίζω",
    "δεν έχω πρόσβαση",
    "δεν μπορώ",
    "I don't know",
    "I don't have access",
    "I cannot",
    "not in my training",
    "δεν έχω πληροφορίες",
    "δεν είμαι σίγουρος",
    "I'm not sure",
    "I do not know",
    "no tengo acceso",
]

# ── Script-consistency regex: Greek + Latin + whitespace + punctuation ──
_SCRIPT_OK_RE = re.compile(
    r"^[ -~\u0370-\u03ff\U0001f000-\U0001ffff\U0001d000-\U0001f7ff\U0001f800-\U0001faff\U0001f000-\U0001f64f\U0001f650-\U0001f6c5\U0001f6c6-\U0001f6ff\U0001f300-\U0001f5ff\U0001f600-\U0001f64f\U0001f650-\U0001f6c5\U0001f6c6-\U0001f6ff\U0001f700-\U0001f7ff\U0001f800-\U0001f8ff\U0001f900-\U0001f9ff\U0001fa00-\U0001faff\U0001fb00-\U0001fbff\U0001fc00-\U0001fcff\U0001fd00-\U0001fdff\U0001fe00-\U0001feff\U0001ff00-\U0001ffff\U00010000-\U0001fffff\U00020000-\U0002ffff\U00030000-\U0003ffff\U00040000-\U0004ffff\U00050000-\U0005ffff\U00060000-\U0006ffff\U00070000-\U0007ffff\U00080000-\U0008ffff\U00090000-\U0009ffff\U000a0000-\U000affff\U000b0000-\U000bffff\U000c0000-\U000cffff\U000d0000-\U000dffff\U000e0000-\U000effff\U000f0000-\U000fffff\U00100000-\U0010ffff\s]+$"
)


def detect_language(text: str) -> str:
    """Same heuristic as server.py:_detect_language."""
    greek_chars = set("αβγδεζηθικλμνξοπρστυφχψωάέήίόύώΑΒΓΔΕΖΗΘΙΚΛΜΝΞΟΠΡΣΤΥΦΧΨΩΆΈΉΊΌΎΏ")
    if any(c in greek_chars for c in text):
        return "Greek"
    return "English"


def check_honest_uncertainty(text: str) -> bool:
    """Return True if the response contains an uncertainty phrase."""
    text_lower = text.lower()
    return any(phrase.lower() in text_lower for phrase in UNCERTAINTY_PHRASES)


def check_script_consistency(text: str) -> tuple[bool, list[str]]:
    """Return (passed, list of offending codepoints)."""
    offenders: list[str] = []
    for i, ch in enumerate(text):
        if ch in ("\n", "\r", "\t", " "):
            continue
        # Greek characters
        code = ord(ch)
        if 0x0370 <= code <= 0x03FF:  # Greek and Coptic
            continue
        # Basic Latin (ASCII), punctuation, whitespace
        if 0x0020 <= code <= 0x007E:
            continue
        offenders.append(f"U+{code:04X} '{ch}'")
    return len(offenders) == 0, offenders


def stream_chat(host: str, messages: list[dict], force_search: bool = False):
    """Send POST /api/chat and yield SSE event dicts. Returns elapsed time."""
    start = time.monotonic()
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
                    yield json.loads(line[6:])
                except json.JSONDecodeError:
                    pass
    elapsed = time.monotonic() - start
    return elapsed


def run_case(host: str, case: dict, model: str) -> dict:
    """Run one test case and return a result dict."""
    name = case["name"]
    prompt = case["prompt"]
    force_search = case.get("force_search", False)

    messages = [{"role": "user", "content": prompt}]
    assistant_text = ""
    sse_events: list[str] = []
    sources: list[dict] = []
    elapsed = 0.0

    try:
        gen = stream_chat(host, messages, force_search)
        for event in gen:
            etype = event.get("type", "")
            sse_events.append(etype)
            if etype == "token":
                assistant_text += event.get("content", "")
            elif etype == "sources":
                sources = event.get("sources", [])
            elif etype == "error":
                return {
                    "name": name,
                    "passed": False,
                    "error": event.get("message", "unknown error"),
                    "assertions": {},
                    "sources": sources,
                    "elapsed": 0,
                    "sse_events": sse_events,
                    "assistant_text": assistant_text,
                }
        # Get elapsed from generator return value
        try:
            elapsed = gen.send(None)  # triggers StopIteration with return value
        except StopIteration as e:
            elapsed = e.value if e.value else 0.0
    except Exception as e:
        return {
            "name": name,
            "passed": False,
            "error": str(e),
            "assertions": {},
            "sources": sources,
            "elapsed": elapsed,
            "sse_events": sse_events,
            "assistant_text": assistant_text,
        }

    # ── Assertions ─────────────────────────────────────────
    assertions: dict[str, bool | dict] = {}

    if case.get("expect_search"):
        assertions["expect_search"] = "searching" in sse_events

    if case.get("expect_url_fetch"):
        assertions["expect_url_fetch"] = "fetching_urls" in sse_events

    if case.get("expect_sources"):
        min_s = case.get("min_sources", 1)
        assertions["expect_sources"] = len(sources) >= min_s

    if case.get("min_tokens"):
        # Rough token heuristic: word count × 1.3
        words = len(assistant_text.split())
        est_tokens = words * 1.3
        assertions["min_tokens"] = est_tokens >= case["min_tokens"]

    if case.get("expect_honest_uncertainty"):
        assertions["expect_honest_uncertainty"] = check_honest_uncertainty(
            assistant_text
        )

    if case.get("expect_script_consistency"):
        ok, offenders = check_script_consistency(assistant_text)
        assertions["expect_script_consistency"] = {
            "passed": ok,
            "offenders": offenders[:20],  # cap at 20
        }

    all_passed = all(
        v if isinstance(v, bool) else v.get("passed", False)
        for v in assertions.values()
    )

    return {
        "name": name,
        "passed": all_passed,
        "assertions": assertions,
        "sources": sources,
        "elapsed": elapsed,
        "sse_events": sse_events,
        "assistant_text": assistant_text,
    }


def write_output(model: str, result: dict):
    """Write snapshot .md and .meta.json for one case."""
    safe_name = result["name"].replace(" · ", "_").replace(" ", "_")
    case_dir = OUTPUT_DIR / model
    case_dir.mkdir(parents=True, exist_ok=True)

    # Meta JSON
    meta = {
        "name": result["name"],
        "passed": result["passed"],
        "assertions": result["assertions"],
        "sources": result["sources"],
        "elapsed": result["elapsed"],
        "sse_events": result["sse_events"],
    }
    if "error" in result:
        meta["error"] = result["error"]
    with open(case_dir / f"{safe_name}.meta.json", "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)

    # Snapshot markdown
    md = f"# {result['name']}\n\n"
    md += f"**Model:** {model}\n"
    md += f"**Passed:** {'✅' if result['passed'] else '❌'}\n"
    md += f"**Elapsed:** {result['elapsed']:.1f}s\n\n"
    if "error" in result:
        md += f"**Error:** {result['error']}\n\n"
    md += "## Response\n\n"
    md += result.get("assistant_text", "(empty)")
    md += "\n"
    with open(case_dir / f"{safe_name}.md", "w", encoding="utf-8") as f:
        f.write(md)


def write_summary(model: str, results: list[dict]):
    """Write _summary.md aggregating pass/fail per category."""
    case_dir = OUTPUT_DIR / model
    case_dir.mkdir(parents=True, exist_ok=True)

    # Group by category (first segment of name before " · ")
    cats: dict[str, dict] = {}
    for r in results:
        cat = r["name"].split(" · ")[0] if " · " in r["name"] else "other"
        if cat not in cats:
            cats[cat] = {"total": 0, "passed": 0, "failed": 0}
        cats[cat]["total"] += 1
        if r["passed"]:
            cats[cat]["passed"] += 1
        else:
            cats[cat]["failed"] += 1

    total = sum(c["total"] for c in cats.values())
    passed = sum(c["passed"] for c in cats.values())

    md = f"# Regression Summary — {model}\n\n"
    md += f"**Date:** {datetime.now(timezone.utc).isoformat()}\n"
    md += f"**Overall:** {passed}/{total} passed\n\n"
    md += "| Category | Total | Passed | Failed |\n"
    md += "|----------|-------|--------|--------|\n"
    for cat in sorted(cats):
        c = cats[cat]
        md += f"| {cat} | {c['total']} | {c['passed']} | {c['failed']} |\n"

    with open(case_dir / "_summary.md", "w", encoding="utf-8") as f:
        f.write(md)


def main():
    parser = argparse.ArgumentParser(description="Logos regression test runner")
    parser.add_argument("--model", default="gemma3:12b", help="Ollama model name")
    parser.add_argument(
        "--quick", action="store_true", help="Skip cases with skip:true"
    )
    parser.add_argument("--category", help="Run only cases whose name starts with this")
    parser.add_argument("--host", default="http://localhost:17842", help="Logos host")
    args = parser.parse_args()

    # Load test cases
    with open(CASES_PATH, encoding="utf-8") as f:
        data = json.load(f)
    cases = data["cases"]

    # Filter
    if args.quick:
        cases = [c for c in cases if not c.get("skip")]
    if args.category:
        cases = [c for c in cases if c["name"].startswith(args.category)]

    print(f"Running {len(cases)} test cases on {args.model}...")

    # Set model via config API, save original for restore
    try:
        resp = httpx.get(f"{args.host}/api/config", timeout=5)
        resp.raise_for_status()
        original_config = resp.json()
    except Exception:
        print("WARNING: Could not read config — model restore may fail")
        original_config = {}

    original_model = original_config.get("ollama_model", "")
    try:
        httpx.post(
            f"{args.host}/api/config",
            json={"ollama_model": args.model},
            timeout=5,
        )
    except Exception as e:
        print(f"ERROR: Could not set model to {args.model}: {e}")
        sys.exit(1)

    results: list[dict] = []
    try:
        for i, case in enumerate(cases, 1):
            name = case["name"]
            print(f"  [{i}/{len(cases)}] {name} ... ", end="", flush=True)
            result = run_case(args.host, case, args.model)
            results.append(result)
            status = "✅" if result["passed"] else "❌"
            print(f"{status} ({result['elapsed']:.1f}s)")
            write_output(args.model, result)
    finally:
        # Restore original model
        if original_model:
            try:
                httpx.post(
                    f"{args.host}/api/config",
                    json={"ollama_model": original_model},
                    timeout=5,
                )
            except Exception:
                pass

    # Summary
    write_summary(args.model, results)
    passed = sum(1 for r in results if r["passed"])
    print(f"\nDone. {passed}/{len(results)} passed.")
    print(f"Output: {OUTPUT_DIR / args.model}")


if __name__ == "__main__":
    main()
