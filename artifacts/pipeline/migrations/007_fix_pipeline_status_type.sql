-- Migration 007: fix pipeline_jobs.status column type
-- On some servers this column was created as a native PostgreSQL enum
-- (pipeline_job_status) instead of VARCHAR(20).  SQLAlchemy then fails with:
--   DatatypeMismatchError: column "status" is of type pipeline_job_status
--   but expression is of type character varying
--
-- These two statements are unconditional and safe to re-run:
--   * ALTER TABLE is a no-op rewrite when the column is already VARCHAR.
--   * DROP TYPE IF EXISTS silently skips when the type does not exist.

ALTER TABLE pipeline_jobs
    ALTER COLUMN status TYPE VARCHAR(20) USING status::text;

DROP TYPE IF EXISTS pipeline_job_status
