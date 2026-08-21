# Task 2 Report — Bounded Evidence-Slot Retrieval

## Delivered

- Added catalog-only, deterministic query variants, limited to four per evidence slot.
- Added per-slot reciprocal-rank fusion with a maximum of 30 candidates per variant, eight evidence nodes per slot, and 20 total evidence nodes.
- Added immutable `SlotRetrieval` and `AnalysisRetrieval` results, including aggregate structured facts for the next judgment stage.
- Added `EvidenceService.search_analysis` while preserving the existing `EvidenceService.search` interface and behavior.
- Kept text-slot retrieval on the attested read-only index, followed by existing safe metadata hydration. Financial and event slots continue through existing audited routes.
- Added mandatory-slot completeness handling with `required_slot_missing:<slot_id>` and no fabricated negative fact.
- Added a conservative pre-retrieval transaction-action guard. Any otherwise-allowed base question with a normalized buy/sell surface returns `policy_transaction_ambiguous`, no evidence, and performs no search/provider call.
- Excluded prompt-injection-bearing corpus text and recorded only bounded, non-content diagnostics.
- Updated RRF so duplicate evidence IDs contribute only once within each variant ranking.

## TDD Evidence

1. RED: `python -m pytest tests/test_freeform_retrieval.py -q` failed at collection because `disclosure_db.freeform_retrieval` did not exist.
2. GREEN: focused test coverage now includes deterministic variants, wrong-issuer/unsafe-version exclusion, duplicate-ID RRF behavior, independent mandatory slots, diagnostics redaction, injection-text exclusion, missing-slot incompleteness, and multi-clause transaction-action fail-closed/no-search behavior.

## Verification

- `python -m pytest tests/test_freeform_retrieval.py tests/test_evidence_service.py tests/test_retrieval_evaluation.py -q`: 47 passed, 4 subtests passed.
- `python -m compileall -q src tests`: passed with `PYTHONPYCACHEPREFIX` directed to a temporary location because pre-existing sandbox-owned bytecode caches rejected replacement.
- `git diff --check`: passed.

## Safety and Scope

- No provider-generated query path was added.
- Diagnostics contain no raw question or evidence excerpt.
- No claim uses a human-verification designation.
- D-drive corpus source, live overlay, and live index were not written, promoted, or changed.
