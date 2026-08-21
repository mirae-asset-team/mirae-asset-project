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

from .analysis_planner import plan_analysis


_FORBIDDEN_MANIFEST_KEYS = ("question", "answer", "excerpt", "credential", "secret", "api_key")
_PROMPT_INJECTION_MARKERS = (
    "ignore previous", "ignore all instructions", "system prompt",
    "이전 지시를 무시", "지시를 무시", "시스템 프롬프트",
)


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


def text_target_is_answer_safe(
    fragment_type: str,
    text: str,
    *,
    markers: Sequence[str],
    minimum_characters: int = 80,
) -> bool:
    """Accept substantive indexed text, never headings, labels, or unsafe instructions."""

    normalized = " ".join(str(text).split())
    lowered = normalized.casefold()
    return bool(
        fragment_type in {"paragraph", "table_row", "html_text", "pdf_block"}
        and len(normalized) >= minimum_characters
        and any(marker in normalized for marker in markers)
        and not any(marker in lowered for marker in _PROMPT_INJECTION_MARKERS)
    )


def validate_case_plan(case: Mapping[str, object], plan: object) -> tuple[tuple[str, str], ...]:
    """Require a generated case to execute the declared judgment slot contract."""

    if getattr(plan, "analysis_mode", None) != "judgment":
        raise ValueError("analysis_plan_not_judgment")
    if getattr(plan, "judgment_dimension", None) != case.get("dimension_id"):
        raise ValueError("judgment_dimension_mismatch")
    raw_expected = case.get("expected_slots")
    if not isinstance(raw_expected, (list, tuple)):
        raise ValueError("expected_slots_invalid")
    expected = tuple(
        (str(slot.get("slot_id", "")), str(slot.get("domain", "")))
        for slot in raw_expected
        if isinstance(slot, Mapping)
    )
    actual = tuple(
        (str(slot.slot_id), str(slot.domain))
        for slot in getattr(plan, "required_evidence_slots", ())
    )
    if not expected or expected != actual:
        raise ValueError("slot_contract_mismatch")
    expected_policy = case.get("correction_policy")
    base_plan = getattr(plan, "base_plan", None)
    if expected_policy is not None and getattr(base_plan, "correction_policy", None) != expected_policy:
        raise ValueError("correction_policy_mismatch")
    return actual


