# HyperCLOVA X Function Calling 연결 설계

## 현재 상태

- `ToolRegistry`가 다섯 Tool의 이름, 설명, 입력/출력 JSON schema, handler와 allowlist dispatcher를 관리한다.
- Tool 실행 뒤 `EvidenceSufficiencyChecker`가 `sufficient|partial|insufficient`, `recommended_action`, `answer_allowed`를 결정한다.
- 기존 `HyperClovaGenerator`는 이미 HCX OpenAI 호환 `/chat/completions`를 사용하지만, 검색 전에 Tool을 선택하는 Function Calling 계층은 없었다.
- Dense retrieval은 100개 `smoke_only` 계약이고 실제 embedding artifact가 작업공간에 없다. Sparse fallback은 동작한다.

## 이번 목표

사용자 질문을 HCX에 전달해 한 개 Tool을 선택하고, 기존 Registry로만 실행한 뒤, 충분성 결과가 최종 답변을 허용할 때에만 두 번째 HCX 생성을 호출한다. `answer_allowed=False`이면 prompt 지시와 무관하게 backend가 생성 호출을 생략하고 `abstain` 또는 `ask_clarification`을 반환한다.

## 설계 결정과 이유

1. `HcxFunctionCallingService`와 `HcxFunctionClient` Protocol을 분리한다. orchestration은 fake client로 검증하고 HTTP 세부사항은 concrete adapter에 가둔다.
2. HCX에 노출하는 `tools`는 `ToolRegistry.list_tools()`의 `input_schema`에서 실행 시점에 직접 만든다. 별도 schema 사본을 관리하지 않아 Registry와 provider 정의가 drift하지 않는다.
3. HCX가 반환한 Tool 이름·arguments를 직접 실행하지 않고 항상 `ToolRegistry.dispatch()`에 전달한다. 미등록 Tool, 누락/오류 자료형, 날짜 역전, Top-k 초과는 기존 검증기가 실행 전에 차단한다.
4. Registry가 기록한 `metadata.sufficiency_check`만 answerability source of truth로 사용한다. Tool 상태가 `success|partial`, `answer_allowed=True`, 권장 동작이 `answer|answer_with_warning`일 때만 최종 생성을 호출한다.
5. 최종 HCX 출력은 `answer`, `citation_ids` 두 필드의 JSON으로 제한하고 모든 citation이 Tool Evidence Bundle에 존재하는지 backend에서 확인한다. unknown citation은 답변을 폐기한다.
6. 공식 NAVER Cloud 문서가 확인된 OpenAI 호환 API를 사용한다. `/v1/openai/chat/completions`, snake_case `tools`/`tool_choice`/`tool_calls`, Function Calling용 `max_tokens=1024`를 적용한다. tuning·LangChain·LangGraph 형식은 추측하거나 도입하지 않는다.
7. API key는 기존 `CLOVASTUDIO_API_KEY`, endpoint/model은 기존 `CLOVASTUDIO_BASE_URL`/`CLOVASTUDIO_MODEL`에서만 읽는다. key가 없으면 네트워크나 Tool 실행 전 `provider_unavailable`로 종료한다.

