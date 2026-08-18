# Development log — deterministic agent-audited Gold

이 문서는 append-only 실행 기록입니다. 시간은 KST와 UTC를 함께 적고, credential·API key·개인 경로의 비밀값은 기록하지 않습니다.

## Plan baseline — `b882f19`

- Design and execution plan committed before implementation: `b882f19 docs: plan deterministic agent gold audit`.

## 2026-08-18T16:40:55+09:00 / 2026-08-18T07:40:55Z — Task 5 closeout (current entry)

- Intent: baseline `agent_audited` Gold와 runtime 평가·감사 문서를 고정.
- Commands:

  ```powershell
  $env:PYTHONPATH='src'
  python scripts/evaluate_agent.py --database 'D:\mirae-asset-project\db\semantic-v1_129f5b0\disclosure_corpus_semantic_v1.sqlite' --overlay 'D:\mirae-asset-project\db\agent\financial_overlay.sqlite' --gold 'data/derived/gold_qa.agent_audited.jsonl' --output 'data/derived/agent_audited_eval.json'
  python -m unittest tests.test_agent_gold -v
  ```

- Result: evaluator `count=31`, `verified_count=31`, `answerability_match_count=11`, `error_count=0`, `latency_ms_p50=59.63`; focused suite 12 passed.
- Artifacts: `data/derived/gold_qa.agent_audited.jsonl`, `data/derived/gold_qa_agent_audit_summary.json`, `data/derived/gold_qa_agent_rejects.jsonl`, `data/derived/agent_audited_eval.json`, `docs/gold-set-audit.md`.
- Commit: pending this documentation commit.

## Task 1 — predicate allowlist

- Intent: prevent wildcard promotion of noisy generic facts.
- Command: `$env:PYTHONPATH='src'; python -m unittest tests.test_agent_gold -v`
- Result: configuration test passed after initial missing-loader failure.
- Artifacts: `config/agent_gold_predicates.json`, `scripts/build_agent_gold.py`, `tests/test_agent_gold.py`.
- Commit: `2a95b96 test: define safe agent gold predicates`.

## Contract correction before Task 2/3

- Finding: existing annotation schema had no `agent_audited` review state; using it would make generated records invalid.
- Change: added explicit schema enum/audit metadata and validator rule; approved model-generated records remain rejected.
- Commit: `7ce5ba3 docs: align plan with agent audited schema`, implemented in `a6d47bb feat: add strict agent gold audit gates`.

## Task 2 — read-only extraction

- Command: `$env:PYTHONPATH='src'; python -m unittest tests.test_agent_gold.AgentGoldTests.test_fact_candidate_extraction_keeps_evidence_filing_alignment -v`
- Result: fixture candidate preserved same-filing evidence and base SHA; full focused suite passed.
- Commit: `f925225 feat: extract read-only agent gold candidates`.

## Task 3 — gates and atomic writes

- Command: `$env:PYTHONPATH='src'; python -m unittest tests.test_agent_gold -v`
- Result: 12 focused tests passed, including missing/cross-filing evidence, unresolved lineage, PDF block, non-finite Decimal, duplicate IDs, and atomic JSONL replacement.
- Commits: `a6d47bb feat: add strict agent gold audit gates`, `135b278 perf: batch source metadata counts during gold audit`, `a1e05f3 perf: hash large corpus with larger blocks`, `38857e9 perf: avoid unnecessary source count rescans`.

## Task 4 — baseline generation

- External attestation command: `Get-FileHash -LiteralPath 'D:\mirae-asset-project\db\semantic-v1_129f5b0\disclosure_corpus_semantic_v1.sqlite' -Algorithm SHA256`.
- Result: `b8fb3be8b90d0cb1d8bc2491bee575aee632d29cc9bade21070e7e7b51646563`; file size `38,773,280,768` bytes.
- Generation command:

  ```powershell
  python scripts/build_agent_gold.py --database 'D:\mirae-asset-project\db\semantic-v1_129f5b0\disclosure_corpus_semantic_v1.sqlite' --gold 'data/derived/gold_qa.jsonl' --overlay-seed 'data/derived/financial_fact_gold_seed.jsonl' --output 'data/derived/gold_qa.agent_audited.jsonl' --summary 'data/derived/gold_qa_agent_audit_summary.json' --rejects 'data/derived/gold_qa_agent_rejects.jsonl' --precomputed-base-sha256 'b8fb3be8b90d0cb1d8bc2491bee575aee632d29cc9bade21070e7e7b51646563' --fact-limit 0
  ```

- Result: `candidate_count=31`, `audited_count=8`, `human_passthrough_count=23`, `rejected_count=0`, `output_count=31`, base mode `precomputed_external_attestation`.
- Commits: `cd9f989 feat: support bounded gold smoke runs`, `c4cb6fd feat: support baseline-only gold generation`, `25cb2af perf: keep overlay hydration read-only and bounded`, `6e0b683 feat: generate audited gold baseline artifacts`.
- Note: unlimited generic-fact extraction was attempted with the same gates but exceeded 30 minutes on the 38GB SQLite join; the process was stopped before atomic output. The unlimited command remains reproducible by omitting `--fact-limit`.

## Dependency and verification notes

- `python -m pip install -r requirements.txt` installed the pinned runtime dependencies required by `evaluate_agent.py` (`lxml`, `PyMuPDF`, `pdfplumber` and transitive packages).
- No source database write occurred; generator connections use `mode=ro` and generated files are sibling-atomic replacements.

## Task 6 — final verification before close

- Intent: verify the release boundary before pushing the tracking branch.
- Commands and results:
  - `$env:PYTHONPATH='src'; python -m unittest discover -s tests -v` → 49 tests ran, 48 passed, 1 skipped because the full external Gold/DB is not in this workspace.
  - `python -m compileall -q src scripts` → pass.
  - `git diff --check` → pass.
  - JSONL parse → 31 audited-output rows, 0 reject rows; summary status `ok`.
  - `Get-Item`/`Get-FileHash` on immutable base → 38,773,280,768 bytes and SHA-256 `b8fb3be8b90d0cb1d8bc2491bee575aee632d29cc9bade21070e7e7b51646563`.
- Release result: `agent_audited` baseline is regression-ready; `human_verified` promotion is still a human decision. Unlimited generic-fact extraction remains a separately reproducible long-running command.
- Pre-close HEAD: `a70553a docs: record agent gold audit and development process`.
- Closeout commit: `44856d5 docs: close agent gold audit`.
