-- Additive semantic-layer schema for an existing validated SQLite structural SSOT.
-- Apply through scripts/migrate_database.py, which creates a consistent copy and audit report.

PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS schema_migration (
    migration_id TEXT PRIMARY KEY,
    applied_at TEXT NOT NULL,
    script_sha256 TEXT NOT NULL,
    source_database TEXT NOT NULL,
    details_json TEXT NOT NULL DEFAULT '{}'
);

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
CREATE INDEX IF NOT EXISTS idx_fragment_table_row
ON fragment(table_id) WHERE table_id IS NOT NULL;

-- Evidence must belong to the same filing as its fact. These triggers protect future writes
-- without rewriting the existing structural ledger.
CREATE TRIGGER IF NOT EXISTS fact_evidence_same_filing_insert
BEFORE INSERT ON fact_evidence
WHEN (SELECT filing_id FROM fact WHERE fact_id=NEW.fact_id)
     <> (SELECT filing_id FROM table_cell WHERE evidence_id=NEW.evidence_id)
BEGIN
    SELECT RAISE(ABORT, 'fact evidence filing mismatch');
END;

CREATE TRIGGER IF NOT EXISTS fact_evidence_same_filing_update
BEFORE UPDATE ON fact_evidence
WHEN (SELECT filing_id FROM fact WHERE fact_id=NEW.fact_id)
     <> (SELECT filing_id FROM table_cell WHERE evidence_id=NEW.evidence_id)
BEGIN
    SELECT RAISE(ABORT, 'fact evidence filing mismatch');
END;

CREATE TRIGGER IF NOT EXISTS financial_fact_evidence_same_filing_insert
BEFORE INSERT ON financial_fact_evidence
WHEN (SELECT filing_id FROM financial_fact WHERE financial_fact_id=NEW.financial_fact_id)
     <> (SELECT filing_id FROM table_cell WHERE evidence_id=NEW.evidence_id)
BEGIN
    SELECT RAISE(ABORT, 'financial fact evidence filing mismatch');
END;

CREATE TRIGGER IF NOT EXISTS financial_fact_evidence_same_filing_update
BEFORE UPDATE ON financial_fact_evidence
WHEN (SELECT filing_id FROM financial_fact WHERE financial_fact_id=NEW.financial_fact_id)
     <> (SELECT filing_id FROM table_cell WHERE evidence_id=NEW.evidence_id)
BEGIN
    SELECT RAISE(ABORT, 'financial fact evidence filing mismatch');
END;

CREATE TRIGGER IF NOT EXISTS fact_validation_requires_evidence_insert
BEFORE INSERT ON fact
WHEN NEW.validation_status='validated'
BEGIN
    SELECT RAISE(ABORT, 'validated fact must be promoted after evidence is attached');
END;

CREATE TRIGGER IF NOT EXISTS financial_fact_validation_requires_evidence_insert
BEFORE INSERT ON financial_fact
WHEN NEW.validation_status='validated'
BEGIN
    SELECT RAISE(ABORT, 'validated financial fact must be promoted after evidence is attached');
END;

CREATE TRIGGER IF NOT EXISTS fact_validation_requires_evidence
BEFORE UPDATE OF validation_status ON fact
WHEN NEW.validation_status='validated'
 AND NOT EXISTS (SELECT 1 FROM fact_evidence WHERE fact_id=NEW.fact_id)
BEGIN
    SELECT RAISE(ABORT, 'validated fact requires cell evidence');
END;

CREATE TRIGGER IF NOT EXISTS financial_fact_validation_requires_evidence
BEFORE UPDATE OF validation_status ON financial_fact
WHEN NEW.validation_status='validated'
 AND NOT EXISTS (
     SELECT 1 FROM financial_fact_evidence WHERE financial_fact_id=NEW.financial_fact_id
 )
BEGIN
    SELECT RAISE(ABORT, 'validated financial fact requires cell evidence');
END;

CREATE TRIGGER IF NOT EXISTS fact_evidence_preserve_validated_delete
BEFORE DELETE ON fact_evidence
WHEN (SELECT validation_status FROM fact WHERE fact_id=OLD.fact_id)='validated'
 AND (SELECT count(*) FROM fact_evidence WHERE fact_id=OLD.fact_id)=1
BEGIN
    SELECT RAISE(ABORT, 'cannot remove final evidence from validated fact');
END;

CREATE TRIGGER IF NOT EXISTS financial_fact_evidence_preserve_validated_delete
BEFORE DELETE ON financial_fact_evidence
WHEN (SELECT validation_status FROM financial_fact WHERE financial_fact_id=OLD.financial_fact_id)='validated'
 AND (SELECT count(*) FROM financial_fact_evidence WHERE financial_fact_id=OLD.financial_fact_id)=1
BEGIN
    SELECT RAISE(ABORT, 'cannot remove final evidence from validated financial fact');
END;

-- FTS rowid safety is not an additive migration: replacing the current FTS table requires an
-- explicit index rebuild and reconciliation. The next full build uses content='fragment' and
-- content_rowid='rowid' from src/disclosure_db/schema.py.
