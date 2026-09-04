"""Gold Workbench record hydration and fail-closed validation helpers."""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any, Mapping

from .gold_validation import validate_record_contract


QUESTION_TYPES = frozenset({
    "single_filing_fact",
    "table_cell",
    "period_comparison",
    "cross_company_comparison",
    "correction_aware",
    "multi_filing_synthesis",
    "unanswerable",
    "adversarial",
})
ANSWERABILITIES = frozenset({"answerable", "unanswerable", "ambiguous"})
SCOPES = frozenset({"consolidated", "separate", "not_applicable"})
VERSION_BASES = frozenset({"latest_effective", "as_of", "not_applicable"})
EVIDENCE_ROLES = frozenset({"support", "operand", "version_before", "version_after", "distractor"})


def default_annotation(review: Mapping[str, Any]) -> dict[str, Any]:
    citations = review.get("citations") if isinstance(review.get("citations"), list) else []
    evidence_ids = list(dict.fromkeys(
        str(item.get("evidence_id"))
        for item in citations
        if isinstance(item, Mapping) and item.get("evidence_id")
    ))
    filing_ids = list(dict.fromkeys(
        str(item.get("filing_id"))
        for item in citations
        if isinstance(item, Mapping) and item.get("filing_id")
    ))
    answerable = review.get("answerable") is not False and review.get("verdict") != "abstain_ok"
    return {
        "question_id": str(review.get("question_id") or f"gold_{str(review['review_id'])[:16]}"),
        "question_type": "single_filing_fact" if answerable else "unanswerable",
        "answerability": "answerable" if answerable else "unanswerable",
        # Deliberately blank: the annotator should establish truth from evidence before
        # revealing or copying the model answer.
        "answer": {"kind": "text", "text": ""} if answerable else {"kind": "unanswerable", "reason": ""},
        "candidate_filing_ids": filing_ids,
        "period": {
            "period_type": "not_applicable",
            "start_date": None,
            "end_date": None,
            "instant_date": None,
        },
        "scope": "not_applicable",
        "as_of": None,
        "version_basis": "latest_effective" if filing_ids else "not_applicable",
        "formula": None,
        "attack_label": None,
        "evidence_ids": evidence_ids,
        "evidence_roles": {evidence_id: "support" for evidence_id in evidence_ids},
        "table_structure_validated": False,
        "notes": "",
    }


