from __future__ import annotations

import json
from pathlib import Path
import sqlite3

import pytest

from disclosure_db.freeform_evaluation import (
    aggregate_freeform_scores,
    assign_group_splits,
    build_freeform_gold,
    build_freeform_manifest,
    canonical_sha256,
    decide_embedding_pilot,
    derive_freeform_source_records,
    score_freeform_case,
    semantic_summary_sha256,
    validate_freeform_gold,
)


DIMENSIONS = (
    "profitability_financial_health",
    "contract_change",
    "financing_pressure",
    "correction_materiality",
    "governance_signal",
    "business_risk",
    "management_discussion",
)


def _contract(*, minimum_cases: int = 120) -> dict[str, object]:
    return {
        "schema_version": "1.0.0",
        "minimum_cases": minimum_cases,
        "dimensions": list(DIMENSIONS),
        "allowed_routes": ["text", "event", "financial", "mixed"],
        "allowed_correction_policies": ["current", "original", "both"],
        "allowed_parse_statuses": ["parsed", "ok"],
        "split_percentages": {"train": 0, "validation": 0, "test": 100},
    }


def _templates() -> dict[str, object]:
    return {
        "templates": [
            {
                "template_id": f"{dimension}-{variant}",
                "dimension_id": dimension,
                "route": "text" if dimension in {
                    "correction_materiality", "business_risk", "management_discussion"
                } else "event",
                "template": "{issuer_name} {dimension_label} 공시 근거를 확인해줘 ({variant_label})",
            }
            for dimension in DIMENSIONS
            for variant in range(6)
        ]
    }


def _record(number: int, *, evidence_id: str | None = None) -> dict[str, object]:
    filing_id = f"202401010000{number:02d}"
    return {
        "source_record_id": f"source-{number}",
        "issuer_name": f"회사{number}",
        "issuer_corp_code": f"{number:08d}",
        "filing_ids": [filing_id],
        "target_evidence_ids": [evidence_id or f"evidence-{number}"],
        "correction_policy": "current",
        "source_sha256": f"{number:064x}",
        "lineage_status": "root",
        "parse_status": "parsed",
        "supported_dimensions": list(DIMENSIONS),
    }


def test_freeform_gold_build_is_order_independent_and_never_human_verified() -> None:
    records = [_record(number) for number in range(1, 4)]

    first = build_freeform_gold(records, _contract(), _templates())
    second = build_freeform_gold(list(reversed(records)), _contract(), _templates())

    assert canonical_sha256(first) == canonical_sha256(second)
    assert len(first) == 126
    assert {row["dimension_id"] for row in first} == set(DIMENSIONS)
    assert {row["review"]["status"] for row in first} == {"agent_audited"}
    assert "human_verified" not in json.dumps(first, ensure_ascii=False)


@pytest.mark.parametrize(
    ("change", "reason"),
    [
        ({"lineage_status": "unresolved"}, "unresolved_lineage"),
        ({"target_evidence_ids": []}, "missing_evidence"),
        ({"issuer_corp_code": None}, "ambiguous_issuer"),
        ({"parse_status": "unsafe"}, "unsafe_source_parse"),
    ],
)
def test_freeform_gold_rejects_unsafe_or_unresolved_source_records(
    change: dict[str, object], reason: str,
) -> None:
    record = _record(1)
    record.update(change)

    with pytest.raises(ValueError, match=reason):
        build_freeform_gold([record], _contract(minimum_cases=1), _templates())


def test_group_splitting_keeps_shared_evidence_or_filing_in_one_split() -> None:
    cases = [
        {"case_id": "a", "filing_ids": ["filing-1"], "target_evidence_ids": ["evidence-1"]},
        {"case_id": "b", "filing_ids": ["filing-1"], "target_evidence_ids": ["evidence-2"]},
        {"case_id": "c", "filing_ids": ["filing-2"], "target_evidence_ids": ["evidence-2"]},
        {"case_id": "d", "filing_ids": ["filing-3"], "target_evidence_ids": ["evidence-3"]},
    ]

    split = assign_group_splits(cases, {"train": 50, "test": 50})
    by_id = {row["case_id"]: row for row in split}

    assert by_id["a"]["group_id"] == by_id["b"]["group_id"] == by_id["c"]["group_id"]
    assert by_id["a"]["split"] == by_id["b"]["split"] == by_id["c"]["split"]


