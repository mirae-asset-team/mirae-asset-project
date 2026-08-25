"""Pure-Python, allow-listed disclosure Tool implementations.

The handlers in this module return evidence and facts only.  They intentionally
contain no LLM, arbitrary SQL, arbitrary function, or caller-supplied path
execution surface.
"""

from __future__ import annotations

from contextlib import closing
from dataclasses import asdict, dataclass, is_dataclass
import json
from pathlib import Path
import sqlite3
from typing import Mapping, Protocol

from .agent_contracts import EvidenceBundle as ServiceEvidenceBundle
from .evidence_service import EvidenceService
from .financial_accounts import FinancialAccountResolution, resolve_financial_account
from .hybrid_retrieval import HybridRetriever, RetrievalResult
from .query_planner import plan_query
from .tool_contracts import COMMON_OUTPUT_SCHEMA, ToolEvidenceBundle, ToolResponse
from .tool_registry import ToolDefinition, ToolRegistry, object_schema


TOOL_BACKEND_VERSION = "tool-registry-v1"
CORRECTION_POLICIES = ("current", "original", "corrected", "both")


class HybridSearch(Protocol):
    def search(self, question: str, **kwargs: object) -> RetrievalResult: ...


def _optional_string(*, maximum: int = 200) -> dict[str, object]:
    return {"type": "string", "minLength": 1, "maxLength": maximum}


def _date() -> dict[str, object]:
    return {"type": "string", "format": "date"}


def _top_k(maximum: int) -> dict[str, object]:
    return {"type": "integer", "minimum": 1, "maximum": maximum}


def _correction_policy() -> dict[str, object]:
    return {"type": "string", "enum": list(CORRECTION_POLICIES)}


def _tool_output_schema(
    data_properties: Mapping[str, object], *, required: tuple[str, ...]
) -> dict[str, object]:
    common_properties = COMMON_OUTPUT_SCHEMA["properties"]
    if not isinstance(common_properties, Mapping):
        raise ValueError("common_tool_output_schema_invalid")
    return {
        **COMMON_OUTPUT_SCHEMA,
        "properties": {
            **common_properties,
            "data": object_schema(data_properties, required=required),
        },
    }


def _metadata(**extra: object) -> dict[str, object]:
    return {"schema_version": "tool-response-v1", "backend_version": TOOL_BACKEND_VERSION, **extra}


def _json_object(value: object) -> object:
    if isinstance(value, Mapping):
        return dict(value)
    if not isinstance(value, str) or not value:
        return value
    try:
        decoded = json.loads(value)
    except json.JSONDecodeError:
        return value
    return decoded


def _row_evidence_ids(row: Mapping[str, object]) -> list[str]:
    values: list[object] = []
    if row.get("evidence_id"):
        values.append(row["evidence_id"])
    raw = row.get("evidence_ids")
    if isinstance(raw, list):
        values.extend(raw)
    elif isinstance(raw, tuple):
        values.extend(raw)
    elif isinstance(raw, str):
        values.extend(item for item in raw.split(",") if item)
    return list(dict.fromkeys(str(item) for item in values if item))


def _retrieval_path(row: Mapping[str, object]) -> str | None:
    sources = row.get("retrieval_sources")
    if isinstance(sources, list) and sources:
        return "+".join(str(item) for item in sources)
    explicit = row.get("retrieval_source") or row.get("matched_index")
    if explicit:
        return str(explicit)
    return None


def _quality_status(row: Mapping[str, object]) -> str | None:
    if row.get("quality_status"):
        return str(row["quality_status"])
    path = _retrieval_path(row)
    if path and ("sparse" in path or row.get("matched_index")):
        return "safe_search_admitted"
    return None


