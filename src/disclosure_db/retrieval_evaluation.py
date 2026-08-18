from __future__ import annotations

import json
import re
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any

from .gold_validation import load_jsonl
from .pipeline import query_database
from .migration import portable_path


TOKEN_PATTERN = re.compile(r"[0-9A-Za-z가-힣]+")
STOPWORDS = {
    "공시", "공시한", "기준", "무엇인가", "얼마인가", "누구인가", "있는가", "인가",
    "각각", "해당", "현재", "원", "몇", "후", "전", "및",
}
PARTICLE_SUFFIXES = ("에서는", "에서", "으로", "에게", "까지", "부터", "에는", "은", "는", "이", "가", "을", "를", "의", "에")


def retrieval_tokens(question: str, *, company_names: list[str] | None = None) -> list[str]:
    excluded = {name.replace(" ", "") for name in (company_names or []) if name}
    output: list[str] = []
    for raw in TOKEN_PATTERN.findall(question):
        token = raw.strip()
        if len(token) < 2 or token in STOPWORDS:
            continue
        variants = [token]
        for suffix in PARTICLE_SUFFIXES:
            if token.endswith(suffix) and len(token) - len(suffix) >= 2:
                variants.append(token[: -len(suffix)])
                break
        if any(value.replace(" ", "") in excluded for value in variants):
            continue
        if any(value.isdigit() and len(value) <= 4 for value in variants):
            continue
        for value in variants:
            if value not in STOPWORDS and value not in output:
                output.append(value)
    if not output:
        raise ValueError("question has no searchable tokens")
    return output


def compile_retrieval_query(question: str, *, company_names: list[str] | None = None) -> str:
    return " OR ".join(
        f'"{token.replace(chr(34), chr(34) * 2)}"'
        for token in retrieval_tokens(question, company_names=company_names)
    )


def _rrf_term_search(
    *,
    database: Path,
    tokens: list[str],
    company_filter: str | None,
    filing_ids: list[str],
    as_of: str | None,
    include_unsafe: bool,
    limit: int,
) -> list[dict[str, object]]:
    fused: dict[str, dict[str, object]] = {}
    for token in tokens:
        rows = query_database(
            database,
            f'"{token.replace(chr(34), chr(34) * 2)}"',
            company=company_filter,
            limit=limit,
            as_of=as_of,
            include_unsafe=include_unsafe,
            filing_ids=filing_ids,
        )
        for rank, row in enumerate(rows, start=1):
            evidence_id = str(row["evidence_id"])
            if evidence_id not in fused:
                fused[evidence_id] = {**row, "rrf_score": 0.0, "matched_terms": []}
            fused[evidence_id]["rrf_score"] = float(fused[evidence_id]["rrf_score"]) + 1.0 / (60 + rank)
            cast_terms = fused[evidence_id]["matched_terms"]
            if isinstance(cast_terms, list):
                cast_terms.append(token)
    return sorted(
        fused.values(),
        key=lambda item: (-float(item["rrf_score"]), float(item["score"]), str(item["evidence_id"])),
    )[:limit]


def _target_fragments(connection: sqlite3.Connection, evidence_id: str) -> list[str]:
    direct = connection.execute(
        "SELECT evidence_id FROM fragment WHERE evidence_id=?", (evidence_id,)
    ).fetchall()
    if direct:
        return [str(row[0]) for row in direct]
    return [
        str(row[0])
        for row in connection.execute(
            """SELECT fr.evidence_id
               FROM table_cell tc
               JOIN fragment fr ON fr.filing_id=tc.filing_id
                 AND fr.fragment_type='table_row'
                 AND fr.table_id=tc.table_id
                 AND CAST(json_extract(fr.locator_json,'$.row') AS INTEGER)=tc.row_index
               WHERE tc.evidence_id=?
               ORDER BY fr.evidence_id""",
            (evidence_id,),
        )
    ]


def evaluate_retrieval(
    *,
    database: Path,
    gold_path: Path,
    limit: int = 20,
) -> dict[str, Any]:
    records = load_jsonl(gold_path)
    database_uri = f"file:{database.resolve().as_posix()}?mode=ro"
    evaluations: list[dict[str, Any]] = []
    with closing(sqlite3.connect(database_uri, uri=True)) as connection:
        for record in records:
            if record.get("answerability") != "answerable" or not record.get("evidence"):
                continue
            company = record.get("company_resolution") or {}
            company_filter = company.get("stock_code") or company.get("corp_code") or company.get("issuer_name")
            company_names = [
                str(company.get(key) or "")
                for key in ("query_name", "issuer_name")
            ]
            tokens = retrieval_tokens(str(record["question"]), company_names=company_names)
            query = " OR ".join(f'"{token}"' for token in tokens)
            historical = record.get("question_type") == "correction_aware"
            results = _rrf_term_search(
                database=database,
                tokens=tokens,
                company_filter=str(company_filter) if company_filter else None,
                filing_ids=[str(item) for item in record.get("candidate_filing_ids", [])],
                as_of=None if historical else record.get("as_of"),
                include_unsafe=historical,
                limit=limit,
            )
            ranks = {str(item["evidence_id"]): rank for rank, item in enumerate(results, start=1)}
            targets: list[dict[str, Any]] = []
            for evidence in record["evidence"]:
                fragment_ids = _target_fragments(connection, str(evidence["evidence_id"]))
                found_ranks = [ranks[item] for item in fragment_ids if item in ranks]
                targets.append(
                    {
                        "evidence_id": evidence["evidence_id"],
                        "target_fragment_ids": fragment_ids,
                        "rank": min(found_ranks) if found_ranks else None,
                    }
                )
            evaluations.append(
                {
                    "question_id": record["question_id"],
                    "question_type": record["question_type"],
                    "query": query,
                    "query_execution": "per_term_fts_plus_rrf_k60",
                    "company_filter": company_filter,
                    "filing_filter": record.get("candidate_filing_ids"),
                    "mode": "historical_version_expanded" if historical else "safe_as_of",
                    "result_count": len(results),
                    "targets": targets,
                }
            )
    target_rows = [target for item in evaluations for target in item["targets"]]
    found = [target for target in target_rows if target["rank"] is not None]
    fully_covered = sum(all(target["rank"] is not None for target in item["targets"]) for item in evaluations)
    # MRR is question-level: one reciprocal rank for the first relevant target in
    # each eligible question. Target recall keeps the evidence-level denominator.
    first_relevant_ranks = [
        min(int(target["rank"]) for target in item["targets"] if target["rank"] is not None)
        for item in evaluations
        if any(target["rank"] is not None for target in item["targets"])
    ]
    post_rerank_found = [target for target in target_rows if target["rank"] is not None and int(target["rank"]) <= 8]
    target_recall = (len(found) / len(target_rows)) if target_rows else None
    return {
        "database": portable_path(database),
        "gold": portable_path(gold_path),
        "limit": limit,
        "evaluation_scope": "retrieval conditioned on Gold company and candidate-filing metadata",
        "eligible_questions": len(evaluations),
        "target_evidence_count": len(target_rows),
        "target_recall_at_k": target_recall,
        "target_recall_at_20": target_recall if limit == 20 else None,
        "question_complete_recall_at_k": (fully_covered / len(evaluations)) if evaluations else None,
        "mrr_at_k": (sum(1 / rank for rank in first_relevant_ranks) / len(evaluations)) if evaluations else None,
        "post_rerank_recall_at_8": (len(post_rerank_found) / len(target_rows)) if target_rows else None,
        "evaluations": evaluations,
    }
