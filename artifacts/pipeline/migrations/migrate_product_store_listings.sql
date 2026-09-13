-- Backfill product_store_listings (the new per-store WooCommerce
-- identity + manual category override table) from the old, single-value
-- Product columns it replaces, for products that were already uploaded
-- or manually category-overridden before this table existed.
--
-- Table itself is created by SQLAlchemy's Base.metadata.create_all
-- (runs before migrations, in the same startup transaction) since
-- ProductStoreListing is a real model now, not raw SQL here.
--
-- Store inference: uses the product's OWN fetch_job -> that job's
-- store_id, as the best available guess for "which store this
-- existing woo_product_id / manual override actually belongs to".
-- KNOWN LIMITATION: a product fetched more than once for different
-- stores over time only has ONE fetch_job_id (the most recent), so an
-- older upload to a DIFFERENT, earlier store than the one currently
-- linked would be backfilled against the wrong store here. Also
-- skips products with no fetch_job_id at all (e.g. added via CSV
-- import rather than a Sunsky fetch) -- those aren't covered by this
-- best-effort backfill and would need manual reconciliation if any
-- turn out to need it. Safe to re-run every startup (ON CONFLICT DO
-- NOTHING, matching every other migration's idempotency requirement
-- in this file's runner) -- inserts once, no-ops on every later run.
--
-- BUG FIX (client feedback confirmed live -- broke startup entirely):
-- a product's fetch_job_id CAN point to a job that itself has
-- store_id = NULL -- specifically csv_import jobs, which are created
-- with store_id=None (see csv_import.py) and ALSO set
-- existing.fetch_job_id = job.id on the product row they touch. The
-- original WHERE only checked p.fetch_job_id IS NOT NULL, which
-- guarantees a MATCHING job row exists via the JOIN, but doesn't
-- guarantee THAT job's own store_id is non-null -- so a product last
-- touched by a CSV import job, but which also had a real
-- woo_product_id set some other way, hit product_store_listings'
-- NOT NULL constraint on store_id and crashed startup entirely
-- ("null value in column store_id... violates not-null constraint").
-- Explicitly requiring j.store_id IS NOT NULL, in addition to the
-- existing checks, is only tested in my own sandbox with a product
-- that had NO fetch_job_id at all -- a different, narrower case than
-- this one (fetch_job_id set, but pointing to a store-less job) that
-- I missed the first time.
INSERT INTO product_store_listings
  (product_id, store_id, woo_product_id, manual_woo_cats_json, manual_primary_woo_cat_id, cat_source)
SELECT p.id, j.store_id, p.woo_product_id, p.manual_woo_cats_json, p.manual_primary_woo_cat_id,
       COALESCE(p.cat_source, 'auto')
FROM products p
JOIN jobs j ON j.id = p.fetch_job_id
WHERE p.fetch_job_id IS NOT NULL
  AND j.store_id IS NOT NULL
  AND (p.woo_product_id IS NOT NULL OR p.manual_woo_cats_json IS NOT NULL)
ON CONFLICT (product_id, store_id) DO NOTHING;
