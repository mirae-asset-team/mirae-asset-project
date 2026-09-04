# 미래에셋 AI 공모전 공시 데이터셋 EDA 및 구축 기준서

> 기준일: 2026-08-12  
> 분석 대상: `data/public/official_dataset/raw/corpus/`  
> 목적: 원본 데이터 재조사 없이 전처리·검색 인덱스·평가 데이터 설계를 시작할 수 있도록 데이터 구조, 품질, 예외, 파싱 규칙을 한 문서에 고정한다.  
> 원본 변경 여부: 없음. 모든 분석은 읽기 전용으로 수행했다.

## 목차

1. [결론 요약](#1-결론-요약)
2. [분석 범위와 방법](#2-분석-범위와-방법)
3. [현재 데이터 폴더 구조](#3-현재-데이터-폴더-구조)
4. [전체 규모와 구성](#4-전체-규모와-구성)
5. [루트 메타데이터 파일](#5-루트-메타데이터-파일)
6. [카테고리별 상세 구조](#6-카테고리별-상세-구조)
7. [실제 문서 포맷과 파싱 난점](#7-실제-문서-포맷과-파싱-난점)
8. [정정공시와 버전 계보](#8-정정공시와-버전-계보)
9. [데이터 품질 진단](#9-데이터-품질-진단)
10. [권장 데이터 계층과 파일 규약](#10-권장-데이터-계층과-파일-규약)
11. [권장 정규화 스키마](#11-권장-정규화-스키마)
12. [카테고리별 파서 구현 규칙](#12-카테고리별-파서-구현-규칙)
13. [검색·청킹·LLM 연결 규칙](#13-검색청킹llm-연결-규칙)
14. [EDA 및 전처리 검증 체크리스트](#14-eda-및-전처리-검증-체크리스트)
15. [구현 순서와 완료 조건](#15-구현-순서와-완료-조건)
16. [미확인 사항과 남은 위험](#16-미확인-사항과-남은-위험)

---

## 1. 결론 요약

이 데이터셋은 **70개 기업, 4개 공시 카테고리, 4,204개 공시 접수건, 4,622개 실제 문서 파일**로 구성된다. 파일 경로, 접수번호, 기업 식별자, 카테고리 수량은 현재 기준으로 정합하다. 그러나 단순한 `XML → 텍스트 → 벡터DB` 파이프라인으로 처리하면 문서 손실과 잘못된 답변이 발생한다.

핵심 판단은 다음과 같다.

1. `manifest.jsonl`을 **선정된 코퍼스의 유일한 문서 목록**으로 사용해야 한다. 회사별 `list_*.json`은 수집 이력이며, 여기에 있는 전체 22,980여 건을 검색 코퍼스로 넣으면 과제 범위 밖 문서가 대량 혼입된다.
2. 확장자가 포맷을 보장하지 않는다. `exchange/*.xml` 1,469개는 실제로 HTML이며, 선언은 EUC-KR이지만 실제 바이트는 UTF-8이다.
3. DART XML 3,147개 중 1,838개(58.41%)가 엄격 XML 파서에서 실패한다. 원본을 고치지 말고 복구 파서 또는 파생 사본의 최소 수선과 수선 로그가 필요하다.
4. 정기공시는 본문 외 감사보고서 첨부가 붙을 수 있어 접수 1건이 1~3개 파일이다. 파일을 접수건과 동일시하면 안 된다.
5. `is_correction=true`만으로 최신 유효본을 결정할 수 없다. 정기공시 2건은 원본이 코퍼스에 없고, 사건성 공시는 같은 유형이 반복되므로 제목만으로 원본과 정정본을 연결할 수 없다.
6. 공시 답변의 최소 인용 단위는 파일이 아니라 `(접수번호, 문서 역할, 섹션, 표, 행, 열 또는 문장 범위)`여야 한다.
7. 수치 계산은 LLM이 아니라 코드가 수행하고, 입력 셀의 기간·단위·근거 ID를 결과까지 전파해야 한다.

### 즉시 채택할 운영 원칙

- `raw/`는 불변 보존한다.
- 실제 포맷을 바이트와 루트 태그로 판별한다.
- 파싱 결과에는 항상 원문 해시와 파서 버전을 기록한다.
- 표는 평문으로 뭉개지 않고 병합 셀을 펼친 격자와 헤더 계보를 보존한다.
- 정정 계보가 확정되지 않은 문서는 `unresolved`로 남긴다.
- 근거가 부족하거나 충돌하면 답을 생성하지 않고 `insufficient_evidence`를 반환한다.

---

## 2. 분석 범위와 방법

### 2.1 전수 확인한 항목

- `manifest.jsonl` 4,204행의 스키마, 자료형, 중복, 결측, 식별자 형식, 경로 존재 여부, `n_files` 정합성
- `universe.csv` 70행의 기업 코드·종목 코드·시장·업종·섹터·카테고리별 건수
- 4개 카테고리의 280개 회사별 `list_*.json`
- 문서 파일 4,622개의 파일명, 크기, 확장자, 바이트 인코딩, 루트 형태 및 SHA-256 중복 여부
- DART XML 3,147개의 엄격 XML 파싱 성공 여부
- 거래소 HTML 1,469개의 표·행·셀·`xforms_input` 구조
- PDF 3개의 암호화 여부, 페이지 수, 텍스트 추출 가능 여부
- 정정 여부, 접수일, 기준연도·기준월, 회사별·카테고리별 분포

### 2.2 표본 확인한 항목

- 정기·주요·대량보유 문서의 제목 계층과 대표 표 구조
- 거래소 각 세부 유형의 필드명과 템플릿 변형
- PDF 대체 문서와 viewer HTML의 본문 포함 여부

### 2.3 해석 시 주의

- 태그 수 통계는 엄격 XML 파싱이 불가능한 문서도 포함하기 위해 원시 바이트의 정확한 태그 서명을 센 진단값이다.
- 이미지 참조의 실제 바이너리는 데이터셋에 없어 이미지 내용은 검증하지 못했다.
- PDF는 텍스트 추출 가능 여부까지 확인했으나 모든 페이지의 표 복원 정확도와 시각적 OCR 품질은 미확인이다.

---

## 3. 현재 데이터 폴더 구조

```text
data/public/official_dataset/raw/corpus/
├─ README.md
├─ data_filter.md
├─ manifest.jsonl
├─ universe.csv
├─ universe.xlsx
└─ raw/
   ├─ periodic/
   │  └─ {corp_code}_{corp_name}/
   │     ├─ list_A.json
   │     ├─ {rcept_no}.xml
   │     ├─ {rcept_no}_00760.xml       # 선택적 감사보고서
   │     ├─ {rcept_no}_00761.xml       # 선택적 연결감사보고서
   │     ├─ {rcept_no}.pdf             # 예외적 대체 원문
   │     └─ {rcept_no}_viewer.html     # 예외적 viewer 또는 본문 HTML
   ├─ major/
   │  └─ {corp_code}_{corp_name}/
   │     ├─ list_B001.json
   │     └─ {rcept_no}.xml
   ├─ exchange/
   │  └─ {corp_code}_{corp_name}/
   │     ├─ list_I.json
   │     └─ {rcept_no}.xml             # 확장자는 XML, 실제 내용은 HTML
   └─ holding/
      └─ {corp_code}_{corp_name}/
         ├─ list_D.json
         └─ {rcept_no}.xml
```

각 카테고리는 정확히 70개 회사 폴더를 가진다. 회사 폴더명은 탐색 편의를 위한 표시값일 뿐, 조인 키로 사용하면 안 된다. 조인과 식별은 `corp_code`, `stock_code`, `rcept_no`로 수행한다.

### 폴더명 예외

- JYP는 폴더와 `corp_name`이 `JYP Ent`, 일부 제출인 표기가 `JYP Ent.`이다.
- 상장명과 법인명은 다를 수 있다. 예: `현대차 ↔ 현대자동차`, `KT ↔ 케이티`, `엔씨소프트 ↔ NC`, `LS ELECTRIC ↔ 엘에스일렉트릭`.
- 따라서 이름 기반 탐색은 별칭 테이블을 거치고, 최종 식별은 코드로 확정해야 한다.

---

## 4. 전체 규모와 구성

### 4.1 카테고리별 인벤토리

| 카테고리 | 접수건 | 실제 문서 파일 | 정정건 | 정정률 | 회사별 목록 JSON 포함 폴더 용량 |
|---|---:|---:|---:|---:|---:|
| 정기공시 `periodic` | 1,054 | 1,472 | 159 | 15.09% | 5,303,925,163 B |
| 주요사항 `major` | 598 | 598 | 173 | 28.93% | 22,886,003 B |
| 거래소 `exchange` | 1,469 | 1,469 | 631 | 42.95% | 19,165,940 B |
| 대량보유 `holding` | 1,083 | 1,083 | 41 | 3.79% | 219,568,191 B |
| **합계** | **4,204** | **4,622** | **1,004** | **23.88%** | **5,565,545,297 B** |

폴더 파일 수에는 280개의 `list_*.json`과 루트 메타데이터가 별도로 존재한다. 위 실제 문서 파일은 `manifest.n_files`를 합산한 값이다.

### 4.2 실제 문서 확장자

| 확장자 | 파일 수 | 실제 의미 |
|---|---:|---|
| `.xml` | 4,616 | DART XML 3,147개 + 실제 HTML인 거래소 문서 1,469개 |
| `.html` | 3 | 정기공시 PDF 대체건의 viewer; 1개는 본문, 2개는 viewer shell |
| `.pdf` | 3 | 정기공시 대체 원문; 모두 텍스트 추출 가능 |

### 4.3 접수일 분포

| 연도 | 건수 |
|---|---:|
| 2023 | 1,093 |
| 2024 | 1,337 |
| 2025 | 1,328 |
| 2026 | 446 |

- 전체 접수일 범위: 2023-01-02 ~ 2026-06-19
- 2026-03-31 이후 접수 78건은 전부 정기공시이다.
- 이 78건은 2026년 1분기보고서 73건과 2025년 사업보고서 정정 5건이다.
- 즉 사건성 공시는 수집 종료일로 제한됐지만, 정기공시는 **대상 회계기간**을 기준으로 이후 제출분까지 포함한다. 질의의 기준일과 회계기간을 혼동하면 안 된다.

### 4.4 회사별 편중

- 회사당 접수건: 최소 18, 중앙값 49.5, 최대 200
- 상위: 셀트리온 200, 한화오션 150, 대우건설 150, 삼성E&A 141, 현대건설 135
- 하위: 세아베스틸지주 18, 시프트업 19, 삼성전기 20, POSCO홀딩스 20, CJ제일제당 21

이 편중 때문에 문서 단위 무작위 train/test 분할은 특정 기업과 템플릿 누출을 만든다. 평가셋은 회사·문서·시간 기준 분할을 병행해야 한다.

### 4.5 기업 유니버스

- 기업 수: 70개
- 시장: KOSPI 61개, KOSDAQ 9개
- 결산월: 전 기업 12월
- 산업 대분류: 산업재 23, 커뮤니케이션서비스 12, 금융 8, IT 7, 소재 7, 건강관리 6, 필수소비재 4, 경기관련소비재 3
- 세부 섹터: 20개
- `universe`의 `n_periodic`, `n_major`, `n_exchange`, `n_holding`은 manifest 집계와 전부 일치한다.

---

## 5. 루트 메타데이터 파일

### 5.1 `manifest.jsonl`

선정된 공시 코퍼스의 기준 원장이다. 한 행의 grain은 **공시 접수건 1건**이며 파일 1개가 아니다.

| 필드 | 의미 | 관찰된 규칙 | 전처리 규칙 |
|---|---|---|---|
| `doc_id` | 내부 문서 ID | 4,204개 전부 고유 | 변경 금지, 기본키 후보 |
| `corp_code` | DART 법인 코드 | 8자리 문자열 | 숫자로 변환 금지 |
| `corp_name` | 법인 표시명 | universe와 일치 | 별칭 검색용, 조인키 금지 |
| `listed_name` | 상장 표시명 | 일부 법인명과 다름 | UI/질의 해석용 |
| `stock_code` | 종목 코드 | 6자리 문자열 | 숫자로 변환 금지 |
| `industry` | 산업 대분류 | 8개 범주 | 필터용 |
| `sector` | 세부 섹터 | 20개 범주 | 필터 및 층화 평가용 |
| `doc_group` | 공시 카테고리 | periodic/major/exchange/holding | 필수 분기 키 |
| `doc_subtype` | 세부 유형 | major 598건만 전부 null | major는 XML의 ACODE로 보완 |
| `report_nm` | 원 공시명 | 정정 접두사 포함 | 원문 보존 + 정규화명 별도 생성 |
| `is_correction` | 정정 여부 | boolean | 버전 계보 입력, 최신본 판정값은 아님 |
| `rcept_no` | 접수번호 | 14자리, 전부 고유 | 공시 근거의 핵심키 |
| `rcept_dt` | 접수일 | YYYYMMDD | 날짜형 파생 컬럼 추가 |
| `flr_nm` | 제출인 | holding에서는 발행사가 아닐 수 있음 | 발행사 ID로 사용 금지 |
| `base_year` | 대상 회계연도 | periodic만 존재 | nullable integer |
| `base_month` | 대상 월 | periodic만 존재 | 3/6/9/12 |
| `file_path` | 접수건 폴더 상대경로 | 전부 존재 | OS 독립 경로로 해석 |
| `file_format` | 대표 포맷 | xml 또는 pdf+html | 실제 파일 sniffing 병행 |
| `n_files` | 접수건 파일 수 | 1~3 | 실제 파일 수와 전부 일치 |

#### 무결성 결과

- 행 중복: 0
- `doc_id` 중복: 0
- `rcept_no` 중복: 0
- 존재하지 않는 경로: 0
- `n_files` 불일치: 0
- 잘못된 법인·종목·접수번호 형식: 0
- 잘못된 달력 날짜: 0
- universe와의 코드·이름·산업·섹터 불일치: 0
- 결측은 의도된 두 영역뿐이다.
  - `doc_subtype`: 598건, 모두 major
  - `base_year`, `base_month`: 각 3,150건, 모두 non-periodic

#### 정정 접두사

| 접두사 | 건수 | 해석 |
|---|---:|---|
| 없음 | 3,194 | 원공시 또는 접두사 없는 공시 |
| `[기재정정]` | 1,004 | 모두 `is_correction=true` |
| `[첨부추가]` | 6 | `is_correction=false`; 첨부 추가를 내용 정정과 동일 취급하면 안 됨 |

### 5.2 `universe.csv`와 `universe.xlsx`

두 파일은 기업 유니버스의 CSV/Excel 표현이다. 자동화의 기준은 diff와 버전 관리가 쉬운 CSV로 두고, Excel은 사람 검토용으로 사용한다.

필드 17개:

```text
corp_code, stock_code, corp_name, listed_name, corp_eng_name,
market, industry, sector_no, sector, listing_date, fiscal_month,
market_cap, n_periodic, n_major, n_exchange, n_holding, note
```

품질 상태:

- 70행, 코드·종목·법인명 모두 고유
- `note` 외 결측 없음; `note`는 65건이 공란
- 카테고리별 건수는 manifest와 전부 일치
- 특기사항 5건은 PDF 대체 원문, 사명 변경, 폴더명 문장부호 차이를 설명한다.

### 5.3 회사별 `list_*.json`

이 파일들은 DART/KIND 목록 조회 결과를 저장한 **수집 provenance**이다. manifest에 선정되지 않은 문서가 다수 포함된다.

| 파일 | 전체 고유 목록 항목 | manifest 선정 | 미선정 | 빈 목록 파일 |
|---|---:|---:|---:|---:|
| `list_A.json` | 1,166 | 1,054 | 112 | 0 |
| `list_B001.json` | 639 | 598 | 41 | 15 |
| `list_I.json` | 10,921 | 1,469 | 9,452 | 0 |
| `list_D.json` | 10,254 | 1,083 | 9,171 | 0 |

모든 manifest 접수번호는 대응 목록에 존재한다. 반대로 목록 전체를 본문 코퍼스로 넣으면 안 된다. 특히 거래소와 대량보유 목록은 미선정 항목이 각각 9천 건 이상이다.

추가 예외:

- `list_I.json`의 공시명 7,546건은 끝에 공백이 있다. 비교할 때 `strip()`하되 raw 문자열은 보존한다.
- `list_B001.json` 15개가 빈 목록이다. 해당 기업이 선택 기간에 주요사항 공시가 없다는 의미일 수 있으며 오류로 단정하지 않는다.

---

## 6. 카테고리별 상세 구조

### 6.1 정기공시 `periodic`

#### 구성

| 세부 유형 | 접수건 | 설명 |
|---|---:|---|
| 분기보고서 | 529 | 기준월 3월 또는 9월 |
| 반기보고서 | 234 | 기준월 6월 |
| 사업보고서 | 291 | 기준월 12월 |

접수건별 파일 수:

- 1개: 841건
- 2개: 8건
- 3개: 205건

파일 역할:

| 패턴 | 수 | 역할 |
|---|---:|---|
| `{rcept_no}.xml` | 1,051 | 정기공시 본문 |
| `{rcept_no}_00760.xml` | 208 | 감사보고서 |
| `{rcept_no}_00761.xml` | 207 | 연결감사보고서 |
| `{rcept_no}.pdf` | 3 | XML 대체 원문 |
| `{rcept_no}_viewer.html` | 3 | viewer/본문 HTML |

사업보고서 291건 중 205건은 3개 파일, 6건은 2개 파일, 80건은 1개 파일이다. 첨부 누락을 즉시 오류로 간주할 수는 없으며 `attachment_expected`와 `attachment_present`를 분리해야 한다.

#### 회계기간 분포

| 대상기간 | 원공시 | 정정 | 합계 |
|---|---:|---:|---:|
| 2023 Q1 | 66 | 10 | 76 |
| 2023 H1 | 67 | 8 | 75 |
| 2023 Q3 | 68 | 10 | 78 |
| 2023 FY | 68 | 41 | 109 |
| 2024 Q1 | 68 | 9 | 77 |
| 2024 H1 | 69 | 13 | 82 |
| 2024 Q3 | 70 | 6 | 76 |
| 2024 FY | 70 | 27 | 97 |
| 2025 Q1 | 70 | 7 | 77 |
| 2025 H1 | 70 | 7 | 77 |
| 2025 Q3 | 70 | 2 | 72 |
| 2025 FY | 69 | 16 | 85 |
| 2026 Q1 | 70 | 3 | 73 |

#### 크기와 구조

- 문서당 크기: 중앙값 4.05 MB, 90백분위 9.12 MB, 최대 36.50 MB
- 문서당 표 수: 중앙값 965, 90백분위 2,252, 최대 5,043
- 문서당 `TD`: 중앙값 8,343, 90백분위 27,065, 최대 93,964
- `IMAGE` 참조가 있는 파일: 1,417개; 참조 총 4,258개
- XBRL/Inline XBRL 표지(`contextRef`, `xbrli`, `ix`)는 발견되지 않았다.

정기공시는 텍스트 문서라기보다 대규모 표와 주석의 집합이다. 고정 길이 텍스트 청킹보다 제목 경로와 표 구조 기반 청킹이 우선이다.

### 6.2 주요사항 `major`

모든 접수건은 `{rcept_no}.xml` 한 파일이다. manifest의 `doc_subtype`은 전부 null이지만 XML의 `ACODE`와 `DOCUMENT-NAME`으로 사건 유형을 복원할 수 있다.

주요 유형:

| ACODE | 유형 | 건수 | 정정건 |
|---|---|---:|---:|
| 11333 | 자기주식 처분 결정 | 157 | 15 |
| 11332 | 자기주식 취득 결정 | 91 | 15 |
| 11329 | 상각형 조건부자본증권 발행 | 71 | 49 |
| 11306 | 유상증자 결정 | 55 | 37 |
| 11334 | 자기주식취득 신탁계약 체결 | 47 | 1 |
| 11335 | 자기주식취득 신탁계약 해지 | 43 | 1 |
| 11344 | 합병 결정 | 24 | 9 |
| 11324 | 전환사채 발행 결정 | 22 | 10 |
| 11347 | 주식교환·이전 결정 | 12 | 6 |
| 11345 | 회사분할 결정 | 12 | 7 |

그 밖에 분할합병, 타법인 주식 양수, 감자, 소송, 무상증자, 교환사채, 유형자산 양수·양도 등이 있다. 구현에서는 29개 관찰 ACODE를 사전으로 관리하되 미등록 ACODE를 버리지 않고 `unknown`과 원 코드를 보존한다.

구조 특성:

- 문서당 크기 중앙값 30.6 KB, 최대 265.9 KB
- 표 중앙값 10개, 문단 중앙값 13개
- 보통 제목 2개: `주요사항보고서 / 거래소 신고의무 사항`, 실제 사건명
- 동일 회사가 같은 사건 유형을 반복하므로 `회사+공시명`은 사건키가 아니다.

### 6.3 거래소 `exchange`

세부 유형:

| 유형 | 건수 | 정정건 | 주요 필드 |
|---|---:|---:|---|
| 공급계약체결 | 1,106 | 563 | 계약명, 금액, 최근매출, 비율, 상대방, 지역, 기간, 계약일 |
| 공급계약해지 | 20 | 0 | 해지금액, 최근매출, 비율, 상대방, 해지사유·일자 |
| 신규시설투자등 | 43 | 15 | 투자금액, 자기자본, 비율, 목적, 기간, 의사결정일 |
| 투자판단관련주요경영사항 | 300 | 53 | 제목, 주요내용, 사실·결정일, 이사회, 기밀; 임상시험형 변형 포함 |

핵심 포맷:

- 파일명은 `.xml`이지만 1,469개 전부 루트가 `<html>`이다.
- 값은 `<input>`이 아니라 `<span class="xforms_input">...</span>`에 들어 있다.
- 문서당 표 중앙값 1개, 90백분위 4개, 최대 5개
- `xforms_input` 중앙값 19개, 90백분위 33개, 최대 53개
- 정정 문서는 정정일, 원 공시 제출일, 정정사유, 변경 전·후 표가 추가된다.
- 빈 라벨은 다중 행이나 부모 헤더를 상속하는 값일 수 있다. 빈 문자열로 즉시 폐기하면 안 된다.

공급계약 템플릿은 구형·신형, 조건부·확정 금액, 생산방식 등의 변형이 존재한다. 셀 위치 번호를 하드코딩하지 말고 행·열 헤더 경로로 필드를 찾는다.

### 6.4 대량보유 `holding`

모든 접수건은 `{rcept_no}.xml` 한 파일이다.

| ACODE | 유형 | 건수 |
|---|---|---:|
| 00636 | 주식등의 대량보유상황보고서(일반) | 607 |
| 00637 | 주식등의 대량보유상황보고서(약식) | 476 |

구조 특성:

- 문서당 크기 중앙값 114.6 KB, 최대 9.57 MB
- 표 중앙값 50개, `TE` 중앙값 539개, 최대 61,302개
- 일반적으로 제1부 개요, 제2부 보유현황, 제3부 변동·취득자금 등으로 구성
- `flr_nm`은 1,083건 전부 대상 발행사 `corp_name`과 다르다. 여기서 제출인은 대주주·기관·보고자이며, 발행사 식별값이 아니다.
- 대표 제출인은 국민연금, 삼성물산, 에코프로 등으로 여러 대상 기업에 반복된다.

대량보유 질문은 최소 세 엔터티를 분리해야 한다.

```text
issuer_corp_code       대상 상장회사
reporting_entity       제출·보고 주체
holder_or_related_party 실제 보유자 또는 특별관계자
```

---

## 7. 실제 문서 포맷과 파싱 난점

### 7.1 DART XML: periodic, major, holding

공통 특성:

- 루트: `<DOCUMENT>`
- 선언 인코딩: UTF-8
- 실제 바이트: 전부 UTF-8 디코딩 가능
- 스키마: `dart3.xsd`, `dart4.xsd`가 혼재하며 전환 기간이 겹친다.
- 주요 태그: `SECTION-1/2/3/4`, `TITLE`, `TABLE`, `TR`, `TH`, `TD`, `TE`, `TU`, `P`, `PGBRK`, `IMAGE`, `IMG`
- 표 병합: `ROWSPAN`, `COLSPAN`
- 단위: `AUNIT`, `AUNITVALUE`

#### 엄격 XML 파싱 결과

| 카테고리 | XML 수 | 성공 | 실패 | 실패율 |
|---|---:|---:|---:|---:|
| periodic | 1,466 | 85 | 1,381 | 94.20% |
| major | 598 | 409 | 189 | 31.61% |
| holding | 1,083 | 815 | 268 | 24.75% |
| **합계** | **3,147** | **1,309** | **1,838** | **58.41%** |

실패 원인:

- invalid token 1,643건: 본문에 이스케이프되지 않은 `&` 등
- mismatched tag 195건: `<별표3-2>`처럼 문구가 태그로 오인되는 형태 등

따라서 `ElementTree.parse()` 같은 엄격 파서를 기본 경로로 쓰면 대규모 문서 손실이 발생한다.

#### 권장 복구 정책

1. 원시 바이트 SHA-256을 계산한다.
2. UTF-8로 명시 디코딩한다.
3. recovery 모드를 지원하는 XML/HTML 파서로 구조를 우선 복원한다.
4. 필요한 경우에만 파생 텍스트에서 문제 문자를 최소 수선한다.
5. 수선마다 `offset`, `before`, `after`, `reason`, `repair_version`을 기록한다.
6. 원본 파일은 절대 덮어쓰지 않는다.
7. 복원 전후 제목·표·문단 수와 대표 텍스트 해시를 비교해 과잉 삭제를 감지한다.

### 7.2 거래소 HTML의 인코딩 함정

거래소 문서는 다음 세 조건이 동시에 존재한다.

- 확장자 `.xml`
- 루트 `<html>`
- meta charset `euc-kr`
- 실제 바이트는 1,469개 전부 UTF-8로 정상 디코딩되고 CP949로는 정상 디코딩되지 않음

HTML 파서에 바이트를 그대로 넘기면 잘못된 meta 선언을 따라 한글이 깨질 수 있다. **UTF-8로 먼저 디코딩한 문자열**을 HTML 파서에 전달해야 한다.

실제 포맷 판별 순서:

```text
1. 파일 시그니처와 BOM 검사
2. UTF-8 strict decode 시도
3. 선두 공백·선언 제거 후 루트 태그 확인
4. DOCUMENT이면 DART XML recovery parser
5. html이면 exchange HTML parser
6. %PDF이면 PDF parser
7. 어느 분기에도 속하지 않으면 quarantine
```

### 7.3 PDF·viewer HTML 대체건

| 기업 | 대상 공시 | 접수번호 | PDF | viewer HTML |
|---|---|---|---:|---|
| KB금융 | 2025 사업보고서 정정 | 20260619000667 | 1,085쪽, 텍스트 추출 가능 | 본문 없는 viewer shell |
| 한화오션 | 2024 Q1 정정 | 20240514001522 | 252쪽, 텍스트 추출 가능 | 본문 없는 viewer shell |
| 한화에어로스페이스 | 2026 Q1 | 20260513000860 | 447쪽, 텍스트 추출 가능 | 본문 포함 HTML |

KB금융과 한화오션 viewer HTML은 JS 목차와 `rcpNo`, `dcmNo`, `eleId`, `offset`, `length` 같은 위치 정보만 있고 실제 본문 표는 외부 DART viewer 호출이 필요하다. 과제 데이터 외 외부 호출을 전제로 하지 않으므로 이 두 건은 PDF를 본문 기준으로 삼고 viewer는 목차 메타데이터로만 사용한다.

PDF 처리 결과에는 반드시 `page_no`, `bbox` 또는 최소한 `page_no + line/span`을 남긴다. 표 추출 실패 시 일반 텍스트를 정상 표 데이터처럼 제공하지 말고 `table_parse_status=failed`를 표시한다.

### 7.4 이미지 참조 누락

- periodic: `IMAGE` 참조 4,258개
- holding: `IMAGE` 참조 479개
- XML의 `IMG` 값은 `대표이사확인서_23.1q.jpg` 같은 파일명이다.
- 실제 이미지 바이너리는 코퍼스에 없다.

이미지 파일명과 캡션은 메타데이터로 보존할 수 있지만, 이미지 내용을 읽었다고 간주하면 안 된다. 이미지가 핵심 근거인 질문은 `missing_binary_asset` 경고와 함께 답변 보류 대상이다.

### 7.5 파일 중복

문서 파일 4,622개의 SHA-256은 모두 고유했다. 동일 바이트 중복 파일은 없다. 정정 문서를 해시 중복으로 제거할 수 없으며, 해시는 무결성과 revision 식별에만 사용한다.

---

## 8. 정정공시와 버전 계보

### 8.1 현재 manifest의 한계

manifest에는 `is_correction`과 접수번호는 있지만 `parent_rcept_no`, `supersedes`, `effective_from`, `effective_to`가 없다. `[기재정정]` 접두사를 제거하는 것은 검색 정규화일 뿐 원본 연결이 아니다.

### 8.2 정기공시

회사·세부유형·기준연도·기준월 조합의 버전 그룹은 897개다.

| 그룹 내 버전 수 | 그룹 수 |
|---:|---:|
| 1 | 762 |
| 2 | 115 |
| 3 | 18 |
| 4 | 2 |

정정 159건 중 157건은 같은 기간의 선행 원본 후보가 정확히 하나다. 다음 2건은 원본이 코퍼스에 없다.

- 한화솔루션 2023 Q1 정정, 접수번호 `20230522000371`
- 한화솔루션 2025 사업보고서 정정, 접수번호 `20260325001401`

이 두 건은 정정본 단독으로 사용할 수는 있으나, 변경 전후 비교나 원문 완전성은 보장하지 못한다.

### 8.3 사건성 공시

major·exchange는 같은 회사가 같은 사건 유형을 반복한다. 회사·유형·날짜만으로 정정 원본을 연결하면 서로 다른 계약이나 자금조달 결정을 합칠 수 있다.

연결 우선순위:

1. 정정표에 명시된 `정정관련 공시서류제출일` 또는 원 접수번호
2. 사건 고유 필드의 조합
   - 공급계약: 계약명, 상대방, 최초 계약일
   - 시설투자: 투자 목적, 투자 기간, 최초 결정일
   - 증자·사채: 발행 종류, 의사결정일, 금액
3. 동일 필드 스냅샷의 유사도와 시간 순서
4. 확정할 수 없으면 `unresolved`; 임의로 가장 가까운 이전 문서에 연결하지 않음

holding은 대상 발행사 외에 제출인과 보유자 그룹을 함께 써야 한다.

### 8.4 권장 버전 그래프

```text
FilingEvent
├─ event_id
├─ issuer_corp_code
├─ event_type
└─ versions[]
   ├─ rcept_no
   ├─ filed_at
   ├─ correction_type
   ├─ parent_rcept_no?
   ├─ lineage_status: resolved | unresolved | missing_original
   ├─ effective_from
   ├─ effective_to?
   └─ source_hashes[]
```

질의 기준일 `as_of`가 있으면 그 시점에 제출돼 있던 버전을 선택하고, 기준일이 없으면 계보가 해결된 최신 유효본을 선택한다. 원본과 정정본을 모두 검색 후보로 노출하되 최종 답에는 사용한 버전과 과거 버전 여부를 명시한다.

---

## 9. 데이터 품질 진단

### 9.1 품질 판정표

| 심각도 | 판정 | 증거 | 영향 | 조치 |
|---|---|---|---|---|
| Critical | 엄격 XML 파싱 실패 | DART XML 58.41% 실패 | 대규모 문서 누락 | recovery parser, 수선 로그, 커버리지 게이트 |
| Critical | 정정 계보 불완전 | parent 필드 없음, 정기 원본 누락 2건, 사건성 다중 후보 | 과거 값을 최신값으로 답할 수 있음 | 버전 그래프와 unresolved 상태 |
| High | 거래소 포맷·인코딩 불일치 | `.xml`이 실제 HTML, meta EUC-KR/실제 UTF-8 | 한글 깨짐, 빈 추출 | 바이트 sniffing + UTF-8 명시 디코딩 |
| High | 목록 JSON의 범위 외 문서 | 미선정 18,776건 | 검색 코퍼스 오염 | manifest allowlist만 본문 인덱싱 |
| High | PDF viewer 2건에 본문 없음 | KB금융·한화오션 shell | 빈 문서 인덱싱 | PDF를 본문 기준으로 사용 |
| High | 이미지 바이너리 부재 | 참조 4,737개, 파일 없음 | 이미지 기반 근거 손실 | 누락 플래그, 관련 질문 거절 |
| High | holding 제출인 의미 차이 | 전 1,083건 issuer와 다름 | 회사 귀속 오류 | issuer/reporter/holder 분리 |
| Medium | major subtype 결측 | 598건 전부 null | 유형 필터 불가 | ACODE에서 파생, 원 코드 보존 |
| Medium | 스키마 버전 혼재 | dart3/dart4와 FORMULA 버전 다수 | 단일 XPath 취약 | capability 기반 파싱 |
| Medium | 문서 크기·표 수 편중 | periodic 최대 36.5 MB, 표 5,043개 | 메모리·지연 급증 | streaming/section 단위 처리 |
| Medium | 표 병합·빈 라벨 | ROWSPAN/COLSPAN, 부모 헤더 | 필드와 값 오연결 | 격자 복원과 헤더 계보 |
| Low | 목록 공시명 후행 공백 | exchange 7,546건 | 문자열 비교 실패 | 정규화 컬럼 별도 생성 |

### 9.2 품질 차원별 현재 평가

| 차원 | 상태 | 근거 |
|---|---|---|
| 완전성 | 부분 충족 | manifest 경로는 완전하나 이미지 바이너리와 일부 원본 정정 계보 부재 |
| 유일성 | 충족 | doc_id·rcept_no·파일 해시 중복 없음 |
| 유효성 | 부분 충족 | 식별자·날짜는 유효, 엄격 XML 문법은 다수 비유효 |
| 일관성 | 부분 충족 | manifest-universe 일치, 확장자·실제 포맷과 인코딩 선언 불일치 |
| 최신성 | 용도 의존 | 사건성은 2026-03-31까지, 정기공시는 회계기간 기준 후속 제출 포함 |
| 계보성 | 미충족 | 접수·경로는 추적 가능하나 정정 parent/effective version 부재 |

---

## 10. 권장 데이터 계층과 파일 규약

현재 `raw/corpus`는 그대로 보존하고 다음 파생 계층을 추가한다.

```text
data/
├─ public/official_dataset/raw/corpus/   # 제공 원본, 절대 수정 금지
├─ interim/
│  ├─ inventory/                        # 파일 인벤토리·해시·실제 포맷
│  ├─ repaired/                         # 최소 수선 파생본과 repair log
│  └─ parsed/                           # 문서 구조를 보존한 파싱 결과
├─ processed/
│  ├─ filings/                          # 접수건·버전 메타데이터
│  ├─ fragments/                        # 섹션·문단·표·셀 단위 근거
│  ├─ facts/                            # 정규화된 수치·사건 사실
│  └─ lineage/                          # 원본-정정 버전 그래프
├─ indexes/
│  ├─ lexical/                          # BM25
│  ├─ dense/                            # embedding
│  └─ metadata/                         # 기업·기간·유형 필터
└─ evaluation/
   ├─ gold/                             # 사람 검증 QA와 근거
   ├─ splits/                           # company/time/document split
   └─ runs/                             # 실험 설정·예측·지표
```

### 10.1 raw 불변성 규칙

- 파일명·내용·줄바꿈·인코딩을 변경하지 않는다.
- raw 파일별 SHA-256을 inventory에 기록한다.
- 파생 결과는 `source_path`, `source_sha256`, `parser_name`, `parser_version`, `parsed_at`을 가진다.
- 재파싱 시 기존 결과를 묵시적으로 덮지 말고 파서 버전으로 revision을 구분한다.

### 10.2 권장 파일 포맷

| 데이터 | 권장 포맷 | 이유 |
|---|---|---|
| 인벤토리·filing·lineage | Parquet + 검토용 CSV | 자료형·압축·대량 조인 |
| 계층 문서·복구 로그 | JSONL | 문서별 독립 처리와 append 가능 |
| 표 셀·facts | Parquet | 열 기반 집계와 수치형 보존 |
| gold QA | JSONL | 질문·답·근거 배열 표현 |
| 검색 인덱스 | 엔진 고유 포맷 + index manifest | 재현 가능한 버전 고정 |

Excel은 검토용 복사본으로만 사용하고 파이프라인의 기준 입력으로 삼지 않는다.

### 10.3 파일명 규약

파생 파일명에는 사람이 읽는 회사명 대신 안전한 식별자를 사용한다.

```text
{rcept_no}__{role}__{source_sha256_12}.{ext}
```

예시 역할:

- `main`
- `audit_separate_00760`
- `audit_consolidated_00761`
- `pdf_main`
- `viewer_toc`

---

## 11. 권장 정규화 스키마

### 11.1 Filing

```text
filing_id              = rcept_no
doc_id
issuer_corp_code
stock_code
issuer_name
listed_name
doc_group
doc_subtype_raw
doc_subtype_normalized
acode
report_name_raw
report_name_normalized
filed_at
base_period_start?
base_period_end?
is_correction
correction_type?
event_id?
parent_rcept_no?
lineage_status
source_files[]
```

### 11.2 SourceDocument

```text
source_id
filing_id
role
path
extension
detected_format
declared_encoding?
detected_encoding
sha256
byte_size
schema_version?
parse_status
repair_count
warnings[]
```

### 11.3 Fragment

```text
evidence_id
filing_id
source_id
section_path[]
fragment_type          paragraph | table | table_row | cell | image_ref
page_no?
char_start?
char_end?
table_id?
row_index?
column_index?
row_header_path[]
column_header_path[]
unit?
text_raw
text_normalized
source_hash
```

`evidence_id`는 위치 필드와 source hash에서 결정론적으로 만든다. LLM이 접수번호나 evidence ID를 생성하게 하지 않고 검색기가 발급한 ID만 선택하도록 한다.

### 11.4 Fact

```text
fact_id
filing_id
event_id?
fact_type
subject
predicate
value_raw
value_numeric?
unit?
currency?
period_start?
period_end?
as_of_date?
scope?                 consolidated | separate | unknown
evidence_ids[]
extraction_method
validation_status
```

수치가 `-`, 빈칸, `해당사항 없음`인 경우 0으로 바꾸지 않는다. 각각 `not_reported`, `not_applicable`, `unknown`을 구분한다.

### 11.5 Table

표는 Markdown 문자열 하나로만 저장하지 않는다.

```text
table_id
caption
section_path[]
unit_text
grid[][]
cells[]
  - row, col, rowspan, colspan
  - raw_text, normalized_text
  - row_header_path[], column_header_path[]
  - evidence_id
parse_status
```

표를 LLM용 텍스트로 직렬화할 때도 표 ID, 단위, 열 헤더, 행 헤더를 각 값과 함께 반복해 문맥 손실을 줄인다.

---

## 12. 카테고리별 파서 구현 규칙

### 12.1 공통 진입점

```text
manifest row
  → resolve source files
  → hash and sniff actual format
  → choose category/format parser
  → preserve hierarchy and coordinates
  → validate coverage
  → write parsed revision
```

### 12.2 periodic

- exact `{rcept_no}` 파일을 main으로 분류한다.
- `_00760`, `_00761`은 별도 역할로 보존하고 main과 섞지 않는다.
- `SECTION-*`와 `TITLE`을 이용해 제목 경로를 만든다.
- 재무제표·주석 표는 단위, 연결/별도, 당기/전기, 누적/분기를 명시한다.
- 빈 TITLE도 위치를 기록한 뒤 검색 텍스트에서만 제외한다.
- 표가 큰 경우 행 묶음으로 나누되 헤더와 단위를 각 chunk에 복제한다.
- PDF 대체건은 page 기반 evidence를 생성한다.

### 12.3 major

- `ACODE`와 `DOCUMENT-NAME`으로 subtype을 파생한다.
- manifest null을 덮어쓰지 말고 raw/null과 derived 값을 함께 저장한다.
- 공시 유형별 필드 extractor를 적용하되 미등록 표는 generic grid로 보존한다.
- 의사결정일, 금액, 발행조건 등 사건키 후보와 정정표를 별도로 추출한다.

### 12.4 exchange

- 확장자를 무시하고 UTF-8 문자열로 명시 디코딩한다.
- HTML recovery parser를 사용한다.
- `span.xforms_input` 값을 추출한다.
- `rowspan/colspan`을 펼친 뒤 상위 헤더를 빈 라벨 셀에 전파한다.
- 정정표의 변경 전·후를 별도 필드로 만들고 최신값만 남기지 않는다.
- `투자판단관련주요경영사항`은 자유형·임상시험형을 구분하되 원 표를 항상 보존한다.

### 12.5 holding

- issuer, reporter, holder, special related parties를 분리한다.
- 보유 주식 수와 비율은 기준일과 변동 전·후를 함께 저장한다.
- 일반/약식 ACODE를 보존한다.
- 대형 문서는 streaming 또는 section 단위 처리한다.
- 보고자 이름을 대상 회사 필터로 사용하지 않는다.

### 12.6 실패 처리

파서가 실패한 문서를 빈 텍스트로 정상 처리하면 안 된다.

```text
parse_status:
  success
  partial
  failed
  unsupported

coverage:
  section_count
  table_count
  paragraph_count
  extracted_char_count
  source_byte_count
  missing_assets[]
```

`partial`과 `failed`는 검색 인덱스에서 별도 필터와 경고를 갖는다.

---

## 13. 검색·청킹·LLM 연결 규칙

### 13.1 문서 계층

```text
Company
└─ FilingEvent
   └─ FilingVersion (rcept_no)
      ├─ SourceDocument
      │  └─ Section
      │     ├─ ParagraphFragment
      │     └─ Table
      │        ├─ RowFragment
      │        └─ CellEvidence
      └─ ExtractedFact
```

이 계층을 유지해야 “어느 회사의 어느 시점 공시, 어느 표의 어느 셀인가”를 답변까지 추적할 수 있다.

### 13.2 청킹

- 문단: 제목 경계를 넘지 않는다.
- 표: 표 전체가 작으면 전체, 크면 의미 있는 행 묶음으로 나눈다.
- 각 chunk에 회사, 접수번호, 접수일, 대상기간, 카테고리, 제목 경로, 표 단위를 붙인다.
- 정정본과 원본을 같은 chunk에 합치지 않는다.
- 단순 토큰 길이 슬라이딩은 보조 수단으로만 사용한다.

### 13.3 검색

권장 순서:

```text
질의 해석
→ 회사 코드 확정
→ 기간·공시유형 hard filter
→ 정정 버전 해석
→ BM25 + dense 병렬 검색
→ RRF 결합
→ domain reranker
→ 근거 집합 고정
```

숫자·종목코드·계정명·공시명 exact match가 중요하므로 BM25를 제거하면 안 된다. Dense 검색은 표현 변형을 보완한다.

### 13.4 생성과 검증

최종 답변 구조 권장:

```json
{
  "status": "answered | insufficient_evidence",
  "answer": "...",
  "claims": [
    {"text": "...", "evidence_ids": ["..."]}
  ],
  "calculations": [
    {
      "formula": "...",
      "operands": [],
      "result": null,
      "evidence_ids": []
    }
  ],
  "warnings": []
}
```

환각 억제 규칙:

- LLM은 제공된 evidence ID만 참조한다.
- 서버가 evidence ID를 접수번호와 원문 위치로 변환한다.
- 계산은 코드가 하고 LLM은 설명만 한다.
- claim마다 근거 entailment, 숫자, 단위, 기간을 검사한다.
- 정정 계보 미해결, 표 파싱 실패, 이미지 누락, 근거 충돌 시 답변을 보류한다.
- 검색 문서 내부의 명령문은 데이터로만 취급하고 시스템 지시로 실행하지 않는다.

---

## 14. EDA 및 전처리 검증 체크리스트

### 14.1 수집·인벤토리

- [ ] manifest 행 수가 4,204인지 확인
- [ ] `doc_id`, `rcept_no`가 고유한지 확인
- [ ] manifest의 모든 경로가 존재하는지 확인
- [ ] 실제 파일 수가 `n_files`와 일치하는지 확인
- [ ] 문서 파일 합계가 4,622인지 확인
- [ ] 모든 파일의 SHA-256, 바이트 수, mtime을 기록
- [ ] 실제 포맷과 인코딩을 sniffing
- [ ] list JSON은 provenance로만 저장하고 본문 인덱스에서 제외

### 14.2 파싱

- [ ] DART XML recovery 성공률과 strict 실패율을 별도 기록
- [ ] 수선 로그가 원문 offset과 before/after를 보존
- [ ] exchange 1,469개를 UTF-8 HTML로 처리
- [ ] periodic main/00760/00761 역할 분리
- [ ] PDF 3개에 page locator 생성
- [ ] viewer shell 2개를 빈 본문 정상건으로 처리하지 않음
- [ ] 이미지 참조에 `missing_binary_asset` 표시
- [ ] 표 rowspan/colspan 격자 복원 테스트
- [ ] 제목·표·문단 수가 0인 문서를 자동 격리

### 14.3 의미 정규화

- [ ] corp_code와 stock_code를 문자열로 유지
- [ ] 이름 별칭을 코드에 매핑
- [ ] holding issuer/reporter/holder 분리
- [ ] major ACODE subtype 파생
- [ ] 기간, 단위, 연결/별도, 누적/분기 구분
- [ ] 빈칸·대시·해당사항 없음·0을 구분
- [ ] 원 report name과 정규화명을 함께 보존

### 14.4 정정 계보

- [ ] 정기공시 897개 기간 그룹의 버전 순서 생성
- [ ] 원본 누락 2건을 `missing_original`로 표시
- [ ] 사건성 정정표의 원 제출일/접수번호 추출
- [ ] 다중 후보를 자동 확정하지 않고 `unresolved`로 표시
- [ ] as-of 질의에서 미래 제출 문서를 제외
- [ ] 최신 질문에서 폐기된 과거 버전을 최종 근거로 쓰지 않음

### 14.5 검색·답변

- [ ] metadata filter 이전/이후 검색 결과 비교
- [ ] BM25, dense, hybrid, reranker ablation
- [ ] 접수번호·섹션·표·셀 위치가 citation에 포함
- [ ] 모든 수치 답변의 operand·단위·기간·식을 검증
- [ ] 근거 없는 질문과 미래예측 질문에 답변 거절
- [ ] 정정 충돌, 누락 이미지, 파싱 partial 사례 평가셋 포함
- [ ] 회사·시간·문서 기준 누출 방지 split

---

## 15. 구현 순서와 완료 조건

### 단계 1. 불변 인벤토리

산출물:

- 파일별 경로, 해시, 크기, 감지 포맷, 감지 인코딩
- manifest와 universe의 정규화 Parquet

완료 조건:

- 4,204개 접수건과 4,622개 문서 파일이 모두 연결됨
- missing path와 `n_files` mismatch가 0
- 동일 입력으로 재실행했을 때 해시와 통계가 동일

### 단계 2. 카테고리별 lossless parser

산출물:

- Filing, SourceDocument, Section, Paragraph, Table, Cell
- repair log와 parse coverage

완료 조건:

- 4개 카테고리별 golden document 테스트 통과
- strict XML 실패 문서도 recovery 결과가 생성되며 과잉 손실 검사 통과
- exchange 한글 깨짐 0
- PDF 3개 모두 page evidence 생성
- 실패 문서가 조용히 빈 텍스트가 되지 않음

### 단계 3. 정정 계보와 fact layer

산출물:

- FilingEvent/Version 그래프
- 카테고리별 핵심 fact와 evidence 연결

완료 조건:

- 정기공시 모든 기간 그룹 상태 부여
- 원본 누락 2건 명시
- 사건성 정정의 자동 연결은 high-confidence 사례만 수행
- fact 100%가 하나 이상의 evidence ID를 가짐

### 단계 4. 검색 baseline

산출물:

- BM25, dense, hybrid+RRF, reranker 실험
- gold relevance set

완료 조건:

- 카테고리와 난이도로 층화한 retrieval Recall@k 측정
- 기업·기간·유형 오염률 측정
- 가장 큰 문서에서도 메모리와 지연 예산 내 처리

### 단계 5. 근거 제한 답변

산출물:

- 구조화 응답, 계산기, citation validator, abstention

완료 조건:

- 모든 검증 가능한 claim에 evidence ID 존재
- 숫자 결과가 source cell과 코드 계산으로 재현됨
- unanswerable인데 답한 비율을 별도 핵심 지표로 관리
- 정정·PDF·누락 이미지·prompt injection 회귀 테스트 통과

---

## 16. 미확인 사항과 남은 위험

1. **이미지 내용 미확인**: XML이 가리키는 이미지 파일이 제공되지 않아 서명 외 도표·도식이 포함됐는지 확정할 수 없다.
2. **PDF 표 복원 품질 미확인**: 3개 PDF 모두 텍스트는 추출되지만 표 셀 병합과 읽기 순서의 전수 정확도는 별도 검증이 필요하다.
3. **정정 원본 외부 존재 여부 미확인**: 코퍼스에 없는 한화솔루션 원본 2건을 외부에서 보완할 수 있는지는 과제의 외부 데이터 제한과 함께 확인해야 한다.
4. **공식 평가 질문 분포 미확인**: 실제 질문이 서술형, 표 수치형, 정정 비교형 중 어디에 집중되는지에 따라 파서와 검색 우선순위가 달라질 수 있다.
5. **수선 정확도 미검증**: 엄격 XML 실패 원인은 전수 집계했으나 recovery parser별 텍스트·표 보존율은 golden set으로 비교해야 한다.

이 문서의 통계는 현재 제공된 데이터셋에 대한 기준선이다. 데이터가 추가되면 같은 검증을 재실행하고 문서 상단의 기준일, 전체 건수, 품질 판정표를 함께 갱신해야 한다.

