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

## Task 10 — real D-drive build and acceptance loop

- Intent: exercise the serving vertical slice against the immutable attested D-drive corpus, keeping the existing `financial_overlay.sqlite` untouched.
- Preflight:
  - `$env:PYTHONPATH=(Resolve-Path 'src').Path; python -m unittest discover -s tests -v` → 166 passed, 2 optional skips.
  - `python -m compileall -q src scripts` → pass.
  - `git diff --check` → pass.
- Offline base attestation:
  - Command: `$p='D:\mirae-asset-project\db\semantic-v1_129f5b0\disclosure_corpus_semantic_v1.sqlite'; Get-FileHash -Algorithm SHA256 -LiteralPath $p` with a `Stopwatch` wrapper.
  - Result: SHA-256 `b8fb3be8b90d0cb1d8bc2491bee575aee632d29cc9bade21070e7e7b51646563`; size `38,773,280,768` bytes; elapsed `53.723 s`; before/after size and UTC mtime unchanged (`2026-08-17T16:00:13Z`).
- Overlay build:
  - Command: `python -m disclosure_db.cli build-agent-overlay --database 'D:\mirae-asset-project\db\semantic-v1_129f5b0\disclosure_corpus_semantic_v1.sqlite' --overlay 'D:\mirae-asset-project\db\agent\agent_overlay.sqlite' --financial-seed 'data\derived\financial_fact_gold_seed.jsonl' --predicate-config 'config\agent_gold_predicates.json' --attestation 'data\derived\database_distribution_manifest_semantic_v1.json' --report 'data\derived\agent_overlay_build.json'`.
  - Result: `quick_check=ok`, `imported=651` (`financial_imported=8`, `event_imported=643`), `rejected=1667`; source hash matches the attestation. `agent_overlay.sqlite` was promoted at 1,028,096 bytes. Existing `financial_overlay.sqlite` was not modified.
- Safe search build:
  - Command: `python -m disclosure_db.cli build-search-index --database 'D:\mirae-asset-project\db\semantic-v1_129f5b0\disclosure_corpus_semantic_v1.sqlite' --output 'D:\mirae-asset-project\db\agent\agent_search.sqlite' --attestation 'data\derived\database_distribution_manifest_semantic_v1.json' --report 'data\derived\agent_search_index_build.json'`.
  - First real-corpus attempt found the older corpus has no `filing.reporter_name`; a source-backed compatibility fallback now projects an empty reporter value while preserving the index schema. The second attempt completed with `quick_check=ok`, `indexed_rows=329323`, `trigram_rows=298128`, revision `safe-search-v1`, elapsed `228.079 s`, and atomic promotion to `D:\mirae-asset-project\db\agent\agent_search.sqlite`.
- Holdout and stage benchmark:
  - The current holdout CLI uses `--input` (the brief’s `--gold` flag is stale): `python scripts/build_agent_holdout.py --input 'data\derived\gold_qa.agent_audited.jsonl' --output 'data\derived\agent_holdout.jsonl'` → 83 rows, 238,402 bytes.
  - `python scripts/benchmark_agent_stages.py ... --output 'data\derived\agent_stage_metrics.json'` measured 8 samples per stage with provider-disabled reranking: planner p95 `9.22 ms`, structured fact lookup p95 `5176.81 ms`, local retrieval p95 `193.32 ms`, reranked retrieval p95 `0.03 ms`. The JSON artifact contains exactly these four finite measured fields; no latency was fabricated.
- Agent evaluation commands:
  - `python scripts/evaluate_agent.py ... --gold 'data\derived\gold_qa.agent_audited.jsonl' --output 'data\derived\agent_eval_vertical_slice.json' --stage-metrics 'data\derived\agent_stage_metrics.json'` → 31 records, `error_count=0`, `false_numeric_claim_count=0`, `unsafe_answer_count=0`, answerability agreement `0.580645`, numeric exactness `0.153846`, citation precision `0.077778`, citation recall `0.1875`, end-to-end p95 `5325.12 ms`, gate `false`.
  - `python scripts/evaluate_agent.py ... --gold 'data\derived\agent_holdout.jsonl' --output 'data\derived\agent_holdout_eval.json' --stage-metrics 'data\derived\agent_stage_metrics.json'` → 83 records, one malformed/error row, answerability agreement `0.865854`, numeric exactness `0.166667`, citation precision `0.045`, citation recall `0.2`, `false_numeric_claim_count=0`, `unsafe_answer_count=0`, end-to-end p95 `5064.28 ms`, gate `false`.
  - Generated holdout metadata is accepted only at evaluator scope (`split_*`, `holdout_*`, and `audit.source_question_id`); unknown fields in ordinary Gold records remain rejected.
