# 미래에셋 AI 공모전 — 공시 Agent

주최 측이 제공한 공시 코퍼스를 검색·해석하고, 사용자의 주식 관련 질문에 근거 공시를 붙여 답하는 HyperCLOVA X 기반 질의응답 시스템 프로젝트입니다.

처음 프로젝트를 보는 팀원은 [초보자용 프로젝트 구조·테스트·잔여 작업 안내](docs/README-project-guide.md)부터 읽어 주세요. 지금까지 구현한 흐름, 구조도, 테스트 방법, NCP 실행과 남은 embedding 평가·배포 작업을 한 문서에 정리했습니다.

팀 저장소: [mirae-asset-team/mirae-asset-project](https://github.com/mirae-asset-team/mirae-asset-project) (주최측 Organization으로 이전됨)

## 평가용 API End-point

```text
End-point: http://101.79.31.221:8000/answer
```

`GET /answer`는 인증 헤더 없이 호출합니다. `question_id`와 `question`을 query string으로 전달하고(한글은 URL 인코딩), 응답은 아래 5개 문자열 필드입니다.

| 필드 | 내용 |
|---|---|
| `question_id` | 요청에 실린 값을 그대로 회신 |
| `question` | 요청에 실린 질문 원문 |
| `retrieved_context` | 근거 공시 목록 `공시명 \| 공시일 \| 접수번호`, 최대 6,000자 |
| `think_trace` | 질의 구조화 → 검색 → 근거 검증 → 판정 요약 |
| `answer` | 검증을 통과한 답변, 또는 근거가 없을 때의 보류 문구 |

```bash
curl -G "http://101.79.31.221:8000/answer" \
  --data-urlencode "question_id=q001" \
  --data-urlencode "question=삼성전자의 최근 사업보고서 기준 매출액을 알려줘"
```

```python
import requests

response = requests.get(
    "http://101.79.31.221:8000/answer",
    params={"question_id": "q001", "question": "삼성전자의 최근 사업보고서 기준 매출액을 알려줘"},
    timeout=300,
)
print(response.json()["answer"])
```

서버 상태는 `curl http://101.79.31.221:8000/health`로 확인합니다. 요청 제한은 IP당 분당 120건·동시 4건이며 초과 시 `429`, 서버 전체 동시 8건 초과 시 `503`을 반환합니다. 현재 서빙 중인 이미지는 이 저장소의 commit `ae5126e`(태그 `submission-2026-09-06-final`)에서 빌드한 `sha256:0361fe2b308561ef9835868ad1024aa82165c40b513b5deeb55af812af1bbfdd`이며, base·overlay·검색 인덱스는 read-only로 mount합니다. 자세한 요청·응답·오류 계약은 [평가용 API 서버 명세](docs/submission/api-server-spec.md), 운영 절차는 [contest-server 런북](docs/operations/contest-server.md)에 있습니다.

## 환경 구성과 실행

Python 3.11 이상이 필요합니다. 답변에 쓰는 세 개의 데이터 파일(불변 base SQLite · overlay · 검색 인덱스)은 용량 때문에 Git에 넣지 않고 별도 링크로 전달하며, 아래 경로에 배치한 뒤 실행합니다.

```bash
# 1) 설치
pip install -e ".[agent]"

# 2) 환경 변수 — 실제 값은 커밋하지 않습니다
cp .env.example .env      # DISCLOSURE_* 경로와 CLOVASTUDIO_API_KEY를 채웁니다

# 3) 직접 실행
PYTHONPATH=src disclosure-agent serve      # http://127.0.0.1:8000

# 4) 또는 Docker Compose 실행 (운영과 동일한 read-only mount)
docker compose up -d disclosure-agent
docker compose logs --tail 200 disclosure-agent
docker compose down                        # 중지
```

배치할 데이터와 검증값은 다음과 같습니다. overlay·검색 인덱스·attestation 3종은 GitHub Release [`submission-2026-09-06-final`](https://github.com/mirae-asset-team/mirae-asset-project/releases/tag/submission-2026-09-06-final)에 첨부돼 있고(`SHA256SUMS.txt` 포함), 불변 base SQLite는 주최측이 제공한 코퍼스입니다. 파일을 놓은 뒤 `GET /health`가 `ready=true`와 `base_attested`·`overlay_attested`·`search_index_ready`를 모두 `true`로 보고해야 질문을 받을 수 있습니다.

| 파일 | 크기 · SHA-256 |
|---|---|
| 불변 base SQLite | `38,773,280,768` bytes · `b8fb3be8b90d0cb1d8bc2491bee575aee632d29cc9bade21070e7e7b51646563` |
| live overlay | `a4491f2072766fcc11db65bad8c592c78696aea87132f3e7420857924938cb55` |
| live 검색 인덱스 | `e223a19fcbefd4757a39b71e2b73eed7c81d01f2b54d74ca82e761dac10a8793` |

회귀 테스트는 `PYTHONPATH=src python -m pytest -q`로 실행하며 `946 passed, 2 skipped, 283 subtests`가 기준입니다. 롤백을 포함한 운영 절차는 [contest-server 런북](docs/operations/contest-server.md)에 있습니다.

> 소스에 보이는 `.../v1/openai` 문자열은 **CLOVA Studio가 제공하는 OpenAI 호환 엔드포인트 경로**입니다. HyperCLOVA X 외의 LLM을 호출하는 코드 경로는 없습니다.

## 현재 개발 상태와 인수인계

> **Judge Stress V2 인수인계:** Task 1~8의 로컬 구현과 회귀가 완료되었습니다. Task 8은 원시 case 결과를 재검산하는 통합 release gate와 동일-image 8001→8000 배포/rollback 절차를 구현했지만, 현재 판정은 의도적으로 `BLOCKED_HARD_GATE`입니다. private holdout·실제 staging/provider·신뢰 앵커가 없고 Sparse Recall@20이 `0.487179... < 0.95`이므로 운영 승격은 실행하지 않았습니다. 다음 작업자는 [2026-09-02 Judge Stress V2 핸드오프](docs/handoffs/2026-09-02-judge-stress-v2-handoff.md)를 읽고 차단 항목을 해소한 뒤 같은 gate를 다시 실행하세요.

> **2026-09-05 기준:** 재무계정 카탈로그, chunk-v1, 5개 Tool Registry, Evidence Gate, bounded analysis, 주장 단위 검증, HCX Function Calling V1.2, 팀용 Web과 Sparse/Dense/Hybrid runtime 연결 및 QA Growth v4 보완이 안전 통합되어 있습니다. Judge Stress V2의 tracked 코드는 개발 480건만 생성하고, 전체 600건 검증에는 별도 git-ignored private holdout 120건을 요구합니다. 로컬 contract harness는 앱·provider를 호출하지 않으므로 해당 480건 통과를 앱 품질로 해석하면 안 됩니다. 통합 release gate는 누락·stale·비유한 지표, 원시 결과 불일치, identity 불일치와 변조된 PASS 보고서를 fail-closed로 거부합니다.

> **main 통합:** PR #5가 CI 통과 후 merge commit `076ca73`으로 병합되었습니다. 병합 직후 release gate는 34개 사유로 계속 `BLOCKED_HARD_GATE`여서 새 이미지를 8000에 승격하지 않았고 기존 공개 서비스는 그대로 유지했습니다.

> **2026-09-03 정형 응답 QA 보완:** HCX credential이 없거나 provider가 일시 중단돼도 결정론적으로 라우팅할 수 있는 재무 수치·기업 비교·기간 증감 질문은 read-only SQLite의 검증된 fact와 citation으로 답합니다. HCX는 이 경로의 도구 선택·계산에 관여하지 않으며, 서버가 만든 답변도 주장 단위 숫자·계산·evidence 검증을 모두 통과해야 노출됩니다. 실제 DB 8종 QA에서 삼성전자 최신값, 에스엠 alias, 다중 지표, 다중 기업, 기간 차이/증가율과 세 가지 안전 거절을 확인했습니다. 이는 private holdout/provider/Dense release gate를 대체하지 않으며, 8001은 외부 health timeout 상태라 배포하지 않았습니다.

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

| 영역 | 2026-09-05 상태 | 다음 작업 |
|---|---|---|
| 작업 브랜치 | `main` + `agent/qa-growth-v4` 안전 통합 후보 | PR 검증 후 `main` 병합 |
| 통합 기준 | `main` `cee4a56`, QA source `9d8c06a`, candidate `810ca5b` 이후 | 문제 archive 이력은 연결하지 않음 |
| 재무계정 카탈로그 | 구현·테스트 완료 | 신규 계정 추가 시 중앙 카탈로그만 확장 |
| Embedding Chunk v1 | XML/HTML/PDF, streaming, checkpoint/resume 구현 완료 | 전체 corpus 산출물의 manifest와 count 확인 |
| Sparse 검색 | 운영 안전 경로, query-time fallback 회귀 통과 | Dense 장애·재시작 평가에서 계속 hard gate로 확인 |
| BGE-M3/FAISS | full-corpus sidecar와 identity 계약 구현; Compose vector count `2,571,506`은 선언값 | 새 image/runtime manifest와 실제 artifact identity 대조 |
| Hybrid retrieval | remote Dense adapter, RRF, 중복 제거, filter, query-time Sparse fallback 구현 | cold start/restart fallback과 Dense 품질 gate 측정 |
| Tool/Evidence | 5개 Tool과 sufficient/partial/insufficient hard gate 완료 | Tool 선택·citation 정확도 반복 평가 |
| HCX Function Calling | V1.2 및 providerless 검증 정형 fallback 구현 | 운영 provider 문장화와 fallback을 각각 반복 smoke |
| FastAPI/Web | `/`, `/health`, `/v1/hcx/function-answer` 및 반응형 Web 완료 | NCP 최신 image 재배포 후 팀 URL 확인 |
| 테스트 | 전체 Python `941 passed, 2 skipped, 260 subtests`; Web JS `13 passed`; 팀 QA `1 passed`; 배포·위생 `48 passed, 94 subtests` | private/provider 600건과 실제 staging identity 평가 |
| Release gate | `BLOCKED_HARD_GATE` (34개 사유) | 차단 사유를 해소한 동일 입력으로만 재평가; 임계값 완화 금지 |
| PostgreSQL/pgvector | 미도입 | SQLite/Dense 측정 결과가 필요성을 증명할 때만 검토 |

### 현재 품질 경계

- 기존 독립 free-form 평가의 Sparse Recall@20 `0.487179...`는 과거 기준선이며 이번 Task 1에서 재측정하지 않았습니다.
- Compose/NCP 관측에는 full-corpus Dense가 연결되어 있지만, 새 identity contract가 포함된 image는 아직 build/deploy되지 않았습니다.
- Dense 채택 조건인 Sparse 대비 Recall@20 `+5%p`, wrong issuer/version `0`, p95 `2초` 이하는 모두 `UNVERIFIED`입니다. 따라서 전체 corpus 의미 검색 성능을 확보했다고 주장하지 않습니다.
- missing/invalid/empty Dense 결과와 sidecar 통신 실패는 로컬 회귀에서 Sparse로 fallback합니다. 다만 Compose의 agent cold start는 현재 Dense `service_healthy`에 의존하므로 cold-start fallback은 `UNVERIFIED`입니다.
- 배포는 두 단계입니다. pre-stage는 정확한 Git commit archive와 별도 hash-trusted retrieval 보고서로 후보 이미지를 한 번 build해 8001에만 올립니다. 실제 `/health.identity`, read-only mount와 600-case/provider 평가를 거쳐 최종 release gate가 PASS한 경우에만 별도 스크립트가 같은 image를 8000에 승격합니다. 현재 보고서는 BLOCKED이므로 어느 배포 단계도 실행되지 않았습니다.
- `[agent]` extra와 기본 `Dockerfile`에는 NumPy를 선언하지 않고, `Dockerfile.dense`가 설치하는 `[dense]` extra에만 `numpy==2.5.2`를 고정했습니다. Docker engine을 사용할 수 없어 실제 image package inventory는 `BLOCKED_ENVIRONMENT`입니다.
- Dense startup은 FAISS/metadata SHA-256, 전 vector의 L2 norm, 실제 FAISS metric/type, 그리고 mounted model 전체 파일 SHA-256을 먼저 검증합니다. 통과한 Python/NumPy/FAISS/model revision/vector count·dimension/index identity만 `/runtime/dense_runtime_manifest.json`과 sidecar `/health`에 동일하게 기록합니다. 기존 health 필드는 유지됩니다.
- 모델 identity는 live/read-only mount 안에서 임의 생성하지 않습니다. staging 모델 복사본에서 `$env:PYTHONPATH='src'; python scripts/build_dense_model_identity.py --model-path <staging-model-dir> --output <staging-model-dir>/model_identity.json`으로 생성하고 검토한 뒤, 그 디렉터리 전체를 read-only로 mount합니다.
- 이 builder의 신뢰 루트는 로컬 Hugging Face cache가 돌려준 정확한 commit snapshot입니다. 로컬 cache 자체의 공급망 진위까지 증명하려면 별도 서명·upstream hash 정책이 필요하며 현재 release gate의 후속 항목으로 남았습니다.
- 원본 base DB, overlay, search SQLite는 계속 read-only로 유지합니다.
- HCX credential, `.env`, SQLite, chunk JSONL, FAISS index와 모델 파일은 Git에 올리지 않습니다.

### Judge Stress V2 600건 suite

Task 2는 기존 300건 stress와 856건 financial release regression을 수정하지 않고 별도 600건 계약을 만들었습니다. 구성은 structured 120, alias/period/correction 90, free-form 120, multi-evidence judgment 90, policy/adversarial 90, API/concurrency 60, fault 30이며 development 480과 hidden holdout 120으로 나뉩니다. tracked 생성기는 audited source에서 development 480만 재현합니다. 실제 holdout 질문·oracle·선정값·paraphrase/template family는 git-ignored private evaluator 입력으로만 존재하며, 입력이 없으면 builder는 개발 파일을 만든 뒤 `BLOCKED_PRIVATE_HOLDOUT`으로 종료합니다.

tracked [manifest](data/derived/judge_stress_v2_manifest.json), [JSON summary](data/derived/judge_stress_v2_summary.json), [standalone HTML summary](data/derived/judge_stress_v2_summary.html)에는 case/group ID, SHA-256, 분류별 수량과 재현 메타데이터만 있습니다. underlying audited source facts는 추적되지만 exact hidden 질문·oracle·private selection은 추적되지 않습니다. raw development도 plan상 tracked 산출물이 아니며 holdout과 함께 `eval/judge_stress_v2/` 아래에서만 다룹니다. 실제 split은 issuer, 독립 document group, 독립 question-template family, source group, exact question hash가 모두 겹치지 않아야 합니다. 애플리케이션 runtime은 evaluator module이나 raw artifact를 import하지 않습니다. Task 7의 provider-free 실행은 `ContractJudgeRuntime`이라는 contract harness로 development root `480/480`을 검사했을 뿐 실제 앱/provider 정확도 평가는 아닙니다. summary는 `runtime_release_eligible=false`, `status=PARTIAL`, `release_state=BLOCKED`이고 `non_release_runtime`, `BLOCKED_PRIVATE_HOLDOUT`, `BLOCKED_PROVIDER`를 hard-gate 사유로 보존합니다.

Task 7 probe 계약은 한국어/영어 재표현, 유일 오타, JSON/순서 변경, 직접·간접·Base64·URL·zero-width 주입, prompt/key 추출, SQL/XSS, 존재하지 않는 사실과 issuer/version/unit/scope 오염, provider/Dense 장애와 restart identity를 다룹니다. hidden provider 대상은 새 root 120개가 아니라 기존 holdout의 free-form `24`개와 multi-evidence `18`개, 총 root `42`개에서 파생되는 versioned probe observation `120`개입니다. private 원문이 없으면 이 120개를 합성하거나 실행하지 않습니다. 결과와 HTML에는 질문·답변·provider body·prompt·credential을 저장하지 않습니다.

Reporter는 manifest 선언을 신뢰하지 않고 매번 600개 unique row, development/holdout `480/120`, 전체 및 split별 exact category allocation, row split/category, unique case ID와 canonical `suite_sha256`을 다시 검증합니다. Privacy 범위는 exact authored private 질문과 private rubric ID가 개발 코드/추적 산출물에서 조회·재구성되지 않는다는 뜻입니다. 공개 issuer/fact provenance나 공개 DB 사실 답변까지 암호학적으로 숨긴다는 주장은 하지 않으며, 유한한 공개 corpus의 hashed group ID는 대조 가능할 수 있습니다. pre-paraphrase 질문이나 공개 fact를 재구성하는 것은 evaluator가 보관하는 exact private holdout 원문과 동일하지 않습니다.

### Task 3 입력·routing 계약

공개 질문 필드는 `/query`, GET `/answer`, query/evidence/answer alias, HCX Function Calling, 공개 eval API에서 정확히 2,000자까지 허용하고 2,001자는 validation error로 거부합니다. 내부 Dense sidecar의 별도 제한은 변경하지 않았습니다. 질문은 공통 NFKC·공백·제로폭 정규화를 거치며, 회사명은 고정 `financial_company_universe.json`과 명시적 `company_aliases.json`에 있는 이름·영문명·종목코드만 대소문자 구분 없이 사용합니다. 임의 alias는 추론하지 않고, 한 글자 교정도 후보가 하나일 때만 적용합니다.

여러 회사·연도·structured 재무계정은 company × period × metric 요구사항으로 분리되어 모두 충족되어야 답변합니다. 존재하지 않는 날짜, 연결/별도 충돌, 의미를 바꾸는 중복 비교 기간은 provider나 Tool 호출 전에 결정론적으로 거부합니다. URL/percent, Base64, Unicode·공백 변형과 한·영·중·일 등 등록된 다국어 prompt-injection 표지는 최대 공개 질문 길이 안에서 탐지만 하며, 복호화 문자열은 Tool 인자·provider prompt·공개 응답에 전달하지 않습니다. 기존 투자 추천 거부 우선순위와 공개 Tool 5개는 유지합니다. Python 전체와 Web `12/12`는 로컬 통과했지만 Docker/NCP/live/provider 또는 600건 실제 실행 결과는 아닙니다.

```powershell
$env:PYTHONPATH = (Resolve-Path -LiteralPath 'src').Path
# 일반 개발 build: development.jsonl 480건 생성 후 private 입력 부재를 명시적으로 차단
python scripts/build_judge_stress_v2.py

# evaluator 보유 환경에서만 전체 600건 검증/manifest 재생성
python scripts/build_judge_stress_v2.py `
  --private-holdout <git-ignored-private-holdout.jsonl>

# evaluator가 case ID별 결과 metadata를 만든 뒤 JSON/HTML 재생성
python scripts/evaluate_judge_stress_v2.py `
  --manifest data/derived/judge_stress_v2_manifest.json `
  --results eval/judge_stress_v2/results.jsonl `
  --json-summary data/derived/judge_stress_v2_summary.json `
  --html-summary data/derived/judge_stress_v2_summary.html

# 로컬에서 가능한 development 480개 계약·공격·장애·동시성 검사
# 실제 provider 품질 PASS가 아니며 private/provider 부재를 BLOCKED로 남깁니다.
python scripts/run_judge_stress_v2.py `
  --repository-root . `
  --manifest data/derived/judge_stress_v2_manifest.json `
  --results eval/judge_stress_v2/results.jsonl `
  --failures eval/judge_stress_v2/failures.jsonl `
  --json-summary data/derived/judge_stress_v2_summary.json `
  --html-summary data/derived/judge_stress_v2_summary.html
```

### 다음 작업자가 시작하는 순서

```powershell
git fetch origin
git switch main
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

Judge Stress V2 Task 6부터 HCX 최종 생성은 claim 단위의 추가 계약을 사용합니다. 기존 최상위
응답 필드는 그대로 유지하면서 `metadata.claim_support`, `metadata.limitations`,
`metadata.verification_trace`를 추가합니다. 각 claim의 citation·evidence slot·DB fact·Decimal 계산
참조와 본문 숫자가 모두 일치할 때만 HCX 문장을 반환합니다. 미등록 citation, 다른 slot의 근거,
누락된 계산 피연산자, 알 수 없는 숫자, `NaN`/`Infinity`, 허용 범위 밖 결론이 하나라도 있으면 해당
HCX 문장은 폐기되고 검증된 구조화 fact/계산으로 결정론적 답변을 만들거나 답변을 보류합니다.
검증 추적은 고정된 상태·개수·검사 결과·사유 코드만 공개하며 prompt, 원문 evidence, provider
content, 숨은 사고과정은 포함하지 않습니다.
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
[기술제안서 PDF](docs/submission/technical-proposal.pdf),
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
