-- Migration: Add sunsky_cat_id to sunsky_category_mappings (missing piece)
-- This column was added to the SQLAlchemy model as groundwork for a more
-- robust ID-based category matching fix, but the migration was never
-- created -- meaning every query touching this table has been failing
-- with "column sunsky_category_mappings.sunsky_cat_id does not exist"
-- since that model change deployed, aborting the transaction and
-- cascading into every subsequent write in the same session (including
-- the pipeline_jobs status update, which is why runs appeared to hang).
-- This is a genuine mistake from earlier in this session -- sorry for
-- the trouble it caused. Run immediately:
--   psql $DATABASE_URL -f add_sunsky_cat_id.sql

ALTER TABLE sunsky_category_mappings ADD COLUMN IF NOT EXISTS sunsky_cat_id VARCHAR;
CREATE INDEX IF NOT EXISTS ix_sunsky_category_mappings_sunsky_cat_id
    ON sunsky_category_mappings (sunsky_cat_id);
