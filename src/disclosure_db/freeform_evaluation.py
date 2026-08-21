"""Canonical free-form Gold construction and exact retrieval evaluation."""

from __future__ import annotations

from collections import Counter, defaultdict
from copy import deepcopy
import hashlib
import json
import math
from pathlib import Path
import sqlite3
from typing import Iterable, Mapping, Sequence


_FORBIDDEN_MANIFEST_KEYS = ("question", "answer", "excerpt", "credential", "secret", "api_key")


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def canonical_sha256(value: object) -> str:
    """Return the SHA-256 of strict canonical JSON."""

    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _string_list(value: object, field: str) -> list[str]:
    if not isinstance(value, (list, tuple)) or not value or any(not isinstance(item, str) or not item for item in value):
        raise ValueError(f"{field}_invalid")
    return sorted(set(value))


def _admit_source_record(record: Mapping[str, object], contract: Mapping[str, object]) -> None:
    if record.get("lineage_status") in {"unresolved", "missing_original", None}:
        raise ValueError("unresolved_lineage")
    if not record.get("target_evidence_ids"):
        raise ValueError("missing_evidence")
    if not record.get("filing_ids"):
        raise ValueError("missing_filing")
    if not record.get("issuer_name") or not record.get("issuer_corp_code"):
        raise ValueError("ambiguous_issuer")
    allowed_parse = set(contract.get("allowed_parse_statuses", ()))
    if allowed_parse and record.get("parse_status") not in allowed_parse:
        raise ValueError("unsafe_source_parse")
    source_sha256 = record.get("source_sha256")
    if not isinstance(source_sha256, str) or len(source_sha256) != 64:
        raise ValueError("invalid_source_hash")


def _split_name(group_id: str, percentages: Mapping[str, object]) -> str:
    normalized = [(str(name), int(weight)) for name, weight in percentages.items() if int(weight) > 0]
    if not normalized:
        raise ValueError("split_percentages_empty")
    total = sum(weight for _, weight in normalized)
    bucket = int(hashlib.sha256(group_id.encode("utf-8")).hexdigest()[:16], 16) % total
    boundary = 0
    for name, weight in normalized:
        boundary += weight
        if bucket < boundary:
            return name
    raise AssertionError("unreachable split bucket")


def assign_group_splits(
    cases: Iterable[Mapping[str, object]],
    split_percentages: Mapping[str, object],
) -> list[dict[str, object]]:
    """Group connected filing/evidence/source cases before deterministic splitting."""

    rows = [deepcopy(dict(case)) for case in cases]
    parents = list(range(len(rows)))

    def find(index: int) -> int:
        while parents[index] != index:
            parents[index] = parents[parents[index]]
            index = parents[index]
        return index

    def union(left: int, right: int) -> None:
        left_root, right_root = find(left), find(right)
        if left_root != right_root:
            parents[max(left_root, right_root)] = min(left_root, right_root)

    owners: dict[str, int] = {}
    for index, row in enumerate(rows):
        keys = [f"filing:{item}" for item in row.get("filing_ids", ())]
        keys += [f"evidence:{item}" for item in row.get("target_evidence_ids", ())]
        if row.get("source_sha256"):
            keys.append(f"source:{row['source_sha256']}")
        for key in keys:
            if key in owners:
                union(index, owners[key])
            else:
                owners[key] = index

    components: dict[int, list[int]] = defaultdict(list)
    for index in range(len(rows)):
        components[find(index)].append(index)
    for members in components.values():
        case_ids = sorted(str(rows[index].get("case_id", "")) for index in members)
        group_id = f"group-{canonical_sha256(case_ids)[:20]}"
        split = _split_name(group_id, split_percentages)
        for index in members:
            rows[index]["group_id"] = group_id
            rows[index]["split"] = split
    return sorted(rows, key=lambda row: str(row.get("case_id", "")))


