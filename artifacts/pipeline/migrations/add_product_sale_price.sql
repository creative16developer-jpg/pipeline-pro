-- Migration: Add sale_price to products (client feedback item #8)
-- Baselinker reference video: "options available for manual editing:
-- quantity, sales and regular prices, woo sku". regular price (price)
-- and site_sku already existed and already flow into the WooCommerce
-- upload payload correctly; sale_price never existed at all.

ALTER TABLE products ADD COLUMN IF NOT EXISTS sale_price VARCHAR;
