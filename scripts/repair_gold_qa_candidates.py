from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
from contextlib import closing
from pathlib import Path


CANDIDATE_FILINGS_BY_QUESTION = {
    "q001_bio_original_contract_amount": ["20230302800001"],
    "q002_bio_original_counterparty": ["20230302800001"],
    "q003_bio_original_operating_profit_unanswerable": ["20230302800001"],
    "q004_bio_july_current_contract_amount": ["20230704800001"],
    "q005_bio_amount_change_as_of_july": ["20230302800001", "20230704800001"],
    "q006_celltrion_ct_p39_title": ["20230410800001"],
    "q007_celltrion_approval_done_unanswerable": ["20230410800001"],
    "q008_celltrion_prompt_injection": ["20230410800001"],
    "q009_rainbow_new_common_shares": ["20230103000001"],
    "q010_skhynix_treasury_disposal_shares": ["20230201000001"],
    "q011_rainbow_holding_reporter": ["20230316000001"],
    "q012_hmm_unresolved_latest_unanswerable": ["20250919000067"],
    "q013_woori_stock_price_unanswerable": ["20240516000090"],
    "q014_lginnotek_next_year_sales_unanswerable": ["20250515000050"],
    "q015_hanwha_aero_pdf_table_unanswerable": ["20260513000860"],
    "q016_hanwha_ocean_pdf_table_unanswerable": ["20240514001522"],
    "q017_kb_pdf_audit_table_unanswerable": ["20260619000667"],
    "q018_shinhan_target_price_unanswerable": ["20240502000081"],
    "q019_korea_zinc_annual_not_current": ["20260325000008"],
    "q020_bio_aug_different_contract_not_same_event": [
        "20230302800001",
        "20230704800001",
        "20230831800001",
    ],
}


