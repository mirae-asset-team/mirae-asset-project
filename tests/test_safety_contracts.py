from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from disclosure_db.lineage import build_lineage
from disclosure_db.parsers import extract_first_submission_date
from disclosure_db.pipeline import query_database
from disclosure_db.model_review import compare_reviews
from scripts.review_gold_with_models import parse_json_output
from disclosure_db.schema import create_indexes, create_schema
from disclosure_db.serving import fetch_validated_facts, fetch_validated_financial_facts
from disclosure_db.evaluation import load_evaluation_contract
from disclosure_db.gold_validation import validate_record_contract
from disclosure_db.migration import apply_semantic_migration
from disclosure_db.retrieval_evaluation import compile_retrieval_query
from scripts.validate_database import validate


def insert_filing(
    connection: sqlite3.Connection,
    filing_id: str,
    *,
    corrected: bool = False,
    reporter_name: str = "제출인",
) -> None:
    connection.execute(
        """INSERT INTO filing(
               filing_id,doc_id,issuer_corp_code,stock_code,issuer_name,listed_name,reporter_name,industry,sector,
               doc_group,doc_subtype_raw,doc_subtype_normalized,report_name_raw,report_name_normalized,
               filed_at,base_year,base_month,is_correction,file_format_declared,source_file_count
           ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            filing_id, f"doc_{filing_id}", "00000001", "000001", "테스트", "테스트", reporter_name, "IT", "IT",
            "exchange", "supply", "supply", ("[기재정정]" if corrected else "") + "공급계약",
            "공급계약", "2024-01-02" if corrected else "2024-01-01", None, None, int(corrected), "xml", 1,
        ),
    )


def insert_source(connection: sqlite3.Connection, filing_id: str, *, image_count: int = 0) -> str:
    source_id = f"src_{filing_id}"
    connection.execute(
        """INSERT INTO source_document(
               source_id,filing_id,role,source_path,extension,detected_format,declared_encoding,
               detected_encoding,sha256,byte_size,modified_at,parser_name,parser_version,
               parse_status,strict_xml_ok,parser_error_count,warnings_json,coverage_json
           ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            source_id, filing_id, "main", f"raw/{filing_id}.xml", ".xml", "dart_xml", "utf-8", "utf-8",
            filing_id.ljust(64, "0")[:64], 10, "2024-01-01T00:00:00+00:00", "test", "1", "success", 1, 0,
            "[]", json.dumps({"extracted_text_chars": 10, "image_reference_count": image_count}),
        ),
    )
    return source_id


def insert_fragment(
    connection: sqlite3.Connection,
    filing_id: str,
    source_id: str,
    sequence_no: int,
    text: str,
    *,
    rowid: int | None = None,
) -> None:
    columns = "rowid," if rowid is not None else ""
    placeholders = "?," if rowid is not None else ""
    values = [rowid] if rowid is not None else []
    values.extend(
        [
            f"ev_{filing_id}_{sequence_no}", filing_id, source_id, "paragraph", sequence_no, "[]", None, None,
            json.dumps({"sequence": sequence_no}), text, text, "1",
        ]
    )
    connection.execute(
        f"""INSERT INTO fragment({columns}evidence_id,filing_id,source_id,fragment_type,sequence_no,
                   section_path_json,page_no,table_id,locator_json,text_raw,text_normalized,parser_version)
               VALUES({placeholders}?,?,?,?,?,?,?,?,?,?,?,?)""",
        values,
    )


def insert_version(connection: sqlite3.Connection, filing_id: str, status: str) -> None:
    event_id = f"evt_{filing_id}"
    connection.execute(
        "INSERT INTO filing_event(event_id,issuer_corp_code,doc_group,event_key,lineage_method) VALUES(?,?,?,?,?)",
        (event_id, "00000001", "exchange", filing_id, "test"),
    )
    connection.execute(
        """INSERT INTO filing_version(
               filing_id,event_id,version_no,parent_filing_id,lineage_status,lineage_confidence,
               effective_from,effective_to,is_current,rationale)
           VALUES(?,?,?,?,?,?,?,?,?,?)""",
        (filing_id, event_id, 1, None, status, "none" if status == "unresolved" else "high", "2024-01-01", None, 1, "test"),
    )


