# Development log — deterministic agent-audited Gold

이 문서는 append-only 실행 기록입니다. 시간은 KST와 UTC를 함께 적고, credential·API key·개인 경로의 비밀값은 기록하지 않습니다.

## 2026-08-20T12:43:54+09:00 / 2026-08-20T03:43:54Z — Insider-purchase query fail-closed regression

- Production reproduction: `최근 삼성바이오로직스 내부자매수 사례 찾아봐` returned an unrelated table row (`신설사업부문 최근 사업연도 매출액(원) | -`) as `verified=true`. The cited filing was corpus-owned, but the generic-text path did not require an audited event fact or semantic claim match.
- Root cause: the deterministic planner did not classify `내부자매수` as an event predicate. Retrieval therefore admitted a company-matching text row, the deterministic generator echoed the first row, and citation verification could not detect the question/evidence intent mismatch.
- RED/GREEN: two regression tests first failed with `fact_domain='text'` and no `validated_event_fact_required`; commit `917f15f` minimally routes the term to the audited event domain, where absent audited facts fail closed. The exact real-corpus local query then returned `verified=false`, `answerable=false`, no citations, and reason `validated_event_fact_required`.
- Fresh verification: `PYTHONPATH=src python -m pytest -q` passed `255`, skipped `1`, and passed `17` subtests; Node web suites passed `10/10`; `compileall` and `git diff --check` passed. Base, overlay, and search artifacts were opened read-only only.
- GitHub: the feature branch and existing PR `#1` were updated. Public NCP code-only redeployment remains `BLOCKED_EXTERNAL` because the authenticated console session expired; the live endpoint still serves the previous image until re-login and re-attestation/restart checks complete.

## 2026-08-20T10:28:59+09:00 / 2026-08-20T01:28:59Z — Financial-fact inventory and hybrid retrieval audit

- Intent: determine whether the four approved-Gold sparse misses require embeddings or are already covered by the structured agent path, without mutating the D-drive base, live overlay, live index, or NCP release.
- TDD implementation: added strict seed-to-Gold coverage checks for question/fact identity, filing, numeric value, scale, and exact evidence set; added an independent hybrid evaluator over the real deterministic planner and `EvidenceService`; added fail-closed CLI configuration requiring overlay plus attestation.
- Read-only result: sparse FTS+RRF Recall@20 remained `13/17 = 0.7647058824`, complete-question recall `0.75`, and MRR `0.3449449856`. Hybrid retrieval recovered `17/17`, complete-question recall `1.0`, with routes financial `8`, event `7`, text `2`, service errors `0`, and residual targets `0`.
- Financial scope: the checked-in validated seed has 8 facts and all 8 exactly match audited Gold. `corpus_wide_complete=false`; no generated candidate was promoted and no broader financial-fact completion is claimed.
- Decision: the four sparse misses were structured financial table cells, not residual text misses. Paid embeddings, pgvector, and OpenSearch are not justified by the current Gold retrieval gate and remain deferred. Provider execution and any production database/NCP change remain separately gated.
- Release bookkeeping: no dense-pilot manifest was created because residual text targets are zero. The release checklist records embeddings and PostgreSQL/pgvector/OpenSearch as evidence-based deferrals, not completed infrastructure, and corrects the stale public-UI NO-GO line using the already-recorded successful anonymous deployment/restart evidence.
- Independent review hardening: fatal base/overlay/search dependency reason codes now invalidate hybrid metrics instead of allowing `status=ok`; hybrid paths require overlay+attestation+search all together; canonical Gold validation, duplicate seed rejection, evidence filing checks, and trust-tier counts protect the 8/8 inventory; output/input path collisions are rejected before any write. Requested K `20` and effective safe-bundle K `8` are explicit. Focused tests grew to 14 and the full regression passed `253`, with one documented skip and 17 subtests.
- Artifacts: detailed outputs were written only to ignored `tmp/hybrid_retrieval_audit.json` and `tmp/financial_fact_coverage_audit.json`; D-drive artifacts were opened read-only and the deployed service was untouched.

## 2026-08-19T23:34:18+09:00 / 2026-08-19T14:34:18Z — NCP public deployment and restart verification

- Intent: finish the public contest-server execution plan on the approved NCP account while keeping the D-drive base, live overlay, and live search index read-only and without lowering any release gate.
- Provisioning: created the contest VPC/server path with 2 vCPU, 8GB memory, a public IP, and a 100GB ext4 data disk mounted persistently at `/srv/mirae`. Docker Engine 29.1.3 and Compose 2.40.3 were installed. ACG permits current-administrator `/32` TCP 22, public TCP 8000, and outbound TCP 443; the stale administrator `/32` was removed after deployment.
- Artifact transfer: compressed base transfer, zstd integrity, decompressed SHA-256/size/mtime attestation, overlay hash, search-index hash, and attestation hash all passed before promotion. Remote base, overlay, search, and attestation files are mode `0444`; container mounts remain read-only. The source files on D were read only.
- Systematic debugging: the first health calculation detected decompression mtime drift, so the manifest mtime was restored and the container restarted; readiness then became healthy without changing the attestation gate. The first q005 remote query found zero event facts even though overlay rows existed. A runtime probe proved `event_alias_count=0`: the Docker image omitted `config`, and the installed wheel resolved the repository-relative path outside `/app`. RED tests covered Docker config inclusion and explicit runtime config-directory resolution; the minimal fixes are commits `8886c10` and `3ad8dd8`.
- Local verification: focused deployment/alias suites passed; full `PYTHONPATH=src` regression ran `221 tests` with one documented optional skip; `compileall` and `git diff --check` passed.
- Internal NCP smoke: health passed; exact numeric returned two verified values/two citations; textual returned one citation; out-of-scope and injection cases returned no values or citations. Query latency was 1.66–1.71s. The same five cases passed after a container restart and again after a full host reboot.
- External smoke: from the Windows development network after host reboot and ACG cleanup, health was 90.82ms; exact numeric request `992c7539608d4a65aface324b0d375f9`, textual `1d4a14498e314e5ca905d510677d48dc`, out-of-scope `7092506341e74bd2bec1377691386e46`, and injection `8a080cd7045547b9ba525268c889bec0` all passed in 1.74–1.99s. No private URL or raw response was recorded.
- Restart evidence: `/srv/mirae` auto-mounted after reboot, all four immutable artifacts remained `0444`, the Compose container auto-started healthy, and internal/public representative queries passed.
- Release status: deterministic hard gates and the public endpoint are GO for team demo. Final submission remains NO-GO because no rotated HyperCLOVA X credential is available for the bounded provider schema smoke and 300-case provider pass; the old chat credential was not reused.

