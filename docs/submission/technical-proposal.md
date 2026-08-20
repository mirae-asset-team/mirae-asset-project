# MIRA: 근거 검증형 공시 AI Agent

제10회 2026 미래에셋증권 AI Festival - 공시 Agent 기술제안서

팀명: 미래에셋 공시 Agent 팀
작성 기준일: 2026-08-20
제안 상태: 로컬 결정론적 품질 게이트 GO / 최종 제출 외부 게이트 진행 중

## 1. 제안 요약

MIRA는 공시를 단순 검색하거나 LLM에 긴 문서를 그대로 전달하는 시스템이 아니다. 기업, 회계기간, 공시일, 정정 이력, 연결·별도 범위, 단위와 원문 근거를 먼저 구조화하고, 검증된 evidence bundle만 HyperCLOVA X에 전달하는 공시 전용 AI Agent다. 수치 계산은 `Decimal` 기반 결정론적 도구가 수행하고, 생성 결과는 답변 가능성·수치·단위·접수번호·인용 귀속을 다시 검사한다. 근거가 부족하면 자연스럽게 꾸며내는 대신 명시적으로 답변을 보류한다.

| 공식 요구 | MIRA의 대응 |
|---|---|
| 검색 및 정보 추출 | 기업·기간·공시유형 구조화, sparse index와 SSOT fallback |
| 종합 비교 및 연산 | 정정 계보, 기준시점 조회, `Decimal` 계산과 근거 전파 |
| 근거 기반 답변 | 답변별 공시명·공시일·접수번호, citation membership 검증 |
| 환각·공격 방지 | 숫자·인용 hard gate, 범위 밖·주입 공격 답변 보류 |
| HyperCLOVA X 전용 | 검증된 근거의 자연어 표현에만 HyperCLOVA X 사용 |
| 제출·재현 | FastAPI, Docker Compose, README·runbook, 평가 API |

핵심 성과는 38.8GB 원본 공시 DB를 변경하지 않은 채 4,204건 공시와 8,437,771개 fragment를 서비스하고, 결정론적 300문항에서 300/300, 허위 숫자·안전하지 않은 답변·미확인 인용·다른 공시 인용·평가 오류를 모두 0건으로 만든 것이다. 이 결과는 로컬 결정론적 경로의 증거이며, HyperCLOVA X 실모델 300문항 통과로 과장하지 않는다.

<!-- pagebreak -->

## 2. 문제 정의

공시는 시간 의존적이고 표 중심이며 정정으로 효력이 바뀐다. 같은 계정명이라도 기간, 연결 범위, 단위가 다르면 비교할 수 없고, 같은 제목의 거래소공시도 서로 다른 사건일 수 있다. 일반적인 문서 RAG는 유사 문장을 잘 찾더라도 다음 오류를 구조적으로 막기 어렵다.

- 회계기간과 공시 제출일을 혼동해 미래 공시를 과거 질문에 사용
- 정정 전 수치와 정정 후 수치를 섞거나 별개의 계약을 한 사건으로 연결
- 표의 행·열·단위를 잃어 숫자는 맞지만 의미가 다른 답변 생성
- 검색되지 않은 숫자나 접수번호를 LLM이 자연스럽게 보완
- 공시에 없는 미래 예측·투자 의견에 무리하게 답변

MIRA의 문제 정의는 “가장 유사한 문장을 찾는가”가 아니라 “질문의 시점에 유효한 공시 버전에서, 요구한 사실과 계산에 필요한 근거를 완전하게 모으고, 그 근거만으로 검증 가능한 답을 만드는가”다.

### 설계 원칙

1. 원본 공시 DB는 immutable SSOT로 유지한다.
2. 정정·기간·단위·회계 범위를 검색 전후에 모두 보존한다.
3. 숫자와 계산은 LLM이 아니라 결정론적 코드가 담당한다.
4. HyperCLOVA X는 검증된 사실을 읽기 쉬운 한국어로 표현한다.
5. 검증 실패는 fallback 또는 답변 보류로 닫힌다.
6. 평가 결과는 deterministic, provider, public deployment를 분리해 보고한다.

