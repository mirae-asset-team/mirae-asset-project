from __future__ import annotations

import re
from pathlib import Path
from typing import Iterable

from lxml import etree, html

from .contracts import CellRecord, FactRecord, FragmentRecord, ParseResult, TableRecord
from .identifiers import evidence_id, fact_id, normalize_text, table_id


_SECTION_TAG = re.compile(r"SECTION-\d+$", re.IGNORECASE)
_UNIT_LABEL = re.compile(r"단위\s*[:：]\s*([^()\[\]]{1,40})")
_UNIT_PAREN = re.compile(r"\(([^()]{1,20})\)")
_DATE = re.compile(r"(20\d{2})[.년\-/ ]+\s*(\d{1,2})[.월\-/ ]+\s*(\d{1,2})")
_UNIT_VALUE = re.compile(r"^(?:원|천원|백만원|억원|조원|주|천주|백만주|명|개|건|회|%|％|배|톤|kg|㎏|m2|㎡)$", re.IGNORECASE)


def local_name(element: etree._Element) -> str:
    if not isinstance(element.tag, str):
        return ""
    return element.tag.rsplit("}", 1)[-1].upper()


def element_text(element: etree._Element) -> str:
    return normalize_text(" ".join(element.itertext()))


def has_ancestor(element: etree._Element, tag_name: str) -> bool:
    parent = element.getparent()
    while parent is not None:
        if local_name(parent) == tag_name:
            return True
        parent = parent.getparent()
    return False


def nearest_ancestor(element: etree._Element, tag_name: str) -> etree._Element | None:
    parent = element.getparent()
    while parent is not None:
        if local_name(parent) == tag_name:
            return parent
        parent = parent.getparent()
    return None


def section_path(element: etree._Element) -> list[str]:
    path: list[str] = []
    current: etree._Element | None = element
    sections: list[etree._Element] = []
    while current is not None:
        if _SECTION_TAG.fullmatch(local_name(current)):
            sections.append(current)
        current = current.getparent()
    for section in reversed(sections):
        title = ""
        for child in section:
            if local_name(child) == "TITLE":
                title = element_text(child)
                if title:
                    break
        path.append(title or local_name(section))
    return path


def _parser_errors(error_log: Iterable[etree._LogEntry], *, phase: str, limit: int = 100) -> list[dict[str, object]]:
    result = []
    for error in list(error_log)[:limit]:
        result.append(
            {
                "phase": phase,
                "level": error.level_name,
                "type": error.type_name,
                "line": error.line,
                "column": error.column,
                "message": error.message,
            }
        )
    return result


def _parse_markup(path: Path, detected_format: str) -> tuple[etree._Element, bool | None, list[dict[str, object]], list[str]]:
    raw = path.read_bytes()
    warnings: list[str] = []
    if detected_format in {"exchange_html", "viewer_html"}:
        text = raw.decode("utf-8", errors="strict")
        parser = html.HTMLParser(encoding="utf-8", recover=True, no_network=True, remove_comments=False)
        tree = html.fromstring(text, parser=parser)
        return tree, None, _parser_errors(parser.error_log, phase="html"), warnings

    strict_parser = etree.XMLParser(
        recover=False,
        no_network=True,
        resolve_entities=False,
        huge_tree=True,
        collect_ids=False,
        remove_comments=False,
    )
    try:
        tree = etree.fromstring(raw, parser=strict_parser)
        return tree, True, _parser_errors(strict_parser.error_log, phase="strict"), warnings
    except etree.XMLSyntaxError as exc:
        strict_errors = _parser_errors(exc.error_log, phase="strict")
        recovery_parser = etree.XMLParser(
            recover=True,
            no_network=True,
            resolve_entities=False,
            huge_tree=True,
            collect_ids=False,
            remove_comments=False,
        )
        tree = etree.fromstring(raw, parser=recovery_parser)
        warnings.append("strict_xml_failed_recovery_used")
        recovery_errors = _parser_errors(recovery_parser.error_log, phase="recovery")
        return tree, False, strict_errors + recovery_errors, warnings


