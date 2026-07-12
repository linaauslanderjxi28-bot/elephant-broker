-- Append-only task ledger for high-value trade chat graph extraction.

CREATE TABLE IF NOT EXISTS chat_graph_extraction_jobs (
    id BIGSERIAL PRIMARY KEY,
    fact_id UUID NOT NULL,
    content_hash TEXT NOT NULL,
    fact_text TEXT NOT NULL,
    session_key TEXT,
    gateway_id TEXT NOT NULL,
    confidence NUMERIC(4,3) NOT NULL,
    decision_domain TEXT,
    gate_status TEXT NOT NULL CHECK (gate_status IN ('eligible','running','completed','failed')),
    gate_score NUMERIC(4,3) NOT NULL,
    gate_reasons JSONB NOT NULL DEFAULT '[]'::jsonb,
    cognee_dataset TEXT,
    cognee_run_id TEXT,
    attempt_count INTEGER NOT NULL DEFAULT 0,
    last_error TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    started_at TIMESTAMPTZ,
    completed_at TIMESTAMPTZ,
    UNIQUE (fact_id, content_hash)
);
CREATE INDEX IF NOT EXISTS idx_chat_graph_jobs_status_created
    ON chat_graph_extraction_jobs (gate_status, created_at);

CREATE TABLE IF NOT EXISTS chat_graph_extraction_events (
    id BIGSERIAL PRIMARY KEY,
    job_id BIGINT NOT NULL REFERENCES chat_graph_extraction_jobs(id) ON DELETE RESTRICT,
    event_type TEXT NOT NULL CHECK (event_type IN ('queued','started','completed','failed')),
    payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_chat_graph_events_job_time
    ON chat_graph_extraction_events (job_id, created_at);

CREATE OR REPLACE FUNCTION reject_chat_graph_event_mutation()
RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'chat_graph_extraction_events is append-only';
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_chat_graph_events_append_only ON chat_graph_extraction_events;
CREATE TRIGGER trg_chat_graph_events_append_only
    BEFORE UPDATE OR DELETE ON chat_graph_extraction_events
    FOR EACH ROW EXECUTE FUNCTION reject_chat_graph_event_mutation();
