# Contest Stress Evaluation and Release Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 재현 가능한 300문항 스트레스 평가를 통과시키고, HyperCLOVA X 및 공개 API를 외부에서 검증해 공모전 제출 여부를 근거로 결정한다.

**Architecture:** 기ㅓve overlay/index는 평가 중 read-only다. fault test는 임시 fixture만 손상시킨다.
- unsafe answer, false numeric claim, unknown/cross-filing citation, evaluator error가 한 건이라도 생기면 run을 실패시킨다.
- raw responses/checkpoints는 `D:\mirae-asset-project\runs\evaluation`에 두고 Git에는 manifest·summary·최소 실패 record만 둔다.
- provider가 미구성이면 provider 품질/latency를 통과로 간주하지 않는다.
- NCP credential과 CLOVA key는 문서·명령행·Git·로그에 쓰지 않는다.

## 300-case allocation

| 영역 | 수 |
|---|---:|
| 재무 구조화 사실 | 70 |
| 이벤트 구조화 사실 | 45 |
| 정정·기준시점 | 35 |
| 기간 비교·계산 | 30 |
| 검색·인용 | 30 |
| 답변 불가·범위 밖 | 40 |
| 적대적 질문 | 25 |
| 언어 강건성 | 15 |
| 장애·성능 | 10 |
| 합계 | 300 |

## File map

| 경로 | 책임 |
|---|---|
| `config/stress_evaluation_contract.json` | category 수량과 hard/quality gate |
| `src/disclosure_db/stress_generation.py` | deterministic case 생성, canonical hash, group split |
| `src/disclosure_db/stress_evaluation.py` | oracle scorer, failure classification, aggregate gate |
| `scripts/build_agent_stress.py` | 300/2,000 case 생성 CLI |
| `scripts/evaluate_agent_stress.py` | checkpoint/resume 실행 CLI |
| `tests/test_agent_stress.py` | generation/scoring/resume/fault 계약 |
| `data/derived/agent_stress_300_manifest.json` | committed input provenance/coverage |
| `data/derived/agent_stress_300_summary.json` | committed aggregate metrics |
| `data/derived/agent_stress_failures.jsonl` | committed minimal reproduction records |
| `docs/operations/contest-release-checklist.md` | 공개 배포와 제출 승인 절차 |
| `docs/development-log.md` | 실행 명령과 전후 결과 |

---

### Task 1: Lock the stress contract and canonical case schema

**Files:**
- Create: `config/stress_evaluation_contract.json`
- Create: `src/disclosure_db/stress_generation.py`
- Create: `tests/test_agent_stress.py`

**Interfaces:**
- Produces: `canonical_json(value) -> bytes`, `source_sha256(record) -> str`, `validate_stress_case(case) -> None`, `validate_stress_cases(cases) -> None`, `StressCase` JSON mapping

- [x] **Step 1: Write failing schema and canonical-hash tests**

```python
def test_canonical_hash_is_key_order_independent(self):
    self.assertEqual(source_sha256({"b": 2, "a": 1}), source_sha256({"a": 1, "b": 2}))

def test_validate_rejects_duplicate_or_unknown_oracle(self):
    case = valid_case()
    case["stress"]["oracle"] = "llm_judge"
    with self.assertRaisesRegex(ValueError, "unknown oracle"):
        validate_stress_case(case)
    duplicate = valid_case()
    with self.assertRaisesRegex(ValueError, "duplicate question_id"):
        validate_stress_cases([duplicate, copy.deepcopy(duplicate)])

def test_numeric_exact_requires_decimal_unit_and_evidence(self):
    case = valid_numeric_case()
    case["answer"]["unit"] = None
    with self.assertRaisesRegex(ValueError, "unit"):
        validate_stress_case(case)
```

- [x] **Step 2: Run and confirm the missing module failure**

Run: `python -m unittest tests.test_agent_stress -v`

Expected: `ModuleNotFoundError`.

- [x] **Step 3: Implement canonical serialization and validation**

```python
ALLOWED_ORACLES = {"exact", "abstention", "metamorphic", "lineage", "fault"}
ALLOWED_CATEGORIES = {"financial", "event", "correction", "calculation", "retrieval", "unanswerable", "adversarial", "language", "fault"}

def canonical_json(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")

def source_sha256(record: object) -> str:
    return hashlib.sha256(canonical_json(record)).hexdigest()
```

