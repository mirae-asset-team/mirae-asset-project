# Contest server runbook

이 문서는 공모전 호환 `POST /query`와 `GET /answer` 서버의 로컬·Docker·NCP 실행 절차다. 원본 SQLite,
agent overlay, sparse search index는 서비스와 운영자가 읽기 전용으로 취급한다. `runs`와
`staging`만 쓰기 대상으로 둔다.

## Prerequisites and warnings

- Python 3.11 이상, 검증된 D-drive DB 세 파일과 distribution attestation이 필요하다.
- Docker 실행은 Docker Desktop 또는 Linux Docker Engine이 필요하다.
- `CLOVASTUDIO_API_KEY`는 교체된 credential만 현재 프로세스 또는 무추적 `.env`에 주입한다.
  키를 명령행, Git, 로그, 응답에 넣지 않는다.
- 서버가 뜨는 것과 공모전 품질 gate 통과는 별개다. `/health.ready=true`만으로 submission
ready를 주장하지 않는다.

## Public web UI

서버의 공개 주소 루트(`/`)를 브라우저에서 열면 공시 질문 화면이 표시된다. 별도 로그인은
없으며 주소를 아는 사용자는 누구나 질문할 수 있다. 질문 결과의 `근거 확인됨` 또는
`답변 보류` 표시와 접힌 `공시 근거 N건`을 함께 확인한다. 근거를 열면 검증기가 승인한
공시 발췌, 한글 위치, 유효한 14자리 접수번호의 DART 원문 링크가 표시된다. 임의 evidence-ID
조회 API는 제공하지 않는다.

헤더의 `공시 DB 준비됨 · N개 기업`은 현재 런타임이 질의를 받을 수 있고 N개 회사명을
검색할 수 있다는 뜻이다. 이는 N개 회사의 모든 재무 항목이 완전하다는 뜻이나 공모전 최종
제출 준비 완료를 뜻하지 않는다. `서비스 정보`에서 provider mode를 별도로 확인한다.
credential이 없으면 `검증형 기본 엔진 사용 · HyperCLOVA X 설명 미사용`, 구성되면
`HyperCLOVA X 설명 연결됨`으로 표시한다. provider 미구성만으로 DB readiness를 경고로
표시하지 않는다.

대화 목록과 메시지는 서버가 아니라 각 사용자의 브라우저 `localStorage`에만 저장된다.
같은 주소라도 다른 브라우저·기기·시크릿 창과 기록을 공유하지 않는다. 왼쪽 아래의
`기록 전체 삭제`를 누르면 해당 브라우저의 기록만 삭제된다. 서버에는 대화 계정,
로그인 세션, 서버 측 대화 기록을 만들지 않는다.

모바일에서는 헤더의 메뉴 버튼으로 기록을 연다. `Enter`는 질문 전송,
`Shift+Enter`는 줄바꿈이다. 예시 질문 버튼은 입력창만 채우며 자동 전송하지 않는다.

## Local PowerShell

```powershell
$env:PYTHONPATH=(Resolve-Path 'src').Path
python -m pip install -e ".[agent]"
powershell -ExecutionPolicy Bypass -File scripts/start-agent.ps1 -Mode Local -DataRoot 'D:\mirae-asset-project'
```

다른 터미널에서 readiness와 대표 질문을 확인한다.

```powershell
powershell -ExecutionPolicy Bypass -File scripts/smoke-agent.ps1 -BaseUrl 'http://127.0.0.1:8000'
```

중지는 서버 터미널에서 `Ctrl+C`를 누른다. 재시작은 서버를 중지한 뒤 같은 시작 명령을
다시 실행하고 smoke를 반복한다.

## Docker Compose

`.env.example`을 복사한 `.env`는 로컬에서만 사용하고 Git에 추가하지 않는다.

```powershell
Copy-Item .env.example .env
powershell -ExecutionPolicy Bypass -File scripts/start-agent.ps1 -Mode Docker -DataRoot 'D:\mirae-asset-project'
powershell -ExecutionPolicy Bypass -File scripts/smoke-agent.ps1
docker compose logs --tail 200 disclosure-agent
docker compose down
```

Compose는 base SQLite와 `db\agent`를 `:ro`로 mount하고 `/runtime`만 쓰기 가능하게 한다.
컨테이너가 unhealthy이면 질문을 보내지 말고 `/health`의 readiness와 attestation 상태를
확인한다.

