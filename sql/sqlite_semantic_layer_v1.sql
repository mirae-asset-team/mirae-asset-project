-- Additive semantic-layer schema for an existing validated SQLite structural SSOT.
-- This file is intentionally NOT applied automatically to data/derived/disclosure_corpus.sqlite.
-- Apply only to a copy after recording the DB SHA-256 and pipeline revision.

PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS financial_fact (
    financial_fact_id TEXT PRIMARY KEY,
    filing_id TEXT NOT NULL REFERENCES filing(filing_id),
    account_id TEXT,
    account_name_raw TEXT NOT NULL,
    statement_type TEXT NOT NULL CHECK (statement_type IN ('BS','IS','CIS','CF','SCE','UNKNOWN')),
    scope TEXT NOT NULL CHECK (scope IN ('consolidated','separate','unknown')),
    period_type TEXT NOT NULL CHECK (period_type IN ('instant','duration','unknown')),
    period_start TEXT,
    period_end TEXT,
    instant_date TEXT,
    value_numeric TEXT NOT NULL,
    currency TEXT,
    scale INTEGER NOT NULL CHECK (scale > 0),
    unit_raw TEXT,
    extraction_method TEXT NOT NULL,
    validation_status TEXT NOT NULL CHECK (validation_status IN ('candidate','validated','rejected')),
    CHECK (
        (period_type='instant' AND instant_date IS NOT NULL AND period_start IS NULL AND period_end IS NULL)
        OR (period_type='duration' AND instant_date IS NULL AND period_start IS NOT NULL AND period_end IS NOT NULL)
        OR period_type='unknown'
    )
);

CREATE TABLE IF NOT EXISTS financial_fact_evidence (
    financial_fact_id TEXT NOT NULL REFERENCES financial_fact(financial_fact_id),
    evidence_id TEXT NOT NULL REFERENCES table_cell(evidence_id),
    PRIMARY KEY (financial_fact_id, evidence_id)
);

CREATE INDEX IF NOT EXISTS idx_financial_fact_filing_account
ON financial_fact(filing_id, account_id, account_name_raw);

-- FTS rowid safety is not an additive migration: replacing the current FTS table requires an
-- explicit index rebuild and reconciliation. The next full build uses content='fragment' and
-- content_rowid='rowid' from src/disclosure_db/schema.py.
