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

- Preserve the legacy 300-case and 856-case regressions.
- Add a deterministic 600-case suite split into 480 development and 120 hidden holdout cases.
- Allocate: structured 120, alias/period/correction 90, free-form 120, multi-evidence judgment 90, policy/adversarial 90, API/concurrency 60, fault 30.
- Produce JSON and HTML reports and classify failures as entity, period, account, routing, retrieval, evidence, calculation, generation, security, or runtime.

## Task 3 — Input and routing hardening

- Normalize NFKC, whitespace/zero-width characters, manifest aliases, English issuer names/tickers, and unique one-edit typos.
- Decompose multiple issuers, periods, and metrics without silently collapsing them.
- Set every public question field to a 2,000-character maximum and reject malformed or contradictory inputs.
- Detect bounded encoded/multilingual prompt-injection surfaces without executing or forwarding decoded instructions.

## Task 4 — Bounded analysis executor

- Connect `plan_analysis -> search_analysis` to the public Function Calling path without adding a sixth public Tool.
- Support profitability, financial health, liquidity/cash flow, financing, CAPEX, business risk, governance, correction materiality, and peer comparison.
- Bind evidence slots by issuer and period. Missing mandatory slots force `insufficient_evidence`.
- Continue refusing transaction recommendations and forecasts while allowing bounded historical disclosure judgments.

## Task 5 — Hybrid retrieval gate

- Keep structured facts/corrections/aggregates on SQLite.
- Apply issuer/version/period filters before Sparse/Dense retrieval and remove instruction-like corpus text from model context.
- Adopt Dense only at >= 5 percentage-point Recall@20 gain, zero wrong issuer/version, and p95 <= 2 seconds; otherwise use Sparse fallback.

## Task 6 — Claim-level verification

- Represent conclusion, claims, calculations, citation IDs, evidence-slot ownership, and limitations explicitly.
- Require every claim to cite admitted evidence and every number to match DB facts or Decimal calculations.
- Fall back to deterministic facts or abstention when HCX output fails verification.
- Return a bounded verification trace, never private chain-of-thought.

## Task 7 — Automated judge and security evaluation

- Cover Korean/English paraphrases, typos, JSON demands, prompt extraction, direct/indirect/encoded injection, SQL/XSS strings, nonexistent facts, corrections, wrong issuer/version/unit/scope, provider faults, Dense faults, and restart/concurrency behavior.
- Run deterministic/policy/error cases without HCX and only the hidden free-form/judgment holdout through the provider.

## Task 8 — Release gate and deployment

- Require exact numeric accuracy 100%, claim citation coverage 100%, zero hallucinated numeric/unknown/cross-filing citations/policy violations/secret leaks, answerability >= 95%, metamorphic consistency >= 98%, free-form Recall@20 >= 95%, structured 20-request p95 <= 2 seconds, provider E2E p95 <= 10 seconds, and zero hidden evaluator errors.
- Add optional detailed fields: `execution_mode`, `analysis_dimension`, `conclusion`, `evidence_slots`, `limitations`, `claim_support`.
- Validate 8001 staging, image digest, read-only mounts, Web/API smoke, and rollback assets before promoting the exact tested image to 8000.
