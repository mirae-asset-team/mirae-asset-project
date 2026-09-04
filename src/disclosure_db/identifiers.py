from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from typing import Any

from .contracts import EVIDENCE_VERSION


_WS = re.compile(r"\s+")
_CORRECTION_PREFIX = re.compile(r"^\s*\[(?:기재정정|첨부정정|첨부추가)\]\s*")


def normalize_text(value: str | None) -> str:
    if not value:
        return ""
    return _WS.sub(" ", unicodedata.normalize("NFC", value)).strip()


def normalize_report_name(value: str) -> str:
    value = _CORRECTION_PREFIX.sub("", normalize_text(value))
    return re.sub(r"\s+", "", value).casefold()


def stable_digest(*parts: object, length: int = 24) -> str:
    payload = "\x1f".join(str(p) for p in parts).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()[:length]


def source_id(filing_id: str, source_sha256: str, role: str) -> str:
    return f"src_{stable_digest(filing_id, source_sha256, role)}"


def evidence_id(
    filing_id: str,
    source_sha256: str,
    fragment_type: str,
    locator: dict[str, Any],
) -> str:
    locator_json = json.dumps(locator, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return f"{EVIDENCE_VERSION}_{stable_digest(filing_id, source_sha256, fragment_type, locator_json, length=32)}"


def table_id(filing_id: str, source_sha256: str, sequence_no: int) -> str:
    return f"tbl_{stable_digest(filing_id, source_sha256, sequence_no, length=28)}"


def fact_id(filing_id: str, predicate: str, value: str, evidence_ids: list[str]) -> str:
    return f"fact_{stable_digest(filing_id, predicate, value, *evidence_ids, length=28)}"

