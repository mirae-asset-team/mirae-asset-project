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

## 개발 평가 결과와 독립성 한계

- 실행 시각: `2026-09-05T09:56:21+09:00` / `2026-09-05T00:56:21Z`
- 사례/요구 적중: 120 cases / 402 required hits
- Recall@20: `402/402 = 1.0`
- Recall@5: `246/402 = 0.6119402985`
- 필수 슬롯 완전성: `1.0`
- wrong issuer / wrong version / hard failure: `0 / 0 / 0`
- 지연: p50 `62.0983ms`, p95 `97.3764ms`
- Gold canonical SHA-256: `26687e76ca771eb65763e548e92d52b3cb5f734f8a8cae53d9fcaea7fef7c71a`
- summary semantic SHA-256: `468a647a23f3718a900f178c96bddc139235ca33008f1dbc8caef0ff34c7d60f`
- base / overlay / search SHA-256:
  `b8fb3be8...6563` / `a4491f20...b55` / `e223a19f...8793`
- 관련 회귀: `212 passed, 94 subtests passed`; 전체 Python: `964 passed, 2 skipped, 264 subtests passed`;
  Web `13 passed`; Team QA `1 passed`; `compileall`과 `git diff --check` 통과

이 V2는 production filter와 같은 marker 규칙으로 자동 생성됐고 19개 source record·7개 issuer만
포함한다. 따라서 manifest와 report에 `evaluation_scope=development_public_agent_audited`,
`release_eligible=false`를 기록한다. `embedding_v2_decision.json`은
`BLOCKED_INDEPENDENT_GOLD`이며, 독립 hidden Gold가 생기기 전에는 Sparse 통과나 embedding 불필요를
주장하지 않는다.

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
않는다. 이 120건은 개발 회귀일 뿐 출시 승인 입력이 아니다. release gate는 별도
`independent_hidden` V2 report만 허용하며 private holdout, provider, 보안, 동일 이미지 staging 결과도
각각 필요하다.
