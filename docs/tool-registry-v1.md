# Tool Registry v1

`ToolRegistry`는 특정 LLM과 분리된 순수 Python 백엔드 계약이다. 등록된 이름만 실행하며, JSON-compatible 입력을 실행 전에 검증하고 모든 정상 실행 뒤 내부 `EvidenceSufficiencyChecker`를 적용한다. 외부 입력으로 SQL, 함수명, 데이터베이스 경로를 받지 않는다.

## Tool 목록과 입력

| Tool | 역할 | 필수 입력 | 선택 입력과 상한 |
|---|---|---|---|
| `search_disclosures` | 기존 `HybridRetriever` 검색 | `question` | `company`, `filing_id`, `start_date`, `end_date`, `correction_policy`, `top_k` 1–100 |
| `get_financial_facts` | 검증된 구조화 재무 fact 조회 | `company`, `account` | `start_date`, `end_date`, `scope`, `correction_policy`, `top_k` 1–20 |
| `analyze_disclosure_trend` | filing 메타데이터 집계 | `start_date`, `end_date`, `granularity` | `company`, `filing_type`, `representative_limit` 1–50 |
| `get_correction_lineage` | 기존 `filing_version` 계보 조회 | `filing_id` | 없음 |
| `build_summary_context` | LLM 입력용 근거 context 구성 | `question` | 검색 필터, `top_k` 1–50, `max_chars` 100–20,000 |

날짜는 ISO `YYYY-MM-DD`이고 1900–2200년만 허용한다. `start_date > end_date`, 미등록 필드, 필수값 누락, 잘못된 자료형, 상한 초과는 handler 실행 전에 `invalid_request`로 차단한다. `correction_policy`는 `current`, `original`, `corrected`, `both` 중 하나다.

## Tool별 출력

- `search_disclosures`: 정렬된 검색 행과 `dense_status`, `dense_corpus_size`, `fallback_used`, `retrieval_mode`를 반환한다. 회사·filing·공시일 범위는 Dense metadata와 SafeSearch 양쪽에서 필터링한다.
- `get_financial_facts`: 값, scale, 단위, 기간, 연결/별도 scope, `evidence_ids`를 반환한다. 중앙 Financial Account Catalog, `QueryPlanner`, `EvidenceService`의 검증된 Financial Fact/Evidence 경로를 재사용한다.
- `analyze_disclosure_trend`: `count_by_period`, `count_by_type`, 대표 filing, 요청 범위, 실제 집계 범위와 source coverage를 반환한다. SQL은 코드에 고정돼 있고 SQLite는 read-only/immutable/query-only로 연다. 자연어 해석은 만들지 않는다.
- `get_correction_lineage`: 기존 `filing_version` 행에서 최초, 정정 목록, 현재 유효 공시, 연결 상태와 confidence를 읽는다. 새로운 연결 알고리즘을 실행하지 않는다.
- `build_summary_context`: Hybrid 순서를 유지한 `[filing_id|evidence_id] text` block을 결정적으로 연결하고 `max_chars`에서 자른다. Tool 내부 요약이나 LLM 호출은 없다.

## 공통 응답과 Evidence Bundle

모든 응답은 `status`, `tool_name`, `data`, `evidence_bundle`, `warnings`, `metadata`를 갖는다. `status`는 `success`, `partial`, `insufficient`, `invalid_request`, `error` 중 하나다. 예상 가능한 근거 부족은 예외가 아니라 `partial` 또는 `insufficient`다.

`evidence_bundle`은 다음 필드를 고정한다.

- `question_intent`, `requested_scope`, `covered_scope`
- `items`, `evidence_ids`, `filing_ids`, `issuer_corp_codes`
- `quality_warnings`, `correction_status`, `retrieval_status`, `sufficiency`

각 item은 적용 가능한 범위에서 `evidence_id(s)`, `chunk_id`, `filing_id`, 구조화된 DART `rcept_no`, `report_name`, 회사 식별자, 공시일/기간, section/table/locator/source path, 원문 또는 구조화 값, 단위, 품질 상태, 검색 경로/점수, 정정 상태를 담는다. 운영 corpus의 `filing_id`가 DART 접수번호 source of truth이며 provider가 번호를 추정하지 않도록 `rcept_no`로 명시적으로 복제한다. 없는 값은 추정하지 않고 `null` 또는 빈 목록으로 둔다.

## 충분성 판정과 fail-closed 정책

`EvidenceSufficiencyChecker`는 Registry에 공개되는 Tool이 아니며 handler 실행 뒤 항상 호출된다. RRF나 Dense 점수 임계치만으로 충분성을 선언하지 않고 다음 계약을 결정론적으로 확인한다.

