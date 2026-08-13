-- Migration: Add focus_keyword content field (basic Yoast SEO support)
-- Completes the basic Yoast setup: SEO title and meta description were
-- already being written to Yoast's real postmeta keys; this adds the
-- third core piece, the focus keyword, generated the same way as the
-- other Content Generation fields.
-- Run once on production: psql $DATABASE_URL -f add_focus_keyword.sql

ALTER TABLE products ADD COLUMN IF NOT EXISTS focus_keyword VARCHAR;
