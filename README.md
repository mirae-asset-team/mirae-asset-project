# 미래에셋 AI 공모전 — 공시 Agent

주최 측이 제공한 공시 코퍼스를 검색·해석하고, 사용자의 주식 관련 질문에 근거 공시를 붙여 답하는 HyperCLOVA X 기반 질의응답 시스템 프로젝트입니다.

처음 프로젝트를 보는 팀원은 [초보자용 프로젝트 구조·테스트·잔여 작업 안내](docs/README-project-guide.md)부터 읽어 주세요. 지금까지 구현한 흐름, 구조도, 테스트 방법, NCP 실행과 남은 전체 embedding 작업을 한 문서에 정리했습니다.

팀 저장소: [ksm12030-sudo/mirae-asset-project](https://github.com/ksm12030-sudo/mirae-asset-project)

## 현재 개발 상태와 인수인계

> **Judge Stress V2 즉시 인수인계:** 이번 작업자는 계획의 Task 1(Dense 런타임·의존성·신원 계약)까지만 완료하고 종료합니다. Task 2~8은 완료가 아니며, 다음 작업자는 [2026-09-02 Judge Stress V2 핸드오프](docs/handoffs/2026-09-02-judge-stress-v2-handoff.md)와 [실행 계획](docs/superpowers/plans/2026-09-02-judge-stress-v2.md)을 읽은 뒤 Task 2의 600건 평가 구축부터 시작하세요.

> **2026-09-02 기준:** 재무계정 카탈로그, chunk-v1, 5개 Tool Registry, Evidence Gate, HCX Function Calling V1.1, 팀용 Web과 Sparse/Dense/Hybrid runtime 연결이 구현되어 있습니다. Compose에는 full-corpus Dense sidecar가 선언되어 있지만, 이번 Task 1에서는 Docker/NCP를 실행하거나 변경하지 않았습니다. 새 image의 Python/NumPy/FAISS/model/vector identity와 Dense Recall@20·issuer/version·p95 채택 gate는 아직 검증되지 않았습니다.

### 현재 한눈에 보기

```text
사용자 Web 질문
→ POST /v1/hcx/function-answer
→ HCX가 등록된 Tool 선택
→ ToolRegistry가 argument 검증 후 Tool 실행
→ EvidenceService / Sparse + 선택적 Dense retrieval / read-only SQLite 조회
→ Evidence 충분성과 실제 rcept_no 검증
→ answer_allowed=true인 경우에만 HCX 최종 답변 생성
→ backend가 검증된 citation과 접수번호를 Web에 반환
```

| 영역 | 2026-09-02 상태 | 다음 작업 |
|---|---|---|
| 작업 브랜치 | `agent/disclosure-db-foundation` (judge-stress-v2 병합됨) | Task별 독립 commit 유지 |
| 기준 커밋 | `99893d4` | Judge Stress V2 Task 1 기준점 |
| 재무계정 카탈로그 | 구현·테스트 완료 | 신규 계정 추가 시 중앙 카탈로그만 확장 |
| Embedding Chunk v1 | XML/HTML/PDF, streaming, checkpoint/resume 구현 완료 | 전체 corpus 산출물의 manifest와 count 확인 |
| Sparse 검색 | 운영 안전 경로, query-time fallback 회귀 통과 | Dense 장애·재시작 평가에서 계속 hard gate로 확인 |
| BGE-M3/FAISS | full-corpus sidecar와 identity 계약 구현; Compose vector count `2,571,506`은 선언값 | 새 image/runtime manifest와 실제 artifact identity 대조 |
| Hybrid retrieval | remote Dense adapter, RRF, 중복 제거, filter, query-time Sparse fallback 구현 | cold start/restart fallback과 Dense 품질 gate 측정 |
| Tool/Evidence | 5개 Tool과 sufficient/partial/insufficient hard gate 완료 | Tool 선택·citation 정확도 반복 평가 |
| HCX Function Calling | V1.1 실제 smoke 성공 | 운영 5종 질문 반복 smoke와 장애율 측정 |
| FastAPI/Web | `/`, `/health`, `/v1/hcx/function-answer` 및 반응형 Web 완료 | NCP 최신 image 재배포 후 팀 URL 확인 |
| 테스트 | Task 1 최종 Python `553 passed, 2 skipped`; Web JS `12 passed`; Dense identity focused `29 passed` | Task 2의 Judge Stress V2 600건 구축 |
| PostgreSQL/pgvector | 미도입 | SQLite/Dense 측정 결과가 필요성을 증명할 때만 검토 |

### 현재 품질 경계

- 기존 독립 free-form 평가의 Sparse Recall@20 `0.487179...`는 과거 기준선이며 이번 Task 1에서 재측정하지 않았습니다.
- Compose/NCP 관측에는 full-corpus Dense가 연결되어 있지만, 새 identity contract가 포함된 image는 아직 build/deploy되지 않았습니다.
- Dense 채택 조건인 Sparse 대비 Recall@20 `+5%p`, wrong issuer/version `0`, p95 `2초` 이하는 모두 `UNVERIFIED`입니다. 따라서 전체 corpus 의미 검색 성능을 확보했다고 주장하지 않습니다.
- missing/invalid/empty Dense 결과와 sidecar 통신 실패는 로컬 회귀에서 Sparse로 fallback합니다. 다만 Compose의 agent cold start는 현재 Dense `service_healthy`에 의존하므로 cold-start fallback은 `UNVERIFIED`입니다.
- `[agent]` extra와 기본 `Dockerfile`에는 NumPy를 선언하지 않고, `Dockerfile.dense`가 설치하는 `[dense]` extra에만 `numpy==2.5.2`를 고정했습니다. Docker engine을 사용할 수 없어 실제 image package inventory는 `BLOCKED_ENVIRONMENT`입니다.
- Dense startup은 FAISS/metadata SHA-256, 전 vector의 L2 norm, 실제 FAISS metric/type, 그리고 mounted model 전체 파일 SHA-256을 먼저 검증합니다. 통과한 Python/NumPy/FAISS/model revision/vector count·dimension/index identity만 `/runtime/dense_runtime_manifest.json`과 sidecar `/health`에 동일하게 기록합니다. 기존 health 필드는 유지됩니다.
- 모델 identity는 live/read-only mount 안에서 임의 생성하지 않습니다. staging 모델 복사본에서 `$env:PYTHONPATH='src'; python scripts/build_dense_model_identity.py --model-path <staging-model-dir> --output <staging-model-dir>/model_identity.json`으로 생성하고 검토한 뒤, 그 디렉터리 전체를 read-only로 mount합니다.
- 이 builder의 신뢰 루트는 로컬 Hugging Face cache가 돌려준 정확한 commit snapshot입니다. 로컬 cache 자체의 공급망 진위까지 증명하려면 별도 서명·upstream hash 정책이 필요하며 현재 release gate의 후속 항목으로 남았습니다.
- 원본 base DB, overlay, search SQLite는 계속 read-only로 유지합니다.
- HCX credential, `.env`, SQLite, chunk JSONL, FAISS index와 모델 파일은 Git에 올리지 않습니다.

### 다음 작업자가 시작하는 순서

```powershell
git fetch origin
git switch agent/disclosure-db-foundation
git pull --ff-only
$env:PYTHONPATH = 'src'
.\.venv\Scripts\python.exe -m pytest -q
node --test tests\web_api.test.mjs tests\web_history.test.mjs
```

1. [8/23~8/25 작업 전체 요약](docs/README-project-guide.md)와 [개발 로그](docs/development-log.md)의 2026-08-23~25 기록을 읽습니다.
2. NCP에서 최신 Web image를 rebuild/recreate하고 `/health`, `/`, 재무·일반 검색·트렌드·정정·근거 부족 5종 smoke를 확인합니다.
3. Git 밖의 전용 디스크에 전체 chunk-v1을 생성하고 manifest의 전체 수·형식별 수·token 통계·실패 수를 검증합니다.
4. BGE-M3 10개 smoke를 batch-size 2로 먼저 실행하고 vector/metadata 순서, 1024차원, L2 norm, 실패·truncation을 확인합니다.
5. 같은 계약으로 전체 corpus FAISS artifact를 checkpoint/resume 방식으로 생성합니다.
6. 독립 Gold에서 Sparse 대비 Recall@20 개선폭 `+5%p` 이상, wrong issuer/version `0`, p95 `2초` 이하를 모두 통과할 때만 Dense를 채택합니다.
7. 통과한 artifact만 NCP read-only 경로로 옮기고 `DISCLOSURE_DENSE_*` 설정과 runtime composition을 연결합니다. Dense 장애 시 Sparse fallback과 Evidence/접수번호 hard gate는 유지합니다.
8. 전체 corpus HCX E2E와 Web 5종 질문을 다시 실행하고 결과를 [개발 로그](docs/development-log.md)에 기록합니다.

세부 embedding 명령, 환경변수, NCP 재배포 순서는 [초보자용 프로젝트 안내](docs/README-project-guide.md)의 “남은 핵심 작업: 전체 embedding” 절을 따릅니다.

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

Registry 기반 HCX Function Calling은 기존 runtime에서 `DisclosureAgent`의 attested `EvidenceService`와
read-only SafeSearch를 재사용해 5개 Tool Registry를 조립합니다. 기존 `/v1/answer`는 유지하며 별도
`POST /v1/hcx/function-answer`가 `HCX Tool 선택 → Registry → Evidence 충분성 → 조건부 최종 생성`을
실행합니다. `answer_allowed=False`이거나 유효한 DART 접수번호가 없으면 두 번째 HCX 호출을 backend에서
차단합니다. 최종 접수번호 목록은 HCX가 만들지 않고 Evidence의 구조화된 `rcept_no`에서 렌더링합니다.
실제 local credential로 5개 Function schema, HCX Tool Call, Registry argument schema 호환 smoke는
통과했습니다. NCP의 Sparse/Structured 실제 smoke에서도 `answered`, `answer_allowed=true`, 구조화된
`citations[].rcept_no` 보존을 확인했습니다.
현재 runtime composition은 환경에 Dense URL이 있으면 remote full-corpus sidecar를 사용하고,
query-time Dense 실패에는 Sparse 결과를 유지합니다. Task 1의 runtime manifest/health identity는 로컬 계약만
검증됐고 새 Docker image 및 NCP runtime에서는 아직 확인하지 않았습니다. 상세 계약은
[Tool Registry v1](docs/tool-registry-v1.md)과
[HCX Function Calling 설계](docs/superpowers/specs/2026-08-23-hcx-function-calling-design.md)를 따릅니다.

FastAPI의 `GET /`는 별도 build가 없는 팀용 공시 Q&A Web을 제공합니다. 이 화면의 질문 요청은
same-origin `POST /v1/hcx/function-answer`만 사용하며 HCX key와 SQLite는 server 내부에 유지됩니다.
NCP image를 갱신한 뒤 브라우저에서 `http://<NCP-IP>:8000/`로 접속할 수 있습니다. 화면은
`answer_allowed`, Evidence 상태, 실제 citation과 접수번호, selected Tool과 latency를 표시하고,
근거 부족이나 유효 접수번호 누락 시 답변 UI를 차단합니다.

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
