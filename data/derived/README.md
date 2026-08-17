# 제작 데이터

원천 데이터를 정제·파싱·통합하거나 프로젝트에서 직접 라벨링·생성한 재생성 가능 데이터를 저장합니다.

- `disclosure_corpus.sqlite`: 전체 구조·근거·정정 계보·품질 이슈를 담은 로컬 검증 DB
- `source_inventory.jsonl`: 파일별 SHA-256·실제 형식·인코딩·파서 coverage
- `gold_candidates.jsonl`: 층화 문서 후보 19개. Gold가 아님
- `gold_qa.jsonl`: 원문·DB 대조와 사람 검수를 마친 QA 23건. 모두 `human_verified/approved`
- `*_candidate_benchmark.json`: 파서 후보의 실제 코퍼스 비교 결과
- `database_validation.json`: 2026-08-14 구조 검증 스냅샷. 내부의 `hard_quality_gate_passed`는
  현재 명칭으로 `structure_gate_passed`에 해당하며 검색 관련성·의미 품질 통과를 뜻하지 않음.
  회사 질의 지연 수치도 안전 필터 개선 전 측정값이므로 최신 성능 비교에 사용하지 않음
- `query_benchmark_after_filter_fix.json`: 회사 metadata 선필터 적용 후 전체 DB 검색 재측정
- `retrieval_gold_baseline_structural.json`: 승인 Gold 중 evidence-addressable 8문항·9근거의
  회사/후보 filing 조건부 FTS+RRF Recall@20·MRR 기준선
- `database_migration_semantic_v1.json`: 원본 보존형 semantic v1 migration 전후 행 수·schema·계보·FTS 감사 기록
- `database_semantic_post_migration_adjustments.json`: legacy quality rule 별칭 2행 제거의 ID·SQL hash·결과
- `database_validation_semantic_v1.json`: migration 사본의 구조·semantic schema·Gold·검색 게이트 결과
- `retrieval_gold_baseline_semantic_v1.json`: semantic 사본에서 원본 기준선과 같은 조건으로 측정한 검색 결과

SQLite와 로그는 크기가 커 Git에서 제외합니다. 현재 로컬 구조 원장
`disclosure_corpus.sqlite`는 38,481,072,128바이트이며 GitHub에는 업로드하지 않습니다. 원본은
수정하지 않으며, 모든 파생 레코드는
`source_path`, `source_sha256`, `parser_version`, 결정론적 `evidence_id`로 원문에 돌아갈 수 있어야 합니다.

Gold 상태는 다음 명령으로 재검증합니다.

```powershell
$env:PYTHONPATH=(Resolve-Path 'src').Path
python scripts/validate_gold.py
```

정상 기준은 `records=23`, `issue_count=0`, `gold_release_gate_passed=true`입니다.

`disclosure_corpus_semantic_v1.sqlite`도 38GB급 로컬 산출물이므로 Git에서 제외합니다. 생성 명령과
감사 JSON만 공유하며, 원본 semantic DB의 `financial_fact`가 0행인 것은 누락이 아니라 재무제표
account/scope/period를 원본에 임의 적재하지 않도록 한 결과입니다. 사람이 원문 셀과 대조한 재무
표본은 별도 `financial_overlay.sqlite`에 8행으로 적재하며, seed 원본은
`financial_fact_gold_seed.jsonl`입니다.

최종 semantic 사본 기준 핵심 상태는 `structure_gate_passed=true`,
`semantic_schema_gate_passed=true`, FTS rowid orphan 0, evidence filing mismatch 0,
unresolved 539, missing-original 2입니다. 검색 관련성 통과는 evidence-addressable 8문항·9근거의
회사/후보 filing 조건부 범위에만 적용됩니다.