- 공통: evidence/filing 식별자, 요청 회사·filing·기간과 실제 범위, 품질 provenance, correction 상태, 인용 가능성
- Financial: Catalog의 `structured` 지원, `validated` fact, 회사·계정·기간·scope grain, 값·단위·기간·evidence
- Search/Summary: 필터된 검색 결과와 인용 evidence, summary 원문, Dense Smoke warning
- Trend: 집계 건수, 대표 filing/evidence, 요청 기간에 대한 source coverage
- Correction: 최초/현재 행, 기존 연결 상태와 confidence, 인용 evidence

결과에는 `sufficient|partial|insufficient`, `reasons`, `missing_requirements`, `recommended_action`, `answer_allowed`가 기록된다. 권장 동작은 `answer`, `answer_with_warning`, `ask_clarification`, `abstain`이다. `ambiguous`, `retrieval_only`, `unsupported`, 이번 단계에서 미구현인 derived 계정은 검증된 숫자로 반환하지 않는다. 회사·기간·scope grain이나 evidence가 맞지 않으면 재무 fact는 fail-closed한다. 불확실한 정정 연결은 확정 표현을 허용하지 않는다.

## Dense Smoke 제한과 fallback

기본 Dense 설정은 정확히 100개 chunk만 기대하는 `smoke_only` pilot이다. Tool 응답과 warning에 이 상태를 유지하며 전체 corpus 검색 품질을 주장하지 않는다. Dense 파일 부재, 로드/질문 오류, metadata filter 뒤 0건이면 기존 Sparse 결과로 fallback하고 Dense 장애가 Sparse 검색을 실패시키지 않는다.

## HCX Function Calling 연결

`src/disclosure_db/hcx_function_calling.py`가 Registry 위에 선택적 HCX 연결 계층을 제공한다. HCX에 노출되는 다섯 function schema는 `ToolRegistry.list_tools()`의 이름·설명·`input_schema`에서 직접 파생되며 별도 schema 파일을 관리하지 않는다.

```python
from disclosure_db.hcx_function_calling import (
    HcxFunctionCallingService,
    HyperClovaFunctionClient,
)

service = HcxFunctionCallingService(registry, HyperClovaFunctionClient())
result = service.answer("테스트회사 사업 내용은?")
```

흐름은 `FastAPI → HCX Tool 선택 → Registry dispatch → EvidenceService/HybridRetriever/SQLite → sufficiency hard gate → 조건부 HCX 최종 생성`이다. `runtime.build_runtime_services()`가 기존 `DisclosureAgent`의 attested base/overlay/search 경로를 재사용하고 Dense 없이 SafeSearch 기반 `HybridRetriever`를 구성한다. 미등록 Tool과 잘못된 arguments는 Registry에서 거부한다. `answer_allowed=False`이거나 권장 동작이 `ask_clarification|abstain`이면 backend가 `generate_answer()`를 호출하지 않는다.

최종 생성 전 backend는 Evidence item에 실제 14자리 `rcept_no`가 최소 하나 있는지 확인한다. 없으면 `answer_generation_blocked_by_missing_rcept_no`로 생성 호출 전에 중단한다. HCX 최종 출력은 일반 `content`가 아니라 private `submit_grounded_answer(answer, citation_ids)` Function Call로 강제하며, `citation_ids` schema의 enum도 현재 Evidence ID로 제한한다. HCX는 접수번호를 작성하지 않고 backend가 citation ID와 Evidence의 `rcept_no`를 결합해 `citations[]`와 `근거 공시` 영역을 결정적으로 붙인다. provider가 답변 본문에 접수번호를 직접 출력하거나 unknown citation을 반환하면 결과를 폐기한다. 정정 계보는 최초공시와 정정공시 접수번호가 모두 검증돼야 하며 두 역할을 구분해 표시한다.

기존 `POST /v1/answer`는 변경하지 않았다. Function Calling 검증 경로는 다음 별도 endpoint다.

```http
POST /v1/hcx/function-answer
Content-Type: application/json

{"question":"삼성전자 2025년 매출액은?"}
```

응답에는 `status`, `answer`, `tool_name`, `tool_response`, `answer_allowed`, `recommended_action`, `citation_ids`, 구조화된 `citations`, `warnings`, `metadata`가 포함된다. `metadata.prompt_version`은 현재 `hcx-function-v1.1`이다. prompt 원문은 `src/disclosure_db/hcx_prompts.py`에서 버전 관리한다. Provider/최종 검증 실패 시에는 질문, Tool Result, raw response 없이 `error_stage`, `error_type`, `error_code`, 선택적 `http_status`와 `provider_error_code`만 API metadata와 structured warning log에 남긴다.

