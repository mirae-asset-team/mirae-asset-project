# Gold QA 품질 보고서 상태

`report.html`과 `artifact.json`은 **2026-08-14 자동 보정 전 상태를 기록한 감사 스냅샷**임.
현재 Gold 후보 상태를 판단하는 정본이 아님.

현재 상태는 다음 파일을 사용함.

- `data/derived/gold_qa.jsonl`: 자동 보정된 QA 후보 23개
- `data/derived/gold_qa_validation.json`: 현재 계약·실DB 대조 결과
- `scripts/validate_gold.py`: 재검증 진입점

현재 판정은 `candidate_review_ready=true`, `gold_release_gate_passed=false`임. 전자는 기계적
계약·근거 대조를 통과했다는 뜻이고, 후자는 독립된 사람 원문 승인이 아직 0건이라는 뜻임.