def _evidence_item(row: Mapping[str, object]) -> dict[str, object]:
    evidence_ids = _row_evidence_ids(row)
    locator = _json_object(row.get("locator") or row.get("locator_json"))
    section = _json_object(row.get("section") or row.get("section_path") or row.get("section_path_json"))
    if section in (None, "") and isinstance(locator, Mapping):
        section = locator.get("section") or locator.get("section_path")
    return {
        "evidence_id": evidence_ids[0] if evidence_ids else None,
        "evidence_ids": evidence_ids,
        "chunk_id": row.get("chunk_id"),
        "filing_id": row.get("filing_id"),
        # DART filing_id is the receipt number in the serving corpus. Keep the
        # provider-facing name explicit so HCX never has to infer or reformat it.
        "rcept_no": row.get("rcept_no") or row.get("filing_id"),
        "report_name": row.get("report_name") or row.get("report_name_raw"),
        "source_id": row.get("source_id"),
        "company_identifiers": {
            "company": row.get("company"),
            "issuer_name": row.get("issuer_name"),
            "listed_name": row.get("listed_name"),
            "reporter_name": row.get("reporter_name"),
            "stock_code": row.get("stock_code"),
            "issuer_corp_code": row.get("issuer_corp_code") or row.get("corp_code"),
        },
        "filed_at": row.get("filed_at"),
        "period": row.get("period"),
        "section": section,
        "table_id": row.get("table_id") or (locator.get("table_id") if isinstance(locator, Mapping) else None),
        "locator": locator,
        "source_path": row.get("source_path"),
        "text": row.get("text_normalized") or row.get("text") or row.get("excerpt"),
        "structured_value": row.get("structured_value") or row.get("value_numeric"),
        "unit": row.get("unit") or row.get("currency") or row.get("unit_raw"),
        "quality_status": _quality_status(row),
        "retrieval_path": _retrieval_path(row),
        "score": row.get("rrf_score") if row.get("rrf_score") is not None else row.get("score"),
        "correction_status": row.get("lineage_status"),
        "is_current": row.get("is_current"),
        "is_correction": row.get("is_correction"),
    }


def _actual_companies(rows: list[Mapping[str, object]]) -> list[str]:
    values: list[str] = []
    for row in rows:
        for key in ("company", "issuer_name", "listed_name", "reporter_name", "stock_code", "issuer_corp_code", "corp_code"):
            if row.get(key):
                values.append(str(row[key]))
    return sorted(set(values))


def _retrieval_bundle(
    intent: str,
    request: Mapping[str, object],
    rows: list[Mapping[str, object]],
    result: RetrievalResult,
) -> ToolEvidenceBundle:
    dates = sorted(str(row["filed_at"]) for row in rows if row.get("filed_at"))
    companies = _actual_companies(rows)
    covered_scope: dict[str, object] = {
        "company": companies,
        "filing_id": sorted({str(row["filing_id"]) for row in rows if row.get("filing_id")}),
        "start_date": dates[0] if dates else None,
        "end_date": dates[-1] if dates else None,
    }
    bundle = ToolEvidenceBundle(
        question_intent=intent,
        requested_scope=dict(request),
        covered_scope=covered_scope,
        items=[_evidence_item(row) for row in rows],
        evidence_ids=[evidence_id for row in rows for evidence_id in _row_evidence_ids(row)],
        filing_ids=[str(row["filing_id"]) for row in rows if row.get("filing_id")],
        issuer_corp_codes=[
            str(row.get("issuer_corp_code") or row.get("corp_code"))
            for row in rows if row.get("issuer_corp_code") or row.get("corp_code")
        ],
        correction_status=_retrieval_correction_status(rows),
        retrieval_status={
            "dense_status": result.dense_status,
            "dense_corpus_size": result.dense_corpus_size,
            "fallback_used": result.fallback_used,
            "retrieval_mode": result.retrieval_mode,
            "smoke_only": result.smoke_only,
        },
    )
    if result.smoke_only:
        bundle.quality_warnings.append("dense_smoke_only")
    if any(item["quality_status"] is None for item in bundle.items):
        bundle.quality_warnings.append("evidence_quality_unknown")
    if bundle.correction_status == "unknown":
        bundle.quality_warnings.append("correction_status_unknown")
    return bundle