## 2026-08-19T12:16:11+09:00 / 2026-08-19T03:16:11Z — Docker runtime hardening and verification

- Intent: complete local Docker execution after WSL2/Docker Desktop became available without changing the immutable base DB or live read-only overlay/index.
- Findings/fixes: Docker Desktop D-drive bind mounts required SQLite `mode=ro&immutable=1` for serving reads; `/health` full search-index validation was moved to startup and cached; company candidates are prewarmed before readiness. Full integrity checks remain enabled.
- Verification: targeted serving suites `88 passed`; full suite `216 passed, 1 skipped, 17 subtests passed`; compileall exit `0`. Docker Compose config rendered successfully; image built and container became healthy. Docker `/health` returned HTTP 200, `ready=true`, attestation/search ready true, provider false, request `aa6a926a29b14ea38b3f19b4c54b436f`.
- Docker query status: answerable query smoke did not return within 120 seconds on the Windows Docker Desktop D-drive bind mount; recorded as `BLOCKED_LOCAL_RUNTIME`, not as a pass and not as a relaxed latency gate.
- Commits: `c804bfc fix: cache startup runtime validation for docker health`; `06841c1 fix: use immutable sqlite reads for serving`; `3e41882 perf: prewarm company candidates before serving`.
- External status: CLOVA/NCP credentials were found in the user-provided export, but no external API/login call was made without explicit permission to use those credentials. Public NCP endpoint and provider smoke remain blocked.

## 2026-08-19T12:25:00+09:00 / 2026-08-19T03:25:00Z — Approved external provider and NCP access attempt

- CLOVA: approved test credential was used process-scoped only. The endpoint returned HTTP 200 with the response body redacted. The adapter's text schema smoke failed closed because the model response was not exact JSON; the structured smoke returned a validated deterministic result with `hcx_fallback`, one citation, and numeric value `10`. No raw provider response or credential was recorded.
- NCP: the console was reached and the sub-account login mode was selected. NCP requires a separate login-page access key in addition to the sub-account ID/password; the supplied export marked that field as unset. No server, public IP, ACG, storage, or billing resource was created or changed.
- Release status: provider HTTP reachability is PASS, provider schema/quality pass remains incomplete, and NCP/public endpoint remains `BLOCKED_EXTERNAL`.

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

## 2026-08-19T03:32:18+09:00 — Plan 1 Task 6: Windows startup and smoke scripts

- Intent: add read-only D-drive validation, local/Docker startup selection, and bounded `/health` + `/query` smoke assertions.
- RED/GREEN: script contract initially failed on missing files; after implementation and the local command-path regression, `tests.test_deployment_artifacts` → 3 passed. The startup script now falls back to the workspace virtualenv Python module when `disclosure-agent` is not on PATH.
- Local smoke: server startup succeeded and `/health` plus `/query` returned HTTP 200. The known financial answerable fixture safely abstained with `answerable=false`, `verified=false`, no evidence, and reason codes including `validated_financial_fact_required`; therefore the answerable smoke assertion remains open pending Plan 2 correctness hardening. No unsafe answer was observed.
- Blocked verification: Docker CLI is unavailable, so Docker smoke was not run. The server process was stopped after diagnostics.

## 2026-08-19T03:34:14+09:00 — Plan 1 Task 7: serving runbook

- Intent: document exact local, Docker, NCP, stop/restart, diagnosis, and recoverable rollback flows.
- Added `docs/operations/contest-server.md` and linked it from README. The runbook states that base/overlay/index are read-only, secrets are process-scoped, and public submission readiness requires external checks.
- Sanitized status: local server start and health route worked; Docker CLI is unavailable; the answerable smoke fixture remains blocked by the known correctness issue; no provider credential or public NCP endpoint is available.

## 2026-08-19T03:36:26+09:00 — Plan 2 Task 1: financial date semantics

- Intent: distinguish accounting periods from filing/version cutoffs for exact-date financial questions.
- RED: the new IS regression returned `(period_start, period_end)=(None, None)` for `2024-12-31`, while the BS case retained its instant date.
- Root cause: the planner assigned an exact calendar date to `instant_date` before statement type detection.
- GREEN: preserved `parsed_calendar_date` and normalized IS/CIS/CF to `YYYY-01-01..YYYY-MM-DD`; BS remains instant. API `as_of` behavior is unchanged.
- Verification: `python -m unittest tests.test_evidence_service -v` → 14 passed. Gold/gate values were not changed.

## 2026-08-19T03:41:05+09:00 — Plan 2 Task 2: exact filing-date filters

- Intent: keep explicit filing dates separate from accounting periods and enforce them in both sparse-index and SSOT retrieval.
- RED: planner lacked `filing_date`; `SafeSearchIndex.search(..., filed_at=...)` and `query_database(..., filed_at=...)` had missing contract/signature failures.
- GREEN: added backward-compatible `QueryPlan.filing_date`, planner routing for non-financial exact dates, `filed_at` in the sparse projection/filter, and exact filing filters in both query paths. Updated the legacy event-date expectation to the new contract.
- Verification: focused and related suite `tests.test_evidence_service tests.test_search_index tests.test_pipeline` → 41 passed. Adjacent filing fixture returned only `2023-04-10`; no live DB was rebuilt or modified.

## 2026-08-19T03:42:53+09:00 — Plan 2 Task 3: claim-specific citations

- Intent: prevent deterministic answers from citing all retrieved candidates when only one claim supports the answer.
- RED: financial and text citation tests observed `['ev_selected', 'ev_extra']` and `['ev1', 'ev2']` instead of one citation.
- GREEN: added bundle-bounded known-ID selection: calculation uses only calculation evidence, structured facts use the first selected fact/event evidence, and text uses the first rendered evidence. Unknown structured evidence produces an abstention draft.
- Verification: `python -m unittest tests.test_agent_runtime tests.test_safety_contracts -q` → 47 passed, 1 optional skip. Existing calculation citation order remains stable in bundle order.

## 2026-08-19T03:50:00+09:00 — Plan 2 Task 4: row-aware numeric event cells

