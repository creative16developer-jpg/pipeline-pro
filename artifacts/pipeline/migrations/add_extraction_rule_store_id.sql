-- Make AI Extraction Rules per-store instead of global.
--
-- Client feedback confirmed live: "Extraction rules need to be
-- individual for each site / Right now they are same for each site."
-- woo_attr_name was globally UNIQUE, so only one rule per attribute
-- name could ever exist across the whole application.
--
-- Follows the exact same optional-override pattern already used
-- successfully by the sibling attribute_mapping_rules table:
-- store_id=NULL means the rule applies globally to all stores (the
-- fallback); a specific store_id creates an override for that store.
-- Existing rules are left with store_id=NULL here, so current
-- behavior is completely unchanged until an operator explicitly
-- creates a new store-specific rule.
ALTER TABLE ai_extraction_rules
  ADD COLUMN IF NOT EXISTS store_id INTEGER REFERENCES stores(id) ON DELETE CASCADE;

-- Drop the old global-uniqueness constraint on woo_attr_name alone.
-- Confirmed via direct testing that this constraint name (SQLAlchemy's
-- default "<table>_<column>_key" pattern for a bare unique=True
-- column) is exactly what Postgres actually assigned for this table.
--
-- NOTE: this migration deliberately avoids DO $$ ... $$ blocks
-- entirely. This app's migration runner (main.py's _run_migrations)
-- strips comments then does a NAIVE split on every bare ";" in the
-- whole file, with no awareness of DO-block boundaries -- a DO block
-- containing an internal ";" (e.g. after an ALTER TABLE inside an IF)
-- would be shredded into broken fragments and crash startup, the same
-- class of bug already hit once with a stray ";" inside a comment.
-- DROP CONSTRAINT IF EXISTS and CREATE UNIQUE INDEX IF NOT EXISTS
-- (used below) are both single statements with no internal semicolon,
-- confirmed safe against this exact runner by testing them directly.
ALTER TABLE ai_extraction_rules
  DROP CONSTRAINT IF EXISTS ai_extraction_rules_woo_attr_name_key;

-- New compound uniqueness: one rule per (store_id, woo_attr_name) pair,
-- so the SAME attribute name can have both a global rule (store_id
-- NULL) and separate per-store override rules simultaneously. A
-- UNIQUE INDEX enforces the identical guarantee as a named UNIQUE
-- CONSTRAINT would via ALTER TABLE ADD CONSTRAINT -- used here
-- instead specifically because Postgres has no "ADD CONSTRAINT IF NOT
-- EXISTS" (confirmed by testing it directly: syntax error, not a
-- no-op), while CREATE UNIQUE INDEX IF NOT EXISTS is directly
-- supported and idempotent as a single statement.
CREATE UNIQUE INDEX IF NOT EXISTS uq_extraction_rule_store_attr
  ON ai_extraction_rules (store_id, woo_attr_name);
