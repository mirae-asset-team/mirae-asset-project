# Contest API and Deployment Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** D드라이브의 검증된 SQLite 세 파일을 read-only로 사용하는 공모전 호환 FastAPI 서버를 로컬과 Docker에서 재현 가능하게 실행한다.

**Architecture:** 기존 `DisclosureAgent`와 `/v1/*` endpoint를 유지하고, 환경변수 기반 runtime factory와 얇은 `POST /query` 어댑터를 추가한다. 38.8GB DB는 이미지에 넣지 않고 host volume으로 연결하며 startup readiness가 실패하면 질문 endpoint를 `503`으로 닫는다.

**Tech Stack:** Python 3.11+, FastAPI 0.115+, Uvicorn 0.30+, SQLite read-only URI, Docker Compose, PowerShell, `unittest`.

**Spec:** `docs/superpowers/specs/2026-08-19-contest-server-mvp-design.md`

## Global Constraints

- 원본 DB는 `D:\mirae-asset-project\db\semantic-v1_129f5b0\disclosure_corpus_semantic_v1.sqlite`이며 서버가 수정하면 안 된다.
- overlay와 search index를 설정할 때 attestation은 필수다.
- 전체 DB SHA-256은 request/startup 경로에서 계산하지 않는다.
- secrets는 `CLOVASTUDIO_API_KEY` 등 환경변수로만 주입하고 Git·로그·응답에 남기지 않는다.
- FastAPI는 기존 `agent` optional dependency에만 둔다.
- 새로운 queue, cache, database framework, reverse proxy는 추가하지 않는다.
- 모든 기능 변경은 실패 테스트 → 최소 구현 → 통과 테스트 → 커밋 순서를 지킨다.

## File map

| 경로 | 책임 |
|---|---|
| `src/disclosure_db/runtime.py` | 환경변수 검증, `RuntimeConfig`, agent factory |
| `src/disclosure_db/api.py` | 공모전 `/query`, health/readiness, response mapping |
| `src/disclosure_db/cli.py` | 환경변수 또는 명시 인자를 사용하는 `serve` |
| `tests/test_runtime_config.py` | 경로·secret·readiness 설정 단위 테스트 |
| `tests/test_agent_runtime.py` | FastAPI route/response/fail-closed 테스트 |
| `tests/test_cli.py` | serve 설정 우선순위 테스트 |
| `tests/test_deployment_artifacts.py` | Docker/Compose/PowerShell 정적 계약 테스트 |
| `Dockerfile`, `compose.yaml`, `.dockerignore`, `.env.example` | 외부 DB volume 기반 실행 패키지 |
| `scripts/start-agent.ps1`, `scripts/smoke-agent.ps1` | D드라이브 시작과 smoke test |
| `docs/operations/contest-server.md` | 팀/NCP 운영 절차 |

---

### Task 1: Add an environment-backed runtime factory

**Files:**
- Create: `src/disclosure_db/runtime.py`
- Create: `tests/test_runtime_config.py`

**Interfaces:**
- Consumes: `AgentSettings`, `DisclosureAgent`, `Path`, `Mapping[str, str]`
- Produces: `RuntimeConfig.from_env(env=None)`, `RuntimeConfig.validate()`, `RuntimeConfig.provider_configured`, `RuntimeConfig.to_agent_settings()`, `build_agent(config)`

- [x] **Step 1: Write failing config tests**

```python
class RuntimeConfigTests(unittest.TestCase):
    def test_from_env_requires_all_three_databases_and_attestation(self):
        with self.assertRaisesRegex(ValueError, "DISCLOSURE_BASE_DB"):
            RuntimeConfig.from_env({})

    def test_validate_accepts_existing_readable_files(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            files = {name: root / name for name in ("base.sqlite", "overlay.sqlite", "search.sqlite", "attestation.json")}
            for path in files.values():
                path.write_bytes(b"x")
            config = RuntimeConfig.from_env({
                "DISCLOSURE_BASE_DB": str(files["base.sqlite"]),
                "DISCLOSURE_OVERLAY_DB": str(files["overlay.sqlite"]),
                "DISCLOSURE_SEARCH_DB": str(files["search.sqlite"]),
                "DISCLOSURE_ATTESTATION": str(files["attestation.json"]),
            })
            self.assertEqual(config.port, 8000)
            self.assertFalse(config.provider_configured)
            config.validate()
```

