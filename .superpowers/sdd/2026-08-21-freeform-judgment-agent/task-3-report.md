# Task 3 report — canonical free-form Gold and evidence-based embedding decision

Status: IMPLEMENTED; LOOP 1 QUALITY NO-GO; DENSE PILOT BLOCKED_EXTERNAL

Base: `016cd198e49993a3aad72eb3f7c4b1a9d156ced2`

## Delivered

- Canonical free-form Gold validation/building with order-independent IDs, connected evidence/filing group splits, strict source admission, and generated review state fixed to `agent_audited`.
- 126 cases: 18 per non-peer dimension across profitability/financial health, contract change, financing pressure, correction materiality, governance signal, business risk, and management discussion. The 42 paraphrase templates are fixed in configuration.
- Exact evidence-ID scoring for Recall@5/20 and MRR, slot completeness, wrong issuer/version hard failures, query/candidate counts, p50/p95, and content-free per-case residual diagnostics.
- Deterministic build/evaluation commands and artifacts, with semantic summary hashing that excludes only timestamps and latency measurement fields.
- Evidence-based embedding decision using the fixed Recall@20 `0.95` and minimum possible/measured gain `0.05` thresholds.
- Conditional dense contracts because the residual-text gate was eligible: metadata filtering before ranking, deterministic evidence-ID tie order, content hash preservation, unknown-evidence refusal, model/manifest refusal, and the 20,000-fragment cap.
- Conditional dense pilot manifest/summary recording `BLOCKED_EXTERNAL`; no embedding model, dependency, vector, or raw provider response was installed, downloaded, or generated.

## TDD evidence

1. Free-form RED: `python -m pytest tests/test_freeform_evaluation.py -q` stopped at collection with `ModuleNotFoundError: disclosure_db.freeform_evaluation`.
2. Core GREEN: the initial schema/determinism/leakage/split/exact-metric/gate suite passed 15 tests.
3. Integration RED/GREEN: source derivation and semantic-hash imports failed first, then passed against real in-memory SQLite behavior. A Windows temporary-root failure was traced to `tempfile.mkdtemp`; the fixture was changed to an injected in-memory database rather than changing production behavior.
4. Version-boundary RED/GREEN: a different current disclosure filing was initially mislabeled as a wrong version. The regression proved that wrong version means correction-policy/lineage mismatch; another current filing remains eligible multi-filing evidence.
5. Residual-diagnostic RED/GREEN: the aggregate initially dropped the content-free root-cause hypothesis, then preserved it with case/route/target/selected IDs and the exclusion boundary.
6. Dense RED: `python -m pytest tests/test_dense_retrieval.py -q` stopped at collection with `ModuleNotFoundError: disclosure_db.dense_retrieval`.
7. Dense GREEN: 5 tests passed for metadata-first filtering, identity preservation, deterministic ties, manifest/model/unknown-evidence refusal, vector dimension checks, and pre-embedding 20,000 cap enforcement.

## Real build and evaluation

The exact brief commands were run twice against:

- base: `D:\mirae-asset-project\db\semantic-v1_129f5b0\disclosure_corpus_semantic_v1.sqlite`
- overlay: `D:\mirae-asset-project\db\agent\agent_overlay.sqlite`
- search index: `D:\mirae-asset-project\db\agent\agent_search.sqlite`

Both cycles produced:

- canonical Gold content SHA-256: `01290ef5833eb1a6b1838ca92abf4fad1f9b5a4ab7b92ec93ebf67e174d86f57`
- Gold JSONL file SHA-256: `ab098ae462baa65ed816c2b9c6abc9b61b26a79d848fc47786f0039f33367107`
- manifest file SHA-256: `d50918a47fe148e53dab8c4601a3c73bae5134c0f6d450cb0eabd66f9ecbff65`
- semantic summary SHA-256: `101d3f1f988fee7a199657e9186f50b9ca4b50bdf8c465c9c70e3ad621bb4e7c`

Gold, manifest, and semantic hashes matched exactly between cycles. The full summary file intentionally retains fresh measured timestamp and latency samples.

## Metrics and gates

