# 미래에셋 AI 공모전 — 공시 Agent

주최 측이 제공한 공시 코퍼스를 검색·해석하고, 사용자의 주식 관련 질문에 근거 공시를 붙여 답하는 HyperCLOVA X 기반 질의응답 시스템 프로젝트입니다.

팀 저장소: [ksm12030-sudo/mirae-asset-project](https://github.com/ksm12030-sudo/mirae-asset-project)

## 현재 개발 상태와 인수인계

> **2026-08-21 기준:** Loop 1은 완료가 아닙니다. 구조화 financial slot 기간 수 차단은 코드에서 고쳤고, 유효 Gold 재생성은 이 작업 환경에 attested D드라이브 DB가 없어 `BLOCKED_LOCAL_RUNTIME`입니다. 기존 `Recall@20=0.007936...`과 이를 바탕으로 한 임베딩 적격 판정은 무효이므로 품질 근거나 모델 도입 근거로 사용하지 마세요. 이번 브랜치 변경은 운영 서버에 배포하지 않았습니다.

| 항목 | 현재 상태 | 다음 판단 |
|---|---|---|
| 작업 브랜치 | `agent/disclosure-db-foundation` | 이 브랜치에서 계속 작업 |
| Task 1 정책·AnalysisPlan | 구현 및 검증 완료 | 보수적 정책 경계 유지 |
| Task 2 슬롯 검색·RRF | 구현 및 독립 재검토 PASS | 회귀 테스트 유지 |
| 구조화 slot 기간 수 | TDD 수정 완료 | 수익성 2기간, 최신값 1, 3개년 3 유지 |
| Task 3 자유형 Gold·검색 평가 | `BLOCKED_LOCAL_RUNTIME` | attested D드라이브에서 Gold 재생성·이중 평가 |
| 임베딩·vector index | 도입 결정 없음 | 유효 text residual 평가 후에만 판정 |
| 운영 배포 | 이번 Loop 1 변경 미배포 | 유효 GO/NO-GO와 전체 검증 뒤 별도 진행 |

수익성·재무건전성 판단의 `income_trend`와 `balance_sheet`는 카탈로그 `min_periods=2`를 structured `QueryPlan.latest_period_count`로 전달합니다. 일반 최신값 lookup은 계속 1개 기간, 명시적 3개년 질문은 3개 기간입니다. 이전 차단(`dimension_source_coverage_missing:profitability_financial_health:0<3`)의 serving 원인 수정은 테스트로 고정했고, 실제 120건 Gold와 Recall은 아직 다시 만들지 않았습니다.

다음 작업자는 아래 순서로 시작합니다.

```powershell
git fetch origin
git switch agent/disclosure-db-foundation
git pull --ff-only
$env:PYTHONPATH = 'src'
python -m pytest tests/test_freeform_evaluation.py tests/test_freeform_retrieval.py tests/test_dense_retrieval.py tests/test_search_index.py tests/test_analysis_planner.py -q
```

1. [Loop 1 핸드오프](docs/handoffs/2026-08-21-loop1-freeform-retrieval-handoff.md)를 전부 읽습니다.
2. [설계 문서](docs/superpowers/specs/2026-08-21-freeform-judgment-agent-design.md)와 [구현 계획](docs/superpowers/plans/2026-08-21-freeform-judgment-agent.md)을 확인합니다.
3. attested D드라이브 base·overlay·search를 읽기 전용으로 두고 serving-admitted Gold를 다시 만듭니다.
4. 동일 입력으로 평가를 두 번 실행해 산출물 hash와 지표를 검증합니다.
5. 유효한 text residual이 확인된 뒤에만 sparse 대비 dense 개선 폭으로 임베딩 도입 여부를 판단합니다.

작업 판단과 검증 결과는 [개발 로그](docs/development-log.md)에 기록합니다. D드라이브의 base DB, live overlay, live search index는 읽기 전용이며 staging 산출물만 새로 만듭니다. credential, 서버 `.env`, provider 원문 응답, 대용량 DB는 Git에 올리지 않습니다. Task 3이 유효한 GO 또는 근거 있는 NO-GO가 되기 전에는 Task 4 이후나 NCP 재배포를 시작하지 않습니다.

팀 QA는 공개 질문 화면(`/`)이 아니라 [검수 데스크](docs/operations/qa-lab.md) `/lab`를 사용합니다. 검수 판정, 성능 측정, 발전 과정을 공유 SQLite에 쌓고 HTML 원장으로 내려받습니다. 암호는 없고 기록에 이름만 남깁니다.

## 공식 제약

- 평가 답변의 LLM은 HyperCLOVA X만 사용합니다.
- 답변 근거는 주최 측 제공 코퍼스로 한정합니다.
- OpenDART 등 외부 API를 실시간 답변 근거로 호출하지 않습니다.
- 근거가 없거나 범위를 벗어난 질문에는 한계를 명시합니다.

## 디렉터리

```text
.
├─ data/
│  ├─ public/       # 주최 측 공식 코퍼스와 공공 원천 데이터
│  ├─ external/     # 알고리즘 연구용 외부 자료; 답변 인덱스에서 제외
│  └─ derived/      # 정제·통합·라벨링·생성한 프로젝트 제작 데이터
├─ references/
│  ├─ official/     # 공모전·네이버클라우드 공식 자료
│  ├─ research/     # LLM·공시 데이터 연구 문서
│  └─ links.md      # 데이터/API/참고 자료 링크 모음
├─ notebooks/       # 데이터 탐색과 모델 실험
└─ src/             # 수집, 전처리, 검색/RAG, LLM 애플리케이션 코드
```

## 현재 자료

- [공식 데이터셋 안내](data/public/official_dataset/README.md)
- [공식 문서 목록](references/official/README.md)
- [LLM·RAG 설계 원리](references/research/llm_system_principles.md)
- [공시 데이터 구조와 해석 원리](references/research/disclosure_data_principles.md)
- [공부·개발 착수 로드맵](references/research/getting_started_roadmap.md)
- [데이터·DB 1~3단계 구축 및 기술 선택 보고서](references/research/db_foundation_implementation_report.md)
- [팀 공유용 데이터·DB 구축 현황과 다음 작업](references/research/team_handoff_data_db.md)

## 데이터 관리 원칙

- `public`과 `external`에는 내려받은 원본을 변경하지 않고 보관합니다.
- 원본을 정제하거나 결합한 결과는 `derived`에 저장합니다.
- 각 자료에는 출처 URL, 수집일, 대상 기간, 이용 조건을 함께 기록합니다.
- API 키와 개인정보, 대용량 모델 파일은 Git에 커밋하지 않습니다.

## EDA 이후 1~3단계 실행

데이터 계약, 전수 구조화 적재, **구조 게이트**는 `src/disclosure_db` 파이프라인으로 재실행합니다.
구조 게이트 통과는 검색 관련성이나 답변 정확도를 의미하지 않습니다.

```powershell
$env:PYTHONPATH=(Resolve-Path 'src').Path
python -m unittest discover -s tests -v
python -m disclosure_db.cli build `
  --corpus 'data/public/official_dataset/raw/corpus' `
  --output 'data/derived/disclosure_corpus.sqlite'
python -m disclosure_db.cli query `
  --database 'data/derived/disclosure_corpus.sqlite' `
  --company '005930' `
  --text '계약금액'

# 원본 구조 SSOT를 보존한 semantic v1 사본 생성
python scripts/migrate_database.py `
  --source 'data/derived/disclosure_corpus.sqlite' `
  --output 'data/derived/disclosure_corpus_semantic_v1.sqlite' `
  --report 'data/derived/database_migration_semantic_v1.json'

# 승인 Gold와 semantic schema까지 함께 검증
python scripts/validate_database.py `
  --database 'data/derived/disclosure_corpus_semantic_v1.sqlite' `
  --output 'data/derived/database_validation_semantic_v1.json' `
  --expected-filings 4204 --expected-sources 4622 `
  --require-semantic-v1 `
  --integrity-mode attested `
  --integrity-attestation 'data/derived/database_migration_semantic_v1.json'
```

현재 38GB 로컬 SQLite는 전수 적재와 무결성 검증을 마친 **구조 SSOT**입니다. 용량과 원천자료
재배포 제약 때문에 DB 파일과 공식 원문은 Git에 올리지 않고, 재현 코드·스키마·검증 결과·SHA-256
인벤토리만 공유합니다. PostgreSQL은 아직 SQLite와 동등하지 않은 운영 후보 DDL 초안이며 이관
코드도 없습니다. 사람 검수를 마친 Gold QA 23건은 모두 `human_verified/approved`이며 Gold 배포
게이트를 통과했습니다. 다음 단계는 이 Gold를 기준으로 검색 관련성, 별도 `financial_fact` 의미 층,
정정 계보와 FTS 계약을 평가한 뒤 PostgreSQL/pgvector와 OpenSearch 후보를 비교하는 것입니다.

`semantic_v1` migration은 원본을 덮어쓰지 않습니다. 일관된 SQLite 온라인 백업 사본에만
`financial_fact` grain, evidence 동일-filing 제약, validated fact evidence 제약, 정정 계보 재계산,
external-content FTS와 증분 동기화 trigger를 적용합니다. 재무제표 정답 Gold는 아직 없으므로
`financial_fact`에 임의 수치를 적재하지 않습니다.

2026-08-16 전수 사본 검증에서는 구조 Gate와 semantic schema Gate가 통과했고 FK·FTS rowid
불일치는 0건이었습니다. 정정 계보는 13개 version이 바뀌어 unresolved가 546건에서 539건으로
줄었지만 539건과 missing-original 2건은 계속 답변에서 차단합니다. 승인 Gold의 근거 주소가 있는
8문항·9근거 조건부 검색은 원본과 사본 모두 Recall@20 1.0, MRR@20 0.381922였습니다. 이는 회사와
후보 filing을 Gold metadata로 고정한 검색 검증이며 전체 사용자 질의나 답변 정확도 수치가 아닙니다.

`query` 명령은 기본적으로 `unresolved`·`missing_original`·폐기 version을 제외합니다. 감사 목적으로
원문 후보 전체가 필요할 때만 `--include-unsafe`를 명시합니다.

## Agent runtime (7시간 수직 슬라이스)

원본 SQLite는 읽기 전용으로 유지하고, 사람이 승인한 재무제표 fact만 별도 overlay에 적재합니다.
overlay가 비어 있으면 에이전트는 일반 공시 fragment 검색만 수행하며, 근거가 없으면 자동으로
숫자·계산 질문을 답변 불가로 반환합니다. 현재 저장소의 `financial_fact_gold_seed.jsonl`에는
고려아연 최신 resolved 정정 사업보고서에서 셀·단위·기간을 대조한 8개 소형 검증 표본이 들어 있습니다.
실제 운영 확장은 같은 형식으로 사람이 원문과 대조한 행만 추가합니다. 숫자 계산은 `Decimal` allowlist만
허용하고, 최종 답변은 evidence ID가
실제로 검색 결과에 포함되는지 검증한 뒤 반환합니다.

```powershell
$env:PYTHONPATH=(Resolve-Path 'src').Path
$py = 'C:\Users\lark0\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe'

# 원본 DB를 절대 덮어쓰지 않는 overlay 구축
& $py -m disclosure_db.cli build-financial-overlay `
  --database 'D:\mirae-asset-project\db\semantic-v1_129f5b0\disclosure_corpus_semantic_v1.sqlite' `
  --overlay 'D:\mirae-asset-project\db\agent\financial_overlay.sqlite' `
  --seed 'data/derived/financial_fact_gold_seed.jsonl'

# HCX 키가 없으면 deterministic fallback으로 동작
& $py -m disclosure_db.cli agent-query `
  --database 'D:\mirae-asset-project\db\semantic-v1_129f5b0\disclosure_corpus_semantic_v1.sqlite' `
  --overlay 'D:\mirae-asset-project\db\agent\financial_overlay.sqlite' `
  --attestation 'data/derived/database_distribution_manifest_semantic_v1.json' `
  --question '삼성전자 매출액은 얼마인가?'

# Gold 회귀 평가(결과는 derived JSON으로 남김)
& $py scripts/evaluate_agent.py `
  --database 'D:\mirae-asset-project\db\semantic-v1_129f5b0\disclosure_corpus_semantic_v1.sqlite' `
  --overlay 'D:\mirae-asset-project\db\agent\financial_overlay.sqlite' `
  --gold 'data/derived/gold_qa.jsonl' `
  --output 'data/derived/agent_eval_full_db.json'
```

HTTP API가 필요하면 `pip install -e .[agent]` 후 `disclosure-agent serve`를 사용합니다. FastAPI와
uvicorn은 선택 의존성으로 지연 로딩되며, 핵심 CLI·테스트에는 필요하지 않습니다. API 응답에는
`request_id`, `corpus_revision`, `latency_ms`가 공통으로 붙고, overlay가 설정됐지만 base attestation에
실패하면 `/health`가 `ready=false`를 반환합니다.

공모전 호환 `/query`의 로컬·Docker·NCP 시작, 중지, 재시작, rollback 절차는
[contest server runbook](docs/operations/contest-server.md)을 따릅니다. `/health.ready=true`는
서버 readiness만 의미하며, 정확도·provider·외부 endpoint gate 통과를 의미하지 않습니다.

제출 산출물은 [기술제안서 원고](docs/submission/technical-proposal.md),
[기술제안서 PDF](output/pdf/mirae-disclosure-agent-technical-proposal.pdf),
[평가 API 서버 명세](docs/submission/api-server-spec.md)에 정리했습니다.

공식 과제자료의 평가 API 예시와 호환되는 `GET /answer`도 제공합니다.

```bash
curl -G "https://<team-endpoint>/answer" \
  --data-urlencode "question_id=Q-001" \
  --data-urlencode "question=평가 질의"
```

응답은 `question_id`, `question`, `retrieved_context`, `think_trace`, `answer` 다섯 필드로
구성됩니다. `think_trace`는 숨은 사고과정이 아니라 공개 가능한 처리 단계 요약이며,
`retrieved_context`에는 검증된 공시명·공시일·접수번호만 포함됩니다.

Runtime `agent-query`/`serve` requires `--attestation` whenever `--overlay` or `--search-index` is
configured. The distribution manifest records the offline-verified SHA-256, byte size, and trusted
`mtime_ns`; a missing or mismatched trusted mtime fails closed. Copying or re-extracting the database
requires a new offline SHA/size/mtime attestation and regeneration of dependent artifacts. Base-only
SSOT reads may run without an attestation because they do not validate or serve an overlay/index.

## Agent-audited Gold 회귀 기준

자동 생성 Gold는 사람 승인 Gold와 분리합니다. `review.status=agent_audited`는 결정론적
evidence·lineage·단위·Decimal·citation 게이트를 통과했다는 뜻이며 `human_verified/approved`로
자동 승격되지 않습니다. 재현 명령과 reject 해석은 [Gold audit guide](docs/gold-set-audit.md),
단계별 명령·결과·커밋은 [development log](docs/development-log.md)에 기록합니다.

```powershell
$env:PYTHONPATH='src'
python scripts/build_agent_gold.py `
  --database 'D:\mirae-asset-project\db\semantic-v1_129f5b0\disclosure_corpus_semantic_v1.sqlite' `
  --gold 'data/derived/gold_qa.jsonl' `
  --overlay-seed 'data/derived/financial_fact_gold_seed.jsonl' `
  --output 'data/derived/gold_qa.agent_audited.jsonl' `
  --summary 'data/derived/gold_qa_agent_audit_summary.json' `
  --rejects 'data/derived/gold_qa_agent_rejects.jsonl' `
  --precomputed-base-sha256 'b8fb3be8b90d0cb1d8bc2491bee575aee632d29cc9bade21070e7e7b51646563' `
  --fact-limit 0
```

`--fact-limit 0`은 현재 baseline(기존 Gold 23건 + validated financial overlay 8건)이며, generic
predicate 후보까지 전수 생성하려면 옵션을 생략합니다. 대형 SQLite 전수 조인은 장시간 실행될 수
있고, 중단되어도 원자적 출력으로 기존 artifact는 손상되지 않습니다.

## Task 10 — real D-drive vertical slice

실제 attested corpus는 `D:\mirae-asset-project\db\semantic-v1_129f5b0\disclosure_corpus_semantic_v1.sqlite`에서 읽기 전용으로 사용합니다. 2026-08-18 재검증 결과 SHA-256은 `b8fb3be8b90d0cb1d8bc2491bee575aee632d29cc9bade21070e7e7b51646563`, 크기는 `38,773,280,768` bytes입니다. `D:\mirae-asset-project\db\agent\financial_overlay.sqlite`는 기존 파일이므로 건드리지 않고, Task 10은 `agent_overlay.sqlite`와 `agent_search.sqlite`만 새로 생성합니다.

```powershell
$env:PYTHONPATH=(Resolve-Path 'src').Path
python -m disclosure_db.cli build-agent-overlay `
  --database 'D:\mirae-asset-project\db\semantic-v1_129f5b0\disclosure_corpus_semantic_v1.sqlite' `
  --overlay 'D:\mirae-asset-project\db\agent\agent_overlay.sqlite' `
  --financial-seed 'data\derived\financial_fact_gold_seed.jsonl' `
  --predicate-config 'config\agent_gold_predicates.json' `
  --attestation 'data\derived\database_distribution_manifest_semantic_v1.json' `
  --report 'data\derived\agent_overlay_build.json'
python -m disclosure_db.cli build-search-index `
  --database 'D:\mirae-asset-project\db\semantic-v1_129f5b0\disclosure_corpus_semantic_v1.sqlite' `
  --output 'D:\mirae-asset-project\db\agent\agent_search.sqlite' `
  --attestation 'data\derived\database_distribution_manifest_semantic_v1.json' `
  --report 'data\derived\agent_search_index_build.json'
python scripts/build_agent_holdout.py --input 'data\derived\gold_qa.agent_audited.jsonl' --output 'data\derived\agent_holdout.jsonl'
python scripts/benchmark_agent_stages.py `
  --database 'D:\mirae-asset-project\db\semantic-v1_129f5b0\disclosure_corpus_semantic_v1.sqlite' `
  --overlay 'D:\mirae-asset-project\db\agent\agent_overlay.sqlite' `
  --search-index 'D:\mirae-asset-project\db\agent\agent_search.sqlite' `
  --attestation 'data\derived\database_distribution_manifest_semantic_v1.json' `
  --gold 'data\derived\gold_qa.agent_audited.jsonl' `
  --output 'data\derived\agent_stage_metrics.json'
```

The safe index is temporary-first and atomically promoted only after attestation and `quick_check`. If it is absent or drifts, serving falls back to the immutable SSOT FTS path. The benchmark writes measured p95 fields plus `reranker_provider_configured=false`; its `reranked_retrieval_p95_ms` is the bounded local-order fallback, not provider latency, and the provider latency gate therefore remains missing. With no `CLOVASTUDIO_API_KEY`, no live provider call occurs. API serving remains optional (`pip install -e .[agent]`); this environment has no FastAPI, so HTTP endpoint smoke was recorded as an environment skip while deterministic runtime smoke passed.

Task 10 artifacts and measured results are recorded in [the development log](docs/development-log.md): overlay quick-check `ok` with 651 imported rows, index quick-check `ok` with 329,323 indexed rows, isolated fact-lookup p95 `172.61 ms`, audited Gold quality gate `false`, and holdout quality gate `false` with no unsafe or false numeric claims. PostgreSQL/OpenSearch/dense embeddings remain evaluation-triggered follow-up work.
