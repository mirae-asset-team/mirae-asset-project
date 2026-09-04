# 재무계정 인벤토리

작성 기준일: 2026-08-22

## 결론

현재 프로젝트에서 검증된 `Financial Fact`로 구조화된 계정은 다음 6개뿐이다.

| canonical ID | 표시명 | 최근연도 검증 기업 | 3개년 검증 기업 | 지원 수준 |
|---|---|---:|---:|---|
| `revenue` | 매출액 | 63/70 | 63/70 | `structured` |
| `operating_income` | 영업이익 | 67/70 | 67/70 | `structured` |
| `net_income` | 당기순이익 | 66/70 | 66/70 | `structured` |
| `total_assets` | 자산총계 | 67/70 | 67/70 | `structured` |
| `total_liabilities` | 부채총계 | 67/70 | 67/70 | `structured` |
| `total_equity` | 자본총계 | 67/70 | 67/70 | `structured` |

근거는 `data/derived/financial_fact_agent_audited_seed.jsonl`의 검증 행 1,191개와 `data/derived/financial_fact_coverage.json`의 1,191/1,260 grain coverage다. 어느 계정도 70/70을 충족하지 않아 corpus 전체 집계는 계속 fail-closed다. 이 문서의 나머지 계정은 이름을 인식하고 올바른 경로를 선택하기 위한 카탈로그 항목이지, 새 구조화 데이터가 아니다.

## 조사 범위와 데이터 한계

- 조사 대상: Query Planner, Agent, Evidence Service, Calculator, Financial Extraction/Validation/Overlay, SQLite/PostgreSQL 스키마, 설정 JSON, 테스트, Gold/holdout/free-form 평가 데이터, versioned derived 결과.
- 저장소에는 `.db`, `.sqlite`, `.sqlite3` 원본 DB가 없다. 따라서 원본 corpus 전체의 계정 수·빈도는 추정하지 않았다.
- versioned seed에서는 1,191개 검증 행, 6개 canonical ID, 66개 exact `account_name_raw` 변형을 직접 확인했다.
- XBRL concept을 보존하거나 판정하는 기존 추출 경로는 발견되지 않았다. 확인되지 않은 concept을 카탈로그에 만들지 않았으므로 모든 `xbrl_concepts`는 빈 배열이다.
- corpus에서 검증된 오타 목록도 발견되지 않았다. 모든 `typo_aliases`는 빈 배열이며 fuzzy matching은 금지된다.
- 향후 실제 DB 조사는 `scripts/inventory_financial_accounts.py`로 수행한다. 이 스크립트는 DB를 read-only/immutable URI로 열고 `financial_fact` 및 주요 재무제표 첫 열의 원문 후보를 집계한다.

예시:

```powershell
$env:PYTHONPATH='src'
python scripts/inventory_financial_accounts.py `
  --database D:\path\to\base.sqlite `
  --database D:\path\to\overlay.sqlite `
  --artifact data\derived\financial_fact_agent_audited_seed.jsonl `
  --output tmp\financial-account-inventory.json
```

## 계정 정의 위치와 중복

