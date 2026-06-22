-- 005_woo_attributes.sql
-- WooCommerce product attributes + terms cache (synced from the store)
-- All statements are idempotent (IF NOT EXISTS).

CREATE TABLE IF NOT EXISTS woo_attributes (
    id         SERIAL PRIMARY KEY,
    store_id   INTEGER NOT NULL REFERENCES stores(id) ON DELETE CASCADE,
    woo_id     INTEGER NOT NULL,
    name       VARCHAR(200) NOT NULL,
    slug       VARCHAR(200) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (store_id, woo_id)
);

CREATE TABLE IF NOT EXISTS woo_attribute_terms (
    id           SERIAL PRIMARY KEY,
    attribute_id INTEGER NOT NULL REFERENCES woo_attributes(id) ON DELETE CASCADE,
    store_id     INTEGER NOT NULL REFERENCES stores(id) ON DELETE CASCADE,
    woo_id       INTEGER NOT NULL,
    name         VARCHAR(200) NOT NULL,
    slug         VARCHAR(200) NOT NULL,
    UNIQUE (attribute_id, woo_id)
);