- Intent: resolve composite event facts only from one labelled numeric sibling cell, with explicit ambiguity and unit gates.
- RED: the composite fixture was rejected as `event_numeric_not_decimal`, and the two-number row had no row-ambiguity reject.
- GREEN: added strict full-cell decimal parsing, configured header/suffix/table-unit resolution, selected-cell-only evidence, and issued/treasury-share predicate aliases. Non-composite arbitrary text retains the prior `event_numeric_not_decimal` reject.
- Verification: targeted resolver tests → 2 passed; `python -m unittest tests.test_financial_overlay -v` → 20 passed. No live D-drive database was read for writing or rebuilt in this task.

## 2026-08-19T04:47:00+09:00 — Plan 2 Task 5: corrected derived database rebuild

- Intent: rebuild overlay/search candidates through staging, verify them against the immutable corpus, and promote only through recoverable renames.
- Base gate: manifest size `38,773,280,768`, mtime, and SHA-256 `b8fb3be8b90d0cb1d8bc2491bee575aee632d29cc9bade21070e7e7b51646563` matched. The handoff's `--distribution-attestation` validator option is absent in this checkout; the current validator plus distribution-attestation fast/hash checks were used without lowering gates.
- Validation: `integrity_check=ok`, foreign-key violations `0`, structure gate `true`, retrieval smoke gate `true`, semantic schema gate `true`. Counts: 4,204 filings, 4,622 sources, 8,437,771 fragments, 36,697,165 table cells.
- Candidate overlay: `imported=1,431` (`financial=8`, `event=1,423`), `rejected=2,498`, `quick_check=ok`, base SHA matched. Candidate SHA-256 `0da755d055e1148e45f611d996805ae9dcfc4124860a5458d645bbed56a22633`.
- Candidate search: `indexed_rows=329,323`, `trigram_rows=298,128`, `quick_check=ok`, revision `safe-search-v1`, candidate SHA-256 `e223a19fcbefd4757a39b71e2b73eed7c81d01f2b54d74ca82e761dac10a8793`.
- Representative checks: q001 `240993039040원`, q004 `495278073440원`, q009 `1940200주`, q010 `495472주`; each returned one selected numeric evidence cell. Live overlay/index were promoted after port 8000 was confirmed stopped. Previous files were preserved as `agent_overlay.20260819-044541.previous.sqlite` and `agent_search.20260819-044541.previous.sqlite`; no base file was modified.
- Verification: fresh full suite → `194 passed, 1 skipped`. Post-promotion live quick checks are `ok`, overlay matches base attestation, and live search metadata validates.

## 2026-08-19T04:53:00+09:00 — Plan 2 Task 6: corrected 114-case regression

- Intent: rerun the same audited/holdout evaluation after the correctness rebuild without changing any contract thresholds.
- Verification: full suite → `194 passed, 1 skipped`; holdout rebuild → `83` unique rows, `0` schema errors, SHA-256 `ee13e9ba0b576f370e0aaf8ac03f037e7abfa8601c84612e40df6465742bda08`.
- Stage benchmark: planner p95 `9.68ms`, fact lookup `28.87ms`, local retrieval `55.28ms`, rerank fallback `0.01ms`; `reranker_provider_configured=false`.
- Audited 31: pass `9`, verified `14`, answerability agreement `0.774194`, numeric exactness `0.615385`, citation precision `0.566667`, citation recall `0.5625`, end-to-end p95 `2190.06ms`.
- Holdout 83: pass `9`, verified `14`, answerability agreement `0.915663`, numeric exactness `0.615385`, citation precision `0.566667`, citation recall `0.5625`, end-to-end p95 `2066.84ms`.
- Retrieval: 16 eligible questions, target Recall@20 `0.7647058824`, question-complete recall `0.75`, MRR `0.3449449856`; post-rerank is not measured because provider is not configured.
- Hard safety results: audited and holdout both have `false_numeric_claim_count=0`, `unsafe_answer_count=0`, and `error_count=0`. Quality gates remain false for answerability/numeric/citation/retrieval/provider metrics; these are recorded as remaining quality gaps, with no gate reduction. External provider/NCP validation remains blocked by absent credentials/resources.

## 2026-08-19T04:58:00+09:00 — Plan 3 Task 1: stress contract and canonical schema

- Intent: define the 300-case stress allocation, immutable hard gates, quality gates, and provenance-safe case schema.
- RED: `tests.test_agent_stress` initially failed with `ModuleNotFoundError: disclosure_db.stress_generation`.
- GREEN: added canonical JSON/hash helpers, strict oracle/category/trust/provenance validation, exact numeric/text/multi-numeric evidence checks, and abstention no-claim checks. Added `config/stress_evaluation_contract.json` with the exact 300-case allocation and zero-valued safety hard gates.
- Verification: `python -m unittest tests.test_agent_stress -v` → 4 passed.

## 2026-08-19T05:02:00+09:00 — Plan 3 Task 2: deterministic stress case generation

- Intent: generate the exact 300-case allocation from audited Gold with deterministic IDs, source hashes, group IDs, and safe abstention fallback for incomplete source evidence.
- RED: generator tests initially failed on the missing `build_stress_cases`/`split_groups` interfaces; the real Gold build then exposed answerable text records without evidence IDs.
- GREEN: incomplete answerable sources now become explicit abstention cases rather than unsafe numeric/text cases. Added deterministic case generation, group-disjoint holdout splitting, atomic JSONL output, and provenance manifest CLI.
- Verification: `tests.test_agent_stress` → 6 passed. Two builds from reversed-equivalent input produced identical output SHA-256 `5279beb78b0a2fb05c800e21b6820c976a28f4de2aab33e5d2b6eaf15c453507`; 300 unique cases, exact allocation, 31 groups, zero group leakage in the split test.
- Actual D-run: `D:\mirae-asset-project\runs\evaluation\agent_stress_300.jsonl` was generated with the same output SHA. Manifest `data/derived/agent_stress_300_manifest.json` records input SHA `04377097c8aa0f159b87c26c787d0d0bf84a1579cf5e07d6f94fd9cfc9e2d681`, generator commit `f185a606`, and no question/answer text.

## 2026-08-19T05:08:00+09:00 — Plan 3 Task 3: deterministic stress scoring

- Intent: score exact numeric/text/multi-numeric, abstention, metamorphic, citation, and safety outcomes without an LLM judge.
- RED: scorer tests initially failed because `disclosure_db.stress_evaluation` did not exist.
- GREEN: added `CaseScore`, oracle-specific deterministic scoring, metamorphic consistency checks, failure-cause precedence, and aggregate hard-gate counters. Unknown/cross-filing citations and false numeric claims remain failures.
- Verification: `python -m unittest tests.test_agent_stress -v` → 9 passed.

