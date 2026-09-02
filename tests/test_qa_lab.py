from __future__ import annotations

import json
import sqlite3
import tempfile
from pathlib import Path

import pytest

from disclosure_db.agent_contracts import CitationRef, VerifiedAnswer
from disclosure_db.api import create_app
from disclosure_db.qa_lab import QaLabStore, QaLabError
from disclosure_db.gold_validation import validate_record_contract
from disclosure_db.schema import create_schema


def _build_gold_corpus(path: Path) -> None:
    with sqlite3.connect(path) as connection:
        create_schema(connection)
        connection.execute(
            """INSERT INTO filing(
                filing_id,doc_id,issuer_corp_code,stock_code,issuer_name,listed_name,reporter_name,
                industry,sector,doc_group,doc_subtype_raw,doc_subtype_normalized,report_name_raw,
                report_name_normalized,filed_at,base_year,base_month,is_correction,
                file_format_declared,source_file_count
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                "20260310002820", "doc-1", "00126380", "005930", "삼성전자", "삼성전자",
                "삼성전자", "전자", "IT", "periodic", "annual", "annual", "사업보고서",
                "사업보고서", "2026-03-10", 2025, 12, 0, "xml", 1,
            ),
        )
        connection.execute(
            """INSERT INTO source_document(
                source_id,filing_id,role,source_path,extension,detected_format,declared_encoding,
                detected_encoding,sha256,byte_size,modified_at,parser_name,parser_version,
                parse_status,strict_xml_ok,parser_error_count,warnings_json,coverage_json
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                "src-1", "20260310002820", "primary", str(path.with_suffix(".xml")), ".xml",
                "dart_xml", "utf-8", "utf-8", "a" * 64, 100, "2026-03-10T00:00:00Z",
                "test", "1", "success", 1, 0, "[]", "{}",
            ),
        )
        connection.execute(
            """INSERT INTO table_record(
                table_id,filing_id,source_id,sequence_no,section_path_json,caption,unit_text,
                row_count,column_count,parse_status,locator_json
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            ("tbl-1", "20260310002820", "src-1", 1, "[]", "손익계산서", "백만원", 2, 2, "success", "{}"),
        )
        connection.execute(
            """INSERT INTO table_cell(
                evidence_id,table_id,source_id,filing_id,row_index,column_index,rowspan,colspan,
                cell_kind,row_header_path_json,column_header_path_json,locator_json,text_raw,
                text_normalized,parser_version
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                "ev-sales", "tbl-1", "src-1", "20260310002820", 1, 1, 1, 1, "data",
                '["매출액"]', '["2025"]', '{"kind":"table_cell","row":1,"column":1}',
                "333,605,938", "333605938", "1",
            ),
        )
        connection.execute(
            "INSERT INTO filing_event(event_id,issuer_corp_code,doc_group,event_key,lineage_method) VALUES (?,?,?,?,?)",
            ("event-1", "00126380", "periodic", "2025-annual", "test"),
        )
        connection.execute(
            """INSERT INTO filing_version(
                filing_id,event_id,version_no,parent_filing_id,lineage_status,lineage_confidence,
                effective_from,effective_to,is_current,rationale
            ) VALUES (?,?,?,?,?,?,?,?,?,?)""",
            ("20260310002820", "event-1", 1, None, "root", "high", "2026-03-10", None, 1, "original"),
        )
        connection.commit()


def test_store_rejects_unknown_verdict(tmp_path: Path) -> None:
    store = QaLabStore(tmp_path / "qa.sqlite")
    with pytest.raises(QaLabError, match="verdict_invalid"):
        store.add_review(
            reviewer="이정찬",
            question="삼성전자 매출액은?",
            answer="모름",
            verdict="pretty_good",
        )


def test_store_accumulates_reviews_perf_and_milestones(tmp_path: Path) -> None:
    store = QaLabStore(tmp_path / "qa.sqlite")
    review = store.add_review(
        reviewer="이정찬",
        question="삼성전자 최근 매출액은?",
        answer="333,605,938 백만원",
        verdict="correct",
        notes="사업보고서 일치",
        answerable=True,
        verified=True,
        latency_ms=87.3,
        request_id="abc",
        corpus_revision="semantic-v1",
        citations=[{"filing_id": "20260310002820", "report_name": "사업보고서"}],
        endpoint="/query",
    )
    perf = store.add_perf_run(
        recorder="이정찬",
        suite="financial_release",
        passed=856,
        failed=0,
        skipped=0,
        p50_ms=10.3,
        p95_ms=86.94,
        git_commit="d3e909a",
        notes="SQLite 20-request gate",
        metrics={"cases": 856, "accuracy": 1.0},
    )
    mile = store.add_milestone(
        author="이정찬",
        title="검수 데스크 시작",
        body="공개 서버 답을 사람이 대조해 누적한다.",
        category="process",
    )
    summary = store.summary()
    assert review["review_id"]
    assert perf["run_id"]
    assert mile["milestone_id"]
    assert summary["reviews"]["total"] == 1
    assert summary["reviews"]["by_verdict"]["correct"] == 1
    assert summary["perf_runs"]["total"] == 1
    assert summary["milestones"]["total"] == 1
    html = store.export_html()
    assert "삼성전자 최근 매출액은?" in html
    assert "financial_release" in html
    assert "검수 데스크 시작" in html
    assert "<script" not in html.lower()


