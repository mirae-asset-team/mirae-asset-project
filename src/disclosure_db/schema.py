from __future__ import annotations

import sqlite3


SQLITE_SCHEMA = r"""
PRAGMA foreign_keys = ON;

CREATE TABLE pipeline_run (
    run_id TEXT PRIMARY KEY,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    parser_name TEXT NOT NULL,
    parser_version TEXT NOT NULL,
    corpus_root TEXT NOT NULL,
    manifest_sha256 TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('running','success','failed')),
    stats_json TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE filing (
    filing_id TEXT PRIMARY KEY,
    doc_id TEXT NOT NULL UNIQUE,
    issuer_corp_code TEXT NOT NULL CHECK (length(issuer_corp_code) = 8),
    stock_code TEXT NOT NULL CHECK (length(stock_code) = 6),
    issuer_name TEXT NOT NULL,
    listed_name TEXT NOT NULL,
    reporter_name TEXT NOT NULL,
    industry TEXT NOT NULL,
    sector TEXT NOT NULL,
    doc_group TEXT NOT NULL CHECK (doc_group IN ('periodic','major','exchange','holding')),
    doc_subtype_raw TEXT,
    doc_subtype_normalized TEXT,
    report_name_raw TEXT NOT NULL,
    report_name_normalized TEXT NOT NULL,
    filed_at TEXT NOT NULL,
    base_year INTEGER,
    base_month INTEGER,
    is_correction INTEGER NOT NULL CHECK (is_correction IN (0,1)),
    file_format_declared TEXT NOT NULL,
    source_file_count INTEGER NOT NULL CHECK (source_file_count > 0)
);

CREATE TABLE source_document (
    source_id TEXT PRIMARY KEY,
    filing_id TEXT NOT NULL REFERENCES filing(filing_id),
    role TEXT NOT NULL,
    source_path TEXT NOT NULL,
    extension TEXT NOT NULL,
    detected_format TEXT NOT NULL,
    declared_encoding TEXT,
    detected_encoding TEXT,
    sha256 TEXT NOT NULL,
    byte_size INTEGER NOT NULL,
    modified_at TEXT NOT NULL,
    parser_name TEXT NOT NULL,
    parser_version TEXT NOT NULL,
    parse_status TEXT NOT NULL CHECK (parse_status IN ('success','partial','failed','unsupported')),
    strict_xml_ok INTEGER,
    parser_error_count INTEGER NOT NULL DEFAULT 0,
    warnings_json TEXT NOT NULL DEFAULT '[]',
    coverage_json TEXT NOT NULL DEFAULT '{}',
    UNIQUE(filing_id, source_path),
    UNIQUE(filing_id, sha256, role)
);

CREATE TABLE parser_error (
    error_id TEXT PRIMARY KEY,
    source_id TEXT NOT NULL REFERENCES source_document(source_id),
    ordinal INTEGER NOT NULL,
    phase TEXT NOT NULL CHECK (phase IN ('strict','recovery','html')),
    level TEXT NOT NULL,
    error_type TEXT NOT NULL,
    line INTEGER,
    column_no INTEGER,
    message TEXT NOT NULL,
    UNIQUE(source_id, ordinal)
);

CREATE TABLE fragment (
    evidence_id TEXT PRIMARY KEY,
    filing_id TEXT NOT NULL REFERENCES filing(filing_id),
    source_id TEXT NOT NULL REFERENCES source_document(source_id),
    fragment_type TEXT NOT NULL CHECK (fragment_type IN ('heading','paragraph','table_row','pdf_block','html_text')),
    sequence_no INTEGER NOT NULL,
    section_path_json TEXT NOT NULL,
    page_no INTEGER,
    table_id TEXT,
    locator_json TEXT NOT NULL,
    text_raw TEXT NOT NULL,
    text_normalized TEXT NOT NULL,
    parser_version TEXT NOT NULL,
    UNIQUE(source_id, fragment_type, sequence_no)
);

CREATE TABLE table_record (
    table_id TEXT PRIMARY KEY,
    filing_id TEXT NOT NULL REFERENCES filing(filing_id),
    source_id TEXT NOT NULL REFERENCES source_document(source_id),
    sequence_no INTEGER NOT NULL,
    section_path_json TEXT NOT NULL,
    caption TEXT,
    unit_text TEXT,
    row_count INTEGER NOT NULL,
    column_count INTEGER NOT NULL,
    parse_status TEXT NOT NULL CHECK (parse_status IN ('success','partial','failed')),
    locator_json TEXT NOT NULL,
    UNIQUE(source_id, sequence_no)
);

CREATE TABLE table_cell (
    evidence_id TEXT PRIMARY KEY,
    table_id TEXT NOT NULL REFERENCES table_record(table_id),
    source_id TEXT NOT NULL REFERENCES source_document(source_id),
    filing_id TEXT NOT NULL REFERENCES filing(filing_id),
    row_index INTEGER NOT NULL,
    column_index INTEGER NOT NULL,
    rowspan INTEGER NOT NULL CHECK (rowspan > 0),
    colspan INTEGER NOT NULL CHECK (colspan > 0),
    cell_kind TEXT NOT NULL CHECK (cell_kind IN ('header','data')),
    row_header_path_json TEXT NOT NULL,
    column_header_path_json TEXT NOT NULL,
    locator_json TEXT NOT NULL,
    text_raw TEXT NOT NULL,
    text_normalized TEXT NOT NULL,
    parser_version TEXT NOT NULL,
    UNIQUE(table_id, row_index, column_index)
);

CREATE TABLE fact (
    fact_id TEXT PRIMARY KEY,
    filing_id TEXT NOT NULL REFERENCES filing(filing_id),
    fact_type TEXT NOT NULL,
    subject TEXT NOT NULL,
    predicate TEXT NOT NULL,
    value_raw TEXT NOT NULL,
    unit TEXT,
    extraction_method TEXT NOT NULL,
    validation_status TEXT NOT NULL CHECK (validation_status IN ('candidate','validated','rejected'))
);

CREATE TABLE fact_evidence (
    fact_id TEXT NOT NULL REFERENCES fact(fact_id),
    evidence_id TEXT NOT NULL REFERENCES table_cell(evidence_id),
    PRIMARY KEY (fact_id, evidence_id)
);

-- Financial statement semantics are deliberately separate from the generic event/ownership
-- key-value candidates in `fact`. No row is inserted here without a domain validator.
CREATE TABLE financial_fact (
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

CREATE TABLE financial_fact_evidence (
    financial_fact_id TEXT NOT NULL REFERENCES financial_fact(financial_fact_id),
    evidence_id TEXT NOT NULL REFERENCES table_cell(evidence_id),
    PRIMARY KEY (financial_fact_id, evidence_id)
);

CREATE TABLE filing_event (
    event_id TEXT PRIMARY KEY,
    issuer_corp_code TEXT NOT NULL,
    doc_group TEXT NOT NULL,
    event_key TEXT NOT NULL,
    lineage_method TEXT NOT NULL,
    UNIQUE(issuer_corp_code, doc_group, event_key)
);

CREATE TABLE filing_version (
    filing_id TEXT PRIMARY KEY REFERENCES filing(filing_id),
    event_id TEXT NOT NULL REFERENCES filing_event(event_id),
    version_no INTEGER NOT NULL,
    parent_filing_id TEXT REFERENCES filing(filing_id),
    lineage_status TEXT NOT NULL CHECK (lineage_status IN ('root','resolved','unresolved','missing_original')),
    lineage_confidence TEXT NOT NULL CHECK (lineage_confidence IN ('high','medium','none')),
    effective_from TEXT NOT NULL,
    effective_to TEXT,
    is_current INTEGER NOT NULL CHECK (is_current IN (0,1)),
    rationale TEXT NOT NULL,
    UNIQUE(event_id, version_no)
);

CREATE TABLE quality_issue (
    issue_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES pipeline_run(run_id),
    severity TEXT NOT NULL CHECK (severity IN ('error','warning','info')),
    dimension TEXT NOT NULL,
    entity_type TEXT NOT NULL,
    entity_id TEXT NOT NULL,
    rule_id TEXT NOT NULL,
    message TEXT NOT NULL,
    details_json TEXT NOT NULL DEFAULT '{}'
);

CREATE TRIGGER fact_evidence_same_filing_insert
BEFORE INSERT ON fact_evidence
WHEN (SELECT filing_id FROM fact WHERE fact_id=NEW.fact_id)
     <> (SELECT filing_id FROM table_cell WHERE evidence_id=NEW.evidence_id)
BEGIN SELECT RAISE(ABORT, 'fact evidence filing mismatch'); END;

CREATE TRIGGER fact_evidence_same_filing_update
BEFORE UPDATE ON fact_evidence
WHEN (SELECT filing_id FROM fact WHERE fact_id=NEW.fact_id)
     <> (SELECT filing_id FROM table_cell WHERE evidence_id=NEW.evidence_id)
BEGIN SELECT RAISE(ABORT, 'fact evidence filing mismatch'); END;

CREATE TRIGGER financial_fact_evidence_same_filing_insert
BEFORE INSERT ON financial_fact_evidence
WHEN (SELECT filing_id FROM financial_fact WHERE financial_fact_id=NEW.financial_fact_id)
     <> (SELECT filing_id FROM table_cell WHERE evidence_id=NEW.evidence_id)
BEGIN SELECT RAISE(ABORT, 'financial fact evidence filing mismatch'); END;

CREATE TRIGGER financial_fact_evidence_same_filing_update
BEFORE UPDATE ON financial_fact_evidence
WHEN (SELECT filing_id FROM financial_fact WHERE financial_fact_id=NEW.financial_fact_id)
     <> (SELECT filing_id FROM table_cell WHERE evidence_id=NEW.evidence_id)
BEGIN SELECT RAISE(ABORT, 'financial fact evidence filing mismatch'); END;

CREATE TRIGGER fact_validation_requires_evidence_insert
BEFORE INSERT ON fact WHEN NEW.validation_status='validated'
BEGIN SELECT RAISE(ABORT, 'validated fact must be promoted after evidence is attached'); END;

CREATE TRIGGER financial_fact_validation_requires_evidence_insert
BEFORE INSERT ON financial_fact WHEN NEW.validation_status='validated'
BEGIN SELECT RAISE(ABORT, 'validated financial fact must be promoted after evidence is attached'); END;

CREATE TRIGGER fact_validation_requires_evidence_update
BEFORE UPDATE OF validation_status ON fact
WHEN NEW.validation_status='validated'
 AND NOT EXISTS (SELECT 1 FROM fact_evidence WHERE fact_id=NEW.fact_id)
BEGIN SELECT RAISE(ABORT, 'validated fact requires cell evidence'); END;

CREATE TRIGGER financial_fact_validation_requires_evidence_update
BEFORE UPDATE OF validation_status ON financial_fact
WHEN NEW.validation_status='validated'
 AND NOT EXISTS (SELECT 1 FROM financial_fact_evidence WHERE financial_fact_id=NEW.financial_fact_id)
BEGIN SELECT RAISE(ABORT, 'validated financial fact requires cell evidence'); END;

CREATE TRIGGER fact_evidence_preserve_validated_delete
BEFORE DELETE ON fact_evidence
WHEN (SELECT validation_status FROM fact WHERE fact_id=OLD.fact_id)='validated'
 AND (SELECT count(*) FROM fact_evidence WHERE fact_id=OLD.fact_id)=1
BEGIN SELECT RAISE(ABORT, 'cannot remove final evidence from validated fact'); END;

CREATE TRIGGER financial_fact_evidence_preserve_validated_delete
BEFORE DELETE ON financial_fact_evidence
WHEN (SELECT validation_status FROM financial_fact WHERE financial_fact_id=OLD.financial_fact_id)='validated'
 AND (SELECT count(*) FROM financial_fact_evidence WHERE financial_fact_id=OLD.financial_fact_id)=1
BEGIN SELECT RAISE(ABORT, 'cannot remove final evidence from validated financial fact'); END;
"""


