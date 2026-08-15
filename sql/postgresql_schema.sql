-- Production target schema. Generated artifacts are first validated in SQLite using the same grains.
-- PostgreSQL 18 + pgvector is the recommended serving baseline; OpenSearch remains a separable
-- retrieval index and must never become the system of record.

CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE filing (
    filing_id text PRIMARY KEY,
    doc_id text NOT NULL UNIQUE,
    issuer_corp_code char(8) NOT NULL,
    stock_code char(6) NOT NULL,
    issuer_name text NOT NULL,
    listed_name text NOT NULL,
    reporter_name text NOT NULL,
    industry text NOT NULL,
    sector text NOT NULL,
    doc_group text NOT NULL CHECK (doc_group IN ('periodic','major','exchange','holding')),
    doc_subtype_raw text,
    doc_subtype_normalized text,
    report_name_raw text NOT NULL,
    report_name_normalized text NOT NULL,
    filed_at date NOT NULL,
    base_year smallint,
    base_month smallint,
    is_correction boolean NOT NULL,
    file_format_declared text NOT NULL,
    source_file_count smallint NOT NULL CHECK (source_file_count > 0)
);

CREATE TABLE source_document (
    source_id text PRIMARY KEY,
    filing_id text NOT NULL REFERENCES filing(filing_id),
    role text NOT NULL,
    source_path text NOT NULL,
    detected_format text NOT NULL,
    detected_encoding text,
    sha256 char(64) NOT NULL,
    byte_size bigint NOT NULL,
    parser_name text NOT NULL,
    parser_version text NOT NULL,
    parse_status text NOT NULL CHECK (parse_status IN ('success','partial','failed','unsupported')),
    warnings jsonb NOT NULL DEFAULT '[]',
    coverage jsonb NOT NULL DEFAULT '{}',
    UNIQUE (filing_id, source_path),
    UNIQUE (filing_id, sha256, role)
);

CREATE TABLE parser_error (
    error_id text PRIMARY KEY,
    source_id text NOT NULL REFERENCES source_document(source_id),
    ordinal integer NOT NULL,
    phase text NOT NULL CHECK (phase IN ('strict','recovery','html')),
    level text NOT NULL,
    error_type text NOT NULL,
    line integer,
    column_no integer,
    message text NOT NULL,
    UNIQUE (source_id, ordinal)
);

CREATE TABLE fragment (
    evidence_id text PRIMARY KEY,
    filing_id text NOT NULL REFERENCES filing(filing_id),
    source_id text NOT NULL REFERENCES source_document(source_id),
    fragment_type text NOT NULL,
    sequence_no integer NOT NULL,
    section_path jsonb NOT NULL,
    page_no integer,
    table_id text,
    locator jsonb NOT NULL,
    text_raw text NOT NULL,
    text_normalized text NOT NULL,
    lexical_vector tsvector GENERATED ALWAYS AS (to_tsvector('simple', text_normalized)) STORED,
    embedding vector(1024),
    parser_version text NOT NULL,
    UNIQUE (source_id, fragment_type, sequence_no)
);

CREATE TABLE table_record (
    table_id text PRIMARY KEY,
    filing_id text NOT NULL REFERENCES filing(filing_id),
    source_id text NOT NULL REFERENCES source_document(source_id),
    sequence_no integer NOT NULL,
    section_path jsonb NOT NULL,
    caption text,
    unit_text text,
    row_count integer NOT NULL,
    column_count integer NOT NULL,
    parse_status text NOT NULL,
    locator jsonb NOT NULL,
    UNIQUE (source_id, sequence_no)
);

CREATE TABLE table_cell (
    evidence_id text PRIMARY KEY,
    table_id text NOT NULL REFERENCES table_record(table_id),
    source_id text NOT NULL REFERENCES source_document(source_id),
    filing_id text NOT NULL REFERENCES filing(filing_id),
    row_index integer NOT NULL,
    column_index integer NOT NULL,
    rowspan integer NOT NULL CHECK (rowspan > 0),
    colspan integer NOT NULL CHECK (colspan > 0),
    cell_kind text NOT NULL,
    row_header_path jsonb NOT NULL,
    column_header_path jsonb NOT NULL,
    locator jsonb NOT NULL,
    text_raw text NOT NULL,
    text_normalized text NOT NULL,
    parser_version text NOT NULL,
    UNIQUE (table_id, row_index, column_index)
);

CREATE TABLE filing_event (
    event_id text PRIMARY KEY,
    issuer_corp_code char(8) NOT NULL,
    doc_group text NOT NULL,
    event_key text NOT NULL,
    lineage_method text NOT NULL,
    UNIQUE (issuer_corp_code, doc_group, event_key)
);

CREATE TABLE filing_version (
    filing_id text PRIMARY KEY REFERENCES filing(filing_id),
    event_id text NOT NULL REFERENCES filing_event(event_id),
    version_no integer NOT NULL,
    parent_filing_id text REFERENCES filing(filing_id),
    lineage_status text NOT NULL CHECK (lineage_status IN ('root','resolved','unresolved','missing_original')),
    lineage_confidence text NOT NULL CHECK (lineage_confidence IN ('high','medium','none')),
    effective_from date NOT NULL,
    effective_to date,
    is_current boolean NOT NULL,
    rationale text NOT NULL,
    UNIQUE (event_id, version_no)
);

CREATE INDEX filing_company_date_idx ON filing (issuer_corp_code, filed_at DESC);
CREATE INDEX filing_stock_date_idx ON filing (stock_code, filed_at DESC);
CREATE INDEX fragment_lexical_idx ON fragment USING gin (lexical_vector);
CREATE INDEX fragment_metadata_idx ON fragment (filing_id, fragment_type);
CREATE INDEX source_warnings_idx ON source_document USING gin (warnings jsonb_path_ops);
-- Create HNSW only after embeddings are loaded and benchmarked against exact search:
-- CREATE INDEX fragment_embedding_hnsw_idx ON fragment USING hnsw (embedding vector_cosine_ops)
-- WITH (m = 16, ef_construction = 64);