<!-- pagebreak -->

## 3. 시스템 구성

```mermaid
flowchart LR
  Q[사용자 질의] --> P[질의 구조화]
  P --> F[기업·기간·공시 필터]
  F --> R[공시 검색·재순위]
  R --> L[정정 계보·기준시점]
  L --> C[수치 추출·Decimal 계산]
  C --> E[Evidence Bundle]
  E --> H[HyperCLOVA X]
  H --> V[수치·인용·안전 검증]
  V --> A[답변·근거·한계]
```

### 계층별 책임

| 계층 | 책임 | 실패 시 동작 |
|---|---|---|
| Query Planner | 기업, 기간, 기준시점, 연산, 정정 정책 해석 | 모호성 reason code와 보수적 검색 |
| Evidence Service | 구조화 fact 우선, sparse/SSOT 검색, 후보 결합 | index 이상 시 immutable SSOT fallback |
| Lineage Resolver | 최초·정정·철회 관계와 유효 구간 선택 | 계보 불명확 시 최신이라고 단정하지 않음 |
| Calculator | 차이·증감률·비율·합계를 `Decimal`로 계산 | 근거 부족·단위 불일치 시 계산 거절 |
| HyperCLOVA X | 제한된 evidence bundle을 자연어로 표현 | 구조화 질문은 결정론적 fallback, 서술형은 보류 |
| Answer Verifier | 숫자, 단위, citation membership, 안전성 검사 | 검증 실패 시 답변 보류 |

원본 DB, overlay, search index, attestation은 컨테이너에 read-only로 마운트한다. 웹 UI와 API는 같은 FastAPI 프로세스에서 제공해 별도 프론트엔드 서버·회원 DB·로그인 비용을 만들지 않는다.

<!-- pagebreak -->

## 4. 공시 데이터와 정정 이력 처리

### 데이터 자산

| 항목 | 검증 값 |
|---|---:|
| 원본 SQLite | 38,773,280,768 bytes |
| 공시 | 4,204건 |
| source | 4,622건 |
| fragment | 8,437,771개 |
| table cell | 36,697,165개 |
| event fact | 1,423개 |
| sparse search row | 329,323개 |

원본은 SHA-256, 크기, 신뢰 mtime으로 attestation한다. overlay와 index는 staging에서 구축하고 SQLite integrity·foreign key·schema·대표 검색을 통과한 후보만 복구 가능한 rename으로 승격한다. 서비스 시에는 `mode=ro`, `immutable=1`, `PRAGMA query_only=ON`을 사용한다.

재무 질의 대상은 76개 검색명과 중복을 제거한 70개 법인으로 구분한다. 최신 유효 사업보고서는 69개 법인에서 선택됐고, 검증된 핵심 재무 수치는 1,191건이다. 최신 연도 기준 coverage는 매출 계열 63/70, 영업이익·자산·부채·자본 67/70, 당기순이익 66/70이다. 따라서 개별 기업 질의는 검증된 수치로 답하지만, 기업 간 개수·순위는 해당 지표가 70/70이 되기 전까지 답변을 보류한다.

### 시간과 버전 의미론

- 재무상태표는 특정 시점, 손익·현금흐름은 기간으로 해석한다.
- 공시일 필터와 회계기간 필터를 별도 필드로 유지한다.
- `as_of` 이후 제출된 공시는 과거 기준시점 답변에 사용하지 않는다.
- 정정표의 명시적 최초 제출일과 공시별 사건 식별자를 우선한다.
- 제목이 같다는 이유만으로 사건을 연결하지 않으며 후보가 여러 개면 모호 상태로 남긴다.
- “최초와 정정 후 각각” 질문은 같은 event chain의 두 버전만 선택한다.

이 구조로 삼성바이오로직스 Pfizer 위탁생산계약의 최초 계약금액과 정정 후 계약금액을 서로 다른 계약과 섞지 않고 각각의 원문 셀에 귀속한다.

<!-- pagebreak -->

