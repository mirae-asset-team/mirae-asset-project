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
    database_present: bool = True


def load_distribution_attestation(path: Path, *, database: Path) -> CorpusAttestation:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    section = payload["database"]
    sha256 = str(section["uncompressed_sha256"]).lower()
    if _SHA256_RE.fullmatch(sha256) is None:
        raise ValueError("distribution attestation requires a 64-character SHA-256")
    size_bytes = int(section["uncompressed_bytes"])
    if size_bytes < 0:
        raise ValueError("distribution attestation requires a non-negative database size")
    raw_mtime_ns = section.get("mtime_ns")
    if raw_mtime_ns is None:
        mtime_ns: int | None = None
    elif isinstance(raw_mtime_ns, bool) or not isinstance(raw_mtime_ns, int) or raw_mtime_ns < 0:
        raise ValueError("distribution attestation requires a non-negative integer mtime_ns")
    else:
        mtime_ns = raw_mtime_ns
    # The manifest is the trusted source of identity. Never substitute the
    # request file's current mtime here; a same-size replacement must fail.
    database_present = Path(database).exists()
    return CorpusAttestation(
        sha256=sha256,
        size_bytes=size_bytes,
        mtime_ns=mtime_ns,
        revision=str(payload.get("database_version") or "unknown"),
        database_present=database_present,
    )


def verify_fast_identity(database: Path, attestation: CorpusAttestation) -> bool:
    if not attestation.database_present or attestation.mtime_ns is None:
        return False
    try:
        stat = Path(database).stat()
    except OSError:
        return False
    return int(stat.st_size) == attestation.size_bytes and int(stat.st_mtime_ns) == attestation.mtime_ns
