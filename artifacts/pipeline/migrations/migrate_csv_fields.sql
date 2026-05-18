-- CSV Import: add csv_title and site_sku to products, create csv_mappings table
-- Safe to run multiple times (idempotent)

ALTER TABLE products ADD COLUMN IF NOT EXISTS csv_title VARCHAR(200);
ALTER TABLE products ADD COLUMN IF NOT EXISTS site_sku VARCHAR(100);

CREATE TABLE IF NOT EXISTS csv_mappings (
    id          SERIAL PRIMARY KEY,
    sunsky_sku  VARCHAR(100) NOT NULL,
    site_sku    VARCHAR(100),
    csv_title   VARCHAR(200),
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_csv_mappings_sunsky_sku
    ON csv_mappings(sunsky_sku);