## 2026-08-19T05:18:00+09:00 — Plan 3 Task 4: resumable runner and fault isolation

- Intent: add hash/commit-bound checkpoints, deterministic per-case execution, hard-stop failure handling, and copied-fixture-only fault tests.
- RED: resume/fault tests initially failed because `run_fault_case` and `should_skip` were absent.
- GREEN: added atomic checkpoint JSON records keyed by case/input/git identity, attestation/index validation before execution, provider-disabled deterministic runner mode, and fault fixtures that never copy or mutate the 38GB corpus.
- Verification: `python -m unittest tests.test_agent_stress -v` → 11 passed; runner compiles successfully. No D-drive stress run was started before the runner implementation was committed.

## 2026-08-19T05:23:00+09:00 — Plan 3 Task 5: first hard-stop diagnosis

- Initial run `20260818T200347Z-5279beb78b0a` stopped after 1 case with `false_numeric_claim_count=1`; no unsafe answer, unknown/cross-filing citation, or evaluator error occurred.
- Root cause: adversarial/unanswerable stress records retained the original answerable question while declaring abstention. The agent correctly answered the original question, exposing an invalid stress mutation rather than a runtime safety failure.
- RED/GREEN: added a regression assertion for adversarial question mutation and changed negative mutations to deterministic out-of-scope/injection prompts. The safety gate remains unchanged.
- The second run exposed the same invalid shape when an explicitly unanswerable Gold record was assigned to another category. RED/GREEN: all source-unanswerable records now receive the same safe abstention prompt and abstention oracle. Stress tests remain green (`12 passed`).

## 2026-08-19T06:32:39+09:00 / 2026-08-18T21:32:39Z — Plan 3 Task 5: final deterministic stress release evidence

- Pre-run identity: commit `962216a35de8179e5c5319683470601a061c2c4c`; 300-case manifest SHA-256 `1b014f0bfca75c8db6f6306dcdab80fff4cafb80dfb3709bf1e9d3bb8d8dd2a0`; immutable base SHA-256 `b8fb3be8b90d0cb1d8bc2491bee575aee632d29cc9bade21070e7e7b51646563`, size `38,773,280,768`; live overlay SHA-256 `92ffe3ce1740153cfec457f5354685535a31cc5fe0d7722d61f1195f2ac1d3ad`; live search SHA-256 `e223a19fcbefd4757a39b71e2b73eed7c81d01f2b54d74ca82e761dac10a8793`; search revision `safe-search-v1`; provider configured `false`; local deterministic server mode; free D: space `135,568,035,840` bytes.
- Failure loop: the first post-fix run passed `292/300` with eight text mismatches, all the same structured-fact generation shape. TDD RED/GREEN fixed value-cell normalization and text-event value-only generation in commits `3832305` and `962216a`; the live overlay was rebuilt in staging, attested, integrity-checked, and promoted with the previous live overlay preserved as `agent_overlay.20260819-061500.previous.sqlite`. The immutable base and search index were not modified.
- Final run `20260818T211732Z-1b014f0bfca7`: `300/300` passed; `error_count=0`; `false_numeric_claim_count=0`; `unsafe_answer_count=0`; `unknown_citation_count=0`; `cross_filing_citation_count=0`; answerability agreement, numeric exactness, citation precision, and citation recall were all `1.0`; hard gate passed. No quality threshold was lowered.
- Verification: `pytest -q` → `213 passed, 1 skipped, 17 subtests passed`; `python -m compileall -q src scripts tests` passed. Plan 2 audited/holdout reruns used `agent_stage_metrics.json` so provider state remained false: audited 31 had pass `15`, answerability agreement `0.935484`, numeric exactness `0.923077`, citation precision `0.842105`, citation recall `0.96875`, and one known `q005` multi-numeric false-claim quality gap; holdout 83 had pass `15`, answerability agreement `0.975904` and the same numeric/citation gap. Retrieval remained Recall@20 `0.7647058824`, question-complete recall `0.75`, MRR `0.3449449856`, with post-rerank blocked by absent provider credentials.
- Runtime: Python `3.12.13`; pytest `9.1.1`; FastAPI `0.141.1`; Uvicorn `0.52.3`; httpx2 `2.12.0`. Secret hygiene found no tracked key material; `.env` and `.env.*` remain ignored. HyperCLOVA smoke/300-case provider pass and NCP deployment remain `BLOCKED_EXTERNAL` because no rotated credential, Docker, NCP server/public IP/ACG/storage, or external network is available.

## 2026-08-19T06:55:00+09:00 / 2026-08-18T21:55:00Z — Local completion of correction-aware multi-value gap

- Intent: complete the remaining local q005 multi-numeric quality gap without changing any hard or quality thresholds.
- RED/GREEN: added correction-aware `both` routing only for questions asking for values “각각”, selected one filing-version chain by `event_id`, hydrated both root and corrected evidence at the API cutoff, and returned all validated numeric event values/citations. A regression test keeps single “정정 후” queries on `corrected` and prevents unrelated same-company contracts from entering the answer.
- Commits: `548f66a fix: answer correction-aware multi-value events`; `e7c8e28 fix: narrow correction-aware multi-value routing`.
- Verification: q005 direct probe returned `240993039040` and `495278073440` with both Gold citations; q004 returned only `495278073440`. Full suite after the fix → `214 passed, 1 skipped, 17 subtests passed`; compileall passed.
- Plan 2 reruns after the fix: audited 31 pass `16`, numeric exactness `1.0`, citation recall `1.0`, `false_numeric_claim_count=0`, `unsafe_answer_count=0`, `error_count=0`; holdout 83 pass `16`, numeric exactness `1.0`, citation recall `1.0`, and the same zero hard-safety counts. Provider configured remains `false`.
- Final post-fix stress run `20260818T214805Z-1b014f0bfca7`: `300/300` pass, all four quality metrics `1.0`, all hard counters `0`, hard gate passed. Manifest and immutable base SHA remain unchanged.

## 2026-08-19T07:05:00+09:00 / 2026-08-18T22:05:00Z — Local API smoke completion