## 5. 질의 처리와 안전한 생성

### 처리 흐름

1. 질문에서 기업·기간·기준시점·연산·정정 의도를 추출한다.
2. 구조화 financial/event fact를 먼저 조회한다.
3. 부족한 서술 근거는 sparse index와 immutable SSOT에서 검색한다.
4. 후보를 공시 ID, 기간, 사건 계보와 evidence ID로 묶는다.
5. 수치·계산 질문은 필요한 근거가 모두 있을 때만 `Decimal` 계산한다.
6. HyperCLOVA X에는 질문과 제한된 evidence bundle만 전달한다.
7. 출력 JSON schema를 확인하고 숫자·단위·인용·답변 가능성을 재검증한다.
8. 검증에 실패하면 근거 없는 문장을 반환하지 않고 보류한다.

### 생성 모델의 제한된 역할

HyperCLOVA X는 공시 지식을 외워 답하는 권위자가 아니다. 검색·정정·계산 계층이 만든 검증 상태를 사용자 친화적 문장으로 바꾸는 표현 계층이다. 모델 출력이 prose로 감싼 JSON 한 개일 때는 안전하게 추출하지만, 객체가 여러 개거나 필드·타입이 다르면 fail-closed한다. provider 장애 시 구조화 수치는 결정론적 renderer로 답하고, 일반 서술형은 공시 근거를 확인할 수 없다고 답한다.

### 정보한계 대응

- 공시에 없는 주가 전망, 목표주가, 투자 의견은 답변하지 않는다.
- 계약금액만 있고 영업이익 기여도가 없으면 계산해 만들지 않는다.
- PDF 표가 사람 검증되지 않았거나 계보가 모호하면 답변을 보류한다.
- 프롬프트 주입이 공시 범위·인용 계약을 바꾸도록 허용하지 않는다.

<!-- pagebreak -->

## 6. 사용자 경험과 평가 API

### 익명 공개 웹 UI

주소를 아는 사용자는 로그인 없이 질문할 수 있다. 왼쪽에는 새 대화, 브라우저별 대화 목록과 검색이 있고, 중앙에는 질문·답변·공시 근거가 표시된다. 대화 기록은 서버가 아니라 사용자 브라우저 `localStorage`에만 저장되며 최대 50개 대화, 대화당 100개 메시지로 제한한다. 모바일에서는 사이드바를 접을 수 있고 키보드 focus를 복원한다.

답변은 `근거 확인됨`과 `답변 보류`를 시각적으로 구분한다. 재무 카드에는 회계연도, 연결·별도, 원 공시 항목명, 값·단위, 공시명·제출일·접수번호와 펼쳐볼 수 있는 표 셀·위치를 표시한다. 서비스 정보에는 76개 검색 대상, 70개 법인, 69개 선택 사업보고서와 지표별 coverage를 표시한다. 오류는 입력 오류·과다 요청·서버 혼잡·일시 장애로 구분한다. 외부 CDN과 임의 HTML 삽입을 사용하지 않고 CSP와 text-only DOM 렌더링을 적용한다.

### API 표면

| 경로 | 용도 |
|---|---|
| `GET /` | 익명 웹 UI |
| `GET /health` | readiness와 provider boolean |
| `POST /query` | 상세 평가·웹 UI JSON |
| `GET /answer` | 공식 예시 5-field 평가 API |
| `POST /v1/answer` | 기존 호환 경로 |

`GET /answer`는 `question_id`, `question`, `retrieved_context`, `think_trace`, `answer`를 반환한다. `think_trace`는 비공개 chain-of-thought가 아니라 “질의 구조화 -> 공시 검색 -> 정정·수치 검증 -> 근거 귀속” 같은 공개 처리 단계다.

### 사용자 시나리오

1. 사용자가 “2025년 연결 매출액”을 질문한다.
2. Agent가 기업과 회계기간·연결 범위를 해석하고 올바른 정기공시를 찾는다.
3. 검증된 표 셀과 단위를 선택해 답변과 근거 공시를 함께 제시한다.
4. 사용자가 정정 전후 계약금액을 질문하면 같은 사건 계보의 두 버전을 비교한다.
5. 공시에서 확인할 수 없는 질문은 이유와 함께 답변을 보류한다.

