from __future__ import annotations

import json
import hashlib
import re
import sqlite3
from pathlib import Path
from typing import Any


TOKEN_PATTERN = re.compile(r"[0-9A-Za-z가-힣][0-9A-Za-z가-힣._-]+")
STOP_TOKENS = {
    "공시", "기준", "얼마인가", "무엇인가", "누구인가", "있는가", "인가", "해당",
    "접수", "보고서", "정정본", "현재", "최초", "그리고", "연결", "대한",
}
MIN_SILVER_REVIEWERS = 3

REVIEW_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "question_id", "answerability", "answer", "support_verdict",
        "selected_evidence_ids", "confidence", "reason",
    ],
    "properties": {
        "question_id": {"type": "string"},
        "answerability": {"enum": ["answerable", "unanswerable", "uncertain"]},
        "answer": {
            "type": "object",
            "additionalProperties": False,
            "required": ["kind", "values", "unit"],
            "properties": {
                "kind": {"enum": ["numeric", "multi_numeric", "text", "boolean", "unanswerable", "uncertain"]},
                "values": {"type": "array", "items": {"type": "string"}},
                "unit": {"type": ["string", "null"]},
            },
        },
        "support_verdict": {"enum": ["supported", "contradicted", "insufficient"]},
        "selected_evidence_ids": {"type": "array", "items": {"type": "string"}, "uniqueItems": True},
        "confidence": {"enum": ["low", "medium", "high"]},
        "reason": {"type": "string", "maxLength": 1000},
    },
}


def _json(value: str | None, fallback: Any) -> Any:
    if not value:
        return fallback
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return fallback


def _bounded_text(value: str | None, limit: int) -> tuple[str, bool]:
    text = value or ""
    if len(text) <= limit:
        return text, False
    head = limit * 2 // 3
    tail = limit - head
    return f"{text[:head]}\n…[TRUNCATED {len(text) - limit} CHARS]…\n{text[-tail:]}", True


def _query_tokens(question: str, company_names: list[str]) -> list[str]:
    blocked = STOP_TOKENS | {name for name in company_names if name}
    tokens: list[str] = []
    for token in TOKEN_PATTERN.findall(question):
        if token in blocked or len(token) < 2 or token.isdigit():
            continue
        if token not in tokens:
            tokens.append(token)
    return tokens[:10]


def _cell_context(connection: sqlite3.Connection, evidence_id: str) -> dict[str, Any] | None:
    cell = connection.execute(
        """SELECT c.evidence_id,c.filing_id,c.source_id,c.table_id,c.row_index,c.column_index,
                  c.cell_kind,c.row_header_path_json,c.column_header_path_json,c.locator_json,
                  c.text_normalized,t.caption,t.unit_text,t.section_path_json
           FROM table_cell c JOIN table_record t ON t.table_id=c.table_id
           WHERE c.evidence_id=?""",
        (evidence_id,),
    ).fetchone()
    if cell is None:
        return None
    neighbors = connection.execute(
        """SELECT evidence_id,row_index,column_index,cell_kind,text_normalized
           FROM table_cell WHERE table_id=? AND row_index BETWEEN ? AND ?
           ORDER BY row_index,column_index""",
        (cell[3], max(0, int(cell[4]) - 2), int(cell[4]) + 1),
    ).fetchall()
    target_text, target_truncated = _bounded_text(cell[10], 4000)
    return {
        "evidence_id": cell[0], "filing_id": cell[1], "source_id": cell[2], "kind": "table_cell",
        "table_id": cell[3], "row_index": cell[4], "column_index": cell[5], "cell_kind": cell[6],
        "row_headers": _json(cell[7], []), "column_headers": _json(cell[8], []),
        "locator": _json(cell[9], {}), "text": target_text, "text_truncated": target_truncated,
        "caption": cell[11], "unit": cell[12],
        "section_path": _json(cell[13], []),
        "nearby_cells": [
            {
                "evidence_id": row[0], "row": row[1], "column": row[2], "kind": row[3],
                "text": _bounded_text(row[4], 1000)[0], "text_truncated": _bounded_text(row[4], 1000)[1],
            }
            for row in neighbors
        ],
    }


def _fragment_context(connection: sqlite3.Connection, evidence_id: str) -> dict[str, Any] | None:
    row = connection.execute(
        """SELECT evidence_id,filing_id,source_id,fragment_type,sequence_no,section_path_json,
                  page_no,table_id,locator_json,text_normalized FROM fragment WHERE evidence_id=?""",
        (evidence_id,),
    ).fetchone()
    if row is None:
        return None
    text, truncated = _bounded_text(row[9], 4000)
    return {
        "evidence_id": row[0], "filing_id": row[1], "source_id": row[2], "kind": row[3],
        "sequence_no": row[4], "section_path": _json(row[5], []), "page_no": row[6],
        "table_id": row[7], "locator": _json(row[8], {}), "text": text, "text_truncated": truncated,
    }