- `/health` via FastAPI TestClient returned HTTP 200 with `ready=true`, base/overlay attestation true, and search index ready; request ID `97de5985c7894355b2618abc38b5b01d`.
- Answerable `/query` q005 returned HTTP 200, `verified=true`, both numeric values and both citations; request ID `a76c16260e274c61ab9a66cbf7e91341`.
- Adversarial `/query` returned HTTP 200 with `answerable=false`, `verified=false`, and no citations; request ID `722d23591a0742b0ba95128830511c47`.
- These are local API evidence only. Public/NCP request IDs and second-network verification remain `BLOCKED_EXTERNAL`.
- 2026-08-19: Provider adapter hardening completed with TDD. Added safe extraction of exactly one balanced JSON object from prose-wrapped HCX content, preserved exact schema/type validation, and fail-closed behavior for multiple objects. Focused HCX tests: 5 passed; full regression: 218 passed, 1 skipped, 17 subtests passed; compileall passed. Windows local start/smoke script passed for answerable and abstention cases and the server was stopped cleanly. Live provider re-smoke remains blocked because the approved credential is not present in the current process; no credential was recorded.

## 2026-08-20 — Anonymous public web UI, Tasks 1-6

- Baseline: `PYTHONPATH=src python -m pytest -q` → `220 passed, 1 skipped, 17 subtests`; no D-drive database was opened for writing.
- Task 1 RED/GREEN: `tests/test_public_web.py` first returned HTTP `404` at `/`; the packaged HTML/CSS/JS shell, CSP, MIME and local-only sources then passed `2` route tests. Related server tests passed `35`; wheel inspection contained all three initial web assets. Commit `9fe4112`.
- Task 2 RED/GREEN: `node --test tests/web_history.test.mjs` first failed with `ERR_MODULE_NOT_FOUND`; immutable versioned history, 50-conversation/100-message bounds, search and deletion then passed `4/4`. Public route regressions passed `2/2`. Commit `772053b`.
- Task 3 RED/GREEN: `tests/web_api.test.mjs` first failed with `ERR_MODULE_NOT_FOUND`; safe same-origin query classification, verified/abstained rendering, evidence metadata, retry, health and responsive controls then passed `10` combined Node tests and `35` server contract tests. Commit `acb757d`.
- Task 4 RED/GREEN: `tests/test_public_limits.py` first failed with `ModuleNotFoundError`; the locked rolling-window limiter then passed `5` focused tests and `20` consecutive runs (`100` total passing test executions) without leaked active counts. Commit `c6c64f4`.
- Task 5 RED/GREEN: FastAPI tests first failed because `create_app` lacked injected public limits; after integration, target-path/IP/rate/concurrency/exception-release and deployment regressions passed `50/50`. A latent `/v1/answer` body-model registration error was reproduced as HTTP `422` and fixed using the existing explicit nested-model binding pattern. `docker compose --env-file .env.example config` rendered limits `120/4/8`; base, agent DB, and attestation mounts remained `read_only: true`. Commit `9db8b8e`.
- Task 6 RED/GREEN: the real PowerShell smoke initially returned success when `/` was absent; after HTML/CSP checks and explicit UTF-8 JSON, `tests/test_smoke_agent.py` passed `2/2`, including answerable, abstention and injection behavior. The real read-only D-drive runtime then passed all probes: health request `4f54b7714450401091f4b73544dab843` (`0.06ms`), verified answer request `7607b1f542b3464d86509becc61506fb` (`4835.49ms`), abstention request `92c202e38c3847dc97408bcf50657fea` (`4790.05ms`), and injection-abstention request `154b0b9584384f13b5069f7314d7e081` (`4779.8ms`). The local server was stopped after the smoke.
- Release state: anonymous no-login UI and local evidence are implemented. Final submission remains **NO-GO** until a rotated HyperCLOVA X credential passes provider smoke and the unchanged 300-case provider gate; NCP external verification remains a separate deployment gate.

## 2026-08-20 — Anonymous public web UI, Task 7 local release gates

- Scope gate: clean branch at `7ce4fb3`; no `.env`, credential, DB, or generated secret file was present. Node `24.14.0` no longer accepts `--experimental-default-type=module`, so the current equivalent `node --test tests/web_history.test.mjs tests/web_api.test.mjs` was used and passed `10/10`.
- Full regression: `PYTHONPATH=src python -m pytest -q` → `235 passed, 1 skipped, 17 subtests passed in 28.19s`; the only warnings were the already documented FastAPI `on_event` deprecations. `python -m compileall -q src scripts tests` passed.
- Package gate: local no-isolation wheel build succeeded without network dependency installation. Wheel `miraeasset_disclosure_db-0.2.0-py3-none-any.whl`, SHA-256 `78056aee8b9d800dae964ae4624848ec05622a0adb922ff15afbe50f2e2e58f2`, contained all five web assets.
- Docker gate: Docker Desktop was started, Engine `29.7.2`; image build succeeded with image ID `sha256:c0adf3adcc07dfb09e2e0fdb8f235b973c1036592c5cc262d3760b6ac1721cd9`. Container became healthy after about `170s`. Web/CSP and three real query probes passed with request IDs health `3029125d830a488ca62743bc4e0f71a3`, answerable `84799fc1e0304306a9ee230d68ace408`, abstention `d5838369907941b4b0bb520eaea6e0b6`, injection `104d496d9b014b328720d37fe174b9ee`. Windows Docker Desktop D-drive bind latency was `192174.44ms`, `205127.23ms`, and `207511.27ms`; this is recorded as an environment performance warning, not hidden or used as a provider metric.
- Read-only gate: Docker inspect reported `/data/base/disclosure.sqlite`, `/data/agent`, and `/data/attestation.json` with `rw=false`; only `/runtime` was writable. Before/after Docker values were identical: base `38,773,280,768` bytes, UTC mtime `2026-08-17T16:00:13Z`, SHA-256 `b8fb3be8b90d0cb1d8bc2491bee575aee632d29cc9bade21070e7e7b51646563`; overlay `1,507,328` bytes, `2026-08-18T21:14:16.8302350Z`, `92ffe3ce1740153cfec457f5354685535a31cc5fe0d7722d61f1195f2ac1d3ad`; search `274,505,728` bytes, `2026-08-18T19:14:09.9088108Z`, `e223a19fcbefd4757a39b71e2b73eed7c81d01f2b54d74ca82e761dac10a8793`.
- Actual-browser gate: desktop had no horizontal overflow, showed a verified answer with one evidence card and a collapsed response detail, then showed an evidence-free abstention. Sidebar search produced `2`, `0`, then restored `3` rows; three single-delete controls were present and browser history was preserved. At 360px, sidebar toggle, Escape close and focus return passed with no overflow. Literal `<img src=x onerror=alert(1)>` matched exact message text, had `0` `img` descendants, opened no dialog, produced no console error, and abstained. Screenshots are ignored runtime evidence under `tmp/browser-evidence/`.
- Fresh deterministic gate: run `20260819T163934Z-1b014f0bfca7`, manifest SHA-256 `1b014f0bfca75c8db6f6306dcdab80fff4cafb80dfb3709bf1e9d3bb8d8dd2a0`, provider configured `false`, `300/300` passed, all hard counters `0`, answerability/numeric/citation precision/citation recall all `1.0`, `hard_gate_passed=true`. Summary SHA-256 `667ab1e2ae380d60bdeeac163d4ee67327fdaadb6bb2f831d7b47b932cc03e97`; failure file remained empty.
- Release decision: local deterministic, package, Docker functionality and browser safety gates are GO. Final contest submission remains **NO-GO** until the unchanged rotated-provider schema smoke and provider 300-case pass complete; no gate was lowered.