## Anonymous request limits

로그인 없는 공개 서비스의 과다 사용은 다음 세 환경 변수로 제한한다. 값은 모두
`1..10000`만 허용하며 `0`, 음수, 비정수, 10000 초과 값은 시작 단계에서 거부된다.

| Variable | Default | Meaning |
|---|---:|---|
| `DISCLOSURE_PUBLIC_RATE_PER_MINUTE` | 120 | 연결 IP별 60초 이동 구간 요청 수 |
| `DISCLOSURE_PUBLIC_PER_IP_CONCURRENCY` | 4 | 연결 IP별 동시 질문 수 |
| `DISCLOSURE_PUBLIC_GLOBAL_CONCURRENCY` | 8 | 단일 서버 프로세스 전체 동시 질문 수 |

`POST /query`, 호환 경로 `POST /v1/answer`, 공식 예시 경로 `GET /answer`에 적용된다. `/`, 정적 자산, `/health`는
적용 대상이 아니다. 분당 또는 IP 동시 제한은 HTTP `429`, 서버 전체 동시 제한은
HTTP `503`과 `detail=server_busy`를 반환한다. 클라이언트는 `Retry-After` 초 이후에
재시도한다.

현재 구현은 조작 가능한 `X-Forwarded-For`를 무시하고 직접 연결된 소켓 IP만 신뢰한다.
따라서 리버스 프록시를 추가하면 모든 사용자가 프록시 IP 하나로 집계된다. 신뢰 프록시와
헤더 검증 설계를 별도로 완료하기 전에는 forwarded-header 신뢰를 켜지 않는다.

설정 확인은 비밀값이 없는 예시 파일로 렌더링할 수 있다.

```powershell
docker compose --env-file .env.example config
```

출력에서 세 제한값과 base/agent/attestation 볼륨의 `read_only: true`를 확인한다.

## Smoke the public experience

서버 시작 후 다음 명령은 공개 HTML/CSP, readiness, 검증 답변, 답변 보류, 프롬프트 주입
거절을 순서대로 확인한다. 출력은 상태·request ID·latency·boolean만 포함하며 공개 URL,
원문 공시, credential은 출력하지 않는다.

```powershell
powershell -ExecutionPolicy Bypass -File scripts/smoke-agent.ps1 -BaseUrl 'http://127.0.0.1:8000'
```

웹 화면이 없거나 CSP가 빠졌거나 어느 질문 계약이 깨지면 0이 아닌 종료 코드로 실패한다.

## Evaluation API contract

공식 예시 형식은 다음처럼 호출한다. 실제 public IP나 credential은 문서에 기록하지 않는다.

```powershell
curl.exe -G "https://<team-endpoint>/answer" `
  --data-urlencode "question_id=Q-001" `
  --data-urlencode "question=평가 질의"
```

```json
{
  "question_id": "Q-001",
  "question": "평가 질의",
  "retrieved_context": "공시명=사업보고서 | 공시일=2025-03-18 | 접수번호=20250318000001",
  "think_trace": "질의 구조화 -> 공시 검색 -> 정정·수치 검증 -> 근거 귀속",
  "answer": "검증된 최종 답변"
}
```

`retrieved_context`는 최대 20개 근거와 6,000자로 제한한다. `think_trace`는 모델의 비공개
추론을 노출하지 않고 시스템의 공개 처리 단계만 표시한다. 답변할 근거가 없으면 context는
빈 문자열이고 trace는 `정보한계 판정`으로 끝난다.

## Sparse and hybrid retrieval audit

기존 sparse FTS+RRF 지표를 유지하면서 실제 에이전트의 구조화 fact 경로를 함께 평가하려면
아래 명령을 사용한다. base, overlay, search index는 모두 read-only 입력이며 출력은 저장소의
ignored `tmp` 아래에만 쓴다.

```powershell
$env:PYTHONPATH=(Resolve-Path 'src').Path
python scripts/evaluate_retrieval.py `
  --database 'D:\mirae-asset-project\db\semantic-v1_129f5b0\disclosure_corpus_semantic_v1.sqlite' `
  --gold 'data\derived\gold_qa.agent_audited.jsonl' `
  --overlay 'D:\mirae-asset-project\db\agent\agent_overlay.sqlite' `
  --attestation 'data\derived\database_distribution_manifest_semantic_v1.json' `
  --search-index 'D:\mirae-asset-project\db\agent\agent_search.sqlite' `
  --financial-seed 'data\derived\financial_fact_gold_seed.jsonl' `
  --inventory-output 'tmp\financial_fact_coverage_audit.json' `
  --output 'tmp\hybrid_retrieval_audit.json' `
  --limit 20
