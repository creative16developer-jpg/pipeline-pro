-- Map step: persistent sunsky-category → woo-category mapping per store
CREATE TABLE IF NOT EXISTS sunsky_category_mappings (
  id           SERIAL PRIMARY KEY,
  store_id     INTEGER NOT NULL REFERENCES stores(id) ON DELETE CASCADE,
  sunsky_cat   TEXT    NOT NULL,
  woo_cat_id   INTEGER,
  woo_cat_name TEXT,
  created_at   TIMESTAMPTZ DEFAULT now(),
  updated_at   TIMESTAMPTZ DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_scm_store_cat
  ON sunsky_category_mappings(store_id, sunsky_cat);

-- Enrich step: AI-extracted attributes per product per pipeline run
CREATE TABLE IF NOT EXISTS product_enrich_attrs (
  id              SERIAL PRIMARY KEY,
  pipeline_job_id INTEGER NOT NULL REFERENCES pipeline_jobs(id) ON DELETE CASCADE,
  product_id      INTEGER NOT NULL REFERENCES products(id)      ON DELETE CASCADE,
  attribute       TEXT    NOT NULL,
  raw_value       TEXT    NOT NULL,
  normalised_value TEXT,
  confidence      FLOAT,
  confirmed       BOOLEAN DEFAULT FALSE,
  created_at      TIMESTAMPTZ DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_pea_pl_prod_attr
  ON product_enrich_attrs(pipeline_job_id, product_id, attribute);

-- Enrich step: persistent raw-value → woo-term normalisation dictionary per store
CREATE TABLE IF NOT EXISTS normalisation_dict (
  id         SERIAL PRIMARY KEY,
  store_id   INTEGER NOT NULL REFERENCES stores(id) ON DELETE CASCADE,
  attribute  TEXT    NOT NULL,
  raw_value  TEXT    NOT NULL,
  woo_term   TEXT    NOT NULL,
  created_at TIMESTAMPTZ DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_nd_store_attr_raw
  ON normalisation_dict(store_id, attribute, raw_value);

-- Enrich step: AI-suggested variant groups per pipeline run
CREATE TABLE IF NOT EXISTS variant_groups (
  id              SERIAL PRIMARY KEY,
  pipeline_job_id INTEGER NOT NULL REFERENCES pipeline_jobs(id) ON DELETE CASCADE,
  attribute       TEXT    NOT NULL,
  product_ids     JSONB   NOT NULL DEFAULT '[]',
  confirmed       BOOLEAN DEFAULT FALSE,
  pattern         TEXT,
  created_at      TIMESTAMPTZ DEFAULT now()
);