## 2026-08-20 — Anonymous public web UI, Task 8 external diagnosis

- Fresh external isolation was performed before any state change. TCP 22 and TCP 8000 were reachable, `/health` returned HTTP 200 with ready/base/overlay/search attestation true and `provider_configured=false`, while `GET /` did not return HTTP 200. The public host therefore remains on the earlier API-only application rather than the locally verified anonymous UI release.
- A representative correction-aware q005 request returned HTTP 200 in about `2.09s`, `verified=true`, `answerable=true`, both expected contract amounts, and two evidence records. The existing deterministic API remains usable; this does not count as public UI verification or provider quality evidence.
- The NCP console redirected to login and no attached authenticated Chrome session was available. Per the plan, no cached password, previously displayed administrator password, PEM content, or chat-exposed provider credential was used. No server, ACG, public-IP, disk, container, or data state was changed.
- `BLOCKED_EXTERNAL`: remote artifact modes/hashes and Compose mounts were not freshly re-attested; the Task 7 source was not deployed; public root/browser/rate-limit/restart/reboot checks were not run. Anonymous public web UI and browser team demo remain **NO-GO**. Existing API-only demo remains **GO** based on the fresh reachability/query probe and prior deployment evidence.
- Unchanged final boundary: final contest submission remains **NO-GO** until both the rotated HyperCLOVA X schema/provider-300 gate and the technical proposal are complete. No deterministic, provider, citation, safety, lineage, or read-only gate was lowered.

## 2026-08-20 — Final provider gate and evaluation API compatibility

- Provider runner RED/GREEN: new tests first failed because provider gate and p95 helpers did not exist. `--provider-mode disabled|required` now makes the intended execution explicit; required mode enables HyperCLOVA X and exits before case execution when it is not configured. Checkpoints include mode/model identity, and summaries include provider-required/configured, p95, pass flag and reason fields without credentials, raw responses, or question text.
- A latent hard-gate mismatch was found while adding provider tests: aggregation emitted `error_count` but checked `evaluator_error_count`, which could let evaluator errors evade the hard-gate reason list. Both fields now report the same measured errors and `evaluator_error_count>0` fails the unchanged hard gate. Focused stress tests: `19 passed`; compileall passed. Commit `ed4f1d7`.
- Official API RED/GREEN: `GET /answer` first returned HTTP 404. It now accepts `question_id` and `question`, reuses the verified agent path, and returns exactly `question_id`, `question`, `retrieved_context`, `think_trace`, and `answer`. Context is bounded to verified citation metadata and the trace is a public processing-stage summary, not hidden chain-of-thought. The same IP/global limiter applies. Focused runtime/limit tests: `46 passed`; compileall passed. Commit `498f90b`.
- External boundary: no rotated provider credential is present, so live schema smoke and provider-required 300 were not run. The public NCP host still serves the prior API-only build, so `GET /answer` and the web UI are not yet public. These remain `BLOCKED_EXTERNAL`; no gate was lowered.

## 2026-08-20 — Technical proposal and submission API specification

- Extracted all pages of the official task PDF and mapped the three submission items to repository artifacts. The proposal explicitly separates deterministic 300/300, provider-required quality, and public deployment evidence; it records Recall@20 and current external gaps instead of presenting them as passes.
- Added a 280-line Korean source proposal and a reproducible ReportLab builder. The final PDF is 12 A4 pages with cover, requirement mapping, architecture diagram, data/version model, generation safety, web/API experience, measured evaluation, security, reproducibility, limitations, effects and submission checklist.
- PDF verification: reopened with pypdf, 12 pages, 9,835 extracted characters, required text present, no Unicode replacement characters, all pages rendered with Poppler and visually inspected. Header/footer ordering defects found on the first render were fixed before final output. The builder now forces invariant PDF metadata; two consecutive builds produced the same SHA-256 `b238a7d75a5604ea73207dc04c095e4d92ebf5d42750c98a233ba415f17cd0cc`. Commits `0cd5673` and `746d338`.
- Added a standalone evaluation API server specification covering exact `GET /answer` five-field schema, detailed `/query`, health, validation and request-limit errors, default limits, and external deployment approval conditions. The final endpoint URL is not invented: it remains `BLOCKED_EXTERNAL` until the authenticated NCP code-only redeploy passes all public/restart gates.

## 2026-08-20 — Final local submission verification

- Current implementation regression: Node web suites `10/10`; Python `243 passed, 1 skipped, 35 warnings, 17 subtests passed in 28.62s`; compileall passed. PDF regenerated from the committed proposal source and its 12-page contract revalidated.
- Fresh final deterministic run `20260819T174202Z-1b014f0bfca7` used the unchanged 300-case manifest SHA-256 `1b014f0bfca75c8db6f6306dcdab80fff4cafb80dfb3709bf1e9d3bb8d8dd2a0`. Result: `300/300`, evaluator error and all hard counters `0`, answerability/numeric/citation precision/citation recall all `1.0`, hard gate passed, end-to-end p95 `5193.46ms`, failure file `0` bytes. Summary SHA-256 `3ab5b0a5823112157a4be4219883bcdba46f4f859d47f5fc2c65b8a0d73230ce`.
- The final run explicitly used `provider-mode=disabled`; the new runner correctly reported `provider_gate_passed=false` with `provider_not_required`. This preserves the boundary between deterministic evidence and missing live HyperCLOVA X quality evidence.
- Base, live overlay and live search sizes/UTC mtimes remained `38,773,280,768 / 2026-08-17T16:00:13Z`, `1,507,328 / 2026-08-18T21:14:16Z`, and `274,505,728 / 2026-08-18T19:14:09Z`. The run wrote only ignored verification output under `tmp/`; no live artifact was modified.

