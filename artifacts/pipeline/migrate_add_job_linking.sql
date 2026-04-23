-- Migration: Add job linking columns
-- Run this ONCE on the server after pulling the latest code:
--   psql $DATABASE_URL -f migrate_add_job_linking.sql
--
-- Safe to run multiple times — uses IF NOT EXISTS / DO $$ guards.

-- 1. Add source_job_id to jobs (links process→fetch, upload→process)
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name='jobs' AND column_name='source_job_id'
    ) THEN
        ALTER TABLE jobs
            ADD COLUMN source_job_id INTEGER REFERENCES jobs(id) ON DELETE SET NULL;
        CREATE INDEX IF NOT EXISTS ix_jobs_source_job_id ON jobs(source_job_id);
        RAISE NOTICE 'Added jobs.source_job_id';
    ELSE
        RAISE NOTICE 'jobs.source_job_id already exists — skipped';
    END IF;
END $$;

-- 2. Add fetch_job_id to products (which fetch job created this product)
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name='products' AND column_name='fetch_job_id'
    ) THEN
        ALTER TABLE products
            ADD COLUMN fetch_job_id INTEGER REFERENCES jobs(id) ON DELETE SET NULL;
        CREATE INDEX IF NOT EXISTS ix_products_fetch_job_id ON products(fetch_job_id);
        RAISE NOTICE 'Added products.fetch_job_id';
    ELSE
        RAISE NOTICE 'products.fetch_job_id already exists — skipped';
    END IF;
END $$;
