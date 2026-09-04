from __future__ import annotations

from dataclasses import replace
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

import disclosure_db.embedding_chunks as chunk_module
from disclosure_db.embedding_chunks import (
    CHUNK_VERSION,
    ChunkConfig,
    build_embedding_chunks,
    count_tokens,
    explain_chunk_query_plans,
    load_chunk_config,
)
from disclosure_db.schema import create_indexes, create_schema


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _jsonl(path: Path) -> list[dict[str, object]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def _seed_database(path: Path) -> dict[str, tuple[str, str, tuple[str, ...], str | None]]:
    connection = sqlite3.connect(path)
    connection.executescript(
        """
        CREATE TABLE source_document(
            source_id TEXT PRIMARY KEY,
            filing_id TEXT NOT NULL,
            detected_format TEXT NOT NULL,
            parse_status TEXT NOT NULL
        );
        CREATE TABLE fragment(
            evidence_id TEXT PRIMARY KEY,
            filing_id TEXT NOT NULL,
            source_id TEXT NOT NULL,
            fragment_type TEXT NOT NULL,
            sequence_no INTEGER NOT NULL,
            section_path_json TEXT NOT NULL,
            page_no INTEGER,
            table_id TEXT,
            locator_json TEXT NOT NULL,
            text_raw TEXT NOT NULL,
            text_normalized TEXT NOT NULL,
            parser_version TEXT NOT NULL
        );
        CREATE TABLE table_record(
            table_id TEXT PRIMARY KEY,
            filing_id TEXT NOT NULL,
            source_id TEXT NOT NULL,
            sequence_no INTEGER NOT NULL,
            section_path_json TEXT NOT NULL,
            caption TEXT,
            unit_text TEXT,
            row_count INTEGER NOT NULL,
            column_count INTEGER NOT NULL,
            parse_status TEXT NOT NULL,
            locator_json TEXT NOT NULL
        );
        CREATE TABLE table_cell(
            evidence_id TEXT PRIMARY KEY,
            table_id TEXT NOT NULL,
            source_id TEXT NOT NULL,
            filing_id TEXT NOT NULL,
            row_index INTEGER NOT NULL,
            column_index INTEGER NOT NULL,
            rowspan INTEGER NOT NULL,
            colspan INTEGER NOT NULL,
            cell_kind TEXT NOT NULL,
            row_header_path_json TEXT NOT NULL,
            column_header_path_json TEXT NOT NULL,
            locator_json TEXT NOT NULL,
            text_raw TEXT NOT NULL,
            text_normalized TEXT NOT NULL,
            parser_version TEXT NOT NULL
        );
        CREATE INDEX idx_fragment_source ON fragment(source_id, sequence_no);
        CREATE INDEX idx_fragment_table_row ON fragment(table_id) WHERE table_id IS NOT NULL;
        CREATE INDEX idx_cell_table ON table_cell(table_id, row_index, column_index);
        """
    )
    connection.executemany(
        "INSERT INTO source_document VALUES(?,?,?,?)",
        [
            ("s_xml", "f_1", "dart_xml", "success"),
            ("s_html", "f_1", "viewer_html", "success"),
            ("s_pdf", "f_2", "pdf", "partial"),
        ],
    )
    evidence: dict[str, tuple[str, str, tuple[str, ...], str | None]] = {}

    def fragment(
        evidence_id: str,
        filing_id: str,
        source_id: str,
        fragment_type: str,
        sequence_no: int,
        section: list[str],
        text: str,
        *,
        table_id: str | None = None,
        row_index: int | None = None,
    ) -> None:
        locator = {"kind": fragment_type, "ordinal": sequence_no}
        if row_index is not None:
            locator["row"] = row_index
        connection.execute(
            "INSERT INTO fragment VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                evidence_id, filing_id, source_id, fragment_type, sequence_no,
                json.dumps(section, ensure_ascii=False), None, table_id,
                json.dumps(locator, sort_keys=True), text, text, "fixture-v1",
            ),
        )
        evidence[evidence_id] = (filing_id, source_id, tuple(section), table_id)

    fragment("ev_xml_a1", "f_1", "s_xml", "paragraph", 0, ["사업", "개요"], "알파 베타 감마 델타 입실론 제타 에타 세타")
    fragment("ev_xml_a2", "f_1", "s_xml", "paragraph", 1, ["사업", "개요"], "요타 카파 람다 뮤 뉴 크시 오미크론 파이")
    fragment("ev_xml_b1", "f_1", "s_xml", "paragraph", 2, ["사업", "위험"], "위험 요인 공급망 환율 금리 변동")
    fragment("ev_html_a1", "f_1", "s_html", "html_text", 0, ["계약"], "계약 상대방 금액 기간 변경 조건")
    fragment("ev_pdf_1", "f_2", "s_pdf", "pdf_block", 0, [], "PDF 추출 본문 표 구조 미검증")

    connection.execute(
        "INSERT INTO table_record VALUES(?,?,?,?,?,?,?,?,?,?,?)",
        (
            "tbl_1", "f_1", "s_xml", 0, json.dumps(["재무", "손익"], ensure_ascii=False),
            "요약손익계산서", "백만원", 9, 3, "success", '{"kind":"table"}',
        ),
    )

    def cell(row: int, column: int, kind: str, text: str, headers: list[str]) -> str:
        evidence_id = f"ev_cell_{row}_{column}"
        connection.execute(
            "INSERT INTO table_cell VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                evidence_id, "tbl_1", "s_xml", "f_1", row, column, 1, 1, kind,
                "[]", json.dumps(headers, ensure_ascii=False),
                json.dumps({"kind": "table_cell", "row": row, "column": column}),
                text, text, "fixture-v1",
            ),
        )
        evidence[evidence_id] = ("f_1", "s_xml", ("재무", "손익"), "tbl_1")
        return evidence_id

    cell(0, 0, "header", "계정", [])
    cell(0, 1, "header", "2024", [])
    cell(0, 2, "header", "2023", [])
    fragment("ev_row_0", "f_1", "s_xml", "table_row", 3, ["재무", "손익"], "계정 | 2024 | 2023", table_id="tbl_1", row_index=0)
    for row in range(1, 9):
        cell(row, 0, "header", f"계정{row}", ["계정"])
        cell(row, 1, "data", str(1000 + row), ["2024"])
        cell(row, 2, "data", str(900 + row), ["2023"])
        fragment(
            f"ev_row_{row}", "f_1", "s_xml", "table_row", 3 + row,
            ["재무", "손익"], f"계정{row} | {1000 + row} | {900 + row}",
            table_id="tbl_1", row_index=row,
        )
    connection.commit()
    connection.close()
    return evidence


