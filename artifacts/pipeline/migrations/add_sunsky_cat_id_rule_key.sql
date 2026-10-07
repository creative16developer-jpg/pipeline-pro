-- Category Mapping rules are per Sunsky category ID (client, PL-164): Sunsky
-- uses the same NAME for several categories ("Protection & Cases" = 111932,
-- 111943, ...), and a rule made for one was applied to all of them. A rule
-- with sunsky_cat_id applies to that ID only, a rule without it to every
-- category of that name -- so two rules may now share store + name + title
-- words when their Sunsky IDs differ. The ID (NULL counted as '') becomes
-- part of the unique key. Existing rows are not changed.
-- Runs after add_sunsky_cat_id.sql (file name order). Idempotent, no DO blocks.
ALTER TABLE sunsky_category_mappings DROP CONSTRAINT IF EXISTS uq_category_mapping_store_title;
DROP INDEX IF EXISTS uq_category_mapping_store_title;
DROP INDEX IF EXISTS uq_category_mapping_global_title;
CREATE UNIQUE INDEX IF NOT EXISTS uq_category_mapping_store_id_title ON sunsky_category_mappings (store_id, sunsky_cat, COALESCE(sunsky_cat_id, ''), title_contains) WHERE store_id IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS uq_category_mapping_global_id_title ON sunsky_category_mappings (sunsky_cat, COALESCE(sunsky_cat_id, ''), title_contains) WHERE store_id IS NULL
