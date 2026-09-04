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

## Fix Round 1/5 — Independent Review Corrections

- Structured slots now mark evidence already admitted by the audited route before final fusion. Historical point-in-time results, original versions, and both-version retrieval remain admitted when the route accepted them.
- Structured `QueryPlan` values now carry each slot's filing date. Structured financial and event facts are rebound twice: once to final slot evidence and again to the final 20-node aggregate evidence set.
- The transaction-action guard now independently inspects both public `AnalysisPlan` question fields and never disables itself based on a supplied policy action. Any detected surface returns `policy_transaction_ambiguous` before retrieval.
- Text retrieval records prompt-injection exclusions before fusion and restores index result order after safe hydration. Structured retrieval now records actual sparse ranks and latency. Diagnostics remain content-free.
- The 20-node bound is enforced while assigning per-slot results, so the union of every slot's evidence and the aggregate evidence list are both bounded.
- TDD: seven new direct public-contract, structured-version/date, fact-binding, ordering/diagnostic, and total-bound tests first failed against the review commit and now pass.
- Verification: focused/listed regression passed `54` tests with `4` subtests; compileall using a temporary bytecode prefix and `git diff --check` passed.
