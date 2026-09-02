# Judge Stress V2 Implementation Plan

## Objective

Harden the public disclosure agent for human and automated contest judging while preserving deterministic financial accuracy, citation lineage, public anonymous access, and read-only corpus boundaries.

## Global constraints

- Start from `99893d4` on `agent/judge-stress-v2`.
- Keep the D-drive base database and live overlay/search index, and all NCP corpus mounts, read-only.
- Do not inspect, change, or commit credentials, `.env`, PEM files, or NCP security settings.
- Keep the five public Tools and existing `/query`, `/answer`, and `/v1/hcx/function-answer` fields backward compatible.
- Use HCX only after deterministic routing/retrieval/calculation; unsupported judgments return established facts plus explicit missing evidence and no conclusion.
- Apply TDD, systematic debugging, full regression verification, documentation, and one independent commit per task.

## Task 1 — Runtime and dependency baseline

- Keep NumPy out of the main `[agent]` image.
- Declare and lock NumPy for the Dense image only; record Python/NumPy/FAISS/model/vector identities in a non-secret runtime manifest and health response.
- Audit sparse fallback and align README/development/release documentation with measured status.

## Task 2 — Judge Stress V2

- [x] Preserve the legacy 300-case and 856-case regressions.
- [x] Add a deterministic tracked 480-case development builder and require an explicitly supplied git-ignored private 120-case holdout to validate the full 600-case suite; block instead of synthesizing when private input is absent.
- [x] Allocate: structured 120, alias/period/correction 90, free-form 120, multi-evidence judgment 90, policy/adversarial 90, API/concurrency 60, fault 30.
- [x] Produce JSON and HTML reports and classify failures as entity, period, account, routing, retrieval, evidence, calculation, generation, security, or runtime.
- [x] Enforce issuer, independent document group, independent question-template family, source group, and exact-question isolation between development and private holdout.
- [x] Before reporting, independently validate 600 unique manifest rows/IDs, `480/120`, exact total/per-split category allocation, row split/category consistency, and recomputed suite SHA-256.

Completed 2026-09-03 and corrected through review fix round 2. Suite SHA-256 `47664c91b6d241288ab4cd928955321eae301658de5681b06a40665054125a87`; tracked reports contain IDs/hashes/counts/reproducibility metadata but no exact authored private question/rubric text. Underlying audited public facts and issuer/source group hashes remain tracked; no cryptographic secrecy is claimed for public provenance. Raw development may remain ignored; no tracked 480-row raw artifact is required. Actual evaluation remains `NOT_RUN` for Tasks 3–7. See `.superpowers/sdd/2026-09-02-judge-stress-v2/task-2-report.md`.

## Task 3 — Input and routing hardening

- [x] Normalize NFKC, whitespace/zero-width characters, manifest aliases, English issuer names/tickers, and unique one-edit typos.
- [x] Decompose multiple issuers, periods, and metrics without silently collapsing them.
- [x] Set every public question field to a 2,000-character maximum and reject malformed or contradictory inputs.
- [x] Detect bounded encoded/multilingual prompt-injection surfaces without executing or forwarding decoded instructions.

Completed locally on 2026-09-03 from base `ab143611f000279ff8f8e2bd099dcdbbc7829272`. Focused input/routing/API/Function Calling/policy/agent regression passed `159` tests with `36` subtests; full Python passed `591` with `2` existing optional skips and `84` subtests; Web passed `12/12`. Public Tool count remains five and existing fields remain present. The 600-case Judge Stress V2 app evaluation, Docker/image, Dense quality, live/provider, NCP, and deployment checks were not run or claimed. See the git-ignored `.superpowers/sdd/2026-09-02-judge-stress-v2/task-3-report.md`.

## Task 4 — Bounded analysis executor

- [x] Connect `plan_analysis -> search_analysis` to the public Function Calling path without adding a sixth public Tool.
- [x] Support profitability, financial health, liquidity/cash flow, financing, CAPEX, business risk, governance, correction materiality, and peer comparison.
- [x] Bind evidence slots by issuer and period. Missing mandatory slots force `insufficient_evidence`.
- [x] Continue refusing transaction recommendations and forecasts while allowing bounded historical disclosure judgments.

Completed locally on 2026-09-03 from Task 3 base `3b11110e18f6b61c7c2f24d54c3c2099afff687a`. The public registry remains exactly five Tools; bounded analysis internally calls `EvidenceService.search_analysis` and reuses `build_summary_context` for the admitted response. Canonical issuer aliases and every explicit issuer/period pair receive distinct slots. Evidence that fails either ownership check is excluded, and any mandatory gap prevents HCX generation with `conclusion=insufficient_evidence`. Focused Task 3/4 regression passed `146` tests with `86` subtests; full Python passed `641` with `2` existing optional skips and `134` subtests; Web passed `12/12`. No provider, Docker, NCP, D-drive, live-data, credential, `.env`, or PEM operation was performed.

## Task 5 — Hybrid retrieval gate

