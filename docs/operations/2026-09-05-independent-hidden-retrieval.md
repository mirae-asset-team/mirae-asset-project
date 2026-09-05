# Independent Hidden Retrieval Evaluation — 2026-09-05

## 판정

독립 hidden Sparse 검색 gate는 **PASS**다. 이 판정은 검색 품질에만 해당하며 Judge Stress V2 전체, 실제 HCX provider, Docker identity 또는 운영 배포 PASS를 뜻하지 않는다.

## 입력 경계와 독립성

- 평가 입력은 `eval/judge_stress_v2/retrieval_hidden.jsonl`에 두며 Git에서 무시한다.
- 질문과 target은 제품 검색 결과를 사용하지 않고 read-only base/overlay의 direct evidence ledger를 대조해 작성했다.
- 표기는 `agent_audited`이며 실제 사람 검수로 가장하지 않는다.
- 추적 보고서에는 질문 원문·답변·provider body·credential을 기록하지 않는다.
- private Gold SHA-256: `52d4e76173f7541aaa281bf0435f4be73e79ed8539a65031f933625ee60e9525`
- base/overlay/search SHA-256:
  - `b8fb3be8b90d0cb1d8bc2491bee575aee632d29cc9bade21070e7e7b51646563`
  - `a4491f2072766fcc11db65bad8c592c78696aea87132f3e7420857924938cb55`
  - `e223a19fcbefd4757a39b71e2b73eed7c81d01f2b54d74ca82e761dac10a8793`

독립성 profile은 120개 고유 질문, 19개 기업, 98개 공시, 7개 판단 차원을 요구한다. `product_output_used=false`, `target_selection=direct_evidence_ledger`, `visibility=git_ignored_private_artifact`를 evaluator가 검증한다. private 경로가 Git에 추적되거나 질문 hash가 중복되거나 기업·공시·차원 다양성 기준을 못 채우면 `release_eligible=false`다.

## 결과

| 지표 | 결과 | 기준 |
|---|---:|---:|
| cases | 120 | 120 |
| 필수 target | 373 | - |
| Recall@20 | 357/373 = 0.9571045576 | >= 0.95 |
| slot completeness | 0.95 | 보고값 |
| wrong issuer | 0 | 0 |
| wrong version | 0 | 0 |
| hard failure | 0 | 0 |
| latency p50 | 338.23 ms | - |
| latency p95 | 1421.31 ms | <= 2000 ms |

보고서 semantic SHA-256은 `a7c12747416850100ba6b23793d8530f4a959cb9979ed037b68ecf854676daf0`이다. 16개 target miss는 content-free residual 목록으로 남겼으며 기업·공시 오염은 없었다. gate를 낮추거나 Gold를 제품 출력에 맞춰 다시 붙이지 않았다.

## 구현 판단

- 질문에 공시번호가 명시된 경우 text/event 검색을 그 공시로 제한한다.
- 숫자 금액을 공시번호로 오인하지 않도록 14자리 식별자는 `공시`, `공시번호`, `접수번호`, `filing id/number` 문맥에서만 해석한다.
- 정정 중요성 slot은 원 공시와 정정 공시를 slot별 version policy로 분리한다.
- 재무 slot은 text/event 공시번호를 무조건 물려받지 않아 다른 재무 공시를 잘못 배제하지 않는다.
- Dense/embedding은 Sparse Recall@20이 이미 95%를 넘었으므로 `DEFERRED_NO_EVIDENCE`다. 구조화 숫자는 계속 SQLite 경로를 사용한다.

## 재현 명령

private Gold가 있는 evaluator 환경에서만 실행한다.

```powershell
$env:PYTHONPATH = 'src'
python scripts/evaluate_freeform_retrieval.py `
  --database D:\mirae-asset-project\db\semantic-v1_129f5b0\disclosure_corpus_semantic_v1.sqlite `
  --overlay D:\mirae-asset-project\db\agent\agent_overlay.sqlite `
  --search-index D:\mirae-asset-project\db\agent\agent_search.sqlite `
  --attestation data\derived\database_distribution_manifest_semantic_v1.json `
  --gold eval\judge_stress_v2\retrieval_hidden.jsonl `
  --summary data\derived\freeform_retrieval_summary.json `
  --embedding-decision data\derived\embedding_decision.json `
  --evaluation-scope independent_hidden `
  --repository-root .
```

출시 다음 단계는 동일 candidate image의 8001 내부 검증, private 600건 실제 Judge/provider 평가, 통합 release gate PASS, 그리고 그 이미지 ID를 그대로 8000에 승격하는 것이다.