def evaluation_exclusion_reason(
    case: Mapping[str, object],
    plan: object,
    *,
    query_count: int,
    serving_evidence_ids: set[str],
) -> str | None:
    """Return a pre-denominator exclusion reason for invalid executions."""

    try:
        validate_case_plan(case, plan)
    except ValueError:
        return "invalid_plan"
    if query_count <= 0:
        return "no_query_executed"
    targets = set(_string_list(case.get("target_evidence_ids"), "target_evidence_ids"))
    if not targets.issubset(serving_evidence_ids):
        return "target_not_serving_admitted"
    return None


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
    answerability = record.get("answerability")
    if not isinstance(answerability, Mapping) or answerability.get("status") != "proved":
        raise ValueError("answerability_not_proved")
    raw_slots = record.get("slot_targets")
    if not isinstance(raw_slots, (list, tuple)) or not raw_slots:
        raise ValueError("slot_targets_invalid")
    path_by_domain = {
        "text": "search_document",
        "financial": "financial_fact_evidence",
        "event": "event_fact_evidence",
    }
    admitted_evidence: set[str] = set()
    admitted_filings: set[str] = set()
    for raw_slot in raw_slots:
        if not isinstance(raw_slot, Mapping):
            raise ValueError("slot_target_invalid")
        domain = str(raw_slot.get("domain", ""))
        if raw_slot.get("serving_path") != path_by_domain.get(domain):
            raise ValueError("serving_path_invalid")
        admitted_evidence.update(_string_list(raw_slot.get("target_evidence_ids"), "slot_target_evidence_ids"))
        admitted_filings.update(_string_list(raw_slot.get("filing_ids"), "slot_target_filing_ids"))
        if domain in {"financial", "event"}:
            _string_list(raw_slot.get("fact_ids"), "slot_fact_ids")
        elif domain == "text":
            features = raw_slot.get("content_features")
            if (
                not isinstance(features, Mapping)
                or features.get("fragment_type") == "heading"
                or int(features.get("character_count", 0)) < 80
                or int(features.get("marker_count", 0)) < 1
            ):
                raise ValueError("text_target_not_answer_safe")
    if admitted_evidence != set(_string_list(record.get("target_evidence_ids"), "target_evidence_ids")):
        raise ValueError("slot_target_evidence_mismatch")
    if not admitted_filings.issubset(set(_string_list(record.get("filing_ids"), "filing_ids"))):
        raise ValueError("slot_target_filing_mismatch")
    if "correction_materiality" in set(record.get("supported_dimensions", ())):
        pair = record.get("version_pair")
        if not isinstance(pair, Mapping):
            raise ValueError("correction_pair_required")
        original_filing = str(pair.get("original_filing_id", ""))
        current_filing = str(pair.get("current_filing_id", ""))
        original_evidence = str(pair.get("original_evidence_id", ""))
        current_evidence = str(pair.get("current_evidence_id", ""))
        if (
            not pair.get("event_id")
            or not original_filing
            or original_filing == current_filing
            or {original_filing, current_filing} - admitted_filings
            or {original_evidence, current_evidence} - admitted_evidence
            or pair.get("original_is_current") is not False
            or pair.get("current_is_current") is not True
        ):
            raise ValueError("correction_pair_invalid")


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
            raw_expected = record.get("expected_slots", record["slot_targets"])
            if not isinstance(raw_expected, (list, tuple)):
                raise ValueError("expected_slots_invalid")
            expected_slots = [
                {"slot_id": str(slot.get("slot_id", "")), "domain": str(slot.get("domain", ""))}
                for slot in raw_expected
                if isinstance(slot, Mapping)
            ]
            domains = {slot["domain"] for slot in expected_slots}
            route = next(iter(domains)) if len(domains) == 1 else "mixed"
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
            case = {
                "schema_version": str(contract.get("schema_version", "1.0.0")),
                "case_id": f"freeform-{canonical_sha256(identity)[:24]}",
                "dimension_id": dimension,
                "question": question,
                "issuer_name": str(record["issuer_name"]),
                "issuer_corp_code": str(record["issuer_corp_code"]),
                "filing_ids": filing_ids,
                "target_evidence_ids": evidence_ids,
                "target_domains": {
                    str(evidence_id): str(slot.get("domain", ""))
                    for slot in record["slot_targets"]
                    if isinstance(slot, Mapping)
                    for evidence_id in slot.get("target_evidence_ids", ())
                },
                "correction_policy": policy,
                "route": route,
                "expected_slots": expected_slots,
                "source_sha256": str(record["source_sha256"]),
                "source_record_id": str(record.get("source_record_id", record["source_sha256"])),
                "paraphrase_template_id": template_id,
                "review": {"status": "agent_audited"},
            }
            validate_case_plan(
                case,
                plan_analysis(
                    question,
                    company_candidates=[str(record["issuer_name"])],
                    as_of=str(record["as_of"]) if record.get("as_of") else None,
                ),
            )
            rows.append(case)

    rows = assign_group_splits(rows, contract.get("split_percentages", {"test": 100}))
    validate_freeform_gold(rows, contract)
    return rows


