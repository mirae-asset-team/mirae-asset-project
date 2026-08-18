# Task 1 report: authoritative agent evaluation

## Implementation summary

- Added `disclosure_db.agent_evaluation` with reusable `evaluation_pass`, `percentile`, and `evaluate_agent` interfaces.
- Evaluation pass status now requires verified answers, matching answerability, complete citation recall for answerable gold, and all applicable numeric/text checks; verified safe abstentions pass unanswerable gold.
- Moved per-record evaluation into the reusable evaluator, retained auditable error rows, and added pass count, answerability-match count, p50 latency, and p95 latency aggregates.
- Reduced `scripts/evaluate_agent.py` to CLI parsing, agent construction, metadata attachment, deterministic UTF-8 JSON output, and summary printing.

## Files changed

- `src/disclosure_db/agent_evaluation.py`
- `scripts/evaluate_agent.py`
- `tests/test_agent_evaluation.py`

## TDD evidence

RED command:

```text
$env:PYTHONPATH = (Resolve-Path 'src').Path; python -m unittest discover -s tests -p 'test_agent_evaluation.py' -v
```

Result: failed as intended with `ModuleNotFoundError: No module named 'disclosure_db.agent_evaluation'`.

GREEN command:

```text
$env:PYTHONPATH = (Resolve-Path 'src').Path; python -m unittest tests.test_agent_evaluation tests.test_agent_runtime -v
```

Result: `Ran 9 tests ... OK`.

## Full-suite result

```text
$env:PYTHONPATH = (Resolve-Path 'src').Path; python -m unittest discover -s tests -v
```

Result: `Ran 53 tests ... OK (skipped=1)`.

## Self-review

- `git diff --check` completed without whitespace errors.
- Confirmed `status="pass"` is assigned only from `evaluation_pass()` and answerability-match aggregation is independent of pass status.
- Confirmed CLI output is UTF-8, deterministic JSON formatting, and includes a trailing newline.

## Concerns

- The short-run p95 contract expects the observed upper-tail value (`4.0` for `[1, 2, 3, 4]`); the percentile helper preserves an observed value for quantiles at or above 0.95 while retaining linear interpolation for ordinary quantiles.
