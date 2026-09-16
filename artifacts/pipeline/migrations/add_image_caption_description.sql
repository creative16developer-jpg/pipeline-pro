-- Two new Product columns for WordPress media library's Caption and
-- Description fields (distinct from image_alt / Alt Text, and from
-- the product's own WooCommerce description).
--
-- Client feedback confirmed live via WordPress media library
-- screenshot: "Additional image fields you didn't put them here? all
-- these fields should be here of wordpress media." Registered as
-- real, independently-generated content_service fields (see
-- services/content_service.py), matching image_alt/image_names'
-- existing pattern exactly, rather than the previous hardcoded reuse
-- of image_alt's value with no field, toggle, or Settings visibility
-- of its own.
--
-- Single statement, no internal semicolon, no DO $$ ... $$ block --
-- matches this app's migration runner's requirements (naive split on
-- every bare ";" in the file, no DO-block awareness), same as every
-- other migration in this project.
ALTER TABLE products
  ADD COLUMN IF NOT EXISTS image_caption TEXT,
  ADD COLUMN IF NOT EXISTS image_description TEXT;
