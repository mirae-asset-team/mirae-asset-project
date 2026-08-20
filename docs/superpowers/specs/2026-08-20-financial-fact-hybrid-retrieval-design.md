# Financial Fact Coverage and Hybrid Retrieval Evaluation Design

## Goal

Close the verified financial-question retrieval gap without weakening the existing sparse-retrieval gate, mutating immutable corpus artifacts, or paying for embeddings before residual text misses demonstrate a need.

## Observed baseline

- The approved agent Gold contains 16 answerable questions and 17 target evidence items.
- The current sparse FTS+RRF evaluation reports Recall@20 `13/17 = 0.7647058824` and complete-question recall `0.75`.
- All four sparse misses are table-cell evidence for 2024/2025 revenue and operating profit.
- Those four cells already belong to the eight human-validated `financial_fact` seed rows and are served through the structured overlay path.
- The immutable base corpus, live overlay, and live safe-search index are read-only inputs. The current anonymous SQLite release remains the rollback source of truth.

## Scope

This subproject adds an auditable financial-fact inventory and a hybrid retrieval evaluator that measures the real structured-plus-sparse evidence path. It does not automatically promote generated facts, create paid embeddings, replace SQLite, migrate production data, or redeploy NCP.

## Contracts

### Financial-fact inventory

- Inventory rows come only from the checked-in validated seed and approved Gold records.
- A seed fact is covered only when an approved answerable Gold record has the exact expected question ID, filing ID, numeric value, scale, and evidence-ID set.
- Missing, ambiguous, cross-filing, duplicate, or value-mismatched records remain explicit gaps; they are never inferred as validated.
- The report distinguishes `human_verified` seed coverage from broader corpus-wide financial-fact coverage. Eight validated facts must not be described as full-corpus completion.
- The inventory is deterministic JSON and contains no credentials, provider output, or copied database content beyond stable IDs and aggregate counts.

### Hybrid retrieval evaluation

- The existing sparse FTS+RRF metrics and per-target ranks remain unchanged and continue to be reported.
- The hybrid evaluator runs the same deterministic query planner and `EvidenceService` used by the agent, with the attested overlay/search configuration supplied by the caller.
- A hybrid target is found only when the exact approved evidence ID appears in the returned safe evidence bundle.
- Each found target is attributed to `structured_financial`, `structured_event`, or `sparse_text` from the facts admitted into that bundle. No route is guessed from question wording.
- Hybrid target recall, complete-question recall, route counts, residual misses, answerable/abstained counts, and reason codes are reported separately from sparse Recall@K.
- A missing or mismatched overlay/search attestation fails closed. It produces an unavailable/failed hybrid result and never silently relabels sparse results as structured success.

### Decision gate for embeddings and final serving database

- Dense embedding work is eligible only for residual targets that are both missed by the hybrid path and belong to the text route.
- Structured financial misses trigger fact-coverage or query-planning work, not embeddings.
- A dense pilot must preserve current safety and citation gates and show measurable recall gain on residual text misses before PostgreSQL+pgvector is adopted.
- PostgreSQL+pgvector, if justified, is a serving sidecar. Immutable SQLite remains the attested source of truth and rollback artifact.
- OpenSearch is deferred unless sparse+structured+pgvector still leaves a demonstrated search/operations need. It is not added to the current 8 GB NCP host by default.

## Implementation shape

1. Add a pure financial seed-to-Gold coverage audit with strict identity/value/evidence checks.
2. Add a hybrid evaluator that composes the existing sparse evaluator with actual `EvidenceService` bundles while preserving both metric families.
3. Extend the retrieval CLI with optional overlay, attestation, and safe-search paths plus an inventory output. Default sparse-only behavior remains backward compatible.
4. Run the evaluator against read-only D-drive artifacts and record the residual-miss decision. Do not write beside those artifacts.

## Verification and release boundary

- Every implementation change follows RED-GREEN TDD and gets a focused test and commit.
- Full Python, web, compile, secret-scan, and diff checks run before completion.
- No hard threshold is reduced. Sparse Recall@20 remains visible even if hybrid recall is higher.
- HCX/provider execution, paid embedding generation, production PostgreSQL/OpenSearch provisioning, and NCP redeployment remain `BLOCKED_EXTERNAL` until credentials, measured need, and a separately approved deployment plan exist.