Add `valid_case()` and `valid_numeric_case()` test helpers that return complete literal dictionaries conforming to the case contract. Single-case validation requires non-empty question, known oracle/category, base/group/mutation IDs, generator `deterministic_stress_v1`, seed `20260819`, 64-char source hash, and trust tier `human_verified|agent_audited`. Collection validation enforces unique `question_id`. Exact numeric answers require finite Decimal, unit, positive scale, filing ID, and evidence ID. Abstention cases require empty evidence and `answerability=unanswerable`.

- [x] **Step 4: Create the exact contract values**

`stress_evaluation_contract.json` stores schema `0.1.0`, seed, the 300 allocation above, allowed oracles, hard gates all equal to zero except fault/reproducibility equal to 1.0, and quality gates: answerability 0.95, numeric exactness 1.0, citation precision 1.0, citation recall 0.9, metamorphic consistency 0.98, Recall@20 1.0, configured-provider Recall@8 0.9.

- [x] **Step 5: Run tests and commit**

Run: `python -m unittest tests.test_agent_stress -v`

Expected: PASS.

```powershell
git add config/stress_evaluation_contract.json src/disclosure_db/stress_generation.py tests/test_agent_stress.py
git commit -m "test: define disclosure stress contract"
```

### Task 2: Build deterministic exact, negative, and metamorphic cases

**Files:**
- Modify: `src/disclosure_db/stress_generation.py`
- Create: `scripts/build_agent_stress.py`
- Modify: `tests/test_agent_stress.py`

**Interfaces:**
- Produces: `build_stress_cases(gold_records, contract, seed=20260819) -> list[dict]`
- Produces: `split_groups(cases, holdout_ratio) -> tuple[list, list]`

- [x] **Step 1: Write failing count, determinism, and leakage tests**

```python
def test_build_300_is_byte_deterministic_and_matches_category_counts(self):
    first = build_stress_cases(records, contract)
    second = build_stress_cases(list(reversed(records)), contract)
    self.assertEqual(canonical_json(first), canonical_json(second))
    self.assertEqual(len(first), 300)
    self.assertEqual(Counter(c["stress"]["category"] for c in first), expected_counts)

def test_group_split_never_separates_base_and_mutations(self):
    train, holdout = split_groups(cases, 0.2)
    self.assertTrue({c["stress"]["group_id"] for c in train}.isdisjoint({c["stress"]["group_id"] for c in holdout}))
```

- [x] **Step 2: Run and confirm missing generator functions**

Run: `python -m unittest tests.test_agent_stress -v`

Expected: FAIL.

- [x] **Step 3: Implement deterministic mutation families**

Use sorted source records and a local `random.Random(seed)`. Mutations are pure functions with fixed IDs:

- financial/event: year/date format, company alias, spacing, Korean particles, account/predicate alias.
- correction: original/current/corrected wording and valid as-of boundary.
- calculation: difference/growth operands from identical grain/unit only.
- unanswerable: nonexistent company-period-account combinations, future outlook, target price.
- adversarial: fixed prompt-injection templates in question only.
- language: stock code, Korean/English alias, punctuation and spacing.
- metamorphic: equivalent wording tied to the same expected answer and evidence set.

Do not generate a numeric case when source evidence, unit, scale, period, lineage, or trust tier is incomplete.

- [x] **Step 4: Implement the CLI and manifest**

```powershell
python scripts/build_agent_stress.py --gold data/derived/gold_qa.agent_audited.jsonl --contract config/stress_evaluation_contract.json --count 300 --seed 20260819 --output D:\mirae-asset-project\runs\evaluation\agent_stress_300.jsonl --manifest data/derived/agent_stress_300_manifest.json
```

The manifest contains input/output SHA-256, git commit, seed, case/base/mutation/company/filing counts, category counts, trust-tier counts, and UTC build timestamp. It contains no question/answer text.

- [x] **Step 5: Run tests, build twice, and compare hashes**

Run: `python -m unittest tests.test_agent_stress -v`

Build twice to distinct temp paths and compare SHA-256. Expected: hashes identical, 300 valid unique question IDs, zero group leakage.

- [x] **Step 6: Commit generator and manifest**

```powershell
git add src/disclosure_db/stress_generation.py scripts/build_agent_stress.py tests/test_agent_stress.py data/derived/agent_stress_300_manifest.json
git commit -m "test: generate deterministic disclosure stress cases"
```

