from __future__ import annotations

import inspect
import json
from pathlib import Path
import sqlite3

import pytest

from disclosure_db.analysis_planner import plan_analysis
from disclosure_db.freeform_evaluation import (
    aggregate_freeform_scores,
    assign_group_splits,
    build_freeform_gold,
    build_freeform_manifest,
    canonical_sha256,
    decide_embedding_pilot,
    derive_freeform_source_records,
    evaluation_exclusion_reason,
    score_freeform_case,
    select_period_covered_financial_candidates,
    semantic_summary_sha256,
    text_candidate_version_is_admitted,
    text_target_is_answer_safe,
    validate_freeform_ledger_identities,
    validate_case_plan,
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


def _audited_business_record(number: int) -> dict[str, object]:
    record = _record(number)
    record.update({
        "supported_dimensions": ["business_risk"],
        "slot_targets": [{
            "slot_id": "disclosed_risk_factors", "domain": "text",
            "serving_path": "search_document",
            "target_evidence_ids": list(record["target_evidence_ids"]),
            "filing_ids": list(record["filing_ids"]),
            "content_features": {"fragment_type": "paragraph", "character_count": 120, "marker_count": 1},
        }],
        "answerability": {"status": "proved", "requirement_count": 1},
    })
    return record


def _business_contract(*, minimum_cases: int = 1) -> dict[str, object]:
    contract = _contract(minimum_cases=minimum_cases)
    contract["dimensions"] = ["business_risk"]
    return contract


def _business_templates() -> dict[str, object]:
    return {"templates": [
        {
            "template_id": "risk-a", "dimension_id": "business_risk", "route": "text",
            "template": "{issuer_name} 공시에 나온 사업 위험요인과 리스크를 종합 분석해줘",
        },
        {
            "template_id": "risk-b", "dimension_id": "business_risk", "route": "text",
            "template": "{issuer_name} 사업 위험요인과 리스크를 공시 근거로 판단해줘",
        },
    ]}


def test_freeform_gold_build_is_order_independent_and_never_human_verified() -> None:
    records = [_audited_business_record(number) for number in range(1, 4)]

    first = build_freeform_gold(records, _business_contract(minimum_cases=6), _business_templates())
    second = build_freeform_gold(list(reversed(records)), _business_contract(minimum_cases=6), _business_templates())

    assert canonical_sha256(first) == canonical_sha256(second)
    assert len(first) == 6
    assert {row["dimension_id"] for row in first} == {"business_risk"}
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
    rows = build_freeform_gold([_audited_business_record(1)], _business_contract(), _business_templates())
    manifest = build_freeform_manifest(
        rows,
        source_gold_sha256="a" * 64,
        contract_sha256="b" * 64,
        templates_sha256="c" * 64,
        database_sha256="d" * 64,
        overlay_sha256="e" * 64,
        search_index_sha256="f" * 64,
    )
    rendered = json.dumps(manifest, ensure_ascii=False).lower()

    assert manifest["case_count"] == 2
    assert manifest["source_record_count"] == 1
    assert manifest["unique_target_count"] == 1
    assert manifest["target_selection"] == "independent_ledger_enumeration"
    assert manifest["overlay_sha256"] == "e" * 64
    assert manifest["search_index_sha256"] == "f" * 64
    assert manifest["content_sha256"] == canonical_sha256(rows)
    for forbidden in ("question", "answer", "excerpt", "credential", "secret", "api_key"):
        assert forbidden not in rendered


def test_source_record_derivation_does_not_select_targets_from_serving_top_twenty() -> None:
    source = inspect.getsource(derive_freeform_source_records)

    assert ".search_analysis(" not in source


@pytest.mark.parametrize(
    ("slot_id", "is_current", "lineage_status", "expected"),
    [
        ("original_disclosure", False, "root", True),
        ("original_disclosure", False, "resolved", False),
        ("original_disclosure", True, "root", False),
        ("effective_correction", True, "resolved", True),
        ("effective_correction", True, "root", True),
        ("effective_correction", False, "resolved", False),
        ("disclosed_risk_factors", True, "root", True),
        ("disclosed_risk_factors", False, "root", False),
    ],
)
def test_text_candidate_version_admission_is_root_strict_for_original(
    slot_id: str, is_current: bool, lineage_status: str, expected: bool,
) -> None:
    assert text_candidate_version_is_admitted(
        slot_id, is_current=is_current, lineage_status=lineage_status,
    ) is expected


def test_freeform_ledger_identity_validation_fails_closed_on_overlay_mismatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "disclosure_db.freeform_evaluation.overlay_matches_base",
        lambda *_args, **_kwargs: False,
    )

    with pytest.raises(ValueError, match="overlay_base_attestation_mismatch"):
        validate_freeform_ledger_identities(
            Path("base.sqlite"), Path("overlay.sqlite"), Path("search.sqlite"),
            type("Attestation", (), {"sha256": "a" * 64, "size_bytes": 123})(),
        )


