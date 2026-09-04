# NCP Team QA Deployment Implementation Plan

> **For agentic workers:** 이 계획은 `/executing-plans`(이 데크)로 태스크 단위 실행한다. Steps use checkbox (`- [ ]`) syntax for tracking. 구현 주체는 lead — 기계적 반복 작업만 `coder` 위임(AGENTS §3·PIPELINE 구현행), 서브에이전트는 탐색·검증·병렬 기계작업만.

**Goal:** 기존 NCP `mirae-contest-agent` 서버에 현재 `/lab` QA·Gold 화면을 팀 전용 Tailscale 주소로 배포하고, 기존 공인 `:8000` 서비스와 불변 데이터 자산을 변경하지 않는다.

**Architecture:** 기존 공인 컨테이너는 그대로 유지한다. 같은 소스에서 별도 Compose 프로젝트를 `:8001`에 기동하되 NCP ACG에는 8001을 열지 않고, 서버가 참여한 Tailscale 네트워크를 통해서만 접근한다. 기준 DB·오버레이·검색 인덱스·인증서는 읽기 전용으로 재사용하고 `/srv/mirae/runtime/qa_lab.sqlite`만 쓰기 가능하게 둔다.

**Tech Stack:** Docker Compose, FastAPI, SQLite WAL, Tailscale, NAVER Cloud VPC Server/ACG

## Global Constraints

- 기존 공인 `101.79.31.221:8000` 컨테이너와 ACG의 공개 8000 규칙을 변경하지 않는다.
- 기준 DB·오버레이·검색 인덱스·인증서는 컨테이너와 호스트 모두 읽기 전용을 유지한다.
- QA 원장만 `/srv/mirae/runtime/qa_lab.sqlite`에 기록한다.
- NCP ACG에 TCP 8001 공개 인바운드 규칙을 추가하지 않는다.
- 10.5GB `IndexFlatIP` 임베딩 인덱스는 8GB VM에 적재하지 않으며 이번 배포 범위에서 제외한다.
- 무관한 dirty worktree 변경을 되돌리거나 커밋하지 않는다.
- 공급 실패나 인증 실패를 성공으로 보고하지 않는다.

---

### Task 1: 팀 전용 Compose 계약

**Files:**
- Create: `compose.team-qa.yaml`
- Modify: `tests/test_deployment_artifacts.py`

**Interfaces:**
- Consumes: 기존 `.env`의 `DISCLOSURE_BASE_DB_HOST`, `DISCLOSURE_AGENT_DB_DIR_HOST`, `DISCLOSURE_ATTESTATION_HOST`, `DISCLOSURE_RUNTIME_DIR_HOST`, `CLOVASTUDIO_API_KEY`
- Produces: `docker compose -f compose.team-qa.yaml -p mirae-team-qa up --build -d`로 실행되는 `team-qa` 서비스

- [x] **Step 1: 실패하는 배포 계약 테스트 작성**

```python
def test_team_qa_compose_keeps_data_read_only_and_uses_nonpublic_port(self):
    compose = Path("compose.team-qa.yaml").read_text(encoding="utf-8")
    self.assertIn('"8001:8000"', compose)
    self.assertIn("DISCLOSURE_QA_DB: /runtime/qa_lab.sqlite", compose)
    self.assertIn("${DISCLOSURE_BASE_DB_HOST}:/data/base/disclosure.sqlite:ro", compose)
    self.assertIn("${DISCLOSURE_AGENT_DB_DIR_HOST}:/data/agent:ro", compose)
    self.assertIn("${DISCLOSURE_ATTESTATION_HOST}:/data/attestation.json:ro", compose)
    self.assertNotIn("0.0.0.0/0", compose)
```

- [x] **Step 2: 테스트가 파일 부재로 실패하는지 확인**

Run: `python -m unittest tests.test_deployment_artifacts -v`

Expected: `compose.team-qa.yaml` 부재로 FAIL.

- [x] **Step 3: 독립 Compose 파일 작성**

```yaml
services:
  team-qa:
    build: .
    restart: unless-stopped
    ports: ["8001:8000"]
    environment:
      DISCLOSURE_BASE_DB: /data/base/disclosure.sqlite
      DISCLOSURE_OVERLAY_DB: /data/agent/agent_overlay.sqlite
      DISCLOSURE_SEARCH_DB: /data/agent/agent_search.sqlite
      DISCLOSURE_ATTESTATION: /data/attestation.json
      DISCLOSURE_QA_DB: /runtime/qa_lab.sqlite
      CLOVASTUDIO_API_KEY: ${CLOVASTUDIO_API_KEY:-}
      DISCLOSURE_PUBLIC_RATE_PER_MINUTE: ${DISCLOSURE_PUBLIC_RATE_PER_MINUTE:-120}
      DISCLOSURE_PUBLIC_PER_IP_CONCURRENCY: ${DISCLOSURE_PUBLIC_PER_IP_CONCURRENCY:-4}
      DISCLOSURE_PUBLIC_GLOBAL_CONCURRENCY: ${DISCLOSURE_PUBLIC_GLOBAL_CONCURRENCY:-8}
    volumes:
      - "${DISCLOSURE_BASE_DB_HOST}:/data/base/disclosure.sqlite:ro"
      - "${DISCLOSURE_AGENT_DB_DIR_HOST}:/data/agent:ro"
      - "${DISCLOSURE_ATTESTATION_HOST}:/data/attestation.json:ro"
      - "${DISCLOSURE_RUNTIME_DIR_HOST}:/runtime"
    healthcheck:
      test: ["CMD", "python", "-c", "import json,urllib.request; assert json.load(urllib.request.urlopen('http://127.0.0.1:8000/health'))['ready']"]
      interval: 30s
      timeout: 30s
      retries: 5
      start_period: 180s
```

