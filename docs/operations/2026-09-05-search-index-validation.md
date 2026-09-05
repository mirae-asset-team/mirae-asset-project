# Search index validation lifetime report

## Decision

`SafeSearchIndex` validates the immutable search database once when `EvidenceService` starts. The validated object is reused for sparse searches and `/health` reports its cached readiness. A missing, corrupt, or attestation-mismatched index remains unavailable until process restart and continues to use the existing fail-closed SSOT fallback reason codes.

This is safe because the release contract pins the search database identity and mounts the live index read-only. Revalidating a 274 MB immutable database for every slot cannot detect a legitimate runtime update; releases replace the process and create a new validated object.

## Root cause and evidence

- The old path constructed `SafeSearchIndex` in every text slot, generic search fallback, and health validation.
- Each construction runs metadata checks, a full row count, and `PRAGMA quick_check`.
- Before the change, the new regression observed three constructions for two searches plus health.
- With the protected D-drive index, a cold startup validation took `6486.30 ms`; the next three searches took `1.50 ms`, `1.13 ms`, and `1.09 ms`. The startup cost is now paid once instead of once per request or evidence slot.

## TDD and verification

- RED: `test_evidence_service_validates_search_index_once_and_reuses_it` failed with `3 != 1`.
- GREEN: the service caches one validated index; two existing tests were updated so their index test double is installed before service startup.
- Related regression: `180 passed`, `24 warnings`, `10 subtests passed`.
- Full Python regression: `944 passed`, `2 skipped`, `98 warnings`, `264 subtests passed` in `88.59s`.
- Team QA Node regression: `1 passed`, `0 failed`.
- The warnings are existing FastAPI `on_event` deprecations.

## Data boundary

- Base SHA-256: `b8fb3be8b90d0cb1d8bc2491bee575aee632d29cc9bade21070e7e7b51646563`
- Search SHA-256: `e223a19fcbefd4757a39b71e2b73eed7c81d01f2b54d74ca82e761dac10a8793`
- The D-drive base and search databases were opened read-only; no corpus, overlay, index, credential, `.env`, PEM, or NCP setting was changed.

## Remaining release gate

This task removes the dominant local p95 source but does not claim the release gate is passed. Structured slot completeness, the versioned free-form relevance contract, the full evaluator, provider observations, staging identity, and same-image deployment still require independent verification.