```

2026-08-20 read-only 실행에서 sparse Recall@20은 `13/17`(`0.7647`)로 그대로였고 hybrid는
`17/17`(`1.0`)이었다. 요청 K는 20이고 최종 safe evidence bundle의 유효 K는 8이다.
hybrid 경로 귀속은 `structured_financial=8`,
`structured_event=7`, `sparse_text=2`이며 service error와 residual target은 0이었다.
검증 seed는 8/8 Gold 일치지만 이는 **checked-in seed 범위**일 뿐 full-corpus
`financial_fact` 완성을 뜻하지 않는다. sparse 지표를 hybrid 값으로 대체하거나 hard gate를
낮추지 않는다.

Hybrid CLI는 base/Gold/overlay/attestation/search/seed와 두 output 경로의 충돌을 시작 전에
거부한다. overlay·attestation·search는 세 경로를 모두 제공하거나 모두 생략해야 한다.
base/overlay/search attestation 및 search SQLite fatal reason이 bundle에 반환되면 hybrid
status는 `failed_closed`, recall은 `null`이며 sparse fallback을 hybrid 통과로 계산하지 않는다.

### BGE-M3 Dense Smoke와 RRF Hybrid retrieval v1

`src/disclosure_db/hybrid_retrieval.py`는 기존 에이전트나 Tool Registry에 연결하지 않은 retrieval
모듈이다. `SparseRetriever`는 attested read-only `SafeSearchIndex.search()`를 그대로 호출하므로
Unicode/trigram FTS, 기존 내부 RRF, 회사·공시·시점·정정 정책을 중복 구현하거나 완화하지 않는다.
`DenseFaissRetriever`는 `BAAI/bge-m3`로 질문을 embedding하고 L2 정규화한 다음, L2 정규화가
검증된 `IndexFlatIP`에 inner-product 검색을 수행한다. `HybridRetriever`는 sparse와 dense 순위에
각각 `1 / (60 + rank)`를 더하는 RRF를 적용한다. 중복 키는 `chunk_id`가 있으면 이를 우선하고,
없을 때만 `evidence_id`를 사용한다.

현재 dense 산출물 계약은 **정확히 100개 chunk만 포함한 `smoke_only` index**다. 로드 시 index
개수와 metadata 행 수, 0부터 증가하는 `vector_id`, 차원, 모든 저장 벡터의 L2 norm을 검증한다.
이는 파일 연결·필터·fallback 검증용이며 전체 corpus recall, latency 또는 검색 품질을 증명하지
않는다. 기본 파일은 `data/derived/bge_m3_dense_pilot/index.faiss`와
`chunk_metadata.jsonl`이고 Git에 포함하지 않는다.

| 환경변수 | 기본값/의미 |
|---|---|
| `DISCLOSURE_DENSE_INDEX` | Smoke `index.faiss` 경로 |
| `DISCLOSURE_DENSE_METADATA` | Smoke `chunk_metadata.jsonl` 경로 |
| `DISCLOSURE_DENSE_MODEL` | `BAAI/bge-m3`; 로컬 모델 디렉터리로 교체 가능 |
| `DISCLOSURE_DENSE_MODEL_REVISION` | 선택적 Hugging Face revision |
| `DISCLOSURE_DENSE_MAX_LENGTH` | `1024` |
| `DISCLOSURE_DENSE_CORPUS_STATUS` | 현재 `smoke_only` |
| `DISCLOSURE_DENSE_EXPECTED_CORPUS_SIZE` | 현재 `100`; `none`이면 개수 고정 해제 |

Dense metadata에 요청된 회사, `filing_id`, `filed_at`, `as_of` 또는 정정 계보 필드가 없으면 해당
필터는 추정하지 않고 dense 결과를 제외한다. Dense 파일 부재, FAISS/metadata 검증 실패, 모델·검색
오류, 또는 필터 후 결과 0건이면 Hybrid는 이미 실행된 Sparse 결과를 `sparse_fallback`으로
반환한다. Dense 예외가 `SafeSearchIndex` 결과를 폐기하지 않는다. 반환값은 `hits` 외에
`dense_status`, `dense_corpus_size`, `fallback_used`, `retrieval_mode`, `smoke_only`를 포함한다.
정상 Smoke 결합은 `dense_status=smoke_only`, `retrieval_mode=hybrid_rrf`이며, 필터 후 0건은
`dense_status=smoke_only_no_filtered_results`, `retrieval_mode=sparse_fallback`이다.

```powershell
$env:PYTHONPATH=(Resolve-Path 'src').Path
$env:DISCLOSURE_DENSE_INDEX='D:\mirae-asset-project\runs\dense-smoke\index.faiss'
$env:DISCLOSURE_DENSE_METADATA='D:\mirae-asset-project\runs\dense-smoke\chunk_metadata.jsonl'
$env:DISCLOSURE_DENSE_CORPUS_STATUS='smoke_only'
$env:DISCLOSURE_DENSE_EXPECTED_CORPUS_SIZE='100'
python -m unittest discover -s tests -p 'test_hybrid_retrieval.py' -v
python -m unittest discover -s tests -p 'test_search_index.py' -v
```

전체 Dense index로 교체할 때는 chunk-v1 전체 corpus로 새 출력 디렉터리를 생성·검증한 뒤 두 경로를
함께 바꾸고, `DISCLOSURE_DENSE_CORPUS_STATUS=full_corpus`와 실제 검증 개수를 설정한다. 그 후 별도의
전체 corpus retrieval 평가와 latency gate를 통과하기 전에는 성능 개선을 주장하거나 production
기본 경로로 승격하지 않는다.

## Provider-required 300-case gate

기본 300문항 실행은 `--provider-mode disabled`이며 비용 없이 결정론적 회귀를 검증한다.
최종 제출용 실행은 교체된 credential을 승인된 프로세스 secret으로 주입한 뒤 아래처럼
별도 출력 경로에서 실행한다.

```powershell
$env:PYTHONPATH=(Resolve-Path 'src').Path
python scripts/evaluate_agent_stress.py `
  --database 'D:\mirae-asset-project\db\semantic-v1_129f5b0\disclosure_corpus_semantic_v1.sqlite' `
  --overlay 'D:\mirae-asset-project\db\agent\agent_overlay.sqlite' `
  --search-index 'D:\mirae-asset-project\db\agent\agent_search.sqlite' `
  --attestation 'data\derived\database_distribution_manifest_semantic_v1.json' `
  --cases 'D:\mirae-asset-project\runs\evaluation\agent_stress_300.jsonl' `
  --contract 'config\stress_evaluation_contract.json' `
  --run-root 'D:\mirae-asset-project\runs\evaluation\provider' `
  --summary 'D:\mirae-asset-project\runs\evaluation\provider_summary.json' `
  --failures 'D:\mirae-asset-project\runs\evaluation\provider_failures.jsonl' `
  --provider-mode required