## 2026-08-20T04:16:23+09:00 — Anonymous public web UI, Task 8 production release

- Authenticated deployment resumed after the user supplied the NCP login session and explicitly approved the one-time PEM upload. The generated administrator password was passed only through one-shot local named pipes and process environment, never printed, committed, or written to a credential file.
- Pre-deploy hard gate: `/srv/mirae` was mounted; base, overlay, search, and attestation were mode `0444` with sizes `38,773,280,768`, `1,507,328`, `274,505,728`, and `1,732`. Full remote SHA-256 values exactly matched `b8fb3be8b90d0cb1d8bc2491bee575aee632d29cc9bade21070e7e7b51646563`, `92ffe3ce1740153cfec457f5354685535a31cc5fe0d7722d61f1195f2ac1d3ad`, `e223a19fcbefd4757a39b71e2b73eed7c81d01f2b54d74ca82e761dac10a8793`, and `13b6de3984edd55dd8f9f93b6fcb41701944637e00dc3b6c77f523c9995be1ce`.
- Code-only release: exact commit `dcf44b8` was packaged with `git archive`; archive SHA-256 was `5e75b7b9e4e86c7d70e93761fb90a6f8f292c23df8835546dba53c006b182cdb`. Archive inspection found no `.env`, SQLite/database, PEM/key, runtime, run, or temporary files. The existing untracked `.env` was copied without display, Compose rendered successfully, and the new image became healthy as `sha256:53d5c5969df4f44338deb298320534bbffd952019568e948e40a8c5cfeb3e84f`. The prior app directory remains at the rollback location; staging files were removed.
- Read-only/runtime boundary after deployment: `/data/base/disclosure.sqlite`, `/data/agent`, and `/data/attestation.json` reported `rw=false`; only `/runtime` reported `rw=true`. No D-drive or NCP data artifact was copied, rebuilt, promoted, or opened for writing.
- Public API/UI gates: `/`, CSS, and JS returned HTTP 200 with CSP and no login. `GET /answer` returned exactly the five official fields and both q005 amounts. Detailed q005 returned `answerable=true`, `verified=true`, both amounts, and two evidence records; request `39ce48ff00184981a0d371d425ec5f29`. Future/out-of-scope and injection prompts abstained with zero evidence; requests `a5eea00ea41e4d1aa010dea42f3cfe06` and `02eea3f8ff814b8ba238dd4080d7e1ab`. An internal-only limiter probe returned `429` on request 121, leaving public client buckets untouched.
- Actual public browser gate: the deployed page showed new conversation, conversation search, sidebar history, ready/provider-offline status, and question controls. A real q005 submission rendered a verified answer with `240993039040`, `495278073440`, two filing evidence cards, and a saved sidebar conversation.
- Recovery gates: `docker restart` returned the new container healthy with the same read-only mounts; post-restart public q005 request `36b4782a214c49f98220b131b9eaacdf` returned both values and two evidence records. A host reboot produced an observed outage and automatic public recovery; `/srv/mirae` auto-mounted, all four files remained `0444`, the new image auto-started healthy, browser UI/history survived reload, and all four full SHA-256 values again matched.
- Final fresh verification: Python `243 passed, 1 skipped, 17 subtests passed`; Node web suites `10/10`; compileall and `git diff --check` passed. The only Python warnings were the documented FastAPI lifecycle deprecations plus a non-fatal sandbox cache-write warning.
- Release decision: anonymous deterministic public web/API deployment is **GO** and does not depend on the development PC. HyperCLOVA X remains `provider_configured=false`; the rotated-provider schema smoke and provider-required 300-case gate remain **BLOCKED_EXTERNAL**, so no final provider quality claim is made and no gate was lowered.

## 2026-08-20 — Public disclosure research UI release, Tasks 1-7

- User-visible findings: the previous anonymous shell reserved 16–21rem for history, repeated a purpose subtitle, presented an absent explanation provider as a warning, exposed locator keys without filing text, and returned the same generic abstention for incomplete cross-company financial counts. The deployed insider-purchase false positive was already traced to text-domain routing and fixed fail-closed before this release.
- Design decision: keep the FastAPI/vanilla-JavaScript architecture and all evidence/readiness/completeness gates. Use a 224px desktop rail, an 880px reading column, compact research cards, a green database-ready chip, and a separate service-information disclosure. Rejected alternatives were a new frontend framework, a public evidence-by-ID endpoint, a runtime font CDN, and partial financial-count estimates; each would add migration/supply-chain/exposure risk or weaken answer truthfulness without improving the contest evidence path.
- Corpus semantics: /health.company_count is the length of the cached, attested company-candidate list and returned 76 on the real read-only runtime. It means 76 distinct searchable companies, not complete comparable financial coverage. The query 영업이익 10억 넘는 기업은 몇 개야? remains unanswerable and receives corpus_wide_financial_coverage_required; no partial count is shown.
- Citation boundary: CitationRef.excerpt is hydrated only from an admitted EvidenceRef.text, normalized, bounded to 600 characters, and omitted when absent or when verification abstains. Provider output can nominate known evidence IDs only; it cannot provide or alter excerpts. The client inserts excerpt text with textContent, renders human-readable locators, and creates a DART link only for an exact 14-digit receipt number.
- Provider wording: readiness is independent of optional explanation generation. Unconfigured mode reads 검증형 기본 엔진 사용 · HyperCLOVA X 설명 미사용; configured mode reads HyperCLOVA X 설명 연결됨. No credential value was printed or committed. The contest key remains process/untracked-environment material for NCP deployment and provider gates only.
- Typography: unmodified font-kopubworld@1.0.3 WOFF2 assets and its Korean license are self-hosted. SHA-256 values: Dotum Bold 61A989AB282D34C898EF538911C07A4956370C624E10A082D44EF9CB32AF93FE; Batang Medium 3CE1A2BF7DEB9184C83292C9BCA9DE46A995F19A37F443A6705D02B68B353A79; Batang Bold C34427249D93EBDF7D0A7158E4AC4294958D8E9877A43A130DD09310F0A22BF6; license 411E7DE3F06D32AA0F1C9AB35C5760A7CBE9543EE5867EC491F9B696ED8E0816. The product title alone uses KoPubWorld Dotum; all other visible text inherits KoPubWorld Batang. No runtime CDN is used.
- TDD evidence:
  - company-count RED: two KeyError: company_count failures; GREEN: focused API/web suite 38 passed;
  - excerpt RED: missing dataclass field/attribute; compatibility investigation found nested asdict() eagerly materialized excerpt=None; GREEN: optional nested serialization preserved and 38 passed;
  - aggregate RED: planner reason missing and JS export absent; GREEN: planner 22 passed, 4 subtests and web API 7/7;
  - UI/font RED: missing structure, 404 font assets, and absent package declarations; GREEN: public/deployment tests 8/8;
  - research rendering RED: missing pure helpers; GREEN: final Node suites 13/13, public route tests 2/2, and JavaScript syntax check passed.
