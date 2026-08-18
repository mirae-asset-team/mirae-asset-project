# Agent-audited Gold set Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Generate a deterministic, evidence-backed `agent_audited` Gold dataset and a Git-tracked development audit trail without changing the immutable corpus or mislabeling automated output as human-approved Gold.

**Architecture:** A read-only candidate extractor reads the semantic SQLite, existing Gold JSONL, and validated financial overlay. Independent schema, lineage, evidence, and runtime-citation gates emit either an audited JSONL record or a reject-ledger entry; a summary and development log make every result reproducible.

**Tech Stack:** Python 3.11+, stdlib `sqlite3`, `json`, `decimal`, existing `disclosure_db` contracts/evidence service, JSONL, unittest, PowerShell on Windows.

**Spec:** `docs/superpowers/specs/2026-08-18-agent-gold-audit-design.md`

## Global Constraints

- The base SQLite is read-only and must retain SHA-256 `b8fb3be8b90d0cb1d8bc2491bee575aee632d29cc9bade21070e7e7b51646563`.
- New records use `agent_audited`; the generator never sets a new record to `human_verified` or `review.status=approved`.
- Numeric values use finite `Decimal`; no float arithmetic or LLM-generated answer is authoritative.
- PDF visual evidence and unresolved/candidate/rejected/parse-failed evidence are rejected.
- Existing `data/derived/gold_qa.jsonl` is copied/read only; it is never rewritten by the generator.
- Every rejection has a machine-readable reason code and source identifiers.
- Every task ends with a focused test and a small commit.

---

### Task 1: Define the predicate and template configuration

**Files:**
- Create: `config/agent_gold_predicates.json`
- Create: `tests/test_agent_gold.py`

**Interfaces:**
- Produces a JSON object with `version`, `predicates`, and `templates` consumed by `scripts/build_agent_gold.py`.
- Each predicate has `id`, `question_type`, `answer_kind`, `allowed_fact_types`, and required evidence fields.
- The test module imports the generator by file path (because `scripts/` is not a package), defines `SAFE_CONFIG = load_predicate_config(...)` after the loader exists, and uses `unittest.TestCase` assertions rather than relying on pytest.

- [ ] **Step 1: Write the failing configuration test**

```python
def test_predicate_config_has_only_explicit_safe_predicates():
    config = load_predicate_config(Path("config/agent_gold_predicates.json"))
    assert config["version"] == "0.1.0"
    assert {item["id"] for item in config["predicates"]} >= {"contract_amount", "counterparty", "issued_shares"}
    assert all(item["answer_kind"] in {"numeric", "text"} for item in config["predicates"])
```

- [ ] **Step 2: Run the focused test and confirm it fails because the config/loader is absent**

Run: `$env:PYTHONPATH='src'; & $py -m unittest tests.test_agent_gold -v`

Expected: FAIL with a missing configuration or loader error.

- [ ] **Step 3: Add the explicit allowlist and a minimal loader**

Implement `load_predicate_config(path: Path) -> dict[str, object]` in `scripts/build_agent_gold.py` only after the test has failed. Include no wildcard predicate and no template that invents a source value.

- [ ] **Step 4: Run the focused test and verify it passes**

Run: `$env:PYTHONPATH='src'; & $py -m unittest tests.test_agent_gold -v`

Expected: PASS.

- [ ] **Step 5: Commit the configuration and test**

```powershell
git add config/agent_gold_predicates.json scripts/build_agent_gold.py tests/test_agent_gold.py
git commit -m "test: define safe agent gold predicates"
```

### Task 2: Implement read-only candidate extraction

**Files:**
- Modify: `scripts/build_agent_gold.py`
- Test: `tests/test_agent_gold.py`

