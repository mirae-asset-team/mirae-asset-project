# Corpus-wide Core Financial Facts Design

## Goal

Make verified financial questions useful for every legal issuer in the corpus without weakening the existing evidence, lineage, citation, or read-only gates. The source contains 70 issuer corporation codes and 76 searchable name aliases; aliases must never be counted as separate companies. The first release covers six canonical metrics from each answer-safe latest current annual report and up to three periods displayed in that report.

## Observed failure and baseline

- The public question `삼성전자의 최근 사업보고서 기준 매출액을 알려줘` abstains because the live overlay has no admitted Samsung `financial_fact`; HyperCLOVA cannot create a number without admitted database evidence.
- The immutable corpus contains the relevant parsed financial-statement tables, but the checked-in seed contains only eight validated financial facts from a narrow Gold set.
- `2026-08-21` baseline on commit `df6f64d`: `unittest` ran 248 tests with 1 skip and no failures; `pytest` ran 262 tests with 1 skip, 19 subtests, and no failures; compileall exited zero.
- Existing sparse/hybrid 17-target results prove the narrow Gold route only. They do not prove corpus-wide financial completeness.

## Data contract

### Universe and filing selection

- Freeze the 70 legal issuers and their 76 searchable aliases into a deterministic manifest with stable company identifier, display name, stock code, selected filing ID, report name, filing date, fiscal year, correction lineage, and source database attestation.
- Select the latest fiscal-year annual report (`사업보고서`) whose current lineage is answer-safe. For a corrected chain, use the current resolved filing. Preserve the original and selected filing identifiers in the audit output.
- If the latest annual filing is not answer-safe, reject that issuer instead of silently substituting an older report. The 2026-08-21 source currently admits 69 issuers and rejects Hanwha Solutions because its 2025 annual filing is `missing_original`.
- Statement scope discovery is deferred to the extraction pass so the 1.5-million-row table corpus is not scanned twice. The universe records `pending_extraction` until Task 3 establishes consolidated availability.
- A manifest build must be order-independent and produce the same canonical JSON and SHA-256 for the same source database.

### Metrics and periods

- Canonical metrics are `revenue`, `operating_income`, `net_income`, `total_assets`, `total_liabilities`, and `total_equity`.
- Preserve the disclosed row label in `account_name_raw`. Finance-sector operating revenue labels may map to `revenue` only through an explicit reviewed alias registry; the response must display the original label.
- Prefer consolidated statements. Use separate statements only when no answer-safe consolidated statement exists for that company and label the fallback explicitly.
- Read the current period and up to two comparative periods from the selected latest annual report. Comparative values are attributed to the selected filing because they may be restated there.
- Every admitted value carries exact filing, table-cell evidence, statement type, scope, period, numeric value, currency/scale/unit, extraction method, validation status, and trust tier.

### Validation and completeness

- Automatic admission requires an answer-safe filing lineage, parse-success textual table evidence, deterministic statement/scope/period/unit recognition, an explicit metric alias, one unambiguous numeric cell, and no conflicting duplicate at the same company/metric/period/scope grain.
- Automatic values use `agent_audited`. `human_verified` is reserved for an actual named human approval; model consensus is never human approval.
- Ambiguous, conflicting, missing-unit, missing-period, visual-only, cross-filing, or unresolved-lineage candidates are rejected into an auditable review queue. Unresolved review rows never enter the serving overlay.
- Individual questions may use any admitted fact. Corpus-wide counts/ranks require a complete 70-legal-issuer snapshot for the requested canonical metric and comparison slot; missing facts fail closed with coverage metadata. The current `missing_original` issuer therefore blocks corpus-wide aggregation until its lineage/data gap is repaired.

## Storage and serving design

- The immutable base, live overlay, and live search index remain read-only. All builds write to repository-derived files or `D:\mirae-asset-project\staging` only.
- Keep SQLite as the serving database for this phase. It already provides attested, query-only joins, atomic file replacement, and a proven rollback path; changing infrastructure would not create the missing facts.
- Extend the overlay with a universe/coverage snapshot sufficient to enforce aggregate completeness without trusting UI state or inferred counts.
- Use `account_id` for the canonical metric and retain `account_name_raw` for evidence-faithful display. The existing financial fact grain already supports statement scope and three periods.
- HyperCLOVA may phrase an answer only after deterministic retrieval admits facts. Provider failure uses the evidence-bound deterministic response for structured questions.

## Rejected alternatives

- **LLM-first extraction:** rejected for initial build because it adds provider cost and nondeterministic classification before deterministic table exceptions are measured.
- **PostgreSQL/OpenSearch first:** rejected because infrastructure migration does not solve absent validated facts and increases NCP cost and operations risk.
- **Partial corpus-wide counts:** rejected because an apparently precise count over an incomplete denominator is misleading.
- **Literal `매출액` only:** rejected because it silently excludes finance-sector operating-revenue statements; explicit aliases with raw-label disclosure are safer.
- **Paid dense embedding now:** deferred until a separate free-form Gold set shows sparse Recall@20 below 0.95 and a bounded dense pilot improves it by at least 0.05 without safety regression.

## Release gates

- Zero unsafe answers, false numeric claims, unknown/cross-filing citations, evaluator errors, or source/live database mutations.
- Exact structured answers and evidence for every admitted regression case; no claim of corpus-wide completeness unless the generated coverage report proves 70/70 legal issuers for that metric snapshot. Search aliases are reported separately.
- New overlay passes source attestation, foreign-key checks, `quick_check`, duplicate/grain checks, and repeat-build determinism before recoverable promotion.
- SQLite remains the final serving choice if a 20-concurrent-request test has zero errors and p95 at or below two seconds. Otherwise record evidence and plan a separate PostgreSQL/OpenSearch migration.

## Audit trail

Each implementation task records its RED failure, GREEN verification, full regression, input/output hashes, reject counts, decision rationale, and commit in `docs/development-log.md`. Stable manifests and aggregate coverage reports are tracked; credentials, raw provider responses, large databases, checkpoints, and runtime logs are not.
