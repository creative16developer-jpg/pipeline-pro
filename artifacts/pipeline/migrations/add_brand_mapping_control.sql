-- Brand handling: a store-level toggle for whether Sunsky-detected
-- brand gets mapped at all, plus a genuine per-product, per-store
-- manual brand override (mirroring category's existing
-- manual_woo_cats_json / cat_source pattern).
--
-- Client feedback, exact spec: "Need to have an option to map brand
-- from Sunsky or not... In any case I need to be able to manual
-- editing in the steps and put whatever brand I want." Confirmed the
-- prior code had NO awareness of a manually-set brand at all (Upload
-- and Sync both called set_product_brand unconditionally every run,
-- silently overwriting any manual choice) and no way to disable
-- Sunsky-based brand detection at all for a store.
--
-- Two separate ALTER TABLE statements (one per table) -- the
-- migration runner splits on every bare ";" and runs each statement
-- individually, so multiple statements per file are fine; the only
-- constraint is no internal semicolon WITHIN a single statement (e.g.
-- a DO $$ ... $$ block), which neither of these has.
ALTER TABLE stores
  ADD COLUMN IF NOT EXISTS map_brand_from_sunsky BOOLEAN NOT NULL DEFAULT true;

ALTER TABLE product_store_listings
  ADD COLUMN IF NOT EXISTS manual_brand_name VARCHAR(255),
  ADD COLUMN IF NOT EXISTS brand_source VARCHAR(20) NOT NULL DEFAULT 'auto';