def _readonly_connection(database: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(
        f"file:{Path(database).resolve().as_posix()}?mode=ro&immutable=1",
        uri=True,
    )
    connection.row_factory = sqlite3.Row
    return connection


def _format_of(detected: object) -> str:
    value = str(detected or "")
    if value == "dart_xml":
        return "xml"
    if value in {"exchange_html", "viewer_html"}:
        return "html"
    if value == "pdf":
        return "pdf"
    return "other"


def _json_object(value: object) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    if not isinstance(value, str):
        return {}
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return {}
    return dict(parsed) if isinstance(parsed, Mapping) else {}


def _unique_strings(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return list(dict.fromkeys(str(item).strip() for item in value if str(item).strip()))


def hydrate_packet(
    corpus_database: Path | None,
    *,
    evidence_ids: list[str],
    filing_ids: list[str],
) -> tuple[dict[str, Any], list[dict[str, str]]]:
    """Hydrate exact evidence, source, filing, and version metadata read-only."""
    packet: dict[str, Any] = {
        "evidence": [],
        "filings": [],
        "sources": [],
        "versions": [],
    }
    issues: list[dict[str, str]] = []
    if corpus_database is None or not Path(corpus_database).is_file():
        return packet, [{"rule_id": "corpus_unavailable", "message": "원문 코퍼스 DB를 열 수 없습니다."}]

    evidence_ids = list(dict.fromkeys(evidence_ids))
    filing_ids = list(dict.fromkeys(filing_ids))
    try:
        with closing(_readonly_connection(Path(corpus_database))) as connection:
            required = {"filing", "filing_version", "source_document", "fragment", "table_cell", "table_record"}
            existing = {
                str(row[0])
                for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
            }
            missing = sorted(required - existing)
            if missing:
                return packet, [{
                    "rule_id": "corpus_schema_missing",
                    "message": ", ".join(missing),
                }]

            if evidence_ids:
                placeholders = ",".join("?" for _ in evidence_ids)
                rows = connection.execute(
                    f"""SELECT x.evidence_id,x.kind,x.table_id,x.filing_id,x.source_id,
                               x.text_value,x.locator_json,s.source_path,s.sha256,s.detected_format,
                               s.parse_status,f.issuer_name,f.issuer_corp_code,f.stock_code,
                               f.report_name_raw,f.filed_at,v.event_id,v.parent_filing_id,
                               v.lineage_status,v.lineage_confidence,v.is_current,
                               v.effective_from,v.effective_to,v.rationale,tr.parse_status AS table_parse_status
                          FROM (
                            SELECT evidence_id,'fragment' AS kind,table_id,filing_id,source_id,
                                   text_normalized AS text_value,locator_json FROM fragment
                            UNION ALL
                            SELECT evidence_id,'table_cell' AS kind,table_id,filing_id,source_id,
                                   text_normalized AS text_value,locator_json FROM table_cell
                          ) x
                          JOIN source_document s ON s.source_id=x.source_id
                          JOIN filing f ON f.filing_id=x.filing_id
                          JOIN filing_version v ON v.filing_id=x.filing_id
                          LEFT JOIN table_record tr ON tr.table_id=x.table_id
                         WHERE x.evidence_id IN ({placeholders})
                         ORDER BY x.filing_id,x.source_id,x.evidence_id""",
                    evidence_ids,
                ).fetchall()
            else:
                rows = []

            found_ids = {str(row["evidence_id"]) for row in rows}
            for evidence_id in evidence_ids:
                if evidence_id not in found_ids:
                    issues.append({"rule_id": "evidence_not_found", "message": evidence_id})

            source_ids: list[str] = []
            for row in rows:
                locator = _json_object(row["locator_json"])
                locator.setdefault("kind", str(row["kind"]))
                source_id = str(row["source_id"])
                source_ids.append(source_id)
                packet["evidence"].append({
                    "evidence_id": str(row["evidence_id"]),
                    "kind": str(row["kind"]),
                    "table_id": row["table_id"],
                    "filing_id": str(row["filing_id"]),
                    "source_id": source_id,
                    "text": str(row["text_value"] or "")[:12000],
                    "locator": locator,
                    "source_path": str(row["source_path"] or ""),
                    "source_sha256": str(row["sha256"] or ""),
                    "detected_format": str(row["detected_format"] or ""),
                    "parse_status": str(row["parse_status"] or ""),
                    "report_name": str(row["report_name_raw"] or ""),
                    "filed_at": str(row["filed_at"] or ""),
                    "lineage_status": str(row["lineage_status"] or ""),
                    "is_current": bool(row["is_current"]),
                    "effective_from": row["effective_from"],
                    "effective_to": row["effective_to"],
                    "table_parse_status": row["table_parse_status"],
                })
                if str(row["parse_status"] or "") != "success":
                    issues.append({
                        "rule_id": "source_parse_not_success",
                        "message": f"{row['evidence_id']}:{row['parse_status']}",
                    })
                if str(row["kind"]) == "table_cell" and str(row["table_parse_status"] or "") != "success":
                    issues.append({
                        "rule_id": "table_parse_not_success",
                        "message": f"{row['evidence_id']}:{row['table_parse_status']}",
                    })
                if str(row["lineage_status"] or "") not in {"root", "resolved"}:
                    issues.append({
                        "rule_id": "unresolved_lineage",
                        "message": f"{row['evidence_id']}:{row['lineage_status']}",
                    })
                filing_ids.append(str(row["filing_id"]))

            filing_ids = list(dict.fromkeys(filing_ids))
            if filing_ids:
                placeholders = ",".join("?" for _ in filing_ids)
                filing_rows = connection.execute(
                    f"""SELECT f.filing_id,f.issuer_name,f.issuer_corp_code,f.stock_code,
                               f.report_name_raw,f.filed_at,v.event_id,v.parent_filing_id,
                               v.lineage_status,v.lineage_confidence,v.is_current,
                               v.effective_from,v.effective_to,v.rationale
                          FROM filing f JOIN filing_version v ON v.filing_id=f.filing_id
                         WHERE f.filing_id IN ({placeholders}) ORDER BY f.filing_id""",
                    filing_ids,
                ).fetchall()
            else:
                filing_rows = []
            found_filings = {str(row["filing_id"]) for row in filing_rows}
            for filing_id in filing_ids:
                if filing_id not in found_filings:
                    issues.append({"rule_id": "filing_not_found", "message": filing_id})
            for row in filing_rows:
                packet["filings"].append({
                    "filing_id": str(row["filing_id"]),
                    "issuer_name": str(row["issuer_name"] or ""),
                    "corp_code": row["issuer_corp_code"],
                    "stock_code": row["stock_code"],
                    "report_name": str(row["report_name_raw"] or ""),
                    "filed_at": str(row["filed_at"] or ""),
                })
                packet["versions"].append({
                    "filing_id": str(row["filing_id"]),
                    "event_id": str(row["event_id"] or ""),
                    "parent_filing_id": row["parent_filing_id"],
                    "lineage_status": str(row["lineage_status"] or ""),
                    "lineage_confidence": str(row["lineage_confidence"] or ""),
                    "is_current": bool(row["is_current"]),
                    "effective_from": row["effective_from"],
                    "effective_to": row["effective_to"],
                    "rationale": str(row["rationale"] or ""),
                })

            # Candidate filings without selected evidence still need source provenance.
            if filing_ids:
                placeholders = ",".join("?" for _ in filing_ids)
                source_rows = connection.execute(
                    f"""SELECT s.source_id,s.filing_id,s.source_path,s.sha256,s.detected_format,
                               s.parse_status,
                               (SELECT COUNT(*) FROM fragment fr WHERE fr.source_id=s.source_id) AS fragment_count,
                               (SELECT COUNT(*) FROM table_record tr WHERE tr.source_id=s.source_id) AS table_count,
                               (SELECT COUNT(*) FROM table_cell tc WHERE tc.source_id=s.source_id) AS cell_count
                          FROM source_document s
                         WHERE s.filing_id IN ({placeholders})
                         ORDER BY s.filing_id,CASE WHEN s.parse_status='success' THEN 0 ELSE 1 END,s.source_id""",
                    filing_ids,
                ).fetchall()
            else:
                source_rows = []
            selected_source_ids = set(source_ids)
            first_by_filing: dict[str, str] = {}
            for row in source_rows:
                first_by_filing.setdefault(str(row["filing_id"]), str(row["source_id"]))
            admitted_source_ids = selected_source_ids | set(first_by_filing.values())
            for row in source_rows:
                if str(row["source_id"]) not in admitted_source_ids:
                    continue
                packet["sources"].append({
                    "source_id": str(row["source_id"]),
                    "filing_id": str(row["filing_id"]),
                    "source_path": str(row["source_path"] or ""),
                    "sha256": str(row["sha256"] or ""),
                    "detected_format": str(row["detected_format"] or ""),
                    "parse_status": str(row["parse_status"] or ""),
                    "fragment_count": int(row["fragment_count"]),
                    "table_count": int(row["table_count"]),
                    "cell_count": int(row["cell_count"]),
                })
    except (OSError, sqlite3.Error) as exc:
        issues.append({"rule_id": "corpus_read_failed", "message": type(exc).__name__})
    return packet, issues


def build_record(
    *,
    question: str,
    annotator: str,
    annotation: Mapping[str, Any],
    packet: Mapping[str, Any],
    status: str = "candidate",
    reviewer: str | None = None,
    reviewed_at: str | None = None,
    review_notes: str = "",
) -> dict[str, Any]:
    answer = annotation.get("answer")
    if not isinstance(answer, Mapping):
        answer = {}
    period = annotation.get("period")
    if not isinstance(period, Mapping):
        period = {}
    evidence_ids = _unique_strings(annotation.get("evidence_ids"))
    filing_ids = _unique_strings(annotation.get("candidate_filing_ids"))
    roles = annotation.get("evidence_roles") if isinstance(annotation.get("evidence_roles"), Mapping) else {}
    table_validated = bool(annotation.get("table_structure_validated"))
    packet_evidence = {
        str(item.get("evidence_id")): item
        for item in packet.get("evidence", [])
        if isinstance(item, Mapping) and item.get("evidence_id")
    }
    evidence: list[dict[str, Any]] = []
    for evidence_id in evidence_ids:
        item = packet_evidence.get(evidence_id)
        if item is None:
            continue
        kind = str(item.get("kind") or "")
        role = str(roles.get(evidence_id) or "support")
        if role not in EVIDENCE_ROLES:
            role = "support"
        answer_kind = str(answer.get("kind") or "")
        unit = str(answer.get("unit")) if answer_kind == "numeric" and answer.get("unit") is not None else None
        scale = answer.get("scale") if answer_kind == "numeric" else None
        evidence.append({
            "evidence_id": evidence_id,
            "filing_id": str(item.get("filing_id") or ""),
            "source_sha256": str(item.get("source_sha256") or ""),
            "locator": dict(item.get("locator") or {}),
            "role": role,
            "lineage_status": str(item.get("lineage_status") or ""),
            "is_current": bool(item.get("is_current")),
            "effective_from": item.get("effective_from"),
            "effective_to": item.get("effective_to"),
            "source_format": _format_of(item.get("detected_format")),
            "table_structure_status": (
                "human_validated" if kind == "table_cell" and table_validated
                else "parsed_unreviewed" if kind == "table_cell"
                else "not_applicable"
            ),
            "table_id": item.get("table_id"),
            "cell_evidence_id": evidence_id if kind == "table_cell" else None,
            "unit": unit,
            "scale": scale,
        })

    sources = []
    for item in packet.get("sources", []):
        if not isinstance(item, Mapping) or str(item.get("filing_id")) not in filing_ids:
            continue
        detected = str(item.get("detected_format") or "")
        sources.append({
            "source_id": str(item.get("source_id") or ""),
            "filing_id": str(item.get("filing_id") or ""),
            "sha256": str(item.get("sha256") or ""),
            "detected_format": detected,
            "parse_status": str(item.get("parse_status") or ""),
            "fragment_count": int(item.get("fragment_count") or 0),
            "table_count": int(item.get("table_count") or 0),
            "cell_count": int(item.get("cell_count") or 0),
            "table_structure_status": (
                "unvalidated" if detected == "pdf"
                else "parsed_unreviewed" if int(item.get("table_count") or 0) > 0
                else "not_applicable"
            ),
        })
    versions = [
        {key: item.get(key) for key in (
            "filing_id", "event_id", "parent_filing_id", "lineage_status",
            "lineage_confidence", "is_current", "effective_from", "effective_to", "rationale",
        )}
        for item in packet.get("versions", [])
        if isinstance(item, Mapping) and str(item.get("filing_id")) in filing_ids
    ]
    filings = [
        item for item in packet.get("filings", [])
        if isinstance(item, Mapping) and str(item.get("filing_id")) in filing_ids
    ]
    first_filing = filings[0] if filings else None
    company_resolution = None
    if first_filing is not None:
        company_resolution = {
            "query_name": str(first_filing.get("issuer_name") or ""),
            "issuer_name": str(first_filing.get("issuer_name") or ""),
            "corp_code": first_filing.get("corp_code"),
            "stock_code": first_filing.get("stock_code"),
        }
    return {
        "schema_version": "0.1.0",
        "question_id": str(annotation.get("question_id") or ""),
        "question_type": str(annotation.get("question_type") or ""),
        "question": question,
        "answerability": str(annotation.get("answerability") or ""),
        "answer": dict(answer),
        "answer_origin": "human_verified" if status == "approved" else "model_generated",
        "candidate_filing_ids": filing_ids,
        "company_resolution": company_resolution,
        "period": {
            "period_type": period.get("period_type"),
            "start_date": period.get("start_date"),
            "end_date": period.get("end_date"),
            "instant_date": period.get("instant_date"),
        },
        "scope": str(annotation.get("scope") or ""),
        "as_of": annotation.get("as_of") or None,
        "version_basis": str(annotation.get("version_basis") or ""),
        "formula": dict(annotation["formula"]) if isinstance(annotation.get("formula"), Mapping) else None,
        "attack_label": str(annotation.get("attack_label")) if annotation.get("attack_label") else None,
        "evidence": evidence,
        "version_evidence": versions,
        "source_evidence": sources,
        "review": {
            "status": status,
            "annotator": annotator,
            "reviewer": reviewer,
            "reviewed_at": reviewed_at,
            "notes": review_notes or str(annotation.get("notes") or ""),
        },
    }


def automatic_checks(record: Mapping[str, Any], hydration_issues: list[dict[str, str]]) -> dict[str, Any]:
    issues = [dict(item) for item in hydration_issues]
    issues.extend({
        "rule_id": str(item.get("rule_id") or "invalid_gold"),
        "message": str(item.get("message") or ""),
    } for item in validate_record_contract(dict(record)))
    filing_ids = set(_unique_strings(record.get("candidate_filing_ids")))
    evidence_rows = record.get("evidence") if isinstance(record.get("evidence"), list) else []
    evidence_filings = {
        str(item.get("filing_id"))
        for item in evidence_rows
        if isinstance(item, Mapping) and item.get("filing_id")
    }
    if not evidence_filings <= filing_ids:
        issues.append({
            "rule_id": "evidence_outside_candidate_filings",
            "message": ",".join(sorted(evidence_filings - filing_ids)),
        })
    source_rows = record.get("source_evidence") if isinstance(record.get("source_evidence"), list) else []
    for item in source_rows:
        if isinstance(item, Mapping) and str(item.get("parse_status") or "") != "success":
            issues.append({
                "rule_id": "source_parse_not_success",
                "message": f"{item.get('source_id')}:{item.get('parse_status')}",
            })
    if record.get("version_basis") == "latest_effective":
        version_rows = record.get("version_evidence") if isinstance(record.get("version_evidence"), list) else []
        non_current = [
            str(item.get("filing_id"))
            for item in version_rows
            if isinstance(item, Mapping) and not bool(item.get("is_current"))
        ]
        if non_current:
            issues.append({
                "rule_id": "latest_basis_requires_current_filings",
                "message": ",".join(non_current),
            })
    deduplicated: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for item in issues:
        normalized = {
            "rule_id": str(item.get("rule_id") or "invalid_gold"),
            "message": str(item.get("message") or ""),
        }
        key = (normalized["rule_id"], normalized["message"])
        if key not in seen:
            seen.add(key)
            deduplicated.append(normalized)
    issues = deduplicated
    return {
        "passed": not issues,
        "issue_count": len(issues),
        "issues": issues,
        "evidence_expected": len(record.get("evidence", [])) if isinstance(record.get("evidence"), list) else 0,
        "evidence_hydrated": len(record.get("evidence", [])) if isinstance(record.get("evidence"), list) else 0,
    }