```

required 모드는 provider 미구성 시 첫 문항 전에 종료한다. summary의
`provider_gate_passed=true`는 provider 구성, 300/300, 기존 hard/quality gate와 p95 10초를
모두 통과했을 때만 가능하다. 키·질문 본문·provider 원문 응답은 summary/checkpoint에 쓰지
않으며, provider-disabled checkpoint를 required 실행에 재사용하지 않는다. 실제 300회 호출은
비용이 발생하므로 credential·예산·모델을 확인한 승인 실행에서만 수행한다.

## NCP deployment

NCP Server에는 최소 80GB 데이터 볼륨을 `/srv/mirae`로 mount한다.

| Host path | Container path | Mode |
|---|---|---|
| `/srv/mirae/data/base/disclosure.sqlite` | `/data/base/disclosure.sqlite` | read-only |
| `/srv/mirae/data/agent` | `/data/agent` | read-only |
| `/srv/mirae/data/attestation.json` | `/data/attestation.json` | read-only |
| `/srv/mirae/runtime` | `/runtime` | read-write |

1. 검증된 base size/SHA-256/mtime과 overlay/index hash를 별도 전송 경로에서 비교한다.
2. ACG inbound는 필요한 공개 HTTP 포트만 허용하고 SSH는 관리자 IP로 제한한다.
3. 승인된 secret 환경을 서버 프로세스에 주입하고 Compose를 시작한다.
4. 서버 네트워크 밖의 두 번째 네트워크에서 `/health`와 `/query`를 호출한다.
5. Docker 재시작 또는 서버 재부팅 후 readiness와 대표 질문을 다시 확인한다.

NCP 계정, public IP, ACG, 데이터 볼륨, 외부 네트워크가 확인되기 전에는 공개 endpoint
완료나 최종 submission ready를 주장하지 않는다. 임시 tunnel은 팀 demo에만 사용한다.

공개 화면과 로컬 deterministic 스모크가 통과해도 HyperCLOVA X 교체 credential로
provider smoke와 300-case 평가를 통과하기 전까지 최종 제출 상태는 **NO-GO**다. 이 gate를
낮추거나 deterministic 결과로 대체하지 않는다.

## Fail-closed release deployment

배포는 순환 의존을 막기 위해 두 단계로 실행한다. `scripts/deploy_staging.ps1`은 최종
`release-gate-summary-v1`을 입력으로 요구하지 않는다. 대신 외부에서 전달한 full commit과
세 data SHA-256을 trust anchor로 삼아 최신 financial 856건, retrieval Recall@20 95%,
Judge 480+120 입력을 각각 재검사한다. 이 **pre-stage** 결과는
`stage_state=READY_FOR_STAGING`, `final_release_passed=false`이며 최종 PASS나 production 승인을
뜻하지 않는다. 현재 저장소의 stale/Recall 미달 보고서는 이 단계에서 **BLOCKED**되고 Docker
build, SSH, SCP를 실행하지 않는다.

pre-stage를 통과하면 `ExpectedCommit`의 `git archive`에서 Dockerfile과 모든 tracked
`src`·`config`·`eval` build input을 임시 context로 추출한다. 현재 working tree를 재귀 복사하지
않으므로 tracked 수정과 untracked/ignored 파일은 이미지에 들어갈 수 없다. 유일한 별도 입력인
retrieval 보고서는 운영자가 전달한 `ExpectedRetrievalReportSha256`과 일치해야 하며, build
context와 배포 묶음에 넣은 뒤에도 같은 hash를 다시 확인한다. private holdout은 Git archive,
build context, image, 배포 archive 어디에도 복사하지 않는다. agent 이미지는 정확히 한 번 build하고 archive/hash를 만든 다음, 서버에서
`docker compose ... up --no-build`로 8001에만 기동한다. 이때 DB·overlay·search·attestation과
Dense data/model mount가 `RW=false`인지, 컨테이너 image ID와 서버 data hash가 trust anchor와
같은지 검사한다.

그 다음 로컬의 bounded evaluator가 실제 8001에만 요청한다. 요청별 timeout과 응답 크기를
제한하고, development 480건과 Git-ignored private holdout 120건, concurrency 20, 필수
security/provider 관측을 수행한다. `staging_evaluation.json`에는 case/hash/metric/counter만
남기며 원 질문, 답변, provider 응답, credential은 남기지 않는다. 평가 전후 실제 8001
`/health.identity`는 `commit`, `image_id`, `base_sha256`, `overlay_sha256`,
`search_index_sha256` 다섯 필드만 허용하며 외부 trust anchor와 정확히 일치해야 한다. 필드가
없거나 더 있거나 값이 다르면 즉시 실패한다. evaluator가 local trust 값을 runtime identity로
덮어쓰지 않으며, 평가가 끝난 뒤 서버 내부 localhost `/health` identity hash와 컨테이너 image,
세 data hash를 다시 묶어 검사해 endpoint 또는 자산이 평가 도중 교체되지 않았음을 확인한다.
현재 API의 `/health`가 이 identity 계약을 제공하지 않으면 staging은 의도적으로 fail-closed되며
production 승격 근거를 만들 수 없다.

마지막으로 `scripts/evaluate_release_candidate.py`가 external trust anchor, financial,
retrieval, 방금 생성한 staging 결과로 최종 gate를 재계산한다. 여기서 생성된
`release-gate-summary-v1`이 `hard_gate_passed=true`, `release_state=PASS`, 실제 빈
`hard_gate_reasons=[]`일 때만 별도의 `scripts/deploy_release.ps1` 입력으로 사용할 수 있다.
staging 스크립트 자체는 8000을 변경하지 않으며 출력의 `production_promoted=false`도 이를
명시한다.

PASS 표시 자체는 신뢰하지 않는다. `hard_gate_reasons`는 JSON `null`이 아닌 실제 빈 배열이어야
하며, 27개 필수 metric을 각각 숫자 타입인지 확인한 뒤 계약의 exact/minimum/maximum 기준을
스크립트가 다시 검사한다. `metrics={}`, 필드 누락, 숫자 모양 문자열, `NaN`/무한대 및 임계치
위반은 모두 외부 명령 전에 거부한다. `evaluated_at_utc`와 네 source timestamp는 UTC offset이
있는 ISO-8601이어야 하며 24시간 freshness와 5분 future skew를 만족해야 한다. Windows
PowerShell 5.1의 문자열 역직렬화와 PowerShell 7의 `DateTime` 역직렬화를 모두 허용하되 같은
시간·freshness 규칙을 적용한다.

최종 승인 보고서의 `identity`에는 full Git commit, staging에서 실제 build·inspect한 Docker
`sha256:` image ID와 아래 3개 SHA-256이 있어야 한다. 명령행 trust anchor와 한 항목이라도
다르면 production을 변경하지 않는다.

| Identity field | 실제 검증 대상 |
|---|---|
| `base_sha256` | `/srv/mirae/data/base/disclosure.sqlite` |
| `overlay_sha256` | `/srv/mirae/data/agent/agent_overlay.sqlite` |
| `search_index_sha256` | `/srv/mirae/data/agent/agent_search.sqlite` |

배포 묶음에는 image archive, release compose, `pre_stage_attestation.json`과 Dockerfile이
요구하는 `data/derived/freeform_retrieval_summary.json`만 포함한다. private holdout과 최종 gate는
포함하지 않는다. 최종 gate는 8001 평가 후 로컬 artifact directory에만 생성한다.

Production 승격은 8001에서 검사된 것과 **동일한 image ID**가 실행 중일 때만 시작한다.
기존 8000 image에는 UTC·image ID가 포함된 고유 rollback tag를 붙이고, 전환 전에 rollback
archive와 SHA-256을 생성해 `0444`로 만든다. 이후에도 `--no-build`로 정확히 같은 후보 image를
8000에 지정한다. image identity, mount 또는 `/health`·공개 루트 smoke가 실패하면
`deploy_release.ps1`의 catch 경로가 `Invoke-Rollback`을 호출하여 이전 image를 다시 기동하고
rollback smoke까지 확인한다.

두 스크립트는 credential, `.env`, PEM 내용을 읽거나 수정하지 않는다. SSH 인증 수단과
provider secret은 실행 환경이 별도로 제공하며 Git·배포 archive·출력에 포함하지 않는다.
실제 호출은 PASS 보고서와 세 data identity, full commit, image ID를 확보한 운영자가
명시적으로 실행할 때만 허용한다.

`RemoteDirectory`와 `RollbackDirectory`는 canonical absolute Unix path만 허용한다. `.`·`..`
component, 중복 `/`, trailing `/`가 있으면 경로 순회 가능성이 있으므로 배포를 시작하지 않는다.
rollback tag/image가 없거나 rollback 컨테이너가 없거나 활성 image ID가 rollback image ID와
다르거나 `/health.ready` 및 공개 루트 smoke 중 하나라도 실패하면 rollback도 실패로 처리한다.
rollback 검증 실패를 성공으로 바꾸거나 원래 promotion 오류를 숨기지 않는다.

## Diagnosis and rollback

- `/health.ready=false`: base identity, attestation, overlay match, search-index revision을
  확인하고 질문 endpoint가 `503`인지 확인한다.
- `runtime_not_ready`: 파일 경로 또는 read-only 자산의 상태를 복구한 뒤 서버를 재시작한다.
- search index drift/corruption: live 파일을 지우지 말고 검증된 이전 파일 또는 staging
  candidate로 recoverable rename을 수행한다.
- overlay rebuild: `D:\mirae-asset-project\staging`에서 build·quick_check·대표 fact query를
  통과시킨 뒤에만 `.previous.sqlite`를 남기는 rename으로 승격한다.
- 원본 DB는 절대 제자리에서 rebuild하거나 덮어쓰지 않는다.
