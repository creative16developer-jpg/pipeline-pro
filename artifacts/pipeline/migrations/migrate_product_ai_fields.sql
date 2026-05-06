-- Migration: Add AI-generated content columns to products table
-- Run once on production: psql $DATABASE_URL -f migrate_product_ai_fields.sql

ALTER TABLE products ADD COLUMN IF NOT EXISTS short_description TEXT;
ALTER TABLE products ADD COLUMN IF NOT EXISTS slug VARCHAR;
ALTER TABLE products ADD COLUMN IF NOT EXISTS meta_title VARCHAR;
ALTER TABLE products ADD COLUMN IF NOT EXISTS meta_description TEXT;
ALTER TABLE products ADD COLUMN IF NOT EXISTS tags TEXT;
ALTER TABLE products ADD COLUMN IF NOT EXISTS image_alt TEXT;
ALTER TABLE products ADD COLUMN IF NOT EXISTS image_names TEXT;
ALTER TABLE products ADD COLUMN IF NOT EXISTS content_source JSON;
