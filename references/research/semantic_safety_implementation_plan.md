# Semantic Safety Layer Implementation Plan

> **For agentic workers:** 이 계획은 `/executing-plans`(이 데크)로 태스크 단위 실행한다. Steps use checkbox (`- [ ]`) syntax for tracking. 구현 주체는 lead — 기계적 반복 작업만 `coder` 위임(AGENTS §3·PIPELINE 구현행), 서브에이전트는 탐색·검증·병렬 기계작업만.

**Goal:** 현재 38GB SQLite 구조 SSOT를 재빌드하지 않고 보존하면서, 검증되지 않은 fact·정정본·검색 projection이 향후 답변 경로로 유입되지 않도록 코드 계약과 다음 적재 스키마를 강화한다.

**Architecture:** 구조 무결성, 검색 smoke, 검색 관련성, 의미·답변 준비도를 별도 게이트로 분리한다. 현재 DB에는 read-only 안전 조회만 적용하고, FTS external-content 및 `financial_fact`는 다음 신규 DB나 명시적 migration에서만 생성한다.

**Tech Stack:** Python 3.11+, SQLite/FTS5, `unittest`, JSON, 기존 정적 HTML 로드맵

**Implementation Status (2026-08-15):** Tasks 1–6의 코드·schema·문서 변경과 다중모델 blind 검수 계층, 19개 회귀 테스트를 완료함. 회사 검색은 filing grain에서 회사·lineage를 선확정한 뒤 FTS rowid 범위를 제한하도록 회귀를 복구했고, 현재 38GB DB에서 3개 질의 중앙값 63–116ms를 확인함. 19개 후보 문서를 모두 덮는 23개 QA 후보의 접수번호·SHA·evidence·version/source 계약을 실DB와 자동 대조해 오류 0, `candidate_review_ready=true`를 확인함. 다만 독립된 사람 승인 0건이므로 `gold_release_gate_passed=false`이며, additive migration, FTS rebuild, lineage rebuild는 실행하지 않음.

## Global Constraints

- 공식 raw 파일과 `data/derived/disclosure_corpus.sqlite`를 수정하거나 재생성하지 않는다.
- PostgreSQL·OpenSearch·dense embedding·HyperCLOVA X를 연결하지 않는다.
- 기존 `fact`는 `event_kv_candidate` 의미로만 취급하고 답변에는 `validated`만 허용한다.
- `unresolved`와 `missing_original` filing은 current/as-of 답변 대상에서 제외한다.
- 이미지 참조 source 전체를 일괄 제외하지 않고 결과에 시각 검증 경고를 전파한다.
- 모든 변경은 synthetic fixture와 기존 4개 회귀 테스트로 검증한다.

---

### Task 1: 게이트 명칭과 범위 분리

**Files:**
- Modify: `src/disclosure_db/quality.py`
- Modify: `scripts/validate_database.py`
- Test: `tests/test_safety_contracts.py`

**Interfaces:**
- Consumes: 기존 SQLite 구조와 `query_database()` sanity 결과
- Produces: `structure_gate_passed`, `retrieval_smoke_gate_passed`, `retrieval_relevance_gate_status`, `semantic_answer_gate_status`

- [x] **Step 1: 기존의 좁은 구조 검사가 포괄 품질 통과로 노출되는 실패 테스트를 작성한다.**
- [x] **Step 2: 테스트가 기존 `hard_quality_gate_passed` 계약 때문에 실패함을 확인한다.**
- [x] **Step 3: 구조·검색 smoke·미평가 게이트를 별도 필드로 반환하고 CLI 종료 코드는 구조 게이트만 사용한다.**
- [x] **Step 4: fixture validator 결과에서 검색 관련성과 의미 게이트가 통과로 오인되지 않는지 확인한다.**

### Task 2: 답변 안전 조회 경로

**Files:**
- Create: `src/disclosure_db/serving.py`
- Modify: `src/disclosure_db/pipeline.py`
- Modify: `src/disclosure_db/cli.py`
- Test: `tests/test_safety_contracts.py`

**Interfaces:**
- Consumes: `filing_version`, `source_document`, `fact`, `fact_evidence`, `table_cell`, `fragment_fts`
- Produces: `query_database(..., as_of=None, include_unsafe=False)`와 `fetch_validated_facts(...)`

