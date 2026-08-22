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

각 item은 적용 가능한 범위에서 `evidence_id(s)`, `chunk_id`, `filing_id`, 회사 식별자, 공시일/기간, section/table/locator/source path, 원문 또는 구조화 값, 단위, 품질 상태, 검색 경로/점수, 정정 상태를 담는다. 없는 값은 추정하지 않고 `null` 또는 빈 목록으로 둔다.

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
```

## 제외 범위와 다음 단계

이 버전에는 LangChain/LangGraph, HCX API·Function Calling, Answer Verifier, GraphRAG, 신규 reranker, FastAPI/NCP 배포, 전체 corpus embedding, PostgreSQL/pgvector가 없다. 다음 단계는 이 고정 Registry 계약을 LangGraph 기반 Function Calling에 연결하는 것이며, 연결 계층은 Registry의 allowlist·입력 검증·Evidence 충분성 결과를 우회하면 안 된다.
