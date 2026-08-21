# Loop 1 자유형 공시 검색 핸드오프

> **중요:** Loop 1은 완료 또는 GO가 아니라 `BLOCKED_SERVING_PATH` 상태다. 기존 `Recall@20=0.007936...`과 임베딩 적격 판정은 독립 검토에서 무효화됐으므로 품질 근거나 모델 도입 근거로 사용하면 안 된다.

이 문서는 `agent/disclosure-db-foundation` 브랜치에서 Task 1·2를 이어받고, Task 3의 마지막 구조화 기간 검색 차단을 해소한 뒤 유효한 Loop 1 평가를 재실행하기 위한 인수인계다.

## 현재 상태

| 구분 | 상태 | 근거 |
|---|---|---|
| Task 1 정책·AnalysisPlan | 구현됨, 보수적 정책 경계 2건 판정 보존 | `0df6dca`~`fdd165a` |
| Task 2 슬롯 검색·RRF | 구현 및 독립 재검토 PASS | `598855c`, `016cd19` |
| 정정 원본/현재 검색 | TDD 구현됨 | `24368cf` |
| 실제 DB에 맞춘 자금조달·지배구조 슬롯 | 구현됨 | `24368cf` |
| serving-admitted Gold 빌더·dense 계약 | 다음 작업용 WIP로 보존 | `74cc2a4` |
| 유효한 120건 Gold | 차단됨 | 수익성 dimension 답변 가능 기업 `0/12` |
| 유효 sparse Recall@20 | 없음 | 이전 수치는 invalid denominator |
| 임베딩 도입 결정 | 없음 | dense gate를 실행할 유효 residual이 없음 |
| 운영 배포 | 이번 작업에서 수행하지 않음 | 공개 서버는 이전 corpus release 상태 |

브랜치의 기준 커밋은 `74cc2a4440e2571de5d3b32dce4a23784dcaac3d`이다. 이 커밋은 의도적으로 `wip:`이며 완료 주장이 아니다.

## 시작하기

```powershell
git fetch origin
git switch agent/disclosure-db-foundation
git pull --ff-only
$env:PYTHONPATH = 'src'
python -m pytest tests/test_freeform_evaluation.py tests/test_dense_retrieval.py tests/test_search_index.py tests/test_analysis_planner.py -q
$env:PYTHONPATH = $null
```

먼저 다음 문서를 순서대로 읽는다.

1. `docs/superpowers/specs/2026-08-21-freeform-judgment-agent-design.md`
2. `docs/superpowers/plans/2026-08-21-freeform-judgment-agent.md`
3. `.superpowers/sdd/2026-08-21-freeform-judgment-agent/task-3-report.md`
4. `.superpowers/sdd/2026-08-21-freeform-judgment-agent/task-3-review.md`
5. `docs/development-log.md`

`.superpowers/sdd`는 로컬 진행 기록이며 Git에서 제외된다. 핵심 상태는 이 핸드오프와 커밋 기록에도 중복 보존했다.

## 정확한 차단 원인

`profitability_financial_health`는 `income_trend`와 `balance_sheet` 두 필수 financial slot에서 각각 최소 2개 기간이 필요하다. 그러나 `EvidenceService.search_analysis`가 만드는 구조화 하위 질의는 `QueryPlan.latest_period_count=1`을 유지한다.

그 결과 실제 12개 audited issuer 모두에서 다음 조건을 만족하는 source group이 0개였다.

```text
income_trend: serving 결과에 서로 다른 기간 2개 이상
balance_sheet: serving 결과에 서로 다른 기간 2개 이상
두 slot의 issuer·공시·기간·evidence가 모두 검증 가능
```

빌더는 `dimension_source_coverage_missing:profitability_financial_health:0<3`으로 fail-closed 종료했다. Gold와 평가 산출물을 덮어쓰지 않았으며, 수치를 꾸며내지 않았다.

## 다음 작업 순서

### 1. 구조화 slot 기간 수를 TDD로 수정하기

먼저 `tests/test_freeform_retrieval.py` 또는 `tests/test_evidence_service.py`에 실패 테스트를 추가한다.

