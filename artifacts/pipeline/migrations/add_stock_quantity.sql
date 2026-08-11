-- Migration: Add real numeric stock_quantity to products table
-- Previously the WooCommerce upload payload hardcoded stock_quantity to
-- 10 or 0 based on the in_stock/out_of_stock boolean, never the real
-- number. This column holds the real value when we have it (from Sunsky's
-- stockNum or an optional CSV "QTY" column). The 10/0 heuristic remains
-- only as a fallback for rows where we never got real data.
-- Run once on production: psql $DATABASE_URL -f add_stock_quantity.sql

ALTER TABLE products ADD COLUMN IF NOT EXISTS stock_quantity INTEGER;