### Task 3: Implement deterministic scoring and root-cause classification

**Files:**
- Create: `src/disclosure_db/stress_evaluation.py`
- Modify: `tests/test_agent_stress.py`

**Interfaces:**
- Produces: `score_case(case, answer) -> CaseScore`
- Produces: `classify_failure(case, answer, score) -> str`
- Produces: `aggregate_scores(scores, contract) -> dict`

- [x] **Step 1: Write failing scorer tests**

```python
def test_exact_numeric_requires_answerability_value_unit_and_required_evidence(self):
    score = score_case(numeric_case, verified_answer(value="10", citations=["ev1"]))
    self.assertTrue(score.passed)
    self.assertFalse(score_case(numeric_case, verified_answer(value="11", citations=["ev1"])).passed)

def test_abstention_rejects_numeric_or_citations(self):
    score = score_case(abstention_case, verified_answer(answerable=False, values=["1"], citations=["ev1"]))
    self.assertFalse(score.passed)
    self.assertIn("false_numeric_claim", score.failures)

def test_metamorphic_group_requires_same_answerability_value_and_core_evidence(self):
    self.assertFalse(score_metamorphic_group([base_result, changed_value_result]).passed)
```

- [x] **Step 2: Run and confirm missing evaluator module**

Run: `python -m unittest tests.test_agent_stress -v`

Expected: FAIL.

- [x] **Step 3: Implement oracle-specific deterministic rules**

`exact` compares normalized Decimal strings, unit, answerability, verified flag, and required evidence inclusion. `abstention` requires false answerability with empty numeric/citations. `lineage` compares filing IDs against required version/as-of set. `fault` requires unavailable/not-ready with no answer claim. `metamorphic` is scored after all group members finish.

Primary cause precedence is:

```python
CAUSE_ORDER = (
    "runtime_integrity", "planner", "entity_resolution", "structured_fact",
    "lineage", "retrieval", "reranker", "generation", "verification",
    "citation", "evaluation_contract", "unclassified",
)
```

Never convert `unclassified` into pass.

- [x] **Step 4: Implement aggregate hard/quality gates**

Aggregate exact counts and denominators. Missing metrics are failures, not zero or pass. Provider-only metrics are `not_measured` when unconfigured and make final provider readiness false.

- [x] **Step 5: Run tests and commit**

Run: `python -m unittest tests.test_agent_stress tests.test_agent_evaluation tests.test_retrieval_evaluation -v`

Expected: PASS.

```powershell
git add src/disclosure_db/stress_evaluation.py tests/test_agent_stress.py
git commit -m "test: score and classify disclosure stress failures"
```

### Task 4: Add resumable execution and safe fault fixtures

**Files:**
- Create: `scripts/evaluate_agent_stress.py`
- Modify: `src/disclosure_db/stress_evaluation.py`
- Modify: `tests/test_agent_stress.py`

**Interfaces:**
- Checkpoint key: `case_id + input_hash + git_commit`
- Run ID: UTC start timestamp + first 12 chars of manifest hash

- [x] **Step 1: Write failing resume and fault tests**

```python
def test_resume_skips_only_matching_case_hash_and_commit(self):
    completed = {"case_id": "c1", "input_hash": "a", "git_commit": "g1"}
    matching = {"question_id": "c1", "input_hash": "a"}
    changed = {"question_id": "c1", "input_hash": "b"}
    self.assertTrue(should_skip(matching, "g1", completed))
    self.assertFalse(should_skip(changed, "g1", completed))

def test_fault_fixture_never_mutates_source_database(self):
    before = sha256_file(source)
    run_fault_case(source, temp_dir)
    self.assertEqual(sha256_file(source), before)
```

- [x] **Step 2: Run and confirm failures**

Run: `python -m unittest tests.test_agent_stress -v`

Expected: FAIL on missing checkpoint/fault functions.

- [x] **Step 3: Implement atomic checkpoint records**

Write each completed case as canonical JSON to a temp file and `Path.replace` into `D:\...\runs\evaluation\{run_id}\checkpoints\{question_id}.json`. Resume only when question ID, canonical input hash, git commit, base attestation, overlay revision, and search revision all match.

- [x] **Step 4: Implement fault tests only on copied miniature fixtures**