def build_freeform_gold(
    records: Iterable[Mapping[str, object]],
    contract: Mapping[str, object],
    templates: Mapping[str, object] | Sequence[Mapping[str, object]],
) -> list[dict[str, object]]:
    """Build deterministic agent-audited cases from admitted evidence records."""

    dimensions = set(_string_list(contract.get("dimensions"), "dimensions"))
    allowed_routes = set(_string_list(contract.get("allowed_routes"), "allowed_routes"))
    allowed_policies = set(_string_list(contract.get("allowed_correction_policies"), "allowed_correction_policies"))
    template_rows = templates.get("templates") if isinstance(templates, Mapping) else templates
    if not isinstance(template_rows, (list, tuple)) or not template_rows:
        raise ValueError("templates_invalid")

    normalized_templates: list[dict[str, object]] = []
    for template in template_rows:
        if not isinstance(template, Mapping):
            raise ValueError("template_invalid")
        item = dict(template)
        if item.get("dimension_id") not in dimensions:
            raise ValueError("template_dimension_invalid")
        if item.get("route") not in allowed_routes:
            raise ValueError("template_route_invalid")
        if not all(isinstance(item.get(key), str) and item.get(key) for key in ("template_id", "template")):
            raise ValueError("template_invalid")
        normalized_templates.append(item)
    normalized_templates.sort(key=lambda item: str(item["template_id"]))

    rows: list[dict[str, object]] = []
    source_records = sorted((dict(record) for record in records), key=canonical_sha256)
    for record in source_records:
        _admit_source_record(record, contract)
        supported = set(record.get("supported_dimensions", dimensions))
        filing_ids = _string_list(record["filing_ids"], "filing_ids")
        evidence_ids = _string_list(record["target_evidence_ids"], "target_evidence_ids")
        policy = str(record.get("correction_policy", "current"))
        if policy not in allowed_policies:
            raise ValueError("correction_policy_invalid")
        for template in normalized_templates:
            dimension = str(template["dimension_id"])
            if dimension not in supported:
                continue
            route_overrides = record.get("routes") if isinstance(record.get("routes"), Mapping) else {}
            route = str(route_overrides.get(dimension, template["route"]))
            if route not in allowed_routes:
                raise ValueError("route_invalid")
            template_id = str(template["template_id"])
            identity = {
                "source_record_id": str(record.get("source_record_id", record["source_sha256"])),
                "dimension_id": dimension,
                "template_id": template_id,
                "filing_ids": filing_ids,
                "target_evidence_ids": evidence_ids,
            }
            question = str(template["template"]).format(
                issuer_name=record["issuer_name"],
                dimension_label=dimension,
                variant_label=template_id,
                filing_count=len(filing_ids),
            )
            rows.append({
                "schema_version": str(contract.get("schema_version", "1.0.0")),
                "case_id": f"freeform-{canonical_sha256(identity)[:24]}",
                "dimension_id": dimension,
                "question": question,
                "issuer_name": str(record["issuer_name"]),
                "issuer_corp_code": str(record["issuer_corp_code"]),
                "filing_ids": filing_ids,
                "target_evidence_ids": evidence_ids,
                "correction_policy": policy,
                "route": route,
                "source_sha256": str(record["source_sha256"]),
                "source_record_id": str(record.get("source_record_id", record["source_sha256"])),
                "paraphrase_template_id": template_id,
                "review": {"status": "agent_audited"},
            })

    rows = assign_group_splits(rows, contract.get("split_percentages", {"test": 100}))
    validate_freeform_gold(rows, contract)
    return rows


