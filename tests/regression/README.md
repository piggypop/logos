# Logos Regression Test Suite

Runs test cases against a live Logos instance and writes snapshot outputs
for before/after comparison across prompt changes.

## Usage

```bash
# Run all non-skipped cases with gemma3:12b
python tests/regression/run.py --model gemma3:12b --quick

# Run only search cases
python tests/regression/run.py --category αναζήτηση

# Custom host
python tests/regression/run.py --host http://localhost:17842
```

## Requirements

- A running Logos instance
- `httpx` (already in `backend/requirements.txt`)
- Python 3.10+

## Output

- `output/<model>/<case_name>.md` — snapshot of the model's response
- `output/<model>/<case_name>.meta.json` — assertions, sources, timing, SSE events
- `output/<model>/_summary.md` — pass/fail counts per category

## Caveats

- Outputs are **not** deterministic — LLM responses vary across runs. The
  `.md` files are for human diff inspection. Boolean assertions (expect_search,
  expect_sources, min_tokens, expect_script_consistency) are reliable enough
  to catch regressions automatically.
- `expect_honest_uncertainty` uses a phrase allowlist — a model might phrase
  "I don't know" in a way that bypasses it. Tune from real outputs.
- The runner temporarily changes the Logos model config and restores it on exit.
