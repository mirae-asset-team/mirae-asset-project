# 공시 Agent 평가 API 서버 명세

작성 기준일: 2026-08-20

## 배포 상태

- 로컬·Docker 구현 commit: `498f90b` 이후
- 기존 NCP 공개 서버: API-only 구버전, health와 기존 `/query` 정상
- 제출용 endpoint URL: 새 UI·`GET /answer` 코드 전용 재배포 후 확정
- 현재 공개 제출 상태: `BLOCKED_EXTERNAL` (인증된 NCP 세션 필요)

실제 URL이 검증되기 전에는 기존 공인 IP나 임시 주소를 제출 endpoint로 기록하지 않는다.

## 1. 공식 예시 호환 API

### Request

`GET /answer`

| Query parameter | Type | Required | Constraint |
|---|---|---:|---|
| `question_id` | string | yes | 1~200자 |
| `question` | string | yes | 1~4,000자 |
| `company` | string | no | 200자 이하, 명시적 기업 힌트 |
| `as_of` | string | no | `YYYY-MM-DD` |

```bash
curl -G "https://<team-endpoint>/answer" \
  --data-urlencode "question_id=Q-001" \
  --data-urlencode "question=평가 질의"
```

```python
import requests

response = requests.get(
    "https://<team-endpoint>/answer",
    params={"question_id": "Q-001", "question": "평가 질의"},
    timeout=30,
)
response.raise_for_status()
result = response.json()
```

### Response 200

반환 필드는 정확히 다섯 개다.

```json
{
  "question_id": "Q-001",
  "question": "평가 질의",
  "retrieved_context": "공시명=사업보고서 | 공시일=2025-03-18 | 접수번호=20250318000001",
  "think_trace": "질의 구조화 -> 공시 검색 -> 정정·수치 검증 -> 근거 귀속",
  "answer": "검증된 최종 답변"
}
```

| Field | Type | Meaning |
|---|---|---|
| `question_id` | string | 요청 ID echo |
| `question` | string | 평가 질의 원문 echo |
| `retrieved_context` | string | 최대 20개, 총 6,000자 이하의 검증된 공시 메타데이터 |
| `think_trace` | string | 공개 처리 단계 요약; 비공개 chain-of-thought가 아님 |
| `answer` | string | 검증된 최종 답변 또는 명시적 답변 보류 |

답변할 수 없으면 `retrieved_context`는 빈 문자열일 수 있고, `think_trace`는 `정보한계 판정`으로 끝난다.

## 2. 상세 평가·웹 API

### Request

`POST /query`

```json
{
  "question_id": "Q-001",
  "question": "평가 질의",
  "company": null,
  "as_of": null,
  "limit": 20
}
```

| Field | Type | Required | Constraint |
|---|---|---:|---|
| `question_id` | string or null | no | 200자 이하 |
| `question` | string | yes | 1~4,000자 |
| `company` | string or null | no | 200자 이하 |
| `as_of` | string or null | no | `YYYY-MM-DD` |
| `limit` | integer | no | 1~100, default 20 |

### Response 200 핵심 필드

```json
{
  "answer": "최종 답변",
  "verified": true,
  "answerable": true,
  "reason_codes": [],
  "numeric_values": ["100"],
  "question_id": "Q-001",
  "evidence": [
    {
      "evidence_id": "evidence-id",
      "filing_id": "receipt-number",
      "receipt_no": "receipt-number",
      "report_name": "사업보고서",
      "filed_at": "2025-03-18",
      "locator": {}
    }
  ],
  "request_id": "server-request-id",
  "corpus_revision": "semantic-v1",
  "latency_ms": 1200.0
}
```

검증되지 않은 수치나 citation은 반환하지 않는다. `answerable=false`이면 숫자와 evidence가 비어 있고 답변에 정보한계를 명시한다.

## 3. Health

`GET /health`

HTTP 200 응답의 주요 boolean은 `ready`, `base_attested`, `overlay_attested`, `search_index_ready`, `provider_configured`다. `ready=true`는 런타임 준비만 의미하며 provider 품질이나 최종 제출 GO를 뜻하지 않는다.

## 4. Error contract

| Status | Detail | Meaning |
|---:|---|---|
| 422 | FastAPI validation detail | 누락·빈 질문·길이·날짜 형식 오류 |
| 429 | `rate_limited` or `ip_busy` | IP별 이동구간 또는 동시 요청 제한 |
| 503 | `server_busy` | 서버 전체 동시 처리 제한 |
| 503 | `runtime_not_ready` | DB attestation 또는 검색 readiness 실패 |
| 500 | generic server error | 예상하지 못한 내부 오류; 내부 경로·credential 비노출 |

429와 503 제한 응답에는 `Retry-After`가 포함된다.

## 5. 익명 요청 제한

| Limit | Default |
|---|---:|
| IP별 60초 요청 | 120 |
| IP별 동시 요청 | 4 |
| 서버 전체 동시 요청 | 8 |

`GET /answer`, `POST /query`, `POST /v1/answer`에 적용한다. 현재는 직접 소켓 IP만 사용하고 `X-Forwarded-For`를 신뢰하지 않는다.

## 6. 공개 배포 승인 조건

1. 원격 base/overlay/index/attestation hash와 `0444` 재확인
2. Compose read-only mount 확인
3. exact source commit 코드 전용 배포
4. 외부 `GET /`, `/health`, `/answer`, `/query` 검증
5. 429/503 제한과 production 기본값 복원 확인
6. 컨테이너 restart 및 host reboot 복구 확인
7. `provider_configured=true`와 provider-required 300 gate 통과

1~6은 인증된 NCP 세션 부재로 아직 실행하지 않았다. 7은 교체된 HyperCLOVA X credential 부재로 아직 실행하지 않았다.
