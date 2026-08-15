-- Production target schema. Generated artifacts are first validated in SQLite using the same grains.
-- PostgreSQL 18 + pgvector is the recommended serving baseline; OpenSearch remains a separable
-- retrieval index and must never become the system of record.

CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE schema_migration (
    migration_id text PRIMARY KEY,
    applied_at timestamptz NOT NULL,
    script_sha256 char(64) NOT NULL,
    source_database text NOT NULL,
    details jsonb NOT NULL DEFAULT '{}'
);

CREATE TABLE pipeline_run (
    run_id text PRIMARY KEY,
    started_at timestamptz NOT NULL,
    finished_at timestamptz,
    parser_name text NOT NULL,
    parser_version text NOT NULL,
    corpus_root text NOT NULL,
    manifest_sha256 char(64) NOT NULL,
    status text NOT NULL CHECK (status IN ('running','success','failed')),
    stats jsonb NOT NULL DEFAULT '{}'
);

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
    extension text NOT NULL,
    detected_format text NOT NULL,
    declared_encoding text,
    detected_encoding text,
    sha256 char(64) NOT NULL,
    byte_size bigint NOT NULL,
    modified_at timestamptz NOT NULL,
    parser_name text NOT NULL,
    parser_version text NOT NULL,
    parse_status text NOT NULL CHECK (parse_status IN ('success','partial','failed','unsupported')),
    strict_xml_ok boolean,
    parser_error_count integer NOT NULL DEFAULT 0,
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

CREATE TABLE fact (
    fact_id text PRIMARY KEY,
    filing_id text NOT NULL REFERENCES filing(filing_id),
    fact_type text NOT NULL CHECK (fact_type='event_kv_candidate'),
    subject text NOT NULL,
    predicate text NOT NULL,
    value_raw text NOT NULL,
    unit text,
    extraction_method text NOT NULL,
    validation_status text NOT NULL CHECK (validation_status IN ('candidate','validated','rejected'))
);

CREATE TABLE fact_evidence (
    fact_id text NOT NULL REFERENCES fact(fact_id),
    evidence_id text NOT NULL REFERENCES table_cell(evidence_id),
    PRIMARY KEY (fact_id, evidence_id)
);

CREATE TABLE financial_fact (
    financial_fact_id text PRIMARY KEY,
    filing_id text NOT NULL REFERENCES filing(filing_id),
    account_id text,
    account_name_raw text NOT NULL,
    statement_type text NOT NULL CHECK (statement_type IN ('BS','IS','CIS','CF','SCE','UNKNOWN')),
    scope text NOT NULL CHECK (scope IN ('consolidated','separate','unknown')),
    period_type text NOT NULL CHECK (period_type IN ('instant','duration','unknown')),
    period_start date,
    period_end date,
    instant_date date,
    value_numeric numeric NOT NULL,
    currency char(3),
    scale bigint NOT NULL CHECK (scale > 0),
    unit_raw text,
    extraction_method text NOT NULL,
    validation_status text NOT NULL CHECK (validation_status IN ('candidate','validated','rejected')),
    CHECK (
        (period_type='instant' AND instant_date IS NOT NULL AND period_start IS NULL AND period_end IS NULL)
        OR (period_type='duration' AND instant_date IS NULL AND period_start IS NOT NULL AND period_end IS NOT NULL)
        OR period_type='unknown'
    )
);

CREATE TABLE financial_fact_evidence (
    financial_fact_id text NOT NULL REFERENCES financial_fact(financial_fact_id),
    evidence_id text NOT NULL REFERENCES table_cell(evidence_id),
    PRIMARY KEY (financial_fact_id, evidence_id)
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
    CHECK (effective_to IS NULL OR effective_to >= effective_from),
    UNIQUE (event_id, version_no)
);

CREATE TABLE quality_issue (
    issue_id text PRIMARY KEY,
    run_id text NOT NULL REFERENCES pipeline_run(run_id),
    severity text NOT NULL CHECK (severity IN ('error','warning','info')),
    dimension text NOT NULL,
    entity_type text NOT NULL,
    entity_id text NOT NULL,
    rule_id text NOT NULL,
    message text NOT NULL,
    details jsonb NOT NULL DEFAULT '{}'
);