def test_freeform_ledger_identity_validation_fails_closed_on_search_mismatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "disclosure_db.freeform_evaluation.overlay_matches_base",
        lambda *_args, **_kwargs: True,
    )

    class RejectingSearchIndex:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            raise ValueError("search index base attestation mismatch")

    monkeypatch.setattr(
        "disclosure_db.freeform_evaluation.SafeSearchIndex", RejectingSearchIndex,
    )

    with pytest.raises(ValueError, match="search_index_attestation_mismatch"):
        validate_freeform_ledger_identities(
            Path("base.sqlite"), Path("overlay.sqlite"), Path("search.sqlite"),
            type("Attestation", (), {"sha256": "a" * 64, "size_bytes": 123})(),
        )


def test_validation_rejects_human_verified_and_duplicate_case_ids() -> None:
    rows = build_freeform_gold([_audited_business_record(1)], _business_contract(), _business_templates())
    rows[0]["review"]["status"] = "human_verified"
    rows[1]["case_id"] = rows[0]["case_id"]

    with pytest.raises(ValueError, match="human_verified"):
        validate_freeform_gold(rows, _business_contract())


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


def test_measured_dense_decision_uses_same_denominator_top_level_p95_and_overall_gain() -> None:
    residual_ids = [f"target-{number}" for number in range(10)]
    summary = {
        "target_count": 100,
        "target_recall_at_20": 0.80,
        "target_hits_at_20": 80,
        "residual_misses": [{
            "case_id": "text-miss", "route": "text",
            "target_evidence_ids": residual_ids, "selected_evidence_ids": [],
            "exclusion_boundary": "not_retrieved_at_20",
        }],
        "wrong_issuer_count": 0,
        "wrong_version_count": 0,
        "dense_pilot": {
            "scope": "overall", "target_count": 100,
            "sparse_target_hits_at_20": 80, "dense_target_hits_at_20": 85,
            "sparse_recall_at_20": 0.80, "dense_recall_at_20": 0.85,
            "measured_gain": 0.05,
            "wrong_issuer_count": 0, "wrong_version_count": 0,
            "p95_ms": 150,
        },
    }

    decision = decide_embedding_pilot(summary)

    assert decision["status"] == "ADOPTED"
    assert decision["measured_gain"] == pytest.approx(0.05)
    assert decision["dense_p95_ms"] == 150
    with pytest.raises(ValueError, match="dense_denominator_mismatch"):
        decide_embedding_pilot({
            **summary,
            "dense_pilot": {**summary["dense_pilot"], "target_count": 10},
        })


def test_source_record_derivation_refuses_missing_serving_identity_databases() -> None:
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
        with pytest.raises(ValueError, match="serving_databases_required"):
            derive_freeform_source_records(audited_gold, Path("unused.sqlite"), contract)
    finally:
        connection.close()


def test_financial_source_selection_uses_same_account_across_distinct_periods() -> None:
    candidates = [
        {
            "evidence_id": "revenue-2025", "filing_id": "filing-current",
            "financial_points": [{"account_id": "revenue", "period": ("2025-01-01", "2025-12-31", None)}],
        },
        {
            "evidence_id": "operating-income-2025", "filing_id": "filing-current",
            "financial_points": [{"account_id": "operating_income", "period": ("2025-01-01", "2025-12-31", None)}],
        },
        {
            "evidence_id": "revenue-2024", "filing_id": "filing-current",
            "financial_points": [{"account_id": "revenue", "period": ("2024-01-01", "2024-12-31", None)}],
        },
    ]

    selected = select_period_covered_financial_candidates(candidates, minimum_periods=2)

    assert [item["evidence_id"] for item in selected] == ["revenue-2025", "revenue-2024"]


