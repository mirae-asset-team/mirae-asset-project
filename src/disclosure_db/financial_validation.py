"""Independent validation and coverage reporting for financial candidates."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections import defaultdict
from contextlib import closing
from pathlib import Path
from typing import Iterable, Mapping

from .financial_extraction import extract_table_candidates


_ACCOUNTS = (
    "revenue",
    "operating_income",
    "net_income",
    "total_assets",
    "total_liabilities",
    "total_equity",
)
_ACCOUNT_ORDER = {account: index for index, account in enumerate(_ACCOUNTS)}
_CORE_FIELDS = (
    "filing_id",
    "table_id",
    "row_index",
    "column_index",
    "account_id",
    "account_name_raw",
    "statement_type",
    "statement_priority",
    "scope",
    "fiscal_year",
    "period_type",
    "period_start",
    "period_end",
    "instant_date",
    "value_numeric",
    "currency",
    "scale",
    "unit_raw",
    "account_evidence_id",
    "evidence_ids",
)


def canonical_json_bytes(payload: Mapping[str, object]) -> bytes:
    return (json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")


def _readonly_connection(database: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(
        f"file:{Path(database).resolve().as_posix()}?mode=ro&immutable=1",
        uri=True,
    )
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only=ON")
    connection.execute("PRAGMA temp_store=MEMORY")
    return connection


def _select_in(
    connection: sqlite3.Connection,
    prefix: str,
    values: list[str],
) -> list[sqlite3.Row]:
    rows: list[sqlite3.Row] = []
    for offset in range(0, len(values), 500):
        batch = values[offset : offset + 500]
        if not batch:
            continue
        placeholders = ",".join("?" for _ in batch)
        rows.extend(connection.execute(f"{prefix} ({placeholders})", batch).fetchall())
    return rows


def apply_review_decisions(
    candidates: Iterable[Mapping[str, object]],
    extraction_rejects: Iterable[Mapping[str, object]],
    decisions: Iterable[Mapping[str, object]],
) -> tuple[list[dict[str, object]], list[dict[str, object]], list[dict[str, object]]]:
    """Apply explicit agent-review choices to equal duplicate grains only."""

    admitted = [dict(item) for item in candidates]
    rejects = [dict(item) for item in extraction_rejects]
    decision_map: dict[tuple[str, str], dict[str, object]] = {}
    for raw in decisions:
        decision = dict(raw)
        key = (str(decision.get("filing_id") or ""), str(decision.get("account_id") or ""))
        if (
            not all(key)
            or key in decision_map
            or decision.get("action") != "prefer_raw_label"
            or decision.get("review_status") != "agent_audited"
            or decision.get("human_confirmed") is not False
        ):
            raise ValueError("invalid or duplicate review decision")
        decision_map[key] = decision
    duplicate_groups: dict[tuple[str, str, int], list[dict[str, object]]] = defaultdict(list)
    untouched: list[dict[str, object]] = []
    for reject in rejects:
        if reject.get("reason_code") == "duplicate_metric_grain":
            duplicate_groups[
                (
                    str(reject.get("filing_id") or ""),
                    str(reject.get("account_id") or ""),
                    int(reject.get("fiscal_year") or 0),
                )
            ].append(reject)
        else:
            untouched.append(reject)
    audit: list[dict[str, object]] = []
    for grain in sorted(duplicate_groups):
        group = duplicate_groups[grain]
        decision = decision_map.get(grain[:2])
        if decision is None:
            untouched.extend(group)
            continue
        audit_row = {
            "filing_id": grain[0],
            "account_id": grain[1],
            "fiscal_year": grain[2],
            "status": "agent_audited",
            "human_confirmed": False,
            "action": "prefer_raw_label",
            "account_name_raw": str(decision.get("account_name_raw") or ""),
            "rationale": str(decision.get("rationale") or ""),
        }
        if len({str(item.get("value_numeric") or "") for item in group}) != 1:
            audit_row["status"] = "unresolved"
            audit_row["reason_code"] = "review_value_conflict"
            untouched.extend({**item, "reason_code": "review_value_conflict"} for item in group)
            audit.append(audit_row)
            continue
        preferred = [
            item for item in group
            if str(item.get("account_name_raw") or "") == audit_row["account_name_raw"]
        ]
        if len(preferred) != 1:
            audit_row["status"] = "unresolved"
            audit_row["reason_code"] = "review_preferred_label_not_unique"
            untouched.extend(
                {**item, "reason_code": "review_preferred_label_not_unique"} for item in group
            )
            audit.append(audit_row)
            continue
        selected = dict(preferred[0])
        selected.pop("reason_code", None)
        selected["review_resolution"] = "equal_duplicate_preferred_raw_label"
        admitted.append(selected)
        audit_row["candidate_id"] = str(selected.get("candidate_id") or "")
        audit.append(audit_row)
    admitted.sort(
        key=lambda item: (
            str(item.get("issuer_corp_code") or ""),
            _ACCOUNT_ORDER.get(str(item.get("account_id") or ""), 999),
            -int(item.get("fiscal_year") or 0),
            str(item.get("candidate_id") or ""),
        )
    )
    untouched.sort(
        key=lambda item: (
            str(item.get("issuer_corp_code") or ""),
            str(item.get("reason_code") or ""),
            _ACCOUNT_ORDER.get(str(item.get("account_id") or ""), 999),
            -int(item.get("fiscal_year") or 0),
            str(item.get("candidate_id") or ""),
        )
    )
    return admitted, untouched, audit


def _reject(candidate: Mapping[str, object], reason_code: str) -> dict[str, object]:
    return {
        "candidate_id": str(candidate.get("candidate_id") or ""),
        "filing_id": str(candidate.get("filing_id") or ""),
        "issuer_corp_code": str(candidate.get("issuer_corp_code") or ""),
        "account_id": str(candidate.get("account_id") or ""),
        "fiscal_year": int(candidate.get("fiscal_year") or 0),
        "reason_code": reason_code,
    }


def _same_core(candidate: Mapping[str, object], source: Mapping[str, object]) -> bool:
    return all(candidate.get(field) == source.get(field) for field in _CORE_FIELDS)


def _fact_id(candidate: Mapping[str, object]) -> str:
    identity = "|".join(
        str(candidate.get(field) or "")
        for field in ("issuer_corp_code", "filing_id", "account_id", "fiscal_year", "scope")
    )
    return f"ff_agent_{hashlib.sha256(identity.encode('utf-8')).hexdigest()[:28]}"


def _seed_row(candidate: Mapping[str, object]) -> dict[str, object]:
    return {
        "financial_fact_id": _fact_id(candidate),
        "filing_id": str(candidate["filing_id"]),
        "issuer_corp_code": str(candidate.get("issuer_corp_code") or ""),
        "stock_code": str(candidate.get("stock_code") or ""),
        "issuer_name": str(candidate.get("issuer_name") or ""),
        "listed_name": str(candidate.get("listed_name") or ""),
        "account_id": str(candidate["account_id"]),
        "account_name_raw": str(candidate["account_name_raw"]),
        "statement_type": str(candidate["statement_type"]),
        "scope": str(candidate["scope"]),
        "fiscal_year": int(candidate["fiscal_year"]),
        "period_type": str(candidate["period_type"]),
        "period_start": candidate.get("period_start"),
        "period_end": candidate.get("period_end"),
        "instant_date": candidate.get("instant_date"),
        "value_numeric": str(candidate["value_numeric"]),
        "currency": str(candidate.get("currency") or "KRW"),
        "scale": int(candidate["scale"]),
        "unit_raw": str(candidate["unit_raw"]),
        "extraction_method": "agent_audited_annual_statement_rule_v1",
        "validation_status": "validated",
        "trust_tier": "agent_audited",
        "evidence_ids": list(candidate["evidence_ids"]),
        "account_evidence_id": str(candidate["account_evidence_id"]),
        "table_id": str(candidate["table_id"]),
        "row_index": int(candidate["row_index"]),
        "column_index": int(candidate["column_index"]),
        "audit_note": "Deterministic source reconstruction and evidence/lineage invariants passed; no human approval claimed.",
    }


def _coverage(
    manifest: Mapping[str, object],
    seed: list[dict[str, object]],
    unresolved: list[dict[str, object]],
    rejected_names: Mapping[str, str],
    review_audit: list[dict[str, object]],
) -> dict[str, object]:
    companies = [dict(item) for item in manifest.get("companies", []) if isinstance(item, Mapping)]
    manifest_rejects = [dict(item) for item in manifest.get("rejects", []) if isinstance(item, Mapping)]
    facts_by_company: dict[str, list[dict[str, object]]] = defaultdict(list)
    for fact in seed:
        facts_by_company[str(fact["issuer_corp_code"])].append(fact)
    reasons_by_grain: dict[tuple[str, str, int], set[str]] = defaultdict(set)
    for item in unresolved:
        reasons_by_grain[
            (
                str(item.get("issuer_corp_code") or ""),
                str(item.get("account_id") or ""),
                int(item.get("fiscal_year") or 0),
            )
        ].add(str(item.get("reason_code") or "unresolved"))
    company_rows: list[dict[str, object]] = []
    for company in sorted(companies, key=lambda item: str(item.get("issuer_corp_code") or "")):
        corp_code = str(company["issuer_corp_code"])
        fiscal_year = int(company["fiscal_year"])
        facts = facts_by_company.get(corp_code, [])
        present = {(str(fact["account_id"]), int(fact["fiscal_year"])) for fact in facts}
        missing: list[dict[str, object]] = []
        for account in _ACCOUNTS:
            for year in range(fiscal_year, fiscal_year - 3, -1):
                if (account, year) in present:
                    continue
                reasons = sorted(reasons_by_grain.get((corp_code, account, year), {"not_admitted"}))
                missing.append({"account_id": account, "fiscal_year": year, "reason_codes": reasons})
        company_rows.append(
            {
                "issuer_corp_code": corp_code,
                "stock_code": str(company.get("stock_code") or ""),
                "listed_name": str(company.get("listed_name") or company.get("issuer_name") or corp_code),
                "selected_filing_id": str(company.get("filing_id") or ""),
                "fiscal_year": fiscal_year,
                "validated_count": len(facts),
                "expected_count": len(_ACCOUNTS) * 3,
                "missing_count": len(missing),
                "missing": missing,
            }
        )
    for item in sorted(manifest_rejects, key=lambda row: str(row.get("issuer_corp_code") or "")):
        corp_code = str(item.get("issuer_corp_code") or "")
        reason = str(item.get("reason_code") or "manifest_rejected")
        company_rows.append(
            {
                "issuer_corp_code": corp_code,
                "stock_code": "",
                "listed_name": rejected_names.get(corp_code, corp_code),
                "selected_filing_id": None,
                "fiscal_year": None,
                "validated_count": 0,
                "expected_count": len(_ACCOUNTS) * 3,
                "missing_count": len(_ACCOUNTS) * 3,
                "missing": [{"account_id": account, "fiscal_year": None, "reason_codes": [reason]} for account in _ACCOUNTS for _ in range(3)],
            }
        )
    company_rows.sort(key=lambda item: str(item["issuer_corp_code"]))
    source_company_count = int(manifest.get("source_company_count") or len(company_rows))
    metrics: dict[str, dict[str, object]] = {}
    for account in _ACCOUNTS:
        latest_complete: list[str] = []
        three_complete: list[str] = []
        missing_names: list[str] = []
        for company in companies:
            corp_code = str(company["issuer_corp_code"])
            fiscal_year = int(company["fiscal_year"])
            years = {
                int(fact["fiscal_year"])
                for fact in facts_by_company.get(corp_code, [])
                if fact["account_id"] == account
            }
            if fiscal_year in years:
                latest_complete.append(corp_code)
            if {fiscal_year, fiscal_year - 1, fiscal_year - 2} <= years:
                three_complete.append(corp_code)
            else:
                missing_names.append(str(company.get("listed_name") or company.get("issuer_name") or corp_code))
        missing_names.extend(rejected_names.get(str(item.get("issuer_corp_code") or ""), str(item.get("issuer_corp_code") or "")) for item in manifest_rejects)
        metrics[account] = {
            "expected_company_count": source_company_count,
            "latest_validated_company_count": len(latest_complete),
            "three_period_validated_company_count": len(three_complete),
            "aggregate_eligible": len(latest_complete) == source_company_count,
            "three_period_aggregate_eligible": len(three_complete) == source_company_count,
            "missing_companies": sorted(set(missing_names)),
        }
    expected_grains = source_company_count * len(_ACCOUNTS) * 3
    return {
        "schema_version": "financial-coverage-v1",
        "source_database_sha256": str(manifest.get("source_database_sha256") or ""),
        "source_company_count": source_company_count,
        "searchable_alias_count": int(manifest.get("searchable_alias_count") or 0),
        "selected_filing_company_count": len(companies),
        "manifest_rejected_company_count": len(manifest_rejects),
        "expected_grain_count": expected_grains,
        "validated_grain_count": len(seed),
        "missing_grain_count": expected_grains - len(seed),
        "aggregate_hard_gate_passed": all(item["aggregate_eligible"] for item in metrics.values()),
        "review_resolution_count": sum(item.get("status") == "agent_audited" for item in review_audit),
        "metrics": metrics,
        "companies": company_rows,
    }


def validate_financial_candidates(
    database: Path,
    manifest: Mapping[str, object],
    candidates: Iterable[Mapping[str, object]],
    *,
    extraction_rejects: Iterable[Mapping[str, object]],
    review_decisions: Iterable[Mapping[str, object]],
) -> dict[str, object]:
    """Reconstruct candidates from immutable cells and emit audited seed/coverage."""

    reviewed_candidates, unresolved, review_audit = apply_review_decisions(
        candidates, extraction_rejects, review_decisions
    )
    companies = [dict(item) for item in manifest.get("companies", []) if isinstance(item, Mapping)]
    company_by_filing = {str(item.get("filing_id") or ""): item for item in companies}
    table_ids = sorted({str(item.get("table_id") or "") for item in reviewed_candidates if item.get("table_id")})
    evidence_ids = sorted(
        {
            str(evidence_id)
            for item in reviewed_candidates
            for evidence_id in [item.get("account_evidence_id"), *(item.get("evidence_ids") or [])]
            if evidence_id
        }
    )
    rejected_codes = [
        str(item.get("issuer_corp_code") or "")
        for item in manifest.get("rejects", [])
        if isinstance(item, Mapping)
    ]
    with closing(_readonly_connection(Path(database))) as connection:
        tables = {
            str(row["table_id"]): dict(row)
            for row in _select_in(
                connection,
                """SELECT table_id,filing_id,source_id,sequence_no,section_path_json,caption,
                          unit_text,row_count,column_count,parse_status,locator_json
                     FROM table_record WHERE table_id IN""",
                table_ids,
            )
        }
        cell_rows = _select_in(
            connection,
            """SELECT evidence_id,table_id,source_id,filing_id,row_index,column_index,rowspan,
                      colspan,cell_kind,row_header_path_json,column_header_path_json,locator_json,
                      text_raw,text_normalized,parser_version
                 FROM table_cell WHERE table_id IN""",
            table_ids,
        )
        evidence_rows = {
            str(row["evidence_id"]): dict(row)
            for row in _select_in(
                connection,
                """SELECT c.evidence_id,c.filing_id,c.table_id,c.row_index,c.column_index,
                          c.text_raw,s.parse_status AS source_parse_status,s.detected_format,
                          tr.parse_status AS table_parse_status
                     FROM table_cell c
                     JOIN source_document s ON s.source_id=c.source_id
                     JOIN table_record tr ON tr.table_id=c.table_id
                    WHERE c.evidence_id IN""",
                evidence_ids,
            )
        }
        versions = {
            str(row["filing_id"]): dict(row)
            for row in _select_in(
                connection,
                "SELECT filing_id,lineage_status,is_current FROM filing_version WHERE filing_id IN",
                sorted(company_by_filing),
            )
        }
        rejected_names: dict[str, str] = {}
        for corp_code in rejected_codes:
            row = connection.execute(
                """SELECT listed_name,issuer_name FROM filing
                    WHERE issuer_corp_code=? ORDER BY filed_at DESC,filing_id DESC LIMIT 1""",
                (corp_code,),
            ).fetchone()
            if row is not None:
                rejected_names[corp_code] = str(row["listed_name"] or row["issuer_name"] or corp_code)
    cells_by_table: dict[str, list[dict[str, object]]] = defaultdict(list)
    for row in cell_rows:
        cells_by_table[str(row["table_id"])].append(dict(row))
    reconstructed: dict[str, dict[str, object]] = {}
    for table_id, table in tables.items():
        company = company_by_filing.get(str(table["filing_id"]))
        if company is None:
            continue
        facts, _ = extract_table_candidates(
            table,
            cells_by_table.get(table_id, []),
            fiscal_year=int(company["fiscal_year"]),
        )
        reconstructed.update({str(fact["candidate_id"]): fact for fact in facts})

    valid: list[dict[str, object]] = []
    validator_rejects: list[dict[str, object]] = []
    for candidate in reviewed_candidates:
        filing_id = str(candidate.get("filing_id") or "")
        company = company_by_filing.get(filing_id)
        if company is None:
            validator_rejects.append(_reject(candidate, "candidate_filing_not_selected"))
            continue
        version = versions.get(filing_id)
        if version is None or version.get("lineage_status") not in {"root", "resolved"} or int(version.get("is_current") or 0) != 1:
            validator_rejects.append(_reject(candidate, "candidate_lineage_not_current_safe"))
            continue
        actual = reconstructed.get(str(candidate.get("candidate_id") or ""))
        if actual is None or not _same_core(candidate, actual):
            validator_rejects.append(_reject(candidate, "candidate_does_not_match_source"))
            continue
        referenced = [str(candidate.get("account_evidence_id") or ""), *map(str, candidate.get("evidence_ids") or [])]
        evidence = [evidence_rows.get(evidence_id) for evidence_id in referenced]
        if any(row is None for row in evidence):
            validator_rejects.append(_reject(candidate, "evidence_missing"))
            continue
        if any(
            row["filing_id"] != filing_id
            or row["table_id"] != candidate.get("table_id")
            or row["source_parse_status"] != "success"
            or row["table_parse_status"] != "success"
            or row["detected_format"] == "pdf"
            for row in evidence
            if row is not None
        ):
            validator_rejects.append(_reject(candidate, "evidence_invariant_failed"))
            continue
        valid.append({**candidate, "issuer_corp_code": str(company["issuer_corp_code"]), "stock_code": str(company.get("stock_code") or ""), "issuer_name": str(company.get("issuer_name") or ""), "listed_name": str(company.get("listed_name") or "")})

    by_grain: dict[tuple[str, str, int, str], list[dict[str, object]]] = defaultdict(list)
    for candidate in valid:
        by_grain[
            (
                str(candidate["issuer_corp_code"]),
                str(candidate["account_id"]),
                int(candidate["fiscal_year"]),
                str(candidate["scope"]),
            )
        ].append(candidate)
    unique: list[dict[str, object]] = []
    for group in by_grain.values():
        if len(group) == 1:
            unique.append(group[0])
        else:
            validator_rejects.extend(_reject(item, "validated_grain_duplicate") for item in group)
    seed = [_seed_row(item) for item in unique]
    seed.sort(
        key=lambda item: (
            str(item["issuer_corp_code"]),
            _ACCOUNT_ORDER[str(item["account_id"])],
            -int(item["fiscal_year"]),
        )
    )
    validator_rejects.sort(
        key=lambda item: (
            str(item.get("issuer_corp_code") or ""),
            str(item.get("reason_code") or ""),
            str(item.get("candidate_id") or ""),
        )
    )
    review_queue = [*unresolved, *validator_rejects]
    review_queue.sort(
        key=lambda item: (
            str(item.get("issuer_corp_code") or ""),
            str(item.get("reason_code") or ""),
            _ACCOUNT_ORDER.get(str(item.get("account_id") or ""), 999),
            -int(item.get("fiscal_year") or 0),
            str(item.get("candidate_id") or ""),
        )
    )
    coverage = _coverage(manifest, seed, review_queue, rejected_names, review_audit)
    return {
        "schema_version": "financial-validation-v1",
        "seed": seed,
        "validator_rejects": validator_rejects,
        "review_queue": review_queue,
        "review_audit": review_audit,
        "coverage": coverage,
    }