- 수익성·재무건전성 judgment의 `income_trend`, `balance_sheet`는 최소 2개 기간을 요청한다.
- 일반 최신값 lookup은 계속 1개 기간만 요청한다.
- 명시적 3개년 질문은 기존 3개 기간 동작을 유지한다.
- issuer, `as_of`, correction policy, filing date, evidence binding을 유지한다.

가장 작은 수정 위치는 `src/disclosure_db/evidence_service.py`의 structured slot용 `QueryPlan` 생성 경계다. dimension별 필요 기간을 코드에 중복 하드코딩하기보다 `config/analysis_dimensions.json`의 slot 계약에서 읽을 수 있는 형태를 우선 검토한다.

### 2. 유효 Gold를 재생성하기

아래 조건을 모두 통과한 evidence만 분모에 넣는다.

- text target은 `agent_search.sqlite.search_document`에 존재한다.
- structured target은 `agent_overlay.sqlite.financial_fact_evidence` 또는 `event_fact_evidence`에 존재한다.
- 생성 질문이 `plan_analysis`에서 `judgment`로 분류되고 예상 dimension·slot domain과 일치한다.
- profitability는 두 financial slot에 각 2개 기간이 있다.
- correction materiality는 동일 issuer·lineage의 original/current pair가 함께 도달 가능하다.
- heading, 짧은 label, 빈 수치 행은 answer-safe evidence로 인정하지 않는다.
- 생성 사례는 `agent_audited`만 사용하고 `human_verified`로 표시하지 않는다.

실행 명령은 Task 3 브리프의 D드라이브 경로를 그대로 사용한다. D드라이브 base·live overlay·live index는 읽기 전용이다.

### 3. 평가를 두 번 재실행하기

유효 Gold가 120건 이상 만들어진 뒤에만 sparse 평가를 실행한다. 두 실행에서 Gold·manifest·semantic summary hash가 같아야 한다. latency와 측정 시각만 semantic hash에서 제외할 수 있다.

다음 값은 새 평가에서 다시 산출해야 한다.

- Recall@5, Recall@20, MRR
- slot completeness
- wrong issuer/version hard failure
- query/candidate count
- p50/p95
- 실제 serving을 실행한 valid residual root cause

### 4. 임베딩을 별도 판정하기

구조화 miss나 serving 미수록 target은 임베딩 근거가 아니다. 유효한 text residual만 존재하고 최대 개선 가능치가 5%p 이상일 때만 dense pilot이 eligible하다.

현재 WIP dense 계약은 다음 안전장치를 포함한다.

- 절대 20,000 fragment 상한
- metadata filter 후 ranking
- trusted known-evidence identity
- model/dimension/manifest/hash mismatch 거절
- deterministic evidence-ID tie ordering

외부 provider 접근이 없으면 `BLOCKED_EXTERNAL`로 남기고 측정값을 채우지 않는다. `bge-m3`를 채택했다고 기록하면 안 된다.

## 이미 해결된 serving 경계

- `correction_policy=current`: 현재 버전만 허용한다.
- `correction_policy=original`: 원본/root 버전만 허용한다.
- `correction_policy=both`: 동일 issuer·lineage·`as_of` 범위 안에서 원본과 현재 버전을 함께 허용한다.
- financing pressure event slot: 실제 overlay에 있는 `issued_shares`, `treasury_disposal_shares` 계열만 사용한다.
- governance signal: 존재하지 않는 event predicate를 꾸미지 않고 answer-safe indexed disclosure text를 사용한다.
- 거래형 질문: Task 2 검색 전에 `policy_transaction_ambiguous`로 fail-closed한다.

## 테스트와 검증 상태

| 범위 | 최신 결과 | 해석 |
|---|---:|---|
| serving-path 수정 묶음 | 91 passed, 8 subtests | `24368cf` 직전 검증 |
| WIP Gold/dense 계약 묶음 | 38 passed | `74cc2a4` 직전 검증 |
| compileall·diff check | PASS | 두 커밋 모두 기록됨 |
| 전체 pytest | 최신 WIP 이후 미실행 | 기간 차단 해소 후 반드시 실행 |
| D드라이브 pre/post | 동일 | 읽기 전용 유지 |

D드라이브 identity는 다음과 같다.

