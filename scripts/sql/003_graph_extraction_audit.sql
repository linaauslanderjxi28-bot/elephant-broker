-- P3: unified, append-only graph-extraction audit and quality observability.
-- Existing source facts/documents remain immutable. This migration only adds an
-- audit projection over the two extraction ledgers and indexes for operations.

CREATE OR REPLACE VIEW graph_extraction_audit_v1 AS
SELECT
    'chat'::TEXT AS extraction_kind,
    j.id AS job_id,
    j.fact_id::TEXT AS source_id,
    j.gate_status AS status,
    j.gate_score,
    j.attempt_count,
    j.created_at,
    j.started_at,
    j.completed_at,
    j.last_error,
    COALESCE((
        SELECT e.payload
        FROM chat_graph_extraction_events e
        WHERE e.job_id = j.id AND e.event_type = 'completed'
        ORDER BY e.created_at DESC
        LIMIT 1
    ), '{}'::JSONB) AS quality,
    COALESCE((
        SELECT max(e.created_at)
        FROM chat_graph_extraction_events e
        WHERE e.job_id = j.id
    ), j.created_at) AS last_event_at
FROM chat_graph_extraction_jobs j
UNION ALL
SELECT
    'document'::TEXT AS extraction_kind,
    j.id AS job_id,
    j.doc_id AS source_id,
    j.gate_status AS status,
    j.gate_score,
    j.attempt_count,
    j.created_at,
    j.started_at,
    j.completed_at,
    j.last_error,
    COALESCE((
        SELECT e.payload
        FROM document_graph_extraction_events e
        WHERE e.job_id = j.id AND e.event_type = 'completed'
        ORDER BY e.created_at DESC
        LIMIT 1
    ), '{}'::JSONB) AS quality,
    COALESCE((
        SELECT max(e.created_at)
        FROM document_graph_extraction_events e
        WHERE e.job_id = j.id
    ), j.created_at) AS last_event_at
FROM document_graph_extraction_jobs j;

CREATE OR REPLACE VIEW graph_extraction_quality_daily_v1 AS
SELECT
    date_trunc('day', COALESCE(completed_at, created_at)) AS day_utc,
    extraction_kind,
    status,
    count(*) AS jobs,
    COALESCE(sum((quality->>'nodes')::INTEGER) FILTER (WHERE quality ? 'nodes'), 0) AS nodes,
    COALESCE(sum((quality->>'edges')::INTEGER) FILTER (WHERE quality ? 'edges'), 0) AS edges,
    COALESCE(sum((quality->>'triples')::INTEGER) FILTER (WHERE quality ? 'triples'), 0) AS triples,
    COALESCE(sum((quality->>'raw_triples')::INTEGER) FILTER (WHERE quality ? 'raw_triples'), 0) AS raw_triples,
    count(*) FILTER (WHERE status = 'completed' AND COALESCE((quality->>'edges')::INTEGER, (quality->>'triples')::INTEGER, 0) = 0) AS zero_edge_completed,
    count(*) FILTER (WHERE status IN ('eligible', 'queued')) AS unclaimed_jobs,
    count(*) FILTER (WHERE status = 'running' AND started_at < NOW() - INTERVAL '15 minutes') AS stale_running_jobs
FROM graph_extraction_audit_v1
GROUP BY 1, 2, 3;

CREATE INDEX IF NOT EXISTS idx_chat_graph_jobs_running_started
    ON chat_graph_extraction_jobs (started_at)
    WHERE gate_status = 'running';
CREATE INDEX IF NOT EXISTS idx_document_graph_jobs_running_started
    ON document_graph_extraction_jobs (started_at)
    WHERE gate_status = 'running';
