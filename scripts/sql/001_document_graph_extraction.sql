-- Append-only task ledger for automatic high-value document graph extraction.
-- No source document or existing fact is modified by this migration.

CREATE TABLE IF NOT EXISTS document_graph_extraction_jobs (
    id BIGSERIAL PRIMARY KEY,
    doc_id TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    document_class TEXT NOT NULL,
    gate_status TEXT NOT NULL CHECK (gate_status IN (
        'eligible', 'rejected_by_gate', 'queued', 'running', 'completed', 'failed'
    )),
    gate_score NUMERIC(4,3) NOT NULL,
    gate_reasons JSONB NOT NULL DEFAULT '[]'::jsonb,
    source_url TEXT,
    source_type TEXT,
    authority_tier TEXT,
    chunk_count INTEGER NOT NULL DEFAULT 0,
    char_count INTEGER NOT NULL DEFAULT 0,
    cognee_dataset TEXT,
    cognee_run_id TEXT,
    attempt_count INTEGER NOT NULL DEFAULT 0,
    last_error TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    started_at TIMESTAMPTZ,
    completed_at TIMESTAMPTZ
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_document_graph_jobs_doc_hash
    ON document_graph_extraction_jobs (doc_id, content_hash);
CREATE INDEX IF NOT EXISTS idx_document_graph_jobs_status_created
    ON document_graph_extraction_jobs (gate_status, created_at);

CREATE TABLE IF NOT EXISTS document_graph_extraction_events (
    id BIGSERIAL PRIMARY KEY,
    job_id BIGINT NOT NULL REFERENCES document_graph_extraction_jobs(id) ON DELETE RESTRICT,
    event_type TEXT NOT NULL CHECK (event_type IN ('classified', 'queued', 'started', 'completed', 'failed', 'rejected')),
    payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_document_graph_events_job_time
    ON document_graph_extraction_events (job_id, created_at);

CREATE OR REPLACE FUNCTION reject_document_graph_event_mutation()
RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'document_graph_extraction_events is append-only';
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_document_graph_events_append_only ON document_graph_extraction_events;
CREATE TRIGGER trg_document_graph_events_append_only
    BEFORE UPDATE OR DELETE ON document_graph_extraction_events
    FOR EACH ROW EXECUTE FUNCTION reject_document_graph_event_mutation();
