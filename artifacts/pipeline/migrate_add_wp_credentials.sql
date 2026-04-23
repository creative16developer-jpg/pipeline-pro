-- Migration: Add WordPress Application Password credentials to stores
-- Safe to run multiple times (idempotent)

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name='stores' AND column_name='wp_username'
    ) THEN
        ALTER TABLE stores ADD COLUMN wp_username VARCHAR;
        RAISE NOTICE 'Added stores.wp_username';
    ELSE
        RAISE NOTICE 'stores.wp_username already exists — skipped';
    END IF;
END $$;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name='stores' AND column_name='wp_app_password'
    ) THEN
        ALTER TABLE stores ADD COLUMN wp_app_password VARCHAR;
        RAISE NOTICE 'Added stores.wp_app_password';
    ELSE
        RAISE NOTICE 'stores.wp_app_password already exists — skipped';
    END IF;
END $$;
