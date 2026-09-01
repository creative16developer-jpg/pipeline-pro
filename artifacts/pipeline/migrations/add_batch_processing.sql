-- Migration: add Claude Batch Processing support
--
-- Client confirmed both build-plan questions (opt-in toggle per pipeline,
-- OK with the pipeline pausing while the batch processes) and confirmed
-- starting with Anthropic only, before extending to other providers later.
--
-- Adds 'batch_processing' to the existing status CHECK constraint (migration
-- 008) -- a new pause state, alongside enrich_review/review, while an
-- Anthropic Message Batch is being processed. Idempotent: DROP CONSTRAINT
-- IF EXISTS makes this safe to re-run, matching migration 008's own pattern.
--
-- Adds three new columns to pipeline_jobs to track the active batch:
-- batch_id (Anthropic's own batch identifier, needed to poll status and
-- fetch results), batch_submitted_at (when it was submitted, useful for
-- diagnosing anything that seems stuck), and use_batch_processing (the
-- operator's opt-in choice at pipeline-creation time, read by the Generate
-- step to decide whether to submit a batch instead of generating
-- synchronously).

ALTER TABLE pipeline_jobs
    DROP CONSTRAINT IF EXISTS pipeline_jobs_status_check;

ALTER TABLE pipeline_jobs
    ADD CONSTRAINT pipeline_jobs_status_check
    CHECK (status IN (
        'queued', 'running', 'review', 'enrich_review',
        'category_review', 'content_review', 'batch_processing',
        'completed', 'failed', 'cancelled'
    ));

ALTER TABLE pipeline_jobs ADD COLUMN IF NOT EXISTS batch_id VARCHAR(100);
ALTER TABLE pipeline_jobs ADD COLUMN IF NOT EXISTS batch_submitted_at TIMESTAMPTZ;
ALTER TABLE pipeline_jobs ADD COLUMN IF NOT EXISTS use_batch_processing BOOLEAN NOT NULL DEFAULT FALSE;