- [x] **Step 4: Compose 렌더링과 배포 테스트 확인**

Run: `docker compose --env-file .env.example -f compose.team-qa.yaml config`

Expected: host 8001만 게시되고 base/agent/attestation mount가 `read_only: true`로 렌더링됨.

Run: `python -m unittest tests.test_deployment_artifacts -v`

Expected: PASS.

### Task 2: 팀 배포·검증 절차 문서화

**Files:**
- Modify: `docs/operations/qa-lab.md`

**Interfaces:**
- Consumes: Task 1의 `compose.team-qa.yaml`
- Produces: NCP 로그인 이후 그대로 실행할 수 있는 설치·배포·롤백 명령

- [x] **Step 1: 현재 공개 노출 문구를 팀 전용 경계로 교체**

문서에 다음 불변식을 기록한다.

```text
공인 IP의 8001은 NCP ACG에서 열지 않는다.
팀원은 `http://<NCP_TAILSCALE_IP>:8001/lab`로만 접속한다.
기존 `http://101.79.31.221:8000` 서비스는 변경하지 않는다.
```

- [x] **Step 2: 원격 실행 명령 기록**

```bash
curl -fsSL https://tailscale.com/install.sh | sh
tailscale up
cd /srv/mirae/app-team-qa
docker compose -f compose.team-qa.yaml -p mirae-team-qa up --build -d
docker compose -f compose.team-qa.yaml -p mirae-team-qa ps
```

- [x] **Step 3: 검증·롤백 명령 기록**

```bash
curl -fsS http://127.0.0.1:8001/health
curl -fsS -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8001/lab
docker inspect mirae-team-qa-team-qa-1 --format '{{json .Mounts}}'
docker compose -f compose.team-qa.yaml -p mirae-team-qa down
```

- [x] **Step 4: 문서와 작업트리 정합성 확인**

Run: `rg -n "8001|Tailscale|compose.team-qa" docs/operations/qa-lab.md compose.team-qa.yaml`

Expected: 공개 8001 허용 지시가 없고 Tailscale URL·롤백 명령이 존재함.

### Task 3: 기존 NCP 서버 배포

**Files:**
- Remote create: `/srv/mirae/app-team-qa`
- Remote create: `/srv/mirae/runtime/qa_lab.sqlite`

**Interfaces:**
- Consumes: 현재 worktree의 코드 전용 아카이브, 기존 원격 `.env`, Task 1 Compose 계약
- Produces: `http://<NCP_TAILSCALE_IP>:8001/lab`

- [ ] **Step 1: NCP 콘솔 로그인 후 SSH ACG를 현재 관리자 `/32`로 갱신**

기존 TCP 22의 오래된 `/32`를 현재 관리자 공인 IP `/32`로 교체하고 TCP 8001 규칙은 만들지 않는다.

- [ ] **Step 2: 원격 불변 자산과 공인 서비스 baseline 캡처**

Run remotely: `stat -c '%n %s %Y %a' /srv/mirae/data/base/disclosure_corpus_semantic_v1.sqlite /srv/mirae/data/agent/agent_overlay.sqlite /srv/mirae/data/agent/agent_search.sqlite /srv/mirae/data/attestation.json && docker ps --format '{{.Names}} {{.Status}} {{.Ports}}'`

Expected: 네 데이터 자산 mode `444`, 기존 공인 컨테이너 healthy.

- [ ] **Step 3: Tailscale 설치·로그인 후 tailnet IP 확인**

Run remotely: `tailscale status && tailscale ip -4`

Expected: NCP 서버가 팀 tailnet에 연결되고 `100.x.y.z` 주소가 반환됨.

- [ ] **Step 4: 코드 전용 아카이브 전송·검사·원자적 배치**

아카이브에는 `.env`, DB/SQLite, PEM/key, `runs`, `runtime`, `tmp`, `team-qa/node_modules`, 임베딩 파일을 포함하지 않는다. 전송 후 원격 staging에서 목록을 재검사하고 `/srv/mirae/app-team-qa`로 승격한다.

- [ ] **Step 5: 별도 QA Compose 프로젝트 기동**

Run remotely: `cd /srv/mirae/app-team-qa && docker compose --env-file /srv/mirae/app/.env -f compose.team-qa.yaml -p mirae-team-qa up --build -d`

Expected: 기존 공인 컨테이너와 별도로 team QA 컨테이너 healthy.

- [ ] **Step 6: 공개·팀 경로와 mount 경계 검증**

Expected:
- `http://101.79.31.221:8000/health`는 계속 200.
- `http://101.79.31.221:8001`은 외부에서 접속 불가.
- `http://<NCP_TAILSCALE_IP>:8001/lab`은 팀 tailnet에서 200.
- base/agent/attestation mount는 `rw=false`, runtime만 `rw=true`.
- Step 2의 네 불변 자산 크기·mtime·mode가 동일함.

- [ ] **Step 7: 팀원 1명 외부 기기 접속 확인**

팀원은 Tailscale 로그인 후 `/lab`에서 이름을 입력하고 질문을 실행한다. QA 판정은 실제 검수 결과만 저장하며 가짜 smoke 레코드는 만들지 않는다.
