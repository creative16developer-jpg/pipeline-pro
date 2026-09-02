-- Migration: add Claude Batch Processing support
--
-- Client confirmed both build-plan questions (opt-in toggle per pipeline,
-- OK with the pipeline pausing while the batch processes) and confirmed
-- starting with Anthropic only, before extending to other providers later.
--
-- The 'batch_processing' status value itself is added to the CHECK
-- constraint directly in migration 008_pipeline_status_check_constraint.sql,
-- not here -- CRITICAL LESSON learned from a real production crash: this
-- file originally also modified the same constraint (DROP + re-ADD), but
-- since migration 008 runs alphabetically BEFORE this file and re-runs its
-- own (at-the-time outdated) constraint definition on every server startup,
-- 008's own ADD CONSTRAINT failed once a real row existed with status =
-- 'batch_processing' -- crashing startup before this file ever got a chance
-- to run and fix it again. Two migrations independently managing the same
-- constraint is fragile; 008 is now the single, canonical source of truth
-- for it, and this file only handles what's genuinely new here: the three
-- columns below.
--
-- Adds three new columns to pipeline_jobs to track the active batch:
-- batch_id (Anthropic's own batch identifier, needed to poll status and
-- fetch results), batch_submitted_at (when it was submitted, useful for
-- diagnosing anything that seems stuck), and use_batch_processing (the
-- operator's opt-in choice at pipeline-creation time, read by the Generate
-- step to decide whether to submit a batch instead of generating
-- synchronously).

ALTER TABLE pipeline_jobs ADD COLUMN IF NOT EXISTS batch_id VARCHAR(100);
ALTER TABLE pipeline_jobs ADD COLUMN IF NOT EXISTS batch_submitted_at TIMESTAMPTZ;
ALTER TABLE pipeline_jobs ADD COLUMN IF NOT EXISTS use_batch_processing BOOLEAN NOT NULL DEFAULT FALSE;

