from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from scripts import build_qa_ground_truth, qa_bank_v4


def test_truth_index_keeps_consolidated_and_separate_grains_distinct(
    tmp_path: Path, monkeypatch,
) -> None:
    path = tmp_path / "ground_truth.json"
    path.write_text(json.dumps({"grains": {
        "consolidated": {
            "company": "테스트회사", "account_id": "revenue", "fiscal_year": 2025,
            "scope": "consolidated", "value": "100",
        },
        "separate": {
            "company": "테스트회사", "account_id": "revenue", "fiscal_year": 2025,
            "scope": "separate", "value": "200",
        },
    }}, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(qa_bank_v4, "GROUND_TRUTH", path)

    truth = qa_bank_v4.load_truth()

    assert truth[("테스트회사", "revenue", 2025, "consolidated")]["value"] == "100"
    assert truth[("테스트회사", "revenue", 2025, "separate")]["value"] == "200"


def _semantic_databases() -> tuple[sqlite3.Connection, sqlite3.Connection]:
    corpus = sqlite3.connect(":memory:")
    corpus.executescript("""
        CREATE TABLE filing(filing_id TEXT PRIMARY KEY, issuer_corp_code TEXT NOT NULL);
        CREATE TABLE table_cell(
            evidence_id TEXT PRIMARY KEY,
            filing_id TEXT NOT NULL,
            text_normalized TEXT,
            text_raw TEXT
        );
        INSERT INTO filing VALUES ('filing-2025', 'corp-1');
        INSERT INTO table_cell VALUES ('ev-1', 'filing-2025', '100', '100');
    """)
    overlay = sqlite3.connect(":memory:")
    overlay.executescript("""
        CREATE TABLE financial_fact(
            financial_fact_id TEXT PRIMARY KEY,
            filing_id TEXT NOT NULL,
            account_id TEXT NOT NULL,
            scope TEXT NOT NULL,
            period_type TEXT NOT NULL,
            period_start TEXT,
            period_end TEXT,
            instant_date TEXT,
            value_numeric TEXT NOT NULL,
            scale INTEGER NOT NULL,
            validation_status TEXT NOT NULL
        );
        CREATE TABLE financial_fact_evidence(
            financial_fact_id TEXT NOT NULL,
            evidence_id TEXT NOT NULL
        );
        INSERT INTO financial_fact VALUES (
            'ff-1', 'filing-2025', 'revenue', 'consolidated', 'duration',
            '2025-01-01', '2025-12-31', NULL, '100', 1, 'validated'
        );
        INSERT INTO financial_fact_evidence VALUES ('ff-1', 'ev-1');
    """)
    return corpus, overlay


def _observation(**updates: object) -> dict[str, object]:
    observation: dict[str, object] = {
        "issuer_corp_code": "corp-1",
        "filing_id": "filing-2025",
        "account_id": "revenue",
        "scope": "consolidated",
        "period_type": "duration",
        "period_start": "2025-01-01",
        "period_end": "2025-12-31",
        "instant_date": "",
        "value": build_qa_ground_truth.Decimal("100"),
        "scale": 1,
        "evidence_ids": ["ev-1"],
    }
    observation.update(updates)
    return observation


def test_corpus_confirmation_requires_matching_financial_semantics() -> None:
    corpus, overlay = _semantic_databases()

    assert build_qa_ground_truth.corpus_confirms(
        corpus, _observation(account_id="operating_income"), overlay
    )[0] is False
    assert build_qa_ground_truth.corpus_confirms(
        corpus, _observation(period_end="1900-12-31"), overlay
    )[0] is False
    assert build_qa_ground_truth.corpus_confirms(
        corpus, _observation(scope="separate"), overlay
    )[0] is False
    assert build_qa_ground_truth.corpus_confirms(
        corpus, _observation(period_start="2025-10-01"), overlay
    )[0] is False


def test_corpus_confirmation_accepts_exact_validated_financial_grain() -> None:
    corpus, overlay = _semantic_databases()

    assert build_qa_ground_truth.corpus_confirms(corpus, _observation(), overlay) == (
        True, "corpus_confirmed"
    )
