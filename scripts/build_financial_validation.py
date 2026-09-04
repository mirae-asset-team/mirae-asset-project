from __future__ import annotations

import argparse
import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Mapping, Sequence

from disclosure_db.attestation import load_distribution_attestation, verify_fast_identity
from disclosure_db.financial_validation import canonical_json_bytes, validate_financial_candidates


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Validate financial candidates and report coverage")
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--attestation", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--candidates", type=Path, required=True)
    parser.add_argument("--extraction-rejects", type=Path, required=True)
    parser.add_argument("--review-decisions", type=Path, required=True)
    parser.add_argument("--seed", type=Path, required=True)
    parser.add_argument("--coverage", type=Path, required=True)
    parser.add_argument("--review-queue", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args(argv)
    inputs = {
        args.database.resolve(),
        args.attestation.resolve(),
        args.manifest.resolve(),
        args.candidates.resolve(),
        args.extraction_rejects.resolve(),
        args.review_decisions.resolve(),
    }
    outputs = {
        args.seed.resolve(),
        args.coverage.resolve(),
        args.review_queue.resolve(),
        args.report.resolve(),
    }
    if len(outputs) != 4 or inputs & outputs:
        parser.error("outputs must be distinct and must not overwrite inputs")
    return args


def _read_jsonl(path: Path) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        row = json.loads(line)
        if not isinstance(row, dict):
            raise ValueError(f"{path}: line {line_no} is not an object")
        rows.append(row)
    return rows


def _jsonl_bytes(rows: list[dict[str, object]]) -> bytes:
    return b"".join(
        (json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
        for row in rows
    )


def _write_atomically(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
            temporary = Path(handle.name)
        os.replace(temporary, path)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()


def _report(coverage: Mapping[str, object]) -> bytes:
    metrics = coverage["metrics"]
    companies = coverage["companies"]
    lines = [
        "# 검증 재무 데이터 Coverage",
        "",
        f"- 원본 DB SHA-256: `{coverage['source_database_sha256']}`",
        f"- 법인 기준 대상: {coverage['source_company_count']}개",
        f"- 안전한 최신 사업보고서 선택: {coverage['selected_filing_company_count']}개",
        f"- 검증 수치: {coverage['validated_grain_count']} / {coverage['expected_grain_count']}",
        f"- 미검증 수치: {coverage['missing_grain_count']}개",
        f"- 전체 기업 집계 hard gate: {'PASS' if coverage['aggregate_hard_gate_passed'] else 'FAIL-CLOSED'}",
        "",
        "자동 검증 값의 신뢰 등급은 `agent_audited`이며 실제 사람 승인을 뜻하지 않습니다.",
        "",
        "## 지표별 상태",
        "",
        "| 지표 | 최신연도 검증 기업 | 3개년 검증 기업 | 전체 집계 | 누락 기업 |",
        "|---|---:|---:|---|---|",
    ]
    for account_id, metric in metrics.items():  # type: ignore[union-attr]
        missing = ", ".join(metric["missing_companies"]) or "없음"
        lines.append(
            f"| `{account_id}` | {metric['latest_validated_company_count']}/{metric['expected_company_count']} "
            f"| {metric['three_period_validated_company_count']}/{metric['expected_company_count']} "
            f"| {'허용' if metric['aggregate_eligible'] else '거절'} | {missing} |"
        )
    lines.extend(
        [
            "",
            "## 기업별 미검증 현황",
            "",
            "| 기업 | 검증/예상 | 누락 | 선택 공시 |",
            "|---|---:|---:|---|",
        ]
    )
    for company in companies:  # type: ignore[union-attr]
        if not company["missing_count"]:
            continue
        lines.append(
            f"| {company['listed_name']} (`{company['issuer_corp_code']}`) "
            f"| {company['validated_count']}/{company['expected_count']} | {company['missing_count']} "
            f"| {company['selected_filing_id'] or '선택 불가'} |"
        )
    lines.extend(
        [
            "",
            "## 판정 원칙",
            "",
            "- 연결재무제표가 존재하면 별도재무제표 수치를 섞지 않습니다.",
            "- 같은 공시의 성공 파싱 셀과 재구성 결과가 정확히 일치한 값만 seed에 포함합니다.",
            "- 금융업 세부 수익을 임의 합산하지 않으며 직접 공시된 총계 별칭만 매출 계열로 사용합니다.",
            "- 누락·중복·단위 불명·lineage 불명 값은 review queue에 남기고 전체 기업 개수·순위 계산을 거절합니다.",
            "",
        ]
    )
    return "\n".join(lines).encode("utf-8")


def main(argv: Sequence[str] | None = None) -> None:
    args = parse_args(argv)
    attestation = load_distribution_attestation(args.attestation, database=args.database)
    if not verify_fast_identity(args.database, attestation):
        raise ValueError("database attestation mismatch")
    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    if not isinstance(manifest, dict):
        raise ValueError("manifest must be an object")
    if str(manifest.get("source_database_sha256") or "") != attestation.sha256:
        raise ValueError("manifest source does not match database attestation")
    decisions_payload = json.loads(args.review_decisions.read_text(encoding="utf-8"))
    if (
        not isinstance(decisions_payload, dict)
        or decisions_payload.get("schema_version") != "financial-review-decisions-v1"
        or not isinstance(decisions_payload.get("decisions"), list)
    ):
        raise ValueError("invalid review decisions contract")
    result = validate_financial_candidates(
        args.database,
        manifest,
        _read_jsonl(args.candidates),
        extraction_rejects=_read_jsonl(args.extraction_rejects),
        review_decisions=decisions_payload["decisions"],
    )
    seed_bytes = _jsonl_bytes(result["seed"])  # type: ignore[arg-type]
    coverage_bytes = canonical_json_bytes(result["coverage"])  # type: ignore[arg-type]
    review_bytes = _jsonl_bytes(result["review_queue"])  # type: ignore[arg-type]
    report_bytes = _report(result["coverage"])  # type: ignore[arg-type]
    _write_atomically(args.seed, seed_bytes)
    _write_atomically(args.coverage, coverage_bytes)
    _write_atomically(args.review_queue, review_bytes)
    _write_atomically(args.report, report_bytes)
    print(
        json.dumps(
            {
                "status": "ok",
                "validated_grain_count": result["coverage"]["validated_grain_count"],  # type: ignore[index]
                "missing_grain_count": result["coverage"]["missing_grain_count"],  # type: ignore[index]
                "aggregate_hard_gate_passed": result["coverage"]["aggregate_hard_gate_passed"],  # type: ignore[index]
                "seed_sha256": hashlib.sha256(seed_bytes).hexdigest(),
                "coverage_sha256": hashlib.sha256(coverage_bytes).hexdigest(),
                "review_queue_sha256": hashlib.sha256(review_bytes).hexdigest(),
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
