# 질문 분류와 빠른 검색 v1

사용자의 모든 질문을 HyperCLOVA X에 먼저 보내지 않는다. 코드로 확실히 구분할 수 있는 질문은
곧바로 알맞은 도구를 실행하고, 해석이나 요약이 필요한 경우에만 마지막 답변 생성에 모델을 사용한다.

| 질문 예시 | 실행 경로 | 모델 호출 |
|---|---|---:|
| 삼성전자 2025년 매출액은? | 검증된 `financial_fact` SQLite 조회 | 0회 |
| 삼성전자 2026년 공시트랜드는? | 기간별·유형별 공시 집계 후 코드가 설명 | 0회 |
| 삼성전자 2025년 매출총이익은? | Sparse 키워드 + BGE-M3 Dense 검색 후 답변 | 1회 |
| 이 공시를 요약해줘 | 근거 청크 검색 후 요약 | 1회 |
| 14자리 접수번호의 정정 내용을 알려줘 | 정정 계보 조회 후 설명 | 1회 |
| 매출이익은? | 매출총이익/영업이익 확인 질문 | 0회 |
| 의도가 불분명한 자유 질문 | HCX Tool 선택 후 근거 기반 답변 | 최대 2회 |

모델 없이 바로 답할 수 있는 구조화 계정은 현재 `매출액`, `영업이익`, `당기순이익`,
`자산총계`, `부채총계`, `자본총계`다. `매출총이익`은 아직 검증된 구조화 seed가 없으므로
숫자를 임의 계산하지 않고 공시 원문 검색 경로를 사용한다.

검색 질문은 기존 SafeSearchIndex의 키워드 순위와 NCP Dense sidecar의 BGE-M3 순위를 RRF로
합친다. Dense 결과의 `evidence_id`는 원본 SQLite에서 다시 읽어 본문, 접수번호, 공시일,
정정 상태를 확인한 뒤에만 답변 근거로 사용한다. Dense 서버 오류 또는 5초 timeout이면 기존
Sparse 결과로 안전하게 돌아간다.

명확한 한 글자 오타만 자동 교정한다. 예를 들어 `삼선전자`의 후보가 `삼성전자` 하나뿐이면
교정하지만 비슷한 회사가 여러 개면 자동 선택하지 않는다. `매출총익`은 `매출총이익`으로
교정하고, 원래 모호한 표현인 `매출이익`은 교정하지 않고 사용자에게 범위를 확인한다.

응답 `metadata`에는 `route_source`, `route_reason`, `tool_selection_called`,
`final_generation_called`, `question_corrections`를 남긴다. 이를 이용하면 질문별로 모델을 몇 번
호출했는지와 오타가 어떻게 처리됐는지 확인할 수 있다.

운영 HCX 모델은 현재 서비스 키의 OpenAI 호환 API에서 실제 동작을 확인한 `HCX-005`를 쓴다.
이미 코드가 도구를 확정한 요약 질문은 Function Calling을 다시 하지 않고, 검색한 근거만 넣은
일반 답변 생성을 한 번 호출한다. 어떤 도구를 써야 할지 불분명한 질문에만 Function Calling을
사용한다.

NCP 전체 Dense 설정은 다음 환경값을 사용한다.

```text
DISCLOSURE_DENSE_URL=http://dense-retriever:8080
DISCLOSURE_DENSE_TIMEOUT_SECONDS=5
DISCLOSURE_DENSE_VECTOR_COUNT=2571506
```

현재 full-corpus 산출물은 BAAI/bge-m3, 1,024차원, 2,571,506개 벡터다. 산출물을 교체할 때는
manifest 확인 후 vector count도 함께 변경해야 한다.