concrete adapter는 공식 [Function calling](https://api.ncloud-docs.com/docs/en/clovastudio-chatcompletionsv3-fc) 및 [OpenAI compatibility](https://api.ncloud-docs.com/docs/en/clovastudio-openaicompatibility) 계약에 따라 기존 `CLOVASTUDIO_API_KEY`, `CLOVASTUDIO_BASE_URL`, `CLOVASTUDIO_MODEL` 환경변수를 사용한다. key가 없으면 네트워크와 Tool 실행을 모두 생략하고 `provider_unavailable`을 반환한다. 2026-08-25 최초 local credential smoke에서 5개 schema 전달과 Tool 선택이 통과했다. NCP E2E에서 발견한 최종 일반문장 출력 문제를 수정한 뒤에는 실제 local credential로 Tool 선택과 private final Function Call 두 요청, backend citation/receipt 렌더링까지 통과했다. credential, final answer와 provider 본문은 기록하지 않았다.

로컬 또는 NCP 서버에서는 Git에 포함되지 않는 `.env`에 아래 이름만 설정한다. 값 자체를 명령행, 로그, 문서에 남기지 않는다. `.gitignore`는 `.env`와 `.env.*`를 제외하고 `.env.example`만 허용한다.

```dotenv
CLOVASTUDIO_API_KEY=
CLOVASTUDIO_BASE_URL=https://clovastudio.stream.ntruss.com/v1/openai
CLOVASTUDIO_MODEL=HCX-005
```

## 사용과 테스트

애플리케이션 조립 시 이미 구성된 `HybridRetriever`, 선택적 `EvidenceService`, read-only 원본 DB 경로를 주입한다.

```python
from disclosure_db.disclosure_tools import build_tool_registry

registry = build_tool_registry(
    hybrid_retriever=hybrid,
    evidence_service=evidence_service,
    base_database=base_database,
)
result = registry.dispatch("search_disclosures", {"question": "사업 내용", "top_k": 10})
```

외부 서비스 없는 최소 검증 명령은 다음과 같다.

```powershell
$env:PYTHONPATH='src'
python -m unittest discover -s tests -p test_disclosure_tools.py -v
python -m unittest discover -s tests -p test_hybrid_retrieval.py -v
python -m unittest discover -s tests -p test_search_index.py -v
python -m unittest discover -s tests -p test_financial_accounts.py -v
python -m unittest discover -s tests -p test_evidence_service.py -v
python -m unittest discover -s tests -p test_hcx_function_calling.py -v
python -m unittest discover -s tests -p test_hcx_runtime_integration.py -v
```

실제 credential과 실행 중인 로컬 API가 준비된 뒤 다음 smoke helper를 사용한다. helper는 key를 읽거나 출력하지 않고 API에 질문만 전송한다. 질문의 회사·기간과 정정 접수번호는 실제 corpus에 있는 값으로 바꾼다.

```powershell
python scripts/smoke_hcx_function_calling.py --question '삼성전자 2025년 매출액은?' --expect-tool get_financial_facts --expect-answer-allowed true
python scripts/smoke_hcx_function_calling.py --question '삼성전자 사업의 내용에서 주요 변화는?' --expect-tool search_disclosures --expect-answer-allowed true
python scripts/smoke_hcx_function_calling.py --question '삼성전자의 2024년 월별 공시 건수 추세는?' --expect-tool analyze_disclosure_trend --expect-answer-allowed true
python scripts/smoke_hcx_function_calling.py --question '접수번호 2026XXXXXXXXXX의 최초·정정 공시 관계는?' --expect-tool get_correction_lineage --expect-answer-allowed true
python scripts/smoke_hcx_function_calling.py --question '공시에 근거가 없는 테스트 질문' --expect-status abstained --expect-answer-allowed false
```

기본 출력은 `query`, `selected_tool`, `evidence_status`, `answer_allowed`, `citations[].rcept_no`, 최종 `answer`, `latency_ms`와 상태/warning만 보여준다. 원본 endpoint envelope가 필요할 때만 `--show-full`을 사용한다.

## 제외 범위와 다음 단계

이 버전에는 LangChain/LangGraph, GraphRAG, 신규 reranker, NCP 배포, 전체 corpus embedding, PostgreSQL/pgvector가 없다. HCX schema/Tool Call smoke와 fake-corpus FastAPI E2E는 통과했지만 운영 corpus live E2E, NCP 재시작·배포, 반복 provider 품질·latency 평가는 아직 수행하지 않았다. Dense는 현재 Function Calling composition에서 사용하지 않으며 기존 sparse fallback만 사용한다.