def test_exact_target_ids_drive_recall_and_mrr() -> None:
    case = {
        "case_id": "case-exact",
        "route": "text",
        "issuer_corp_code": "00000001",
        "filing_ids": ["filing-1"],
        "target_evidence_ids": ["target-a", "target-b"],
        "correction_policy": "current",
    }
    selected = [
        {"evidence_id": "semantic-but-not-target", "issuer_corp_code": "00000001", "filing_id": "filing-1", "is_current": True},
        {"evidence_id": "target-a", "issuer_corp_code": "00000001", "filing_id": "filing-1", "is_current": True},
        *[
            {"evidence_id": f"distractor-{rank}", "issuer_corp_code": "00000001", "filing_id": "filing-1", "is_current": True}
            for rank in range(3, 20)
        ],
        {"evidence_id": "target-b", "issuer_corp_code": "00000001", "filing_id": "filing-1", "is_current": True},
    ]

    score = score_freeform_case(case, selected, slot_complete=True, query_count=3, candidate_count=24, latency_ms=12)

    assert score["target_hits_at_5"] == 1
    assert score["target_hits_at_20"] == 2
    assert score["recall_at_5"] == 0.5
    assert score["recall_at_20"] == 1.0
    assert score["mrr"] == 0.5
    assert score["selected_evidence_ids"][:2] == ["semantic-but-not-target", "target-a"]


@pytest.mark.parametrize(
    "bad_hit",
    [
        {"evidence_id": "wrong-issuer", "issuer_corp_code": "99999999", "filing_id": "filing-1", "is_current": True},
        {"evidence_id": "stale-version", "issuer_corp_code": "00000001", "filing_id": "filing-1", "is_current": False},
    ],
)
def test_wrong_issuer_or_version_is_a_hard_failure(bad_hit: dict[str, object]) -> None:
    case = {
        "case_id": "case-safe",
        "route": "text",
        "issuer_corp_code": "00000001",
        "filing_ids": ["filing-1"],
        "target_evidence_ids": ["target"],
        "correction_policy": "current",
    }

    score = score_freeform_case(case, [bad_hit])

    assert score["hard_failure"] is True
    assert score["wrong_issuer_count"] + score["wrong_version_count"] == 1


def test_other_current_filing_is_not_mislabeled_as_a_wrong_version() -> None:
    case = {
        "case_id": "case-multifiling",
        "route": "text",
        "issuer_corp_code": "00000001",
        "filing_ids": ["target-filing"],
        "target_evidence_ids": ["target"],
        "correction_policy": "current",
    }
    selected = [{
        "evidence_id": "other-current-evidence",
        "issuer_corp_code": "00000001",
        "filing_id": "other-current-filing",
        "is_current": True,
    }]

    score = score_freeform_case(case, selected)

    assert score["wrong_version_count"] == 0
    assert score["hard_failure"] is False


def test_aggregate_scores_weight_target_recall_and_report_latency_percentiles() -> None:
    scores = [
        {
            "case_id": "a", "route": "text", "target_count": 2, "target_hits_at_5": 1,
            "target_hits_at_20": 2, "mrr": 0.5, "slot_complete": True,
            "wrong_issuer_count": 0, "wrong_version_count": 0, "hard_failure": False,
            "query_count": 2, "candidate_count": 6, "latency_ms": 10,
            "missing_target_evidence_ids": [], "selected_evidence_ids": ["one", "two"],
        },
        {
            "case_id": "b", "route": "text", "target_count": 1, "target_hits_at_5": 0,
            "target_hits_at_20": 0, "mrr": 0.0, "slot_complete": False,
            "wrong_issuer_count": 0, "wrong_version_count": 0, "hard_failure": False,
            "query_count": 3, "candidate_count": 8, "latency_ms": 30,
            "missing_target_evidence_ids": ["missing"], "selected_evidence_ids": ["other"],
        },
    ]

    summary = aggregate_freeform_scores(scores)

    assert summary["case_count"] == 2
    assert summary["target_count"] == 3
    assert summary["target_recall_at_5"] == pytest.approx(1 / 3)
    assert summary["target_recall_at_20"] == pytest.approx(2 / 3)
    assert summary["mrr"] == 0.25
    assert summary["slot_completeness"] == 0.5
    assert summary["latency_ms"]["p50"] == 20
    assert summary["latency_ms"]["p95"] == 29
    assert summary["residual_misses"] == [{
        "case_id": "b",
        "route": "text",
        "target_evidence_ids": ["missing"],
        "selected_evidence_ids": ["other"],
        "exclusion_boundary": "not_retrieved_at_20",
    }]


