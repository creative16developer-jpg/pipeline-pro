-- Allow a global (store_id NULL) Inventory Mapping config, used as the
-- default for any store with no config of its own -- alongside the
-- existing per-store configs, unchanged.
--
-- Client feedback: "lets do whichever is necessary to do for per store
-- thing... global and per store both rules options are there, so if
-- client want to do per store or global that is his choice."
--
-- store_id was previously NOT NULL + plain UNIQUE (exactly one config
-- per store, no global concept at all). Making it nullable alone isn't
-- enough: a plain UNIQUE constraint does NOT enforce "at most one NULL
-- row" in standard SQL (NULL is never considered equal to another NULL
-- for uniqueness purposes) -- confirmed by testing directly. Needs two
-- separate PARTIAL unique indexes instead: one preserving the existing
-- per-store uniqueness for non-null values, one separately ensuring at
-- most one global (NULL) row can ever exist.
--
-- All statements here are single statements with no internal
-- semicolon and no DO $$ ... $$ blocks -- this app's migration runner
-- does a naive split on every bare ";" in the file with no DO-block
-- awareness (confirmed by testing directly, and already the root
-- cause of two earlier migration bugs in this project). Every
-- statement below (ADD COLUMN IF NOT EXISTS is a no-op here since the
-- column already exists NOT NULL -- included only for
-- self-documentation of intent, DROP CONSTRAINT IF EXISTS, ALTER
-- COLUMN DROP NOT NULL, CREATE UNIQUE INDEX IF NOT EXISTS) is
-- confirmed idempotent and safe to re-run on every startup.
ALTER TABLE inventory_mapping_configs
  ALTER COLUMN store_id DROP NOT NULL;

ALTER TABLE inventory_mapping_configs
  DROP CONSTRAINT IF EXISTS inventory_mapping_configs_store_id_key;

CREATE UNIQUE INDEX IF NOT EXISTS uq_inventory_config_store
  ON inventory_mapping_configs (store_id) WHERE store_id IS NOT NULL;

CREATE UNIQUE INDEX IF NOT EXISTS uq_inventory_config_global
  ON inventory_mapping_configs ((1)) WHERE store_id IS NULL;
