"""Bounded, catalog-only retrieval helpers for disclosure analysis slots."""

from __future__ import annotations

import json
import os
import re
import unicodedata
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType

from .agent_contracts import EvidenceRef
from .analysis_contracts import AnalysisPlan, EvidenceSlot
from .analysis_planner import _DIRECT_TRANSACTION_ACTION
from .search_index import rrf_fuse


MAX_VARIANTS_PER_SLOT = 4
MAX_CANDIDATES_PER_VARIANT = 30
MAX_EVIDENCE_PER_SLOT = 8
MAX_TOTAL_EVIDENCE = 20
_PROMPT_INJECTION_MARKERS = (
    "ignore previous", "ignore all previous", "system prompt", "developer message",
    "이전 지시를 무시", "지시를 무시", "시스템 프롬프트",
)
_DEFAULT_EXPANSIONS_PATH = Path(__file__).resolve().parents[2] / "config" / "freeform_query_expansions.json"


def _safe_diagnostics(value: Mapping[str, object] | None = None) -> Mapping[str, object]:
    return MappingProxyType(dict(value or {}))


@dataclass(frozen=True, slots=True)
class SlotRetrieval:
    slot_id: str
    evidence: tuple[EvidenceRef, ...] = ()
    complete: bool = False
    reason_codes: tuple[str, ...] = ()
    retrieval_diagnostics: Mapping[str, object] = field(default_factory=_safe_diagnostics)

    def __post_init__(self) -> None:
        object.__setattr__(self, "evidence", tuple(self.evidence))
        object.__setattr__(self, "reason_codes", tuple(self.reason_codes))
        object.__setattr__(self, "retrieval_diagnostics", _safe_diagnostics(self.retrieval_diagnostics))


@dataclass(frozen=True, slots=True)
class AnalysisRetrieval:
    evidence: tuple[EvidenceRef, ...] = ()
    slots: tuple[SlotRetrieval, ...] = ()
    complete: bool = False
    reason_codes: tuple[str, ...] = ()
    retrieval_diagnostics: Mapping[str, object] = field(default_factory=_safe_diagnostics)
    financial_facts: tuple[Mapping[str, object], ...] = ()
    event_facts: tuple[Mapping[str, object], ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "evidence", tuple(self.evidence))
        object.__setattr__(self, "slots", tuple(self.slots))
        object.__setattr__(self, "reason_codes", tuple(self.reason_codes))
        object.__setattr__(self, "retrieval_diagnostics", _safe_diagnostics(self.retrieval_diagnostics))
        object.__setattr__(self, "financial_facts", tuple(MappingProxyType(dict(item)) for item in self.financial_facts))
        object.__setattr__(self, "event_facts", tuple(MappingProxyType(dict(item)) for item in self.event_facts))


def _normalized_action_text(text: str) -> str:
    return re.sub(r"[\s\W_]+", "", unicodedata.normalize("NFKC", text).casefold())


def has_transaction_action_surface(question: str) -> bool:
    """Return true for any buy/sell action surface, including quoted historical text."""
    return bool(_DIRECT_TRANSACTION_ACTION.search(_normalized_action_text(question)))


def _load_catalog(path: Path | None = None) -> Mapping[str, tuple[str, ...]]:
    catalog_path = path or Path(os.environ.get("DISCLOSURE_CONFIG_DIR", "")) / "freeform_query_expansions.json"
    if not str(catalog_path) or not catalog_path.exists():
        catalog_path = _DEFAULT_EXPANSIONS_PATH
    with catalog_path.open(encoding="utf-8") as handle:
        payload = json.load(handle)
    raw_terms = payload.get("slot_terms") if isinstance(payload, dict) else None
    if not isinstance(raw_terms, dict):
        raise ValueError("freeform query expansion catalog must contain slot_terms")
    catalog: dict[str, tuple[str, ...]] = {}
    for slot_id, terms in raw_terms.items():
        if not isinstance(slot_id, str) or not slot_id or not isinstance(terms, list):
            raise ValueError("freeform query expansion catalog is invalid")
        normalized = tuple(dict.fromkeys(str(term).strip() for term in terms if isinstance(term, str) and term.strip()))
        if not normalized or len(normalized) > MAX_VARIANTS_PER_SLOT:
            raise ValueError("freeform query expansion terms must be one to four unique strings")
        catalog[slot_id] = normalized
    return MappingProxyType(catalog)


def build_query_variants(plan: AnalysisPlan, slot: EvidenceSlot) -> tuple[str, ...]:
    """Build at most four deterministic issuer-bound search variants from the local catalog."""
    issuer = slot.issuer or plan.base_plan.company
    if not issuer:
        return ()
    catalog_slot_id = slot.slot_id.split("__i", 1)[0]
    terms = _load_catalog().get(catalog_slot_id, ())
    return tuple(f"{issuer} {term}" for term in terms[:MAX_VARIANTS_PER_SLOT])


def _is_prompt_injection(ref: EvidenceRef) -> bool:
    return any(marker in ref.text.casefold() for marker in _PROMPT_INJECTION_MARKERS)


def _is_fusion_safe(ref: EvidenceRef, expected_issuer: str | None) -> bool:
    if ref.lineage_status not in {"root", "resolved"} or _is_prompt_injection(ref):
        return False
    locator = ref.locator if isinstance(ref.locator, dict) else {}
    issuer = locator.get("analysis_issuer")
    if expected_issuer is not None and issuer is not None and issuer != expected_issuer:
        return False
    return ref.is_current is not False or bool(locator.get("analysis_version_admitted"))


def fuse_slot_results(rankings: Iterable[Iterable[EvidenceRef]], *, limit: int) -> list[EvidenceRef]:
    """RRF-fuse already hydrated references while retaining only safe, compatible nodes."""
    expected_issuer: str | None = None
    normalized_rankings: list[list[EvidenceRef]] = []
    for ranking in rankings:
        refs = list(ranking)
        if expected_issuer is None:
            expected_issuer = next(
                (
                    str(ref.locator["analysis_issuer"])
                    for ref in refs
                    if isinstance(ref.locator, dict) and ref.locator.get("analysis_issuer")
                ),
                None,
            )
        normalized_rankings.append(refs)
    rows = [
        [
            {"evidence_id": ref.evidence_id, "ref": ref}
            for ref in ranking
            if _is_fusion_safe(ref, expected_issuer)
        ]
        for ranking in normalized_rankings
    ]
    fused = rrf_fuse(rows, limit=max(0, min(limit, MAX_EVIDENCE_PER_SLOT)))
    return [row["ref"] for row in fused if isinstance(row.get("ref"), EvidenceRef)]


__all__ = [
    "AnalysisRetrieval",
    "MAX_CANDIDATES_PER_VARIANT",
    "MAX_EVIDENCE_PER_SLOT",
    "MAX_TOTAL_EVIDENCE",
    "MAX_VARIANTS_PER_SLOT",
    "SlotRetrieval",
    "build_query_variants",
    "fuse_slot_results",
    "has_transaction_action_surface",
]