class SafetyContractTests(unittest.TestCase):
    def test_retrieval_query_is_fts_safe_and_keeps_domain_terms(self) -> None:
        query = compile_retrieval_query(
            "삼성바이오로직스가 2023-03-02에 최초 공시한 계약금액(원)은 얼마인가?",
            company_names=["삼성바이오로직스"],
        )
        self.assertIn('"계약금액"', query)
        self.assertNotIn("삼성바이오로직스", query)
        self.assertNotIn("2023", query)
        connection = sqlite3.connect(":memory:")
        connection.execute("CREATE VIRTUAL TABLE docs USING fts5(text)")
        connection.execute("INSERT INTO docs VALUES('계약금액 | 1000원')")
        self.assertEqual(connection.execute("SELECT count(*) FROM docs WHERE docs MATCH ?", (query,)).fetchone()[0], 1)
        connection.close()

    def test_semantic_migration_is_audited_and_fts_stays_in_sync(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            database = Path(temp) / "semantic.sqlite"
            connection = sqlite3.connect(database)
            create_schema(connection)
            connection.execute(
                """INSERT INTO pipeline_run(
                       run_id,started_at,finished_at,parser_name,parser_version,corpus_root,
                       manifest_sha256,status,stats_json
                   ) VALUES('run','2024-01-01','2024-01-01','test','1','fixture','x','success','{}')"""
            )
            insert_filing(connection, "20240101000001")
            source_id = insert_source(connection, "20240101000001")
            insert_fragment(connection, "20240101000001", source_id, 0, "계약금액 1000원")
            insert_version(connection, "20240101000001", "root")
            connection.execute(
                """INSERT INTO quality_issue VALUES(
                       'legacy','run','warning','consistency','filing','20240101000001',
                       'lineage_missing_original','legacy alias','{}')"""
            )
            connection.commit()
            connection.close()

            result = apply_semantic_migration(
                database,
                source_database=database.with_name("structural.sqlite"),
                migration_sql=Path("sql/sqlite_semantic_layer_v1.sql"),
                rebuild_lineage=False,
                rebuild_fts=True,
            )
            self.assertEqual(result["quick_check"], "ok")
            self.assertEqual(result["foreign_key_violations"], 0)
            self.assertEqual(result["semantic_counts"]["schema_migration"], 1)
            self.assertIn("fragment_fts_ai", result["triggers"])
            connection = sqlite3.connect(database)
            self.assertEqual(
                connection.execute(
                    "SELECT count(*) FROM quality_issue WHERE rule_id='lineage_missing_original'"
                ).fetchone()[0],
                0,
            )
            connection.close()
            validation = validate(database, require_semantic_v1=True)
            self.assertTrue(validation["semantic_schema_gate_passed"], validation["semantic_schema_invariants"])
            attestation = Path(temp) / "migration.json"
            attestation.write_text(json.dumps(result), encoding="utf-8")
            attested_validation = validate(
                database,
                require_semantic_v1=True,
                integrity_mode="attested",
                integrity_attestation=attestation,
            )
            self.assertEqual(attested_validation["integrity_check"], "ok")
            self.assertTrue(attested_validation["integrity_attestation"]["schema_sha256_match"])

            connection = sqlite3.connect(database)
            insert_fragment(connection, "20240101000001", source_id, 1, "추가 검색 문장")
            self.assertEqual(
                connection.execute(
                    "SELECT count(*) FROM fragment_fts WHERE fragment_fts MATCH '추가'"
                ).fetchone()[0],
                1,
            )
            connection.execute(
                """INSERT INTO fact VALUES(
                       'orphan','20240101000001','event_kv_candidate','테스트','금액','1000','원','test','candidate'
                   )"""
            )
            with self.assertRaisesRegex(sqlite3.IntegrityError, "requires cell evidence"):
                connection.execute("UPDATE fact SET validation_status='validated' WHERE fact_id='orphan'")
            connection.close()

    def test_postgresql_candidate_covers_ledger_and_semantic_grains(self) -> None:
        ddl = Path("sql/postgresql_schema.sql").read_text(encoding="utf-8").lower()
        for table in (
            "schema_migration", "pipeline_run", "filing", "source_document", "parser_error",
            "fragment", "table_record", "table_cell", "fact", "fact_evidence",
            "financial_fact", "financial_fact_evidence", "filing_event", "filing_version",
            "quality_issue",
        ):
            self.assertIn(f"create table {table}", ddl)
        self.assertIn("filing_version_one_current_idx", ddl)
        self.assertIn("financial_fact_evidence_same_filing", ddl)

    def test_model_review_parser_accepts_fenced_json_and_control_newline(self) -> None:
        raw = '''```json
{"question_id":"q1","answerability":"uncertain","answer":{"kind":"uncertain","values":[],"unit":null},"support_verdict":"insufficient","selected_evidence_ids":[],"confidence":"low","reason":"첫 줄
둘째 줄"}
```'''
        self.assertEqual(parse_json_output(raw)["question_id"], "q1")

    def test_model_review_parser_reads_grok_structured_output_envelope(self) -> None:
        contract = {
            "question_id": "q1", "answerability": "uncertain",
            "answer": {"kind": "uncertain", "values": [], "unit": None},
            "support_verdict": "insufficient", "selected_evidence_ids": [],
            "confidence": "low", "reason": "근거 부족",
        }
        raw = json.dumps({"text": json.dumps(contract), "structuredOutput": contract})
        self.assertEqual(parse_json_output(raw), contract)

    def test_multi_model_review_does_not_promote_text_by_consensus(self) -> None:
        record = {
            "question_id": "q_text", "answerability": "answerable",
            "answer": {"kind": "text", "text": "적정의견"},
        }
        reviews = [
            {
                "answerability": "answerable", "answer": {"kind": "text", "values": ["적정의견"]},
                "support_verdict": "supported",
            },
            {
                "answerability": "answerable", "answer": {"kind": "text", "values": ["적정의견"]},
                "support_verdict": "supported",
            },
        ]
        result = compare_reviews(record, reviews)
        self.assertTrue(result["answerability_agreement"])
        self.assertFalse(result["silver_auto_pass"])

    def test_multi_model_review_requires_three_exact_numeric_reviewers_for_silver(self) -> None:
        record = {
            "question_id": "q_num", "answerability": "answerable",
            "answer": {"kind": "numeric", "value": "2999999", "unit": "원", "scale": 1},
        }
        reviews = [
            {
                "answerability": "answerable", "answer": {"kind": "numeric", "values": ["2,999,999"]},
                "support_verdict": "supported",
            },
            {
                "answerability": "answerable", "answer": {"kind": "numeric", "values": ["2999999"]},
                "support_verdict": "supported",
            },
        ]
        result = compare_reviews(record, reviews)
        self.assertTrue(result["exact_numeric_agreement"])
        self.assertFalse(result["silver_auto_pass"])
        reviews.append(reviews[0])
        result = compare_reviews(record, reviews)
        self.assertTrue(result["silver_auto_pass"])
    def test_first_submission_date_requires_explicit_correction_label(self) -> None:
        self.assertIsNone(extract_first_submission_date(["공시서류제출일 2024년 01월 01일"]))
        self.assertEqual(extract_first_submission_date(["최초제출일 2024년 01월 01일"]), "2024-01-01")
        self.assertEqual(extract_first_submission_date(["정정관련 공시서류제출일 2024.01.01"]), "2024-01-01")

    def test_lineage_reads_explicit_original_date_after_fragment_40(self) -> None:
        connection = sqlite3.connect(":memory:")
        create_schema(connection)
        insert_filing(connection, "20240101000001")
        insert_filing(connection, "20240102000002", corrected=True)
        original_source = insert_source(connection, "20240101000001")
        correction_source = insert_source(connection, "20240102000002")
        insert_fragment(connection, "20240101000001", original_source, 0, "원본")
        for sequence in range(41):
            insert_fragment(connection, "20240102000002", correction_source, sequence, f"일반 문단 {sequence}")
        insert_fragment(connection, "20240102000002", correction_source, 41, "최초제출일 2024년 01월 01일")
        build_lineage(connection)
        row = connection.execute(
            "SELECT lineage_status,parent_filing_id FROM filing_version WHERE filing_id='20240102000002'"
        ).fetchone()
        self.assertEqual(row, ("resolved", "20240101000001"))

    def test_safe_search_excludes_unresolved_and_handles_sparse_rowids(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            database = Path(temp) / "safe.sqlite"
            connection = sqlite3.connect(database)
            create_schema(connection)
            insert_filing(connection, "20240102000002", corrected=True)
            source_id = insert_source(connection, "20240102000002", image_count=2)
            insert_fragment(connection, "20240102000002", source_id, 0, "계약금액 1000원", rowid=7)
            insert_version(connection, "20240102000002", "unresolved")
            create_indexes(connection)
            connection.commit()
            connection.close()

            self.assertEqual(query_database(database, "계약금액", company="테스트"), [])
            audit_rows = query_database(database, "계약금액", company="테스트", include_unsafe=True)
            self.assertEqual(len(audit_rows), 1)
            self.assertTrue(audit_rows[0]["requires_visual_verification"])
            self.assertEqual(audit_rows[0]["lineage_status"], "unresolved")
            self.assertEqual(
                query_database(
                    database, "계약금액", include_unsafe=True,
                    filing_ids=["does-not-exist"],
                ),
                [],
            )

    def test_fetch_validated_facts_never_returns_candidates(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            database = Path(temp) / "facts.sqlite"
            connection = sqlite3.connect(database)
            create_schema(connection)
            insert_filing(connection, "20240101000001")
            source_id = insert_source(connection, "20240101000001")
            insert_version(connection, "20240101000001", "root")
            connection.execute(
                """INSERT INTO table_record(table_id,filing_id,source_id,sequence_no,section_path_json,caption,
                       unit_text,row_count,column_count,parse_status,locator_json)
                   VALUES('tbl',?,?,?,?,?,?,?,?,?,?)""",
                ("20240101000001", source_id, 0, "[]", None, "원", 1, 2, "success", "{}"),
            )
            connection.execute(
                """INSERT INTO table_cell(evidence_id,table_id,source_id,filing_id,row_index,column_index,rowspan,
                       colspan,cell_kind,row_header_path_json,column_header_path_json,locator_json,text_raw,
                       text_normalized,parser_version)
                   VALUES('cell','tbl',?,?,0,1,1,1,'data','[]','[]','{}','1000','1000','1')""",
                (source_id, "20240101000001"),
            )
            for status in ("candidate", "validated"):
                fact_id = f"fact_{status}"
                connection.execute(
                    "INSERT INTO fact VALUES(?,?,?,?,?,?,?,?,?)",
                    (fact_id, "20240101000001", "event_kv_candidate", "테스트", "계약금액", "1000", "원", "test", "candidate"),
                )
                connection.execute("INSERT INTO fact_evidence VALUES(?, 'cell')", (fact_id,))
                if status == "validated":
                    connection.execute(
                        "UPDATE fact SET validation_status='validated' WHERE fact_id=?", (fact_id,)
                    )
            connection.commit()
            connection.close()

            rows = fetch_validated_facts(database, filing_id="20240101000001")
            self.assertEqual([row["fact_id"] for row in rows], ["fact_validated"])

    def test_company_resolution_includes_disclosure_reporter(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            database = Path(temp) / "reporter.sqlite"
            connection = sqlite3.connect(database)
            create_schema(connection)
            insert_filing(connection, "20240101000001", reporter_name="삼성전자")
            source_id = insert_source(connection, "20240101000001")
            insert_fragment(connection, "20240101000001", source_id, 0, "보유주식등의 수 100주")
            insert_version(connection, "20240101000001", "root")
            create_indexes(connection)
            connection.commit()
            connection.close()

            rows = query_database(database, "보유주식등의", company="삼성전자")
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["reporter_name"], "삼성전자")

    def test_financial_fact_is_a_separate_schema_grain(self) -> None:
        connection = sqlite3.connect(":memory:")
        create_schema(connection)
        columns = {row[1] for row in connection.execute("PRAGMA table_info(financial_fact)")}
        self.assertTrue(
            {"account_name_raw", "statement_type", "scope", "period_start", "period_end", "scale", "currency", "validation_status"}
            <= columns
        )
        self.assertEqual(connection.execute("SELECT count(*) FROM financial_fact").fetchone()[0], 0)

    def test_financial_fact_cannot_be_validated_without_cell_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            database = Path(temp) / "financial.sqlite"
            connection = sqlite3.connect(database)
            create_schema(connection)
            insert_filing(connection, "20240101000001")
            insert_source(connection, "20240101000001")
            insert_version(connection, "20240101000001", "root")
            with self.assertRaisesRegex(sqlite3.IntegrityError, "promoted after evidence"):
                connection.execute(
                    """INSERT INTO financial_fact(
                           financial_fact_id,filing_id,account_id,account_name_raw,statement_type,scope,
                           period_type,period_start,period_end,instant_date,value_numeric,currency,scale,
                           unit_raw,extraction_method,validation_status)
                       VALUES('ff_orphan','20240101000001','Revenue','매출액','IS','consolidated',
                              'duration','2023-01-01','2023-12-31',NULL,'1000','KRW',1,'원','human','validated')"""
                )
            connection.commit()
            connection.close()
            self.assertEqual(fetch_validated_financial_facts(database, filing_id="20240101000001"), [])

    def test_validator_separates_structure_smoke_relevance_and_semantics(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            database = Path(temp) / "validation.sqlite"
            connection = sqlite3.connect(database)
            create_schema(connection)
            insert_filing(connection, "20240101000001")
            source_id = insert_source(connection, "20240101000001")
            insert_fragment(connection, "20240101000001", source_id, 0, "테스트 공시")
            insert_version(connection, "20240101000001", "root")
            create_indexes(connection)
            connection.commit()
            connection.close()

            result = validate(database)
            self.assertIn("structure_gate_passed", result)
            self.assertNotIn("hard_quality_gate_passed", result)
            self.assertTrue(result["structure_gate_passed"])
            self.assertFalse(result["retrieval_smoke_gate_passed"])
            self.assertEqual(result["retrieval_relevance_gate_status"], "not_evaluated_without_human_gold")
            self.assertTrue(str(result["semantic_answer_gate_status"]).startswith("not_evaluated"))
            self.assertEqual(result["evaluation_contract"]["version"], "0.3.0")
            self.assertEqual(result["evaluation_contract"]["question_type_count"], 8)
            strict_result = validate(database, expected_filings=4204, expected_sources=4622)
            self.assertFalse(strict_result["structure_gate_passed"])
            self.assertGreater(strict_result["expected_counts"]["mismatches"]["filings"], 0)

    def test_evaluation_contract_requires_semantic_gold_fields(self) -> None:
        contract = load_evaluation_contract(Path("config/evaluation_contract.json"))
        schema = json.loads(
            Path("config/gold_annotation_schema.json").read_text(encoding="utf-8")
        )
        self.assertEqual(contract["gold_annotation_schema"], "gold_annotation_schema.json")
        self.assertTrue(
            {"company_resolution", "period", "scope", "as_of", "version_basis", "formula", "attack_label", "answer_origin"}.issubset(
                schema["required"]
            )
        )
        self.assertTrue(
            {"annotator", "reviewer"}.issubset(schema["properties"]["review"]["required"])
        )

    def test_gold_review_contract_blocks_model_or_self_approval(self) -> None:
        record = {
            "schema_version": "0.1.0",
            "question_id": "q_test",
            "question_type": "correction_aware",
            "question": "테스트 질문",
            "answerability": "answerable",
            "answer": {"kind": "text", "text": "테스트"},
            "answer_origin": "model_generated",
            "candidate_filing_ids": ["20240101000001"],
            "company_resolution": None,
            "period": {"period_type": "event_date", "start_date": None, "end_date": None, "instant_date": "2024-01-01"},
            "scope": "not_applicable",
            "as_of": None,
            "version_basis": "latest_effective",
            "formula": None,
            "attack_label": None,
            "evidence": [],
            "version_evidence": [{"filing_id": "20240101000001"}],
            "source_evidence": [{"filing_id": "20240101000001"}],
            "review": {"status": "approved", "annotator": "same", "reviewer": "same", "reviewed_at": None, "notes": ""},
        }
        rule_ids = {item["rule_id"] for item in validate_record_contract(record)}
        self.assertIn("model_answer_not_candidate", rule_ids)
        self.assertIn("approved_answer_not_human", rule_ids)
        self.assertIn("self_approved", rule_ids)
        self.assertIn("approved_without_timestamp", rule_ids)

    def test_gold_qa_artifact_is_schema_complete_and_ids_exist(self) -> None:
        gold = Path("data/derived/gold_qa.jsonl")
        database = Path("data/derived/disclosure_corpus.sqlite")
        if not gold.exists() or not database.exists():
            self.skipTest("full gold artifact or structural DB is not in this workspace")
        from scripts.validate_gold import validate

        result = validate(gold, Path("config/gold_annotation_schema.json"), database)
        self.assertTrue(result["ok"], result["errors"])
        self.assertTrue(result["candidate_review_ready"])
        self.assertTrue(result["gold_release_gate_passed"])
        self.assertEqual(result["status_counts"], {"approved": 23})
        self.assertEqual(result["answer_origin_counts"], {"human_verified": 23})
        self.assertGreaterEqual(int(result["rows"]), 20)


if __name__ == "__main__":
    unittest.main()