- [x] **Step 2: Run the tests and confirm the missing module failure**

Run: `python -m unittest tests.test_runtime_config -v`

Expected: `ModuleNotFoundError: disclosure_db.runtime`.

- [x] **Step 3: Implement the minimal runtime contract**

```python
@dataclass(frozen=True, slots=True)
class RuntimeConfig:
    base_database: Path
    overlay_database: Path
    search_database: Path
    attestation_path: Path
    host: str = "0.0.0.0"
    port: int = 8000
    clovastudio_api_key: str | None = None

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "RuntimeConfig":
        values = os.environ if env is None else env
        required = {
            "base_database": "DISCLOSURE_BASE_DB",
            "overlay_database": "DISCLOSURE_OVERLAY_DB",
            "search_database": "DISCLOSURE_SEARCH_DB",
            "attestation_path": "DISCLOSURE_ATTESTATION",
        }
        missing = [name for name in required.values() if not values.get(name)]
        if missing:
            raise ValueError("missing environment variables: " + ",".join(missing))
        return cls(
            **{field: Path(values[name]) for field, name in required.items()},
            host=values.get("DISCLOSURE_HOST", "0.0.0.0"),
            port=int(values.get("DISCLOSURE_PORT", "8000")),
            clovastudio_api_key=values.get("CLOVASTUDIO_API_KEY") or None,
        )

    @property
    def provider_configured(self) -> bool:
        return bool(self.clovastudio_api_key)

    def validate(self) -> None:
        for path in (self.base_database, self.overlay_database, self.search_database, self.attestation_path):
            if not path.is_file():
                raise ValueError(f"runtime file missing: {path}")

    def to_agent_settings(self) -> AgentSettings:
        return AgentSettings(
            base_database=self.base_database,
            overlay_database=self.overlay_database,
            search_database=self.search_database,
            attestation_path=self.attestation_path,
        )

def build_agent(config: RuntimeConfig) -> DisclosureAgent:
    config.validate()
    return DisclosureAgent(config.to_agent_settings())
```

Do not read or expose the API key after constructing provider adapters; `provider_configured` is the only public status.

- [x] **Step 4: Run focused and baseline tests**

Run: `python -m unittest tests.test_runtime_config tests.test_attestation -v`

Expected: PASS.

- [x] **Step 5: Commit**

```powershell
git add src/disclosure_db/runtime.py tests/test_runtime_config.py
git commit -m "feat: add contest runtime configuration"
```

### Task 2: Add the competition-compatible `/query` adapter

**Files:**
- Modify: `src/disclosure_db/api.py` in `create_app`
- Modify: `tests/test_agent_runtime.py`

**Interfaces:**
- Consumes: `DisclosureAgent.answer(question, company=None, as_of=None, limit=20)` and `_health_status`
- Produces: `POST /query`; response fields `question_id`, `answer`, `answerable`, `verified`, `evidence`, `reason_codes`, `request_id`, `corpus_revision`, `latency_ms`

- [x] **Step 1: Install the existing API extra in the active virtual environment**

Run: `python -m pip install -e ".[agent]"`

Expected: FastAPI and Uvicorn import successfully. Do not add a new dependency group.

- [x] **Step 2: Write failing route tests**

```python
class ReadyService:
    base_database = existing_base_fixture
    overlay_database = None
    search_database = None
    attestation = None
    corpus_revision = "test-revision"

    def company_candidates(self):
        return ["테스트"]

class FakeAgent:
    evidence_service = ReadyService()

    def answer(self, question, **kwargs):
        return VerifiedAnswer(
            answer="답", citation_ids=["ev1"], verified=True, answerable=True,
            citations=[CitationRef("ev1", "f1", report_name="사업보고서")],
        )

def test_contest_query_echoes_question_id_and_maps_citations(self):
    client = TestClient(create_app(FakeAgent()))
    response = client.post("/query", json={"question_id": "q-1", "question": "테스트 질문"})
    self.assertEqual(response.status_code, 200)
    body = response.json()
    self.assertEqual(body["question_id"], "q-1")
    self.assertEqual(body["evidence"][0]["receipt_no"], "f1")
    self.assertTrue(body["verified"])

def test_contest_query_rejects_empty_and_oversized_questions(self):
    client = TestClient(create_app(FakeAgent()))
    self.assertEqual(client.post("/query", json={"question": ""}).status_code, 422)
    self.assertEqual(client.post("/query", json={"question": "가" * 4001}).status_code, 422)
```