- Visual evidence: local browser inspection at 1440×900 measured a 224px rail, 848px composer, KoPubWorld Dotum title, KoPubWorld Batang body, and no removed subtitle. At 390×844 the rail was hidden behind the menu toggle, the composer became one column, and horizontal overflow was false.
- Final local gate: full pytest 260 passed, 1 skipped, 19 subtests passed in 11.14s; Node 13/13; compileall and git diff --check passed. Direct setuptools wheel build produced miraeasset_disclosure_db-0.2.0-py3-none-any.whl, size 8,500,964, SHA-256 E2CFABE2160F9DCCA43856952963CA5A1B439B728D4BE4F4CF680FAD3EB364CF, containing all three WOFF2 assets and fonts/LICENSE.md.
- Real read-only runtime smoke: /health returned ready with company_count=76 and provider_configured=false; a correction-aware contract query was verified with two evidence records and two non-empty excerpts; the corpus-wide operating-profit count abstained with the coverage reason; the insider-purchase query abstained with validated_event_fact_required. The local process was stopped after smoke; no D-drive database, overlay, or index was written.
- Focused commits: db0841b plan, a2d4ca1 company count, d0cab39 excerpts, 056131b aggregate explanation, e106c3b UI/fonts, aa91eae status/evidence rendering. NCP code-only deployment and provider-required evaluation remain separate external steps; no external hard gate is claimed by this local release.

## 2026-08-20T17:40:30+09:00 — Explicit mobile sidebar close and NCP code-only release

- User finding: the mobile history rail could be closed with Escape or the header toggle in code, but the open overlay did not expose a visible close control. TDD first added `sidebar-close` to the public-shell contract and observed the expected missing-element failure; the implementation added an accessible `대화 목록 닫기` button visible only at the mobile breakpoint and reused the existing `closeSidebar(true)` state/focus path.
- Verification before release: the focused public-shell test and JavaScript syntax check passed; full Python regression passed with `261 passed, 1 skipped, 36 warnings, 19 subtests passed`; Node web suites passed `13/13`; `git diff --check` passed. Feature commit `83b3c5f` was pushed to `agent/disclosure-db-foundation` and PR #1.
- ACG diagnosis: the administrative SSH rule still allowed only the previous client network, while public port 8000 and the service remained healthy. After explicit action-time approval, one current-client `/32` TCP/22 rule was added without deleting the previous SSH rule or changing the public API rule. The client address itself is intentionally not recorded in Git.
- Code-only artifact: exact commit `83b3c5f` was archived from `Dockerfile`, `compose.yaml`, `pyproject.toml`, `config`, and `src` only. Archive SHA-256 was `302d171e5287a0a63bdba9302ff5884b936dd6ce664a8bcdab755ff0b6e7db0a`; inspection found no `.env`, key/PEM, database, runtime, or data artifact. The deployed image is `sha256:4d2185d76a1cdc432778291db051a752bfe71ed6664f76269e48583b69db78d5`; rollback is preserved at `/srv/mirae/releases/app-before-83b3c5f`.
- Runtime hard gates: `.env` remained mode `0600`; base DB, live overlay, live search, and attestation remained mode `0444`; container mounts for `/data/base/disclosure.sqlite`, `/data/agent`, and `/data/attestation.json` remained `RW=false`, with only `/runtime` writable. Post-deploy full SHA-256 values exactly remained `b8fb3be8b90dcb1d8bc2491bee575aee632d29cc9bade21070e7e7b51646563`, `92ffe3ce1740153cfec457f5354685535a31cc5fe0d7722d61f1195f2ac1d3ad`, `e223a19fcbefd4757a39b71e2b73eed7c81d01f2b54d74ca82e761dac10a8793`, and `13b6de3984edd55dd8f9f93b6fcb41701944637e00dc3b6c77f523c9995be1ce`.
- Public validation: `/health` returned ready with `company_count=76` and `provider_configured=true`; mobile 390x844 browser automation opened the rail, observed the close control, closed it, and confirmed `aria-expanded=false`. Correction-aware q005 request `988c1aa1393e47b08275c2129da60854` remained verified with both expected values and two evidence records. Corpus-wide operating-profit request `1570a268a72a4699beaa80672e3db276` abstained with zero evidence and `corpus_wide_financial_coverage_required`. Container restart recovered healthy and retained the close control and all read-only mounts; recovery health request was `7075c19420274b6f806b401f7162d91c`.
- Provider boundary: after trimming an accidental trailing clipboard newline from the untracked contest credential, a direct HCX-005 compatibility smoke returned HTTP 200 and `/health` reports the provider configured. The verified q005 route still recorded `hcx_fallback`, so the strict application output-schema path is not yet provider-passed. Per the user's request to finish the short release and continue later, the provider-required 300-case hard gate remains **PENDING/NO-GO**; no provider quality claim or lowered gate is made.

## 2026-08-20 — GitHub CI dependency correction

- The first post-release GitHub Actions run failed before exercising the application because the workflow installed only the base package and invoked `unittest`, while the committed public API suites require the optional FastAPI dependencies and two test modules import pytest. This was a CI-environment mismatch, not a production outage or a relaxation of any test gate.
- TDD first added a workflow contract test and observed it fail against the old install/test commands. The workflow now installs `.[agent]` plus pytest and runs the same pytest suite used by local release verification. Focused deployment-artifact tests passed `7/7`; the fresh full Python suite passed `262 passed, 1 skipped, 36 warnings, 19 subtests`; Node web suites passed `13/13`; `git diff --check` passed. No server redeployment is required because this correction changes CI and its regression contract only.
