from __future__ import annotations

import json
import re
import sqlite3
from contextlib import closing
from decimal import Decimal, InvalidOperation
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


def _audited_financial_gold(record: dict[str, Any]) -> bool:
    audit = record.get("audit") if isinstance(record.get("audit"), dict) else {}
    review = record.get("review") if isinstance(record.get("review"), dict) else {}
    return bool(
        record.get("answerability") == "answerable"
        and (
            audit.get("state") in {"agent_audited", "human_verified"}
            or review.get("status") == "approved"
        )
    )


def _decimal_equal(left: object, right: object) -> bool:
    try:
        return Decimal(str(left)) == Decimal(str(right))
    except InvalidOperation:
        return False


def audit_financial_fact_coverage(*, seed_path: Path, gold_path: Path) -> dict[str, Any]:
    """Compare checked-in validated financial seeds with audited Gold, without promotion."""
    seed_rows = [row for row in load_jsonl(seed_path) if row.get("validation_status") == "validated"]
    gold_by_question: dict[str, list[dict[str, Any]]] = {}
    for record in load_jsonl(gold_path):
        if _audited_financial_gold(record):
            gold_by_question.setdefault(str(record.get("question_id") or ""), []).append(record)

    facts: list[dict[str, Any]] = []
    for seed in sorted(seed_rows, key=lambda row: str(row.get("financial_fact_id") or "")):
        fact_id = str(seed.get("financial_fact_id") or "")
        question_id = f"agent_financial_{fact_id}"
        candidates = gold_by_question.get(question_id, [])
        reasons: list[str] = []
        if not candidates:
            reasons.append("missing_gold")
        else:
            if len(candidates) != 1:
                reasons.append("duplicate_gold")
            record = candidates[0]
            if [str(item) for item in record.get("candidate_filing_ids", [])] != [str(seed.get("filing_id") or "")]:
                reasons.append("filing_mismatch")
            answer = record.get("answer") if isinstance(record.get("answer"), dict) else {}
            if answer.get("kind") != "numeric" or not _decimal_equal(answer.get("value"), seed.get("value_numeric")):
                reasons.append("value_mismatch")
            try:
                scale_matches = int(answer.get("scale")) == int(seed.get("scale"))
            except (TypeError, ValueError):
                scale_matches = False
            if not scale_matches:
                reasons.append("scale_mismatch")
            gold_evidence = {
                str(item.get("evidence_id"))
                for item in record.get("evidence", [])
                if isinstance(item, dict) and item.get("evidence_id")
            }
            seed_evidence = {str(item) for item in seed.get("evidence_ids", [])}
            if gold_evidence != seed_evidence:
                reasons.append("evidence_mismatch")
        facts.append(
            {
                "financial_fact_id": fact_id,
                "question_id": question_id,
                "status": "covered" if not reasons else "gap",
                "reason_codes": sorted(reasons),
            }
        )

    covered = sum(item["status"] == "covered" for item in facts)
    return {
        "scope": "checked_in_validated_seed_vs_audited_gold",
        "validated_seed_count": len(facts),
        "covered_seed_count": covered,
        "gap_seed_count": len(facts) - covered,
        "coverage": (covered / len(facts)) if facts else None,
        "corpus_wide_complete": False,
        "facts": facts,
    }


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
    reranker: object | None = None,
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
            seen_evidence: set[str] = set()
            for evidence in record["evidence"]:
                evidence_id = str(evidence["evidence_id"])
                if evidence_id in seen_evidence:
                    continue
                seen_evidence.add(evidence_id)
                fragment_ids = sorted(set(_target_fragments(connection, evidence_id)))
                found_ranks = [ranks[item] for item in fragment_ids if item in ranks]
                targets.append(
                    {
                    "evidence_id": evidence_id,
                        "target_fragment_ids": fragment_ids,
                        "rank": min(found_ranks) if found_ranks else None,
                    }
                )
            post_rank_map: dict[str, int] | None = None
            post_reason = "reranker_not_configured"
            if reranker is not None:
                try:
                    if hasattr(reranker, "rerank"):
                        rerank_result = reranker.rerank(str(record["question"]), results, limit=min(8, len(results)))
                    else:
                        rerank_result = reranker(str(record["question"]), results, limit=min(8, len(results)))
                    if isinstance(rerank_result, dict):
                        used_provider = rerank_result.get("used_provider", True)
                        reason_codes = list(rerank_result.get("reason_codes", []))
                        ordered_ids = rerank_result.get("evidence_ids", rerank_result.get("ids", []))
                    else:
                        used_provider = getattr(rerank_result, "used_provider", True)
                        reason_codes = list(getattr(rerank_result, "reason_codes", []))
                        ordered_ids = getattr(rerank_result, "evidence_ids", rerank_result)
                    if not used_provider:
                        post_reason = reason_codes[0] if reason_codes else "reranker_unavailable"
                    elif not isinstance(ordered_ids, (list, tuple)):
                        post_reason = "reranker_invalid_response"
                    else:
                        safe_ids = {str(item["evidence_id"]) for item in results}
                        filtered_ids = [str(item) for item in ordered_ids if str(item) in safe_ids]
                        if not filtered_ids:
                            post_reason = "reranker_empty_response"
                        else:
                            post_rank_map = {item: rank for rank, item in enumerate(filtered_ids, start=1)}
                            post_reason = "ok"
                except Exception:
                    post_reason = "reranker_error"
            post_targets = [
                {"evidence_id": target["evidence_id"], "rank": min((post_rank_map[item] for item in target["target_fragment_ids"] if post_rank_map and item in post_rank_map), default=None)}
                for target in targets
            ]
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
                    "post_rerank_targets": post_targets,
                    "post_rerank_reason": post_reason,
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
    target_recall = (len(found) / len(target_rows)) if target_rows else None
    post_rerank_attempted = len(evaluations) if reranker is not None else 0
    post_rerank_success = sum(item["post_rerank_reason"] == "ok" for item in evaluations)
    post_rerank_failure = post_rerank_attempted - post_rerank_success
    post_rows = [target for item in evaluations for target in item["post_rerank_targets"]] if post_rerank_success else []
    post_rerank_found = [target for target in post_rows if target["rank"] is not None and int(target["rank"]) <= 8]
    post_rerank_available = post_rerank_success > 0
    reasons: dict[str, int] = {}
    for item in evaluations:
        if item["post_rerank_reason"] != "ok":
            reason = str(item["post_rerank_reason"])
            reasons[reason] = reasons.get(reason, 0) + 1
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
        "post_rerank_recall_at_8": (len(post_rerank_found) / len(post_rows)) if post_rerank_available and post_rows else None,
        "post_rerank_reason": "ok" if post_rerank_available else (evaluations[0]["post_rerank_reason"] if evaluations else "no_eligible_questions"),
        "post_rerank_attempted_count": post_rerank_attempted,
        "post_rerank_success_count": post_rerank_success,
        "post_rerank_failure_count": post_rerank_failure,
        "post_rerank_failure_reasons": reasons,
        "evaluations": evaluations,
    }