At test setup, create `existing_base_fixture` as an empty temporary file so `_health_status` sees an existing unattested base.

- [x] **Step 3: Run both tests and confirm `/query` is 404**

Run: `python -m unittest tests.test_agent_runtime.AgentRuntimeTests.test_contest_query_echoes_question_id_and_maps_citations tests.test_agent_runtime.AgentRuntimeTests.test_contest_query_rejects_empty_and_oversized_questions -v`

Expected: FAIL because `/query` does not exist.

- [x] **Step 4: Implement the request and response mapping**

```python
class ContestQueryRequest(BaseModel):
    question_id: str | None = Field(default=None, max_length=200)
    question: str = Field(min_length=1, max_length=4000)
    company: str | None = Field(default=None, max_length=200)
    as_of: str | None = Field(default=None, pattern=r"^20\d{2}-\d{2}-\d{2}$")
    limit: int = Field(default=20, ge=1, le=100)

def contest_payload(answer: Any, question_id: str | None) -> dict[str, Any]:
    body = to_jsonable(answer)
    citations = body.pop("citations", [])
    body.pop("citation_ids", None)
    body["question_id"] = question_id
    body["evidence"] = [
        {**citation, "receipt_no": citation["filing_id"]}
        for citation in citations
    ]
    return body

@app.post("/query")
def contest_query(request: ContestQueryRequest) -> dict[str, Any]:
    started = perf_counter()
    health = _health_status(agent.evidence_service)
    if not health["ready"]:
        raise HTTPException(status_code=503, detail="runtime_not_ready")
    answer = agent.answer(request.question, company=request.company, as_of=request.as_of, limit=request.limit)
    return envelope(contest_payload(answer, request.question_id), started)
```

- [x] **Step 5: Add and run a fail-closed readiness test**

```python
def test_contest_query_returns_503_when_runtime_is_not_ready(self):
    class MissingService(ReadyService):
        base_database = Path("missing.sqlite")
    class MissingAgent(FakeAgent):
        evidence_service = MissingService()
    client = TestClient(create_app(MissingAgent()))
    response = client.post("/query", json={"question": "질문"})
    self.assertEqual(response.status_code, 503)
    self.assertEqual(response.json()["detail"], "runtime_not_ready")
```

Run: `python -m unittest tests.test_agent_runtime -v`

Expected: PASS.

- [x] **Step 6: Commit**

```powershell
git add src/disclosure_db/api.py tests/test_agent_runtime.py
git commit -m "feat: add contest query endpoint"
```

### Task 3: Expose honest provider and readiness status

**Files:**
- Modify: `src/disclosure_db/generation.py`
- Modify: `src/disclosure_db/agent.py`
- Modify: `src/disclosure_db/api.py`
- Modify: `tests/test_agent_runtime.py`

**Interfaces:**
- Produces: `HyperClovaGenerator.configured: bool`, `DisclosureAgent.provider_configured: bool`, `/health.provider_configured`

- [ ] **Step 1: Write a failing provider-status test**

```python
def test_health_reports_provider_configuration_without_exposing_key(self):
    class ReadyService:
        base_database = existing_base_fixture
        overlay_database = None
        search_database = None
        attestation = None
        corpus_revision = "test-revision"
        def company_candidates(self):
            return []
    agent = DisclosureAgent(evidence_service=ReadyService(), generator=HyperClovaGenerator(api_key="secret"))
    body = TestClient(create_app(agent)).get("/health").json()
    self.assertTrue(body["provider_configured"])
    self.assertNotIn("secret", json.dumps(body))
```

- [ ] **Step 2: Run it and confirm the field is absent**

Run: `python -m unittest tests.test_agent_runtime.AgentRuntimeTests.test_health_reports_provider_configuration_without_exposing_key -v`

Expected: FAIL on missing `provider_configured`.

- [ ] **Step 3: Implement boolean-only status**

