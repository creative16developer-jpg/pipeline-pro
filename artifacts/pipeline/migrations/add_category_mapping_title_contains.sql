-- "IF title contains" condition for Category Mapping (client milestone
-- point 2): several rules per Sunsky category, one per set of title words.
-- Existing rules get title_contains = '' and behave exactly as before.
-- Idempotent; no DO blocks (the migration runner splits on ";").
ALTER TABLE sunsky_category_mappings ADD COLUMN IF NOT EXISTS title_contains TEXT NOT NULL DEFAULT '';
ALTER TABLE sunsky_category_mappings DROP CONSTRAINT IF EXISTS sunsky_category_mappings_store_id_sunsky_cat_key;
DROP INDEX IF EXISTS ux_scm_store_cat;
CREATE UNIQUE INDEX IF NOT EXISTS uq_category_mapping_store_title ON sunsky_category_mappings (store_id, sunsky_cat, title_contains);
DROP INDEX IF EXISTS uq_category_mapping_global;
CREATE UNIQUE INDEX IF NOT EXISTS uq_category_mapping_global_title ON sunsky_category_mappings (sunsky_cat, title_contains) WHERE store_id IS NULL
