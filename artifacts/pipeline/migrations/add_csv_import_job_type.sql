-- Add csv_import value to job_type enum (idempotent via DO block)
-- Runs with AUTOCOMMIT isolation (see _run_enum_migrations in main.py)
ALTER TYPE job_type ADD VALUE IF NOT EXISTS 'csv_import';