```python
class HyperClovaGenerator:
    @property
    def configured(self) -> bool:
        return bool(self.api_key)

class DisclosureAgent:
    @property
    def provider_configured(self) -> bool:
        return bool(getattr(self.generator, "configured", False))
```

Add `provider_configured` to the health payload using the agent property. Never serialize generator attributes.

- [ ] **Step 4: Run API tests**

Run: `python -m unittest tests.test_agent_runtime tests.test_reranker -v`

Expected: PASS.

- [ ] **Step 5: Commit**

```powershell
git add src/disclosure_db/generation.py src/disclosure_db/agent.py src/disclosure_db/api.py tests/test_agent_runtime.py
git commit -m "feat: report disclosure provider readiness"
```

### Task 4: Let `serve` use the runtime environment

**Files:**
- Modify: `src/disclosure_db/cli.py` in `_agent_parser` and `agent_main`
- Create: `tests/test_cli.py`

**Interfaces:**
- `disclosure-agent serve` consumes `DISCLOSURE_*` when file arguments are omitted.
- Explicit CLI arguments override environment variables.

- [ ] **Step 1: Write failing parser/selection tests**

```python
def test_serve_without_paths_loads_runtime_environment(self):
    args = argparse.Namespace(
        database=None, overlay=None, search_database=None, attestation=None,
        host="127.0.0.1", port=8000,
    )
    config = RuntimeConfig(base, overlay, search, attestation)
    with patch("disclosure_db.cli.RuntimeConfig.from_env", return_value=config) as load:
        settings, host, port = _serving_settings(args)
    load.assert_called_once()
    self.assertEqual(settings.base_database, base)
    self.assertEqual((host, port), (config.host, config.port))
```

Add a second test with all four explicit paths and assert environment loading is not used.

- [ ] **Step 2: Run the tests and confirm argparse rejects missing `--database`**

Run: `python -m unittest tests.test_cli -v`

Expected: FAIL with argparse exit 2.

- [ ] **Step 3: Implement one settings selection function**

```python
def _serving_settings(args: argparse.Namespace) -> tuple[AgentSettings, str, int]:
    explicit = (args.database, args.overlay, args.search_database, args.attestation)
    if any(explicit):
        if not all(explicit):
            raise SystemExit("serve requires all four database arguments or none")
        return AgentSettings(
            base_database=args.database,
            overlay_database=args.overlay,
            search_database=args.search_database,
            attestation_path=args.attestation,
        ), args.host, args.port
    config = RuntimeConfig.from_env()
    config.validate()
    return config.to_agent_settings(), config.host, config.port
```

Make the four `serve` file arguments optional. Keep `agent-query` behavior unchanged.

- [ ] **Step 4: Run CLI and runtime tests**

Run: `python -m unittest tests.test_cli tests.test_runtime_config -v`

Expected: PASS.

- [ ] **Step 5: Commit**

```powershell
git add src/disclosure_db/cli.py src/disclosure_db/runtime.py tests/test_cli.py tests/test_runtime_config.py
git commit -m "feat: serve disclosure agent from environment"
```

### Task 5: Add the minimal container package

**Files:**
- Create: `Dockerfile`
- Create: `compose.yaml`
- Create: `.dockerignore`
- Create: `.env.example`
- Create: `tests/test_deployment_artifacts.py`

**Interfaces:**
- Host variables: `DISCLOSURE_BASE_DB_HOST`, `DISCLOSURE_AGENT_DB_DIR_HOST`, `DISCLOSURE_ATTESTATION_HOST`, `DISCLOSURE_RUNTIME_DIR_HOST`, `CLOVASTUDIO_API_KEY`
- Container paths: `/data/base/disclosure.sqlite`, `/data/agent/agent_overlay.sqlite`, `/data/agent/agent_search.sqlite`, `/data/attestation.json`, `/runtime`

- [ ] **Step 1: Write failing static artifact tests**