def test_financial_source_selection_is_order_independent_and_preserves_account_period_pairs() -> None:
    candidates = [
        {
            "evidence_id": "revenue-2024", "filing_id": "filing-current",
            "financial_points": [{"account_id": "revenue", "period": ("2024-01-01", "2024-12-31", None)}],
        },
        {
            "evidence_id": "mixed", "filing_id": "filing-current",
            "financial_points": [
                {"account_id": "revenue", "period": ("2025-01-01", "2025-12-31", None)},
                {"account_id": "operating_income", "period": ("2023-01-01", "2023-12-31", None)},
            ],
        },
        {
            "evidence_id": "income-2025", "filing_id": "filing-current",
            "financial_points": [{"account_id": "operating_income", "period": ("2025-01-01", "2025-12-31", None)}],
        },
    ]

    forward = select_period_covered_financial_candidates(candidates, minimum_periods=2)
    reverse = select_period_covered_financial_candidates(list(reversed(candidates)), minimum_periods=2)

    assert [item["evidence_id"] for item in forward] == ["income-2025", "mixed"]
    assert [item["evidence_id"] for item in reverse] == ["income-2025", "mixed"]


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


def test_case_plan_requires_judgment_dimension_and_exact_slot_domains() -> None:
    case = {
        "dimension_id": "financing_pressure",
        "expected_slots": [
            {"slot_id": "debt_position", "domain": "financial"},
            {"slot_id": "financing_disclosures", "domain": "event"},
        ],
    }
    valid_plan = plan_analysis(
        "삼성전자 차입과 자본조달 압력을 공시 근거로 종합 분석해줘",
        company_candidates=["삼성전자"],
    )
    lookup_plan = plan_analysis("삼성전자 발행주식수는?", company_candidates=["삼성전자"])

    assert validate_case_plan(case, valid_plan) == (
        ("debt_position", "financial"),
        ("financing_disclosures", "event"),
    )
    with pytest.raises(ValueError, match="analysis_plan_not_judgment"):
        validate_case_plan(case, lookup_plan)
    with pytest.raises(ValueError, match="slot_contract_mismatch"):
        validate_case_plan({**case, "expected_slots": [{"slot_id": "debt_position", "domain": "text"}]}, valid_plan)
    corrected = plan_analysis(
        "삼성전자 정정 공시의 중요성을 분석해줘", company_candidates=["삼성전자"],
    )
    with pytest.raises(ValueError, match="correction_policy_mismatch"):
        validate_case_plan({
            "dimension_id": "correction_materiality", "correction_policy": "both",
            "expected_slots": [
                {"slot_id": "original_disclosure", "domain": "text"},
                {"slot_id": "effective_correction", "domain": "text"},
            ],
        }, corrected)


def test_gold_preserves_serving_admitted_multislot_targets_and_derives_mixed_route() -> None:
    contract = _contract(minimum_cases=1)
    contract["dimensions"] = ["financing_pressure"]
    record = {
        **_record(1),
        "supported_dimensions": ["financing_pressure"],
        "target_evidence_ids": ["ev-debt", "ev-capital"],
        "filing_ids": ["filing-debt", "filing-capital"],
        "slot_targets": [
            {
                "slot_id": "debt_position", "domain": "financial",
                "serving_path": "financial_fact_evidence",
                "target_evidence_ids": ["ev-debt"], "filing_ids": ["filing-debt"],
                "fact_ids": ["ff-debt"],
            },
            {
                "slot_id": "financing_disclosures", "domain": "event",
                "serving_path": "event_fact_evidence",
                "target_evidence_ids": ["ev-capital"], "filing_ids": ["filing-capital"],
                "fact_ids": ["ef-capital"],
            },
        ],
        "answerability": {"status": "proved", "requirement_count": 2},
    }
    templates = {"templates": [{
        "template_id": "financing-valid",
        "dimension_id": "financing_pressure",
        "route": "mixed",
        "template": "{issuer_name} 차입과 자본조달 압력을 공시 근거로 종합 분석해줘",
    }]}

    rows = build_freeform_gold([record], contract, templates)

    assert rows[0]["route"] == "mixed"
    assert rows[0]["target_evidence_ids"] == ["ev-capital", "ev-debt"]
    assert rows[0]["expected_slots"] == [
        {"domain": "financial", "slot_id": "debt_position"},
        {"domain": "event", "slot_id": "financing_disclosures"},
    ]


