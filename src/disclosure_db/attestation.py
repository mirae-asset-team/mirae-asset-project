"""Fast, startup-loaded identity checks for distributed corpus files."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path


_SHA256_RE = re.compile(r"[0-9a-fA-F]{64}\Z")


@dataclass(frozen=True, slots=True)
class CorpusAttestation:
    sha256: str
    size_bytes: int
    mtime_ns: int | None
    revision: str


def load_distribution_attestation(path: Path, *, database: Path) -> CorpusAttestation:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    section = payload["database"]
    sha256 = str(section["uncompressed_sha256"]).lower()
    if _SHA256_RE.fullmatch(sha256) is None:
        raise ValueError("distribution attestation requires a 64-character SHA-256")
    size_bytes = int(section["uncompressed_bytes"])
    if size_bytes < 0:
        raise ValueError("distribution attestation requires a non-negative database size")
    stat = Path(database).stat()
    return CorpusAttestation(
        sha256=sha256,
        size_bytes=size_bytes,
        mtime_ns=int(stat.st_mtime_ns),
        revision=str(payload.get("database_version") or "unknown"),
    )


def verify_fast_identity(database: Path, attestation: CorpusAttestation) -> bool:
    try:
        stat = Path(database).stat()
    except OSError:
        return False
    return int(stat.st_size) == attestation.size_bytes and (
        attestation.mtime_ns is None or int(stat.st_mtime_ns) == attestation.mtime_ns
    )