공식 근거: [CLOVA Studio Function calling](https://api.ncloud-docs.com/docs/en/clovastudio-chatcompletionsv3-fc), [OpenAI compatibility](https://api.ncloud-docs.com/docs/en/clovastudio-openaicompatibility).

## 구현 내용

- `hcx_tool_schemas(registry)`: 5개 Registry contract를 HCX/OpenAI function schema로 변환하고 JSON round-trip으로 복제·호환성을 확인한다.
- `HyperClovaFunctionClient`: 표준 라이브러리 HTTP adapter, 단일 Tool call 파싱, assistant/tool message 연결, strict final JSON 파싱을 담당한다.
- `HcxFunctionCallingService.answer()`: 질문 검증 → Tool 선택 → Registry dispatch → 충분성 hard gate → 조건부 최종 생성 → evidence citation 검증 순서를 소유한다.
- `FunctionCallingResult`: `answered|abstained|invalid_request|provider_unavailable|error`, Tool 결과, 권장 동작, answerability, citations와 content-free provider 상태를 반환한다.
- Search/Summary Tool이 받은 문자열 질문은 실제 실행 직전에 원래 사용자 질문으로 고정한다. HCX가 질문을 누락하거나 잘못된 자료형으로 반환하면 덮어쓰지 않고 Registry에서 거부한다.

## 발견한 문제와 해결

- 기존 HCX generator와 새 adapter가 JSON 추출 규칙을 중복할 가능성이 있었다. 기존 parser를 `parse_hcx_json_content`로 공개하고 이전 private alias를 유지해 한 구현을 재사용했다.
- HCX의 prompt만으로 단일 Tool이나 abstention을 보장할 수 없다. 단일 call shape를 adapter에서 검사하고 다중/무호출을 protocol error로 처리하며, 최종 생성 여부는 backend boolean gate가 결정하도록 했다.
- 전체 Dense artifact가 없어도 흐름을 검증해야 했다. Dense `unavailable`인 `RetrievalResult`와 Sparse evidence fixture를 사용해 selection부터 final answer까지 실행했다.
- 기존 HTTP 테스트 일부는 선택 의존성 `fastapi`가 없는 현재 환경에서 실행할 수 없다. Function Calling 테스트는 표준 라이브러리와 fake client만 사용하며 이 의존성과 분리했다.

## 테스트 결과

- Function Calling 신규 테스트: 8개 통과.
- Tool Registry 신규/기존 fixture: 11개 통과.
- 최종 전체·회귀 명령 결과는 `docs/development-log.md`에 같은 날짜 항목으로 기록한다.
- 실제 HCX 네트워크 호출과 credential 사용은 수행하지 않았다.

## 현재 제한사항

- 실제 CLOVA Studio key를 사용한 schema smoke, model별 Tool 선택 품질·latency·비용은 아직 검증하지 않았다.
- 최종 citation membership은 검증하지만 자연어 claim 전체의 의미·수치 일치 검증은 별도 Answer Verifier 연결 범위다.
- Function Calling service는 순수 Python backend 계층이며 FastAPI/CLI/운영 배포에는 아직 연결하지 않았다.
- 한 질문당 정확히 한 Tool만 허용한다. 공식 API가 지원할 수 있는 parallel tool calls는 이번 안전 경계에서 거부한다.
- Dense는 여전히 100개 `smoke_only`, embedding artifact pending 상태이며 전체 corpus 검색 품질을 주장하지 않는다.

## 다음 할 일

1. 승인된 test/service API key로 5개 Tool schema smoke와 insufficient hard-gate telemetry를 검증한다.
2. 선택 정확도, final citation/수치 검증, latency와 provider 오류율을 고정 평가셋으로 측정한다.
3. 별도 승인 범위에서 기존 runtime/API에 `HcxFunctionCallingService`를 주입하고 readiness에 Function Calling 상태를 추가한다.
4. 전체 embedding artifact가 준비되면 동일 Tool/Evidence contract를 유지한 채 Dense 경로만 교체하고 full-corpus retrieval 회귀를 실행한다.

## 2026-08-25 runtime integration 업데이트

### 작업 전 상태 → 이번 목표

Function Calling service와 fake client 테스트는 있었지만 실제 `serve` runtime은 `DisclosureAgent`와 기존 `/v1/answer`만 구성했다. 이번 목표는 기존 경로를 보존하면서 별도 FastAPI endpoint가 Function Calling service, Registry, EvidenceService, HybridRetriever와 NCP에 mount되는 read-only SQLite까지 연결되도록 composition root를 완성하는 것이다.

### runtime composition과 선택 이유

- `runtime.build_runtime_services(settings)`가 하나의 `DisclosureAgent`를 만들고, 그 Agent가 이미 검증한 `EvidenceService`, base DB, attestation, search DB를 `build_function_calling_service()`에 재사용한다. DB 경로와 검증 정책을 별도로 다시 해석하지 않는다.
- `SparseRetriever.open()`은 같은 attestation의 base SHA-256/size로 기존 `SafeSearchIndex`를 열고 `HybridRetriever(sparse, None)`에 주입한다. 전체 Dense artifact가 없는 상태에서 Dense를 새로 만들거나 다운로드하지 않는다.
- `build_tool_registry()`에는 위 Hybrid retriever, 동일 EvidenceService, 동일 base DB만 주입한다. Tool contract, handler, 충분성 판정은 기존 source of truth 그대로다.
- CLI `serve`는 이 runtime services를 `create_app()`에 전달한다. 기존 `/v1/answer`는 기존 Agent를 계속 호출하고 Function Calling은 별도 `/v1/hcx/function-answer`만 사용한다.

### endpoint와 credential 연결

- `POST /v1/hcx/function-answer`는 `{\"question\": \"...\"}`만 받고 `FunctionCallingResult` envelope를 반환한다. runtime readiness가 실패하거나 service가 주입되지 않으면 503으로 fail-closed한다.
- `/health`에는 secret-free `function_calling_configured` boolean만 추가한다. key나 provider 응답은 노출하지 않는다.
- concrete client는 `CLOVASTUDIO_API_KEY`, `CLOVASTUDIO_BASE_URL`, `CLOVASTUDIO_MODEL`만 환경에서 읽는다. Compose가 세 이름을 container에 전달하고 `.env.example`은 이름/공개 기본값만 기록한다. `.gitignore`의 `.env`, `.env.*` 규칙은 유지된다.
- 실제 secret은 사용자가 로컬/NCP의 untracked `.env` 또는 프로세스 환경에 직접 설정한다. 채팅, 명령 인자, repository 문서, 로그로 전달하지 않는다.

### HCX Prompt v1과 DART 접수번호 hard gate

- prompt는 `src/disclosure_db/hcx_prompts.py`의 `hcx-function-v1`로 분리했다. Tool 선택과 최종 생성 prompt 모두 DART/Tool Evidence 한정, 사실·숫자 추측 금지, 회사·기간·계정·단위 보존, citation ID 반환을 명시한다. 모든 trace 결과의 `metadata.prompt_version`에 버전을 남긴다.
- Tool Evidence item은 corpus `filing_id`를 source of truth로 구조화된 `rcept_no`와 `report_name`에 보존한다. HCX는 접수번호를 answer 본문에 작성하지 않는다.
- 충분성 gate를 통과해도 유효한 14자리 `rcept_no`가 하나도 없으면 최종 HCX 호출 전에 `abstained`로 중단한다. 생성 후에는 citation ID가 Evidence에 있고 유효한 접수번호에 연결되는지 검증한다. provider가 본문에 14자리 번호를 직접 만들면 결과를 폐기한다.
- backend가 검증된 citation만 `citations[{evidence_id,rcept_no,report_name,filed_at,correction_role}]`로 반환하고 답변 끝의 `근거 공시` 영역을 결정적으로 렌더링한다. 정정 Tool은 최초공시와 정정공시의 유효한 접수번호 쌍이 모두 있어야 생성하며 두 역할을 구분한다.

### 변경 파일

- composition/API: `src/disclosure_db/runtime.py`, `cli.py`, `api.py`
- HCX/prompt/evidence: `hcx_function_calling.py`, `hcx_prompts.py`, `disclosure_tools.py`, `generation.py`
- 운영 준비: `compose.yaml`, `.env.example`, `scripts/smoke_hcx_function_calling.py`
- 테스트/문서: `tests/test_hcx_function_calling.py`, `test_hcx_runtime_integration.py`, `test_disclosure_tools.py`, `test_deployment_artifacts.py`, README, Tool 문서, 개발 로그와 이 문서

### 발견한 문제와 해결

- `filing_id`가 실질적인 접수번호였지만 provider-facing 이름이 없어 HCX가 의미를 추정해야 했다. Evidence item과 최종 citation에 명시적 `rcept_no`를 추가하고 backend만 표시하도록 했다.
- Tool 충분성이 true여도 접수번호 형식이 유효하다는 보장은 없었다. 기존 충분성 정책을 대규모 변경하지 않고 최종 생성 직전 별도 receipt hard gate를 두었다.
- 기존 runtime과 Function Calling이 별도 EvidenceService를 만들면 attestation/DB 설정이 drift할 수 있었다. 같은 Agent 인스턴스의 EvidenceService를 재사용하도록 composition root를 단일화했다.
- 로컬 Python에는 선택 의존성 FastAPI가 없다. pure-Python composition/fake-client 테스트는 실행하고, FastAPI TestClient E2E는 테스트로 추가하되 현재 환경에서는 명시적으로 skip한다. CI/운영 extra가 설치된 환경에서 그대로 실행된다.

### 테스트 결과

최종 회귀, compileall, diff 결과는 같은 날짜의 `docs/development-log.md`에 기록한다. 실제 HCX credential과 네트워크 호출, NCP SSH, 배포, 서버 재시작은 수행하지 않았다.

### 현재 제한사항과 실제 NCP 배포 후 할 일

- 실제 HCX model이 5개 질문 유형을 올바르게 선택하는지, schema 호환성, latency, quota/비용은 아직 측정하지 않았다.
- NCP에서 untracked `.env`에 credential 이름을 설정하고 compose/runtime가 세 변수를 전달하는지 확인한 뒤, 기존 read-only mount/attestation readiness를 먼저 검증해야 한다.
- 재배포·재시작 후 `/health`의 `ready`와 `function_calling_configured`를 확인하고 재무, 검색, 트렌드, 정정, 근거 부족 smoke를 차례로 실행한다. 실제 응답의 모든 `citations[].rcept_no`를 corpus와 대조한다.
- provider 선택·최종 생성 오류율, receipt/citation 실패와 hard-gate 차단 횟수를 content-free telemetry로 측정한 뒤 운영 endpoint 공개 여부를 결정한다.

### embedding 이후 다음 단계

검증된 전체 corpus index가 준비되면 composition의 `dense=None`을 즉시 바꾸지 않는다. 먼저 index/metadata identity, L2/IP 계약, full-corpus retrieval Gold와 latency를 별도 gate로 검증한다. 통과 후 동일 Tool/Evidence/receipt 계약을 유지한 채 Dense retriever만 주입하고 sparse fallback과 HCX E2E 회귀를 다시 실행한다.

## 2026-08-25 실제 HCX·FastAPI·배포 준비 검증

### 실제 HCX schema smoke

- untracked `.env`에 세 HCX 설정이 모두 존재하고 값이 비어 있지 않은지만 확인했다. 실제 값, 길이, prefix, provider 응답 본문은 출력하거나 기록하지 않았다.
- sandbox 외부의 승인된 실제 호출 두 번이 성공했다. Registry에서 직접 파생한 5개 schema를 전달했고 HCX는 표준 `choices[].message.tool_calls` 형태의 단일 function call을 반환했다.
- 질문 `삼성전자의 사업 내용 관련 공시를 검색해줘.`에 `search_disclosures`를 선택했고 argument key는 `company`, `question`, `top_k`였다. 반환 arguments는 기존 Registry input schema 검증을 통과했다. 두 번째 측정 왕복은 약 `3,635.9ms`였다.
- 실제 로컬 NCP corpus가 없으므로 Tool dispatch, SQLite Evidence, 최종 HCX 생성까지의 live E2E는 실행하지 않았다. schema/tool-call smoke 성공을 DB E2E 성공으로 확대 해석하지 않는다.

### FastAPI endpoint 검증

- 전역 Python을 변경하지 않고 Git 제외 대상 `.venv`를 만들고 CI와 같은 `pip install -e \".[agent]\" pytest httpx`로 선택 의존성을 설치했다.
- 실제 FastAPI TestClient에서 `/health`와 `POST /v1/hcx/function-answer`를 실행했다. fake HCX + Sparse Evidence의 정상 답변은 structured `citations[].rcept_no`와 latency를 반환했다.
- Evidence가 없을 때 `evidence_status=insufficient`, `answer_allowed=False`이며 fake client의 최종 생성 호출 수는 0이었다. Evidence는 있으나 유효한 `rcept_no`가 없을 때도 최종 생성 호출 수 0과 명시적 warning을 확인했다.
- HCX/runtime focused suite는 `18 passed`, 기존 Agent/API/Public limit/Web 회귀는 `57 passed`였다. 전체 pytest는 `482 passed, 2 skipped, 46 warnings, 45 subtests`로 통과했고 compileall과 diff check도 통과했다. optional fake-vector 테스트 수집에 필요한 `numpy`만 workspace `.venv`에 추가했으며 FAISS·모델·embedding은 설치하거나 실행하지 않았다. 경고는 기존 FastAPI `on_event`, TestClient/httpx와 Windows subprocess encoding warning이며 기능 실패는 아니었다.

### Docker/NCP 배포 준비 점검

- `Dockerfile`은 `src`와 `config`를 복사하고 `.[agent]`를 설치한 뒤 `disclosure-agent serve`를 실행한다. 이 entrypoint가 `build_runtime_services()`를 통해 Agent와 Function Calling service를 함께 조립한다.
- Compose는 container 내부 base/overlay/search/attestation 4개 경로와 HCX key/base/model 3개 변수를 전달한다. base 파일, agent DB 디렉터리, attestation은 read-only mount이고 Function Calling runtime은 `HybridRetriever(sparse, None)`을 사용한다.
- deployment/runtime/CLI artifact 테스트 `13 passed`로 entrypoint, endpoint, sparse-only composition, 환경변수 전달과 `.env` Git 제외를 확인했다.
- 현재 로컬에는 Docker CLI가 없어 `docker compose config`, image build, container healthcheck는 실행하지 못했다. 따라서 코드/정적 계약은 READY지만 실제 image/runtime 검증은 NCP 또는 Docker가 있는 별도 환경에서 필요하다.

### 배포 후 smoke helper

- helper 기본 출력은 `query`, `selected_tool`, `evidence_status`, `answer_allowed`, 구조화된 citation/접수번호, 최종 answer, endpoint latency와 warning이다. `--expect-tool`, `--expect-status`, `--expect-answer-allowed`로 5종 smoke를 fail-fast 검증하고 `--show-full`일 때만 전체 envelope를 출력한다.
- summary contract 단위 테스트와 CLI help 실행이 통과했다. helper는 credential을 읽거나 전송하지 않고 실행 중 endpoint만 호출한다.

### 현재 결정과 다음 절차

- Sparse/Structured HCX Function Calling V1은 실제 provider schema와 로컬 FastAPI control flow 기준 배포 후보 상태다. Dense는 명시적으로 `None`; embedding 코드는 변경하지 않았다.
- NCP에서 사용자가 직접 `.env`를 갱신하고 mode `0600`을 확인한 뒤 `docker compose config --quiet`, build, up/restart, `/health` 검증 순으로 진행한다. 이후 실제 corpus 값으로 재무·검색·트렌드·정정·근거 부족 5종 helper를 실행한다.
- 모든 답변의 Tool 선택, Evidence sufficiency, `answer_allowed`, 실제 corpus 접수번호와 최종 답변을 대조하고 provider/DB 실패율을 확인하기 전에는 운영 완료로 판정하지 않는다.

## 2026-08-25 NCP final-generation 장애 분석과 V1.1 보정

### 서버 관측 상태와 정확한 실패 경로

- 사용자 실행 NCP smoke는 health/Docker/search readiness가 모두 정상이고 `get_financial_facts → Evidence sufficient`까지 성공했지만 최종 결과가 `hcx_final_generation_failed`, `answer_allowed=false`, `citations=[]`였다. 이 서버 상태는 사용자가 제공한 관측값이며 이번 로컬 작업에서 NCP에 접속하거나 재배포하지 않았다.
- `HcxFunctionCallingService.answer()`의 최종 `try`는 `client.generate_answer()` 요청, provider 출력 파싱, citation membership, 접수번호 연결 검증을 한 `except`로 축약했다. 어느 단계든 실패하면 검증된 citation을 결과에 복사하기 전에 `error` envelope를 만들기 때문에 `citations=[]`가 된다.
- 로컬 실제 credential로 동일 final adapter를 재현한 결과 HTTP 요청은 성공했지만 HCX `message.content`는 93자의 일반 문장이었고 JSON 중괄호와 `answer|citation_ids` 키가 모두 0개였다. 기존 `parse_hcx_json_content()`가 `hcx_output_json_ambiguous`를 발생시킨 것이 이번 장애의 직접 원인이다. 접수번호는 이미 `_available_citations()`를 통과한 상태였으며 citation 빈 배열은 `rcept_no` 유실이 아니라 final 출력 계약 실패 후 fail-closed 결과다.

### 요청 schema 대조와 수정 결정

- 첫 요청은 `system,user + tools + tool_choice=auto + max_tokens=1024`이고 단일 Tool Call을 받는다. 최종 요청은 공식 follow-up 순서인 `system,user,assistant(tool_calls),tool(tool_call_id, JSON content)`를 사용한다. OpenAI 호환 규격에 맞게 assistant의 `function.arguments`와 tool의 `content`는 JSON string이고 message role, call ID 연결과 `max_tokens=1024`도 실제 API에서 수용됐다.
- 문제는 request message 형식이 아니라 최종 응답을 prompt만으로 JSON으로 만들려 한 점이다. 공식 OpenAI 호환 `response_format=json_schema`도 시험했지만 현재 credential/model 조합은 HTTP 400, provider code `40001`로 거부해 운영 해결책으로 사용하지 않았다.
- HCX-005에서 이미 동작하는 Function Calling을 최종 출력 계약에도 재사용했다. private `submit_grounded_answer` Tool을 한 개만 노출하고 `tool_choice`로 강제한다. arguments는 `answer`, `citation_ids` 두 필드이며 citation item enum은 Tool Result의 Evidence ID로 제한한다. 이 Tool은 Registry에 등록하거나 사용자에게 노출하는 업무 Tool이 아니라 provider 출력 transport다.
- Prompt trace는 `hcx-function-v1.1`로 올렸다. Evidence Sufficiency, `answer_allowed`, 유효 14자리 `rcept_no`, correction pair, provider receipt 렌더링 금지와 citation membership gate는 완화하거나 우회하지 않았다.

### 안전한 structured error logging

- provider/검증 실패 시 `hcx_function_calling_failure` warning을 JSON으로 기록한다. 필드는 `prompt_version`, `error_stage`, `error_type`, 안전한 `error_code`, 선택적 `http_status`, allowlist 형식의 `provider_error_code`, 등록 Tool 이름뿐이다.
- API metadata에도 같은 content-free 진단값과 `available_citation_count`, `valid_rcept_no_count`를 남겨 HTTP 요청 실패와 provider response/citation 검증 실패를 구분한다. 질문, Tool arguments/result, Evidence 본문, `rcept_no` 값, credential, provider error message와 raw response는 로그에 넣지 않는다.

### 검증 결과와 현재 경계

- HCX/endpoint focused suite는 `22 passed`였다. 최종 강제 Tool schema와 enum, message role/call ID/JSON string, 일반문장 거부 stage, safe HTTP code 추출, 로그 비노출, insufficient/missing-receipt backend 차단을 검증했다.
- 실제 local credential로 고정된 sufficient Tool Result를 사용한 전체 service smoke가 성공했다. 질문 유형은 NCP와 같은 삼성전자 2024 연결 매출액이었고 HCX는 `get_financial_facts`를 선택한 뒤 `submit_grounded_answer`를 호출했다. 결과는 `answered`, `answer_allowed=true`, citation 1개, 기대한 `rcept_no` 보존, backend 근거 공시 영역 포함, warning 없음, `hcx-function-v1.1`이었으며 약 7.74초였다. 답변/provider 원문과 credential은 출력·저장하지 않았다.
- 전체 회귀는 `485 passed, 2 skipped, 46 warnings, 45 subtests`로 통과했다. compileall과 diff check도 통과했다. 경고는 기존 FastAPI lifecycle/TestClient와 Windows subprocess encoding 경고다.
- 로컬에는 NCP corpus가 없어 SQLite Tool Result만 최소 고정 응답으로 대체했다. NCP 재배포 뒤 같은 실제 corpus 질문으로 Tool Result의 structured value, `evidence_bundle.items[].rcept_no`, 최종 `citations[].rcept_no`를 다시 대조해야 한다. Dense는 계속 `None`이며 embedding 코드는 변경하지 않았다.

## 2026-08-25 팀 공시 Q&A Web

### 작업 전 상태와 목표

- FastAPI는 이미 package 안의 `src/disclosure_db/web/`을 `/static`에 mount하고 `GET /`에서 `index.html`을 반환했지만, 기존 화면은 `/query`, `/health`, `/financial-coverage`를 호출하고 HCX Function Calling V1.1 응답 계약을 표시하지 못했다.
- 새 프런트 framework나 build step을 추가하지 않고 기존 HTML/CSS/ES module/localStorage 구조를 유지하면서, 팀원이 NCP의 `http://<NCP-IP>:8000/`만 열어 HCX 공시 Q&A를 사용할 수 있게 하는 것이 목표다.

### 구현 결정

- 브라우저의 유일한 질문 요청은 same-origin `POST /v1/hcx/function-answer`이며 body는 `{"question":"..."}`뿐이다. 브라우저는 HCX credential, SQLite 경로, Tool Registry에 접근하지 않고 FastAPI backend control flow만 통과한다.
- 응답은 backend 계약을 그대로 읽는다. 답변 허용은 `status=answered && answer_allowed=true`, Evidence 상태는 `tool_response.metadata.sufficiency_check.status`, 근거는 `citations[]`, 보조정보는 `tool_name`, `latency_ms`, `metadata.prompt_version`이다.
- 허용된 응답은 citation이 한 건 이상이고 각 citation이 `evidence_id`와 유효한 14자리 `rcept_no`를 가질 때만 렌더링한다. 접수번호·공시명·일자·정정 역할은 backend가 준 값만 표시하며 누락 값을 추측하지 않는다. DART 링크도 실제 14자리 접수번호에만 만든다.
- `answer_allowed=false`는 차단 카드와 `recommended_action` 안내로 표시한다. 최종 답변 영역을 만들지 않고, citation이 없으면 접수번호도 표시하지 않는다. timeout/network/API/invalid schema는 안전한 오류 카드와 선택적 재시도로 수렴한다.
- 밝은 배경, Navy/Blue 카드, 공시 근거 목록, 접수번호 code 표시, 작은 Tool/latency 보조영역으로 구성했다. 첫 화면은 서비스 원칙과 재무·검색·트렌드·정정 예시를 제공한다. 별도 외부 font/CDN/script는 없다.

### 검증과 현재 경계

- ES module 단위 테스트 `12 passed`: 정상/차단 분류, nested Evidence 상태, citation/rcept_no 보존, 유효 접수번호 gate, endpoint/body, loading, timeout/network/API/malformed 응답과 localStorage 구조를 검증했다.
- FastAPI Web/runtime focused `21 passed`: `/` serve, CSP/local asset, HCX endpoint 단독 사용, secret/SQLite 식별자 부재와 기존 runtime/limit 계약을 확인했다.
- 실제 로컬 FastAPI + fake Function Calling service를 브라우저에서 확인했다. 데스크톱과 390px 모바일, 첫 화면, loading 및 전송 버튼 비활성화, answered/Evidence sufficient/citation/receipt/Tool/latency, insufficient abstain, HTTP 500과 연결 실패 오류 UI가 동작했다. fake service는 credential과 DB를 읽지 않았다.
- 전체 Python 회귀는 `486 passed, 2 skipped, 48 warnings, 45 subtests`, compileall과 `git diff --check`는 통과했다. 경고는 기존 FastAPI lifecycle/TestClient 및 Windows subprocess encoding 계열이다.
- Dense/embedding, GraphRAG, Agent framework, backend Evidence gate와 HCX adapter는 이 Web 작업에서 변경하지 않았다. 실제 NCP 반영은 새 image build/recreate 뒤 사용자가 수행하고, `/health`와 실제 재무·검색·트렌드·정정·근거 부족 질문을 다시 확인해야 한다.
