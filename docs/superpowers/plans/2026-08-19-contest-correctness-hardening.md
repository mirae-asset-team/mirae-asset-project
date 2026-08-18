# Contest Correctness Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 현재 114건 평가에서 반복되는 재무 기간, 이벤트 숫자, filing 날짜, 과다 인용 결함을 안전 gate를 약화하지 않고 수정한다.

**Architecture:** query planner에서 회계기간과 filing 날짜를 분리하고, structured fact는 원본 표의 정확한 numeric cell만 overlay에 넣는다. 검색 단계에서 명시 filing 날짜를 강제하며 generator는 실제 claim에 필요한 최소 evidence만 인용한다.

**Tech Stack:** Python 3.11+, SQLite, Decimal, FTS5, dataclasses, `unittest`, 기존 평가 CLI.

**Spec:** `docs/superpowers/specs/2026-08-19-contest-server-mvp-design.md`

## Global Constraints

- Gold expected answer, safety threshold, attestation 값은 결함을 숨기기 위해 수정하지 않는다.
- 재무 statement semantics: BS는 instant, IS/CIS/CF는 duration이다.
- API `as_of`는 filing version cutoff이며 회계기간이나 filing date로 재해석하지 않는다.
- numeric event fact는 정확히 한 개의 Decimal cell, 명확한 unit, 같은 filing의 parse-success evidence가 있어야 한다.
- 불확실하거나 복수 후보가 있는 event row는 `build_reject`에 reason code를 남기고 답하지 않는다.
- 최종 citation은 evidence bundle 내부이며 claim을 직접 지지하는 최소 집합이어야 한다.
- D드라이브의 기존 overlay를 제자리에서 쓰지 말고 staging 파일을 검증한 뒤 명시적으로 교체한다.

## File map

| 경로 | 책임 |
|---|---|
| `src/disclosure_db/agent_contracts.py` | `QueryPlan.filing_date` 계약 |
| `src/disclosure_db/query_planner.py` | statement-aware period와 exact filing date 분리 |
| `src/disclosure_db/evidence_service.py` | exact filing-date 전달과 structured retrieval |
| `src/disclosure_db/pipeline.py` | SSOT FTS exact `filed_at` 필터 |
| `src/disclosure_db/search_index.py` | sparse index exact `filed_at` 필터 |
| `src/disclosure_db/financial_overlay.py` | row-aware event numeric cell resolver |
| `src/disclosure_db/generation.py` | 최소 claim-specific citation 선택 |
| `config/agent_gold_predicates.json` | q009/q010을 포함한 명시 predicate alias |
| 관련 `tests/test_*.py` | 각 결함의 regression fixture |
| `docs/development-log.md` | 전후 평가와 rebuild 기록 |

---

### Task 1: Correct exact-date financial period semantics

**Files:**
- Modify: `src/disclosure_db/query_planner.py` in `plan_query`
- Modify: `tests/test_evidence_service.py` planner tests

**Interfaces:**
- Produces: IS/CIS/CF `YYYY-MM-DD` → duration `YYYY-01-01..YYYY-MM-DD`; BS → `instant_date=YYYY-MM-DD`
- Preserves: explicit API `as_of`

- [ ] **Step 1: Write failing statement-aware date tests**

```python
def test_exact_date_income_statement_is_year_to_date_duration(self):
    plan = plan_query("고려아연의 2024-12-31 연결 XI. 당기순이익은 얼마인가?", company_candidates=["고려아연"])
    self.assertEqual(plan.statement_type, "IS")
    self.assertEqual((plan.period_start, plan.period_end), ("2024-01-01", "2024-12-31"))
    self.assertIsNone(plan.instant_date)
    self.assertEqual(plan.target_periods, [{"period_type": "duration", "start": "2024-01-01", "end": "2024-12-31", "instant": None}])

def test_exact_date_balance_sheet_remains_instant(self):
    plan = plan_query("테스트의 2024-12-31 연결 자산총계는 얼마인가?", company_candidates=["테스트"])
    self.assertEqual(plan.statement_type, "BS")
    self.assertEqual(plan.instant_date, "2024-12-31")
    self.assertIsNone(plan.period_start)
```

- [ ] **Step 2: Run and confirm the IS test fails**