def _table_unit(table: etree._Element) -> str | None:
    def valid_unit(value: str) -> str | None:
        candidate = normalize_text(value).strip("()[] ")[:100]
        if not candidate or _DATE.search(candidate) or "현재" in candidate or "기준" in candidate:
            return None
        return candidate if _UNIT_VALUE.fullmatch(candidate) else None

    for key, value in table.attrib.items():
        if key.upper() in {"AUNIT", "AUNITVALUE"} and normalize_text(value):
            unit = valid_unit(value)
            if unit:
                return unit
    sibling = table.getprevious()
    checked = 0
    while sibling is not None and checked < 4:
        text = element_text(sibling)
        if "단위" in text:
            match = _UNIT_LABEL.search(text) or _UNIT_PAREN.search(text)
            unit = valid_unit(match.group(1)) if match else None
            if unit:
                return unit
        sibling = sibling.getprevious()
        checked += 1
    return None


def _table_caption(table: etree._Element) -> str | None:
    sibling = table.getprevious()
    checked = 0
    while sibling is not None and checked < 3:
        if local_name(sibling) in {"TITLE", "P", "TU"}:
            value = element_text(sibling)
            if value:
                return value[:500]
        sibling = sibling.getprevious()
        checked += 1
    return None


def _grid_position(row_index: int, occupied: dict[tuple[int, int], bool]) -> int:
    column = 0
    while occupied.get((row_index, column), False):
        column += 1
    return column


def _positive_span(value: str | None) -> int:
    try:
        result = int(value or "1")
    except ValueError:
        return 1
    return max(result, 1)


def _cell_kind(cell: etree._Element, detected_format: str) -> str:
    if local_name(cell) == "TH":
        return "header"
    if detected_format == "exchange_html":
        has_input_value = any(
            "xforms_input" in normalize_text(descendant.attrib.get("class", "")).split()
            for descendant in cell.iter()
            if isinstance(descendant.tag, str)
        )
        return "data" if has_input_value else "header"
    return "data"


