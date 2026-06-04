ALTER TABLE sunsky_category_mappings ADD COLUMN IF NOT EXISTS woo_cats_json TEXT;
ALTER TABLE sunsky_category_mappings ADD COLUMN IF NOT EXISTS primary_woo_cat_id INTEGER;
ALTER TABLE sunsky_category_mappings ADD COLUMN IF NOT EXISTS times_used INTEGER DEFAULT 0;
ALTER TABLE sunsky_category_mappings ADD COLUMN IF NOT EXISTS last_used_at TIMESTAMP WITH TIME ZONE;
