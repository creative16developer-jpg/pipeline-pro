-- Migration 007: Fix pipeline_jobs.status column type
--
-- On some servers the status column was created as a native PostgreSQL enum
-- (pipeline_job_status) instead of VARCHAR(20) as the original migration
-- intended.  This causes SQLAlchemy to fail with:
--   DatatypeMismatchError: column "status" is of type pipeline_job_status
--   but expression is of type character varying
--
-- This migration converts the column back to VARCHAR(20) and drops the
-- now-unused enum type.  It is a no-op when the column is already VARCHAR.

DO $$
BEGIN
    -- Only alter if the column is currently an enum type
    IF EXISTS (
        SELECT 1
        FROM   information_schema.columns
        WHERE  table_name   = 'pipeline_jobs'
          AND  column_name  = 'status'
          AND  udt_name     = 'pipeline_job_status'
    ) THEN
        ALTER TABLE pipeline_jobs
            ALTER COLUMN status TYPE VARCHAR(20) USING status::text;

        RAISE NOTICE 'pipeline_jobs.status converted from enum to VARCHAR(20)';

        -- Drop the now-unused type (safe — only pipeline_jobs.status used it)
        DROP TYPE IF EXISTS pipeline_job_status;

        RAISE NOTICE 'pipeline_job_status enum type dropped';
    ELSE
        RAISE NOTICE 'pipeline_jobs.status is already VARCHAR — no action taken';
    END IF;
END$$;