def derive_freeform_source_records(
    audited_gold: Iterable[Mapping[str, object]],
    database: Path,
    contract: Mapping[str, object],
    *,
    overlay_database: Path | None = None,
    search_index: Path | None = None,
    attestation: object | None = None,
) -> list[dict[str, object]]:
    """Derive only targets returned through trusted serving identities."""

    if overlay_database is None or search_index is None or attestation is None:
        raise ValueError("serving_databases_required")

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

    from .evidence_service import EvidenceService

    dimensions = _string_list(contract.get("dimensions"), "dimensions")
    seed_questions = contract.get("dimension_seed_questions")
    text_markers = contract.get("text_markers")
    if not isinstance(seed_questions, Mapping) or not isinstance(text_markers, Mapping):
        raise ValueError("serving_admission_catalog_invalid")
    per_dimension = int(contract.get("source_records_per_dimension", 3))
    if per_dimension <= 0:
        raise ValueError("source_records_per_dimension_invalid")
    per_dimension_overrides = contract.get("source_records_by_dimension", {})
    if not isinstance(per_dimension_overrides, Mapping):
        raise ValueError("source_records_by_dimension_invalid")

    base = sqlite3.connect(f"{Path(database).resolve().as_uri()}?mode=ro&immutable=1", uri=True)
    overlay = sqlite3.connect(f"{Path(overlay_database).resolve().as_uri()}?mode=ro&immutable=1", uri=True)
    search = sqlite3.connect(f"{Path(search_index).resolve().as_uri()}?mode=ro&immutable=1", uri=True)
    base.row_factory = sqlite3.Row
    overlay.row_factory = sqlite3.Row
    search.row_factory = sqlite3.Row
    service = EvidenceService(
        Path(database), Path(overlay_database), attestation=attestation, search_database=Path(search_index),
    )
    try:
        authoritative_names = {
            corp_code: str(row[0])
            for corp_code in sorted(issuers)
            if (row := base.execute(
                "SELECT issuer_name FROM filing WHERE issuer_corp_code=? AND issuer_name<>'' "
                "ORDER BY filed_at DESC,filing_id DESC LIMIT 1",
                (corp_code,),
            ).fetchone()) is not None
        }
        financial_links: dict[str, list[dict[str, object]]] = defaultdict(list)
        for row in overlay.execute(
            "SELECT ff.*,ffe.evidence_id FROM financial_fact ff "
            "JOIN financial_fact_evidence ffe ON ffe.financial_fact_id=ff.financial_fact_id "
            "WHERE ff.validation_status='validated' AND ff.trust_tier='agent_audited' "
            "ORDER BY ff.financial_fact_id,ffe.evidence_id"
        ):
            financial_links[str(row["evidence_id"])].append(dict(row))
        event_links: dict[str, list[dict[str, object]]] = defaultdict(list)
        for row in overlay.execute(
            "SELECT ef.*,efe.evidence_id FROM event_fact ef "
            "JOIN event_fact_evidence efe ON efe.event_fact_id=ef.event_fact_id "
            "WHERE ef.trust_tier='agent_audited' ORDER BY ef.event_fact_id,efe.evidence_id"
        ):
            event_links[str(row["evidence_id"])].append(dict(row))

        def search_metadata(evidence_id: str) -> dict[str, object] | None:
            row = search.execute(
                "SELECT evidence_id,filing_id,source_id,corp_code,is_current,lineage_status,fragment_type,text_normalized "
                "FROM search_document WHERE evidence_id=?",
                (evidence_id,),
            ).fetchone()
            return dict(row) if row is not None else None

        def source_metadata(evidence_id: str) -> dict[str, object] | None:
            row = base.execute(
                "SELECT sd.sha256,sd.parse_status FROM source_document sd JOIN ("
                "SELECT source_id FROM fragment WHERE evidence_id=? UNION ALL "
                "SELECT source_id FROM table_cell WHERE evidence_id=?"
                ") e ON e.source_id=sd.source_id LIMIT 1",
                (evidence_id, evidence_id),
            ).fetchone()
            return dict(row) if row is not None else None

        def filing_version(filing_id: str) -> dict[str, object] | None:
            row = base.execute(
                "SELECT event_id,lineage_status,is_current FROM filing_version WHERE filing_id=?",
                (filing_id,),
            ).fetchone()
            return dict(row) if row is not None else None

        output: list[dict[str, object]] = []
        for dimension in dimensions:
            required_records = int(per_dimension_overrides.get(dimension, per_dimension))
            if required_records <= 0:
                raise ValueError(f"source_records_per_dimension_invalid:{dimension}")
            question_template = seed_questions.get(dimension)
            if not isinstance(question_template, str) or not question_template:
                raise ValueError(f"dimension_seed_question_missing:{dimension}")
            selected_for_dimension: list[dict[str, object]] = []
            for corp_code in sorted(authoritative_names):
                issuer_name = authoritative_names[corp_code]
                question = question_template.format(issuer_name=issuer_name)
                plan = plan_analysis(question, company_candidates=[issuer_name])
                if plan.analysis_mode != "judgment" or plan.judgment_dimension != dimension:
                    continue
                result = service.search_analysis(plan, limit=20)
                expected_slots = [
                    {"slot_id": slot.slot_id, "domain": slot.domain}
                    for slot in plan.required_evidence_slots
                ]
                facts_by_evidence: dict[str, list[dict[str, object]]] = defaultdict(list)
                for fact in (*result.financial_facts, *result.event_facts):
                    for evidence_id in fact.get("evidence_ids", ()):  # type: ignore[union-attr]
                        facts_by_evidence[str(evidence_id)].append(dict(fact))
                candidates_by_slot: dict[str, list[dict[str, object]]] = defaultdict(list)
                planned_slots = {slot.slot_id: slot for slot in plan.required_evidence_slots}
                for slot in result.slots:
                    planned_slot = planned_slots[slot.slot_id]
                    markers = tuple(str(item) for item in text_markers.get(slot.slot_id, ()))
                    for ref in slot.evidence:
                        evidence_id = str(ref.evidence_id)
                        filing_id = str(ref.filing_id)
                        if slot.slot_id == "financing_disclosures":
                            facts = [
                                fact for fact in event_links.get(evidence_id, ())
                                if fact.get("predicate_id") in {"issued_shares", "treasury_disposal_shares"}
                            ]
                            if not facts:
                                continue
                            candidates_by_slot[slot.slot_id].append({
                                "evidence_id": evidence_id, "filing_id": filing_id,
                                "fact_ids": sorted(str(fact["event_fact_id"]) for fact in facts),
                            })
                        elif planned_slot.domain == "financial":
                            facts = financial_links.get(evidence_id, ())
                            if not facts:
                                continue
                            candidates_by_slot[slot.slot_id].append({
                                "evidence_id": evidence_id, "filing_id": filing_id,
                                "fact_ids": sorted(str(fact["financial_fact_id"]) for fact in facts),
                                "period_count": len({
                                    (fact.get("period_start"), fact.get("period_end"), fact.get("instant_date"))
                                    for fact in facts
                                }),
                            })
                        elif planned_slot.domain == "event":
                            facts = event_links.get(evidence_id, ())
                            if not facts:
                                continue
                            candidates_by_slot[slot.slot_id].append({
                                "evidence_id": evidence_id, "filing_id": filing_id,
                                "fact_ids": sorted(str(fact["event_fact_id"]) for fact in facts),
                            })
                        else:
                            metadata = search_metadata(evidence_id)
                            if (
                                metadata is None
                                or str(metadata["corp_code"]) != corp_code
                                or not text_target_is_answer_safe(
                                    str(metadata["fragment_type"]), str(metadata["text_normalized"]),
                                    markers=markers, minimum_characters=int(contract.get("minimum_text_characters", 80)),
                                )
                            ):
                                continue
                            candidates_by_slot[slot.slot_id].append({
                                "evidence_id": evidence_id, "filing_id": filing_id,
                                "is_current": bool(metadata["is_current"]),
                                "lineage_status": str(metadata["lineage_status"]),
                                "content_features": {
                                    "fragment_type": str(metadata["fragment_type"]),
                                    "character_count": len(" ".join(str(metadata["text_normalized"]).split())),
                                    "marker_count": sum(marker in str(metadata["text_normalized"]) for marker in markers),
                                },
                            })

                chosen: dict[str, list[dict[str, object]]] = {}
                version_pair: dict[str, object] | None = None
                if dimension == "profitability_financial_health":
                    for slot_id in ("income_trend", "balance_sheet"):
                        eligible = [item for item in candidates_by_slot[slot_id] if int(item.get("period_count", 0)) >= 2]
                        if eligible:
                            chosen[slot_id] = eligible[:1]
                elif dimension == "contract_change":
                    eligible = candidates_by_slot["contract_current"]
                    filing_order: list[str] = []
                    selected_items: list[dict[str, object]] = []
                    for item in eligible:
                        if str(item["filing_id"]) not in filing_order:
                            filing_order.append(str(item["filing_id"]))
                            selected_items.append(item)
                        if len(filing_order) == 2:
                            break
                    if len(filing_order) == 2:
                        chosen["contract_current"] = selected_items
                elif dimension == "correction_materiality":
                    for original in candidates_by_slot["original_disclosure"]:
                        original_version = filing_version(str(original["filing_id"]))
                        if not original_version or original.get("is_current") is not False:
                            continue
                        for current in candidates_by_slot["effective_correction"]:
                            current_version = filing_version(str(current["filing_id"]))
                            if (
                                current_version
                                and current.get("is_current") is True
                                and original_version["event_id"] == current_version["event_id"]
                                and original["filing_id"] != current["filing_id"]
                            ):
                                chosen = {
                                    "original_disclosure": [original],
                                    "effective_correction": [current],
                                }
                                version_pair = {
                                    "event_id": str(original_version["event_id"]),
                                    "original_filing_id": str(original["filing_id"]),
                                    "current_filing_id": str(current["filing_id"]),
                                    "original_evidence_id": str(original["evidence_id"]),
                                    "current_evidence_id": str(current["evidence_id"]),
                                    "original_is_current": False,
                                    "current_is_current": True,
                                }
                                break
                        if version_pair:
                            break
                else:
                    for slot in plan.required_evidence_slots:
                        if candidates_by_slot[slot.slot_id]:
                            chosen[slot.slot_id] = candidates_by_slot[slot.slot_id][:1]

                mandatory_slots = {slot.slot_id for slot in plan.required_evidence_slots if slot.mandatory}
                if not mandatory_slots.issubset(chosen):
                    continue
                slot_targets: list[dict[str, object]] = []
                target_ids: list[str] = []
                filing_ids: list[str] = []
                for slot in plan.required_evidence_slots:
                    items = chosen.get(slot.slot_id, [])
                    if not items:
                        continue
                    evidence_ids = sorted({str(item["evidence_id"]) for item in items})
                    slot_filings = sorted({str(item["filing_id"]) for item in items})
                    target: dict[str, object] = {
                        "slot_id": slot.slot_id,
                        "domain": slot.domain,
                        "serving_path": {
                            "text": "search_document",
                            "financial": "financial_fact_evidence",
                            "event": "event_fact_evidence",
                        }[slot.domain],
                        "target_evidence_ids": evidence_ids,
                        "filing_ids": slot_filings,
                    }
                    if slot.domain == "text":
                        target["content_features"] = items[0]["content_features"]
                    else:
                        target["fact_ids"] = sorted({
                            str(fact_id) for item in items for fact_id in item.get("fact_ids", ())
                        })
                    slot_targets.append(target)
                    target_ids.extend(evidence_ids)
                    filing_ids.extend(slot_filings)
                source_rows = [source_metadata(evidence_id) for evidence_id in sorted(set(target_ids))]
                if any(row is None or row.get("parse_status") != "success" for row in source_rows):
                    continue
                source_hashes = sorted({str(row["sha256"]) for row in source_rows if row is not None})
                record: dict[str, object] = {
                    "source_record_id": f"{dimension}:{corp_code}:{canonical_sha256(sorted(set(target_ids)))[:16]}",
                    "issuer_name": issuer_name,
                    "issuer_corp_code": corp_code,
                    "filing_ids": sorted(set(filing_ids)),
                    "target_evidence_ids": sorted(set(target_ids)),
                    "correction_policy": str(plan.base_plan.correction_policy),
                    "source_sha256": canonical_sha256(source_hashes),
                    "source_sha256s": source_hashes,
                    "lineage_status": "resolved" if version_pair else "root",
                    "parse_status": "success",
                    "supported_dimensions": [dimension],
                    "expected_slots": expected_slots,
                    "slot_targets": slot_targets,
                    "answerability": {
                        "status": "proved",
                        "requirement_count": len(mandatory_slots),
                        "target_count": len(set(target_ids)),
                        "filing_count": len(set(filing_ids)),
                    },
                }
                if version_pair is not None:
                    record["version_pair"] = version_pair
                selected_for_dimension.append(record)
                if len(selected_for_dimension) == required_records:
                    break
            if len(selected_for_dimension) < required_records:
                raise ValueError(
                    f"dimension_source_coverage_missing:{dimension}:{len(selected_for_dimension)}<{required_records}"
                )
            output.extend(selected_for_dimension)
        return sorted(output, key=canonical_sha256)
    finally:
        base.close()
        overlay.close()
        search.close()


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
        "target_domains", "expected_slots", "source_sha256", "group_id", "split",
        "paraphrase_template_id", "review",
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
        target_domains = row.get("target_domains")
        if (
            not isinstance(target_domains, Mapping)
            or set(str(key) for key in target_domains) != set(row["target_evidence_ids"])
            or any(value not in {"text", "financial", "event"} for value in target_domains.values())
        ):
            raise ValueError("target_domains_invalid")
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
        if dense.get("scope") != "overall" or int(dense.get("target_count", 0)) != target_count:
            raise ValueError("dense_denominator_mismatch")
        sparse_hits = int(dense.get("sparse_target_hits_at_20", -1))
        dense_hits = int(dense.get("dense_target_hits_at_20", -1))
        if sparse_hits < 0 or dense_hits < 0:
            raise ValueError("dense_hit_counts_invalid")
        if "target_hits_at_20" in summary and sparse_hits != int(summary["target_hits_at_20"]):
            raise ValueError("dense_sparse_hits_mismatch")
        dense_sparse_recall = sparse_hits / target_count
        dense_recall = dense_hits / target_count
        if (
            abs(dense_sparse_recall - sparse_recall) > 1e-12
            or abs(float(dense.get("sparse_recall_at_20", -1)) - dense_sparse_recall) > 1e-12
            or abs(float(dense.get("dense_recall_at_20", -1)) - dense_recall) > 1e-12
        ):
            raise ValueError("dense_recall_schema_mismatch")
        measured_gain = dense_recall - dense_sparse_recall
        if abs(float(dense.get("measured_gain", math.inf)) - measured_gain) > 1e-12:
            raise ValueError("dense_gain_schema_mismatch")
        dense_safety = int(dense.get("wrong_issuer_count", 0)) + int(dense.get("wrong_version_count", 0))
        p95 = float(dense.get("p95_ms", math.inf))
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