def parse_markup(
    path: Path,
    *,
    filing_id: str,
    source_id: str,
    source_sha256: str,
    detected_format: str,
    issuer_name: str,
    doc_group: str,
) -> ParseResult:
    try:
        tree, strict_ok, errors, warnings = _parse_markup(path, detected_format)
    except Exception as exc:
        return ParseResult(
            parse_status="failed",
            strict_xml_ok=None,
            warnings=[f"parse_exception:{type(exc).__name__}:{exc}"],
        )

    result = ParseResult(parse_status="success", strict_xml_ok=strict_ok, parser_errors=errors, warnings=warnings)
    result.tree_text_chars = len(element_text(tree))
    result.image_reference_count = sum(1 for e in tree.iter() if local_name(e) in {"IMAGE", "IMG"})
    sequence_no = 0

    # Headings and non-table narrative. TE/TU are included because DART often stores prose there.
    narrative_tags = {"TITLE", "P", "TE", "TU"}
    for element in tree.iter():
        tag = local_name(element)
        if tag not in narrative_tags or has_ancestor(element, "TABLE"):
            continue
        text = element_text(element)
        if not text:
            continue
        fragment_type = "heading" if tag == "TITLE" else ("html_text" if detected_format.endswith("html") else "paragraph")
        locator = {"kind": "element", "tag": tag, "ordinal": sequence_no}
        ev_id = evidence_id(filing_id, source_sha256, fragment_type, locator)
        result.fragments.append(
            FragmentRecord(
                evidence_id=ev_id,
                filing_id=filing_id,
                source_id=source_id,
                fragment_type=fragment_type,
                sequence_no=sequence_no,
                section_path=section_path(element),
                text_raw=text,
                text_normalized=text,
                locator=locator,
            )
        )
        result.extracted_text_chars += len(text)
        sequence_no += 1

    tables = [element for element in tree.iter() if local_name(element) == "TABLE"]
    table_numbers = {id(element): index for index, element in enumerate(tables)}
    rows_by_table: dict[int, list[etree._Element]] = {index: [] for index in range(len(tables))}
    for element in tree.iter():
        if local_name(element) != "TR":
            continue
        owner = nearest_ancestor(element, "TABLE")
        if owner is not None and id(owner) in table_numbers:
            rows_by_table[table_numbers[id(owner)]].append(element)

    for table_index, table in enumerate(tables):
        table_cell_start = len(result.cells)
        tbl_id = table_id(filing_id, source_sha256, table_index)
        table_rows = rows_by_table.get(table_index, [])
        occupied: dict[tuple[int, int], bool] = {}
        row_texts: list[list[str]] = []
        row_evidence: list[list[str]] = []
        max_column = 0
        for row_index, row in enumerate(table_rows):
            cells = [child for child in row if local_name(child) in {"TD", "TH", "TE"}]
            if not cells:
                continue
            texts: list[str] = []
            evidence_ids: list[str] = []
            for cell in cells:
                column_index = _grid_position(row_index, occupied)
                rowspan = _positive_span(cell.attrib.get("ROWSPAN") or cell.attrib.get("rowspan"))
                colspan = _positive_span(cell.attrib.get("COLSPAN") or cell.attrib.get("colspan"))
                text = element_text(cell)
                locator = {
                    "kind": "table_cell",
                    "table": table_index,
                    "row": row_index,
                    "column": column_index,
                }
                ev_id = evidence_id(filing_id, source_sha256, "cell", locator)
                result.cells.append(
                    CellRecord(
                        evidence_id=ev_id,
                        table_id=tbl_id,
                        source_id=source_id,
                        filing_id=filing_id,
                        row_index=row_index,
                        column_index=column_index,
                        rowspan=rowspan,
                        colspan=colspan,
                        cell_kind=_cell_kind(cell, detected_format),
                        row_header_path=[],
                        column_header_path=[],
                        text_raw=text,
                        text_normalized=text,
                        locator=locator,
                    )
                )
                for rr in range(row_index, row_index + rowspan):
                    for cc in range(column_index, column_index + colspan):
                        occupied[(rr, cc)] = True
                max_column = max(max_column, column_index + colspan)
                texts.append(text)
                evidence_ids.append(ev_id)
            row_texts.append(texts)
            row_evidence.append(evidence_ids)
            row_text = " | ".join(texts)
            if row_text.strip(" |"):
                locator = {"kind": "table_row", "table": table_index, "row": row_index}
                ev_id = evidence_id(filing_id, source_sha256, "table_row", locator)
                result.fragments.append(
                    FragmentRecord(
                        evidence_id=ev_id,
                        filing_id=filing_id,
                        source_id=source_id,
                        fragment_type="table_row",
                        sequence_no=sequence_no,
                        section_path=section_path(table),
                        text_raw=row_text,
                        text_normalized=row_text,
                        locator=locator,
                        table_id=tbl_id,
                    )
                )
                result.extracted_text_chars += len(row_text)
                sequence_no += 1

        table_cells = result.cells[table_cell_start:]
        if doc_group != "periodic":
            cells_by_row: dict[int, list[CellRecord]] = {}
            for cell in table_cells:
                cells_by_row.setdefault(cell.row_index, []).append(cell)
            for row_cells in cells_by_row.values():
                row_cells.sort(key=lambda item: item.column_index)
                nonempty = [item for item in row_cells if item.text_normalized]
                if len(nonempty) < 2 or any(item.cell_kind == "header" for item in nonempty):
                    continue
                first = nonempty[0]
                if first.column_index == 0 and not any(character.isdigit() for character in first.text_normalized):
                    first.cell_kind = "header"
        row_headers_index: dict[int, list[tuple[int, str]]] = {}
        column_headers_index: dict[int, list[tuple[int, str]]] = {}
        for other in table_cells:
            if other.cell_kind != "header" or not other.text_normalized:
                continue
            for row in range(other.row_index, other.row_index + other.rowspan):
                row_headers_index.setdefault(row, []).append((other.column_index, other.text_normalized))
            for column in range(other.column_index, other.column_index + other.colspan):
                column_headers_index.setdefault(column, []).append((other.row_index, other.text_normalized))
        for headers in row_headers_index.values():
            headers.sort()
        for headers in column_headers_index.values():
            headers.sort()
        for cell in table_cells:
            row_headers = [
                text
                for column, text in row_headers_index.get(cell.row_index, [])
                if column < cell.column_index
            ]
            column_headers = [
                text
                for row, text in column_headers_index.get(cell.column_index, [])
                if row < cell.row_index
            ]
            cell.row_header_path = list(dict.fromkeys(row_headers))
            cell.column_header_path = list(dict.fromkeys(column_headers))

        unit = _table_unit(table)
        result.tables.append(
            TableRecord(
                table_id=tbl_id,
                filing_id=filing_id,
                source_id=source_id,
                sequence_no=table_index,
                section_path=section_path(table),
                caption=_table_caption(table),
                unit_text=unit,
                row_count=len(table_rows),
                column_count=max_column,
                parse_status="success" if table_rows else "partial",
                locator={"kind": "table", "ordinal": table_index},
            )
        )

        # Conservative generic facts for event/ownership filings. Periodic financial facts require
        # a dedicated account/period/scope normalizer and are intentionally not guessed here.
        if doc_group != "periodic":
            for texts, evidence_ids in zip(row_texts, row_evidence):
                values = [(text, ev) for text, ev in zip(texts, evidence_ids) if text]
                if not 2 <= len(values) <= 6:
                    continue
                predicate = values[0][0]
                value = " | ".join(item[0] for item in values[1:])
                if not (1 <= len(predicate) <= 120 and value and predicate != value):
                    continue
                supporting = [item[1] for item in values]
                f_id = fact_id(filing_id, predicate, value, supporting)
                result.facts.append(
                    FactRecord(
                        fact_id=f_id,
                        filing_id=filing_id,
                        fact_type="event_kv_candidate",
                        subject=issuer_name,
                        predicate=predicate,
                        value_raw=value,
                        unit=unit,
                        evidence_ids=supporting,
                        extraction_method="generic_table_label_value_v1",
                        validation_status="candidate",
                    )
                )

    if not result.fragments and result.tree_text_chars:
        result.parse_status = "partial"
        result.warnings.append("nonempty_tree_without_indexable_fragments")
    if errors:
        result.warnings.append("parser_reported_errors")
    return result