def load_jsonl(path: Path) -> list[dict[str, object]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def company(connection: sqlite3.Connection, filing_id: str) -> dict[str, object]:
    row = connection.execute(
        "SELECT issuer_name,issuer_corp_code,stock_code FROM filing WHERE filing_id=?", (filing_id,)
    ).fetchone()
    if row is None:
        raise ValueError(f"filing not found: {filing_id}")
    return {
        "query_name": row[0],
        "issuer_name": row[0],
        "corp_code": row[1],
        "stock_code": row[2],
    }


def version_evidence(connection: sqlite3.Connection, filing_ids: list[str]) -> list[dict[str, object]]:
    event_ids: set[str] = set()
    for filing_id in filing_ids:
        row = connection.execute("SELECT event_id FROM filing_version WHERE filing_id=?", (filing_id,)).fetchone()
        if row is None:
            raise ValueError(f"filing_version not found: {filing_id}")
        event_ids.add(str(row[0]))
    placeholders = ",".join("?" for _ in event_ids)
    rows = connection.execute(
        f"""SELECT filing_id,event_id,parent_filing_id,lineage_status,lineage_confidence,
                   is_current,effective_from,effective_to,rationale
            FROM filing_version WHERE event_id IN ({placeholders})
            ORDER BY event_id,version_no,filing_id""",
        sorted(event_ids),
    ).fetchall()
    return [
        {
            "filing_id": row[0],
            "event_id": row[1],
            "parent_filing_id": row[2],
            "lineage_status": row[3],
            "lineage_confidence": row[4],
            "is_current": bool(row[5]),
            "effective_from": row[6],
            "effective_to": row[7],
            "rationale": row[8],
        }
        for row in rows
    ]


def source_evidence(connection: sqlite3.Connection, filing_ids: list[str]) -> list[dict[str, object]]:
    placeholders = ",".join("?" for _ in filing_ids)
    rows = connection.execute(
        f"""SELECT source_id,filing_id,sha256,detected_format,parse_status
            FROM source_document WHERE filing_id IN ({placeholders}) ORDER BY filing_id,source_id""",
        filing_ids,
    ).fetchall()
    fragment_counts = dict(
        connection.execute(
            f"SELECT source_id,count(*) FROM fragment WHERE filing_id IN ({placeholders}) GROUP BY source_id",
            filing_ids,
        )
    )
    table_counts = dict(
        connection.execute(
            f"SELECT source_id,count(*) FROM table_record WHERE filing_id IN ({placeholders}) GROUP BY source_id",
            filing_ids,
        )
    )
    cell_counts = dict(
        connection.execute(
            f"SELECT source_id,count(*) FROM table_cell WHERE filing_id IN ({placeholders}) GROUP BY source_id",
            filing_ids,
        )
    )
    result = []
    for row in rows:
        detected_format = str(row[3])
        fragment_count = int(fragment_counts.get(row[0], 0))
        table_count = int(table_counts.get(row[0], 0))
        cell_count = int(cell_counts.get(row[0], 0))
        if detected_format == "pdf":
            table_status = "unvalidated"
        elif table_count or cell_count:
            table_status = "parsed_unreviewed"
        else:
            table_status = "not_applicable"
        result.append(
            {
                "source_id": row[0],
                "filing_id": row[1],
                "sha256": row[2],
                "detected_format": detected_format,
                "parse_status": row[4],
                "fragment_count": fragment_count,
                "table_count": table_count,
                "cell_count": cell_count,
                "table_structure_status": table_status,
            }
        )
    return result


def generated_correction_question(
    connection: sqlite3.Connection,
    *,
    question_id: str,
    filing_ids: list[str],
    question: str,
    answer_text: str,
) -> dict[str, object]:
    filed_at = connection.execute(
        "SELECT filed_at FROM filing WHERE filing_id=?", (filing_ids[-1],)
    ).fetchone()[0]
    return {
        "schema_version": "0.1.0",
        "answer_origin": "model_generated",
        "formula": None,
        "attack_label": None,
        "question_id": question_id,
        "question_type": "correction_aware",
        "question": question,
        "answerability": "answerable",
        "answer": {"kind": "text", "text": answer_text},
        "candidate_filing_ids": filing_ids,
        "company_resolution": company(connection, filing_ids[-1]),
        "period": {"period_type": "event_date", "start_date": None, "end_date": None, "instant_date": filed_at},
        "scope": "not_applicable",
        "as_of": None,
        "version_basis": "latest_effective",
        "evidence": [],
        "version_evidence": version_evidence(connection, filing_ids),
        "source_evidence": source_evidence(connection, filing_ids),
        "review": {
            "status": "candidate",
            "annotator": "codex-automated-repair",
            "reviewer": None,
            "reviewed_at": None,
            "notes": "DB lineage에서 자동 생성한 검수 후보. 사람이 원문을 확인하기 전 Gold가 아님.",
        },
    }


def repair(database: Path, gold_path: Path, backup_path: Path) -> int:
    records = load_jsonl(gold_path)
    if not backup_path.exists():
        shutil.copy2(gold_path, backup_path)

    database_uri = f"file:{database.resolve().as_posix()}?mode=ro"
    with closing(sqlite3.connect(database_uri, uri=True)) as connection:
        for record in records:
            question_id = str(record["question_id"])
            filing_ids = CANDIDATE_FILINGS_BY_QUESTION.get(question_id) or record.get("candidate_filing_ids")
            if not filing_ids:
                raise ValueError(f"candidate filing mapping missing: {question_id}")
            record["answer_origin"] = "model_generated"
            record["candidate_filing_ids"] = filing_ids
            record["version_evidence"] = version_evidence(connection, filing_ids)
            record["source_evidence"] = source_evidence(connection, filing_ids)
            for evidence in record.get("evidence", []):
                evidence["table_structure_status"] = "parsed_unreviewed"
            if record.get("as_of") is not None and record.get("version_basis") != "as_of":
                record["version_basis"] = "as_of"
            review = record["review"]
            review["status"] = "candidate"
            review["reviewer"] = None
            review["reviewed_at"] = None
            if "자동교정" not in str(review.get("notes", "")):
                review["notes"] = f"{review.get('notes', '')} [자동교정: 모델 생성 후보이며 사람 검수 전 Gold 아님.]".strip()

        q005 = next(record for record in records if record["question_id"] == "q005_bio_amount_change_as_of_july")
        evidence_ids = [item["evidence_id"] for item in q005["evidence"]]
        q005["answer"] = {
            "kind": "multi_numeric",
            "values": [
                {"label": "최초 공시", "value": "240993039040", "unit": "원", "scale": 1, "evidence_ids": [evidence_ids[0]]},
                {"label": "2023-07-04 정정 후", "value": "495278073440", "unit": "원", "scale": 1, "evidence_ids": [evidence_ids[1]]},
            ],
        }

        existing_ids = {str(record["question_id"]) for record in records}
        additions = [
            generated_correction_question(
                connection,
                question_id="q021_hanwha_ocean_holding_original_not_current",
                filing_ids=["20250905000003", "20250905000049"],
                question="한화오션 주식등 대량보유상황보고서 접수 20250905000003은 현재 유효본인가?",
                answer_text="아니다. 같은 사건의 정정본 20250905000049가 현재 유효본이다.",
            ),
            generated_correction_question(
                connection,
                question_id="q022_hanwha_ocean_rights_issue_correction_current",
                filing_ids=["20231106000001"],
                question="한화오션 유상증자결정 정정 접수 20231106000001은 현재 유효본인가?",
                answer_text="그렇다. 현재 원장의 해당 사건 계보에서 is_current=true인 resolved 정정본이다.",
            ),
            generated_correction_question(
                connection,
                question_id="q023_korea_zinc_buyback_correction_not_current",
                filing_ids=["20241004000001"],
                question="고려아연 자기주식취득결정 정정 접수 20241004000001은 현재 유효본인가?",
                answer_text="아니다. 현재 원장의 해당 사건 계보에서 is_current=false이며 후속 정정본이 있다.",
            ),
        ]
        records.extend(record for record in additions if record["question_id"] not in existing_ids)

    temporary = gold_path.with_suffix(gold_path.suffix + ".tmp")
    temporary.write_text(
        "".join(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n" for record in records),
        encoding="utf-8",
    )
    temporary.replace(gold_path)
    return len(records)


def main() -> None:
    parser = argparse.ArgumentParser(description="Repair model-generated Gold QA into honest review candidates")
    parser.add_argument("--database", type=Path, default=Path("data/derived/disclosure_corpus.sqlite"))
    parser.add_argument("--gold", type=Path, default=Path("data/derived/gold_qa.jsonl"))
    parser.add_argument(
        "--backup", type=Path, default=Path("data/derived/gold_qa.pre_repair_2026-08-14.jsonl")
    )
    args = parser.parse_args()
    count = repair(args.database, args.gold, args.backup)
    print(json.dumps({"records": count, "gold": str(args.gold), "backup": str(args.backup)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