<!-- pagebreak -->

## 7. 평가 결과

### 완료된 로컬 게이트

| 검증 | 결과 |
|---|---:|
| Python 전체 회귀 | 297 passed, 1 skipped, 19 subtests |
| Frontend Node 테스트 | 16/16 passed |
| 결정론적 stress | 300/300 passed |
| 허위 숫자 | 0 |
| 안전하지 않은 답변 | 0 |
| 미확인 citation | 0 |
| 다른 공시 citation | 0 |
| 평가 오류 | 0 |
| Answerability agreement | 1.0 |
| Numeric exactness | 1.0 |
| Citation precision / recall | 1.0 / 1.0 |
| 실제 브라우저 XSS | literal text, DOM image 0, dialog 0 |

대표 로컬 직접 실행 질의는 약 4.8초였고, 기존 NCP API-only 배포의 대표 정정 질문은 외부 Windows 네트워크에서 약 2.09초에 두 값과 두 근거를 반환했다. Windows Docker Desktop의 D-drive bind mount에서는 192~208초가 측정돼 기능은 통과했지만 성능 기준으로 사용하지 않았다. Linux NCP의 기존 API-only 배포에서는 내부 질의가 약 1.66~1.71초였다.

### 해석에 필요한 한계

- deterministic 300은 검증된 자료에서 규칙적으로 생성한 회귀다. 미지의 자유형 질문과 HyperCLOVA X 품질을 대신하지 않는다.
- 별도 audited/holdout 검색 평가의 target Recall@20은 약 0.765로, unseen open 질문 확장이 필요하다.
- HyperCLOVA X HTTP 200은 과거 확인했지만 당시 출력 schema가 최종 계약을 만족하지 않았다.
- 교체된 credential이 없어 현재 provider-required schema smoke와 provider 300은 실행하지 않았다.

<!-- pagebreak -->

## 8. 보안·신뢰성과 운영

### 데이터·근거 무결성

- base/overlay/index/attestation read-only mount
- 시작 시 attestation·overlay base match·search revision 검증
- API 질의는 원본을 수정하지 않는 immutable SQLite URI 사용
- 후보 build와 live promotion 분리, 이전 파일 보존
- 응답 citation은 검색 bundle에 포함된 evidence ID만 허용

### 익명 공개 운영

- IP별 분당 120건, IP별 동시 4건, 전체 동시 8건 기본값
- rate limit은 429와 `Retry-After`, 전체 혼잡은 503 반환
- 질문 최대 4,000자, 서버 측 대화 저장·회원 DB·로그인 없음
- `X-Forwarded-For`를 신뢰하지 않고 직접 소켓 IP만 사용
- CSP, frame 차단, MIME sniffing 방지, referrer 차단
- credential, 공인 IP, raw provider 응답과 질문 원문을 검증 문서에 기록하지 않음

### 장애 복구

검색 index가 drift되면 immutable SSOT로 fallback한다. provider timeout이나 schema mismatch는 구조화 수치의 결정론적 fallback 또는 서술형 답변 보류로 처리한다. 컨테이너 재시작과 서버 재부팅 후 데이터 자동 마운트·read-only 권한·health·대표 질의를 재검증하는 runbook을 제공한다.

<!-- pagebreak -->

## 9. 재현 가능한 개발·배포

저장소는 Python 3.11+, FastAPI, SQLite/FTS5, Docker Compose를 사용한다. DB는 이미지에 포함하지 않고 외부 read-only volume으로 연결한다. `.env.example`에는 경로와 빈 key만 있고 실제 `.env`는 Git에서 제외한다.

### 재현 절차