Run: `python -m unittest tests.test_evidence_service.EvidenceServiceTests.test_exact_date_income_statement_is_year_to_date_duration tests.test_evidence_service.EvidenceServiceTests.test_exact_date_balance_sheet_remains_instant -v`

Expected: IS plan incorrectly has `instant_date`.

- [ ] **Step 3: Normalize after statement type detection**

Keep `parsed_calendar_date` separate during regex parsing. After `statement_type` is known:

```python
if parsed_calendar_date and statement_type in {"IS", "CIS", "CF"}:
    year = parsed_calendar_date[:4]
    period_start = f"{year}-01-01"
    period_end = parsed_calendar_date
    instant_date = None
elif parsed_calendar_date and statement_type == "BS":
    instant_date = parsed_calendar_date
```

Build `target_periods` only after this normalization. A text/event exact date is handled in Task 2.

- [ ] **Step 4: Run focused and full planner/evidence tests**

Run: `python -m unittest tests.test_evidence_service -v`

Expected: PASS.

- [ ] **Step 5: Commit**

```powershell
git add src/disclosure_db/query_planner.py tests/test_evidence_service.py
git commit -m "fix: distinguish financial instant and duration dates"
```

### Task 2: Add an exact filing-date contract and filters

**Files:**
- Modify: `src/disclosure_db/agent_contracts.py` in `QueryPlan`
- Modify: `src/disclosure_db/query_planner.py`
- Modify: `src/disclosure_db/evidence_service.py`
- Modify: `src/disclosure_db/pipeline.py` in `query_database`
- Modify: `src/disclosure_db/search_index.py` in schema builder/filter/search methods
- Modify: `tests/test_evidence_service.py`
- Modify: `tests/test_search_index.py`
- Modify: `tests/test_pipeline.py`

**Interfaces:**
- Produces: `QueryPlan.filing_date: str | None = None`
- Extends: `query_database(..., filed_at=None)` and `SafeSearchIndex.search(..., filed_at=None)`

- [ ] **Step 1: Write failing planning and retrieval tests**

```python
def test_text_question_exact_date_sets_filing_date_not_accounting_period(self):
    plan = plan_query("테스트가 2023-04-10 공시한 제목은?", company_candidates=["테스트"])
    self.assertEqual(plan.filing_date, "2023-04-10")
    self.assertIsNone(plan.instant_date)

def test_search_index_exact_filing_date_excludes_nearby_filing(self):
    rows = index.search("공시 제목", company="테스트", as_of=None, filed_at="2023-04-10")
    self.assertEqual({row["filed_at"] for row in rows}, {"2023-04-10"})
```

Seed two otherwise matching filings on `2023-04-03` and `2023-04-10`.

- [ ] **Step 2: Run and confirm missing field/signature failures**

Run: `python -m unittest tests.test_evidence_service tests.test_search_index tests.test_pipeline -v`

Expected: FAIL on missing `filing_date`/`filed_at`.

- [ ] **Step 3: Add the backward-compatible contract**

```python
@dataclass(slots=True)
class QueryPlan:
    question: str
    # existing fields remain in order
    filing_date: str | None = None
```

For exact dates: financial questions use Task 1 semantics; event/text questions set `filing_date`. API `as_of` remains independent.

- [ ] **Step 4: Add exact filters to both retrieval paths**

In `query_database`, add `f.filed_at=?` to the eligible filing query or global query when `filed_at` is set. Include `filed_at` in `SafeSearchIndex.search_document` if not already stored, rebuild its schema revision, and add `d.filed_at=?` in `_filters`.

```python
def search(self, question: str, *, company: str | None, as_of: str | None,
           filed_at: str | None = None, limit: int = 30,
           correction_policy: str = "current") -> list[dict[str, object]]:
    ...
```

`EvidenceService.search` passes `plan.filing_date` to index and SSOT paths. It does not convert it into `as_of`.

- [ ] **Step 5: Run focused retrieval tests**

Run: `python -m unittest tests.test_evidence_service tests.test_search_index tests.test_pipeline -v`

Expected: PASS; nearby filing fixture is excluded.

- [ ] **Step 6: Commit**

```powershell
git add src/disclosure_db/agent_contracts.py src/disclosure_db/query_planner.py src/disclosure_db/evidence_service.py src/disclosure_db/pipeline.py src/disclosure_db/search_index.py tests/test_evidence_service.py tests/test_search_index.py tests/test_pipeline.py
git commit -m "fix: enforce exact disclosure filing dates"
```

