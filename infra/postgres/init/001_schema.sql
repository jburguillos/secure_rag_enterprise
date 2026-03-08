CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE TABLE IF NOT EXISTS documents (
    doc_id TEXT PRIMARY KEY,
    source TEXT NOT NULL,
    title TEXT,
    mime_type TEXT,
    modified_time TIMESTAMPTZ,
    content_hash TEXT,
    permissions_summary JSONB NOT NULL DEFAULT '{}'::jsonb,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS admin_settings (
    key TEXT PRIMARY KEY,
    value JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS ingestion_runs (
    ingestion_run_id UUID PRIMARY KEY,
    source TEXT NOT NULL,
    dataset_source TEXT NOT NULL,
    started_at TIMESTAMPTZ NOT NULL,
    ended_at TIMESTAMPTZ,
    status TEXT NOT NULL,
    added_count INT NOT NULL DEFAULT 0,
    updated_count INT NOT NULL DEFAULT 0,
    deleted_count INT NOT NULL DEFAULT 0,
    skipped_count INT NOT NULL DEFAULT 0,
    error_count INT NOT NULL DEFAULT 0,
    errors JSONB NOT NULL DEFAULT '[]'::jsonb,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE TABLE IF NOT EXISTS policy_decisions (
    decision_id UUID PRIMARY KEY,
    run_id UUID,
    timestamp TIMESTAMPTZ NOT NULL,
    user_id_hash TEXT,
    user_groups JSONB NOT NULL DEFAULT '[]'::jsonb,
    policy_input JSONB NOT NULL,
    policy_result JSONB NOT NULL,
    policy_version TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS query_runs (
    run_id UUID PRIMARY KEY,
    timestamp TIMESTAMPTZ NOT NULL,
    user_id_hash TEXT,
    user_groups JSONB NOT NULL DEFAULT '[]'::jsonb,
    query_hash TEXT NOT NULL,
    raw_query TEXT,
    mode TEXT NOT NULL,
    response_status TEXT NOT NULL,
    refusal_reason TEXT,
    model_id TEXT,
    model_version TEXT,
    config_version TEXT,
    policy_decision_id UUID REFERENCES policy_decisions(decision_id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS query_run_evidence (
    id UUID PRIMARY KEY,
    run_id UUID NOT NULL REFERENCES query_runs(run_id),
    node_id TEXT NOT NULL,
    doc_id TEXT,
    page INT,
    chunk_id TEXT,
    modality TEXT,
    score DOUBLE PRECISION,
    payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS query_run_citations (
    id UUID PRIMARY KEY,
    run_id UUID NOT NULL REFERENCES query_runs(run_id),
    node_id TEXT NOT NULL,
    doc_id TEXT,
    page INT,
    chunk_id TEXT,
    modality TEXT,
    citation_label TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS feedback_events (
    id UUID PRIMARY KEY,
    run_id UUID NOT NULL REFERENCES query_runs(run_id),
    thumb TEXT NOT NULL CHECK (thumb IN ('up', 'down')),
    reason TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE OR REPLACE FUNCTION set_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_documents_updated_at ON documents;
CREATE TRIGGER trg_documents_updated_at
BEFORE UPDATE ON documents
FOR EACH ROW EXECUTE FUNCTION set_updated_at();

DROP TRIGGER IF EXISTS trg_admin_settings_updated_at ON admin_settings;
CREATE TRIGGER trg_admin_settings_updated_at
BEFORE UPDATE ON admin_settings
FOR EACH ROW EXECUTE FUNCTION set_updated_at();

CREATE OR REPLACE FUNCTION prevent_table_mutation()
RETURNS TRIGGER AS $$
BEGIN
    RAISE EXCEPTION 'append-only table: %', TG_TABLE_NAME;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_policy_decisions_no_update ON policy_decisions;
DROP TRIGGER IF EXISTS trg_policy_decisions_no_delete ON policy_decisions;
CREATE TRIGGER trg_policy_decisions_no_update BEFORE UPDATE ON policy_decisions
FOR EACH ROW EXECUTE FUNCTION prevent_table_mutation();
CREATE TRIGGER trg_policy_decisions_no_delete BEFORE DELETE ON policy_decisions
FOR EACH ROW EXECUTE FUNCTION prevent_table_mutation();

DROP TRIGGER IF EXISTS trg_query_runs_no_update ON query_runs;
DROP TRIGGER IF EXISTS trg_query_runs_no_delete ON query_runs;
CREATE TRIGGER trg_query_runs_no_update BEFORE UPDATE ON query_runs
FOR EACH ROW EXECUTE FUNCTION prevent_table_mutation();
CREATE TRIGGER trg_query_runs_no_delete BEFORE DELETE ON query_runs
FOR EACH ROW EXECUTE FUNCTION prevent_table_mutation();

DROP TRIGGER IF EXISTS trg_query_evidence_no_update ON query_run_evidence;
DROP TRIGGER IF EXISTS trg_query_evidence_no_delete ON query_run_evidence;
CREATE TRIGGER trg_query_evidence_no_update BEFORE UPDATE ON query_run_evidence
FOR EACH ROW EXECUTE FUNCTION prevent_table_mutation();
CREATE TRIGGER trg_query_evidence_no_delete BEFORE DELETE ON query_run_evidence
FOR EACH ROW EXECUTE FUNCTION prevent_table_mutation();

DROP TRIGGER IF EXISTS trg_query_citations_no_update ON query_run_citations;
DROP TRIGGER IF EXISTS trg_query_citations_no_delete ON query_run_citations;
CREATE TRIGGER trg_query_citations_no_update BEFORE UPDATE ON query_run_citations
FOR EACH ROW EXECUTE FUNCTION prevent_table_mutation();
CREATE TRIGGER trg_query_citations_no_delete BEFORE DELETE ON query_run_citations
FOR EACH ROW EXECUTE FUNCTION prevent_table_mutation();

DROP TRIGGER IF EXISTS trg_feedback_no_update ON feedback_events;
DROP TRIGGER IF EXISTS trg_feedback_no_delete ON feedback_events;
CREATE TRIGGER trg_feedback_no_update BEFORE UPDATE ON feedback_events
FOR EACH ROW EXECUTE FUNCTION prevent_table_mutation();
CREATE TRIGGER trg_feedback_no_delete BEFORE DELETE ON feedback_events
FOR EACH ROW EXECUTE FUNCTION prevent_table_mutation();