- Retrieval evaluation:
  - `python scripts/evaluate_retrieval.py --database 'D:\mirae-asset-project\db\semantic-v1_129f5b0\disclosure_corpus_semantic_v1.sqlite' --gold 'data\derived\gold_qa.agent_audited.jsonl' --output 'data\derived\retrieval_vertical_slice.json' --limit 20` → 16 eligible questions, 17 targets, target Recall@20 `0.7647058824`, question-complete recall `0.75`, MRR `0.3449449856`; post-rerank metric `null` because no provider reranker was configured.
- Provider/API smoke:
  - `CLOVASTUDIO_API_KEY` was absent. Deterministic runtime smoke produced safe abstentions for out-of-scope and prompt-injection questions with no citations or numeric claims; no live provider request was made.
  - `fastapi` is not installed in this environment, so HTTP `/health`, `/v1/query/plan`, `/v1/evidence/search`, `/v1/financial-facts`, `/v1/event-facts`, `/v1/calculate`, and `/v1/answer` smoke is an optional dependency skip. The `serve --help` CLI surface is present.
- Artifacts: `data/derived/agent_overlay_build.json`, `data/derived/agent_search_index_build.json`, `data/derived/agent_stage_metrics.json`, `data/derived/agent_eval_vertical_slice.json`, `data/derived/agent_holdout.jsonl`, `data/derived/agent_holdout_eval.json`, `data/derived/retrieval_vertical_slice.json`.
- Residuals: acceptance quality gates remain false on measured answerability/citation/numeric quality, retrieval Recall@20 below target, fact-lookup latency above its threshold, one malformed holdout row, and absent provider post-rerank. Thresholds and Gold answers were not changed. PostgreSQL/OpenSearch/dense embeddings remain evaluation-triggered follow-up work.

## Task 10 fix round 1

- Scope: regenerate only stage metrics, holdout, agent evaluations, docs, and the ignored Task 10 report. The D-drive overlay/index were not rebuilt or modified.
- Benchmark correction:
  - The prior benchmark incorrectly timed `EvidenceService.search`, which combines structured lookup, retrieval, and reranking. `scripts/benchmark_agent_stages.py` now times `fetch_overlay_facts`/`fetch_event_facts` directly for the audited structured lookup boundary, `SafeSearchIndex.search` for local retrieval, and `ClovaReranker(api_key=None)` only for the deterministic local-order fallback.
  - Command: `python scripts/benchmark_agent_stages.py --database 'D:\mirae-asset-project\db\semantic-v1_129f5b0\disclosure_corpus_semantic_v1.sqlite' --overlay 'D:\mirae-asset-project\db\agent\agent_overlay.sqlite' --search-index 'D:\mirae-asset-project\db\agent\agent_search.sqlite' --attestation 'data\derived\database_distribution_manifest_semantic_v1.json' --gold 'data\derived\gold_qa.agent_audited.jsonl' --output 'data\derived\agent_stage_metrics.json' --limit 8`.
  - Result: 8 planner, 8 structured fact, 8 local retrieval, and 8 fallback samples; p95 planner `0.41 ms`, isolated fact lookup `172.61 ms`, local retrieval `156.53 ms`, fallback rerank `0.04 ms`. Artifact metadata sets `reranker_provider_configured=false`; evaluator treats provider latency/quality as missing rather than claiming `0.04 ms` provider performance.
- Holdout runtime fix:
  - RED: `python -m unittest tests.test_evidence_service.EvidenceServiceTests.test_query_plan_resolves_company_and_operation_without_model -v` reproduced `calendar.IllegalMonthError: bad month number 20`.
  - Root cause: `holdout_financial_numeric_5e02f8063179ddaf10c8` (source `q004_bio_july_current_contract_amount`) contains `2023년 2023-03-02`; the planner captured the first two digits after `년` as month `20`.
  - Fix: constrain planner month/day regex tokens to valid ranges and require a non-digit boundary, allowing the ISO date to parse as `instant_date=2023-03-02`.
  - GREEN: the same focused test passed; regenerated holdout evaluation now has `count=83`, `error_count=0`, and `answerability_denominator=83`. Gold answers and holdout expected values were not edited.