CREATE INDEX filing_company_date_idx ON filing (issuer_corp_code, filed_at DESC);
CREATE INDEX filing_stock_date_idx ON filing (stock_code, filed_at DESC);
CREATE INDEX fragment_lexical_idx ON fragment USING gin (lexical_vector);
CREATE INDEX fragment_metadata_idx ON fragment (filing_id, fragment_type);
CREATE INDEX source_warnings_idx ON source_document USING gin (warnings jsonb_path_ops);
CREATE INDEX fact_filing_predicate_idx ON fact (filing_id, predicate);
CREATE INDEX financial_fact_filing_account_idx ON financial_fact (filing_id, account_id, account_name_raw);
CREATE INDEX quality_rule_idx ON quality_issue (rule_id, severity);
CREATE UNIQUE INDEX filing_version_one_current_idx ON filing_version (event_id) WHERE is_current;

CREATE FUNCTION enforce_fact_evidence_same_filing() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF (SELECT filing_id FROM fact WHERE fact_id=NEW.fact_id)
       IS DISTINCT FROM (SELECT filing_id FROM table_cell WHERE evidence_id=NEW.evidence_id) THEN
        RAISE EXCEPTION 'fact evidence filing mismatch';
    END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER fact_evidence_same_filing
BEFORE INSERT OR UPDATE ON fact_evidence
FOR EACH ROW EXECUTE FUNCTION enforce_fact_evidence_same_filing();

CREATE FUNCTION enforce_financial_fact_evidence_same_filing() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF (SELECT filing_id FROM financial_fact WHERE financial_fact_id=NEW.financial_fact_id)
       IS DISTINCT FROM (SELECT filing_id FROM table_cell WHERE evidence_id=NEW.evidence_id) THEN
        RAISE EXCEPTION 'financial fact evidence filing mismatch';
    END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER financial_fact_evidence_same_filing
BEFORE INSERT OR UPDATE ON financial_fact_evidence
FOR EACH ROW EXECUTE FUNCTION enforce_financial_fact_evidence_same_filing();

CREATE FUNCTION enforce_fact_validation_evidence() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.validation_status='validated' AND (
        TG_OP='INSERT' OR NOT EXISTS (SELECT 1 FROM fact_evidence WHERE fact_id=NEW.fact_id)
    ) THEN
        RAISE EXCEPTION 'validated fact requires cell evidence';
    END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER fact_validation_evidence
BEFORE INSERT OR UPDATE OF validation_status ON fact
FOR EACH ROW EXECUTE FUNCTION enforce_fact_validation_evidence();

CREATE FUNCTION enforce_financial_fact_validation_evidence() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.validation_status='validated' AND (
        TG_OP='INSERT' OR NOT EXISTS (
            SELECT 1 FROM financial_fact_evidence WHERE financial_fact_id=NEW.financial_fact_id
        )
    ) THEN
        RAISE EXCEPTION 'validated financial fact requires cell evidence';
    END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER financial_fact_validation_evidence
BEFORE INSERT OR UPDATE OF validation_status ON financial_fact
FOR EACH ROW EXECUTE FUNCTION enforce_financial_fact_validation_evidence();

CREATE FUNCTION preserve_validated_fact_evidence() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF (SELECT validation_status FROM fact WHERE fact_id=OLD.fact_id)='validated'
       AND (SELECT count(*) FROM fact_evidence WHERE fact_id=OLD.fact_id)=1 THEN
        RAISE EXCEPTION 'cannot remove final evidence from validated fact';
    END IF;
    RETURN OLD;
END;
$$;
CREATE TRIGGER preserve_validated_fact_evidence
BEFORE DELETE ON fact_evidence
FOR EACH ROW EXECUTE FUNCTION preserve_validated_fact_evidence();

CREATE FUNCTION preserve_validated_financial_fact_evidence() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF (SELECT validation_status FROM financial_fact WHERE financial_fact_id=OLD.financial_fact_id)='validated'
       AND (SELECT count(*) FROM financial_fact_evidence WHERE financial_fact_id=OLD.financial_fact_id)=1 THEN
        RAISE EXCEPTION 'cannot remove final evidence from validated financial fact';
    END IF;
    RETURN OLD;
END;
$$;
CREATE TRIGGER preserve_validated_financial_fact_evidence
BEFORE DELETE ON financial_fact_evidence
FOR EACH ROW EXECUTE FUNCTION preserve_validated_financial_fact_evidence();
-- Create HNSW only after embeddings are loaded and benchmarked against exact search:
-- CREATE INDEX fragment_embedding_hnsw_idx ON fragment USING hnsw (embedding vector_cosine_ops)
-- WITH (m = 16, ef_construction = 64);
