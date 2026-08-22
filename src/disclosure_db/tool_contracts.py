"""LLM-agnostic contracts for the first disclosure Tool backend."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Mapping


TOOL_STATUSES = frozenset({"success", "partial", "insufficient", "invalid_request", "error"})
SUFFICIENCY_STATUSES = frozenset({"sufficient", "partial", "insufficient"})
RECOMMENDED_ACTIONS = frozenset({"answer", "answer_with_warning", "ask_clarification", "abstain"})


@dataclass(slots=True)
class ToolEvidenceBundle:
    question_intent: str
    requested_scope: dict[str, object] = field(default_factory=dict)
    covered_scope: dict[str, object] = field(default_factory=dict)
    items: list[dict[str, object]] = field(default_factory=list)
    evidence_ids: list[str] = field(default_factory=list)
    filing_ids: list[str] = field(default_factory=list)
    issuer_corp_codes: list[str] = field(default_factory=list)
    quality_warnings: list[str] = field(default_factory=list)
    correction_status: str | None = None
    retrieval_status: dict[str, object] = field(default_factory=dict)
    sufficiency: str = "insufficient"

    def normalize(self) -> None:
        self.evidence_ids = sorted({str(item) for item in self.evidence_ids if item})
        self.filing_ids = sorted({str(item) for item in self.filing_ids if item})
        self.issuer_corp_codes = sorted({str(item) for item in self.issuer_corp_codes if item})
        self.quality_warnings = list(dict.fromkeys(str(item) for item in self.quality_warnings if item))
        if self.sufficiency not in SUFFICIENCY_STATUSES:
            raise ValueError("tool_evidence_sufficiency_invalid")

    def to_dict(self) -> dict[str, object]:
        self.normalize()
        return asdict(self)


@dataclass(frozen=True, slots=True)
class SufficiencyResult:
    status: str
    reasons: tuple[str, ...] = ()
    missing_requirements: tuple[str, ...] = ()
    recommended_action: str = "abstain"
    answer_allowed: bool = False

    def __post_init__(self) -> None:
        if self.status not in SUFFICIENCY_STATUSES:
            raise ValueError("sufficiency_status_invalid")
        if self.recommended_action not in RECOMMENDED_ACTIONS:
            raise ValueError("recommended_action_invalid")

    def to_dict(self) -> dict[str, object]:
        return {
            "status": self.status,
            "reasons": list(self.reasons),
            "missing_requirements": list(self.missing_requirements),
            "recommended_action": self.recommended_action,
            "answer_allowed": self.answer_allowed,
        }


@dataclass(slots=True)
class ToolResponse:
    status: str
    tool_name: str
    data: dict[str, object]
    evidence_bundle: ToolEvidenceBundle
    warnings: list[str] = field(default_factory=list)
    metadata: dict[str, object] = field(default_factory=dict)

    def to_dict(self) -> dict[str, object]:
        if self.status not in TOOL_STATUSES:
            raise ValueError("tool_status_invalid")
        return {
            "status": self.status,
            "tool_name": self.tool_name,
            "data": self.data,
            "evidence_bundle": self.evidence_bundle.to_dict(),
            "warnings": list(dict.fromkeys(str(item) for item in self.warnings if item)),
            "metadata": dict(self.metadata),
        }


COMMON_OUTPUT_SCHEMA: Mapping[str, object] = {
    "type": "object",
    "required": ["status", "tool_name", "data", "evidence_bundle", "warnings", "metadata"],
    "additionalProperties": False,
    "properties": {
        "status": {"type": "string", "enum": sorted(TOOL_STATUSES)},
        "tool_name": {"type": "string"},
        "data": {"type": "object"},
        "evidence_bundle": {
            "type": "object",
            "required": [
                "question_intent", "requested_scope", "covered_scope", "items",
                "evidence_ids", "filing_ids", "issuer_corp_codes", "quality_warnings",
                "correction_status", "retrieval_status", "sufficiency",
            ],
        },
        "warnings": {"type": "array", "items": {"type": "string"}},
        "metadata": {"type": "object"},
    },
}


def empty_bundle(intent: str, request: Mapping[str, object] | None = None) -> ToolEvidenceBundle:
    return ToolEvidenceBundle(question_intent=intent, requested_scope=dict(request or {}))


__all__ = [
    "COMMON_OUTPUT_SCHEMA",
    "SufficiencyResult",
    "ToolEvidenceBundle",
    "ToolResponse",
    "empty_bundle",
]