| 위치 | 기존 역할 | 조사 결과 / 현재 조치 |
|---|---|---|
| `src/disclosure_db/query_planner.py` | 질의에서 계정 판정 | 기존 6개 딕셔너리가 순서 기반 부분문자열 매칭을 사용했다. 중앙 판정기로 교체했다. |
| `src/disclosure_db/financial_extraction.py` | 본문 재무제표 행을 6개 canonical ID로 추출 | 별도 alias 딕셔너리를 제거하고 중앙 카탈로그의 `structured` 항목만 사용한다. |
| `src/disclosure_db/financial_validation.py` | 기대 계정과 coverage 산출 | 별도 `_ACCOUNTS` 튜플을 제거하고 중앙 카탈로그의 structured ID를 사용한다. |
| `config/financial_account_aliases.json` | Evidence 검색 확장 | 6개 계정의 또 다른 축약 alias 사본이었다. deprecated 포인터로 바꾸고 런타임은 새 카탈로그에서 확장을 생성한다. |
| `src/disclosure_db/evidence_service.py` | structured overlay 또는 일반 Evidence 조회 | 중앙 카탈로그를 로드해 `structured`와 `retrieval_only` 경로를 분리한다. |
| `src/disclosure_db/financial_overlay.py` | 검증 seed 적재·조회·coverage | `account_id`를 저장하지만 계정 allowlist는 없었다. 카탈로그 판정 이후에만 조회한다. |
| `src/disclosure_db/schema.py`, `sql/*.sql` | `financial_fact` 스키마 | `account_id`, raw label, statement/period/scope/value/evidence를 보존한다. 계정 의미 정의는 포함하지 않는다. |
| `src/disclosure_db/calculator.py` | Decimal 연산 allowlist | `lookup`, `difference`, `ratio`, `growth_rate`, `sum`만 등록돼 있다. 계정별 공식은 없었다. |
| `src/disclosure_db/web/api.js` | UI 표시명 | 6개 structured ID의 표시용 라벨이 남아 있다. 판정 권한은 없으며 서버 카탈로그와 별개인 UI 표현 계층이다. |
| `config/analysis_dimensions.json` | 분석 slot의 검색 개념 | 6개 structured 계정 외에 `차입금`을 financial slot에서 사용한다. 새 판정기는 `차입금`을 범위 확인이 필요한 `ambiguous`로 본다. |
| Gold/평가 데이터 | 질의와 expected fact | 현행/legacy canonical ID가 섞여 있다. 아래에 별도 기록한다. |

기존 오류의 직접 원인은 Query Planner가 등록 순서대로 `alias in text`를 실행한 것이다. `매출총이익`과 `매출이익` 안의 짧은 alias `매출`이 먼저 잡히면 `revenue`가 됐다. 같은 종류의 위험이 `유동자산`/`자산`, `유동부채`/`부채`, 현금흐름 세부 표현에도 있었다.

## 중앙 카탈로그 구조

권위 파일은 `config/financial_account_catalog.json`, 로더와 판정기는 `src/disclosure_db/financial_accounts.py`다. config에 두어 Docker의 기존 `DISCLOSURE_CONFIG_DIR=/app/config` 배포 경계를 유지했고, Python 모듈은 검증·정규화·판정 로직만 담당한다.

각 계정은 다음 필드를 가진다.

- 식별/표시: `canonical_id`, `label_ko`, `label_en`
- 재무 의미: `statement_type`, `period_type`, `expected_value_type`
- 표면형: `aliases`, `typo_aliases`, `xbrl_concepts`
- 지원/라우팅: `support_level`, `retrieval_route`
- 모호성/계산: `ambiguous_with`, `required_accounts`, `formula`
- 설명: `description`, `cautions`

현재 카탈로그는 42개 계정, 61개 alias를 포함한다. 지원 수준은 `structured` 6, `retrieval_only` 25, `derived` 8, `ambiguous` 2, `unsupported` 1이다. 등록된 typo alias와 XBRL concept은 각각 0개다.

## 계정별 지원 수준

### 손익계산서

