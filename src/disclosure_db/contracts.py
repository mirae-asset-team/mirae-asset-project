from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


PARSER_NAME = "disclosure_db"
PARSER_VERSION = "0.1.0"
EVIDENCE_VERSION = "ev1"


@dataclass(slots=True)
class CellRecord:
    evidence_id: str
    table_id: str
    source_id: str
    filing_id: str
    row_index: int
    column_index: int
    rowspan: int
    colspan: int
    cell_kind: str
    row_header_path: list[str]
    column_header_path: list[str]
    text_raw: str
    text_normalized: str
    locator: dict[str, Any]


@dataclass(slots=True)
class FragmentRecord:
    evidence_id: str
    filing_id: str
    source_id: str
    fragment_type: str
    sequence_no: int
    section_path: list[str]
    text_raw: str
    text_normalized: str
    locator: dict[str, Any]
    page_no: int | None = None
    table_id: str | None = None


@dataclass(slots=True)
class TableRecord:
    table_id: str
    filing_id: str
    source_id: str
    sequence_no: int
    section_path: list[str]
    caption: str | None
    unit_text: str | None
    row_count: int
    column_count: int
    parse_status: str
    locator: dict[str, Any]


@dataclass(slots=True)
class FactRecord:
    fact_id: str
    filing_id: str
    fact_type: str
    subject: str
    predicate: str
    value_raw: str
    unit: str | None
    evidence_ids: list[str]
    extraction_method: str
    validation_status: str


@dataclass(slots=True)
class ParseResult:
    parse_status: str
    strict_xml_ok: bool | None
    parser_errors: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    fragments: list[FragmentRecord] = field(default_factory=list)
    tables: list[TableRecord] = field(default_factory=list)
    cells: list[CellRecord] = field(default_factory=list)
    facts: list[FactRecord] = field(default_factory=list)
    page_count: int | None = None
    tree_text_chars: int = 0
    extracted_text_chars: int = 0
    image_reference_count: int = 0