### Task 3: Cite only evidence used by the claim

**Files:**
- Modify: `src/disclosure_db/generation.py` in `DeterministicGenerator.generate`
- Modify: `tests/test_agent_runtime.py`

**Interfaces:**
- Produces: `_claim_citation_ids(bundle) -> list[str]`
- Structured fact: first selected fact evidence only; calculation: calculation evidence only; text: first evidence only

- [ ] **Step 1: Write failing minimal-citation tests**

```python
def test_deterministic_financial_answer_cites_only_selected_fact(self):
    bundle = EvidenceBundle(
        question="매출액?",
        evidence=[EvidenceRef("ev_selected", "f1", "s1", "매출액 10"), EvidenceRef("ev_extra", "f1", "s1", "부가 설명")],
        answerable=True,
        financial_facts=[{"value_numeric": "10", "evidence_ids": ["ev_selected"]}],
    )
    self.assertEqual(DeterministicGenerator().generate(bundle).citation_ids, ["ev_selected"])

def test_deterministic_text_answer_cites_only_rendered_first_evidence(self):
    bundle = EvidenceBundle(question="제목?", evidence=[ev1, ev2], answerable=True)
    self.assertEqual(DeterministicGenerator().generate(bundle).citation_ids, [ev1.evidence_id])
```

- [ ] **Step 2: Run and confirm both currently cite all evidence**

Run: `python -m unittest tests.test_agent_runtime.AgentRuntimeTests.test_deterministic_financial_answer_cites_only_selected_fact tests.test_agent_runtime.AgentRuntimeTests.test_deterministic_text_answer_cites_only_rendered_first_evidence -v`

Expected: FAIL.

- [ ] **Step 3: Implement ordered, bundle-bounded citation selection**

```python
def _known_ids(bundle: EvidenceBundle, requested: Iterable[str]) -> list[str]:
    available = {ref.evidence_id for ref in bundle.evidence}
    return list(dict.fromkeys(item for item in requested if item in available))

def _claim_citation_ids(bundle: EvidenceBundle) -> list[str]:
    if bundle.calculation is not None:
        return _known_ids(bundle, bundle.calculation.evidence_ids)
    if bundle.financial_facts:
        return _known_ids(bundle, bundle.financial_facts[0].get("evidence_ids", []))
    if bundle.event_facts:
        return _known_ids(bundle, bundle.event_facts[0].get("evidence_ids", []))
    return [bundle.evidence[0].evidence_id] if bundle.evidence else []
```

If a structured claim resolves to zero known IDs, return unanswerable rather than falling back to all evidence.

- [ ] **Step 4: Run generator/verifier tests**

Run: `python -m unittest tests.test_agent_runtime tests.test_safety_contracts -v`

Expected: PASS, including unknown citation and numeric mismatch tests.

- [ ] **Step 5: Commit**

```powershell
git add src/disclosure_db/generation.py tests/test_agent_runtime.py
git commit -m "fix: emit claim-specific disclosure citations"
```

### Task 4: Resolve safe numeric event cells by table row

**Files:**
- Modify: `src/disclosure_db/financial_overlay.py`
- Modify: `config/agent_gold_predicates.json`
- Modify: `tests/test_financial_overlay.py`

**Interfaces:**
- Produces: `_resolve_numeric_event_cell(base, fact_id, predicate_values) -> dict[str, object]`
- Reject codes: `event_numeric_cell_missing`, `event_numeric_cell_ambiguous`, `event_numeric_unit_missing`, `event_numeric_unit_ambiguous`

- [ ] **Step 1: Add a realistic composite-row failing fixture**

Create one row with header cells `2. 계약내역`, `계약금액(원)` and data cell `240,993,039,040`. Set `fact.value_raw` to `2. 계약내역 | 계약금액(원) | 240,993,039,040`, and link evidence to one cell in that row.

```python
def test_agent_overlay_resolves_numeric_value_from_labeled_sibling_cell(self):
    result = build_agent_overlay(base, overlay, seed, predicates)
    rows = fetch_event_facts(base, overlay, company="테스트", predicate_terms=["계약금액"])
    self.assertEqual(result.event_imported, 1)
    self.assertEqual(rows[0]["value_numeric"], "240993039040")
    self.assertEqual(rows[0]["unit"], "원")
    self.assertEqual(rows[0]["evidence_ids"], [numeric_cell_evidence_id])
```