- [x] **Step 1: unresolved correction과 candidate fact가 기본 조회에 노출되는 실패 테스트를 작성한다.**
- [x] **Step 2: 기본 검색은 root/resolved의 current 또는 as-of 유효본만 반환하도록 SQL을 수정한다.**
- [x] **Step 3: `fetch_validated_facts()`가 `validation_status='validated'`와 안전 lineage를 동시에 강제하도록 구현한다.**
- [x] **Step 4: 이미지 참조 수를 `requires_visual_verification`으로 반환하고 source 전체를 일괄 삭제하지 않는지 테스트한다.**

### Task 3: FTS rowid 계약 고정

**Files:**
- Modify: `src/disclosure_db/schema.py`
- Modify: `src/disclosure_db/pipeline.py`
- Test: `tests/test_safety_contracts.py`

**Interfaces:**
- Consumes: `fragment.rowid`, `fragment.text_normalized`
- Produces: `content='fragment', content_rowid='rowid'` external-content FTS와 rowid 기반 명시적 join

- [x] **Step 1: fragment rowid에 공백이 있어도 회사 검색이 올바른 evidence를 찾는 fixture를 만든다.**
- [x] **Step 2: FTS를 external-content 방식으로 선언하고 `rebuild` 명령으로 같은 rowid를 고정한다.**
- [x] **Step 3: 검색 SQL이 FTS의 중복 filing/evidence 열 대신 `fragment`를 rowid로 join하도록 바꾼다.**
- [x] **Step 4: FTS/fragment rowid·행 수 invariant와 검색 회귀 테스트를 실행한다.**

### Task 4: 별도 재무 의미 grain

**Files:**
- Modify: `src/disclosure_db/schema.py`
- Create: `sql/sqlite_semantic_layer_v1.sql`
- Test: `tests/test_safety_contracts.py`

**Interfaces:**
- Consumes: 검수된 `table_cell.evidence_id`
- Produces: `financial_fact`와 `financial_fact_evidence`; 기존 `fact`와 독립된 validated 재무 의미 층

- [x] **Step 1: account·statement·scope·period·scale·currency·validation을 요구하는 schema 테스트를 작성한다.**
- [x] **Step 2: 신규 DB schema와 기존 DB용 비파괴 additive migration SQL을 작성한다.**
- [x] **Step 3: validated financial fact가 최소 하나의 cell evidence를 가져야 한다는 조회 계약을 테스트한다.**
- [x] **Step 4: migration 파일은 검사만 하고 현재 38GB DB에는 적용하지 않는다.**

### Task 5: 정정 lineage 다음 적재 보강

**Files:**
- Modify: `src/disclosure_db/lineage.py`
- Test: `tests/test_safety_contracts.py`

**Interfaces:**
- Consumes: filing별 모든 `table_row`, `paragraph`, `html_text` fragment
- Produces: 명시적 순서의 전체 fragment에서 찾은 최초 제출일 기반 correction chain

- [x] **Step 1: 최초 제출일이 41번째 이후 fragment에 있는 사건성 정정 fixture를 작성한다.**
- [x] **Step 2: 40개 제한을 제거하고 `source_id, sequence_no, evidence_id` 순서를 SQL에 명시한다.**
- [x] **Step 3: 단독 `공시서류제출일`이 원본 날짜로 오인되지 않는지 parser 테스트를 추가한다.**
- [x] **Step 4: false link 0을 우선하며 모호한 후보는 계속 unresolved로 남는지 확인한다.**

### Task 6: 로드맵·핸드오프 동기화

**Files:**
- Modify: `references/research/system_build_roadmap.html`
- Modify: `references/research/roadmap/roadmap-data.js`
- Modify: `references/research/team_handoff_data_db.md`
- Modify: `README.md`

**Interfaces:**
- Consumes: Tasks 1–5의 실제 구현·테스트 결과
- Produces: 구조 SSOT 동결과 의미 층 구축 순서를 동일하게 설명하는 팀 공유 자료

- [x] **Step 1: `hard quality` 표현을 구조 게이트로 교정한다.**
- [x] **Step 2: 현재 위치를 `안전 차단→QA 후보 23건 승인→financial_fact→정정→FTS→RAG`로 표시한다.**
- [x] **Step 3: 적용 완료와 다음 전수 적재 때 효력이 생기는 변경을 분리해 쓴다.**
- [x] **Step 4: HTML·JavaScript·Python 테스트와 문서 링크를 최종 검증한다.**
