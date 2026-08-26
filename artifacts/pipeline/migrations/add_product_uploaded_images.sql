-- Migration: Add uploaded_images_json to products (real duplicate-
-- image gap found live: patch 78's dedup tracking lived on the Image
-- row itself, which gets deleted and recreated every time Process runs
-- again for the same product (patch 42's correct, separate behavior)
-- -- so a full pipeline re-run (Fetch->Process->...->Upload) had no
-- memory of images already uploaded to WordPress in a previous run,
-- and genuinely re-uploaded them, creating real duplicate attachments.
-- This is a MORE DURABLE tracking layer at the product level, keyed by
-- each image's original source URL (which stays stable across
-- Process re-runs, unlike the Image row's own primary key), so dedup
-- survives a full pipeline re-run, not just a plain re-upload/re-sync.

ALTER TABLE products ADD COLUMN IF NOT EXISTS uploaded_images_json TEXT;
