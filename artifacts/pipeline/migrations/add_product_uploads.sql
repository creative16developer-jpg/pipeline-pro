-- Upload history per product (client milestone point 10: show which
-- pipeline uploaded each product and its SKU in WooCommerce). Idempotent;
-- no DO blocks (the runner splits on ";").
CREATE TABLE IF NOT EXISTS product_uploads (
  id              SERIAL PRIMARY KEY,
  product_id      INTEGER NOT NULL REFERENCES products(id) ON DELETE CASCADE,
  store_id        INTEGER REFERENCES stores(id) ON DELETE CASCADE,
  pipeline_job_id INTEGER REFERENCES pipeline_jobs(id) ON DELETE SET NULL,
  job_id          INTEGER REFERENCES jobs(id) ON DELETE SET NULL,
  woo_product_id  INTEGER,
  woo_sku         VARCHAR(100),
  action          VARCHAR(20) NOT NULL,
  uploaded_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_product_uploads_product_id ON product_uploads (product_id);
CREATE INDEX IF NOT EXISTS ix_product_uploads_pipeline_job_id ON product_uploads (pipeline_job_id);
-- Backfill past uploads from the upload jobs' own log lines, e.g.
-- "  PU981B → CREATED woo_id=21803" / "  TBD06061769 → UPDATED woo_id=21791
-- (full payload re-sent)" / "  X → existing SKU found, UPDATED woo_id=N
-- instead of creating". Only rows not backfilled yet (same job + product);
-- woo_sku left NULL (old logs don't record the SKU sent).
-- NO colon-word sequences anywhere in executable SQL: the runner executes
-- statements through SQLAlchemy text(), which reads them as bind
-- parameters (a "(?:...)" regex group made startup fail in testing).
INSERT INTO product_uploads (product_id, store_id, pipeline_job_id, job_id, woo_product_id, woo_sku, action, uploaded_at)
SELECT p.id, COALESCE(j.store_id, pj.store_id), j.pipeline_job_id, j.id, CAST(m.parts[4] AS INTEGER), NULL, lower(m.parts[3]), jl.created_at
FROM job_logs jl
JOIN jobs j ON j.id = jl.job_id AND j.type = 'upload'
LEFT JOIN pipeline_jobs pj ON pj.id = j.pipeline_job_id
CROSS JOIN LATERAL (SELECT regexp_match(jl.message, '^\s*(\S+) → (existing SKU found, )?(CREATED|UPDATED) woo_id=(\d+)') AS parts) m
JOIN products p ON p.sku = m.parts[1]
WHERE jl.message LIKE '%woo_id=%' AND m.parts IS NOT NULL
  AND NOT EXISTS (SELECT 1 FROM product_uploads u WHERE u.job_id = j.id AND u.product_id = p.id)