def _retrieval_correction_status(rows: list[Mapping[str, object]]) -> str:
    statuses = {str(row["lineage_status"]) for row in rows if row.get("lineage_status")}
    if statuses and statuses <= {"root", "resolved"}:
        return "policy_applied"
    if statuses:
        return "uncertain"
    return "unknown"


def _read_only_connection(path: Path) -> sqlite3.Connection:
    resolved = path.resolve()
    if not resolved.is_file():
        raise FileNotFoundError(resolved)
    connection = sqlite3.connect(f"file:{resolved.as_posix()}?mode=ro&immutable=1", uri=True)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only=ON")
    return connection


def _period_for_fact(fact: Mapping[str, object]) -> dict[str, object]:
    return {
        "period_type": fact.get("period_type"),
        "period_start": fact.get("period_start"),
        "period_end": fact.get("period_end"),
        "instant_date": fact.get("instant_date"),
    }


def _fact_matches_request(fact: Mapping[str, object], request: Mapping[str, object], account_id: str) -> bool:
    if fact.get("account_id") != account_id or fact.get("validation_status") != "validated":
        return False
    company = str(request["company"])
    actual_company_values = {
        str(fact[key]) for key in ("issuer_name", "listed_name", "reporter_name", "stock_code", "issuer_corp_code")
        if fact.get(key)
    }
    if not actual_company_values or company not in actual_company_values:
        return False
    if request.get("scope") and fact.get("scope") != request["scope"]:
        return False
    start_date = request.get("start_date")
    end_date = request.get("end_date")
    if fact.get("period_type") == "instant":
        instant = fact.get("instant_date")
        if start_date and (not instant or str(instant) < str(start_date)):
            return False
        if end_date and (not instant or str(instant) > str(end_date)):
            return False
    else:
        if start_date and fact.get("period_start") != start_date:
            return False
        if end_date and fact.get("period_end") != end_date:
            return False
    return bool(fact.get("evidence_ids"))


def _service_ref_item(ref: object) -> dict[str, object]:
    row = asdict(ref) if is_dataclass(ref) else dict(ref) if isinstance(ref, Mapping) else {}
    row["quality_status"] = "validated_structured_evidence"
    row["retrieval_source"] = "financial_fact_evidence"
    return _evidence_item(row)


