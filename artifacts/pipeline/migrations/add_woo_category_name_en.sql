-- Migration: Add name_en to woo_categories (client feedback item #8)
-- Client feedback: "here this is the new category in Woo in bulgarian.
-- that person will always know what will be English of that Bulgaria
-- language. So he can map easily."
-- Stores a cached English translation of each (Bulgarian, or any
-- non-English) WooCommerce category name, populated on demand via
-- POST /api/stores/{id}/categories/translate using the AI provider
-- already configured for Content Generation. NULL until translated.

ALTER TABLE woo_categories ADD COLUMN IF NOT EXISTS name_en TEXT;
