-- Migration: Add pipeline_jobs and pipeline_logs tables
-- Also adds pipeline_job_id column to jobs table

-- pipeline_jobs: one row per pipeline run (fetch → process → generate → review → upload → sync)
CREATE TABLE IF NOT EXISTS pipeline_jobs (
    id           SERIAL PRIMARY KEY,
    store_id     INTEGER NOT NULL REFERENCES stores(id) ON DELETE CASCADE,
    fetch_job_id INTEGER NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    status       VARCHAR(20)  NOT NULL DEFAULT 'queued',
    current_step VARCHAR(30)  NULL,
    config       JSONB        NULL,
    stats_json   JSONB        NULL,
    error_message TEXT        NULL,
    created_at   TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at   TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

-- pipeline_logs: step-level log lines per pipeline
CREATE TABLE IF NOT EXISTS pipeline_logs (
    id               SERIAL PRIMARY KEY,
    pipeline_job_id  INTEGER NOT NULL REFERENCES pipeline_jobs(id) ON DELETE CASCADE,
    step             VARCHAR(50) NULL,
    level            VARCHAR(20) NOT NULL DEFAULT 'info',
    message          TEXT        NOT NULL,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Add pipeline_job_id to jobs (circular FK — added after both tables exist)
ALTER TABLE jobs ADD COLUMN IF NOT EXISTS pipeline_job_id INTEGER NULL;
ALTER TABLE jobs DROP CONSTRAINT IF EXISTS fk_jobs_pipeline_job_id;
ALTER TABLE jobs
    ADD CONSTRAINT fk_jobs_pipeline_job_id
    FOREIGN KEY (pipeline_job_id) REFERENCES pipeline_jobs(id) ON DELETE SET NULL;

-- Indexes
CREATE INDEX IF NOT EXISTS ix_pipeline_jobs_store_id    ON pipeline_jobs(store_id);
CREATE INDEX IF NOT EXISTS ix_pipeline_jobs_status      ON pipeline_jobs(status);
CREATE INDEX IF NOT EXISTS ix_pipeline_jobs_fetch_job   ON pipeline_jobs(fetch_job_id);
CREATE INDEX IF NOT EXISTS ix_pipeline_logs_plj         ON pipeline_logs(pipeline_job_id);
CREATE INDEX IF NOT EXISTS ix_jobs_pipeline_job_id      ON jobs(pipeline_job_id);
