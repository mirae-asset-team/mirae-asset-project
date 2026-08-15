from __future__ import annotations

import sqlite3
from collections import defaultdict
from dataclasses import dataclass

from .identifiers import normalize_report_name, stable_digest
from .parsers import extract_first_submission_date


@dataclass(slots=True)
class FilingNode:
    filing_id: str
    issuer_corp_code: str
    doc_group: str
    subtype: str | None
    report_name: str
    filed_at: str
    base_year: int | None
    base_month: int | None
    is_correction: bool
    source_texts: list[str]


def _event_key(node: FilingNode) -> str:
    if node.doc_group == "periodic":
        return f"periodic:{node.subtype}:{node.base_year}:{node.base_month}"
    return f"{node.doc_group}:{normalize_report_name(node.report_name)}:{node.filing_id}"


def _insert_event(connection: sqlite3.Connection, node: FilingNode, event_key: str, method: str) -> str:
    event_id = f"evt_{stable_digest(node.issuer_corp_code, node.doc_group, event_key, length=28)}"
    connection.execute(
        "INSERT OR IGNORE INTO filing_event(event_id,issuer_corp_code,doc_group,event_key,lineage_method) VALUES(?,?,?,?,?)",
        (event_id, node.issuer_corp_code, node.doc_group, event_key, method),
    )
    return event_id


def _write_chain(
    connection: sqlite3.Connection,
    nodes: list[FilingNode],
    *,
    event_key: str,
    method: str,
    missing_original: bool = False,
) -> None:
    nodes.sort(key=lambda item: (item.filed_at, item.filing_id))
    event_id = _insert_event(connection, nodes[0], event_key, method)
    for index, node in enumerate(nodes):
        parent = nodes[index - 1].filing_id if index else None
        if index == 0 and node.is_correction and missing_original:
            status, confidence, rationale = "missing_original", "none", "periodic_group_has_correction_only"
        elif index == 0:
            status, confidence, rationale = "root", "high", method
        else:
            status, confidence, rationale = "resolved", "high", method
        effective_to = nodes[index + 1].filed_at if index + 1 < len(nodes) else None
        connection.execute(
            """INSERT INTO filing_version(
                   filing_id,event_id,version_no,parent_filing_id,lineage_status,lineage_confidence,
                   effective_from,effective_to,is_current,rationale
               ) VALUES(?,?,?,?,?,?,?,?,?,?)""",
            (
                node.filing_id,
                event_id,
                index + 1,
                parent,
                status,
                confidence,
                node.filed_at,
                effective_to,
                int(index == len(nodes) - 1),
                rationale,
            ),
        )


def build_lineage(connection: sqlite3.Connection) -> dict[str, int]:
    connection.execute("DELETE FROM filing_version")
    connection.execute("DELETE FROM filing_event")
    rows = connection.execute(
        """SELECT f.filing_id,f.issuer_corp_code,f.doc_group,f.doc_subtype_normalized,
                  f.report_name_raw,f.filed_at,f.base_year,f.base_month,f.is_correction
           FROM filing f ORDER BY f.issuer_corp_code,f.filed_at,f.filing_id"""
    ).fetchall()
    texts: dict[str, list[str]] = defaultdict(list)
    for filing_id, text in connection.execute(
        """SELECT filing_id,text_normalized
           FROM fragment
           WHERE fragment_type IN ('table_row','paragraph','html_text')
           ORDER BY filing_id,source_id,sequence_no,evidence_id"""
    ):
        texts[filing_id].append(text)
    nodes = [
        FilingNode(
            filing_id=row[0],
            issuer_corp_code=row[1],
            doc_group=row[2],
            subtype=row[3],
            report_name=row[4],
            filed_at=row[5],
            base_year=row[6],
            base_month=row[7],
            is_correction=bool(row[8]),
            source_texts=texts[row[0]],
        )
        for row in rows
    ]

    periodic_groups: dict[tuple[object, ...], list[FilingNode]] = defaultdict(list)
    remaining: list[FilingNode] = []
    for node in nodes:
        if node.doc_group == "periodic":
            periodic_groups[(node.issuer_corp_code, node.subtype, node.base_year, node.base_month)].append(node)
        else:
            remaining.append(node)
    for key, group in periodic_groups.items():
        event_key = f"periodic:{key[1]}:{key[2]}:{key[3]}"
        _write_chain(
            connection,
            group,
            event_key=event_key,
            method="issuer+periodic_subtype+base_period",
            missing_original=all(item.is_correction for item in group),
        )

    by_issuer_date_name: dict[tuple[str, str, str, str], list[FilingNode]] = defaultdict(list)
    for node in remaining:
        by_issuer_date_name[(node.issuer_corp_code, node.doc_group, node.filed_at, normalize_report_name(node.report_name))].append(node)

    assigned: set[str] = set()
    for node in remaining:
        if not node.is_correction or node.filing_id in assigned:
            continue
        original_date = extract_first_submission_date(node.source_texts)
        normalized = normalize_report_name(node.report_name)
        candidates = (
            by_issuer_date_name.get((node.issuer_corp_code, node.doc_group, original_date, normalized), [])
            if original_date
            else []
        )
        candidates = [candidate for candidate in candidates if not candidate.is_correction and candidate.filing_id != node.filing_id]
        if len(candidates) == 1:
            original = candidates[0]
            chain = [original, node]
            # Same-day or later corrections explicitly referencing the same original date join the chain.
            for other in remaining:
                if other.filing_id in {item.filing_id for item in chain} or not other.is_correction:
                    continue
                if (
                    other.issuer_corp_code == node.issuer_corp_code
                    and other.doc_group == node.doc_group
                    and normalize_report_name(other.report_name) == normalized
                    and extract_first_submission_date(other.source_texts) == original_date
                ):
                    chain.append(other)
            event_key = f"explicit_original_date:{original.filing_id}"
            _write_chain(connection, chain, event_key=event_key, method="correction_header_original_date")
            assigned.update(item.filing_id for item in chain)

    # Ambiguous event disclosures remain isolated. This is deliberate: a false correction edge can
    # make an obsolete value look current, which is worse than an unresolved warning.
    for node in remaining:
        if node.filing_id in assigned:
            continue
        event_key = _event_key(node)
        event_id = _insert_event(connection, node, event_key, "isolated_unresolved_event")
        status = "unresolved" if node.is_correction else "root"
        confidence = "none" if node.is_correction else "high"
        connection.execute(
            """INSERT INTO filing_version(
                   filing_id,event_id,version_no,parent_filing_id,lineage_status,lineage_confidence,
                   effective_from,effective_to,is_current,rationale
               ) VALUES(?,?,?,?,?,?,?,?,?,?)""",
            (
                node.filing_id,
                event_id,
                1,
                None,
                status,
                confidence,
                node.filed_at,
                None,
                1,
                "correction_without_unique_explicit_original" if node.is_correction else "standalone_event_root",
            ),
        )

    stats = dict(
        connection.execute(
            "SELECT lineage_status, count(*) FROM filing_version GROUP BY lineage_status"
        ).fetchall()
    )
    stats["events"] = connection.execute("SELECT count(*) FROM filing_event").fetchone()[0]
    return {str(key): int(value) for key, value in stats.items()}
