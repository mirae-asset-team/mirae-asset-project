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