def derive_freeform_source_records(
    audited_gold: Iterable[Mapping[str, object]],
    database: Path,
    contract: Mapping[str, object],
    *,
    connection: sqlite3.Connection | None = None,
) -> list[dict[str, object]]:
    """Select deterministic, current, safely parsed corpus evidence for audited issuers."""

    issuers: dict[str, str] = {}
    for row in audited_gold:
        review = row.get("review") if isinstance(row.get("review"), Mapping) else {}
        audit = row.get("audit") if isinstance(row.get("audit"), Mapping) else {}
        if review.get("status") not in {"approved", "agent_audited"}:
            continue
        if audit and audit.get("state") not in {"human_verified", "agent_audited"}:
            continue
        resolution = row.get("company_resolution")
        if not isinstance(resolution, Mapping):
            continue
        corp_code = resolution.get("corp_code")
        issuer_name = resolution.get("issuer_name")
        if isinstance(corp_code, str) and corp_code and isinstance(issuer_name, str) and issuer_name:
            issuers[corp_code] = issuer_name
    if not issuers:
        raise ValueError("no_audited_issuers")

    dimensions = _string_list(contract.get("dimensions"), "dimensions")
    candidate_terms = contract.get("candidate_terms")
    routes = contract.get("dimension_routes")
    if not isinstance(candidate_terms, Mapping) or not isinstance(routes, Mapping):
        raise ValueError("candidate_catalog_invalid")
    allowed_parse = _string_list(contract.get("allowed_parse_statuses"), "allowed_parse_statuses")
    per_dimension = int(contract.get("source_records_per_dimension", 3))
    if per_dimension <= 0:
        raise ValueError("source_records_per_dimension_invalid")

    owns_connection = connection is None
    if connection is None:
        uri = f"{database.resolve().as_uri()}?mode=ro&immutable=1"
        connection = sqlite3.connect(uri, uri=True)
    connection.row_factory = sqlite3.Row
    try:
        issuer_marks = ",".join("?" for _ in issuers)
        parse_marks = ",".join("?" for _ in allowed_parse)
        output: list[dict[str, object]] = []
        for dimension in dimensions:
            terms = _string_list(candidate_terms.get(dimension), f"candidate_terms:{dimension}")
            term_clause = " OR ".join("instr(fr.text_normalized, ?) > 0" for _ in terms)
            sql = f"""
                SELECT fr.evidence_id, fi.filing_id, fi.issuer_corp_code, fi.issuer_name,
                       fi.filed_at, sd.sha256 AS source_sha256, sd.parse_status,
                       fv.lineage_status
                FROM filing AS fi
                JOIN filing_version AS fv ON fv.filing_id = fi.filing_id
                JOIN fragment AS fr ON fr.filing_id = fi.filing_id
                JOIN source_document AS sd
                  ON sd.source_id = fr.source_id AND sd.filing_id = fi.filing_id
                WHERE fi.issuer_corp_code IN ({issuer_marks})
                  AND fv.lineage_status IN ('root', 'resolved')
                  AND fv.is_current = 1
                  AND sd.parse_status IN ({parse_marks})
                  AND ({term_clause})
                ORDER BY fi.issuer_corp_code, fi.filed_at DESC, fi.filing_id,
                         fr.sequence_no, fr.evidence_id
            """
            parameters = [*sorted(issuers), *allowed_parse, *terms]
            candidates = connection.execute(sql, parameters)
            selected: list[sqlite3.Row] = []
            seen_filings: set[str] = set()
            for candidate in candidates:
                text_row = connection.execute(
                    "SELECT text_normalized FROM fragment WHERE evidence_id = ?",
                    (candidate["evidence_id"],),
                ).fetchone()
                text = str(text_row[0] if text_row else "").casefold()
                if any(marker in text for marker in (
                    "ignore previous", "ignore all instructions", "system prompt",
                    "이전 지시를 무시", "지시를 무시", "시스템 프롬프트",
                )):
                    continue
                if candidate["filing_id"] in seen_filings:
                    continue
                selected.append(candidate)
                seen_filings.add(str(candidate["filing_id"]))
                if len(selected) == per_dimension:
                    break
            if len(selected) < per_dimension:
                raise ValueError(
                    f"dimension_source_coverage_missing:{dimension}:{len(selected)}<{per_dimension}"
                )
            route = routes.get(dimension)
            if not isinstance(route, str) or not route:
                raise ValueError(f"dimension_route_missing:{dimension}")
            for candidate in selected:
                evidence_id = str(candidate["evidence_id"])
                output.append({
                    "source_record_id": f"{dimension}:{evidence_id}",
                    "issuer_name": str(candidate["issuer_name"] or issuers[str(candidate["issuer_corp_code"])]),
                    "issuer_corp_code": str(candidate["issuer_corp_code"]),
                    "filing_ids": [str(candidate["filing_id"])],
                    "target_evidence_ids": [evidence_id],
                    "correction_policy": "current",
                    "source_sha256": str(candidate["source_sha256"]),
                    "lineage_status": str(candidate["lineage_status"]),
                    "parse_status": str(candidate["parse_status"]),
                    "supported_dimensions": [dimension],
                    "routes": {dimension: route},
                    "as_of": str(candidate["filed_at"])[:10],
                })
        return sorted(output, key=canonical_sha256)
    finally:
        if owns_connection:
            connection.close()