def _add_large_narrative_corpus(path: Path, *, groups: int) -> None:
    connection = sqlite3.connect(path)
    connection.executemany(
        "INSERT INTO source_document VALUES(?,?,?,?)",
        [
            (f"bulk_{index:06d}", f"bulk_filing_{index:06d}", "dart_xml", "success")
            for index in range(groups)
        ],
    )
    connection.executemany(
        "INSERT INTO fragment VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
        [
            (
                f"bulk_ev_{index:06d}", f"bulk_filing_{index:06d}", f"bulk_{index:06d}",
                "paragraph", 0, '["bulk"]', None, None,
                '{"kind":"element","ordinal":0}', f"대용량 회귀 {index}", f"대용량 회귀 {index}",
                "fixture-v1",
            )
            for index in range(groups)
        ],
    )
    connection.commit()
    connection.close()


class EmbeddingChunkTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.database = self.root / "corpus.sqlite"
        self.evidence = _seed_database(self.database)
        self.config = ChunkConfig(
            target_tokens=24,
            max_tokens=32,
            overlap_tokens=4,
            batch_size=2,
            checkpoint_every_groups=1,
        )

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_default_config_has_required_chunk_v1_limits(self) -> None:
        config = load_chunk_config()
        self.assertEqual(config.chunk_version, "chunk-v1")
        self.assertEqual((config.target_tokens, config.max_tokens, config.overlap_tokens), (450, 700, 80))

    def test_build_preserves_source_and_enforces_format_section_table_and_evidence_contracts(self) -> None:
        before = _sha256(self.database)
        output = self.root / "output"
        result = build_embedding_chunks(self.database, output, config=self.config)
        self.assertEqual(result.status, "complete")
        self.assertEqual(_sha256(self.database), before)

        chunks = _jsonl(output / "chunks.jsonl")
        links = _jsonl(output / "chunk_evidence.jsonl")
        manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
        self.assertTrue(chunks)
        self.assertEqual({str(row["source_format"]) for row in chunks}, {"xml", "html", "pdf"})
        self.assertEqual(manifest["chunk_version"], CHUNK_VERSION)
        self.assertEqual(manifest["embedding"], {"status": "not_run", "model": None})
        self.assertEqual(manifest["summary"]["failure_count"], 0)
        self.assertGreater(manifest["summary"]["pdf_source_count"], 0)

        for chunk in chunks:
            self.assertLessEqual(int(chunk["token_count"]), self.config.max_tokens)
            self.assertEqual(int(chunk["token_count"]), count_tokens(str(chunk["text"])))
            self.assertEqual(str(chunk["text_sha256"]), hashlib.sha256(str(chunk["text"]).encode("utf-8")).hexdigest())
            source_keys = {self.evidence[str(evidence_id)] for evidence_id in chunk["evidence_ids"]}
            self.assertEqual({key[0] for key in source_keys}, {chunk["filing_id"]})
            self.assertEqual({key[1] for key in source_keys}, {chunk["source_id"]})
            self.assertEqual({key[2] for key in source_keys}, {tuple(chunk["section_path"])})
            if chunk["table_id"] is not None:
                self.assertEqual({key[3] for key in source_keys}, {chunk["table_id"]})

        pdf_chunks = [row for row in chunks if row["source_format"] == "pdf"]
        self.assertTrue(pdf_chunks)
        self.assertTrue(all(row["quality_status"] == "unverified" for row in pdf_chunks))
        self.assertTrue(all(row["table_structure_verified"] is False for row in pdf_chunks))

        table_chunks = [row for row in chunks if row["table_id"] == "tbl_1"]
        self.assertGreater(len(table_chunks), 1)
        for chunk in table_chunks:
            text = str(chunk["text"])
            self.assertIn("Caption : 요약손익계산서", text)
            self.assertIn("Header : 계정 | 2024 | 2023", text)
            self.assertIn("Unit : 백만원", text)
            self.assertTrue({"ev_cell_0_0", "ev_cell_0_1", "ev_cell_0_2", "ev_row_0"}.issubset(chunk["evidence_ids"]))
            self.assertTrue(chunk["table_structure_verified"])

        nested_links = {
            (str(chunk["chunk_id"]), str(evidence_id))
            for chunk in chunks for evidence_id in chunk["evidence_ids"]
        }
        file_links = {(str(row["chunk_id"]), str(row["evidence_id"])) for row in links}
        self.assertEqual(file_links, nested_links)

    def test_checkpoint_resume_is_byte_deterministic(self) -> None:
        resumed = self.root / "resumed"
        first = build_embedding_chunks(
            self.database, resumed, config=self.config, max_groups=2
        )
        self.assertEqual(first.status, "checkpointed")
        self.assertTrue((resumed / "checkpoint.json").is_file())
        second = build_embedding_chunks(
            self.database, resumed, config=self.config, resume=True
        )
        self.assertEqual(second.status, "complete")

        fresh = self.root / "fresh"
        build_embedding_chunks(self.database, fresh, config=self.config)
        for name in ("chunks.jsonl", "chunk_evidence.jsonl", "failures.jsonl"):
            self.assertEqual((resumed / name).read_bytes(), (fresh / name).read_bytes())
        chunks = _jsonl(resumed / "chunks.jsonl")
        self.assertEqual(len({str(row["chunk_id"]) for row in chunks}), len(chunks))

    def test_existing_output_requires_explicit_resume_or_overwrite(self) -> None:
        output = self.root / "existing"
        build_embedding_chunks(self.database, output, config=self.config)
        with self.assertRaises(FileExistsError):
            build_embedding_chunks(self.database, output, config=self.config)

    def test_query_plans_use_existing_schema_indexes_without_temp_sort(self) -> None:
        connection = sqlite3.connect(":memory:")
        create_schema(connection)
        create_indexes(connection, build_fts=False)
        plans = explain_chunk_query_plans(connection)
        connection.close()

        flattened = {name: " | ".join(details) for name, details in plans.items()}
        self.assertIn("sqlite_autoindex_source_document_1", flattened["source_page"])
        self.assertIn("idx_fragment_source", flattened["narrative_page"])
        self.assertIn("sqlite_autoindex_table_record_1", flattened["table_page"])
        self.assertIn("idx_cell_table", flattened["table_cell_page"])
        self.assertIn("idx_fragment_table_row", flattened["table_row_page"])
        self.assertTrue(all("TEMP B-TREE" not in detail for detail in flattened.values()))

    def test_large_max_groups_stops_keyset_queries_before_full_corpus_scan(self) -> None:
        _add_large_narrative_corpus(self.database, groups=5_000)
        statements: list[str] = []
        original_connection = chunk_module._readonly_connection

        def monitored_connection(path: Path) -> sqlite3.Connection:
            connection = original_connection(path)
            connection.set_trace_callback(statements.append)
            return connection

        output = self.root / "bounded"
        config = replace(self.config, batch_size=7, checkpoint_every_groups=50)
        with patch.object(chunk_module, "_readonly_connection", side_effect=monitored_connection):
            result = build_embedding_chunks(
                self.database,
                output,
                config=config,
                max_groups=3,
            )

        self.assertEqual(result.status, "checkpointed")
        self.assertEqual(result.summary["total_chunk_count"], 3)
        normalized = [" ".join(statement.split()).upper() for statement in statements]
        self.assertFalse(any("UNION ALL" in statement for statement in normalized))
        self.assertFalse(any("COUNT(" in statement for statement in normalized))
        self.assertFalse(any("FROM TABLE_RECORD" in statement for statement in normalized))
        narrative_pages = [
            statement for statement in normalized
            if "FROM FRAGMENT INDEXED BY IDX_FRAGMENT_SOURCE" in statement
        ]
        self.assertLessEqual(len(narrative_pages), 4)
        self.assertTrue(all("LIMIT 7" in statement for statement in narrative_pages))
        checkpoint = json.loads((output / "checkpoint.json").read_text(encoding="utf-8"))
        self.assertEqual(checkpoint["query_strategy"], "chunk-keyset-v1")
        self.assertEqual(checkpoint["phase_cursor"]["source_id"], "bulk_000002")

    def test_batch_and_checkpoint_tuning_do_not_change_chunk_bytes(self) -> None:
        small = self.root / "small-batch"
        large = self.root / "large-batch"
        build_embedding_chunks(
            self.database,
            small,
            config=replace(self.config, batch_size=2, checkpoint_every_groups=1),
        )
        build_embedding_chunks(
            self.database,
            large,
            config=replace(self.config, batch_size=11, checkpoint_every_groups=7),
        )
        for name in ("chunks.jsonl", "chunk_evidence.jsonl", "failures.jsonl"):
            self.assertEqual((small / name).read_bytes(), (large / name).read_bytes())


if __name__ == "__main__":
    unittest.main()