**Interfaces:**
- `extract_existing_gold(gold_path: Path) -> list[dict[str, object]]`
- `extract_fact_candidates(base: Path, config: dict[str, object], limit: int | None) -> list[dict[str, object]]`
- `extract_overlay_candidates(seed_path: Path) -> list[dict[str, object]]`
- All SQLite connections use `file:<absolute-path>?mode=ro` and no write pragma.
- The test module provides `build_fixture_database(path: Path) -> Path`, creating only the minimal schema rows needed for `filing`, `source_document`, `table_record`, `table_cell`, `fact`, `fact_evidence`, and `filing_version`; the fixture is deleted by `TemporaryDirectory` cleanup.

- [ ] **Step 1: Add fixture-based failing tests for read-only access and same-filing evidence**

```python
def test_fact_candidate_extraction_keeps_evidence_filing_alignment(tmp_path):
    base = build_fixture_database(tmp_path / "base.sqlite")
    before = base.read_bytes()
    rows = extract_fact_candidates(base, SAFE_CONFIG, limit=10)
    assert rows
    assert all(row["filing_id"] == row["evidence_filing_id"] for row in rows)
    assert base.read_bytes() == before
```

- [ ] **Step 2: Run the focused test and confirm it fails**

Run: `$env:PYTHONPATH='src'; & $py -m unittest tests.test_agent_gold.AgentGoldTests.test_fact_candidate_extraction_keeps_evidence_filing_alignment -v`

Expected: FAIL because the extractor is not implemented.

- [ ] **Step 3: Implement deterministic SQL extraction**

Use this read-only query shape and map each row into a candidate dictionary; the predicate filter is an `IN` list built only from the loaded configuration, never a wildcard:

```sql
SELECT f.fact_id, f.filing_id, f.fact_type, f.subject, f.predicate,
       f.value_raw, f.unit, f.validation_status,
       fe.evidence_id, tc.table_id, tc.source_id,
       tc.text_raw AS evidence_text, tr.parse_status AS table_parse_status,
       sd.extension, sd.detected_format, sd.parse_status AS source_parse_status,
       fv.lineage_status, fv.is_current
FROM fact AS f
JOIN fact_evidence AS fe ON fe.fact_id = f.fact_id
JOIN table_cell AS tc ON tc.evidence_id = fe.evidence_id
JOIN table_record AS tr ON tr.table_id = tc.table_id
JOIN source_document AS sd ON sd.source_id = tc.source_id
JOIN filing_version AS fv ON fv.filing_id = f.filing_id
WHERE f.validation_status = 'candidate'
  AND f.predicate IN (?, ?, ...)
ORDER BY f.fact_id, fe.evidence_id
```

Keep only rows whose source and table parse status is `success`, whose evidence is not PDF visual content, and whose lineage is `root` or `resolved` with `is_current=1`; attach `filing_id` from both fact and cell so the auditor can reject a mismatch. Do not call an LLM.

- [ ] **Step 4: Run the focused tests and verify read-only behavior**

Run: `$env:PYTHONPATH='src'; & $py -m unittest tests.test_agent_gold -v`

Expected: PASS, including unchanged fixture hash.

- [ ] **Step 5: Commit the extractor**

```powershell
git add scripts/build_agent_gold.py tests/test_agent_gold.py
git commit -m "feat: extract read-only agent gold candidates"
```

### Task 3: Build schema, lineage, and rejection gates

**Files:**
- Modify: `scripts/build_agent_gold.py`
- Test: `tests/test_agent_gold.py`

**Interfaces:**
- `audit_candidate(candidate: dict[str, object], base: Path, *, source_sha256: str) -> AuditResult`
- `AuditResult` contains `status`, `record`, and `reason_codes`.
- `write_jsonl_atomic(path: Path, rows: Iterable[dict[str, object]]) -> None`.

- [ ] **Step 1: Write failing tests for the safety gates**

Cover: missing evidence, cross-filing evidence, unresolved lineage, PDF evidence, non-finite numeric values, duplicate IDs, and a valid text candidate. Each rejection must assert a specific reason code.

- [ ] **Step 2: Run the focused tests and confirm the new gate cases fail**

Run: `$env:PYTHONPATH='src'; & $py -m unittest tests.test_agent_gold -v`