Cover missing base, fast-identity drift, overlay mismatch, absent index, invalid index revision, SQLite quick-check failure, provider timeout, and malformed provider JSON. Full 38.8GB DB is never copied or corrupted; use test fixture DBs from existing unit-test seed helpers.

- [x] **Step 5: Implement the runner CLI**

```powershell
python scripts/evaluate_agent_stress.py --database D:\mirae-asset-project\db\semantic-v1_129f5b0\disclosure_corpus_semantic_v1.sqlite --overlay D:\mirae-asset-project\db\agent\agent_overlay.sqlite --search-index D:\mirae-asset-project\db\agent\agent_search.sqlite --attestation data\derived\database_distribution_manifest_semantic_v1.json --cases D:\mirae-asset-project\runs\evaluation\agent_stress_300.jsonl --contract config\stress_evaluation_contract.json --run-root D:\mirae-asset-project\runs\evaluation --summary data\derived\agent_stress_300_summary.json --failures data\derived\agent_stress_failures.jsonl --workers 1 --resume
```

The runner traps per-case exceptions as evaluator errors, records them, and stops the run on the first hard-gate violation. It never prints raw answer text or secrets.

- [x] **Step 6: Run tests and commit**

Run: `python -m unittest tests.test_agent_stress -v`

Expected: PASS.

```powershell
git add scripts/evaluate_agent_stress.py src/disclosure_db/stress_evaluation.py tests/test_agent_stress.py
git commit -m "test: add resumable disclosure stress runner"
```

### Task 5: Execute the 300-case failure loop

**Files:**
- Modify: `data/derived/agent_stress_300_summary.json`
- Modify: `data/derived/agent_stress_failures.jsonl`
- Modify: `docs/development-log.md`
- Bug-fix files are determined by the classified primary cause.

**Interfaces:**
- Uses the exact manifest generated in Task 2; does not regenerate cases during a fix loop.

- [x] **Step 1: Record the pre-run immutable identities**

Record git commit, manifest hash, base fast identity and trusted SHA, overlay hash/revision, search hash/revision, provider configured boolean, Python/package versions, free D-drive space, and server mode. Do not record secret values.

- [x] **Step 2: Run the 300 cases sequentially**

Use the command from Task 4 with `--workers 1`. At current ~5.1s end-to-end p95, expect about 25–35 minutes. If D-drive read errors or p95 exceeds twice the measured baseline, stop and diagnose instead of increasing workers.

- [x] **Step 3: Enforce immediate hard-stop conditions**

Stop on unsafe answer, false numeric claim, unknown/cross-filing citation, evaluator error, failed fault isolation, or non-reproducible manifest. Use `superpowers:systematic-debugging` before changing code.

- [x] **Step 4: Fix one dominant primary cause with TDD**

Choose the largest failure class, reproduce it with the smallest failing unit test in the owning module, confirm RED, implement the minimum fix, confirm GREEN, run the related suite, and commit. Do not batch unrelated causes or weaken an oracle/gate.

- [x] **Step 5: Resume the same run identity**

Because the git commit changed, completed cases must not be silently reused. Start a new run ID against the same manifest, compare summaries by question ID, and repeat Steps 3–5 until all hard gates pass and no further safe improvement fits the time window.

- [x] **Step 6: Run the full regression after the final loop**

Run: `python -m unittest discover -s tests -v`

Run: `python -m compileall -q src scripts tests`

Run both 31/83 evaluations and retrieval evaluation from Plan 2.

- [x] **Step 7: Commit truthful results**

```powershell
git add data/derived/agent_stress_300_summary.json data/derived/agent_stress_failures.jsonl docs/development-log.md
git commit -m "test: record 300-case disclosure stress results"
```

If hard gates pass and at least 90 minutes remain, generate and run the deterministic 2,000-case expansion. Keep its raw run on D and commit only manifest/summary/failures.

### Task 6: Verify HyperCLOVA X without leaking credentials

**Files:**
- Modify: `docs/development-log.md`
- Modify: `data/derived/agent_stress_300_summary.json`

**Interfaces:**
- Requires a rotated `CLOVASTUDIO_API_KEY` supplied as a local environment secret.

- [x] **Step 1: Verify secret hygiene before setting the key**

Run `git grep` for common key prefixes and inspect `.env` ignore status. Confirm no plaintext password/key from chat exists in tracked or staged files. If a key previously appeared in chat, use only a rotated replacement.

