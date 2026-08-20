# Corpus-wide Financial Facts Implementation Plan

> Execute with `superpowers:executing-plans`, `superpowers:test-driven-development`, `superpowers:systematic-debugging`, and `superpowers:verification-before-completion`.

## Task 1: Record baseline and decisions

**Files:** design/plan documents, `docs/development-log.md`

1. Record branch, commit, clean status, both Python test baselines, compile result, read-only boundaries, and the observed Samsung failure cause.
2. Record consolidated-first, three-period, six-metric, finance-revenue alias, strict aggregate-completeness, SQLite-first, and embedding go/no-go decisions.
3. Verify documentation and commit `docs: plan corpus-wide financial facts`.

## Task 2: Build the deterministic company/filing manifest

**Files:** new financial extraction module and CLI, focused tests, generated manifest/report

1. RED: test deterministic legal-issuer snapshot behavior using small real SQLite fixtures, including latest annual report, corrected lineage, alias separation, and stable ordering/hash.
2. GREEN: implement a query-only manifest builder and CLI. Reject unresolved or missing annual-report selections rather than guessing.
3. Run against the attested D-drive base, writing only repository output. Verify the observed 70 legal issuers/76 raw aliases, every answer-safe selection/reject, deterministic repeat output, and unchanged source metadata. Do not substitute an older filing for an unsafe latest report.
4. Update the development log and commit `feat: build financial filing universe`.

## Task 3: Extract six metrics and three periods

**Files:** financial extraction module/CLI and focused tests

1. RED: cover statement scope, period columns, units/scales, canonical aliases, finance revenue labels, consolidated preference, separate fallback, and ambiguous/conflicting rejection.
2. GREEN: extract candidate rows from selected filing table cells without provider calls or source writes.
3. Verify on representative Samsung, finance-sector, corrected, and separate-only filings; record candidate/reject distributions.
4. Update the development log and commit `feat: extract core financial fact candidates`.

## Task 4: Validate and report coverage

**Files:** validator/review report module, tests, canonical seed, coverage/reject reports

1. RED: require exact evidence/filing alignment, answer-safe lineage, parse-success textual cells, finite Decimal values, complete period/unit/scope, unique grain, and honest trust tiers.
2. GREEN: emit admitted `agent_audited` seed rows and deterministic review/reject/coverage reports. Never self-label human approval.
3. Review every exception against table context; unresolved rows remain excluded. Record per-company/metric/period coverage and whether each 70-legal-issuer aggregate snapshot is complete.
4. Run full regression and commit `feat: validate corpus-wide financial facts`.

## Task 5: Build a staged overlay

**Files:** overlay schema/builder, tests, operations documentation

1. RED: add universe/coverage snapshot and aggregate-completeness invariants while preserving existing event and fact behavior.
2. GREEN: build a candidate overlay only under `D:\mirae-asset-project\staging` using atomic output.
3. Verify attestation, foreign keys, `quick_check`, duplicates, evidence alignment, repeatability, and before/after live artifact metadata.
4. Commit `feat: stage corpus-wide financial overlay`. Do not promote until later serving gates pass.

## Task 6: Extend query planning, evidence, and APIs

**Files:** planner, evidence/agent/API contracts, generation, tests

1. RED/GREEN latest annual-report lookup with consolidated-first selection and explicit separate fallback.
2. RED/GREEN canonical metric/year/scope filters and `/financial-coverage`.
3. RED/GREEN `count_above`, rank, and company-list queries. Aggregate only with database-proven complete coverage; otherwise abstain with missing coverage.
4. RED/GREEN evidence-bound response metadata and deterministic structured fallback. Run full regression and commit focused changes independently.

## Task 7: Present coverage and evidence in the public UI

**Files:** packaged web assets and browser/API tests

1. RED/GREEN database company count and metric coverage presentation.
2. RED/GREEN expandable evidence details for report, filing date, scope, period, unit, raw label, and admitted table excerpt.
3. Preserve no-login use, responsive/sidebar controls, local KoPub fonts, XSS-safe rendering, and existing rate limits.
4. Verify Node, Python, package, syntax, and browser behavior; commit `feat: present financial evidence coverage`.

## Task 8: Evaluate, promote, deploy, and decide infrastructure

1. Run all admitted single-company facts, Samsung regression, three-year comparison, alias, correction, fallback, unit, threshold, rank, and incomplete-coverage cases.
2. Run full Python/Node/compile/package/secret/diff gates and 20-concurrent-request load test.
3. Promote the staged overlay by recoverable rename only after all gates pass, retaining the prior live artifact and verifying immutable/live before/after hashes.
4. Deploy code/data to NCP only with available authenticated access. Recheck read-only mounts, health, public browser/API, restart, and rollback.
5. Record provider-required gates as blocked if credentials fail; continue deterministic release work. Decide embeddings and PostgreSQL/OpenSearch only from measured thresholds.
6. Update the technical proposal, release checklist, development log, GitHub branch/PR, and commit/push each completed boundary.
