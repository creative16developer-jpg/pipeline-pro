-- Per-store toggle for whether the pipeline may auto-create a missing
-- WooCommerce category/attribute/term, or must only use taxonomy the
-- operator has already set up themselves.
--
-- Client feedback (Review_4.docx, item #12): "The pipeline creates
-- new categories/attributes in Woo which are not defined before the
-- pipeline."
--
-- DEFAULT TRUE preserves existing behavior exactly -- the pipeline has
-- always auto-created missing taxonomy rather than silently dropping
-- it, and this migration must not change that for any existing store
-- without the operator explicitly opting into the stricter behavior.
--
-- Single statement, no internal semicolon, no DO $$ ... $$ block --
-- matches this app's migration runner's requirements (naive split on
-- every bare ";" in the file, no DO-block awareness), same as every
-- other migration in this project.
ALTER TABLE stores
  ADD COLUMN IF NOT EXISTS allow_auto_create_taxonomy BOOLEAN NOT NULL DEFAULT true;