| canonical ID | 표시명 | 지원 수준 | 조회 경로 | 비고 |
|---|---|---|---|---|
| `revenue` | 매출액 | `structured` | `financial_fact` | 영업수익·보험영업수익 등 검증 alias 포함 |
| `cost_of_sales` | 매출원가 | `retrieval_only` | `evidence_search` | 구조화 seed 없음 |
| `gross_profit` | 매출총이익 | `retrieval_only` | `evidence_search` | revenue로 매핑 금지 |
| `selling_general_admin_expenses` | 판매비와관리비 | `retrieval_only` | `evidence_search` | 판관비 alias 포함 |
| `operating_income` | 영업이익 | `structured` | `financial_fact` | legacy `operating_profit`의 현행 ID |
| `finance_income` | 금융수익 | `retrieval_only` | `evidence_search` | 구조화 seed 없음 |
| `finance_costs` | 금융비용 | `retrieval_only` | `evidence_search` | 구조화 seed 없음 |
| `profit_before_tax` | 법인세비용차감전순이익 | `retrieval_only` | `evidence_search` | 구조화 seed 없음 |
| `net_income` | 당기순이익 | `structured` | `financial_fact` | 전체 당기순손익 |
| `profit_attributable_to_owners` | 지배기업 소유주 귀속 순이익 | `retrieval_only` | `evidence_search` | net income과 자동 대체 금지 |

### 재무상태표·자본변동

| canonical ID | 표시명 | 지원 수준 | 조회 경로 | 비고 |
|---|---|---|---|---|
| `total_assets` | 자산총계 | `structured` | `financial_fact` | 유동/비유동자산과 구분 |
| `current_assets` | 유동자산 | `retrieval_only` | `evidence_search` | total assets로 매핑 금지 |
| `noncurrent_assets` | 비유동자산 | `retrieval_only` | `evidence_search` | total assets로 매핑 금지 |
| `cash_and_cash_equivalents` | 현금및현금성자산 | `retrieval_only` | `evidence_search` | 잔액 계정 |
| `trade_receivables` | 매출채권 | `retrieval_only` | `evidence_search` | 기타채권 포함 여부 확인 |
| `inventories` | 재고자산 | `retrieval_only` | `evidence_search` | 구조화 seed 없음 |
| `total_liabilities` | 부채총계 | `structured` | `financial_fact` | 유동/비유동부채와 구분 |
| `current_liabilities` | 유동부채 | `retrieval_only` | `evidence_search` | total liabilities로 매핑 금지 |
| `noncurrent_liabilities` | 비유동부채 | `retrieval_only` | `evidence_search` | total liabilities로 매핑 금지 |
| `borrowings` | 차입금 | `ambiguous` | `clarification` | 단기/장기 범위 확인 필요 |
| `short_term_borrowings` | 단기차입금 | `retrieval_only` | `evidence_search` | 구조화 seed 없음 |
| `long_term_borrowings` | 장기차입금 | `retrieval_only` | `evidence_search` | 구조화 seed 없음 |
| `total_equity` | 자본총계 | `structured` | `financial_fact` | 자본금과 구분 |
| `share_capital` | 자본금 | `retrieval_only` | `evidence_search` | 자본총계로 매핑 금지 |
| `retained_earnings` | 이익잉여금 | `retrieval_only` | `evidence_search` | 결손금 부호 확인 필요 |

### 현금흐름표

| canonical ID | 표시명 | 지원 수준 | 조회 경로 |
|---|---|---|---|
| `cash_flows_from_operating_activities` | 영업활동현금흐름 | `retrieval_only` | `evidence_search` |
| `cash_flows_from_investing_activities` | 투자활동현금흐름 | `retrieval_only` | `evidence_search` |
| `cash_flows_from_financing_activities` | 재무활동현금흐름 | `retrieval_only` | `evidence_search` |
| `net_change_in_cash_and_cash_equivalents` | 현금및현금성자산의 증감 | `retrieval_only` | `evidence_search` |
| `purchase_of_property_plant_and_equipment` | 유형자산의 취득 | `retrieval_only` | `evidence_search` |

### 주당·파생지표

