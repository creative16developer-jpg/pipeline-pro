-- Migration 008: T02 — enforce valid pipeline_jobs.status values
--
-- Developer Guidelines v2.0 asked for a PostgreSQL-level Enum type. We use a
-- CHECK constraint instead of a native `CREATE TYPE ... AS ENUM` here on
-- purpose: migration 007 shows a native enum was already tried on this exact
-- column and had to be reverted in production (DatatypeMismatchError, because
-- the enum didn't include every status string the app actually writes, e.g.
-- 'content_review'). A CHECK constraint gives the same data-integrity
-- guarantee — no invalid status can be written — without ALTER TYPE's
-- fragility, and it's a plain transactional statement to change later.
--
-- Idempotent: DROP CONSTRAINT IF EXISTS makes this safe to re-run.
--
-- CRITICAL FIX, confirmed live in production: this migration re-runs on
-- EVERY server startup, not just once. 'batch_processing' was added later
-- via a separate migration (add_batch_processing.sql, which runs after this
-- file alphabetically) -- but THIS file's own constraint definition was
-- never updated to match. Once a real pipeline row existed with status =
-- 'batch_processing', THIS migration's own ADD CONSTRAINT statement started
-- failing on every subsequent restart (Postgres validates existing rows
-- against a new CHECK constraint), crashing the entire application startup
-- before add_batch_processing.sql ever got a chance to run and widen it
-- again. Fixed by including 'batch_processing' directly in this file's own
-- definition, so it can never again narrow the constraint back down
-- regardless of execution order or what any later migration does.
--
-- Valid values (as actually used across routers/pipeline.py, map_step.py,
-- enrich.py, and tasks/pipeline_tasks.py):
--   queued, running, review, enrich_review, category_review, content_review,
--   batch_processing, completed, failed, cancelled
--
-- Note: 'category_review' is included for forward-compatibility with the
-- Category Review pause described in spec Section 5.3, which is not yet
-- wired into the pipeline state machine as of this migration — see the
-- accompanying audit notes. Adding it here now means no further migration
-- is needed once that pause is implemented.

ALTER TABLE pipeline_jobs
    DROP CONSTRAINT IF EXISTS pipeline_jobs_status_check;

ALTER TABLE pipeline_jobs
    ADD CONSTRAINT pipeline_jobs_status_check
    CHECK (status IN (
        'queued', 'running', 'review', 'enrich_review',
        'category_review', 'content_review', 'batch_processing',
        'completed', 'failed', 'cancelled'
    ));