@dataclass(slots=True)
class DisclosureToolBackend:
    hybrid_retriever: HybridSearch
    evidence_service: EvidenceService | None = None
    base_database: Path | None = None

    def search_disclosures(self, request: Mapping[str, object]) -> ToolResponse:
        result = self.hybrid_retriever.search(
            str(request["question"]),
            company=request.get("company"), filing_id=request.get("filing_id"),
            start_date=request.get("start_date"), end_date=request.get("end_date"),
            correction_policy=str(request.get("correction_policy") or "current"),
            limit=int(request.get("top_k") or 10),
        )
        rows = [dict(row) for row in result.hits]
        bundle = _retrieval_bundle("search_disclosures", request, rows, result)
        warnings = list(bundle.quality_warnings)
        return ToolResponse(
            "success" if rows else "insufficient", "search_disclosures",
            {
                "results": bundle.items,
                "dense_status": result.dense_status,
                "dense_corpus_size": result.dense_corpus_size,
                "fallback_used": result.fallback_used,
                "retrieval_mode": result.retrieval_mode,
            },
            bundle, warnings, _metadata(result_count=len(rows)),
        )

    def get_financial_facts(self, request: Mapping[str, object]) -> ToolResponse:
        resolution = resolve_financial_account(str(request["account"]))
        if resolution.status != "resolved" or resolution.support_level != "structured" or not resolution.canonical_id:
            warning = _financial_resolution_warning(resolution)
            bundle = ToolEvidenceBundle(
                question_intent="get_financial_facts", requested_scope=dict(request),
                covered_scope={}, quality_warnings=[warning], correction_status="not_evaluated",
            )
            return ToolResponse(
                "insufficient", "get_financial_facts",
                {"account_resolution": resolution.to_dict(), "facts": []},
                bundle, [warning], _metadata(),
            )
        if self.evidence_service is None:
            raise RuntimeError("financial_evidence_service_unavailable")
        question = f"{request['company']} {request['account']}"
        plan = plan_query(question, company_hint=str(request["company"]))
        plan.period_start = str(request["start_date"]) if request.get("start_date") else None
        plan.period_end = str(request["end_date"]) if request.get("end_date") else None
        plan.scope = str(request["scope"]) if request.get("scope") else None
        plan.correction_policy = str(request.get("correction_policy") or "current")
        plan.latest_period_count = int(request.get("top_k") or 10)
        service_bundle: ServiceEvidenceBundle = self.evidence_service.search(
            plan, limit=int(request.get("top_k") or 10)
        )
        facts: list[dict[str, object]] = []
        for raw_fact in service_bundle.financial_facts:
            fact = dict(raw_fact)
            if not _fact_matches_request(fact, request, resolution.canonical_id):
                continue
            facts.append({
                "financial_fact_id": fact.get("financial_fact_id"),
                "filing_id": fact.get("filing_id"),
                "rcept_no": fact.get("filing_id"),
                "account_id": resolution.canonical_id,
                "account_name": resolution.label_ko,
                "value_numeric": fact.get("value_numeric"),
                "scale": fact.get("scale"),
                "unit": fact.get("currency") or fact.get("unit_raw"),
                "period": _period_for_fact(fact),
                "scope": fact.get("scope"),
                "company_identifiers": {
                    "company": request["company"],
                    "issuer_name": fact.get("issuer_name"),
                    "listed_name": fact.get("listed_name"),
                    "reporter_name": fact.get("reporter_name"),
                    "stock_code": fact.get("stock_code"),
                    "issuer_corp_code": fact.get("issuer_corp_code"),
                },
                "support_level": "structured",
                "validation_status": fact.get("validation_status"),
                "evidence_ids": list(fact.get("evidence_ids") or []),
            })
        allowed_ids = {str(item) for fact in facts for item in fact["evidence_ids"]}  # type: ignore[union-attr]
        refs = [ref for ref in service_bundle.evidence if ref.evidence_id in allowed_ids]
        actual_dates = sorted(
            str(value) for fact in facts for value in (
                fact["period"].get("period_start"), fact["period"].get("period_end"), fact["period"].get("instant_date")  # type: ignore[union-attr]
            ) if value
        )
        issuer_codes = sorted({
            str(raw.get("issuer_corp_code")) for raw in service_bundle.financial_facts
            if raw.get("issuer_corp_code") and any(fact.get("filing_id") == raw.get("filing_id") for fact in facts)
        })
        evidence_items: list[dict[str, object]] = []
        for ref in refs:
            item = _service_ref_item(ref)
            related = next((fact for fact in facts if ref.evidence_id in fact["evidence_ids"]), None)  # type: ignore[operator]
            if related is not None:
                item["company_identifiers"] = related["company_identifiers"]
                item["period"] = related["period"]
                item["structured_value"] = related["value_numeric"]
                item["unit"] = related["unit"]
            evidence_items.append(item)
        bundle = ToolEvidenceBundle(
            question_intent="get_financial_facts", requested_scope=dict(request),
            covered_scope={
                "company": [str(request["company"])] if facts else [],
                "account": [str(request["account"])] if facts else [],
                "start_date": actual_dates[0] if actual_dates else None,
                "end_date": actual_dates[-1] if actual_dates else None,
                "scope": sorted({str(fact["scope"]) for fact in facts if fact.get("scope")}),
            },
            items=evidence_items,
            evidence_ids=sorted(allowed_ids),
            filing_ids=[str(fact["filing_id"]) for fact in facts if fact.get("filing_id")],
            issuer_corp_codes=issuer_codes,
            correction_status="policy_applied" if facts else "not_evaluated",
            retrieval_status={"route": "evidence_service_financial_fact"},
        )
        warnings: list[str] = []
        if not facts:
            warnings.append("financial_fact_grain_mismatch_or_missing")
        return ToolResponse(
            "success" if facts else "insufficient", "get_financial_facts",
            {"account_resolution": resolution.to_dict(), "facts": facts},
            bundle, warnings, _metadata(result_count=len(facts)),
        )

    def analyze_disclosure_trend(self, request: Mapping[str, object]) -> ToolResponse:
        database = self._database()
        where, params = _filing_filters(request, include_dates=True)
        period_expression = {
            "day": "substr(filed_at,1,10)",
            "month": "substr(filed_at,1,7)",
            "week": "date(substr(filed_at,1,10), '-' || ((CAST(strftime('%w',substr(filed_at,1,10)) AS INTEGER)+6)%7) || ' days')",
        }[str(request["granularity"])]
        with closing(_read_only_connection(database)) as connection:
            count_by_period = [dict(row) for row in connection.execute(
                f"SELECT {period_expression} period,COUNT(*) count FROM filing WHERE {where} GROUP BY period ORDER BY period",
                params,
            )]
            count_by_type = [dict(row) for row in connection.execute(
                f"SELECT doc_group filing_type,COUNT(*) count FROM filing WHERE {where} GROUP BY doc_group ORDER BY doc_group",
                params,
            )]
            representative_limit = int(request.get("representative_limit") or 10)
            representatives = [dict(row) for row in connection.execute(
                f"""SELECT f.filing_id,f.issuer_corp_code,f.issuer_name,f.listed_name,f.reporter_name,f.stock_code,
                           f.doc_group,f.doc_subtype_normalized,f.report_name_raw,f.filed_at,f.is_correction,
                           (SELECT fr.evidence_id FROM fragment fr WHERE fr.filing_id=f.filing_id
                             ORDER BY fr.sequence_no,fr.evidence_id LIMIT 1) evidence_id,
                           (SELECT fr.locator_json FROM fragment fr WHERE fr.filing_id=f.filing_id
                             ORDER BY fr.sequence_no,fr.evidence_id LIMIT 1) locator_json
                      FROM filing f WHERE {where}
                     ORDER BY filed_at DESC,filing_id LIMIT ?""",
                [*params, representative_limit],
            )]
            bounds_where, bounds_params = _filing_filters(request, include_dates=False)
            bounds = connection.execute(
                f"SELECT MIN(filed_at) min_date,MAX(filed_at) max_date FROM filing WHERE {bounds_where}",
                bounds_params,
            ).fetchone()
        total_count = sum(int(row["count"]) for row in count_by_period)
        available_start = str(bounds["min_date"]) if bounds and bounds["min_date"] else None
        available_end = str(bounds["max_date"]) if bounds and bounds["max_date"] else None
        coverage_complete = bool(
            available_start and available_end
            and available_start <= str(request["start_date"])
            and available_end >= str(request["end_date"])
        )
        rows: list[Mapping[str, object]] = representatives
        bundle = ToolEvidenceBundle(
            question_intent="analyze_disclosure_trend", requested_scope=dict(request),
            covered_scope={
                "company": _actual_companies(rows),
                "start_date": str(request["start_date"]) if total_count else None,
                "end_date": str(request["end_date"]) if total_count else None,
                "available_start_date": available_start,
                "available_end_date": available_end,
            },
            items=[{**_evidence_item(row), "quality_status": "filing_metadata"} for row in rows],
            evidence_ids=[evidence_id for row in rows for evidence_id in _row_evidence_ids(row)],
            filing_ids=[str(row["filing_id"]) for row in rows],
            issuer_corp_codes=[str(row["issuer_corp_code"]) for row in rows if row.get("issuer_corp_code")],
            correction_status="filing_metadata_only",
            retrieval_status={"route": "read_only_filing_aggregation"},
        )
        warnings = [] if coverage_complete else ["requested_period_not_fully_covered_by_available_filings"]
        return ToolResponse(
            "success" if total_count else "insufficient", "analyze_disclosure_trend",
            {
                "count_by_period": count_by_period,
                "count_by_type": count_by_type,
                "representative_filings": representatives,
                "total_count": total_count,
                "requested_range": {"start_date": request["start_date"], "end_date": request["end_date"]},
                "actual_aggregate_range": {
                    "start_date": count_by_period[0]["period"] if count_by_period else None,
                    "end_date": count_by_period[-1]["period"] if count_by_period else None,
                },
                "coverage_complete": coverage_complete,
            },
            bundle, warnings, _metadata(),
        )

    def get_correction_lineage(self, request: Mapping[str, object]) -> ToolResponse:
        with closing(_read_only_connection(self._database())) as connection:
            rows = [dict(row) for row in connection.execute(
                """SELECT v.event_id,v.version_no,v.parent_filing_id,v.lineage_status,
                          v.lineage_confidence,v.effective_from,v.effective_to,v.is_current,v.rationale,
                          f.filing_id,f.issuer_corp_code,f.issuer_name,f.listed_name,f.reporter_name,
                          f.stock_code,f.report_name_raw,f.filed_at,f.is_correction,
                          (SELECT fr.evidence_id FROM fragment fr WHERE fr.filing_id=f.filing_id
                            ORDER BY fr.sequence_no,fr.evidence_id LIMIT 1) evidence_id,
                          (SELECT fr.locator_json FROM fragment fr WHERE fr.filing_id=f.filing_id
                            ORDER BY fr.sequence_no,fr.evidence_id LIMIT 1) locator_json
                     FROM filing_version v JOIN filing f ON f.filing_id=v.filing_id
                    WHERE v.event_id=(SELECT event_id FROM filing_version WHERE filing_id=?)
                    ORDER BY v.version_no,v.filing_id""",
                (request["filing_id"],),
            )]
        originals = [row for row in rows if row["lineage_status"] == "root"]
        currents = [row for row in rows if bool(row["is_current"])]
        original = originals[0] if len(originals) == 1 else None
        current = currents[0] if len(currents) == 1 else None
        corrections = [row for row in rows if bool(row["is_correction"]) or int(row["version_no"]) > 1]
        confidence = _lowest_confidence(rows)
        resolved = bool(original and current and confidence == "high" and all(
            row["lineage_status"] in {"root", "resolved"} for row in rows
        ))
        connection_status = "resolved" if resolved else "uncertain" if rows else "not_found"
        items = [{**_evidence_item(row), "quality_status": "lineage_metadata"} for row in rows]
        bundle = ToolEvidenceBundle(
            question_intent="get_correction_lineage", requested_scope=dict(request),
            covered_scope={"filing_id": [str(row["filing_id"]) for row in rows]},
            items=items,
            evidence_ids=[evidence_id for row in rows for evidence_id in _row_evidence_ids(row)],
            filing_ids=[str(row["filing_id"]) for row in rows],
            issuer_corp_codes=[str(row["issuer_corp_code"]) for row in rows if row.get("issuer_corp_code")],
            correction_status=connection_status,
            retrieval_status={"route": "existing_filing_version_lineage"},
        )
        warnings = [] if resolved else ["correction_lineage_uncertain"]
        return ToolResponse(
            "success" if rows else "insufficient", "get_correction_lineage",
            {
                "original_filing": original,
                "corrected_filings": corrections,
                "current_filing": current,
                "connection_status": connection_status,
                "lineage_confidence": confidence,
            },
            bundle, warnings, _metadata(version_count=len(rows)),
        )

    def build_summary_context(self, request: Mapping[str, object]) -> ToolResponse:
        result = self.hybrid_retriever.search(
            str(request["question"]),
            company=request.get("company"), filing_id=request.get("filing_id"),
            start_date=request.get("start_date"), end_date=request.get("end_date"),
            correction_policy=str(request.get("correction_policy") or "current"),
            limit=int(request.get("top_k") or 10),
        )
        rows = [dict(row) for row in result.hits]
        maximum = int(request.get("max_chars") or 6000)
        context_parts: list[str] = []
        remaining = maximum
        for row in rows:
            body = str(row.get("text_normalized") or row.get("text") or row.get("excerpt") or "").strip()
            if not body or remaining <= 0:
                continue
            identity = row.get("evidence_id") or row.get("chunk_id") or "unidentified"
            prefix = f"[{row.get('filing_id') or 'unknown'}|{identity}] "
            separator = "\n\n" if context_parts else ""
            part = separator + prefix + body
            if len(part) > remaining:
                part = part[:remaining]
            context_parts.append(part)
            remaining -= len(part)
        context = "".join(context_parts)
        bundle = _retrieval_bundle("build_summary_context", request, rows, result)
        warnings = list(bundle.quality_warnings)
        if not context:
            warnings.append("summary_context_text_unavailable")
            bundle.quality_warnings.append("summary_context_text_unavailable")
        return ToolResponse(
            "success" if context else "insufficient", "build_summary_context",
            {
                "context": context,
                "context_chars": len(context),
                "result_count": len(rows),
                "dense_status": result.dense_status,
                "dense_corpus_size": result.dense_corpus_size,
                "fallback_used": result.fallback_used,
                "retrieval_mode": result.retrieval_mode,
            },
            bundle, warnings, _metadata(max_chars=maximum),
        )

    def _database(self) -> Path:
        if self.base_database is None:
            raise RuntimeError("base_database_unavailable")
        return Path(self.base_database)


