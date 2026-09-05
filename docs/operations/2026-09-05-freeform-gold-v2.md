# Free-form Gold V2 relevance-set 평가

## 목적

Legacy V1은 폭넓은 질문의 정답을 evidence ID 하나씩 모두 맞혀야 하는 exact-target 방식이었다. 같은
사실을 설명하는 여러 공시 문단 중 하나만 반환해도 충분한 경우까지 실패로 계산했고, 반대로 구조화
다중근거 슬롯은 필요한 계정·기간을 명시하지 못했다. V1 산출물은 회귀용으로 보존하고 V2에서 슬롯별
대체 근거 집합과 최소 적중 수를 명시했다.

## TDD와 원인 분석

- relevance set의 ID·domain·slot·최소 적중 수·evidence 비중복·전체 target 합집합을 검증하는 실패
  테스트를 먼저 추가했다.
- 재무 슬롯은 선언된 모든 canonical account의 최신 `min_periods`를 각각 하나의 relevance set으로
  만든다. broad text/event 슬롯은 읽기 전용 ledger에서 serving-admitted 대체 근거를 최대 100개까지
  보존한다.
- 최초 실데이터 V2는 Recall@20 `342/378 = 90.48%`였다. 잔여 실패를 직접 대조하자
  `사업 소득` 같은 짧은 표 행이 긴 위험 문단을 밀어내고 있었다. broad semantic 슬롯에 80자 이상과
  선언된 search concept을 요구하는 제품 검색 필터를 추가했다.
- 중간 평가는 Recall@20 `354/378 = 93.65%`였다. 별도 Gold 감사에서
  `고위험고수익투자신탁`의 부분 문자열 `위험`과 법정서식의 `경영권/경영진 변동`이 사업 위험·경영
  설명 정답으로 오인된 것을 확인했다. V2 marker를 강한 문구로 한정하고, 경영 설명 검색을
  `사업의 내용/사업경쟁력/주력 사업/경영 효율성` 공시 문맥으로 정렬했다.

## 최종 결과

- 실행 시각: `2026-09-05T09:29:51+09:00` / `2026-09-05T00:29:51Z`
- 사례/요구 적중: 120 cases / 378 required hits
- Recall@20: `378/378 = 1.0`
- Recall@5: `240/378 = 0.6349206349`
- 필수 슬롯 완전성: `1.0`
- wrong issuer / wrong version / hard failure: `0 / 0 / 0`
- 지연: p50 `61.9949ms`, p95 `103.0709ms`
- Gold canonical SHA-256: `2b020d6924c1c735732d6abf8913836fd13e09f8487a3896343f5affa1e78de5`
- summary semantic SHA-256: `561b5a45b0a9cffc3e6304010cb8d5642f8aba300903181348b8b3283bd302b3`
- base / overlay / search SHA-256:
  `b8fb3be8...6563` / `a4491f20...b55` / `e223a19f...8793`
- 관련 회귀: `127 passed`; 전체 Python: `954 passed, 2 skipped, 264 subtests passed`; Team QA:
  `1 passed`; `compileall`과 `git diff --check` 통과

`embedding_v2_decision.json`은 Sparse Recall gate가 이미 충족됐으므로
`DEFERRED_NO_EVIDENCE`다. 이는 Dense가 영구 불필요하다는 뜻이 아니라, 독립 hidden Gold에서 5%p
이상 개선을 입증하기 전 운영 의존성으로 채택하지 않는다는 뜻이다.

## 재현

```powershell
$env:PYTHONPATH=(Resolve-Path 'src').Path
python scripts/build_freeform_gold.py `
  --gold data/derived/gold_qa.agent_audited.jsonl `
  --contract config/freeform_gold_v2_contract.json `
  --templates config/freeform_question_templates.json `
  --database 'D:\mirae-asset-project\db\semantic-v1_129f5b0\disclosure_corpus_semantic_v1.sqlite' `
  --overlay 'D:\mirae-asset-project\db\agent\agent_overlay.sqlite' `
  --search-index 'D:\mirae-asset-project\db\agent\agent_search.sqlite' `
  --attestation data/derived/database_distribution_manifest_semantic_v1.json `
  --output data/derived/freeform_gold_v2.agent_audited.jsonl `
  --manifest data/derived/freeform_gold_v2_manifest.json

python scripts/evaluate_freeform_retrieval.py `
  --database 'D:\mirae-asset-project\db\semantic-v1_129f5b0\disclosure_corpus_semantic_v1.sqlite' `
  --overlay 'D:\mirae-asset-project\db\agent\agent_overlay.sqlite' `
  --search-index 'D:\mirae-asset-project\db\agent\agent_search.sqlite' `
  --attestation data/derived/database_distribution_manifest_semantic_v1.json `
  --gold data/derived/freeform_gold_v2.agent_audited.jsonl `
  --summary data/derived/freeform_retrieval_v2_summary.json `
  --embedding-decision data/derived/embedding_v2_decision.json
```

세 D 드라이브 입력은 `mode=ro&immutable=1`과 기존 attestation으로 열며 생성·평가 과정에서 수정하지
않는다. 이 120건 공개 재현 평가는 전체 출시 gate의 일부다. private holdout, provider, 보안, 동일 이미지
staging 결과를 대체하지 않는다.