| canonical ID | 표시명 | 지원 수준 | 조회 경로 | 필요한 계정 / 제한 |
|---|---|---|---|---|
| `earnings_per_share` | 주당이익(EPS) | `ambiguous` | `clarification` | 기본/희석 구분 필요 |
| `basic_earnings_per_share` | 기본주당이익 | `retrieval_only` | `evidence_search` | 구조화 seed 없음 |
| `diluted_earnings_per_share` | 희석주당이익 | `retrieval_only` | `evidence_search` | 구조화 seed 없음 |
| `book_value_per_share` | 주당순자산(BPS) | `unsupported` | `insufficient_evidence` | 검증 주식수·귀속자본 입력 없음 |
| `revenue_growth_rate` | 매출 증가율 | `derived` | `calculator` | `revenue`, 2개 기간; 기존 계산 가능 |
| `operating_income_growth_rate` | 영업이익 증가율 | `derived` | `calculator` | `operating_income`, 2개 기간; 기존 계산 가능 |
| `operating_margin` | 영업이익률 | `derived` | `calculator` | `operating_income`, `revenue`; 실행 미활성 |
| `net_margin` | 순이익률 | `derived` | `calculator` | `net_income`, `revenue`; 실행 미활성 |
| `debt_ratio` | 부채비율 | `derived` | `calculator` | `total_liabilities`, `total_equity`; 실행 미활성 |
| `current_ratio` | 유동비율 | `derived` | `calculator` | 원천 두 계정이 retrieval-only라 실행 미활성 |
| `return_on_assets` | 총자산이익률(ROA) | `derived` | `calculator` | 평균자산 정책 미확정 |
| `return_on_equity` | 자기자본이익률(ROE) | `derived` | `calculator` | 평균자본·귀속순이익 정책 미확정 |

## 모호한 표현과 unknown 정책

| 표현 | 결과 | clarification 후보 |
|---|---|---|
| `매출이익` | `ambiguous` | `gross_profit`, `operating_income` |
| `순이익` | `ambiguous` | `net_income`, `profit_attributable_to_owners` |
| `자산` | `ambiguous` | `total_assets`, `current_assets`, `noncurrent_assets` |
| `부채` | `ambiguous` | `total_liabilities`, `current_liabilities`, `noncurrent_liabilities` |
| `자본` | `ambiguous` | `total_equity`, `share_capital` |
| 미등록 표현 | `unknown` | 없음; `insufficient_evidence` |

판정기는 NFKC Unicode 정규화, 공백·기호 제거, XBRL exact, 공식 라벨, 가장 긴 alias, 등록 typo, 모호성 순서로 처리한다. 서로 겹치지 않는 여러 계정이 한 질문에서 발견돼도 하나를 임의 선택하지 않는다. 짧은 alias가 더 긴 미등록 단어 안에 들어 있는 경우도 한국어 조사·명시적 질의 suffix 경계가 아니면 매칭하지 않는다.

## versioned corpus에서 확인된 raw 계정명

`financial_fact_agent_audited_seed.jsonl`에서 확인한 exact raw label 66개를 계정별로 기록한다. 괄호 안은 행 수다.