Add a second row with two parseable data cells and assert `event_numeric_cell_ambiguous`.

- [ ] **Step 2: Run and confirm the composite value is rejected**

Run: `python -m unittest tests.test_financial_overlay.FinancialOverlayTests.test_agent_overlay_resolves_numeric_value_from_labeled_sibling_cell tests.test_financial_overlay.FinancialOverlayTests.test_agent_overlay_rejects_ambiguous_numeric_sibling_cells -v`

Expected: first fails with `event_numeric_not_decimal`; second lacks the new reject.

- [ ] **Step 3: Implement strict row resolution**

For each numeric candidate:

1. Load all linked `table_cell` rows and same-table/same-row siblings.
2. Require a header path or sibling header matching one configured predicate value.
3. Parse only `cell_kind='data'` cells whose entire normalized text matches `^[+-]?[0-9]{1,3}(?:,[0-9]{3})*(?:\.[0-9]+)?$|^[+-]?[0-9]+(?:\.[0-9]+)?$`.
4. Require exactly one parseable data cell after excluding label and percentage/reference cells.
5. Resolve unit in priority order: candidate unit, predicate suffix `(원|주|%)`, table `unit_text`; require one unambiguous value.
6. Store only the selected numeric cell evidence ID.

```python
def _decimal_cell(text: str) -> Decimal | None:
    compact = text.strip()
    if not _DECIMAL_CELL.fullmatch(compact):
        return None
    value = Decimal(compact.replace(",", ""))
    return value if value.is_finite() else None
```

Do not parse a number from an arbitrary sentence or multi-value cell.

- [ ] **Step 4: Add explicit issued/treasury share aliases**

Extend `agent_gold_predicates.json` with `issued_shares` aliases that occur in q009 and a separate `treasury_disposal_shares` predicate for q010, including `보통주식 처분예정주식 수`, `처분예정주식(주)`, and exact raw labels observed in the trusted cells. Each is numeric, unit `주`, and restricted to `event_kv_candidate`.

- [ ] **Step 5: Run the full overlay safety suite**

Run: `python -m unittest tests.test_financial_overlay -v`

Expected: PASS, including cross-filing, PDF, lineage, ambiguity, missing unit, and conflict rejects.

- [ ] **Step 6: Commit**

```powershell
git add src/disclosure_db/financial_overlay.py config/agent_gold_predicates.json tests/test_financial_overlay.py
git commit -m "fix: validate row-aware numeric event facts"
```

### Task 5: Rebuild derived DBs through staging and attest them

**Files:**
- Modify: `docs/development-log.md`
- Generated, not committed: `D:\mirae-asset-project\staging\agent_overlay.candidate.sqlite`
- Generated, not committed: `D:\mirae-asset-project\staging\agent_search.candidate.sqlite`

**Interfaces:**
- Promotes candidate files only after `PRAGMA quick_check`, base attestation, build report, and representative fact queries pass.

- [ ] **Step 1: Verify the immutable base identity before building**

Run:

```powershell
python scripts/validate_database.py --database 'D:\mirae-asset-project\db\semantic-v1_129f5b0\disclosure_corpus_semantic_v1.sqlite' --distribution-attestation 'data\derived\database_distribution_manifest_semantic_v1.json'
```

Expected: size 38,773,280,768 and SHA-256 `b8fb3be8b90d0cb1d8bc2491bee575aee632d29cc9bade21070e7e7b51646563` match. Stop on mismatch.

- [ ] **Step 2: Build a candidate overlay outside the live path**

Run:

```powershell
python -m disclosure_db.cli build-agent-overlay --database 'D:\mirae-asset-project\db\semantic-v1_129f5b0\disclosure_corpus_semantic_v1.sqlite' --overlay 'D:\mirae-asset-project\staging\agent_overlay.candidate.sqlite' --financial-seed 'data\derived\financial_fact_gold_seed.jsonl' --predicate-config 'config\agent_gold_predicates.json' --attestation 'data\derived\database_distribution_manifest_semantic_v1.json' --report 'D:\mirae-asset-project\runs\evaluation\agent_overlay_candidate_build.json'
```