def test_residual_diagnostics_preserve_content_free_root_cause_hypothesis() -> None:
    summary = aggregate_freeform_scores([{
        "case_id": "safe-id", "route": "text", "target_count": 1,
        "target_hits_at_5": 0, "target_hits_at_20": 0, "mrr": 0,
        "slot_complete": False, "wrong_issuer_count": 0, "wrong_version_count": 0,
        "hard_failure": False, "query_count": 1, "candidate_count": 2, "latency_ms": 3,
        "missing_target_evidence_ids": ["target-id"], "selected_evidence_ids": ["selected-id"],
        "exclusion_boundary": "expanded_sparse_not_retrieved_at_20",
        "hypothesis": "target_absent_from_search_index",
    }])

    assert summary["residual_misses"][0]["hypothesis"] == "target_absent_from_search_index"


def test_manifest_contains_hashes_and_counts_but_no_content_or_credentials() -> None:
    rows = build_freeform_gold([_record(1)], _contract(minimum_cases=1), _templates())
    manifest = build_freeform_manifest(
        rows,
        source_gold_sha256="a" * 64,
        contract_sha256="b" * 64,
        templates_sha256="c" * 64,
        database_sha256="d" * 64,
    )
    rendered = json.dumps(manifest, ensure_ascii=False).lower()

    assert manifest["case_count"] == 42
    assert manifest["content_sha256"] == canonical_sha256(rows)
    for forbidden in ("question", "answer", "excerpt", "credential", "secret", "api_key"):
        assert forbidden not in rendered


def test_validation_rejects_human_verified_and_duplicate_case_ids() -> None:
    rows = build_freeform_gold([_record(1)], _contract(minimum_cases=1), _templates())
    rows[0]["review"]["status"] = "human_verified"
    rows[1]["case_id"] = rows[0]["case_id"]

    with pytest.raises(ValueError, match="human_verified"):
        validate_freeform_gold(rows, _contract(minimum_cases=1))


def test_embedding_is_eligible_only_for_residual_text_misses_and_five_point_gain() -> None:
    summary_with_residual_text_misses = {
        "target_count": 20,
        "target_recall_at_20": 0.90,
        "residual_misses": [{
            "case_id": "text-miss", "route": "text",
            "target_evidence_ids": ["target"], "selected_evidence_ids": [],
            "exclusion_boundary": "not_retrieved_at_20",
        }],
        "wrong_issuer_count": 0,
        "wrong_version_count": 0,
    }
    summary_with_only_structured_misses = {
        **summary_with_residual_text_misses,
        "residual_misses": [{
            "case_id": "financial-miss", "route": "financial",
            "target_evidence_ids": ["target"], "selected_evidence_ids": [],
            "exclusion_boundary": "structured_route",
        }],
    }

    decision = decide_embedding_pilot(summary_with_residual_text_misses)
    rejected = decide_embedding_pilot(summary_with_only_structured_misses)

    assert decision["eligible"] is True
    assert decision["status"] == "ELIGIBLE_PILOT"
    assert decision["maximum_possible_gain"] == 0.05
    assert rejected["eligible"] is False
    assert rejected["status"] == "DEFERRED_NO_EVIDENCE"
    assert rejected["reason"] == "no_residual_text_misses"


def test_embedding_is_deferred_when_sparse_recall_passes_or_possible_gain_is_too_small() -> None:
    passed = decide_embedding_pilot({
        "target_count": 100, "target_recall_at_20": 0.95,
        "residual_misses": [], "wrong_issuer_count": 0, "wrong_version_count": 0,
    })
    too_small = decide_embedding_pilot({
        "target_count": 100, "target_recall_at_20": 0.94,
        "residual_misses": [{
            "case_id": "one", "route": "text", "target_evidence_ids": ["target"],
            "selected_evidence_ids": [], "exclusion_boundary": "not_retrieved_at_20",
        }],
        "wrong_issuer_count": 0, "wrong_version_count": 0,
    })

    assert passed["status"] == "DEFERRED_NO_EVIDENCE"
    assert passed["reason"] == "sparse_recall_gate_met"
    assert too_small["eligible"] is False
    assert too_small["reason"] == "insufficient_maximum_possible_gain"


