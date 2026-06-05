-- Add woo_attr_name to product_enrich_attrs and normalisation_dict
-- Allows renaming the WooCommerce attribute during Enrich review
ALTER TABLE product_enrich_attrs ADD COLUMN IF NOT EXISTS woo_attr_name TEXT;
ALTER TABLE normalisation_dict   ADD COLUMN IF NOT EXISTS woo_attr_name TEXT;
