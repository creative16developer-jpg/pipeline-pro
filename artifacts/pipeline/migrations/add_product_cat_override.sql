-- Add product-level manual category override columns
ALTER TABLE products
  ADD COLUMN IF NOT EXISTS manual_woo_cats_json      TEXT,
  ADD COLUMN IF NOT EXISTS manual_primary_woo_cat_id INTEGER,
  ADD COLUMN IF NOT EXISTS cat_source                VARCHAR(20) DEFAULT 'auto';
