"""Run the deterministic/HCX agent against the approved Gold JSONL contract."""

from __future__ import annotations

import argparse
import json
import time
from decimal import Decimal, InvalidOperation
from pathlib import Path

from disclosure_db.agent import AgentSettings, DisclosureAgent
from disclosure_db.agent_contracts import to_jsonable


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--overlay", type=Path)
    parser.add_argument("--gold", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--limit", type=int, default=20)
    args = parser.parse_args()
    agent = DisclosureAgent(AgentSettings(args.database, args.overlay))
    evaluations: list[dict[str, object]] = []
    for line_no, line in enumerate(args.gold.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        record = json.loads(line)
        started = time.perf_counter()
        try:
            answer = agent.answer(str(record["question"]), as_of=record.get("as_of"), limit=args.limit)
            elapsed_ms = round((time.perf_counter() - started) * 1000, 2)
            expected = record.get("answerability") == "answerable"
            expected_evidence = {str(item.get("evidence_id")) for item in record.get("evidence", []) if item.get("evidence_id")}
            selected_evidence = set(answer.citation_ids)
            citation_recall = (len(expected_evidence & selected_evidence) / len(expected_evidence)) if expected_evidence else None
            expected_value = record.get("answer", {}).get("value") if isinstance(record.get("answer"), dict) else None
            expected_text = record.get("answer", {}).get("text") if isinstance(record.get("answer"), dict) else None
            numeric_match = None
            if expected_value is not None:
                try:
                    numeric_match = any(Decimal(str(expected_value)) == Decimal(str(value)) for value in answer.numeric_values)
                except (InvalidOperation, TypeError):
                    numeric_match = False
            text_match = (str(expected_text).casefold() in answer.answer.casefold()) if expected_text else None
            evaluations.append({
                "question_id": record.get("question_id", f"line-{line_no}"),
                "expected_answerable": expected,
                "answerable": answer.answerable,
                "verified": answer.verified,
                "status": "pass" if answer.verified and answer.answerable == expected and (not expected or citation_recall == 1.0 and (numeric_match is not False) and (text_match is not False)) else "review",
                "citation_recall": citation_recall,
                "numeric_match": numeric_match,
                "text_match": text_match,
                "latency_ms": elapsed_ms,
                "reason_codes": answer.reason_codes,
                "citation_ids": answer.citation_ids,
                "answer": answer.answer,
            })
        except Exception as exc:  # keep the evaluator auditable even with one malformed record
            evaluations.append({"question_id": record.get("question_id", f"line-{line_no}"), "status": "error", "error": str(exc)})
    answered = [item for item in evaluations if item.get("status") != "error"]
    output = {
        "database": str(args.database), "overlay": str(args.overlay) if args.overlay else None,
        "gold": str(args.gold), "count": len(evaluations),
        "verified_count": sum(bool(item.get("verified")) for item in answered),
        "answerability_match_count": sum(item.get("status") == "pass" for item in answered),
        "error_count": sum(item.get("status") == "error" for item in evaluations),
        "latency_ms_p50": sorted(float(item["latency_ms"]) for item in answered if "latency_ms" in item)[len([item for item in answered if "latency_ms" in item]) // 2] if any("latency_ms" in item for item in answered) else None,
        "evaluations": evaluations,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(to_jsonable(output), ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({key: value for key, value in output.items() if key != "evaluations"}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