| Artifact | Size | UTC mtime | SHA-256 |
|---|---:|---|---|
| base corpus | 38,773,280,768 | 2026-08-17T16:00:13Z | `b8fb3be8b90d0cb1d8bc2491bee575aee632d29cc9bade21070e7e7b51646563` |
| live overlay | 2,162,688 | 2026-08-20T16:05:41.5227673Z | `a4491f2072766fcc11db65bad8c592c78696aea87132f3e7420857924938cb55` |
| live search index | 274,505,728 | 2026-08-18T19:14:09.9088108Z | `e223a19fcbefd4757a39b71e2b73eed7c81d01f2b54d74ca82e761dac10a8793` |

## Evidence → Finding → Path

### E-001

- title: Loop 1 유효 Gold 생성 차단
- observed_at: 2026-08-21 KST
- source_type: log
- source_ref: `.superpowers/sdd/2026-08-21-freeform-judgment-agent/task-3-report.md`
- content_hash: Git commit `74cc2a4440e2571de5d3b32dce4a23784dcaac3d`
- repro_command: `PYTHONPATH=src python scripts/build_freeform_gold.py`를 Task 3 브리프의 전체 인자로 실행
- raw_excerpt: `dimension_source_coverage_missing:profitability_financial_health:0<3`
- linked_workitem: Loop 1 Task 3
- supersedes: none

### E-002

- title: 구조화 slot 기간 수 기본값
- observed_at: 2026-08-21 KST
- source_type: file
- source_ref: `src/disclosure_db/evidence_service.py`, `src/disclosure_db/agent_contracts.py`
- content_hash: Git commit `74cc2a4440e2571de5d3b32dce4a23784dcaac3d`
- repro_command: `rg -n "latest_period_count" src tests`
- raw_excerpt: structured judgment subquery가 `latest_period_count=1`을 유지한다.
- linked_workitem: Loop 1 Task 3
- supersedes: none

### F-001

- title: 수익성 judgment가 필요한 2개 기간을 serving하지 못함
- severity: high
- category: design
- status: validated
- evidence_ids: [E-001, E-002]
- location: `src/disclosure_db/evidence_service.py`
- impact: answer-safe 7-dimension Gold 120건과 유효 Recall/embedding gate를 만들 수 없다.
- confidence: high
- repro_steps: Task 3 Gold 빌더를 실제 read-only DB 세 개로 실행하고 profitability coverage 오류를 확인한다.
- remediation: dimension slot 계약의 최소 기간 수를 structured subquery에 전달하고 current lookup 회귀를 고정한다.

### P-001

- title: Loop 1 재개 경로
- path_type: callflow
- start: `AnalysisPlan(profitability_financial_health)`
- goal: 유효 Gold와 sparse retrieval gate
- steps:
  1. financial slot별 최소 기간 수를 읽는다 — evidence: E-002 — finding: F-001
  2. audited structured route에서 각 slot의 2개 기간을 반환한다 — evidence: E-001 — finding: F-001
  3. serving-admitted Gold 120건을 fail-closed 생성한다 — evidence: E-001 — finding: F-001
  4. 동일 입력으로 sparse 평가를 두 번 실행한다 — evidence: E-001 — finding: F-001
  5. 유효 text residual에 대해서만 dense gate를 판정한다 — evidence: E-001 — finding: F-001
- residual_risks: 기간 수 확대가 일반 최신값 lookup이나 정정 버전 격리를 깨뜨릴 수 있으므로 focused·전체 회귀가 필요하다.

## 금지 사항

- 기존 invalid `freeform_retrieval_summary.json` 수치를 제품 품질로 인용하지 않는다.
- invalid `embedding_decision.json`을 모델 도입 승인으로 사용하지 않는다.
- Gold target이나 0.95/0.05 gate를 통과시키기 위해 변경하지 않는다.
- D드라이브 원본 DB, live overlay, live search index를 수정하거나 직접 승격하지 않는다.
- agent-generated 값을 `human_verified`로 기록하지 않는다.
- credential, 서버 `.env`, provider raw response를 Git에 올리지 않는다.

## Loop 1 이후

Loop 1이 유효한 GO 또는 근거 있는 NO-GO로 확정된 뒤에만 계획의 Task 4 `EvidenceGraph`부터 진행한다. 현재 사용자 요청에 따라 Task 4~10과 NCP 재배포는 시작하지 않았다.