Expected: FAIL for the unimplemented auditor.

- [ ] **Step 3: Implement the auditor with existing `gold_validation` rules**

Implement the decision order below so one candidate has stable, explainable reasons and no partial record is emitted:

```python
def audit_candidate(candidate, base, *, source_sha256):
    reasons = schema_reasons(candidate)
    reasons += evidence_reasons(candidate, base)
    reasons += lineage_reasons(candidate, base)
    reasons += value_reasons(candidate)
    reasons += citation_reasons(candidate)
    if reasons:
        return AuditResult(status="rejected", record=None,
                           reason_codes=sorted(set(reasons)))
    record = copy_as_agent_audited(candidate, source_sha256=source_sha256)
    return AuditResult(status="agent_audited", record=record, reason_codes=[])
```

Preserve original `human_verified` records; assign new records `review.status='agent_audited'`, `review.annotator='deterministic_agent_gold_v1'`, and `review.reviewer=None`. Use the fixed reason codes `evidence_not_found`, `cross_filing_evidence`, `lineage_not_answer_safe`, `visual_evidence_blocked`, `numeric_not_decimal`, `duplicate_candidate`, `schema_invalid`, `source_parse_failed`, `table_parse_failed`, and `citation_mismatch`.

- [ ] **Step 4: Run tests and validate atomic output behavior**

Run: `$env:PYTHONPATH='src'; & $py -m unittest tests.test_agent_gold -v`

Expected: PASS; interrupted writes leave no partial target file.

- [ ] **Step 5: Commit the audit gates**

```powershell
git add scripts/build_agent_gold.py tests/test_agent_gold.py
git commit -m "feat: add strict agent gold audit gates"
```

### Task 4: Add CLI generation, summary, and reject ledger

**Files:**
- Modify: `scripts/build_agent_gold.py`
- Create: `data/derived/gold_qa.agent_audited.jsonl`
- Create: `data/derived/gold_qa_agent_audit_summary.json`
- Create: `data/derived/gold_qa_agent_rejects.jsonl`
- Test: `tests/test_agent_gold.py`

**Interfaces:**
- Command: `python scripts/build_agent_gold.py --database <D:sqlite> --gold data/derived/gold_qa.jsonl --overlay-seed data/derived/financial_fact_gold_seed.jsonl --output data/derived/gold_qa.agent_audited.jsonl --summary data/derived/gold_qa_agent_audit_summary.json --rejects data/derived/gold_qa_agent_rejects.jsonl`.
- Summary contains `base_sha256`, `gold_input_sha256`, `seed_input_sha256`, `candidate_count`, `audited_count`, `rejected_count`, `reason_counts`, and `status`.

- [ ] **Step 1: Write failing CLI integration tests**

Run the command against the existing fixture and assert all three outputs are valid JSON/JSONL, every output carries input hashes, and the rejected rows have reasons.

- [ ] **Step 2: Run the integration test and confirm it fails**

Run: `$env:PYTHONPATH='src'; & $py -m unittest tests.test_agent_gold -v`

Expected: FAIL because the CLI and output writers are absent.

- [ ] **Step 3: Implement the CLI and atomic writers**

Use UTF-8, `ensure_ascii=False`, stable sorting by `(question_id, evidence_id)`, and write summary/rejects even when every candidate is rejected. Implement the writer with a sibling temporary file and `os.replace`:

```python
def write_jsonl_atomic(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".tmp")
    with temp.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
    os.replace(temp, path)
```

Do not mutate `data/derived/gold_qa.jsonl`; include the SHA-256 of every input in each generated record and in the summary.

- [ ] **Step 4: Run the command against the real D: corpus**

Run the exact command above with `D:\mirae-asset-project\db\semantic-v1_129f5b0\disclosure_corpus_semantic_v1.sqlite`. Confirm the base file size and SHA-256 remain unchanged.

- [ ] **Step 5: Commit generated artifacts and the CLI**

