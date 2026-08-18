# Contest server runbook

이 문서는 공모전 호환 `POST /query` 서버의 로컬·Docker·NCP 실행 절차다. 원본 SQLite,
agent overlay, sparse search index는 서비스와 운영자가 읽기 전용으로 취급한다. `runs`와
`staging`만 쓰기 대상으로 둔다.

## Prerequisites and warnings

- Python 3.11 이상, 검증된 D-drive DB 세 파일과 distribution attestation이 필요하다.
- Docker 실행은 Docker Desktop 또는 Linux Docker Engine이 필요하다.
- `CLOVASTUDIO_API_KEY`는 교체된 credential만 현재 프로세스 또는 무추적 `.env`에 주입한다.
  키를 명령행, Git, 로그, 응답에 넣지 않는다.
- 서버가 뜨는 것과 공모전 품질 gate 통과는 별개다. `/health.ready=true`만으로 submission
  ready를 주장하지 않는다.

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

## Diagnosis and rollback

- `/health.ready=false`: base identity, attestation, overlay match, search-index revision을
  확인하고 질문 endpoint가 `503`인지 확인한다.
- `runtime_not_ready`: 파일 경로 또는 read-only 자산의 상태를 복구한 뒤 서버를 재시작한다.
- search index drift/corruption: live 파일을 지우지 말고 검증된 이전 파일 또는 staging
  candidate로 recoverable rename을 수행한다.
- overlay rebuild: `D:\mirae-asset-project\staging`에서 build·quick_check·대표 fact query를
  통과시킨 뒤에만 `.previous.sqlite`를 남기는 rename으로 승격한다.
- 원본 DB는 절대 제자리에서 rebuild하거나 덮어쓰지 않는다.
