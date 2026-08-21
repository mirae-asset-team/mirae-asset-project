# Financial Fact Coverage and Hybrid Retrieval Implementation Plan

> Execute with `superpowers:executing-plans`, `superpowers:test-driven-development`, `superpowers:systematic-debugging`, and `superpowers:verification-before-completion`.

## Task 1: Audit validated financial-fact coverage without promotion

**Files:** `src/disclosure_db/retrieval_evaluation.py`, `tests/test_retrieval_evaluation.py`

1. Add failing tests for exact seed/Gold identity, numeric value, scale, filing, and evidence matching; duplicate, missing, and mismatched rows must remain explicit gaps.
2. Implement a pure `audit_financial_fact_coverage` function that reads JSONL inputs and returns deterministic aggregate counts plus per-fact status.
3. Keep generated candidates out of the validated count and label the scope as checked-in seed coverage, not corpus-wide completion.
4. Run focused tests, full Python regression, and compileall; commit `feat: audit validated financial fact coverage`.

## Task 2: Measure actual structured-plus-sparse retrieval

**Files:** `src/disclosure_db/retrieval_evaluation.py`, `tests/test_retrieval_evaluation.py`

1. Add failing tests proving sparse metrics are preserved, exact bundle evidence drives hybrid recall, route attribution comes from admitted facts, and unavailable structured dependencies fail closed.
2. Implement a hybrid evaluation function that composes `evaluate_retrieval`, deterministic planning, and a caller-supplied evidence service.
3. Report separate hybrid target/question recall, route counts, answerability, reason-code counts, and residual target IDs without changing sparse ranks or denominators.
4. Run focused tests, full Python regression, and compileall; commit `feat: evaluate hybrid evidence retrieval`.

## Task 3: Add a fail-closed CLI and produce the read-only audit

**Files:** `scripts/evaluate_retrieval.py`, `tests/test_retrieval_evaluation.py`, `docs/operations/contest-server.md`, `docs/development-log.md`

1. Add failing CLI/parser tests for optional `--overlay`, `--attestation`, `--search-index`, `--financial-seed`, and `--inventory-output`; partial structured configuration must be rejected.
2. Build the attested `EvidenceService` only when complete structured inputs are present; preserve sparse-only defaults.
3. Run the CLI against the read-only D-drive base, overlay, and safe-search index, writing results only under repository `tmp/`.
4. Record sparse and hybrid metrics, financial seed coverage, residual misses, and the embedding/PostgreSQL decision without claiming full-corpus financial completion.
5. Run focused tests, full Python and web regressions, compileall, secret scan, and `git diff --check`; commit `docs: record hybrid retrieval closure audit`.

## Task 4: Decide the next database step from residual evidence

**Files:** `docs/operations/contest-release-checklist.md`, `docs/development-log.md`

1. If residual text misses remain, write a bounded dense-pilot input manifest containing stable IDs only; do not call a paid provider without credentials and approval.
2. If there are no residual text misses, explicitly defer embeddings and pgvector as unjustified for the current Gold gate.
3. Mark HCX, paid embeddings, PostgreSQL/OpenSearch provisioning, and any NCP redeployment `BLOCKED_EXTERNAL` where applicable while leaving the deployed SQLite service untouched.
4. Verify the final documentation against generated audit JSON and commit `docs: decide post-hybrid database path`.
