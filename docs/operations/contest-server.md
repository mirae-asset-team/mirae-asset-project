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

## Diagnosis and rollback

- `/health.ready=false`: base identity, attestation, overlay match, search-index revision을
  확인하고 질문 endpoint가 `503`인지 확인한다.
- `runtime_not_ready`: 파일 경로 또는 read-only 자산의 상태를 복구한 뒤 서버를 재시작한다.
- search index drift/corruption: live 파일을 지우지 말고 검증된 이전 파일 또는 staging
  candidate로 recoverable rename을 수행한다.
- overlay rebuild: `D:\mirae-asset-project\staging`에서 build·quick_check·대표 fact query를
  통과시킨 뒤에만 `.previous.sqlite`를 남기는 rename으로 승격한다.
- 원본 DB는 절대 제자리에서 rebuild하거나 덮어쓰지 않는다.