def _financial_resolution_warning(resolution: FinancialAccountResolution) -> str:
    if resolution.status == "ambiguous" or resolution.support_level == "ambiguous":
        return "financial_account_clarification_required"
    if resolution.support_level == "retrieval_only":
        return "financial_account_retrieval_only_not_structured"
    if resolution.support_level == "derived":
        return "derived_metric_not_implemented"
    if resolution.support_level == "unsupported" or resolution.status == "unsupported":
        return "financial_account_unsupported"
    return "financial_account_unknown"


def _filing_filters(request: Mapping[str, object], *, include_dates: bool) -> tuple[str, list[object]]:
    clauses: list[str] = []
    params: list[object] = []
    if include_dates:
        clauses.extend(("filed_at>=?", "filed_at<=?"))
        params.extend((request["start_date"], request["end_date"]))
    if request.get("company"):
        clauses.append("(issuer_name=? OR listed_name=? OR reporter_name=? OR stock_code=? OR issuer_corp_code=?)")
        params.extend([request["company"]] * 5)
    if request.get("filing_type"):
        clauses.append("(doc_group=? OR doc_subtype_normalized=?)")
        params.extend([request["filing_type"]] * 2)
    return " AND ".join(clauses) if clauses else "1=1", params


def _lowest_confidence(rows: list[Mapping[str, object]]) -> str:
    if not rows:
        return "none"
    order = {"none": 0, "medium": 1, "high": 2}
    return min((str(row.get("lineage_confidence") or "none") for row in rows), key=lambda item: order.get(item, 0))


