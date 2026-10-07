-- "IF title contains" condition for Category Mapping (client milestone
-- point 2): several rules per Sunsky category, one per set of title words.
-- Existing rules get title_contains = '' and behave exactly as before.
-- Idempotent; no DO blocks (the migration runner splits on ";").
ALTER TABLE sunsky_category_mappings ADD COLUMN IF NOT EXISTS title_contains TEXT NOT NULL DEFAULT '';
ALTER TABLE sunsky_category_mappings DROP CONSTRAINT IF EXISTS sunsky_category_mappings_store_id_sunsky_cat_key;
DROP INDEX IF EXISTS ux_scm_store_cat;
-- (uq_category_mapping_store_title on (store_id, sunsky_cat, title_contains) and
-- uq_category_mapping_global_title were replaced by per-Sunsky-ID unique indexes,
-- see add_sunsky_cat_id_rule_key.sql -- they must not be re-created here, since
-- every migration file runs again on each start.)
DROP INDEX IF EXISTS uq_category_mapping_global;