- [x] Keep structured facts/events/corrections/aggregates on SQLite and never invoke Dense on those routes.
- [x] Apply issuer, `as_of`, correction-version, and filing-period filters before Dense; send only the resulting filing-ID allowlist and reject hydrated evidence whose actual filing is outside it.
- [x] Admit evidence through one instruction-like-text gate before any public model context, including tool search results and bounded summary context.
- [x] A Dense URL or self-asserted decision JSON alone does not enable serving. Strict `dense-adoption-v2` must match the Git-tracked evaluation summary's pinned file/semantic hashes, its recomputed ADOPTED decision and evaluated runtime identity, and the live sidecar `/health` identity. That identity includes the validated Dense build manifest, FAISS index, chunk metadata, and model-identity manifest hashes. Missing/unreachable/stale/mismatched/non-finite input keeps Sparse serving.
- [x] No current deployment artifact is declared adopted, so current Compose agents remain Sparse-first and start without waiting for Dense health.
- [x] Focused verification after review fixes: `237 passed, 1 skipped, 34 warnings, 31 subtests` across Dense client/runtime/retrieval, deployment artifacts, evidence service, disclosure tools, free-form evaluation/retrieval, runtime configuration/integration, hybrid retrieval, and agent runtime. The skip is the existing optional local smoke index; warnings are existing FastAPI `on_event` deprecations.

Independent review rejected the first Task 5 commit because its adoption artifact was self-asserted, top-level Sparse recall accepted non-finite values, Dense hit ownership was not paired to hydrated evidence, text-slot periods were omitted from prefilters, and Unicode/zero-width instruction text reached summary context. The fixup used RED regressions for each boundary. Dense evidence now requires a unique exact `hit filing_id == hydrated filing_id` mapping, text slots send both period bounds to Sparse and Dense prefilters, and the shared evidence gate applies NFKC plus control/format removal and compact-marker detection. Re-review also found that `.dockerignore` excluded the pinned summary copied by the agent Dockerfile; a RED deployment regression now permits only that tracked summary through the otherwise excluded `data` tree. Current tracked evaluation is not ADOPTED, so Dense remains disabled. Fresh final verification is recorded in the Task 5 fixup commit and development log; no live/provider/NCP evaluation is claimed.

## Task 6 — Claim-level verification

- [x] Represent conclusion, claims, calculations, citation IDs, evidence-slot ownership, and limitations explicitly.
- [x] Require every claim to cite admitted evidence and every number to match DB facts or Decimal calculations.
- [x] Fall back to deterministic facts or abstention when HCX output fails verification.
- [x] Return a bounded verification trace, never private chain-of-thought.

Completed locally on 2026-09-03 from reviewed Task 5 base
`39649d3dd696ba156700cf80aa65d4515d56bd08`. The private HCX final Tool now requires
typed claims and limitations while all existing public top-level response fields and exactly five
public Tools remain unchanged. Provider prose is rendered only after citation, slot, fact,
calculation, finite-Decimal, all-operand, policy, and bounded-conclusion verification. Any failure
discards the provider prose and uses admitted deterministic facts/calculations or abstains; generic
and bounded-judgment failures never reuse rejected prose. `verification_trace` contains only a fixed
schema version, outcome, counts, five check statuses, and stable failure codes—never prompts, raw
evidence, provider content, or private reasoning. Focused Task 6 verification passed `60` tests with
`6` subtests; full Python passed `678` tests with `2` existing optional skips and `146` subtests;
Web passed `12/12`; compile and diff checks passed. No external/provider/live evaluation is claimed.

## Task 7 — Automated judge and security evaluation

- [x] Cover Korean/English paraphrases, typos, JSON demands, prompt extraction, direct/indirect/encoded injection, SQL/XSS strings, nonexistent facts, corrections, wrong issuer/version/unit/scope, provider faults, Dense faults, and restart/concurrency behavior.
- [x] Run deterministic/policy/error cases without HCX and reserve provider eligibility for the hidden free-form/judgment holdout.

Completed locally on 2026-09-03 from Task 6 final review-approved HEAD `75ae106`. The harness keeps
the original 600 roots and defines 120 versioned provider observations from the 42 eligible hidden
roots (24 free-form and 18 multi-evidence); it does not invent private roots. Raw questions,
answers, provider bodies, prompts, decoded attack payloads, and credentials are absent from result,
failure, JSON, HTML, and checkpoint artifacts. The provider-free local run evaluated all 480
development roots with zero case/evaluator/security/concurrency/provider-call failures, but this is
only a `ContractJudgeRuntime` contract-harness result, explicitly marked
`runtime_release_eligible=false`, and not an application-accuracy result. Because the private holdout and staging provider were unavailable,
the tracked report is honestly `PARTIAL`, `BLOCKED_PRIVATE_HOLDOUT`, `BLOCKED_PROVIDER`, and
`hard_gate_passed=false`; it is not an application-quality or release PASS. Focused Task 7 tests
passed `42`; Task 7 plus legacy stress passed `61`; the full Python suite passed `706` with `2` existing optional skips
and `146` subtests, Web passed `12/12`, and compileall/diff passed. Final commit verification is recorded
in the development log.

## Task 8 — Release gate and deployment

- Require exact numeric accuracy 100%, claim citation coverage 100%, zero hallucinated numeric/unknown/cross-filing citations/policy violations/secret leaks, answerability >= 95%, metamorphic consistency >= 98%, free-form Recall@20 >= 95%, structured 20-request p95 <= 2 seconds, provider E2E p95 <= 10 seconds, and zero hidden evaluator errors.
- Add optional detailed fields: `execution_mode`, `analysis_dimension`, `conclusion`, `evidence_slots`, `limitations`, `claim_support`.
- Validate 8001 staging, image digest, read-only mounts, Web/API smoke, and rollback assets before promoting the exact tested image to 8000.