def tool_definitions(backend: DisclosureToolBackend) -> tuple[ToolDefinition, ...]:
    search_properties = {
        "question": _optional_string(maximum=2000),
        "company": _optional_string(), "filing_id": _optional_string(),
        "start_date": _date(), "end_date": _date(), "top_k": _top_k(100),
        "correction_policy": _correction_policy(),
    }
    retrieval_status_properties = {
        "dense_status": {"type": "string"},
        "dense_corpus_size": {"type": "integer"},
        "fallback_used": {"type": "boolean"},
        "retrieval_mode": {"type": "string"},
    }
    return (
        ToolDefinition(
            "search_disclosures", "Search filtered disclosures with existing Dense/Sparse hybrid retrieval.",
            object_schema(search_properties, required=("question",)),
            _tool_output_schema(
                {"results": {"type": "array", "items": {"type": "object"}}, **retrieval_status_properties},
                required=("results", "dense_status", "dense_corpus_size", "fallback_used", "retrieval_mode"),
            ),
            backend.search_disclosures,
        ),
        ToolDefinition(
            "get_financial_facts", "Return only validated structured financial facts and their evidence.",
            object_schema({
                "company": _optional_string(), "account": _optional_string(),
                "start_date": _date(), "end_date": _date(),
                "scope": {"type": "string", "enum": ["consolidated", "separate"]},
                "correction_policy": _correction_policy(), "top_k": _top_k(20),
            }, required=("company", "account")),
            _tool_output_schema(
                {
                    "account_resolution": {"type": "object"},
                    "facts": {"type": "array", "items": {"type": "object"}},
                },
                required=("account_resolution", "facts"),
            ),
            backend.get_financial_facts,
        ),
        ToolDefinition(
            "analyze_disclosure_trend", "Aggregate read-only filing metadata without generating interpretation.",
            object_schema({
                "start_date": _date(), "end_date": _date(), "company": _optional_string(),
                "filing_type": _optional_string(),
                "granularity": {"type": "string", "enum": ["day", "week", "month"]},
                "representative_limit": _top_k(50),
            }, required=("start_date", "end_date", "granularity")),
            _tool_output_schema(
                {
                    "count_by_period": {"type": "array", "items": {"type": "object"}},
                    "count_by_type": {"type": "array", "items": {"type": "object"}},
                    "representative_filings": {"type": "array", "items": {"type": "object"}},
                    "total_count": {"type": "integer"},
                    "requested_range": {"type": "object"},
                    "actual_aggregate_range": {"type": "object"},
                    "coverage_complete": {"type": "boolean"},
                },
                required=(
                    "count_by_period", "count_by_type", "representative_filings", "total_count",
                    "requested_range", "actual_aggregate_range", "coverage_complete",
                ),
            ),
            backend.analyze_disclosure_trend,
        ),
        ToolDefinition(
            "get_correction_lineage", "Read the existing correction lineage for one filing.",
            object_schema({"filing_id": _optional_string()}, required=("filing_id",)),
            _tool_output_schema(
                {
                    "original_filing": {"type": ["object", "null"]},
                    "corrected_filings": {"type": "array", "items": {"type": "object"}},
                    "current_filing": {"type": ["object", "null"]},
                    "connection_status": {"type": "string"},
                    "lineage_confidence": {"type": "string"},
                },
                required=(
                    "original_filing", "corrected_filings", "current_filing",
                    "connection_status", "lineage_confidence",
                ),
            ),
            backend.get_correction_lineage,
        ),
        ToolDefinition(
            "build_summary_context", "Build bounded evidence context without running an LLM summary.",
            object_schema({
                **search_properties,
                "top_k": _top_k(50),
                "max_chars": {"type": "integer", "minimum": 100, "maximum": 20000},
            }, required=("question",)),
            _tool_output_schema(
                {
                    "context": {"type": "string"},
                    "context_chars": {"type": "integer"},
                    "result_count": {"type": "integer"},
                    **retrieval_status_properties,
                },
                required=(
                    "context", "context_chars", "result_count", "dense_status",
                    "dense_corpus_size", "fallback_used", "retrieval_mode",
                ),
            ),
            backend.build_summary_context,
        ),
    )


def build_tool_registry(
    *,
    hybrid_retriever: HybridRetriever,
    evidence_service: EvidenceService | None = None,
    base_database: Path | None = None,
) -> ToolRegistry:
    backend = DisclosureToolBackend(hybrid_retriever, evidence_service, base_database)
    registry = ToolRegistry()
    for definition in tool_definitions(backend):
        registry.register(definition)
    return registry


__all__ = [
    "DisclosureToolBackend",
    "TOOL_BACKEND_VERSION",
    "build_tool_registry",
    "tool_definitions",
]
