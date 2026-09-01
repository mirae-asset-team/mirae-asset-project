"""Deterministic, backend-only evidence sufficiency checks for Tool results."""

from __future__ import annotations

from typing import Mapping

from .tool_contracts import SufficiencyResult, ToolEvidenceBundle


def _scope_value_matches(requested: object, covered: object) -> bool:
    if requested in (None, "", []):
        return True
    if isinstance(covered, list):
        return str(requested) in {str(item) for item in covered}
    return str(requested) == str(covered)


class EvidenceSufficiencyChecker:
    """Judge scope/source/quality contracts without using retrieval score thresholds."""

    def check(
        self,
        tool_name: str,
        request: Mapping[str, object],
        bundle: ToolEvidenceBundle,
        data: Mapping[str, object],
    ) -> SufficiencyResult:
        reasons: list[str] = []
        missing: list[str] = []
        if not bundle.filing_ids and tool_name != "get_financial_facts":
            missing.append("filing_ids")
        for field_name in ("company", "filing_id", "account"):
            requested = request.get(field_name)
            covered = bundle.covered_scope.get(field_name)
            if requested not in (None, "") and not _scope_value_matches(requested, covered):
                missing.append(f"scope_mismatch:{field_name}")
        requested_start = request.get("start_date")
        requested_end = request.get("end_date")
        covered_start = bundle.covered_scope.get("start_date")
        covered_end = bundle.covered_scope.get("end_date")
        if requested_start and covered_start and str(covered_start) < str(requested_start):
            missing.append("scope_mismatch:start_date")
        if requested_end and covered_end and str(covered_end) > str(requested_end):
            missing.append("scope_mismatch:end_date")
        if bundle.items and any(item.get("quality_status") in (None, "", "unknown") for item in bundle.items):
            reasons.append("evidence_quality_not_fully_known")
        if bundle.correction_status in (None, "unknown", "uncertain"):
            reasons.append("correction_policy_status_not_fully_known")

        if tool_name == "get_financial_facts":
            return self._financial(request, bundle, data, reasons, missing)
        if tool_name in {"search_disclosures", "build_summary_context"}:
            return self._search_or_summary(bundle, data, reasons, missing)
        if tool_name == "analyze_disclosure_trend":
            return self._trend(bundle, data, reasons, missing)
        if tool_name == "get_correction_lineage":
            return self._correction(bundle, data, reasons, missing)
        return SufficiencyResult("insufficient", ("unknown_tool_contract",), ("known_tool",), "abstain", False)

    @staticmethod
    def _financial(
        request: Mapping[str, object],
        bundle: ToolEvidenceBundle,
        data: Mapping[str, object],
        reasons: list[str],
        missing: list[str],
    ) -> SufficiencyResult:
        facts = data.get("facts")
        if not isinstance(facts, list) or not facts:
            missing.append("validated_structured_financial_fact")
        for fact in facts if isinstance(facts, list) else []:
            if not isinstance(fact, Mapping):
                missing.append("financial_fact_shape")
                continue
            if fact.get("support_level") != "structured" or fact.get("validation_status") != "validated":
                missing.append("structured_validated_status")
            for field_name in ("value_numeric", "unit", "period", "scope", "evidence_ids"):
                if fact.get(field_name) in (None, "", []):
                    missing.append(f"financial_fact:{field_name}")
        requirements = request.get("requirements")
        if isinstance(requirements, list) and requirements:
            coverage = data.get("requirement_coverage")
            coverage_rows = [item for item in coverage if isinstance(item, Mapping)] if isinstance(coverage, list) else []
            if len(coverage_rows) != len(requirements):
                missing.append("financial_comparison_requirement_coverage")
            for index, _requirement in enumerate(requirements):
                if index >= len(coverage_rows) or coverage_rows[index].get("covered") is not True:
                    missing.append(f"financial_comparison_requirement:{index}")
        if not bundle.evidence_ids:
            missing.append("citable_evidence")
        if not bundle.items:
            missing.append("evidence_items")
        if missing:
            resolution = data.get("account_resolution")
            resolution_status = str(resolution.get("status") or "") if isinstance(resolution, Mapping) else ""
            clarification = resolution_status == "ambiguous" or any(
                item in {"scope_mismatch:company", "scope_mismatch:scope"} for item in missing
            )
            return SufficiencyResult(
                "insufficient",
                tuple(dict.fromkeys([*reasons, "financial_fact_contract_not_met"])),
                tuple(dict.fromkeys(missing)),
                "ask_clarification" if clarification else "abstain",
                False,
            )
        return SufficiencyResult("sufficient", ("validated_structured_fact_with_evidence",), (), "answer", True)

    @staticmethod
    def _search_or_summary(
        bundle: ToolEvidenceBundle,
        data: Mapping[str, object],
        reasons: list[str],
        missing: list[str],
    ) -> SufficiencyResult:
        if not bundle.items:
            missing.append("search_results")
        if not bundle.evidence_ids:
            missing.append("citable_evidence")
        if "context" in data and not data.get("context"):
            missing.append("summary_context_text")
        if missing:
            return SufficiencyResult(
                "insufficient", tuple(dict.fromkeys([*reasons, "no_citable_filtered_result"])),
                tuple(dict.fromkeys(missing)), "abstain", False,
            )
        dense_status = str(bundle.retrieval_status.get("dense_status") or "")
        warnings = set(bundle.quality_warnings)
        if "smoke_only" in dense_status or "dense_smoke_only" in warnings or reasons:
            return SufficiencyResult(
                "partial", tuple(dict.fromkeys([*reasons, "filtered_evidence_available_with_warning"])), (),
                "answer_with_warning", True,
            )
        return SufficiencyResult("sufficient", ("filtered_citable_results_available",), (), "answer", True)

    @staticmethod
    def _trend(
        bundle: ToolEvidenceBundle,
        data: Mapping[str, object],
        reasons: list[str],
        missing: list[str],
    ) -> SufficiencyResult:
        total = int(data.get("total_count") or 0)
        if total <= 0:
            missing.append("filing_count")
        if not bundle.filing_ids:
            missing.append("representative_filings")
        if not bundle.evidence_ids:
            missing.append("representative_evidence")
        if missing:
            return SufficiencyResult(
                "insufficient", ("trend_has_no_verifiable_filings",), tuple(dict.fromkeys(missing)),
                "abstain", False,
            )
        if not bool(data.get("coverage_complete")):
            return SufficiencyResult(
                "partial", ("trend_source_covers_only_part_of_requested_period",),
                ("complete_requested_period",), "answer_with_warning", True,
            )
        return SufficiencyResult("sufficient", ("trend_counts_trace_to_filings",), (), "answer", True)

    @staticmethod
    def _correction(
        bundle: ToolEvidenceBundle,
        data: Mapping[str, object],
        reasons: list[str],
        missing: list[str],
    ) -> SufficiencyResult:
        confidence = str(data.get("lineage_confidence") or "none")
        if not data.get("original_filing"):
            missing.append("original_filing")
        if not data.get("current_filing"):
            missing.append("current_filing")
        if not bundle.evidence_ids:
            missing.append("citable_evidence")
        if confidence != "high" or bundle.correction_status != "resolved":
            answer_allowed = bool(bundle.filing_ids and bundle.evidence_ids)
            return SufficiencyResult(
                "partial" if answer_allowed else "insufficient",
                ("correction_lineage_uncertain",), tuple(dict.fromkeys(missing or ["resolved_lineage"])),
                "answer_with_warning" if answer_allowed else "abstain",
                answer_allowed,
            )
        if missing:
            return SufficiencyResult("insufficient", ("correction_chain_incomplete",), tuple(missing), "abstain", False)
        return SufficiencyResult("sufficient", ("resolved_correction_chain",), (), "answer", True)


__all__ = ["EvidenceSufficiencyChecker"]