def test_source_record_derivation_uses_only_safe_current_corpus_evidence() -> None:
    connection = sqlite3.connect(":memory:")
    connection.executescript(
        """
        CREATE TABLE filing (
            filing_id TEXT PRIMARY KEY, issuer_corp_code TEXT, issuer_name TEXT, filed_at TEXT
        );
        CREATE TABLE filing_version (
            filing_id TEXT PRIMARY KEY, lineage_status TEXT, is_current INTEGER
        );
        CREATE TABLE source_document (
            source_id TEXT PRIMARY KEY, filing_id TEXT, sha256 TEXT, parse_status TEXT
        );
        CREATE TABLE fragment (
            evidence_id TEXT PRIMARY KEY, filing_id TEXT, source_id TEXT,
            sequence_no INTEGER, text_normalized TEXT
        );
        """
    )
    connection.executemany(
        "INSERT INTO filing VALUES (?, ?, ?, ?)",
        [
            ("filing-safe", "00000001", "회사1", "2024-01-02"),
            ("filing-unsafe", "00000001", "회사1", "2024-01-03"),
        ],
    )
    connection.executemany(
        "INSERT INTO filing_version VALUES (?, ?, ?)",
        [("filing-safe", "root", 1), ("filing-unsafe", "unresolved", 1)],
    )
    connection.executemany(
        "INSERT INTO source_document VALUES (?, ?, ?, ?)",
        [
            ("source-safe", "filing-safe", "a" * 64, "success"),
            ("source-unsafe", "filing-unsafe", "b" * 64, "partial"),
        ],
    )
    connection.executemany(
        "INSERT INTO fragment VALUES (?, ?, ?, ?, ?)",
        [
            ("evidence-safe", "filing-safe", "source-safe", 1, "사업 위험요인"),
            ("evidence-unsafe", "filing-unsafe", "source-unsafe", 1, "사업 위험요인"),
        ],
    )
    connection.commit()
    audited_gold = [{
        "question_id": "seed-1",
        "company_resolution": {"issuer_name": "회사1", "corp_code": "00000001"},
        "review": {"status": "agent_audited"},
        "audit": {"state": "agent_audited"},
    }]
    contract = {
        "dimensions": ["business_risk"],
        "allowed_parse_statuses": ["success"],
        "source_records_per_dimension": 1,
        "dimension_routes": {"business_risk": "text"},
        "candidate_terms": {"business_risk": ["위험요인"]},
    }

    try:
        records = derive_freeform_source_records(
            audited_gold, Path("unused.sqlite"), contract, connection=connection,
        )

        assert records == [{
            "source_record_id": "business_risk:evidence-safe",
            "issuer_name": "회사1",
            "issuer_corp_code": "00000001",
            "filing_ids": ["filing-safe"],
            "target_evidence_ids": ["evidence-safe"],
            "correction_policy": "current",
            "source_sha256": "a" * 64,
            "lineage_status": "root",
            "parse_status": "success",
            "supported_dimensions": ["business_risk"],
            "routes": {"business_risk": "text"},
            "as_of": "2024-01-02",
        }]
    finally:
        connection.close()


def test_semantic_summary_hash_ignores_only_measurement_fields() -> None:
    first = {
        "generated_at": "first",
        "metrics": {"target_recall_at_20": 0.9, "latency_ms": {"p95": 10}},
        "case_scores": [{"case_id": "a", "selected_evidence_ids": ["one"], "latency_ms": 3}],
    }
    second = {
        "generated_at": "second",
        "metrics": {"target_recall_at_20": 0.9, "latency_ms": {"p95": 999}},
        "case_scores": [{"case_id": "a", "selected_evidence_ids": ["one"], "latency_ms": 44}],
    }

    assert semantic_summary_sha256(first) == semantic_summary_sha256(second)
    second["case_scores"][0]["selected_evidence_ids"] = ["different"]
    assert semantic_summary_sha256(first) != semantic_summary_sha256(second)
