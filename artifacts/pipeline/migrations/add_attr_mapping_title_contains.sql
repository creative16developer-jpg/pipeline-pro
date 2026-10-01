-- "If title contains" for Attribute Mapping rules (client point 2, same as
-- Category Mapping). Existing rules get '' = no title condition. Idempotent.
ALTER TABLE attribute_mapping_rules ADD COLUMN IF NOT EXISTS title_contains TEXT NOT NULL DEFAULT ''