```python
def test_compose_mounts_databases_read_only_and_has_healthcheck(self):
    text = Path("compose.yaml").read_text(encoding="utf-8")
    self.assertIn("/data/base/disclosure.sqlite:ro", text)
    self.assertIn("/data/agent:ro", text)
    self.assertIn("healthcheck:", text)
    self.assertNotIn("CLOVASTUDIO_API_KEY=", text)

def test_dockerfile_does_not_copy_local_data(self):
    self.assertIn("data/", Path(".dockerignore").read_text(encoding="utf-8"))
    self.assertNotIn("D:\\", Path("Dockerfile").read_text(encoding="utf-8"))
```

- [ ] **Step 2: Run and confirm missing-file failures**

Run: `python -m unittest tests.test_deployment_artifacts -v`

Expected: FAIL because artifacts do not exist.

- [ ] **Step 3: Create the Dockerfile**

```dockerfile
FROM python:3.11-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY pyproject.toml ./
COPY src ./src
RUN pip install --no-cache-dir ".[agent]"
RUN useradd --create-home --uid 10001 agent
USER agent
EXPOSE 8000
CMD ["disclosure-agent", "serve"]
```

- [ ] **Step 4: Create Compose and secret-free example environment**

```yaml
services:
  disclosure-agent:
    build: .
    restart: unless-stopped
    ports: ["8000:8000"]
    environment:
      DISCLOSURE_BASE_DB: /data/base/disclosure.sqlite
      DISCLOSURE_OVERLAY_DB: /data/agent/agent_overlay.sqlite
      DISCLOSURE_SEARCH_DB: /data/agent/agent_search.sqlite
      DISCLOSURE_ATTESTATION: /data/attestation.json
      CLOVASTUDIO_API_KEY: ${CLOVASTUDIO_API_KEY:-}
    volumes:
      - "${DISCLOSURE_BASE_DB_HOST}:/data/base/disclosure.sqlite:ro"
      - "${DISCLOSURE_AGENT_DB_DIR_HOST}:/data/agent:ro"
      - "${DISCLOSURE_ATTESTATION_HOST}:/data/attestation.json:ro"
      - "${DISCLOSURE_RUNTIME_DIR_HOST}:/runtime"
    healthcheck:
      test: ["CMD", "python", "-c", "import json,urllib.request; assert json.load(urllib.request.urlopen('http://127.0.0.1:8000/health'))['ready']"]
      interval: 30s
      timeout: 5s
      retries: 5
```

`.env.example` contains D-drive DB path examples, `DISCLOSURE_ATTESTATION_HOST=./data/derived/database_distribution_manifest_semantic_v1.json`, and an empty `CLOVASTUDIO_API_KEY=` only; `.env` remains ignored.

- [ ] **Step 5: Run static tests and build the image**

Run: `python -m unittest tests.test_deployment_artifacts -v`

Run: `docker compose config`

Run: `docker build -t mirae-disclosure-agent:local .`

Expected: tests PASS, Compose config resolves after copying `.env.example` to local `.env`, image build exits 0.

- [ ] **Step 6: Commit**

```powershell
git add Dockerfile compose.yaml .dockerignore .env.example tests/test_deployment_artifacts.py
git commit -m "build: package disclosure agent server"
```

### Task 6: Add Windows start and smoke scripts

**Files:**
- Create: `scripts/start-agent.ps1`
- Create: `scripts/smoke-agent.ps1`
- Modify: `tests/test_deployment_artifacts.py`

**Interfaces:**
- `start-agent.ps1 -Mode Local|Docker -DataRoot D:\mirae-asset-project`
- `smoke-agent.ps1 -BaseUrl http://127.0.0.1:8000`

- [ ] **Step 1: Add failing script-contract tests**

```python
def test_windows_scripts_validate_read_only_inputs_and_smoke_query(self):
    start = Path("scripts/start-agent.ps1").read_text(encoding="utf-8")
    smoke = Path("scripts/smoke-agent.ps1").read_text(encoding="utf-8")
    self.assertIn("Test-Path -LiteralPath", start)
    self.assertIn("disclosure-agent", start)
    self.assertIn("/health", smoke)
    self.assertIn("/query", smoke)
```

- [ ] **Step 2: Run and confirm missing-script failures**

Run: `python -m unittest tests.test_deployment_artifacts -v`

Expected: FAIL.

- [ ] **Step 3: Implement `start-agent.ps1`**