def _retrieved_fragments(
    connection: sqlite3.Connection, filing_ids: list[str], question: str, company_names: list[str], limit: int = 8
) -> list[dict[str, Any]]:
    tokens = _query_tokens(question, company_names)
    if not tokens:
        return []
    expression = " OR ".join(f'"{token.replace(chr(34), chr(34) * 2)}"' for token in tokens)
    output: list[dict[str, Any]] = []
    per_filing = max(2, limit // max(1, len(filing_ids)))
    for filing_id in filing_ids:
        rows = connection.execute(
            """SELECT f.evidence_id,f.filing_id,f.source_id,f.fragment_type,f.sequence_no,
                      f.section_path_json,f.page_no,f.table_id,f.locator_json,f.text_normalized,
                      bm25(fragment_fts) AS score
               FROM fragment_fts JOIN fragment f ON f.rowid=fragment_fts.rowid
               WHERE fragment_fts MATCH ? AND f.filing_id=?
               ORDER BY score LIMIT ?""",
            (expression, filing_id, per_filing),
        ).fetchall()
        output.extend(
            {
                "evidence_id": row[0], "filing_id": row[1], "source_id": row[2], "kind": row[3],
                "sequence_no": row[4], "section_path": _json(row[5], []), "page_no": row[6],
                "table_id": row[7], "locator": _json(row[8], {}),
                "text": _bounded_text(row[9], 4000)[0], "text_truncated": _bounded_text(row[9], 4000)[1],
                "retrieval_score": row[10],
            }
            for row in rows
        )
    return sorted(output, key=lambda item: float(item["retrieval_score"]))[:limit]


def build_blind_packet(connection: sqlite3.Connection, record: dict[str, Any]) -> dict[str, Any]:
    """Create a review packet that deliberately excludes the candidate answer and review notes."""
    direct: list[dict[str, Any]] = []
    for evidence in record.get("evidence", []):
        item = _cell_context(connection, evidence["evidence_id"]) or _fragment_context(
            connection, evidence["evidence_id"]
        )
        if item is not None:
            item["role"] = evidence.get("role")
            direct.append(item)
    company = record.get("company_resolution", {})
    filing_ids = list(record.get("candidate_filing_ids", []))
    return {
        "packet_version": "0.1.0",
        "question_id": record["question_id"],
        "question_type": record["question_type"],
        "question": record["question"],
        "company_resolution": company,
        "period": record.get("period"),
        "scope": record.get("scope"),
        "as_of": record.get("as_of"),
        "version_basis": record.get("version_basis"),
        "candidate_filing_ids": filing_ids,
        "direct_evidence": direct,
        "retrieved_context": _retrieved_fragments(
            connection, filing_ids, record["question"],
            [str(company.get("query_name") or ""), str(company.get("issuer_name") or "")],
        ),
        "version_evidence": record.get("version_evidence", []),
        "source_evidence": record.get("source_evidence", []),
    }


def build_packets(database: Path, gold_path: Path) -> list[dict[str, Any]]:
    records = [json.loads(line) for line in gold_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    uri = f"file:{database.resolve().as_posix()}?mode=ro"
    with sqlite3.connect(uri, uri=True) as connection:
        packets = [build_blind_packet(connection, record) for record in records]
    for packet in packets:
        canonical = json.dumps(packet, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        packet["packet_sha256"] = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return packets


def review_prompt(packet: dict[str, Any]) -> str:
    return (
        "당신은 한국 공시 QA의 독립 검수자다. 아래 PACKET만 근거로 판단하라. "
        "기존 후보 답은 제공되지 않았으며 외부 지식·추측·투자 판단을 사용하지 마라. "
        "표는 target cell, header path, nearby cells, unit을 함께 읽는다. 정정 질문은 version_evidence의 "
        "event_id, parent, is_current, effective interval을 사용한다. 근거가 충분하지 않으면 uncertain 또는 "
        "unanswerable로 판정한다. selected_evidence_ids에는 PACKET에 실제 존재하는 ID만 쓴다. "
        "숫자는 콤마 없이 문자열로 쓴다. JSON Schema에 맞는 JSON 하나만 반환하라.\n\nPACKET:\n"
        + json.dumps(packet, ensure_ascii=False, separators=(",", ":"))
    )


def candidate_signature(record: dict[str, Any]) -> dict[str, Any]:
    answer = record["answer"]
    kind = answer.get("kind")
    if kind == "numeric":
        values = [str(answer.get("value", ""))]
    elif kind == "multi_numeric":
        values = [str(item.get("value", "")) for item in answer.get("values", [])]
    elif kind == "text":
        values = [str(answer.get("text", ""))]
    else:
        values = []
    return {"answerability": record["answerability"], "kind": kind, "values": values}


def compare_reviews(record: dict[str, Any], reviews: list[dict[str, Any]]) -> dict[str, Any]:
    candidate = candidate_signature(record)
    answerability_votes = [review.get("answerability") for review in reviews]
    all_answerability_agree = bool(reviews) and len(set(answerability_votes + [candidate["answerability"]])) == 1
    normalized_model_values = [
        [re.sub(r"[\s,]", "", value) for value in review.get("answer", {}).get("values", [])]
        for review in reviews
    ]
    normalized_candidate = [re.sub(r"[\s,]", "", value) for value in candidate["values"]]
    exact_value_agreement = (
        candidate["kind"] in {"numeric", "multi_numeric"}
        and bool(reviews)
        and all(values == normalized_candidate for values in normalized_model_values)
    )
    all_supported = bool(reviews) and all(review.get("support_verdict") == "supported" for review in reviews)
    return {
        "question_id": record["question_id"],
        "candidate": candidate,
        "review_count": len(reviews),
        "answerability_agreement": all_answerability_agree,
        "exact_numeric_agreement": exact_value_agreement,
        "all_reviewers_supported": all_supported,
        "silver_auto_pass": len(reviews) >= MIN_SILVER_REVIEWERS and all_answerability_agree and all_supported and (
            exact_value_agreement if candidate["kind"] in {"numeric", "multi_numeric"} else False
        ),
        "reviews": reviews,
    }