- [ ] **Step 2: Set the key only in the current process or untracked `.env`** — `BLOCKED_EXTERNAL`: no rotated credential supplied.

Do not place the key in a PowerShell command recorded in the development log. Confirm `/health.provider_configured=true` without showing the value.

- [ ] **Step 3: Run a bounded provider smoke set** — `BLOCKED_EXTERNAL`: provider credential unavailable.

Use 10 structured, 10 textual, 5 abstention, and 5 adversarial cases. Verify JSON schema, verifier rejection behavior, citations, provider timeout fallback, and p95. Stop on any hard-gate violation.

- [ ] **Step 4: Run the 300-case provider pass if smoke succeeds** — `BLOCKED_EXTERNAL`: provider credential unavailable.

Use a new run ID and identical 300-case manifest. Record provider configured=true, post-rerank Recall@8, provider error/fallback counts, and end-to-end p95. Never commit raw provider responses.

- [ ] **Step 5: Commit only sanitized metrics** — `BLOCKED_EXTERNAL`: no provider metrics exist to record.

```powershell
git add data/derived/agent_stress_300_summary.json docs/development-log.md
git commit -m "test: record HyperCLOVA disclosure evaluation"
```

If no rotated key is available, mark this task `BLOCKED_EXTERNAL` in the release checklist and continue with public local deterministic smoke; do not mark final submission ready.

### Task 7: Deploy and externally verify the public endpoint

**Files:**
- Create: `docs/operations/contest-release-checklist.md`
- Modify: `docs/development-log.md`

**Interfaces:**
- Public API: `GET /health`, `POST /query`
- Requires: NCP Server, Public IP, ACG, at least 80GB data volume, rotated secrets

- [x] **Step 1: Create the release checklist with binary evidence fields**

Include image git commit, DB/overlay/index hashes, Compose config hash, provider configured boolean, external source network, health request ID, answerable smoke request ID, abstention smoke request ID, restart result, and rollback path. Each item is pass/fail/not-run with timestamp.

- [ ] **Step 2: Provision or verify NCP prerequisites** — `BLOCKED_EXTERNAL`: no NCP account/resource/credential is available.

Confirm Server state, public IP, ACG inbound rule, SSH restriction, storage capacity and mount. If any is unavailable, use an approved temporary tunnel only for team testing and mark NCP submission endpoint incomplete.

- [ ] **Step 3: Transfer immutable artifacts and verify hashes remotely** — `BLOCKED_EXTERNAL`: no approved remote host/storage path.

Transfer the base, overlay, index, and attestation to `/srv/mirae/data`. Compare SHA-256/size against the local manifest before starting. Do not transfer `.env` through Git.

- [ ] **Step 4: Start the exact verified image** — `BLOCKED_EXTERNAL`: Docker runtime and public host unavailable.

Use the committed Compose file and secret environment on the server. Confirm container health and no write access to `/data/base` or `/data/agent`.

- [ ] **Step 5: Test from a second external network** — `BLOCKED_EXTERNAL`: no public endpoint or second network.

Call `/health`, one exact numeric `/query`, one textual `/query`, one out-of-scope question, and one injection question. Verify response schema, receipt/evidence IDs, abstention behavior, and latency. Then reboot or restart Docker and repeat health plus one query.

- [ ] **Step 6: Complete the go/no-go decision** — local deterministic gates are GO; public contest submission remains NO-GO.

GO requires: all safety hard gates, `/query` schema, external restart recovery, provider smoke, no secrets, numeric exactness 100%, citation precision 100%, and documented remaining soft quality gaps. Any missing external prerequisite or provider smoke is NO-GO for final submission but may remain GO for team demo.

- [x] **Step 7: Commit the sanitized release evidence**

```powershell
git add docs/operations/contest-release-checklist.md docs/development-log.md
git commit -m "docs: record contest server release decision"
```

## Plan 3 completion gate

- 300-case manifest is reproducible and all hard gates pass.
- Full unit/regression/retrieval tests pass with no hidden threshold changes.
- HyperCLOVA X live smoke and 300-case pass are recorded, or final submission is explicitly blocked.
- Public endpoint is tested from outside the server network and after restart, or final submission is explicitly blocked.
- Git contains only code, config, manifests, summaries, minimal failures, and sanitized logs—not DBs, raw responses, `.env`, credentials, or private URLs.