The script resolves these exact inputs, validates all with `Test-Path -LiteralPath -PathType Leaf`, sets process-scoped `DISCLOSURE_*`, and invokes either `disclosure-agent serve` in the current console or `docker compose up -d --build`. It must not print environment values or accept credentials as command-line parameters.

```powershell
param(
  [ValidateSet('Local','Docker')][string]$Mode = 'Local',
  [string]$DataRoot = 'D:\mirae-asset-project'
)
$baseDb = Join-Path $DataRoot 'db\semantic-v1_129f5b0\disclosure_corpus_semantic_v1.sqlite'
$agentDir = Join-Path $DataRoot 'db\agent'
$attestation = Join-Path $PSScriptRoot '..\data\derived\database_distribution_manifest_semantic_v1.json'
$required = @($baseDb, (Join-Path $agentDir 'agent_overlay.sqlite'), (Join-Path $agentDir 'agent_search.sqlite'), $attestation)
foreach ($path in $required) {
  if (-not (Test-Path -LiteralPath $path -PathType Leaf)) { throw "Required runtime file missing: $path" }
}
```

- [ ] **Step 4: Implement deterministic smoke assertions**

`smoke-agent.ps1` waits at most 60 seconds for `ready=true`, posts one answerable fixture and one out-of-scope fixture, asserts `request_id`, `answerable`, `verified`, `evidence`, and exits non-zero on any mismatch. Use `Invoke-RestMethod`; do not log the API key.

- [ ] **Step 5: Run static and local smoke tests**

Run: `python -m unittest tests.test_deployment_artifacts -v`

Run in terminal A: `powershell -ExecutionPolicy Bypass -File scripts/start-agent.ps1 -Mode Local`

Run in terminal B: `powershell -ExecutionPolicy Bypass -File scripts/smoke-agent.ps1`

Expected: test PASS and smoke exits 0. Stop terminal A with Ctrl+C.

- [ ] **Step 6: Commit**

```powershell
git add scripts/start-agent.ps1 scripts/smoke-agent.ps1 tests/test_deployment_artifacts.py
git commit -m "ops: add Windows agent startup smoke"
```

### Task 7: Document and verify the serving package

**Files:**
- Create: `docs/operations/contest-server.md`
- Modify: `README.md`
- Modify: `docs/development-log.md`

**Interfaces:**
- Produces exact local, Docker, NCP, stop, restart, and diagnosis commands.

- [ ] **Step 1: Write the operations guide**

The guide starts with prerequisites and warnings, then includes these executable flows:

```powershell
Copy-Item .env.example .env
powershell -ExecutionPolicy Bypass -File scripts/start-agent.ps1 -Mode Docker
powershell -ExecutionPolicy Bypass -File scripts/smoke-agent.ps1
docker compose logs --tail 200 disclosure-agent
docker compose down
```

For NCP, map `/srv/mirae/data/base`, `/srv/mirae/data/agent`, `/srv/mirae/runtime`; document ACG restriction, public-IP smoke from a second network, and restart verification. State that NCP completion cannot be claimed until those external checks pass.

- [ ] **Step 2: Add a short README quick start and development-log entry**

Link the operations guide from README. Record DB identity, commands executed, whether provider was configured, smoke request IDs, and observed latency without recording questions, answers, credentials, or private URLs.

- [ ] **Step 3: Run the complete local verification**

Run: `python -m unittest discover -s tests -v`

Run: `python -m compileall -q src scripts tests`

Run: `git diff --check`

Expected: all tests PASS except documented optional skips; compile and diff checks exit 0.

- [ ] **Step 4: Run Docker smoke**

Run: `docker compose up -d --build`

Run: `powershell -ExecutionPolicy Bypass -File scripts/smoke-agent.ps1`

Run: `docker compose down`

Expected: container becomes healthy, smoke exits 0, container stops cleanly.

- [ ] **Step 5: Commit**

```powershell
git add README.md docs/operations/contest-server.md docs/development-log.md
git commit -m "docs: add contest server runbook"
```

## Plan 1 completion gate

- `/query` contract tests pass.
- Local and Docker smoke tests pass against D-drive DBs.
- `/health` reports DB and provider state without secrets.
- `git status --short` contains no generated DB, raw response, `.env`, or log artifact.
- Proceed to `2026-08-19-contest-correctness-hardening.md`; do not claim contest readiness yet.