def parse_pdf(
    path: Path,
    *,
    filing_id: str,
    source_id: str,
    source_sha256: str,
) -> ParseResult:
    try:
        import pymupdf

        document = pymupdf.open(path)
    except Exception as exc:
        return ParseResult(
            parse_status="failed",
            strict_xml_ok=None,
            warnings=[f"pdf_open_exception:{type(exc).__name__}:{exc}"],
        )
    result = ParseResult(parse_status="success", strict_xml_ok=None, page_count=document.page_count)
    sequence_no = 0
    try:
        for page_index, page in enumerate(document):
            blocks = page.get_text("blocks", sort=True)
            for block_index, block in enumerate(blocks):
                text = normalize_text(block[4])
                if not text:
                    continue
                bbox = [round(float(value), 3) for value in block[:4]]
                locator = {"kind": "pdf_block", "page": page_index + 1, "block": block_index, "bbox": bbox}
                ev_id = evidence_id(filing_id, source_sha256, "pdf_block", locator)
                result.fragments.append(
                    FragmentRecord(
                        evidence_id=ev_id,
                        filing_id=filing_id,
                        source_id=source_id,
                        fragment_type="pdf_block",
                        sequence_no=sequence_no,
                        section_path=[],
                        page_no=page_index + 1,
                        text_raw=text,
                        text_normalized=text,
                        locator=locator,
                    )
                )
                result.extracted_text_chars += len(text)
                sequence_no += 1
        result.tree_text_chars = result.extracted_text_chars
        if not result.fragments:
            result.parse_status = "partial"
            result.warnings.append("pdf_without_extractable_text")
        result.warnings.append("pdf_table_structure_not_validated")
    except Exception as exc:
        result.parse_status = "partial" if result.fragments else "failed"
        result.warnings.append(f"pdf_extract_exception:{type(exc).__name__}:{exc}")
    finally:
        document.close()
    return result


def extract_first_submission_date(texts: Iterable[str]) -> str | None:
    """Extract a correction's explicitly declared original submission date."""
    for text in texts:
        if "최초제출일" not in text and "정정관련 공시서류제출일" not in text:
            continue
        match = _DATE.search(text)
        if match:
            year, month, day = (int(value) for value in match.groups())
            return f"{year:04d}-{month:02d}-{day:02d}"
    return None