INDEX_SQL = r"""
CREATE INDEX IF NOT EXISTS idx_filing_company_date ON filing(issuer_corp_code, filed_at DESC);
CREATE INDEX IF NOT EXISTS idx_filing_stock_date ON filing(stock_code, filed_at DESC);
CREATE INDEX IF NOT EXISTS idx_filing_reporter_date ON filing(reporter_name, filed_at DESC);
CREATE INDEX IF NOT EXISTS idx_filing_group_subtype_date ON filing(doc_group, doc_subtype_normalized, filed_at DESC);
CREATE INDEX IF NOT EXISTS idx_source_filing ON source_document(filing_id);
CREATE INDEX IF NOT EXISTS idx_parser_error_source ON parser_error(source_id, ordinal);
CREATE INDEX IF NOT EXISTS idx_fragment_filing_type ON fragment(filing_id, fragment_type);
CREATE INDEX IF NOT EXISTS idx_fragment_source ON fragment(source_id, sequence_no);
CREATE INDEX IF NOT EXISTS idx_fragment_table_row ON fragment(table_id) WHERE table_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_cell_filing ON table_cell(filing_id);
CREATE INDEX IF NOT EXISTS idx_cell_table ON table_cell(table_id, row_index, column_index);
CREATE INDEX IF NOT EXISTS idx_fact_filing_predicate ON fact(filing_id, predicate);
CREATE INDEX IF NOT EXISTS idx_financial_fact_filing_account ON financial_fact(filing_id, account_id, account_name_raw);
CREATE INDEX IF NOT EXISTS idx_version_event_current ON filing_version(event_id, is_current);
CREATE INDEX IF NOT EXISTS idx_quality_rule ON quality_issue(rule_id, severity);
"""