def test_review_preserves_exact_evidence_packet_fields(tmp_path: Path) -> None:
    store = QaLabStore(tmp_path / "qa.sqlite")
    review = store.add_review(
        reviewer="검수자",
        question="매출액은?",
        answer="333,605,938 백만원",
        verdict="correct",
        citations=[{
            "evidence_id": "ev-sales",
            "filing_id": "20260310002820",
            "report_name": "사업보고서",
            "filed_at": "2026-03-10",
            "locator": {"kind": "table_cell", "row": 1, "column": 1},
            "excerpt": "333,605,938",
        }],
    )
    assert review["citations"] == [{
        "evidence_id": "ev-sales",
        "filing_id": "20260310002820",
        "report_name": "사업보고서",
        "filed_at": "2026-03-10",
        "locator": {"kind": "table_cell", "row": 1, "column": 1},
        "excerpt": "333,605,938",
    }]


def test_gold_candidate_requires_independent_approval_and_exports_canonical_record(tmp_path: Path) -> None:
    corpus = tmp_path / "corpus.sqlite"
    _build_gold_corpus(corpus)
    store = QaLabStore(tmp_path / "qa.sqlite", corpus_database=corpus)
    review = store.add_review(
        reviewer="QA검수자",
        question="삼성전자의 2025년 매출액은?",
        answer="333,605,938 백만원",
        verdict="correct",
        answerable=True,
        verified=True,
        citations=[{
            "evidence_id": "ev-sales",
            "filing_id": "20260310002820",
            "report_name": "사업보고서",
            "filed_at": "2026-03-10",
            "locator": {"kind": "table_cell", "row": 1, "column": 1},
            "excerpt": "333,605,938",
        }],
    )
    candidate = store.create_gold_candidate(review_id=review["review_id"], annotator="정답작성자")
    assert candidate["status"] == "candidate"
    assert candidate["evidence_packet"]["evidence"][0]["text"] == "333605938"
    annotation = candidate["annotation"]
    annotation.update({
        "question_type": "table_cell",
        "answerability": "answerable",
        "answer": {"kind": "numeric", "value": "333605938", "unit": "백만원", "scale": 1000000},
        "period": {
            "period_type": "duration",
            "start_date": "2025-01-01",
            "end_date": "2025-12-31",
            "instant_date": None,
        },
        "scope": "consolidated",
        "version_basis": "latest_effective",
        "table_structure_validated": True,
        "notes": "원문 표의 행·열 헤더와 단위 확인",
    })
    saved = store.update_gold_candidate(
        candidate_id=candidate["candidate_id"],
        editor="정답작성자",
        annotation=annotation,
    )
    assert saved["automatic_checks"]["passed"] is True
    with pytest.raises(QaLabError, match="self_approval_forbidden"):
        store.decide_gold_candidate(
            candidate_id=candidate["candidate_id"],
            reviewer="정답작성자",
            decision="approve",
        )
    with sqlite3.connect(corpus) as connection:
        connection.execute(
            "UPDATE filing_version SET is_current=0,effective_to='2026-08-01' WHERE filing_id='20260310002820'"
        )
        connection.commit()
    with pytest.raises(QaLabError, match="latest_basis_requires_current_filings"):
        store.decide_gold_candidate(
            candidate_id=candidate["candidate_id"],
            reviewer="최종검수자",
            decision="approve",
        )
    with sqlite3.connect(corpus) as connection:
        connection.execute(
            "UPDATE filing_version SET is_current=1,effective_to=NULL WHERE filing_id='20260310002820'"
        )
        connection.commit()
    approved = store.decide_gold_candidate(
        candidate_id=candidate["candidate_id"],
        reviewer="최종검수자",
        decision="approve",
        notes="원문과 일치",
    )
    assert approved["status"] == "approved"
    assert validate_record_contract(approved["record"]) == []
    exported = [json.loads(line) for line in store.export_gold_jsonl().splitlines()]
    assert exported == [approved["record"]]
    with pytest.raises(QaLabError, match="approved_candidate_immutable"):
        store.update_gold_candidate(
            candidate_id=candidate["candidate_id"],
            editor="정답작성자",
            annotation=annotation,
        )


@pytest.fixture
def lab_client(tmp_path: Path):
    from fastapi.testclient import TestClient

    class ReadyService:
        base_database = tmp_path / "base.sqlite"
        overlay_database = None
        search_database = None
        attestation = None
        corpus_revision = "test-revision"

        def company_candidates(self) -> list[str]:
            return ["삼성전자"]

    _build_gold_corpus(ReadyService.base_database)

    class FakeAgent:
        evidence_service = ReadyService()
        provider_configured = False

        def answer(self, question: str, **kwargs: object) -> VerifiedAnswer:
            return VerifiedAnswer(
                answer="검증된 답변",
                citation_ids=["ev1"],
                verified=True,
                answerable=True,
                citations=[CitationRef("ev1", "f1", report_name="사업보고서")],
            )

    return TestClient(create_app(
        FakeAgent(),
        qa_database=tmp_path / "qa_lab.sqlite",
    ))