def validate_freeform_gold(rows: Sequence[Mapping[str, object]], contract: Mapping[str, object]) -> None:
    """Fail closed when canonical Gold violates its schema or provenance boundary."""

    minimum = int(contract.get("minimum_cases", 0))
    if len(rows) < minimum:
        raise ValueError(f"minimum_cases_not_met:{len(rows)}<{minimum}")
    allowed_dimensions = set(contract.get("dimensions", ()))
    allowed_routes = set(contract.get("allowed_routes", ()))
    case_ids: set[str] = set()
    dimensions: set[str] = set()
    required = {
        "case_id", "dimension_id", "question", "issuer_name", "issuer_corp_code",
        "filing_ids", "target_evidence_ids", "correction_policy", "route",
        "source_sha256", "group_id", "split", "paraphrase_template_id", "review",
    }
    for row in rows:
        missing = required - set(row)
        if missing:
            raise ValueError(f"missing_fields:{','.join(sorted(missing))}")
        status = row.get("review", {}).get("status") if isinstance(row.get("review"), Mapping) else None
        if status != "agent_audited":
            raise ValueError(f"human_verified_forbidden:{status}")
        case_id = str(row["case_id"])
        if case_id in case_ids:
            raise ValueError(f"duplicate_case_id:{case_id}")
        case_ids.add(case_id)
        dimension = str(row["dimension_id"])
        dimensions.add(dimension)
        if dimension not in allowed_dimensions:
            raise ValueError(f"dimension_invalid:{dimension}")
        if row["route"] not in allowed_routes:
            raise ValueError(f"route_invalid:{row['route']}")
        _string_list(row["filing_ids"], "filing_ids")
        _string_list(row["target_evidence_ids"], "target_evidence_ids")
        if len(str(row["source_sha256"])) != 64:
            raise ValueError("invalid_source_hash")
    if rows and dimensions != allowed_dimensions:
        missing_dimensions = sorted(allowed_dimensions - dimensions)
        raise ValueError(f"dimension_coverage_missing:{','.join(missing_dimensions)}")


def _manifest_is_safe(value: object) -> bool:
    if isinstance(value, Mapping):
        for key, item in value.items():
            lowered = str(key).lower()
            if any(forbidden in lowered for forbidden in _FORBIDDEN_MANIFEST_KEYS):
                return False
            if not _manifest_is_safe(item):
                return False
    elif isinstance(value, (list, tuple)):
        return all(_manifest_is_safe(item) for item in value)
    return True


def build_freeform_manifest(
    rows: Sequence[Mapping[str, object]],
    *,
    source_gold_sha256: str,
    contract_sha256: str,
    templates_sha256: str,
    database_sha256: str,
) -> dict[str, object]:
    """Build a content-free manifest for a canonical Gold artifact."""

    manifest: dict[str, object] = {
        "schema_version": "1.0.0",
        "artifact": "freeform_gold.agent_audited.jsonl",
        "review_status": "agent_audited",
        "case_count": len(rows),
        "target_count": sum(len(row.get("target_evidence_ids", ())) for row in rows),
        "dimension_counts": dict(sorted(Counter(str(row["dimension_id"]) for row in rows).items())),
        "route_counts": dict(sorted(Counter(str(row["route"]) for row in rows).items())),
        "split_counts": dict(sorted(Counter(str(row["split"]) for row in rows).items())),
        "source_gold_sha256": source_gold_sha256,
        "contract_sha256": contract_sha256,
        "templates_sha256": templates_sha256,
        "database_sha256": database_sha256,
        "content_sha256": canonical_sha256(rows),
    }
    if not _manifest_is_safe(manifest):
        raise ValueError("manifest_content_leak")
    return manifest


def _hit_value(hit: object, field: str, default: object = None) -> object:
    if isinstance(hit, Mapping):
        return hit.get(field, default)
    return getattr(hit, field, default)