1. 검증된 base, overlay, search index, attestation을 준비한다.
2. `PYTHONPATH=src`에서 전체 테스트와 compileall을 실행한다.
3. 로컬 서버 또는 Docker Compose를 시작한다.
4. smoke script로 HTML/CSP, health, 정상 숫자, 답변 보류, 주입 방어를 확인한다.
5. 외부 배포 전 원격 파일의 hash와 `0444`, Compose read-only mount를 재확인한다.
6. 교체된 HyperCLOVA X credential로 provider-required 300을 별도 출력 경로에서 실행한다.
7. 외부 네트워크에서 웹 UI, 평가 API, rate limit, restart와 reboot를 검증한다.

### Provider 최종 GO 조건

provider가 실제 구성되고 300개가 모두 완료돼야 한다. 300/300, 기존 hard gate, answerability 0.95 이상, numeric exactness 1.0, citation precision 1.0, citation recall 0.9 이상, end-to-end p95 10초 이하를 모두 만족할 때만 `provider_gate_passed=true`다. provider-disabled checkpoint는 required 실행에 재사용하지 않는다.

<!-- pagebreak -->

## 10. 기대효과와 확장성

### 기대효과

- 분석 시간 단축: 여러 버전의 공시와 표를 수작업으로 대조하는 시간을 줄인다.
- 신뢰 가능한 설명: 결론과 함께 근거 공시·공시일·접수번호를 제공한다.
- 정정 리스크 감소: 최신 수치만 보여주는 대신 최초·정정 이력과 기준시점을 보존한다.
- 안전한 업무 보조: 공시에 없는 예측과 투자 의견을 생성하지 않고 한계를 고지한다.
- 재현 가능한 평가: 데이터 identity, 코드 commit, stress manifest와 결과를 분리 기록한다.

### 확장 방향

1. 사람 검증 Gold를 확대해 open 질문과 PDF 표의 Recall@20을 개선한다.
2. HyperCLOVA X reranker와 생성기를 독립 ablation해 비용·지연·품질 효과를 측정한다.
3. 설명형 비교 질문을 위한 evidence completeness와 문장별 citation을 확장한다.
4. HTTPS reverse proxy를 추가하되 신뢰 proxy와 실제 client IP 계약을 먼저 검증한다.
5. 대회 범위 밖 확장 시에도 외부 데이터 혼합 여부를 출처별로 명시하고 정책을 분리한다.

### 현재 완료 경계

로컬 엔진, 익명 웹 UI, Docker 패키지, 공식 예시 API와 fail-closed provider runner는 구현·테스트됐다. 기존 공개 서버는 API-only 버전이며 새 UI·`GET /answer`의 NCP 재배포는 인증 세션 부재로 차단돼 있다. 최종 제출 GO에는 교체된 HyperCLOVA X provider schema smoke·300문항과 인증된 NCP 코드 전용 재배포가 추가로 필요하다.

<!-- pagebreak -->

## 11. 제출 항목 대응표

| 공식 제출 항목 | 준비 산출물 | 상태 |
|---|---|---|
| 소스코드·재현 환경 | source, tests, Dockerfile, compose.yaml, README | 완료 |
| 기술제안서 | 본 Markdown 및 PDF | 완료 |
| 평가 API 정보 | `GET /answer`, `POST /query`, runbook | 로컬 완료 / 공개 재배포 필요 |
| HyperCLOVA X 전용 | adapter, schema verifier, required runner | 실 credential 검증 필요 |
| 공개 API 서버 | 기존 API-only NCP endpoint | 새 UI/API commit 재배포 필요 |

## 12. 참고자료

1. 미래에셋증권, 「제10회 2026 미래에셋증권 AI Festival - 과제 소개: 공시 Agent」, 2026.07.
2. NAVER Cloud, CLOVA Studio / HyperCLOVA X 공식 API 가이드.
3. 저장소 `docs/operations/contest-release-checklist.md`, 2026-08-20 측정 증거.
4. 저장소 `docs/development-log.md`, 단계별 TDD·평가·배포 기록.

본 제안서의 수치와 상태는 작성 기준일의 추적 가능한 테스트·릴리스 증거를 기준으로 하며, 미실행 외부 게이트를 통과로 표시하지 않는다.