def test_lab_page_is_separate_from_public_chat(lab_client) -> None:
    page = lab_client.get("/lab")
    public = lab_client.get("/")
    assert page.status_code == 200
    assert 'id="qa-reviewer"' in page.text
    assert "/static/lab.js" in page.text
    assert 'id="gold-panel"' in page.text
    assert "/lab/export/gold.jsonl" in page.text
    assert "기록 전체 삭제" not in page.text
    assert 'id="qa-reviewer"' not in public.text


def test_lab_api_persists_review_without_token(lab_client) -> None:
    created = lab_client.post(
        "/lab/api/reviews",
        json={
            "reviewer": "이정찬",
            "question": "삼성전자 최근 매출액은?",
            "answer": "333,605,938 백만원",
            "verdict": "correct",
            "notes": "일치",
            "answerable": True,
            "verified": True,
            "latency_ms": 120.5,
            "citations": [{"evidence_id": "ev-sales", "filing_id": "20260310002820"}],
        },
    )
    assert created.status_code == 200
    body = created.json()
    assert body["review"]["verdict"] == "correct"
    listed = lab_client.get("/lab/api/reviews").json()
    assert listed["reviews"][0]["question"] == "삼성전자 최근 매출액은?"
    summary = lab_client.get("/lab/api/summary").json()
    assert summary["reviews"]["total"] == 1
    candidate = lab_client.post("/lab/api/gold-candidates", json={
        "review_id": body["review"]["review_id"],
        "annotator": "정답작성자",
    })
    assert candidate.status_code == 200
    candidate_body = candidate.json()["candidate"]
    annotation = candidate_body["annotation"]
    annotation.update({
        "question_type": "table_cell",
        "answerability": "answerable",
        "answer": {"kind": "numeric", "value": "333605938", "unit": "백만원", "scale": 1000000},
        "period": {
            "period_type": "duration",
            "start_date": "2025-01-01",
            "end_date": "2025-12-31",
            "instant_date": None,
        },
        "scope": "consolidated",
        "version_basis": "latest_effective",
        "table_structure_validated": True,
    })
    updated = lab_client.put(
        f"/lab/api/gold-candidates/{candidate_body['candidate_id']}",
        json={"editor": "정답작성자", "annotation": annotation},
    )
    assert updated.status_code == 200
    approved = lab_client.post(
        f"/lab/api/gold-candidates/{candidate_body['candidate_id']}/decision",
        json={"reviewer": "최종검수자", "decision": "approve", "notes": "원문 확인"},
    )
    assert approved.status_code == 200
    assert approved.json()["candidate"]["status"] == "approved"
    listed_candidates = lab_client.get("/lab/api/gold-candidates").json()
    assert listed_candidates["candidates"][0]["status"] == "approved"
    exported = lab_client.get("/lab/export/gold.jsonl")
    assert exported.status_code == 200
    assert json.loads(exported.text)["question_id"] == annotation["question_id"]


def test_lab_records_performance_and_export_html(lab_client) -> None:
    perf = lab_client.post("/lab/api/perf-runs", json={
        "recorder": "이정찬",
        "suite": "pytest",
        "passed": 393,
        "failed": 0,
        "p95_ms": 23.7,
        "notes": "full regression",
    })
    assert perf.status_code == 200
    mile = lab_client.post("/lab/api/milestones", json={
        "author": "이정찬",
        "title": "QA 데스크 오픈",
        "body": "공개 서버 검수 기록을 공유 DB에 쌓기 시작한다.",
        "category": "process",
    })
    assert mile.status_code == 200
    export = lab_client.get("/lab/export.html")
    assert export.status_code == 200
    assert export.headers["content-type"].startswith("text/html")
    assert "pytest" in export.text
    assert "QA 데스크 오픈" in export.text


def test_lab_api_requires_configured_database(tmp_path: Path) -> None:
    from fastapi.testclient import TestClient

    class ReadyService:
        base_database = tmp_path / "base.sqlite"
        overlay_database = None
        search_database = None
        attestation = None
        corpus_revision = "test"

        def company_candidates(self) -> list[str]:
            return []

    ReadyService.base_database.write_bytes(b"")

    class FakeAgent:
        evidence_service = ReadyService()
        provider_configured = False

    from unittest.mock import patch

    with patch.dict("os.environ", {"DISCLOSURE_QA_DB": ""}, clear=False):
        client = TestClient(create_app(FakeAgent(), qa_database=None))
    page = client.get("/lab")
    assert page.status_code == 200
    blocked = client.get("/lab/api/summary")
    assert blocked.status_code == 503
    assert blocked.json()["detail"] == "lab_db_not_configured"
