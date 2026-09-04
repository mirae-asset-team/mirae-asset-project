"""Environment-backed configuration for the contest serving runtime."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from .agent import AgentSettings, DisclosureAgent
from .bounded_analysis import BoundedAnalysisExecutor
from .dense_client import EvidenceServiceDenseRetriever
from .disclosure_tools import HybridSearch, build_tool_registry
from .hcx_function_calling import (
    HcxFunctionCallingService,
    HcxFunctionClient,
    HyperClovaFunctionClient,
)
from .hybrid_retrieval import HybridRetriever, SparseRetriever
from .question_routing import DeterministicQuestionRouter


@dataclass(frozen=True, slots=True)
class RuntimeConfig:
    base_database: Path
    overlay_database: Path
    search_database: Path
    attestation_path: Path
    host: str = "0.0.0.0"
    port: int = 8000
    clovastudio_api_key: str | None = None

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "RuntimeConfig":
        values = os.environ if env is None else env
        required = {
            "base_database": "DISCLOSURE_BASE_DB",
            "overlay_database": "DISCLOSURE_OVERLAY_DB",
            "search_database": "DISCLOSURE_SEARCH_DB",
            "attestation_path": "DISCLOSURE_ATTESTATION",
        }
        missing = [name for name in required.values() if not values.get(name)]
        if missing:
            raise ValueError("missing environment variables: " + ",".join(missing))
        return cls(
            **{field: Path(values[name]) for field, name in required.items()},
            host=values.get("DISCLOSURE_HOST", "0.0.0.0"),
            port=int(values.get("DISCLOSURE_PORT", "8000")),
            clovastudio_api_key=values.get("CLOVASTUDIO_API_KEY") or None,
        )

    @property
    def provider_configured(self) -> bool:
        return bool(self.clovastudio_api_key)

    def validate(self) -> None:
        for path in (self.base_database, self.overlay_database, self.search_database, self.attestation_path):
            if not path.is_file():
                raise ValueError(f"runtime file missing: {path}")

    def to_agent_settings(self) -> AgentSettings:
        return AgentSettings(
            base_database=self.base_database,
            overlay_database=self.overlay_database,
            search_database=self.search_database,
            attestation_path=self.attestation_path,
        )


def build_agent(config: RuntimeConfig) -> DisclosureAgent:
    config.validate()
    return DisclosureAgent(config.to_agent_settings())


@dataclass(frozen=True, slots=True)
class RuntimeServices:
    """Fully composed serving dependencies for the existing and HCX routes."""

    agent: DisclosureAgent
    function_calling: HcxFunctionCallingService


def build_function_calling_service(
    agent: DisclosureAgent,
    *,
    client: HcxFunctionClient | None = None,
    hybrid_retriever: HybridSearch | None = None,
) -> HcxFunctionCallingService:
    """Wire ToolRegistry to the agent's attested read-only serving inputs."""

    evidence_service = agent.evidence_service
    if hybrid_retriever is None:
        search_database = getattr(evidence_service, "search_database", None)
        attestation = getattr(evidence_service, "attestation", None)
        if search_database is None:
            raise ValueError("function_calling_search_database_not_configured")
        if attestation is None:
            raise ValueError("function_calling_attestation_not_configured")
        sparse = SparseRetriever.open(
            search_database,
            base_sha256=attestation.sha256,
            expected_base_size=attestation.size_bytes,
        )
        remote_dense = (
            EvidenceServiceDenseRetriever(evidence_service)
            if getattr(evidence_service, "dense_client", None) is not None
            else None
        )
        hybrid_retriever = HybridRetriever(sparse, remote_dense)  # type: ignore[arg-type]
    registry = build_tool_registry(
        hybrid_retriever=hybrid_retriever,  # type: ignore[arg-type]
        evidence_service=evidence_service,
        base_database=Path(evidence_service.base_database),
    )
    candidate_loader = getattr(evidence_service, "company_candidates", None)
    router = DeterministicQuestionRouter(candidate_loader()) if callable(candidate_loader) else None
    return HcxFunctionCallingService(
        registry,
        client if client is not None else HyperClovaFunctionClient(),
        router=router,
        analysis_executor=BoundedAnalysisExecutor(evidence_service),
    )


def build_runtime_services(
    settings: AgentSettings,
    *,
    client: HcxFunctionClient | None = None,
    hybrid_retriever: HybridSearch | None = None,
) -> RuntimeServices:
    """Build the existing agent and the separate HCX Function Calling path."""

    agent = DisclosureAgent(settings)
    function_calling = build_function_calling_service(
        agent, client=client, hybrid_retriever=hybrid_retriever,
    )
    return RuntimeServices(agent, function_calling)


__all__ = [
    "RuntimeConfig",
    "RuntimeServices",
    "build_agent",
    "build_function_calling_service",
    "build_runtime_services",
]