Expected: build exits 0 and q001/q004/q009/q010 numeric facts are present with exact evidence cells.

- [ ] **Step 3: Run read-only candidate verification**

Run `PRAGMA quick_check`, `overlay_matches_base`, counts by predicate/reject reason, and four representative agent queries. Record imported/rejected counts and candidate SHA-256 in the development log. Any `event_numeric_cell_ambiguous`, cross-filing, PDF, or lineage case must remain rejected.

- [ ] **Step 4: Build and verify a candidate search index if schema revision changed**

Run:

```powershell
python -m disclosure_db.cli build-search-index --database 'D:\mirae-asset-project\db\semantic-v1_129f5b0\disclosure_corpus_semantic_v1.sqlite' --output 'D:\mirae-asset-project\staging\agent_search.candidate.sqlite' --attestation 'data\derived\database_distribution_manifest_semantic_v1.json' --report 'D:\mirae-asset-project\runs\evaluation\agent_search_candidate_build.json'
```

Expected: build exits 0, quick check is `ok`, and exact filing-date fixtures work.

- [ ] **Step 5: Promote with a recoverable rename**

Stop the server. Resolve and verify the four absolute paths under `D:\mirae-asset-project`. Rename current live files to timestamped `.previous.sqlite`, then rename fully verified candidates to `agent_overlay.sqlite` and `agent_search.sqlite`. Never delete the previous files in this task. Start the server and run `scripts/smoke-agent.ps1`.

- [ ] **Step 6: Commit only the audit record**

```powershell
git add docs/development-log.md
git commit -m "docs: record corrected agent database rebuild"
```

### Task 6: Re-run the 114-case regression without moving gates

**Files:**
- Modify generated summaries: `data/derived/agent_eval_vertical_slice.json`, `data/derived/agent_holdout_eval.json`, `data/derived/retrieval_vertical_slice.json`, `data/derived/agent_stage_metrics.json`
- Modify: `docs/development-log.md`

**Interfaces:**
- 31 audited and 83 holdout cases; exact same question IDs and expected answers as baseline.

- [ ] **Step 1: Run complete unit verification**

Run: `python -m unittest discover -s tests -v`

Expected: all tests PASS except documented optional skips.

- [ ] **Step 2: Rebuild holdout deterministically**

Run:

```powershell
python scripts/build_agent_holdout.py --input 'data\derived\gold_qa.agent_audited.jsonl' --output 'data\derived\agent_holdout.jsonl'
```

Expected: 83 rows and zero schema errors.

- [ ] **Step 3: Measure stage latency**

Run `scripts/benchmark_agent_stages.py` with the live D-drive base, overlay, search index, attestation, audited Gold, `--limit 8`, and output `data/derived/agent_stage_metrics.json`.

Expected: finite planner/fact/local/rerank fields; provider configuration is reported truthfully.

- [ ] **Step 4: Evaluate audited and holdout sets**

Run `scripts/evaluate_agent.py` twice using the same D-drive inputs, first with `gold_qa.agent_audited.jsonl`, then `agent_holdout.jsonl`. Run `scripts/evaluate_retrieval.py` for Recall@20. Do not edit `config/evaluation_contract.json` after seeing results.

- [ ] **Step 5: Compare by question ID**

Record baseline and new values for answerability matches, numeric exactness, citation precision/recall, Recall@20, false numeric claims, unsafe answers, and p95 stages. List every remaining failure with primary cause. Stop immediately if false numeric claims or unsafe answers exceed zero.

- [ ] **Step 6: Commit reproducible summaries and log**

```powershell
git add data/derived/agent_eval_vertical_slice.json data/derived/agent_holdout_eval.json data/derived/retrieval_vertical_slice.json data/derived/agent_stage_metrics.json docs/development-log.md
git commit -m "test: record corrected disclosure agent regression"
```

## Plan 2 completion gate

- IS exact-date questions retrieve duration facts; BS exact-date questions retrieve instant facts.
- Explicit filing date excludes nearby filings in both index and SSOT paths.
- q001/q004/q009/q010 structured cases use exact Decimal values and direct evidence cells or abstain.
- Deterministic answers no longer cite all eight candidates.
- False numeric claims and unsafe answers remain 0.
- All remaining quality failures are documented; proceed to `2026-08-19-contest-stress-release.md` even if soft quality gates remain false, but never if a hard safety gate fails.