```powershell
git add scripts/build_agent_gold.py tests/test_agent_gold.py data/derived/gold_qa.agent_audited.jsonl data/derived/gold_qa_agent_audit_summary.json data/derived/gold_qa_agent_rejects.jsonl
git commit -m "feat: generate agent-audited gold artifacts"
```

### Task 5: Evaluate the generated set and document the process

**Files:**
- Create: `docs/gold-set-audit.md`
- Create: `docs/development-log.md`
- Create: `data/derived/agent_audited_eval.json`
- Modify: `README.md`
- Test: `tests/test_agent_gold.py`

**Interfaces:**
- `scripts/evaluate_agent.py --gold data/derived/gold_qa.agent_audited.jsonl` remains the evaluation entry point.
- The development log records UTC/KST timestamp, intent, exact command, exit/result, artifact paths, and commit ID for each task; secrets and API keys are never recorded.

- [ ] **Step 1: Write the documentation acceptance test**

Assert the audit guide names the generator command, state labels, rejection policy, and the Evidence → Finding → Path chain. Assert the development log contains the current baseline commit and the generation command.

- [ ] **Step 2: Run the documentation test and confirm it fails**

Run: `$env:PYTHONPATH='src'; & $py -m unittest tests.test_agent_gold -v`

Expected: FAIL because the new docs/log are absent.

- [ ] **Step 3: Write the task-oriented documents**

`docs/gold-set-audit.md` must contain these headings in order: `Quick start`, `Dataset states`, `Gate decision table`, `Reject ledger`, `Evidence → Finding → Path`, and `Release boundary`; include the exact generator and evaluator commands from this plan. `docs/development-log.md` must be append-only and include one dated entry for Tasks 1–5 with intent, exact command, exit/result, artifact paths, and commit ID; omit credentials and machine-local secrets.

- [ ] **Step 4: Run the generated-set evaluation**

Run: `$env:PYTHONPATH='src'; & $py scripts/evaluate_agent.py --database D:\mirae-asset-project\db\semantic-v1_129f5b0\disclosure_corpus_semantic_v1.sqlite --overlay D:\mirae-asset-project\db\agent\financial_overlay.sqlite --gold data\derived\gold_qa.agent_audited.jsonl --output data\derived\agent_audited_eval.json`

Expected: JSON with zero runtime errors, per-record status, verification count, answerability match, and latency p50.

- [ ] **Step 5: Run final verification and commit documentation**

Run: `$env:PYTHONPATH='src'; & $py -m unittest discover -s tests -v`; `$py -m compileall -q src scripts`; `git diff --check`; JSONL parse of all generated artifacts; base SHA-256 comparison.

```powershell
git add README.md docs/gold-set-audit.md docs/development-log.md data/derived/agent_audited_eval.json tests/test_agent_gold.py
git commit -m "docs: record agent gold audit and development process"
```

### Task 6: Review and push

**Files:**
- Review only: all files from Tasks 1–5

- [ ] **Step 1: Inspect the final diff for secrets, path leaks, and accidental base writes**

Run: `git diff --stat HEAD~5..HEAD`; `rg -n "API_KEY|Bearer |password|secret|D:\\mirae-asset-project\\db\\semantic" docs data scripts` and confirm only documented command examples contain the D: path.

- [ ] **Step 2: Run the full verification suite**

Run: `$env:PYTHONPATH='src'; & $py -m unittest discover -s tests -v`; `$py -m compileall -q src scripts`; `git diff --check`.

- [ ] **Step 3: Commit a final audit note**

Append the final test counts, generated artifact counts, and commit ID to `docs/development-log.md` and commit with `docs: close agent gold audit`.

- [ ] **Step 4: Push the tracking branch**

Run: `git push origin HEAD:agent/disclosure-db-foundation`.

- [ ] **Step 5: Report the release boundary**

Report `agent_audited` as regression-ready, explicitly state that `human_verified` still requires a human promotion decision, and list any remaining rejected reason categories.