- Regenerated audited evaluation: `python scripts/evaluate_agent.py ... --gold 'data\derived\gold_qa.agent_audited.jsonl' --output 'data\derived\agent_eval_vertical_slice.json' --stage-metrics 'data\derived\agent_stage_metrics.json'`.
  - Measured: 31 records, errors `0`, answerability matches `18/31=0.580645`, numeric exactness `0.153846`, citation precision `0.077778`, citation recall `0.1875`, end-to-end p95 `5232.78 ms`, false numeric claims `0`, unsafe answers `0`.
  - Gate reasons, each against `config/evaluation_contract.json`: regression answerability matches `18 < 31` (fail); numeric exactness `0.153846 < 1.0` (fail); citation precision `0.077778 < 1.0` (fail); citation recall `0.1875 < 0.9` (fail); holdout answerability agreement `0.580645 < 0.9` (fail); integrated `retrieval_recall_at_20` missing (fail); integrated `post_rerank_recall_at_8` missing (fail); provider reranked latency missing because provider is unconfigured (fail). False numeric claims `0 <= 0` (pass), unsafe answers `0 <= 0` (pass), planner `0.41 <= 20` (pass), fact lookup `172.61 <= 200` (pass), local retrieval `156.53 <= 800` (pass), end-to-end `5232.78 <= 10000` (pass).
- Regenerated holdout evaluation: `python scripts/evaluate_agent.py ... --gold 'data\derived\agent_holdout.jsonl' --output 'data\derived\agent_holdout_eval.json' --stage-metrics 'data\derived\agent_stage_metrics.json'`.
  - Measured: 83 records, errors `0`, answerability matches `71/83=0.855422`, numeric exactness `0.153846`, citation precision `0.045`, citation recall `0.1875`, end-to-end p95 `5234.87 ms`, false numeric claims `0`, unsafe answers `0`.
  - Gate reasons: numeric exactness `0.153846 < 1.0` (fail); citation precision `0.045 < 1.0` (fail); citation recall `0.1875 < 0.9` (fail); holdout answerability agreement `0.855422 < 0.9` (fail); integrated retrieval Recall@20 missing (fail); integrated post-rerank Recall@8 missing (fail); provider reranked latency missing (fail). Error count, false numeric claims, unsafe answers, planner/fact/local/end-to-end latency pass their configured gates; regression answerability count `71 >= 31` passes.
- Standalone retrieval remains separate from agent-gate integration: `python scripts/evaluate_retrieval.py ... --limit 20` reports Recall@20 `0.7647058824` (below configured `1.0`), question-complete recall `0.75`, MRR `0.3449449856`, and post-rerank `null` with `reranker_not_configured`. The agent evaluator’s missing retrieval fields are not reported as zero and are independent of this standalone result.
- Documentation/report: README and this log now distinguish every failed gate, measured pass, missing integrated retrieval metric, standalone retrieval residual, and provider-disabled fallback. The ignored `.superpowers/sdd/2026-08-18-agent-serving-vertical-slice/task-10-report.md` contains the exact command/result ledger.

## Task 10 final review fix round 1

- Trusted identity: `data/derived/database_distribution_manifest_semantic_v1.json` now records the
  offline-verified D-drive `mtime_ns=1786982413000000000` alongside the unchanged SHA-256 and byte
  size. `load_distribution_attestation` reads only this trusted manifest value; it never captures the
  request file's current mtime. Missing mtime fails `verify_fast_identity`, and bool, fractional,
  negative, or non-integer values are rejected. Same-size replacement before a fresh load is covered
  by a regression test. Copied/re-extracted databases require offline re-attestation and dependent
  artifact regeneration.
- Runtime fail-closed boundary: `DisclosureAgent`/`EvidenceService` reject overlay or search-index
  configuration without an attestation; CLI `agent-query` and `serve` require `--attestation` for
  those options. API and serving event/fact wrappers return empty/unanswerable without attestation
  and never invoke the offline-only full-hash fallback. `overlay_matches_base(..., attestation=None)`
  remains available only to explicit offline/unit fixture callers.
- Citation serialization: populated `CitationRef` metadata, locator, and calculation fields are now
  asserted through `to_jsonable` and strict JSON round-trip serialization used by CLI/API envelopes.