- `net_income`: `당기순손익`(3), `당기순이익`(87), `당기순이익 (주34)`(3), `당기순이익 (주8,28,31,42)`(3), `당기순이익(손실)`(78), `연결당기순이익`(9), `연결당기순이익(손실)`(6), `Ⅵ.당기순이익`(3), `VII.당기순이익(손실)`(3), `XI. 당기순이익`(3).
- `operating_income`: `영업손익 (주38)`(3), `영업손익 (주6)`(3), `영업이익`(105), `영업이익 (주30)`(3), `영업이익 (주35)`(3), `영업이익 (주4)`(3), `영업이익 (주41)`(3), `영업이익 (주7)`(3), `영업이익 (주8)`(3), `영업이익(손실)`(57), `영업이익(손실) (주36)`(3), `영업이익(손실) (주6)`(3), `Ⅰ.영업이익`(3), `Ⅰ.영업이익(손실)`(3), `Ⅴ.영업이익`(3).
- `revenue`: `매출`(6), `매출 (주5,20,38,41)`(3), `매출액`(66), `매출액 (주23,36)`(3), `매출액 (주23)`(3), `매출액 (주24,34)`(6), `매출액 (주26,27,36,38)`(3), `매출액 (주26,31,32,40,44)`(3), `매출액 (주27,36)`(3), `매출액 (주28,29,37)`(3), `매출액 (주29,38,41)`(3), `매출액 (주3,20,24)`(3), `매출액 (주3)`(3), `매출액 (주30)`(3), `매출액 (주4,22,24,25,33)`(3), `매출액 (주4,24,31)`(3), `매출액 (주6,32)`(3), `매출액 (주6,7)`(3), `매출액 (주7,36)`(3), `매출액 (주7,37)`(3), `보험영업수익`(3), `수익(매출액)`(21), `수익(매출액) (주26,33,36,37)`(3), `영업수익`(12), `영업수익 (주22)`(3), `영업수익 (주26,35)`(3), `영업수익 (주29,36)`(3), `영업수익 (주35)`(3), `영업수익 (주4,30)`(3), `영업수익 (주4,36)`(3), `영업수익 (주8)`(3).
- `total_assets`: `자산 총계`(9), `자산 합계`(3), `자산총계`(186), `자산총계 (주8)`(3).
- `total_liabilities`: `부채 총계`(12), `부채 합계`(3), `부채총계`(183), `부채총계 (주8)`(3).
- `total_equity`: `자본 총계`(15), `자본총계`(186).

`revenue`의 주석부 raw 변형은 모두 seed에 보존돼 있으며 중앙 alias는 주석 번호를 계정 alias로 등록하지 않는다. 추출기가 기존과 동일하게 `주...` 주석과 로마 숫자 행 접두어를 제거한 뒤 exact catalog surface만 비교한다.

## Gold QA와 평가 데이터

- `data/derived/financial_fact_gold_seed.jsonl`: `revenue`, `net_income`, `total_assets` 및 legacy `operating_profit`을 각 2행 사용한다. `operating_profit`은 현행 overlay의 `operating_income`과 불일치하는 legacy ID이며 카탈로그의 `legacy_ids`에만 기록했다. 원본 평가 파일은 이 작업에서 재작성하지 않았다.
- `data/derived/gold_qa.jsonl`: 공급계약의 영업이익 기여(q003), 미래 매출액(q014), PDF 매출액 셀(q015, q016) 질문이 있다. 이들은 안전한 숫자 답을 강제하는 structured coverage 근거가 아니다.
- `config/analysis_dimensions.json`: structured 분석 slot은 매출액·영업이익·당기순이익·자산총계·부채총계·자본총계를 사용한다. financing slot의 `차입금`은 이제 clarification 대상이다.
- `config/freeform_gold_contract.json`, `config/freeform_question_templates.json`, `config/freeform_query_expansions.json`: 매출액·영업이익·당기순이익·부채총계 표현을 사용한다.
- `src/disclosure_db/web/api.js`: 위 6개 structured 계정의 표시 라벨을 사용한다.

## 향후 구조화 우선순위

1. `cost_of_sales`, `gross_profit`, `selling_general_admin_expenses`: 손익 구조와 영업이익률의 직접 원천이며 `매출총이익` 오분류 회귀를 실제 fact coverage로 확장할 수 있다.
2. `current_assets`, `current_liabilities`: 유동비율의 원천이며 total 계정과 분리 검증이 필요하다.
3. `cash_flows_from_operating_activities`: 핵심 현금창출 계정이며 잔액 `cash_and_cash_equivalents`와 분리해야 한다.
4. `profit_attributable_to_owners`: ROE·순이익률 정책에서 전체 순이익과 귀속 순이익을 구분하기 위해 필요하다.
5. 기본/희석 EPS와 검증 주식수: BPS/EPS를 안전하게 지원하기 위한 전제다.

각 우선 계정은 실제 DB inventory, raw label review, statement/period/scope 규칙, evidence-backed seed, coverage hard gate를 갖춘 뒤에만 `structured`로 승격한다.
