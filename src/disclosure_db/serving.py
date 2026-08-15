from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path


SAFE_LINEAGE_STATUSES = ("root", "resolved")


def version_filter_sql(*, alias: str = "v", as_of: str | None = None) -> tuple[str, list[object]]:
    """Return the mandatory lineage/PIT predicate for answer-producing reads."""
    status = f"{alias}.lineage_status IN ('root','resolved')"
    if as_of is None:
        return f"{status} AND {alias}.is_current=1", []
    return (
        f"{status} AND {alias}.effective_from<=? "
        f"AND ({alias}.effective_to IS NULL OR ? < {alias}.effective_to)",
        [as_of, as_of],
    )


def fetch_validated_facts(
    database: Path,
    *,
    filing_id: str,
    predicate: str | None = None,
    as_of: str | None = None,
    limit: int = 100,
) -> list[dict[str, object]]:
    """Read facts eligible for an answer; candidates and unresolved versions are never returned."""
    if limit <= 0:
        return []
    version_sql, version_params = version_filter_sql(as_of=as_of)
    predicate_sql = " AND f.predicate=?" if predicate is not None else ""
    params: list[object] = [filing_id, *version_params]
    if predicate is not None:
        params.append(predicate)
    params.append(limit)
    with closing(sqlite3.connect(database)) as connection:
        connection.row_factory = sqlite3.Row
        rows = connection.execute(
            f"""SELECT f.fact_id,f.filing_id,f.fact_type,f.subject,f.predicate,f.value_raw,f.unit,
                       f.extraction_method,f.validation_status,v.lineage_status,
                       group_concat(DISTINCT fe.evidence_id) AS evidence_ids,
                       max(CAST(json_extract(s.coverage_json,'$.image_reference_count') AS INTEGER))
                           AS image_reference_count
                FROM fact f
                JOIN filing_version v ON v.filing_id=f.filing_id
                JOIN fact_evidence fe ON fe.fact_id=f.fact_id
                JOIN table_cell c ON c.evidence_id=fe.evidence_id
                JOIN source_document s ON s.source_id=c.source_id
                WHERE f.filing_id=? AND f.validation_status='validated'
                  AND s.parse_status='success' AND {version_sql}{predicate_sql}
                GROUP BY f.fact_id
                ORDER BY f.fact_id
                LIMIT ?""",
            params,
        ).fetchall()
    result: list[dict[str, object]] = []
    for row in rows:
        item = dict(row)
        item["evidence_ids"] = str(item["evidence_ids"]).split(",") if item["evidence_ids"] else []
        image_count = int(item.pop("image_reference_count") or 0)
        item["requires_visual_verification"] = image_count > 0
        item["image_reference_count"] = image_count
        result.append(item)
    return result


def fetch_validated_financial_facts(
    database: Path,
    *,
    filing_id: str,
    account_id: str | None = None,
    as_of: str | None = None,
    limit: int = 100,
) -> list[dict[str, object]]:
    """Read only evidence-backed, validated rows from the separate financial semantic grain."""
    if limit <= 0:
        return []
    version_sql, version_params = version_filter_sql(as_of=as_of)
    account_sql = " AND ff.account_id=?" if account_id is not None else ""
    params: list[object] = [filing_id, *version_params]
    if account_id is not None:
        params.append(account_id)
    params.append(limit)
    with closing(sqlite3.connect(database)) as connection:
        connection.row_factory = sqlite3.Row
        rows = connection.execute(
            f"""SELECT ff.*,v.lineage_status,
                       group_concat(DISTINCT ffe.evidence_id) AS evidence_ids
                FROM financial_fact ff
                JOIN filing_version v ON v.filing_id=ff.filing_id
                JOIN financial_fact_evidence ffe ON ffe.financial_fact_id=ff.financial_fact_id
                JOIN table_cell c ON c.evidence_id=ffe.evidence_id
                JOIN source_document s ON s.source_id=c.source_id
                WHERE ff.filing_id=? AND ff.validation_status='validated'
                  AND s.parse_status='success' AND {version_sql}{account_sql}
                GROUP BY ff.financial_fact_id
                ORDER BY ff.financial_fact_id
                LIMIT ?""",
            params,
        ).fetchall()
    result: list[dict[str, object]] = []
    for row in rows:
        item = dict(row)
        item["evidence_ids"] = str(item["evidence_ids"]).split(",") if item["evidence_ids"] else []
        result.append(item)
    return result