FTS_SQL = r"""
DROP TRIGGER IF EXISTS fragment_fts_ai;
DROP TRIGGER IF EXISTS fragment_fts_ad;
DROP TRIGGER IF EXISTS fragment_fts_au;
DROP TABLE IF EXISTS fragment_fts;
CREATE VIRTUAL TABLE fragment_fts USING fts5(
    text_normalized,
    content='fragment',
    content_rowid='rowid',
    tokenize='unicode61 remove_diacritics 0'
);
INSERT INTO fragment_fts(fragment_fts) VALUES('rebuild');
INSERT INTO fragment_fts(fragment_fts) VALUES('optimize');

CREATE TRIGGER fragment_fts_ai AFTER INSERT ON fragment BEGIN
    INSERT INTO fragment_fts(rowid,text_normalized) VALUES (new.rowid,new.text_normalized);
END;
CREATE TRIGGER fragment_fts_ad AFTER DELETE ON fragment BEGIN
    INSERT INTO fragment_fts(fragment_fts,rowid,text_normalized)
    VALUES('delete',old.rowid,old.text_normalized);
END;
CREATE TRIGGER fragment_fts_au AFTER UPDATE OF text_normalized ON fragment BEGIN
    INSERT INTO fragment_fts(fragment_fts,rowid,text_normalized)
    VALUES('delete',old.rowid,old.text_normalized);
    INSERT INTO fragment_fts(rowid,text_normalized) VALUES (new.rowid,new.text_normalized);
END;
"""


def create_schema(connection: sqlite3.Connection) -> None:
    connection.executescript(SQLITE_SCHEMA)


def create_indexes(connection: sqlite3.Connection, *, build_fts: bool = True) -> None:
    connection.executescript(INDEX_SQL)
    if build_fts:
        connection.executescript(FTS_SQL)
