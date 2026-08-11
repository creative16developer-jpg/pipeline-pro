-- Migration: Add optional deterministic "selector" to ai_extraction_rules
-- A simple tag[.class|#id] selector (e.g. "h2.product-main-title") tried
-- against the product's raw description HTML before falling back to AI.
-- Run once on production: psql DATABASE_URL -f add_extraction_rule_selector.sql

ALTER TABLE ai_extraction_rules ADD COLUMN IF NOT EXISTS selector TEXT;
