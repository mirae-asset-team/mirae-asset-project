"""Environment-backed configuration for the contest serving runtime."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from .agent import AgentSettings, DisclosureAgent


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