def score_freeform_case(
    case: Mapping[str, object],
    selected: Iterable[object],
    *,
    slot_complete: bool = False,
    query_count: int = 0,
    candidate_count: int = 0,
    latency_ms: float = 0,
) -> dict[str, object]:
    """Score one retrieval using exact evidence identifiers."""

    target_ids = _string_list(case.get("target_evidence_ids"), "target_evidence_ids")
    _string_list(case.get("filing_ids"), "filing_ids")
    issuer = str(case.get("issuer_corp_code", ""))
    correction_policy = str(case.get("correction_policy", "current"))
    unique_hits: list[object] = []
    seen: set[str] = set()
    for hit in selected:
        evidence_id = str(_hit_value(hit, "evidence_id", ""))
        if evidence_id and evidence_id not in seen:
            seen.add(evidence_id)
            unique_hits.append(hit)
    selected_ids = [str(_hit_value(hit, "evidence_id")) for hit in unique_hits[:20]]
    wrong_issuer = 0
    wrong_version = 0
    for hit in unique_hits[:20]:
        hit_issuer = _hit_value(hit, "issuer_corp_code", _hit_value(hit, "corp_code"))
        if hit_issuer is not None and str(hit_issuer) != issuer:
            wrong_issuer += 1
            continue
        is_current = _hit_value(hit, "is_current")
        policy_mismatch = correction_policy == "current" and is_current is False
        policy_mismatch = policy_mismatch or (correction_policy == "original" and is_current is True)
        if policy_mismatch:
            wrong_version += 1
    target_set = set(target_ids)
    hits_at_5 = len(target_set.intersection(selected_ids[:5]))
    hits_at_20 = len(target_set.intersection(selected_ids[:20]))
    first_rank = next((rank for rank, evidence_id in enumerate(selected_ids, 1) if evidence_id in target_set), None)
    missing = sorted(target_set - set(selected_ids[:20]))
    return {
        "case_id": str(case.get("case_id", "")),
        "route": str(case.get("route", "")),
        "target_count": len(target_ids),
        "target_hits_at_5": hits_at_5,
        "target_hits_at_20": hits_at_20,
        "recall_at_5": hits_at_5 / len(target_ids),
        "recall_at_20": hits_at_20 / len(target_ids),
        "mrr": 0.0 if first_rank is None else 1.0 / first_rank,
        "slot_complete": bool(slot_complete),
        "wrong_issuer_count": wrong_issuer,
        "wrong_version_count": wrong_version,
        "hard_failure": bool(wrong_issuer or wrong_version),
        "query_count": int(query_count),
        "candidate_count": int(candidate_count),
        "latency_ms": float(latency_ms),
        "missing_target_evidence_ids": missing,
        "selected_evidence_ids": selected_ids,
        "exclusion_boundary": "not_retrieved_at_20" if missing else None,
    }


