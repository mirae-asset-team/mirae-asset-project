# Provider Final Gate Implementation Plan

> Execute with `superpowers:executing-plans`, `superpowers:test-driven-development`, `superpowers:systematic-debugging`, and `superpowers:verification-before-completion`.

## Task 1: Make provider 300 execution real and fail closed

**Files:** `scripts/evaluate_agent_stress.py`, `src/disclosure_db/stress_evaluation.py`, `tests/test_agent_stress.py`

1. Add failing unit tests for provider-mode parsing, missing-provider preflight, checkpoint identity separation, p95, and provider gate reasons.
2. Add a pure provider-gate evaluator and percentile helper.
3. Add `--provider-mode disabled|required`; initialize `AgentSettings.use_hcx` from it and fail before case execution when required but unconfigured.
4. Time non-fault answers, include provider identity in checkpoints, and write the provider fields without secrets or question text.
5. Run focused tests, full Python regression, compileall, then commit `feat: add fail-closed provider stress gate`.

## Task 2: Add official example-compatible GET /answer

**Files:** `src/disclosure_db/api.py`, `tests/test_agent_runtime.py`, `tests/test_public_limits.py`

1. Add failing tests for required query parameters, exact response fields, bounded evidence context, safe trace summary, abstention, and rate limiting.
2. Reuse the existing verified answer path; do not duplicate retrieval or generation.
3. Apply the same IP/concurrency limiter to `GET /answer`.
4. Run focused API tests and full regression, then commit `feat: add contest answer compatibility endpoint`.

## Task 3: Document and verify the release interface

**Files:** `README.md`, `docs/operations/contest-server.md`, `docs/operations/contest-release-checklist.md`, `docs/development-log.md`

1. Document cURL/Python examples and response fields without a real public IP.
2. Document provider-disabled and provider-required commands, cost warning, checkpoint isolation, and exact GO rule.
3. Re-run Node tests, full Python tests, compileall, secret scan, and `git diff --check`.
4. If no rotated credential exists, record schema smoke/provider 300/NCP deploy as `BLOCKED_EXTERNAL`; commit `docs: add final provider and evaluation api runbook`.