| Measure | Result | Gate |
|---|---:|---:|
| Cases / targets | 126 / 126 | cases >= 120: PASS |
| Review status | 126 `agent_audited` | no generated `human_verified`: PASS |
| Recall@5 | 1/126 = 0.0079365079 | reported |
| Recall@20 | 1/126 = 0.0079365079 | >= 0.95: FAIL |
| MRR | 0.0039682540 | reported |
| Slot completeness | 0.6666666667 | reported |
| Wrong issuer | 0 | must be 0: PASS |
| Wrong version | 0 | must be 0: PASS |
| Hard failures | 0 | must be 0: PASS |
| Expanded queries / candidates | 261 / 1,827 | reported |
| Sparse p50 / p95 | 1805.92ms / 3646.57ms | p95 concern |

The 125 exact misses were not repaired by changing targets or thresholds:

- 71: `structured_target_not_returned_by_audited_route`
- 42: `target_absent_from_search_index`
- 12: `target_outside_expanded_top20`

The retrieval summary contains every failure's case ID, route, target IDs, selected IDs, exclusion boundary, and hypothesis. It contains no raw content.

## Embedding decision

Residual text targets are 54/126. The maximum possible Recall@20 gain is `0.4285714286`, above the fixed `0.05` pilot-eligibility threshold, so the pilot is eligible even though the overall sparse path is a quality NO-GO.

Official NAVER Cloud documentation identifies Embedding v2/`bge-m3` as the long-text embedding path and documents 1,024-float outputs:

- https://api.ncloud-docs.com/docs/clovastudio-embeddingv2
- https://api.ncloud-docs.com/docs/clovastudio-openaicompatibility

Provider access is unavailable in the execution process. The decision is therefore `BLOCKED_EXTERNAL`, not adopted or rejected on fake measurements. `dense_pilot_manifest.json` records model `bge-m3`, dimension 1024, cap 20,000, null cost, input hashes, and zero vector records. `dense_pilot_summary.json` records null dense recall/gain/safety/latency and `adopted=false`.

## Manifest and diagnostic hygiene

The free-form manifest, retrieval summary, embedding decision, dense manifest, and dense summary were scanned for prohibited content-bearing key names and against all canonical Gold question strings. Each scan returned zero forbidden tokens and zero raw Gold text matches. No answer, excerpt, provider secret, or raw response is present.

## Immutable artifact pre/post attestation

All values matched exactly before and after both real cycles and the conditional dense decision step.

| Artifact | Size | UTC mtime | SHA-256 |
|---|---:|---|---|
| base corpus | 38,773,280,768 | 2026-08-17T16:00:13.0000000Z | `b8fb3be8b90d0cb1d8bc2491bee575aee632d29cc9bade21070e7e7b51646563` |
| live overlay | 2,162,688 | 2026-08-20T16:05:41.5227673Z | `a4491f2072766fcc11db65bad8c592c78696aea87132f3e7420857924938cb55` |
| live search index | 274,505,728 | 2026-08-18T19:14:09.9088108Z | `e223a19fcbefd4757a39b71e2b73eed7c81d01f2b54d74ca82e761dac10a8793` |

## Verification

- Focused Task 3 plus consumed-interface suites: 77 passed, 4 subtests passed.
- Full pytest: 368 passed, 1 skipped, 19 subtests passed; 38 pre-existing FastAPI lifecycle deprecation warnings.
- `python -m compileall -q src tests scripts`: passed at host level. The sandbox run first failed only on ignored bytecode-cache write ACLs; no syntax error was reported.
- Canonical build/evaluation determinism: PASS across two exact cycles.
- Manifest/raw-content leak scan: PASS.
- D-drive size/UTC mtime/SHA-256 pre/post comparison: PASS.
- No subagents were used.

## Concerns

- Loop 1 cannot be called GO: Recall@20 is far below 0.95.
- Sparse p95 exceeds 2 seconds, although the 2-second requirement is formally a dense-adoption constraint in this task.
- The dense pilot has no measured gain, safety regression result, cost, or p95 because external execution is blocked; embeddings must not be adopted from this artifact.
- The 71 structured-route mismatches and 42 targets absent from the live search index indicate upstream Gold-to-serving admission/route alignment work. This report deliberately does not reopen immutable Task 1/2 implementation or change Gold targets to hide that gap.
