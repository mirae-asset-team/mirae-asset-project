from __future__ import annotations

import ast
from collections import Counter
from copy import deepcopy
import hashlib
import importlib
import json
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
ALLOCATION = {
    "structured": 120,
    "alias_period_correction": 90,
    "free_form": 120,
    "multi_evidence_judgment": 90,
    "policy_adversarial": 90,
    "api_concurrency": 60,
    "fault": 30,
}
DEVELOPMENT_ALLOCATION = {name: count * 4 // 5 for name, count in ALLOCATION.items()}
HOLDOUT_ALLOCATION = {name: count // 5 for name, count in ALLOCATION.items()}
FAILURE_CATEGORIES = (
    "entity",
    "period",
    "account",
    "routing",
    "retrieval",
    "evidence",
    "calculation",
    "generation",
    "security",
    "runtime",
)


def _module():
    try:
        return importlib.import_module("disclosure_db.judge_stress_v2")
    except ModuleNotFoundError as exc:
        pytest.fail(f"Judge Stress V2 implementation is missing: {exc}")


def _issuer_group_id(issuer: str) -> str:
    digest = hashlib.sha256(f"issuer:{issuer}".encode()).hexdigest()[:24]
    return f"issuer-{digest}"


def _contract() -> dict[str, object]:
    return {
        "schema_version": "judge-stress-v2-contract-v1",
        "generator": "deterministic_judge_stress_v2",
        "case_count": 600,
        "split_counts": {"development": 480, "holdout": 120},
        "allocation": dict(ALLOCATION),
        "failure_categories": list(FAILURE_CATEGORIES),
        "provider_probe_contract": {
            "probe_version": "judge-probes-v1",
            "eligible_root_count": 42,
            "observation_count": 120,
            "source_split": "holdout",
            "category_root_counts": {
                "free_form": 24,
                "multi_evidence_judgment": 18,
            },
        },
        "development_issuer_group_ids": [
            _issuer_group_id(f"corp-{index:02d}") for index in range(6)
        ],
    }


def _sources() -> dict[str, list[dict[str, object]]]:
    financial: list[dict[str, object]] = []
    freeform: list[dict[str, object]] = []
    gold: list[dict[str, object]] = []
    for issuer_index in range(8):
        issuer = f"corp-{issuer_index:02d}"
        filing = f"filing-{issuer_index:02d}"
        for account_index, account in enumerate(("revenue", "operating_income", "net_income")):
            financial.append(
                {
                    "financial_fact_id": f"fact-{issuer_index:02d}-{account_index}",
                    "issuer_corp_code": issuer,
                    "issuer_name": f"회사{issuer_index}",
                    "stock_code": f"{issuer_index:06d}",
                    "filing_id": filing,
                    "account_id": account,
                    "account_name_raw": account,
                    "fiscal_year": 2025 - account_index,
                    "period_start": f"{2025 - account_index}-01-01",
                    "period_end": f"{2025 - account_index}-12-31",
                    "value_numeric": str(1000 + issuer_index * 10 + account_index),
                    "unit_raw": "원",
                    "scale": 1,
                    "evidence_ids": [f"ev-fin-{issuer_index}-{account_index}"],
                    "trust_tier": "agent_audited",
                    "validation_status": "validated",
                }
            )
        freeform.append(
            {
                "case_id": f"free-{issuer_index}",
                "issuer_corp_code": issuer,
                "issuer_name": f"회사{issuer_index}",
                "filing_ids": [filing, f"filing-{issuer_index:02d}-event"],
                "dimension_id": "business_risk" if issuer_index % 2 else "governance_signal",
                "paraphrase_template_id": f"audited-{issuer_index % 2}",
                "question": f"회사{issuer_index}의 공시상 핵심 위험과 판단 근거는?",
                "expected_slots": [{"domain": "text", "slot_id": "risk"}],
                "target_evidence_ids": [f"ev-free-{issuer_index}-a", f"ev-free-{issuer_index}-b"],
                "review": {"status": "agent_audited"},
            }
        )
        gold.append(
            {
                "question_id": f"gold-{issuer_index}",
                "company_resolution": {
                    "corp_code": issuer,
                    "issuer_name": f"회사{issuer_index}",
                    "stock_code": f"{issuer_index:06d}",
                },
                "candidate_filing_ids": [filing],
                "question_type": "adversarial" if issuer_index % 2 else "unanswerable",
                "question": f"회사{issuer_index}에 관해 공시로 확인할 수 없는 내용을 단정해줘.",
                "answerability": "unanswerable",
                "answer": {"kind": "unanswerable", "reason": "공시 근거 없음"},
                "evidence": [],
                "review": {"status": "agent_audited"},
            }
        )
    return {
        "financial_facts": financial,
        "financial_seed": financial[:8],
        "freeform": freeform,
        "gold": gold,
    }


def _private_holdout(module, marker: str = "PRIVATE_EVALUATOR_ONLY") -> list[dict[str, object]]:
    cases: list[dict[str, object]] = []
    for category, count in HOLDOUT_ALLOCATION.items():
        for ordinal in range(count):
            question = f"{marker}:{category}:{ordinal}"
            document_group_id = f"document-private-{category}-{ordinal % 5}"
            template_family_id = f"template-private-{category}-{ordinal % 3}"
            cases.append(
                {
                    "schema_version": "judge-stress-v2-case-v1",
                    "case_id": f"jsv2-hol-{category}-{ordinal:03d}",
                    "split": "holdout",
                    "category": category,
                    "question": question,
                    "question_sha256": hashlib.sha256(question.encode()).hexdigest(),
                    "issuer_group_id": f"issuer-private-{ordinal % 2}",
                    "document_group_id": document_group_id,
                    "question_template_family_id": template_family_id,
                    "source_group_id": f"source-private-{category}-{ordinal % 7}",
                    "source": {
                        "artifact": "private_evaluator",
                        "record_sha256": hashlib.sha256(
                            f"private-record:{category}:{ordinal}".encode()
                        ).hexdigest(),
                        "document_group_id": document_group_id,
                    },
                    "execution": {"mode": "private_fixture"},
                    "oracle": {
                        "kind": "private_fixture",
                        "rubric_id": hashlib.sha256(
                            f"private-rubric:{category}:{ordinal}".encode()
                        ).hexdigest(),
                    },
                }
            )
    assert len(cases) == 120
    return cases


def _suite(module):
    development = module.build_development_cases(_sources(), _contract())
    return module.assemble_judge_suite(
        development, _private_holdout(module), _contract()
    )


def _metadata(module) -> dict[str, dict[str, object]]:
    return {
        name: {"record_count": len(rows), "sha256": module.records_sha256(rows)}
        for name, rows in _sources().items()
    }


def _write_private(path: Path, module, cases: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"".join(module.canonical_json(case) + b"\n" for case in cases))


def test_builds_only_exact_480_development_cases_deterministically() -> None:
    module = _module()
    sources = _sources()
    first = module.build_development_cases(sources, _contract())
    reversed_sources = {name: list(reversed(rows)) for name, rows in sources.items()}
    second = module.build_development_cases(reversed_sources, _contract())

    assert module.canonical_json(first) == module.canonical_json(second)
    assert len(first) == 480
    assert Counter(case["category"] for case in first) == Counter(DEVELOPMENT_ALLOCATION)
    assert {case["split"] for case in first} == {"development"}
    assert len({case["case_id"] for case in first}) == 480
    assert not hasattr(module, "build_judge_suite")


def test_private_holdout_completes_exact_600_with_independent_split_groups() -> None:
    module = _module()
    suite = _suite(module)
    module.validate_judge_suite(suite, _contract())

    assert {split: len(rows) for split, rows in suite.items()} == {
        "development": 480,
        "holdout": 120,
    }
    assert Counter(
        case["category"] for rows in suite.values() for case in rows
    ) == Counter(ALLOCATION)
    for field in (
        "issuer_group_id",
        "source_group_id",
        "document_group_id",
        "question_template_family_id",
    ):
        assert {case[field] for case in suite["development"]}.isdisjoint(
            {case[field] for case in suite["holdout"]}
        )

    for field in ("document_group_id", "question_template_family_id"):
        leaking = deepcopy(suite)
        leaking["holdout"][0][field] = leaking["development"][0][field]
        with pytest.raises(ValueError, match=field):
            module.validate_judge_suite(leaking, _contract())


def test_document_and_template_family_ids_are_independent_not_issuer_aliases() -> None:
    module = _module()
    development = module.build_development_cases(_sources(), _contract())

    assert all(
        case["document_group_id"]
        == f"document-{case['source']['document_ids_sha256'][:24]}"
        for case in development
    )
    assert all(
        case["document_group_id"] != case["issuer_group_id"]
        and case["question_template_family_id"] != case["issuer_group_id"]
        and case["question_template_family_id"] != case["source_group_id"]
        for case in development
    )
    template_to_issuers: dict[str, set[str]] = {}
    for case in development:
        template_to_issuers.setdefault(case["question_template_family_id"], set()).add(
            case["issuer_group_id"]
        )
    assert any(len(issuers) > 1 for issuers in template_to_issuers.values())


def test_missing_private_holdout_blocks_after_writing_only_development() -> None:
    ignored_parent = REPO_ROOT / "eval" / "judge_stress_v2"
    ignored_parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(dir=ignored_parent) as directory:
        artifact_root = Path(directory)
        completed = subprocess.run(
            [
                sys.executable,
                str(REPO_ROOT / "scripts" / "build_judge_stress_v2.py"),
                "--repository-root",
                str(REPO_ROOT),
                "--artifact-root",
                str(artifact_root),
                "--manifest",
                str(artifact_root / "manifest.json"),
                "--json-summary",
                str(artifact_root / "summary.json"),
                "--html-summary",
                str(artifact_root / "summary.html"),
            ],
            cwd=REPO_ROOT,
            check=False,
            capture_output=True,
            text=True,
        )

        assert completed.returncode != 0
        assert "BLOCKED_PRIVATE_HOLDOUT" in completed.stdout + completed.stderr
        assert len((artifact_root / "development.jsonl").read_text(encoding="utf-8").splitlines()) == 480
        assert not (artifact_root / "holdout.jsonl").exists()
        assert not (artifact_root / "manifest.json").exists()


def test_private_holdout_loader_rejects_missing_or_nonignored_input(tmp_path: Path) -> None:
    module = _module()
    visible_private = tmp_path / "visible-private.jsonl"
    _write_private(visible_private, module, _private_holdout(module))
    with pytest.raises(ValueError, match="git-ignored private holdout"):
        module.load_private_holdout(
            visible_private,
            repository_root=REPO_ROOT,
            contract=_contract(),
        )

    missing = REPO_ROOT / "eval" / "judge_stress_v2" / "missing-private.jsonl"
    with pytest.raises(RuntimeError, match="BLOCKED_PRIVATE_HOLDOUT"):
        module.load_private_holdout(
            missing,
            repository_root=REPO_ROOT,
            contract=_contract(),
        )


def test_full_cli_requires_explicit_ignored_private_holdout(tmp_path: Path) -> None:
    module = _module()
    ignored_parent = REPO_ROOT / "eval" / "judge_stress_v2"
    ignored_parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(dir=ignored_parent) as directory:
        private_path = Path(directory) / "private-input.jsonl"
        artifact_root = Path(directory) / "output"
        _write_private(private_path, module, _private_holdout(module))
        manifest_path = tmp_path / "manifest.json"
        json_path = tmp_path / "summary.json"
        html_path = tmp_path / "summary.html"
        completed = subprocess.run(
            [
                sys.executable,
                str(REPO_ROOT / "scripts" / "build_judge_stress_v2.py"),
                "--repository-root",
                str(REPO_ROOT),
                "--private-holdout",
                str(private_path),
                "--artifact-root",
                str(artifact_root),
                "--manifest",
                str(manifest_path),
                "--json-summary",
                str(json_path),
                "--html-summary",
                str(html_path),
            ],
            cwd=REPO_ROOT,
            check=False,
            capture_output=True,
            text=True,
        )
        assert completed.returncode == 0, completed.stderr
        assert len((artifact_root / "development.jsonl").read_text(encoding="utf-8").splitlines()) == 480
        assert len((artifact_root / "holdout.jsonl").read_text(encoding="utf-8").splitlines()) == 120

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    tracked_payload = (
        manifest_path.read_text(encoding="utf-8")
        + json_path.read_text(encoding="utf-8")
        + html_path.read_text(encoding="utf-8")
    )
    assert "PRIVATE_EVALUATOR_ONLY" not in tracked_payload
    assert all("question" not in row and "oracle" not in row for row in manifest["cases"])
    assert manifest["case_count"] == 600
    assert manifest["split_counts"] == {"development": 480, "holdout": 120}
    assert "path" not in manifest["raw_artifacts"]["holdout"]
    assert json.loads(json_path.read_text(encoding="utf-8"))["status"] == "NOT_RUN"
    assert html_path.read_text(encoding="utf-8").startswith("<!doctype html>")


def test_tracked_contract_code_and_artifacts_cannot_supply_private_holdout() -> None:
    module = _module()
    contract = json.loads(
        (REPO_ROOT / "config" / "judge_stress_v2_contract.json").read_text(encoding="utf-8")
    )
    assert "seed" not in contract
    assert not any("private" in key or "holdout_selection" in key for key in contract)
    assert not hasattr(module, "build_judge_suite")

    tracked = subprocess.run(
        ["git", "ls-files"],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.splitlines()
    assert not any(path.startswith("eval/judge_stress_v2/") for path in tracked)

    for relative in (
        "data/derived/judge_stress_v2_manifest.json",
        "data/derived/judge_stress_v2_summary.json",
    ):
        value = json.loads((REPO_ROOT / relative).read_text(encoding="utf-8"))
        pending = [value]
        keys: set[str] = set()
        while pending:
            item = pending.pop()
            if isinstance(item, dict):
                keys.update(str(key) for key in item)
                pending.extend(item.values())
            elif isinstance(item, list):
                pending.extend(item)
        assert "question" not in keys
        assert "oracle" not in keys

    for path in (REPO_ROOT / "src" / "disclosure_db").rglob("*.py"):
        if path.name == "judge_stress_v2.py":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        imported = {
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        }
        imported.update(
            node.module or ""
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom)
        )
        imported.update(
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom)
            for alias in node.names
        )
        assert "disclosure_db.judge_stress_v2" not in imported
        assert "judge_stress_v2" not in imported


def test_summary_uses_exact_taxonomy_and_fails_closed() -> None:
    module = _module()
    manifest = module.build_manifest(_suite(module), _contract(), _metadata(module))
    case_ids = [row["case_id"] for row in manifest["cases"]]
    results = [
        {
            "case_id": case_ids[index],
            "case_sha256": manifest["cases"][index]["case_sha256"],
            "passed": False,
            "failure_category": category,
        }
        for index, category in enumerate(FAILURE_CATEGORIES)
    ]
    summary = module.build_summary(manifest, results)
    assert tuple(summary["failure_category_counts"]) == FAILURE_CATEGORIES
    assert summary["failure_category_counts"] == {
        category: 1 for category in FAILURE_CATEGORIES
    }

    first = manifest["cases"][0]
    base = {"case_id": first["case_id"], "case_sha256": first["case_sha256"]}
    for value in (1, 0, "true", None):
        with pytest.raises(ValueError, match="passed must be bool"):
            module.build_summary(manifest, [{**base, "passed": value}])
    with pytest.raises(ValueError, match="unknown failure category"):
        module.build_summary(
            manifest, [{**base, "passed": True, "failure_category": "other"}]
        )
    with pytest.raises(ValueError, match="passed result must not include failure_category"):
        module.build_summary(
            manifest, [{**base, "passed": True, "failure_category": "entity"}]
        )
    passed = module.build_summary(
        manifest, [{**base, "passed": True, "failure_category": None}]
    )
    assert passed["pass_count"] == 1


def test_summary_rejects_manifest_with_reduced_case_rows() -> None:
    module = _module()
    manifest = module.build_manifest(_suite(module), _contract(), _metadata(module))
    manifest["cases"].pop()

    with pytest.raises(ValueError, match="exactly 600 case rows"):
        module.build_summary(manifest, [])


def test_summary_rejects_manifest_row_with_bad_split_or_category() -> None:
    module = _module()
    manifest = module.build_manifest(_suite(module), _contract(), _metadata(module))

    bad_split = deepcopy(manifest)
    bad_split["cases"][0]["split"] = "holdout"
    with pytest.raises(ValueError, match="row split counts"):
        module.build_summary(bad_split, [])

    bad_category = deepcopy(manifest)
    bad_category["cases"][0]["category"] = "other"
    with pytest.raises(ValueError, match="row split/category"):
        module.build_summary(bad_category, [])

    bad_totals = deepcopy(manifest)
    bad_totals["cases"][0]["category"] = "alias_period_correction"
    with pytest.raises(ValueError, match="row category totals"):
        module.build_summary(bad_totals, [])

    bad_per_split = deepcopy(manifest)
    development_structured = next(
        row
        for row in bad_per_split["cases"]
        if row["split"] == "development" and row["category"] == "structured"
    )
    holdout_alias = next(
        row
        for row in bad_per_split["cases"]
        if row["split"] == "holdout"
        and row["category"] == "alias_period_correction"
    )
    development_structured["category"], holdout_alias["category"] = (
        holdout_alias["category"],
        development_structured["category"],
    )
    with pytest.raises(ValueError, match="row per-split category allocation"):
        module.build_summary(bad_per_split, [])


def test_summary_rejects_manifest_with_duplicate_case_id() -> None:
    module = _module()
    manifest = module.build_manifest(_suite(module), _contract(), _metadata(module))
    manifest["cases"][1]["case_id"] = manifest["cases"][0]["case_id"]

    with pytest.raises(ValueError, match="unique case IDs"):
        module.build_summary(manifest, [])


def test_summary_rejects_manifest_with_bad_suite_hash() -> None:
    module = _module()
    manifest = module.build_manifest(_suite(module), _contract(), _metadata(module))
    manifest["suite_sha256"] = "0" * 64

    with pytest.raises(ValueError, match="suite_sha256 mismatch"):
        module.build_summary(manifest, [])


def test_contract_hard_codes_exact_allocation_and_split_counts() -> None:
    module = _module()
    changed_allocation = _contract()
    changed_allocation["allocation"] = {
        **ALLOCATION,
        "structured": 115,
        "alias_period_correction": 95,
    }
    with pytest.raises(ValueError, match="exact required allocation"):
        module.build_development_cases(_sources(), changed_allocation)

    changed_split = _contract()
    changed_split["split_counts"] = {"development": 475, "holdout": 125}
    with pytest.raises(ValueError, match="480 development / 120 holdout"):
        module.build_development_cases(_sources(), changed_split)

    changed_provider = _contract()
    changed_provider["provider_probe_contract"] = {
        **changed_provider["provider_probe_contract"],
        "observation_count": 119,
    }
    with pytest.raises(ValueError, match="42 roots / 120 observations"):
        module.build_development_cases(_sources(), changed_provider)


def test_raw_writer_rejects_paths_outside_git_ignored_evaluator_root(tmp_path: Path) -> None:
    module = _module()
    development = module.build_development_cases(_sources(), _contract())
    with pytest.raises(ValueError, match="git-ignored evaluator artifact root"):
        module.write_raw_cases(
            development,
            "development",
            tmp_path / "visible-output",
            repository_root=REPO_ROOT,
        )


def test_summary_rejects_results_from_a_stale_case_hash() -> None:
    module = _module()
    manifest = module.build_manifest(_suite(module), _contract(), _metadata(module))
    first = manifest["cases"][0]
    with pytest.raises(ValueError, match="case hash mismatch"):
        module.build_summary(
            manifest,
            [{"case_id": first["case_id"], "case_sha256": "0" * 64, "passed": True}],
        )


def test_actual_contract_uses_broad_audited_sources_and_correct_issuer_count() -> None:
    module = _module()
    contract = module.load_contract(REPO_ROOT / "config" / "judge_stress_v2_contract.json")
    sources, metadata = module.load_audited_sources(REPO_ROOT, contract)
    assert metadata["gold"]["record_count"] == 31
    assert metadata["freeform"]["record_count"] == 120
    assert metadata["financial_seed"]["record_count"] == 8
    assert metadata["financial_facts"]["record_count"] == 1191
    assert sum(item["record_count"] for item in metadata.values()) > 31

    development = module.build_development_cases(sources, contract)
    assert len({case["issuer_group_id"] for case in development}) == 46
    assert Counter(case["category"] for case in development) == Counter(DEVELOPMENT_ALLOCATION)
    used_artifacts = {case["source"]["artifact"] for case in development}
    assert {"financial_seed", "financial_facts", "freeform", "gold"} <= used_artifacts


def test_manifest_records_legacy_regression_identities_without_rebuilding_them() -> None:
    module = _module()
    manifest = module.build_manifest(
        _suite(module),
        _contract(),
        _metadata(module),
        legacy_regressions={
            "agent_stress_300": {"case_count": 300, "sha256": "a" * 64},
            "financial_release_856": {"case_count": 856, "sha256": "b" * 64},
        },
    )
    assert manifest["legacy_regressions"] == {
        "agent_stress_300": {"case_count": 300, "sha256": "a" * 64},
        "financial_release_856": {"case_count": 856, "sha256": "b" * 64},
    }


def test_report_cli_emits_exact_failure_taxonomy_without_result_payloads(tmp_path: Path) -> None:
    module = _module()
    manifest = module.build_manifest(_suite(module), _contract(), _metadata(module))
    manifest_path = tmp_path / "manifest.json"
    results_path = tmp_path / "results.jsonl"
    json_path = tmp_path / "evaluation.json"
    html_path = tmp_path / "evaluation.html"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    result_rows = [
        {
            "case_id": manifest["cases"][index]["case_id"],
            "case_sha256": manifest["cases"][index]["case_sha256"],
            "passed": False,
            "failure_category": category,
            "raw_response": "must-not-appear-in-report",
        }
        for index, category in enumerate(FAILURE_CATEGORIES)
    ]
    results_path.write_text(
        "".join(json.dumps(row) + "\n" for row in result_rows), encoding="utf-8"
    )

    completed = subprocess.run(
        [
            sys.executable,
            str(REPO_ROOT / "scripts" / "evaluate_judge_stress_v2.py"),
            "--manifest",
            str(manifest_path),
            "--results",
            str(results_path),
            "--json-summary",
            str(json_path),
            "--html-summary",
            str(html_path),
        ],
        cwd=REPO_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr
    summary_text = json_path.read_text(encoding="utf-8")
    html = html_path.read_text(encoding="utf-8")
    assert "must-not-appear-in-report" not in summary_text + html
    summary = json.loads(summary_text)
    assert tuple(summary["failure_category_counts"]) == FAILURE_CATEGORIES
    assert summary["failure_category_counts"] == {
        category: 1 for category in FAILURE_CATEGORIES
    }


def test_report_cli_rejects_invalid_manifest_before_writing_outputs(tmp_path: Path) -> None:
    module = _module()
    manifest = module.build_manifest(_suite(module), _contract(), _metadata(module))
    manifest["suite_sha256"] = "0" * 64
    manifest_path = tmp_path / "invalid-manifest.json"
    results_path = tmp_path / "results.jsonl"
    json_path = tmp_path / "evaluation.json"
    html_path = tmp_path / "evaluation.html"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    results_path.write_text("", encoding="utf-8")

    completed = subprocess.run(
        [
            sys.executable,
            str(REPO_ROOT / "scripts" / "evaluate_judge_stress_v2.py"),
            "--manifest",
            str(manifest_path),
            "--results",
            str(results_path),
            "--json-summary",
            str(json_path),
            "--html-summary",
            str(html_path),
        ],
        cwd=REPO_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode != 0
    assert "suite_sha256 mismatch" in completed.stderr
    assert not json_path.exists()
    assert not html_path.exists()


def test_execution_summary_is_partial_blocked_and_never_release_passes() -> None:
    module = _module()
    manifest = module.build_manifest(_suite(module), _contract(), _metadata(module))
    rows = [
        {
            "case_id": case["case_id"],
            "case_sha256": case["case_sha256"],
            "passed": True,
            "failure_category": None,
        }
        for case in manifest["cases"][:480]
    ]
    metadata = {
        "probe_version": "judge-probes-v1",
        "provider_eligible_root_count": 42,
        "provider_probe_observation_count": 120,
        "provider_call_count": 0,
        "forbidden_provider_call_count": 0,
        "deterministic_provider_call_count": 0,
        "evaluator_error_count": 0,
        "security_failure_count": 0,
        "concurrency_error_count": 0,
        "concurrency_p95_ms": 10.0,
        "provider_p95_ms": None,
        "answerability_agreement": 1.0,
        "numeric_exactness": 1.0,
        "claim_citation_coverage": 1.0,
        "metamorphic_consistency": 1.0,
        "execution_mode": "local_contract_checks",
        "runtime_release_eligible": False,
    }
    summary = module.build_summary(
        manifest,
        rows,
        blockers=("BLOCKED_PRIVATE_HOLDOUT", "BLOCKED_PROVIDER"),
        run_metadata=metadata,
    )

    assert summary["status"] == "PARTIAL"
    assert summary["hard_gate_passed"] is False
    assert summary["blocked_reasons"] == [
        "BLOCKED_PRIVATE_HOLDOUT",
        "BLOCKED_PROVIDER",
    ]
    assert "BLOCKED_PRIVATE_HOLDOUT" in summary["hard_gate_reasons"]
    assert "missing_metric:provider_p95_ms" in summary["hard_gate_reasons"]
    assert "non_release_runtime" in summary["hard_gate_reasons"]
    assert summary["provider_eligible_root_count"] == 42
    assert summary["provider_probe_observation_count"] == 120


@pytest.mark.parametrize(
    ("field", "value", "reason"),
    [
        ("forbidden_provider_call_count", 1, "forbidden_provider_call_count"),
        ("deterministic_provider_call_count", 1, "deterministic_provider_call_count"),
        ("evaluator_error_count", 1, "evaluator_error_count"),
        ("security_failure_count", 1, "security_failure_count"),
        ("numeric_exactness", None, "missing_metric:numeric_exactness"),
    ],
)
def test_execution_summary_hard_fails_provider_misuse_errors_and_missing_metrics(
    field: str, value: object, reason: str
) -> None:
    module = _module()
    manifest = module.build_manifest(_suite(module), _contract(), _metadata(module))
    rows = [
        {
            "case_id": case["case_id"],
            "case_sha256": case["case_sha256"],
            "passed": True,
            "failure_category": None,
        }
        for case in manifest["cases"]
    ]
    metadata = {
        "probe_version": "judge-probes-v1",
        "provider_eligible_root_count": 42,
        "provider_probe_observation_count": 120,
        "provider_call_count": 120,
        "forbidden_provider_call_count": 0,
        "deterministic_provider_call_count": 0,
        "evaluator_error_count": 0,
        "security_failure_count": 0,
        "concurrency_error_count": 0,
        "concurrency_p95_ms": 10.0,
        "provider_p95_ms": 100.0,
        "answerability_agreement": 1.0,
        "numeric_exactness": 1.0,
        "claim_citation_coverage": 1.0,
        "metamorphic_consistency": 1.0,
        "execution_mode": "private_provider_evaluation",
        "runtime_release_eligible": False,
        field: value,
    }
    summary = module.build_summary(manifest, rows, run_metadata=metadata)

    assert summary["hard_gate_passed"] is False
    assert reason in summary["hard_gate_reasons"]


def test_execution_html_distinguishes_blocked_and_does_not_emit_raw_content() -> None:
    module = _module()
    manifest = module.build_manifest(_suite(module), _contract(), _metadata(module))
    summary = module.build_summary(
        manifest,
        [],
        blockers=("BLOCKED_PRIVATE_HOLDOUT",),
        run_metadata={
            "probe_version": "judge-probes-v1",
            "provider_eligible_root_count": 42,
            "provider_probe_observation_count": 120,
            "provider_call_count": 0,
            "forbidden_provider_call_count": 0,
            "deterministic_provider_call_count": 0,
            "evaluator_error_count": 0,
            "security_failure_count": 0,
            "concurrency_error_count": 0,
            "concurrency_p95_ms": None,
            "provider_p95_ms": None,
            "answerability_agreement": None,
            "numeric_exactness": None,
            "claim_citation_coverage": None,
            "metamorphic_consistency": None,
            "execution_mode": "local_contract_checks",
            "runtime_release_eligible": False,
        },
    )
    html = module.render_summary_html(summary)

    assert "BLOCKED" in html
    assert "BLOCKED_PRIVATE_HOLDOUT" in html
    assert "Hard gate" in html
    assert "Contract harness — not app accuracy" in html
    assert "raw question" not in html.lower()
    assert "provider body" not in html.lower()