def _percentile(values: Sequence[float], percentile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    position = (len(ordered) - 1) * percentile
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def aggregate_freeform_scores(scores: Iterable[Mapping[str, object]]) -> dict[str, object]:
    """Aggregate exact target metrics and content-free residual diagnostics."""

    rows = [dict(score) for score in scores]
    target_count = sum(int(row.get("target_count", 0)) for row in rows)
    hits_at_5 = sum(int(row.get("target_hits_at_5", 0)) for row in rows)
    hits_at_20 = sum(int(row.get("target_hits_at_20", 0)) for row in rows)
    latencies = [float(row.get("latency_ms", 0)) for row in rows]
    residuals = []
    for row in rows:
        missing = list(row.get("missing_target_evidence_ids", ()))
        if missing:
            residual = {
                "case_id": str(row.get("case_id", "")),
                "route": str(row.get("route", "")),
                "target_evidence_ids": sorted(str(item) for item in missing),
                "selected_evidence_ids": list(row.get("selected_evidence_ids", ()))[:20],
                "exclusion_boundary": str(row.get("exclusion_boundary") or "not_retrieved_at_20"),
            }
            if row.get("hypothesis"):
                residual["hypothesis"] = str(row["hypothesis"])
            residuals.append(residual)
    result = {
        "case_count": len(rows),
        "target_count": target_count,
        "target_hits_at_5": hits_at_5,
        "target_hits_at_20": hits_at_20,
        "target_recall_at_5": hits_at_5 / target_count if target_count else 0.0,
        "target_recall_at_20": hits_at_20 / target_count if target_count else 0.0,
        "mrr": sum(float(row.get("mrr", 0)) for row in rows) / len(rows) if rows else 0.0,
        "slot_completeness": sum(bool(row.get("slot_complete")) for row in rows) / len(rows) if rows else 0.0,
        "wrong_issuer_count": sum(int(row.get("wrong_issuer_count", 0)) for row in rows),
        "wrong_version_count": sum(int(row.get("wrong_version_count", 0)) for row in rows),
        "hard_failure_count": sum(bool(row.get("hard_failure")) for row in rows),
        "query_count": sum(int(row.get("query_count", 0)) for row in rows),
        "candidate_count": sum(int(row.get("candidate_count", 0)) for row in rows),
        "latency_ms": {"p50": _percentile(latencies, 0.50), "p95": _percentile(latencies, 0.95)},
        "residual_misses": sorted(residuals, key=lambda row: str(row["case_id"])),
    }
    return result


_MEASUREMENT_KEYS = {"generated_at", "measured_at", "latency_ms", "latency_samples_ms"}


def _without_measurements(value: object) -> object:
    if isinstance(value, Mapping):
        return {
            str(key): _without_measurements(item)
            for key, item in value.items()
            if str(key) not in _MEASUREMENT_KEYS and str(key) != "semantic_sha256"
        }
    if isinstance(value, (list, tuple)):
        return [_without_measurements(item) for item in value]
    return value


def semantic_summary_sha256(summary: Mapping[str, object]) -> str:
    """Hash evaluation semantics while excluding timestamps and latency samples."""

    return canonical_sha256(_without_measurements(summary))


def decide_embedding_pilot(
    summary: Mapping[str, object],
    *,
    minimum_recall: float = 0.95,
    minimum_gain: float = 0.05,
) -> dict[str, object]:
    """Apply the measured residual-text gate without invoking a model."""

    target_count = int(summary.get("target_count", 0))
    sparse_recall = float(summary.get("target_recall_at_20", 0))
    if target_count <= 0:
        raise ValueError("target_count_must_be_positive")
    base = {
        "minimum_recall": minimum_recall,
        "minimum_gain": minimum_gain,
        "sparse_recall_at_20": sparse_recall,
        "target_count": target_count,
    }
    safety_failures = int(summary.get("wrong_issuer_count", 0)) + int(summary.get("wrong_version_count", 0))
    if safety_failures:
        return {**base, "eligible": False, "status": "BLOCKED_SAFETY", "reason": "sparse_safety_failure"}
    if sparse_recall >= minimum_recall:
        return {**base, "eligible": False, "status": "DEFERRED_NO_EVIDENCE", "reason": "sparse_recall_gate_met"}

    text_targets: set[tuple[str, str]] = set()
    for miss in summary.get("residual_misses", ()):  # type: ignore[union-attr]
        if isinstance(miss, Mapping) and miss.get("route") == "text":
            case_id = str(miss.get("case_id", ""))
            for evidence_id in miss.get("target_evidence_ids", ()):  # type: ignore[union-attr]
                text_targets.add((case_id, str(evidence_id)))
    if not text_targets:
        return {**base, "eligible": False, "status": "DEFERRED_NO_EVIDENCE", "reason": "no_residual_text_misses"}
    maximum_gain = len(text_targets) / target_count
    if maximum_gain + 1e-12 < minimum_gain:
        return {
            **base,
            "eligible": False,
            "status": "DEFERRED_NO_EVIDENCE",
            "reason": "insufficient_maximum_possible_gain",
            "residual_text_target_count": len(text_targets),
            "maximum_possible_gain": maximum_gain,
        }

    dense = summary.get("dense_pilot")
    if isinstance(dense, Mapping):
        dense_recall = float(dense.get("target_recall_at_20", 0))
        measured_gain = dense_recall - sparse_recall
        dense_safety = int(dense.get("wrong_issuer_count", 0)) + int(dense.get("wrong_version_count", 0))
        p95 = float(dense.get("latency_ms", {}).get("p95", 0)) if isinstance(dense.get("latency_ms"), Mapping) else math.inf
        adopted = measured_gain + 1e-12 >= minimum_gain and dense_safety == 0 and p95 <= 2000
        return {
            **base,
            "eligible": True,
            "status": "ADOPTED" if adopted else "REJECTED_PILOT",
            "reason": "dense_gate_passed" if adopted else "dense_gate_not_met",
            "residual_text_target_count": len(text_targets),
            "maximum_possible_gain": maximum_gain,
            "dense_recall_at_20": dense_recall,
            "measured_gain": measured_gain,
            "dense_wrong_issuer_or_version_count": dense_safety,
            "dense_p95_ms": p95,
        }
    return {
        **base,
        "eligible": True,
        "status": "ELIGIBLE_PILOT",
        "reason": "residual_text_gain_gate_met",
        "residual_text_target_count": len(text_targets),
        "maximum_possible_gain": maximum_gain,
    }
