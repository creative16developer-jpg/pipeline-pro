-- Allow a global (store_id NULL) Category Mapping rule, resolved by
-- NAME against each store's own category tree at usage time -- alongside
-- the existing per-store rules (which continue to store real,
-- store-specific WooCommerce category IDs directly, unchanged).
--
-- Client feedback: "lets do whichever is necessary to do for per store
-- thing... global and per store both rules options are there, so if
-- client want to do per store or global that is his choice."
--
-- IMPORTANT DESIGN NOTE (not enforced by this migration itself, but by
-- the application code that reads/writes global rows going forward):
-- a global row's woo_cats_json must be treated as a NAME PATH to be
-- re-resolved per store, NOT a list of directly-usable WooCommerce
-- category IDs -- unlike a per-store row, where the stored IDs are
-- already correct for that one specific store. Raw WooCommerce
-- category IDs are never portable between different store
-- installations (confirmed extensively earlier this session -- this
-- is the same root cause already fixed for per-product woo_product_id
-- and manual category overrides).
--
-- store_id was previously NOT NULL. Making it nullable is the easy
-- part; the existing UniqueConstraint(store_id, sunsky_cat) does NOT
-- correctly prevent duplicate GLOBAL rows for the same sunsky_cat --
-- confirmed by testing directly that Postgres never considers two
-- NULLs "equal" for uniqueness purposes, even inside a compound
-- constraint, so two (NULL, 'SameCategory') rows would both be
-- accepted by the existing constraint alone. A separate partial
-- unique index is required specifically for the NULL case -- same
-- general pattern as the Inventory Mapping and Extraction Rules
-- migrations earlier, but scoped per sunsky_cat here rather than a
-- single global row, since (unlike Inventory Mapping) there can
-- legitimately be many different global category rules, just at most
-- one per Sunsky category.
--
-- All statements are single statements with no internal semicolon and
-- no DO $$ ... $$ blocks, matching this app's migration runner's
-- requirements (naive split on every bare ";" in the file, no
-- DO-block awareness) -- confirmed safe by testing directly, same as
-- every other migration in this project.
ALTER TABLE sunsky_category_mappings
  ALTER COLUMN store_id DROP NOT NULL;

CREATE UNIQUE INDEX IF NOT EXISTS uq_category_mapping_global
  ON sunsky_category_mappings (sunsky_cat) WHERE store_id IS NULL;
