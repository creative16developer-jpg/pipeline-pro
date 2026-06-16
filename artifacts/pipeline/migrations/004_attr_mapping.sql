-- Migration 004: Attribute mapping system
-- AI extraction rules, attribute profiles, inventory mapping config
-- Idempotent: all statements use IF NOT EXISTS / ADD COLUMN IF NOT EXISTS

-- ── Attribute Profiles ────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS attribute_profiles (
    id          SERIAL PRIMARY KEY,
    name        TEXT NOT NULL UNIQUE,
    description TEXT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS profile_attributes (
    id            SERIAL PRIMARY KEY,
    profile_id    INTEGER NOT NULL REFERENCES attribute_profiles(id) ON DELETE CASCADE,
    woo_attr_name TEXT NOT NULL,
    required      BOOLEAN NOT NULL DEFAULT TRUE,
    sort_order    INTEGER NOT NULL DEFAULT 0,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(profile_id, woo_attr_name)
);

CREATE INDEX IF NOT EXISTS ix_profile_attributes_profile_id ON profile_attributes(profile_id);

-- ── AI Extraction Rules ───────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS ai_extraction_rules (
    id                   SERIAL PRIMARY KEY,
    woo_attr_name        TEXT NOT NULL UNIQUE,
    source_fields        VARCHAR(20) NOT NULL DEFAULT 'both',
    instruction          TEXT NOT NULL DEFAULT '',
    confidence_threshold FLOAT NOT NULL DEFAULT 0.7,
    if_not_found         VARCHAR(30) NOT NULL DEFAULT 'flag',
    default_value        TEXT,
    sort_order           INTEGER NOT NULL DEFAULT 0,
    created_at           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at           TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ── Inventory Mapping Config ──────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS inventory_mapping_configs (
    id             SERIAL PRIMARY KEY,
    store_id       INTEGER NOT NULL UNIQUE REFERENCES stores(id) ON DELETE CASCADE,
    weight_unit    VARCHAR(10) NOT NULL DEFAULT 'kg',
    dimension_unit VARCHAR(10) NOT NULL DEFAULT 'cm',
    weight_null    VARCHAR(20) NOT NULL DEFAULT 'leave_blank',
    length_null    VARCHAR(20) NOT NULL DEFAULT 'leave_blank',
    width_null     VARCHAR(20) NOT NULL DEFAULT 'leave_blank',
    height_null    VARCHAR(20) NOT NULL DEFAULT 'leave_blank',
    weight_default    VARCHAR(30),
    length_default    VARCHAR(30),
    width_default     VARCHAR(30),
    height_default    VARCHAR(30),
    created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS ix_inventory_mapping_configs_store_id ON inventory_mapping_configs(store_id);

-- ── Add profile_id FK to sunsky_category_mappings ─────────────────────────────
ALTER TABLE sunsky_category_mappings
    ADD COLUMN IF NOT EXISTS profile_id INTEGER REFERENCES attribute_profiles(id) ON DELETE SET NULL;

-- ── Add source + flagged to product_enrich_attrs ──────────────────────────────
ALTER TABLE product_enrich_attrs
    ADD COLUMN IF NOT EXISTS source  VARCHAR(20) NOT NULL DEFAULT 'ai';

ALTER TABLE product_enrich_attrs
    ADD COLUMN IF NOT EXISTS flagged BOOLEAN NOT NULL DEFAULT FALSE;
