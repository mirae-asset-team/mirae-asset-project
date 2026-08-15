# Semantic Safety Layer Implementation Plan

> **For agentic workers:** 이 계획은 `/executing-plans`(이 데크)로 태스크 단위 실행한다. Steps use checkbox (`- [ ]`) syntax for tracking. 구현 주체는 lead — 기계적 반복 작업만 `coder` 위임(AGENTS §3·PIPELINE 구현행), 서브에이전트는 탐색·검증·병렬 기계작업만.

**Goal:** 현재 38GB SQLite 구조 SSOT를 재빌드하지 않고 보존하면서, 검증되지 않은 fact·정정본·검색 projection이 향후 답변 경로로 유입되지 않도록 코드 계약과 다음 적재 스키마를 강화한다.

**Architecture:** 구조 무결성, 검색 smoke, 검색 관련성, 의미·답변 준비도를 별도 게이트로 분리한다. 원본 DB에는 read-only 안전 조회만 적용하고, FTS external-content 및 `financial_fact`는 원본의 SQLite online backup 사본에 명시적 migration으로 생성한다.

**Tech Stack:** Python 3.11+, SQLite/FTS5, `unittest`, JSON, 기존 정적 HTML 로드맵

**Implementation Status (2026-08-16):** Tasks 1–6에 이어 원본 보존형 migration runner, external-content FTS 동기화 trigger, 별도 `financial_fact` grain, 의미 schema validator, 승인 Gold 기반 retrieval 평가기, DART TD/rowspan/unit 파서 회귀, PostgreSQL 후보 DDL parity, CI를 구현함. 23개 QA는 모두 대표님 원문 검수와 DB 계약 검증을 거쳐 `human_verified/approved`이며 release gate를 통과함. 원본 구조 원장의 evidence-addressable 8문항·9근거 조건부 평가에서 target Recall@20과 question-complete Recall@20은 모두 1.0, MRR@20은 0.381922임. 전체 회귀 테스트는 23개임. 복제 DB 전수 migration·검증의 정확한 결과는 `data/derived/database_migration_semantic_v1.json`과 `database_validation_semantic_v1.json`을 정본으로 삼음.

## Global Constraints

- 공식 raw 파일과 `data/derived/disclosure_corpus.sqlite`를 수정하거나 재생성하지 않는다.
- PostgreSQL·OpenSearch·dense embedding·HyperCLOVA X를 연결하지 않는다.
- 기존 `fact`는 `event_kv_candidate` 의미로만 취급하고 답변에는 `validated`만 허용한다.
- `unresolved`와 `missing_original` filing은 current/as-of 답변 대상에서 제외한다.
- 이미지 참조 source 전체를 일괄 제외하지 않고 결과에 시각 검증 경고를 전파한다.
- 모든 변경은 synthetic fixture, 실제 원문 표본, 전체 회귀 테스트와 전수 DB 감사 보고서로 검증한다.

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

### Task 7: 원본 보존형 semantic v1 migration

**Files:**
- Create: `src/disclosure_db/migration.py`
- Create: `scripts/migrate_database.py`
- Modify: `sql/sqlite_semantic_layer_v1.sql`

- [x] **Step 1: 원본과 출력 경로가 같거나 출력이 이미 존재하면 중단한다.**
- [x] **Step 2: SQLite online backup으로 일관된 사본을 만들고 원본 fingerprint를 기록한다.**
- [x] **Step 3: 별도 의미 grain·강제 trigger·lineage·FTS를 사본에만 적용한다.**
- [x] **Step 4: migration 전후 schema·핵심 행 수·변경 lineage·quick check·FK를 JSON으로 남긴다.**

### Task 8: 승인 Gold 검색 기준선

**Files:**
- Create: `src/disclosure_db/retrieval_evaluation.py`
- Create: `scripts/evaluate_retrieval.py`
- Modify: `scripts/validate_database.py`
- Modify: `config/evaluation_contract.json`

- [x] **Step 1: 사람 승인과 DB evidence 계약을 모두 통과한 Gold만 읽는다.**
- [x] **Step 2: 표 셀 evidence를 같은 filing·table·row의 검색 fragment로 결정론적으로 연결한다.**
- [x] **Step 3: 회사와 후보 filing을 고정한 범위에서 per-term FTS와 RRF를 평가한다.**
- [x] **Step 4: 전체 entity resolution/RAG 성능으로 과장하지 않도록 평가 범위를 결과에 기록한다.**

### Task 9: 파서·운영 후보 보강

**Files:**
- Modify: `src/disclosure_db/parsers.py`
- Modify: `src/disclosure_db/contracts.py`
- Modify: `sql/postgresql_schema.sql`

- [x] **Step 1: 주요사항·지분 공시의 TD label과 rowspan row header를 보존한다.**
- [x] **Step 2: 명시적 `단위:`와 허용 단위만 채택해 날짜가 unit이 되는 오류를 막는다.**
- [x] **Step 3: 실제 DART 표본 두 건에서 header·unit 결과를 probe한다.**
- [x] **Step 4: PostgreSQL 후보 DDL에 구조 원장·의미 grain·감사·증거 trigger를 맞춘다.**

### Task 10: 배포 전 검증과 CI

**Files:**
- Create: `.github/workflows/ci.yml`
- Modify: `README.md`
- Modify: `data/derived/README.md`
- Modify: `references/research/team_handoff_data_db.md`
- Modify: `references/research/system_build_roadmap.html`
- Modify: `references/research/roadmap/roadmap-data.js`

- [x] **Step 1: unittest·compileall·JSON·JavaScript 문법 검사를 자동화하거나 실행한다.**
- [x] **Step 2: 복제 DB의 migration report와 semantic validator를 확인한다.**
- [x] **Step 3: 원본 파일 크기·수정시각 불변과 Git 대용량 제외를 확인한다.**
- [ ] **Step 4: 의도한 파일만 커밋·푸시하고 Draft PR CI를 확인한다.**