def test_gold_rejects_untrusted_serving_path_before_case_generation() -> None:
    contract = _contract(minimum_cases=1)
    contract["dimensions"] = ["business_risk"]
    record = {
        **_record(1),
        "supported_dimensions": ["business_risk"],
        "slot_targets": [{
            "slot_id": "disclosed_risk_factors", "domain": "text",
            "serving_path": "broad_keyword_fragment",
            "target_evidence_ids": ["evidence-1"], "filing_ids": ["20240101000001"],
            "content_features": {"fragment_type": "paragraph", "character_count": 120, "marker_count": 1},
        }],
        "answerability": {"status": "proved", "requirement_count": 1},
    }
    templates = {"templates": [{
        "template_id": "risk-valid", "dimension_id": "business_risk", "route": "text",
        "template": "{issuer_name} 공시에 나온 사업 위험요인과 리스크를 종합 분석해줘",
    }]}

    with pytest.raises(ValueError, match="serving_path_invalid"):
        build_freeform_gold([record], contract, templates)


def test_correction_gold_requires_distinct_original_and_current_pair() -> None:
    contract = _contract(minimum_cases=1)
    contract["dimensions"] = ["correction_materiality"]
    features = {"fragment_type": "paragraph", "character_count": 180, "marker_count": 2}
    record = {
        **_record(1),
        "supported_dimensions": ["correction_materiality"],
        "correction_policy": "both",
        "filing_ids": ["filing-original", "filing-current"],
        "target_evidence_ids": ["ev-original", "ev-current"],
        "slot_targets": [
            {
                "slot_id": "original_disclosure", "domain": "text", "serving_path": "search_document",
                "target_evidence_ids": ["ev-original"], "filing_ids": ["filing-original"],
                "content_features": features,
            },
            {
                "slot_id": "effective_correction", "domain": "text", "serving_path": "search_document",
                "target_evidence_ids": ["ev-current"], "filing_ids": ["filing-current"],
                "content_features": features,
            },
        ],
        "answerability": {"status": "proved", "requirement_count": 2},
    }
    templates = {"templates": [{
        "template_id": "correction-valid", "dimension_id": "correction_materiality", "route": "text",
        "template": "{issuer_name} 최초 공시와 정정 공시를 각각 비교하여 수정 중요성을 분석해줘",
    }]}

    with pytest.raises(ValueError, match="correction_pair_required"):
        build_freeform_gold([record], contract, templates)


def test_invalid_plan_no_query_and_unreachable_targets_are_excluded_before_scoring() -> None:
    case = {
        "dimension_id": "business_risk",
        "target_evidence_ids": ["ev-risk"],
        "expected_slots": [{"slot_id": "disclosed_risk_factors", "domain": "text"}],
    }
    judgment = plan_analysis(
        "삼성전자 공시에 나온 사업 위험요인과 리스크를 종합 분석해줘",
        company_candidates=["삼성전자"],
    )
    lookup = plan_analysis("삼성전자 매출액은?", company_candidates=["삼성전자"])

    assert evaluation_exclusion_reason(case, lookup, query_count=1, serving_evidence_ids={"ev-risk"}) == "invalid_plan"
    assert evaluation_exclusion_reason(case, judgment, query_count=0, serving_evidence_ids={"ev-risk"}) == "no_query_executed"
    assert evaluation_exclusion_reason(case, judgment, query_count=1, serving_evidence_ids=set()) == "target_not_serving_admitted"
    assert evaluation_exclusion_reason(case, judgment, query_count=1, serving_evidence_ids={"ev-risk"}) is None


@pytest.mark.parametrize(
    ("fragment_type", "text", "markers", "expected"),
    [
        ("heading", "사업 위험요인" * 20, ("위험요인",), False),
        ("table_row", "사업 위험요인", ("위험요인",), False),
        ("paragraph", "사업 위험요인과 불확실성을 구체적으로 설명합니다. " * 4, ("위험요인", "불확실성"), True),
        ("paragraph", "일반적인 사업 설명입니다. " * 10, ("위험요인",), False),
    ],
)
def test_text_answerability_rejects_headings_labels_and_marker_free_content(
    fragment_type: str, text: str, markers: tuple[str, ...], expected: bool,
) -> None:
    assert text_target_is_answer_safe(fragment_type, text, markers=markers, minimum_characters=80) is expected
