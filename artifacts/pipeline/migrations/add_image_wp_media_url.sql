-- Migration: Add wp_media_url to images (Review 3, item #4 -- "photos
-- uploaded twice"). Confirmed root cause: woo_image_id existed in the
-- schema but was never read or written anywhere, so every
-- upload/re-upload/re-sync blindly re-uploaded every image file to
-- WordPress's media library from scratch, creating genuine new
-- physical duplicate attachments each time. wp_media_url stores the
-- resolved URL alongside the existing woo_image_id, so a later run can
-- detect "already uploaded" and reuse it directly without any extra
-- WordPress API call at all.

ALTER TABLE images ADD COLUMN IF NOT EXISTS wp_media_url VARCHAR;