- Focused verification: `python -m unittest tests.test_attestation tests.test_financial_overlay tests.test_agent_runtime tests.test_safety_contracts tests.test_search_index -q` → 90 passed, 2 optional skips.
- Full verification: `python -m unittest discover -s tests` → 175 passed, 2 optional skips; `python -m compileall -q src scripts` and `git diff --check` passed.
- Real D-drive smoke (no rebuild): with the D base, existing `agent_overlay.sqlite` and
  `agent_search.sqlite`, and the updated manifest, `_health_status` reported
  `base_attested=true`, `overlay_attested=true`, `search_index_ready=true`, `ready=true`. A live
  deterministic answer probe ran with `disclosure_db.financial_overlay.sha256_file` patched to
  raise; no hash was called. The probe safely returned `answerable=false` for its question.

## 2026-08-19T03:08:35+09:00 — Plan 1 Task 1: runtime configuration

- Intent: add an environment-backed runtime factory without exposing provider secrets.
- RED: `$env:PYTHONPATH='src'; .venv\Scripts\python.exe -m unittest tests.test_runtime_config -v` failed with the expected `ModuleNotFoundError: disclosure_db.runtime`.
- GREEN: created `src/disclosure_db/runtime.py` with required database/attestation environment validation, file validation, agent settings conversion, and boolean-only provider status.
- Verification: `$env:PYTHONPATH='src'; .venv\Scripts\python.exe -m unittest tests.test_runtime_config tests.test_attestation -v` → 13 passed; baseline remains 175 passed, 2 optional skips; compileall and `git diff --check` passed.
- External status: provider key not configured; no provider call was attempted.

## 2026-08-19T03:15:31+09:00 — Plan 1 Task 2: competition query adapter

- Intent: add the validated `POST /query` contract while keeping `/v1/*` routes intact.
- Dependency: installed the existing `.[agent]` extra with approval. The installed Starlette TestClient also required environment-only `httpx2`; it was installed without changing project dependencies or tracked files.
- RED: focused route tests first returned `404` for `/query`. After implementation, FastAPI 0.141.1 treated the nested request model as a query parameter; the exact response showed `loc=["query", "request"]`. The root cause was a deferred local annotation under `from __future__ import annotations`; binding the actual model before route registration fixed it.
- GREEN: `/query` now validates bounded request fields, fails closed with `503 runtime_not_ready`, calls the agent, maps citations to evidence with `receipt_no`, and emits the standard request/corpus/latency envelope.
- Verification: focused route suite → 3 passed; `tests.test_agent_runtime` → 24 passed. No provider call was attempted and no secret was logged.

## 2026-08-19T03:18:11+09:00 — Plan 1 Task 3: honest provider readiness

- Intent: report only whether HyperCLOVA X is configured, without exposing the API key.
- RED: `test_health_reports_provider_configuration_without_exposing_key` failed with `KeyError: 'provider_configured'`.
- GREEN: added `HyperClovaGenerator.configured`, `DisclosureAgent.provider_configured`, and boolean-only `/health` mapping.
- Verification: provider-status focused test → 1 passed; `tests.test_agent_runtime tests.test_reranker` → 41 passed. The test used a sentinel key only in memory; it was not written to logs or responses.
- External status: no rotated production credential is available, so live provider smoke remains blocked and deterministic fallback remains the only verified provider mode.

## 2026-08-19T03:20:09+09:00 — Plan 1 Task 4: environment-backed serve selection

- Intent: let `disclosure-agent serve` load all runtime paths from `DISCLOSURE_*` while preserving explicit CLI settings.
- RED: `tests.test_cli` failed to import the planned `_serving_settings` selector because it did not exist.
- GREEN: added all-or-none explicit path selection, runtime environment loading and validation, and host/port selection; `agent-query` behavior remains unchanged.
- Verification: `python -m unittest tests.test_cli tests.test_runtime_config -v` → 4 passed. No database files or provider credentials were accessed.

## 2026-08-19T03:22:18+09:00 — Plan 1 Task 5: container package

- Intent: package the server without copying the D-drive databases into the image and without committing secrets.
- RED/GREEN: static artifact tests initially failed with missing `compose.yaml`/`.dockerignore`; after adding the Dockerfile, Compose, ignore rules, and `.env.example`, `python -m unittest tests.test_deployment_artifacts -v` → 2 passed.
- Blocked verification: `docker compose config` could not run because the Docker CLI is not installed in this environment. No Docker build or container smoke result is claimed; the Task 5 Docker verification checkbox remains open.
- Hygiene: a temporary secret-free `.env` was created only for the attempted config check and removed immediately. No credential, DB, raw response, or runtime log was created or committed.
