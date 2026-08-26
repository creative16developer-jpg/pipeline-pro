"""
Background job processing tasks.
Tasks run as asyncio coroutines via asyncio.create_task() — no Celery/Redis required.
"""
import sys
from pathlib import Path

_pkg_dir = str(Path(__file__).parent.parent.resolve())
if _pkg_dir not in sys.path:
    sys.path.insert(0, _pkg_dir)

import asyncio
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

# ── Sunsky category tree cache ─────────────────────────────────────────────
# The BFS through the Sunsky tree easily hits Sunsky's per-minute API call
# limit.  We persist discovered categories to a JSON file and reuse them on
# subsequent syncs.  Entries that are older than CACHE_TTL_DAYS are evicted.
_CAT_CACHE_FILE = Path(__file__).parent.parent / "cache" / "sunsky_cat_cache.json"
_CACHE_TTL_DAYS = 7


def _load_cat_cache() -> dict[str, dict]:
    """Load persisted category entries. Returns {} if file missing or all expired."""
    try:
        if not _CAT_CACHE_FILE.exists():
            return {}
        raw = json.loads(_CAT_CACHE_FILE.read_text(encoding="utf-8"))
        cutoff = datetime.now(timezone.utc) - timedelta(days=_CACHE_TTL_DAYS)
        return {
            k: v for k, v in raw.items()
            if datetime.fromisoformat(v.get("_cached_at", "2000-01-01T00:00:00+00:00")) > cutoff
        }
    except Exception:
        return {}


def _save_cat_cache(entries: dict[str, dict]) -> None:
    """Merge new entries into the persisted cache file."""
    try:
        _CAT_CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        existing: dict[str, dict] = {}
        if _CAT_CACHE_FILE.exists():
            try:
                existing = json.loads(_CAT_CACHE_FILE.read_text(encoding="utf-8"))
            except Exception:
                pass
        existing.update(entries)
        _CAT_CACHE_FILE.write_text(
            json.dumps(existing, ensure_ascii=False),
            encoding="utf-8",
        )
    except Exception as e:
        print(f"[cat_cache] Could not save: {e}")


async def _execute_job(job_id: int):
    import sys
    from pathlib import Path
    _d = str(Path(__file__).parent.parent.resolve())
    if _d not in sys.path:
        sys.path.insert(0, _d)

    from database import make_session_factory
    from models.models import Job, JobStatus, JobType, LogLevel, JobLog
    from datetime import datetime, timezone

    CelerySession, celery_engine = make_session_factory()

    try:
        async with CelerySession() as db:
            job = await db.get(Job, job_id)
            if not job:
                return

            job.status = JobStatus.running
            job.started_at = datetime.now(timezone.utc)
            await db.commit()

            try:
                if job.type == JobType.fetch:
                    await _run_fetch(db, job)
                elif job.type == JobType.process:
                    await _run_process(db, job)
                elif job.type == JobType.upload:
                    await _run_upload(db, job)
                elif job.type == JobType.sync:
                    await _run_sync(db, job)

                job.status = JobStatus.completed
                job.progress_percent = 100.0
            except Exception as e:
                job.status = JobStatus.failed
                job.error_message = str(e)
                await _log(db, job.id, LogLevel.error, f"Job failed: {e}")

            job.completed_at = datetime.now(timezone.utc)
            await db.commit()

            # ── Auto-advance pipeline chain ────────────────────────────────
            # If another pending job declares this job as its source, trigger
            # it automatically so the full pipeline runs without manual steps.
            if job.status == JobStatus.completed:
                from sqlalchemy import select as _sa_select
                next_job = (
                    await db.execute(
                        _sa_select(Job).where(
                            Job.source_job_id == job.id,
                            Job.status == JobStatus.pending,
                        ).limit(1)
                    )
                ).scalar_one_or_none()
                if next_job:
                    await _log(
                        db, next_job.id, LogLevel.info,
                        f"[pipeline] Auto-starting job #{next_job.id} "
                        f"({next_job.type.value}) after job #{job.id} completed",
                    )
                    await db.commit()
                    asyncio.create_task(_execute_job(next_job.id))
    finally:
        await celery_engine.dispose()


def _apply_inventory_mapping(raw: dict, config: Optional[dict]) -> dict:
    """
    Build WooCommerce weight/dimensions fields from Sunsky's raw product
    data, honoring the store's Inventory Mapping config (unit conversion,
    null-handling, defaults). Returns a dict with optional 'weight' and
    'dimensions' keys ready to merge into the upload payload.

    Sunsky provides weight in kg and dimensions in cm. Prefers package
    (shipping) dimensions (packWeight/packLength/packWidth/packHeight) over
    unit/item dimensions (unitWeight/unitLength/unitWidth/unitHeight) as the
    primary source, since package dimensions are what actually ships --
    falls back to unit dimensions if package data is missing.
    """
    cfg = config or {}
    weight_unit = cfg.get("weight_unit", "kg")
    dim_unit = cfg.get("dimension_unit", "cm")

    def _to_float(v) -> Optional[float]:
        if v is None or v == "":
            return None
        try:
            return float(v)
        except (TypeError, ValueError):
            return None

    def _conv_weight(raw_val) -> Optional[float]:
        kg = _to_float(raw_val)
        if kg is None:
            return None
        return kg * 2.20462 if weight_unit == "lbs" else kg

    def _conv_dim(raw_val) -> Optional[float]:
        cm = _to_float(raw_val)
        if cm is None:
            return None
        return cm / 2.54 if dim_unit == "in" else cm

    def _resolve(raw_val, conv_fn, null_mode_key: str, default_key: str) -> Optional[str]:
        converted = conv_fn(raw_val)
        if converted is not None:
            return f"{converted:.2f}"
        null_mode = cfg.get(null_mode_key, "leave_blank")
        if null_mode == "use_default":
            return cfg.get(default_key) or ""
        if null_mode == "skip":
            return None  # omit the field entirely
        return ""  # leave_blank -- explicit empty value

    raw_weight = raw.get("packWeight") or raw.get("unitWeight")
    raw_length = raw.get("packLength") or raw.get("unitLength")
    raw_width  = raw.get("packWidth")  or raw.get("unitWidth")
    raw_height = raw.get("packHeight") or raw.get("unitHeight")

    result: dict = {}

    weight = _resolve(raw_weight, _conv_weight, "weight_null", "weight_default")
    if weight is not None:
        result["weight"] = weight

    length = _resolve(raw_length, _conv_dim, "length_null", "length_default")
    width  = _resolve(raw_width,  _conv_dim, "width_null",  "width_default")
    height = _resolve(raw_height, _conv_dim, "height_null", "height_default")
    dims = {k: v for k, v in (("length", length), ("width", width), ("height", height)) if v is not None}
    if dims:
        result["dimensions"] = dims

    return result


async def _log(db, job_id: int, level, message: str):
    from models.models import JobLog
    db.add(JobLog(job_id=job_id, level=level, message=message))
    await db.commit()


# ---------------------------------------------------------------------------
# FETCH — pull ALL products from Sunsky (full pagination), stamp with job id
# ---------------------------------------------------------------------------

async def _run_fetch(db, job):
    from models.models import Product, ProductStatus, LogLevel
    from pipeline import sunsky_client
    from sqlalchemy import select

    cfg = job.config or {}
    category_id = cfg.get("category_id")
    keyword      = cfg.get("keyword")
    page_size    = int(cfg.get("page_size", cfg.get("limit", 50)))
    max_pages    = cfg.get("max_pages")  # None = fetch ALL pages

    await _log(
        db, job.id, LogLevel.info,
        f"Fetch started — page_size={page_size}"
        + (f", keyword='{keyword}'" if keyword else "")
        + (f", category_id={category_id}" if category_id else "")
        + (f", max_pages={max_pages}" if max_pages else " (all pages)"),
    )

    # ── counters
    created = updated = skipped = failed = 0
    page_count = 0

    async def _on_page(page: int, batch: list, total: int):
        nonlocal page_count
        page_count += 1
        await _log(db, job.id, LogLevel.info,
                   f"  Page {page}: received {len(batch)} products (total reported by API: {total})")

    try:
        all_products = await sunsky_client.get_all_products(
            category_id=category_id,
            keyword=keyword,
            page_size=page_size,
            max_pages=max_pages,
            on_page=_on_page,
        )
    except Exception as e:
        await _log(db, job.id, LogLevel.error, f"Sunsky API error: {e}")
        raise

    await _log(db, job.id, LogLevel.info,
               f"Sunsky returned {len(all_products)} product(s) across {page_count} page(s)")

    job.total_items = len(all_products)
    await db.commit()

    for i, p in enumerate(all_products):
        sunsky_id = str(p["id"])
        try:
            existing: Product | None = (
                await db.execute(select(Product).where(Product.sunsky_id == sunsky_id))
            ).scalar_one_or_none()

            if not existing:
                # Same fix as routers/sunsky.py's fetch_products — a CSV
                # import may have already created a row for this exact real
                # item keyed by SKU (CSV import sets sunsky_id = the SKU
                # string, differing from Sunsky's own internal "id" field
                # used here), so check by SKU before creating a duplicate.
                existing = (
                    await db.execute(select(Product).where(Product.sku == p["sku"]))
                ).scalar_one_or_none()
                if existing:
                    existing.sunsky_id = sunsky_id

            images   = p.get("images", [])
            raw_data = p.get("raw_data", {})

            if existing:
                # ── Compare key fields; update if anything changed
                changed_fields = []
                if existing.name != p["name"] and p["name"]:
                    existing.name = p["name"]
                    changed_fields.append("name")
                if existing.price != p.get("price") and p.get("price"):
                    existing.price = p["price"]
                    changed_fields.append("price")
                if existing.stock_status != p.get("stock_status") and p.get("stock_status"):
                    existing.stock_status = p["stock_status"]
                    changed_fields.append("stock_status")
                if p.get("stock_quantity") is not None and existing.stock_quantity != p["stock_quantity"]:
                    existing.stock_quantity = p["stock_quantity"]
                    changed_fields.append("stock_quantity")
                if p.get("description") and existing.description != p["description"]:
                    existing.description = p["description"]
                    changed_fields.append("description")
                if images and existing.image_count != len(images):
                    existing.image_count = len(images)
                    existing.raw_data = raw_data
                    changed_fields.append("images")
                elif raw_data and not images:
                    pass  # no image update needed

                if changed_fields:
                    # If the product was already uploaded, reset it so upload re-runs
                    if existing.status == ProductStatus.uploaded:
                        existing.status = ProductStatus.pending
                        existing.woo_product_id = None
                    existing.raw_data = raw_data
                    existing.fetch_job_id = job.id
                    await _log(db, job.id, LogLevel.info,
                               f"  Updated {p['sku']}: {', '.join(changed_fields)} changed")
                    updated += 1
                else:
                    # No field changes, but this product IS part of the
                    # current pipeline's batch — re-stamp fetch_job_id so
                    # downstream Process/Enrich/Upload steps (which all scope
                    # their product queries to THIS pipeline's fetch_job_id)
                    # actually find it. Without this, re-fetching an
                    # unchanged product left it permanently linked only to
                    # whichever earlier pipeline first created it, so every
                    # later pipeline saw 0 products to enrich/process even
                    # with Force Re-run on.
                    existing.fetch_job_id = job.id
                    await _log(db, job.id, LogLevel.debug,
                               f"  Skipped {p['sku']}: no changes detected")
                    skipped += 1
            else:
                db.add(Product(
                    sunsky_id=sunsky_id,
                    sku=p["sku"],
                    name=p["name"],
                    description=p.get("description", ""),
                    price=p.get("price", "0"),
                    stock_status=p.get("stock_status", "in_stock"),
                    stock_quantity=p.get("stock_quantity"),
                    category_id=p.get("category_id", ""),
                    image_count=len(images),
                    raw_data=raw_data,
                    status=ProductStatus.pending,
                    fetch_job_id=job.id,
                ))
                await _log(db, job.id, LogLevel.info, f"  Created {p['sku']}")
                created += 1

        except Exception as e:
            await _log(db, job.id, LogLevel.error, f"  Failed to save {p.get('sku', sunsky_id)}: {e}")
            failed += 1

        job.processed_items = i + 1
        job.progress_percent = round((i + 1) / len(all_products) * 100, 1)
        if (i + 1) % 10 == 0:
            await db.commit()

    await db.commit()

    # ── Job Summary
    await _log(db, job.id, LogLevel.info,
               f"\n{'='*50}\n"
               f"FETCH JOB SUMMARY\n"
               f"  Total fetched : {len(all_products)}\n"
               f"  Created       : {created}\n"
               f"  Updated       : {updated}\n"
               f"  Skipped       : {skipped} (no changes)\n"
               f"  Failed        : {failed}\n"
               f"{'='*50}")

    job.failed_items = failed
    await db.commit()


# ---------------------------------------------------------------------------
# PROCESS — download + compress images with retry; scoped to a fetch job
# ---------------------------------------------------------------------------

async def _run_process(db, job):
    from models.models import Product, ProductStatus, Image, ImageStatus, LogLevel
    from pipeline.image_processor import ImageProcessor
    from sqlalchemy import select, delete

    cfg = job.config or {}
    limit = cfg.get("limit", 200)
    force_rerun = cfg.get("force_rerun", False)

    # With force_rerun include already-processed products so they are re-processed
    # in place rather than creating new DB rows.
    from sqlalchemy import or_ as _or
    if force_rerun:
        eligible_status = _or(
            Product.status == ProductStatus.pending,
            Product.status == ProductStatus.processed,
        )
        await _log(db, job.id, LogLevel.info,
                   "force_rerun=True — including already-processed products")
    else:
        eligible_status = Product.status == ProductStatus.pending

    base_q = select(Product).where(eligible_status)

    if job.source_job_id:
        stamped_q = base_q.where(Product.fetch_job_id == job.source_job_id).limit(limit)
        products = (await db.execute(stamped_q)).scalars().all()

        if products:
            await _log(db, job.id, LogLevel.info,
                       f"Process scoped to fetch job #{job.source_job_id} — "
                       f"{len(products)} product(s) found")
        else:
            await _log(db, job.id, LogLevel.warn,
                       f"No products stamped with fetch_job_id={job.source_job_id} — "
                       f"falling back to un-linked eligible products")
            fallback_q = base_q.where(Product.fetch_job_id.is_(None)).limit(limit)
            products = (await db.execute(fallback_q)).scalars().all()
            if products:
                await _log(db, job.id, LogLevel.info,
                           f"Found {len(products)} un-linked eligible product(s) to process")
    else:
        await _log(db, job.id, LogLevel.info,
                   "No source job selected — processing ALL eligible products")
        products = (await db.execute(base_q.limit(limit))).scalars().all()

    # Reset already-processed products so image pipeline runs again
    if force_rerun:
        for p in products:
            if p.status == ProductStatus.processed:
                p.status = ProductStatus.pending
        await db.commit()

    if not products:
        await _log(db, job.id, LogLevel.info, "No pending products to process")
        return

    job.total_items = len(products)
    await db.commit()

    processor = ImageProcessor()
    total_images_ok = total_images_fail = 0
    prod_ok = prod_fail = 0

    for i, product in enumerate(products):
        product.status = ProductStatus.processing
        await db.commit()

        try:
            import io, zipfile
            from pipeline import sunsky_client

            raw = product.raw_data or {}
            # The SKU is the Sunsky itemNo — use it for all API calls
            item_no = product.sku or product.sunsky_id

            # ── Stage 1: image URLs already in raw_data (rare, cached from prior run) ──
            image_urls = raw.get("images", [])
            if isinstance(image_urls, str):
                image_urls = [image_urls]
            image_urls = [
                u for u in image_urls if isinstance(u, str) and u.startswith("http")
            ][:5]

            # ── Stage 2: fetch product detail (correct endpoint: product!detail.do) ──
            # Always call detail API to get paramsTable / optionList / modelLabel
            # for the later sync step, even if we already have image URLs.
            zip_bytes: Optional[bytes] = None
            if item_no:
                await _log(db, job.id, LogLevel.info,
                           f"  {product.sku}: fetching detail from Sunsky (product!detail.do)…")
                detail = await sunsky_client.get_product_detail(item_no)
                if detail:
                    detail_raw = detail.get("raw_data") or {}
                    # Pull spec fields out of the raw detail response
                    params_table = detail_raw.get("paramsTable", "")
                    option_list  = detail_raw.get("optionList", {})
                    model_label  = detail_raw.get("modelLabel", "")

                    if not image_urls:
                        image_urls = [
                            u for u in detail.get("images", [])
                            if isinstance(u, str) and u.startswith("http")
                        ][:5]

                    # Merge everything back into raw_data
                    updated_raw = {
                        **raw,
                        "images": image_urls,
                        "paramsTable": params_table,
                        "optionList": option_list,
                        "modelLabel": model_label,
                    }
                    product.raw_data = updated_raw
                    product.image_count = len(image_urls)
                    await db.commit()
                    await _log(db, job.id, LogLevel.info,
                               f"  {product.sku}: detail fetched — "
                               f"{len(image_urls)} image(s), "
                               f"paramsTable={'yes' if params_table else 'no'}, "
                               f"optionList={'yes' if option_list else 'no'}")

            # ── Stage 3: download ZIP via product!getImages.do ──
            if not image_urls and item_no:
                await _log(db, job.id, LogLevel.info,
                           f"  {product.sku}: downloading images ZIP from Sunsky (product!getImages.do)…")
                zip_bytes = await sunsky_client.download_product_images(item_no, size="middle")
                if zip_bytes:
                    await _log(db, job.id, LogLevel.info,
                               f"  {product.sku}: ZIP received ({len(zip_bytes):,} bytes)")
                else:
                    await _log(db, job.id, LogLevel.warn,
                               f"  {product.sku}: no images available from any Sunsky source")

            await _log(db, job.id, LogLevel.info,
                       f"Processing {product.sku}: "
                       f"{'ZIP' if zip_bytes else str(len(image_urls)) + ' URL(s)'}")

            # Client feedback confirmed live: duplicate images (e.g. an
            # 800x800 AND a 1200x1200 version of the same photo) sitting
            # together in the WooCommerce gallery. Root cause: nothing here
            # ever deleted a product's existing Image rows before Process
            # created a new set, so every repeated Process run (Force
            # Re-run, or simply re-running the pipeline) ACCUMULATED a
            # fresh batch on top of whatever was already there from a
            # previous run. _resolve_product_images' upload query selects
            # every Image row with status=compressed for the product,
            # regardless of which run created it -- so every accumulated
            # duplicate got uploaded to WordPress every single time Upload
            # ran. Only delete once we know there's real new data to
            # replace them with, so a failed/empty fetch this run can't
            # wipe out otherwise-good images from a previous successful run.
            if zip_bytes or image_urls:
                await db.execute(delete(Image).where(Image.product_id == product.id))
                await db.commit()

            processed_count = 0

            # ── Process from ZIP bytes ──
            if zip_bytes:
                try:
                    zf = zipfile.ZipFile(io.BytesIO(zip_bytes))
                    img_names = sorted([
                        n for n in zf.namelist()
                        if n.lower().endswith((".jpg", ".jpeg", ".png", ".webp", ".gif"))
                        and not n.startswith("__MACOSX")
                    ])[:5]

                    for pos, name in enumerate(img_names):
                        ext = name.rsplit(".", 1)[-1].lower()
                        img_data = zf.read(name)
                        processed_path = await processor.process_from_bytes(
                            img_data, product.sku, pos, ext
                        )
                        if processed_path:
                            db.add(Image(
                                product_id=product.id,
                                original_url=f"sunsky-zip://{item_no}/{name}",
                                processed_path=processed_path,
                                position=pos,
                                status=ImageStatus.compressed,
                                is_main=(pos == 0),
                            ))
                            processed_count += 1
                            total_images_ok += 1
                            await _log(db, job.id, LogLevel.debug,
                                       f"  [{product.sku}] ZIP img {pos} ({name}) OK → {processed_path}")
                        else:
                            db.add(Image(
                                product_id=product.id,
                                original_url=f"sunsky-zip://{item_no}/{name}",
                                position=pos,
                                status=ImageStatus.failed,
                                is_main=(pos == 0),
                                error_message="process_from_bytes returned None",
                            ))
                            total_images_fail += 1
                            await _log(db, job.id, LogLevel.warn,
                                       f"  [{product.sku}] ZIP img {pos} ({name}) FAILED")

                    product.image_count = processed_count
                    await db.commit()
                except zipfile.BadZipFile as zf_err:
                    await _log(db, job.id, LogLevel.warn,
                               f"  {product.sku}: invalid ZIP from Sunsky: {zf_err}")

            # ── Process from URLs (stage 1 or 2) ──
            for pos, url in enumerate(image_urls):
                processed_path = None
                img_error = None

                for attempt in range(1, 4):
                    try:
                        processed_path = await processor.process(url, product.sku, pos)
                        break
                    except Exception as img_err:
                        img_error = img_err
                        if attempt < 3:
                            await asyncio.sleep(2 * attempt)
                            await _log(db, job.id, LogLevel.warn,
                                       f"  [{product.sku}] img {pos} attempt {attempt} failed: {img_err} — retrying")

                if processed_path:
                    db.add(Image(
                        product_id=product.id,
                        original_url=url,
                        processed_path=processed_path,
                        position=pos,
                        status=ImageStatus.compressed,
                        is_main=(pos == 0),
                    ))
                    processed_count += 1
                    total_images_ok += 1
                    await _log(db, job.id, LogLevel.debug,
                               f"  [{product.sku}] img {pos} OK → {processed_path}")
                else:
                    db.add(Image(
                        product_id=product.id,
                        original_url=url,
                        position=pos,
                        status=ImageStatus.failed,
                        is_main=(pos == 0),
                        error_message=str(img_error) if img_error else "processor returned None",
                    ))
                    total_images_fail += 1
                    await _log(db, job.id, LogLevel.warn,
                               f"  [{product.sku}] img {pos} FAILED after 3 attempts: {img_error}")

                await db.commit()

            product.status = ProductStatus.processed
            prod_ok += 1
            await _log(db, job.id, LogLevel.info,
                       f"  {product.sku}: {processed_count}/{len(image_urls)} images OK")

        except Exception as e:
            product.status = ProductStatus.failed
            product.error_message = str(e)
            prod_fail += 1
            await _log(db, job.id, LogLevel.error,
                       f"  {product.sku}: FAILED — {e}")

        job.processed_items = i + 1
        job.progress_percent = round((i + 1) / len(products) * 100, 1)
        await db.commit()

    # ── Job Summary
    await _log(db, job.id, LogLevel.info,
               f"\n{'='*50}\n"
               f"PROCESS JOB SUMMARY\n"
               f"  Products processed  : {prod_ok}\n"
               f"  Products failed     : {prod_fail}\n"
               f"  Images OK           : {total_images_ok}\n"
               f"  Images failed       : {total_images_fail}\n"
               f"{'='*50}")

    job.failed_items = prod_fail
    await db.commit()


# ---------------------------------------------------------------------------
# IMAGE RESOLUTION HELPER
# ---------------------------------------------------------------------------

async def _resolve_product_images(db, job, product, raw: dict, wc, store) -> list[str]:
    """
    For a product, return a list of image URLs to send to WooCommerce.

    Priority order:
      1. Upload processed WebP files to WordPress media library
         (requires wp_username + wp_app_password set on the store).
      2. Build public static URL for the processed file so WooCommerce can
         sideload it from this server (requires SERVER_BASE_URL in .env).
      3. Raw Sunsky CDN URLs as last resort.
    """
    from models.models import Image, ImageStatus, LogLevel
    from sqlalchemy import select
    from config import get_settings

    settings = get_settings()

    imgs_q = (
        select(Image)
        .where(
            Image.product_id == product.id,
            Image.status == ImageStatus.compressed,
            Image.processed_path.isnot(None),
        )
        .order_by(Image.position)
    )
    processed_images = (await db.execute(imgs_q)).scalars().all()

    if processed_images:
        has_wp_creds = bool(store.wp_username and store.wp_app_password)
        has_base_url = bool(settings.server_base_url)

        if has_wp_creds:
            await _log(db, job.id, LogLevel.info,
                       f"  {product.sku}: uploading {len(processed_images)} image(s) to WP media…")
            urls: list[str] = []
            base_slug = product.slug
            if not base_slug:
                from services.content_service import _slugify as _cs_slugify
                base_slug = _cs_slugify(product.name or product.sku or "product")

            # Product-level, source-URL-keyed dedup cache. Confirmed
            # live that patch 78's Image-row-level dedup alone wasn't
            # enough: a full pipeline re-run (Fetch->Process->...->
            # Upload) for a product already uploaded before still
            # created genuine duplicate WP media attachments, because
            # Process deletes and recreates every Image row for that
            # product each time it runs (patch 42's correct, separate
            # behavior) -- wiping the Image-row-level memory along with
            # it. This is a MORE DURABLE layer at the product level,
            # keyed by each image's ORIGINAL SOURCE URL (stable across
            # Process re-runs, unlike the Image row's own id), so dedup
            # survives a full pipeline re-run, not just a plain
            # re-upload/re-sync on the same existing Image rows.
            import json as _img_cache_json
            try:
                uploaded_cache: dict = _img_cache_json.loads(product.uploaded_images_json or "{}")
            except Exception:
                uploaded_cache = {}
            print(f"[_resolve_product_images] {product.sku}: product-level cache has {len(uploaded_cache)} "
                  f"entries: {list(uploaded_cache.keys())}")

            for img in processed_images:
                cache_key = img.original_url or ""
                cached = uploaded_cache.get(cache_key) if cache_key else None
                print(f"[_resolve_product_images] {product.sku} pos={img.position}: "
                      f"original_url={cache_key!r} → {'CACHE HIT' if cached else 'cache miss'}")

                if cached and cached.get("wp_url"):
                    urls.append(cached["wp_url"])
                    img.wp_media_url = cached["wp_url"]
                    img.woo_image_id = cached.get("woo_image_id")
                    await _log(db, job.id, LogLevel.debug,
                               f"    pos={img.position} → reusing WP media from product cache (already uploaded in a previous run)")
                    continue

                # Review 3, item #4: "photos uploaded twice." Fast-path
                # for the same-Image-row case (re-upload/re-sync without
                # re-running Process) -- the product-level cache above
                # is the primary, durable check; this covers the case
                # where the row itself already has the info too.
                if img.wp_media_url and img.woo_image_id:
                    urls.append(img.wp_media_url)
                    if cache_key:
                        uploaded_cache[cache_key] = {"wp_url": img.wp_media_url, "woo_image_id": img.woo_image_id}
                    await _log(db, job.id, LogLevel.debug,
                               f"    pos={img.position} → reusing existing WP media #{img.woo_image_id} (already uploaded)")
                    continue
                # Review 3, item #4 second part: "wrong image file name
                # format." Confirmed root cause: no filename was ever
                # explicitly passed here, so the upload silently used the
                # LOCAL processed file's own name -- SKU-based
                # (e.g. "PU1087T_0.webp", from image_processor.py's
                # f"{safe_sku}_{position}.{ext}") -- completely bypassing
                # the SEO-friendly name shown in Content Review's own
                # "Image File Names" field. Built directly from
                # product.slug (the authoritative field) with correct
                # per-position numbering, independent of image_names'
                # stored single-string preview value (which only ever
                # represented what image #1 would look like).
                ext = Path(img.processed_path).suffix or ".webp"
                wp_filename = f"{base_slug}-{img.position + 1}{ext}"
                wp_url, wp_media_id = await wc.upload_image_to_wordpress(store, img.processed_path, filename=wp_filename)
                if wp_url:
                    urls.append(wp_url)
                    img.wp_media_url = wp_url
                    img.woo_image_id = wp_media_id
                    if cache_key:
                        uploaded_cache[cache_key] = {"wp_url": wp_url, "woo_image_id": wp_media_id}
                    await _log(db, job.id, LogLevel.debug,
                               f"    pos={img.position} → {wp_filename} → {wp_url}")
                else:
                    await _log(db, job.id, LogLevel.warn,
                               f"    pos={img.position} WP upload failed — {img.processed_path}")
            if urls:
                product.uploaded_images_json = _img_cache_json.dumps(uploaded_cache)
                await db.commit()
                print(f"[_resolve_product_images] {product.sku}: saved cache with {len(uploaded_cache)} "
                      f"entries to product.uploaded_images_json")
                return urls

        if has_base_url:
            await _log(db, job.id, LogLevel.info,
                       f"  {product.sku}: using static server URLs for {len(processed_images)} image(s)")
            base = settings.server_base_url.rstrip("/")
            return [f"{base}/media/images/{Path(img.processed_path).name}"
                    for img in processed_images]

        await _log(db, job.id, LogLevel.warn,
                   f"  {product.sku}: processed images found but no WP creds or SERVER_BASE_URL set "
                   f"— falling back to Sunsky CDN URLs")

    raw_imgs = raw.get("images", [])
    if isinstance(raw_imgs, str):
        raw_imgs = [raw_imgs]
    fallback = [u for u in raw_imgs if isinstance(u, str) and u.startswith("http")][:5]
    if fallback:
        await _log(db, job.id, LogLevel.warn,
                   f"  {product.sku}: no processed images — using {len(fallback)} raw Sunsky URL(s)")
        return fallback

    # Last resort: try Sunsky detail API to get fresh image URLs
    if product.sunsky_id:
        try:
            from pipeline import sunsky_client
            detail = await sunsky_client.get_product_detail(product.sunsky_id)
            if detail:
                api_imgs = [
                    u for u in detail.get("images", [])
                    if isinstance(u, str) and u.startswith("http")
                ][:5]
                if api_imgs:
                    # Cache into raw_data for future runs
                    updated_raw = {**(product.raw_data or {}), "images": api_imgs}
                    product.raw_data = updated_raw
                    product.image_count = len(api_imgs)
                    await db.commit()
                    await _log(db, job.id, LogLevel.info,
                               f"  {product.sku}: got {len(api_imgs)} image(s) from Sunsky detail API")
                    return api_imgs
        except Exception as detail_err:
            await _log(db, job.id, LogLevel.warn,
                       f"  {product.sku}: detail API unavailable: {detail_err}")

    await _log(db, job.id, LogLevel.warn, f"  {product.sku}: no images available from any source")
    return []


# ---------------------------------------------------------------------------
# UPLOAD — push to WooCommerce with SKU duplicate check + update logic
# ---------------------------------------------------------------------------

async def _run_upload(db, job):
    from models.models import Product, ProductStatus, Store, LogLevel
    from pipeline import woo_client as wc
    from sqlalchemy import select, or_, text
    from pathlib import Path

    if not job.store_id:
        raise ValueError("store_id required for upload jobs")

    store = await db.get(Store, job.store_id)
    if not store:
        raise ValueError("Store not found")

    # ── Inventory Mapping config (weight/dimension unit conversion + null
    # handling) — loaded once per store, same for every product this run.
    # None (no config saved yet) is handled gracefully by
    # _apply_inventory_mapping, which falls back to sensible defaults.
    from models.models import InventoryMappingConfig
    _inv_row = (
        await db.execute(
            select(InventoryMappingConfig).where(InventoryMappingConfig.store_id == job.store_id)
        )
    ).scalar_one_or_none()
    inventory_config = None
    if _inv_row:
        inventory_config = {
            "weight_unit":     _inv_row.weight_unit,
            "dimension_unit":  _inv_row.dimension_unit,
            "weight_null":     _inv_row.weight_null,
            "length_null":     _inv_row.length_null,
            "width_null":      _inv_row.width_null,
            "height_null":     _inv_row.height_null,
            "weight_default":  _inv_row.weight_default,
            "length_default":  _inv_row.length_default,
            "width_default":   _inv_row.width_default,
            "height_default":  _inv_row.height_default,
        }

    # Category ID -> name resolution, loaded once for this whole upload run.
    # Category mappings are saved keyed by NAME (via the Category Review
    # panel / Settings -> Category Mapping), but Sunsky's raw product data
    # only ever has a numeric categoryId -- without resolving through this
    # map first, the lookup below can never match what was saved, and every
    # product silently uploads with no category at all ("Uncategorized").
    # Same fix as routers/map_step.py and services/enrich_service.py earlier
    # today -- this is a third, independent copy of the same lookup logic.
    from services.enrich_service import get_effective_category_name_map
    upload_category_name_map = await get_effective_category_name_map(db)

    # ── Concurrent safety: advisory lock per store prevents two upload jobs
    # from the same store running simultaneously and double-uploading products.
    lock_result = await db.execute(
        text("SELECT pg_try_advisory_xact_lock(:lock_id)"),
        {"lock_id": 1000000 + job.store_id},
    )
    if not lock_result.scalar():
        raise RuntimeError(
            f"Another upload job for store #{job.store_id} is already running. "
            f"Wait for it to finish before starting a new one."
        )

    cfg = job.config or {}
    skip_images = cfg.get("skip_images", False)
    # See routers/pipeline.py content_confirm -- Content Review's "Exclude
    # from upload" checkbox previously had zero effect on the backend.
    # These IDs, when present, come from the operator explicitly excluding
    # specific products before confirming, and must never be uploaded even
    # though they'd otherwise match base_filter below.
    excluded_product_ids: set[int] = set(cfg.get("excluded_product_ids") or [])
    limit = cfg.get("limit", 200)

    # ── Resolve which products to upload, scoped by source job
    fetch_job_id = None
    if job.source_job_id:
        from models.models import Job as JobModel
        source_job = await db.get(JobModel, job.source_job_id)
        if source_job:
            from models.models import JobType
            if source_job.type == JobType.process and source_job.source_job_id:
                fetch_job_id = source_job.source_job_id
                await _log(db, job.id, LogLevel.info,
                           f"Upload scoped via process job #{source_job.id} → fetch job #{fetch_job_id}")
            else:
                fetch_job_id = source_job.id
                await _log(db, job.id, LogLevel.info,
                           f"Upload scoped to fetch job #{fetch_job_id}")
        else:
            await _log(db, job.id, LogLevel.warn,
                       f"Source job #{job.source_job_id} not found — uploading ALL eligible products")
    else:
        await _log(db, job.id, LogLevel.info,
                   "No source job selected — uploading ALL eligible products")

    force_rerun = cfg.get("force_rerun", False)

    # With force_rerun: include already-uploaded products so they are updated
    # in WooCommerce in-place rather than creating duplicate rows.
    # The existing_woo branch in the loop handles update vs. skip automatically.
    if force_rerun:
        base_filter = [
            or_(
                Product.status == ProductStatus.uploaded,
                Product.status == ProductStatus.processed,
                Product.status == ProductStatus.pending,
                Product.status == ProductStatus.failed,
                Product.status == ProductStatus.processing,
            ),
            # No woo_product_id filter — re-check everything
        ]
        await _log(db, job.id, LogLevel.info,
                   "force_rerun=True — including already-uploaded products for update check")
    else:
        # ── Include ALL non-uploaded statuses (pending/processed/failed/processing)
        # to prevent the "5 fetched → 3 uploaded" bug caused by processing status gaps.
        base_filter = [
            or_(
                Product.status == ProductStatus.processed,
                Product.status == ProductStatus.pending,
                Product.status == ProductStatus.failed,
                Product.status == ProductStatus.processing,
            ),
            Product.woo_product_id.is_(None),
        ]

    if excluded_product_ids:
        base_filter.append(Product.id.notin_(excluded_product_ids))
        await _log(db, job.id, LogLevel.info,
                   f"  {len(excluded_product_ids)} product(s) excluded by operator — will not be uploaded")

    if fetch_job_id:
        stamped_filter = base_filter + [Product.fetch_job_id == fetch_job_id]
        products = (
            await db.execute(select(Product).where(*stamped_filter).limit(limit))
        ).scalars().all()

        if not products:
            await _log(db, job.id, LogLevel.warn,
                       f"No products stamped with fetch_job_id={fetch_job_id} — "
                       f"falling back to un-linked eligible products")
            fallback_filter = base_filter + [Product.fetch_job_id.is_(None)]
            products = (
                await db.execute(select(Product).where(*fallback_filter).limit(limit))
            ).scalars().all()
            if products:
                await _log(db, job.id, LogLevel.info,
                           f"Found {len(products)} un-linked product(s) to upload")
    else:
        products = (
            await db.execute(select(Product).where(*base_filter).limit(limit))
        ).scalars().all()

    # Even when Phase 1 has nothing to upload, Phase 2 must still run to
    # assign categories + attributes to products already in WooCommerce.
    phase1_skip = not products
    if phase1_skip:
        await _log(db, job.id, LogLevel.info,
                   "No new products to upload — proceeding to Phase 2 "
                   "(categories + attributes) for already-uploaded products")
    else:
        job.total_items = len(products)
        await db.commit()

    created_count = updated_count = skipped_count = failed_count = 0

    for i, product in enumerate(products):
        action = "?"
        try:
            raw = product.raw_data or {}

            # ── Resolve category mapping for this product ─────────────────
            woo_cat_ids: list[int] = []
            primary_woo_cat_id: Optional[int] = None
            try:
                # Manual product-level override takes absolute priority over batch rules
                if getattr(product, "cat_source", None) == "manual" and product.manual_woo_cats_json:
                    import json as _json_manual
                    _manual = _json_manual.loads(product.manual_woo_cats_json)
                    woo_cat_ids = [c["id"] for c in _manual if c.get("id")]
                    primary_woo_cat_id = product.manual_primary_woo_cat_id
                else:
                    from models.models import SunskyCategoryMapping
                    raw_for_cat = product.raw_data or {}
                    sunsky_cat = (
                        str(raw_for_cat.get("catName") or
                            raw_for_cat.get("categoryName") or "").strip()
                    )
                    if not sunsky_cat:
                        # No name field on the raw data (the normal case for
                        # real Sunsky products) -- resolve the numeric ID
                        # through the same name map the Category Review
                        # panel and Settings -> Category Mapping use, so
                        # this lookup can actually match a saved mapping.
                        cat_id = str(raw_for_cat.get("categoryId") or raw_for_cat.get("catId") or "").strip()
                        sunsky_cat = upload_category_name_map.get(cat_id, cat_id)
                    if sunsky_cat and job.store_id:
                        from sqlalchemy import select as _sel
                        mapping = (await db.execute(
                            _sel(SunskyCategoryMapping).where(
                                SunskyCategoryMapping.store_id == job.store_id,
                                SunskyCategoryMapping.sunsky_cat == sunsky_cat,
                            )
                        )).scalar_one_or_none()
                        if mapping:
                            if mapping.woo_cats_json:
                                import json as _json_cat
                                _woo_cats = _json_cat.loads(mapping.woo_cats_json)
                                woo_cat_ids = [c["id"] for c in _woo_cats if c.get("id")]
                            elif mapping.woo_cat_id:
                                woo_cat_ids = [mapping.woo_cat_id]
                            primary_woo_cat_id = mapping.primary_woo_cat_id
                            if woo_cat_ids:
                                await _log(db, job.id, LogLevel.info,
                                           f"  {product.sku}: category mapping {sunsky_cat!r} → woo ids {woo_cat_ids}")
                        else:
                            await _log(db, job.id, LogLevel.warn,
                                       f"  {product.sku}: no category mapping for {sunsky_cat!r} — product will have no category")
            except Exception as _cat_err:
                await _log(db, job.id, LogLevel.warn,
                           f"  {product.sku}: category lookup error — {_cat_err}")

            # Client feedback: "If choose categories manual none of them
            # can't be really selected as primary." Confirmed root cause:
            # primary_woo_cat_id/manual_primary_woo_cat_id were saved to
            # the database by the Content Review category picker (patch
            # 59) but NEVER read anywhere in this payload construction --
            # "Set primary" updated a field nothing downstream consumed,
            # so it had zero real effect on the actual WooCommerce
            # upload regardless of what the operator picked. Reorders
            # the designated primary to the front of the list (the
            # convention most themes/plugins use for "the main
            # category"), and separately sets Yoast's own primary-
            # category meta field below (woo_client.py) -- confirmed via
            # the client's own WordPress screenshots that Yoast SEO is
            # the SEO plugin in use, and Yoast's "primary category"
            # feature is driven by its own meta field, not just array
            # order in the categories list.
            if primary_woo_cat_id and primary_woo_cat_id in woo_cat_ids:
                woo_cat_ids = [primary_woo_cat_id] + [cid for cid in woo_cat_ids if cid != primary_woo_cat_id]

            payload = {
                "name":              product.name,
                "sku":               product.site_sku or product.sku,
                "price":             product.price or "0",
                "description":       product.description or "",
                "short_description": product.short_description or "",
                "slug":              product.slug or "",
                "meta_title":        product.meta_title or "",
                "meta_description":  product.meta_description or "",
                "focus_keyword":     product.focus_keyword or "",
                "tags":              product.tags or "",
                "image_alt":         product.image_alt or "",
                # Real quantity when we have it (Sunsky's stockNum, or an
                # operator-supplied CSV "QTY" column). Previously this was
                # always hardcoded to 10/0 based only on the in/out-of-stock
                # boolean -- never a real number for any product.
                "stock_quantity":    (
                    product.stock_quantity if product.stock_quantity is not None
                    else (10 if product.stock_status == "in_stock" else 0)
                ),
            }
            # Only included when set -- an empty-string sale_price would
            # incorrectly mark the product "On Sale" at $0 in WooCommerce.
            # Client feedback item #8: "sales and regular prices" both
            # need to be editable (Baselinker reference); regular price
            # already flowed through correctly, sale_price never existed.
            if product.sale_price:
                payload["sale_price"] = product.sale_price
            if woo_cat_ids:
                payload["categories"] = [{"id": cid} for cid in woo_cat_ids]
            if primary_woo_cat_id:
                payload["primary_category_id"] = primary_woo_cat_id

            # Inventory Mapping: weight/dimensions from Sunsky raw data,
            # converted to the store's configured units, with null-handling
            # per the store's saved rules (leave blank / use default / skip).
            payload.update(_apply_inventory_mapping(raw, inventory_config))

            if not skip_images:
                image_urls = await _resolve_product_images(db, job, product, raw, wc, store)
                if image_urls:
                    # Client feedback: "duplicate/non-unique alt text across
                    # images" -- previously every photo in the gallery got
                    # the exact same alt string. The main (first) image
                    # keeps the clean base text, since that's the one most
                    # likely to matter for SEO/accessibility; each
                    # additional image gets a numbered suffix so it's still
                    # descriptive (not a bare "image 2" with no product
                    # context) while being distinct from the others.
                    base_alt = product.image_alt or product.name or ""
                    payload["images"] = [
                        {"src": url, "alt": (base_alt if i == 0 else f"{base_alt} - {i + 1}") if base_alt else ""}
                        for i, url in enumerate(image_urls)
                    ]

            # ── Check if SKU already exists in WooCommerce (prevents duplicates)
            existing_woo = await wc.get_product_by_sku(store, product.sku)

            if existing_woo:
                woo_id = existing_woo["id"]

                # Always push the full payload on re-upload — not just when
                # name/price/stock happen to differ. `payload` already
                # contains everything (description, short_description,
                # slug, meta_title, meta_description, tags, images,
                # categories, stock_quantity) built above, so a narrower
                # comparison here only risked silently skipping real
                # content changes (a new AI-generated description, updated
                # images, etc.) that don't happen to touch price/stock/name.
                await wc.update_product(store, woo_id, payload)
                product.woo_product_id = woo_id
                product.status = ProductStatus.uploaded
                product.error_message = None
                action = "updated"
                updated_count += 1
                await _log(db, job.id, LogLevel.info,
                           f"  {product.sku} → UPDATED woo_id={woo_id} (full payload re-sent)")
            else:
                # Create new product in WooCommerce
                await _log(db, job.id, LogLevel.info,
                           f"  {product.sku} → creating in WooCommerce…")
                try:
                    result = await wc.create_product(store, payload)
                    product.woo_product_id = result.get("id")
                    product.status = ProductStatus.uploaded
                    product.error_message = None
                    action = "created"
                    created_count += 1
                    await _log(db, job.id, LogLevel.info,
                               f"  {product.sku} → CREATED woo_id={product.woo_product_id}")
                except Exception as create_err:
                    err_text = str(create_err)
                    if "woocommerce_rest_product_not_created" in err_text and "already present in the lookup table" in err_text:
                        existing_woo = await wc.get_product_by_sku(store, product.sku)
                        if existing_woo:
                            woo_id = existing_woo["id"]
                            await wc.update_product(store, woo_id, payload)
                            product.woo_product_id = woo_id
                            product.status = ProductStatus.uploaded
                            product.error_message = None
                            action = "updated"
                            updated_count += 1
                            await _log(db, job.id, LogLevel.warn,
                                       f"  {product.sku} → existing SKU found, UPDATED woo_id={woo_id} instead of creating")
                        else:
                            raise
                    else:
                        raise

        except Exception as e:
            product.status = ProductStatus.failed
            product.error_message = str(e)
            action = "failed"
            failed_count += 1
            await _log(db, job.id, LogLevel.error,
                       f"  {product.sku} → FAILED: {e}")

        job.processed_items = i + 1
        job.progress_percent = round((i + 1) / len(products) * 100, 1)
        await db.commit()

    # ═══════════════════════════════════════════════════════════════════════
    # PHASE 2 — Assign categories + attributes from stored Sunsky data
    # No Sunsky API calls are made here.  All data comes from:
    #   • product.raw_data  (stored during fetch/process steps)
    #   • disk category cache (built by sync jobs, reused here)
    # ═══════════════════════════════════════════════════════════════════════
    # Query ALL products in this batch that have a WooCommerce ID — including
    # ones already uploaded in previous runs (which were excluded from the
    # Phase 1 query by woo_product_id.is_(None)).  This ensures categories
    # and attributes are always applied, even on re-runs.
    if fetch_job_id:
        uploaded_products = (
            await db.execute(
                select(Product).where(
                    Product.fetch_job_id == fetch_job_id,
                    Product.woo_product_id.is_not(None),
                )
            )
        ).scalars().all()
    else:
        # No fetch_job_id: fall back to in-memory products that got woo_product_id set
        uploaded_products = [p for p in products if p.woo_product_id]

    if not uploaded_products:
        await _log(db, job.id, LogLevel.info,
                   "  Phase 2: no products with WooCommerce IDs — skipping category/attribute assignment")
    else:
        await _log(db, job.id, LogLevel.info,
                   f"── Phase 2: Assigning categories + attributes to "
                   f"{len(uploaded_products)} product(s) ──")

        # ── Pre-load WooCommerce categories ──────────────────────────────
        try:
            woo_cats = await woo_client.get_all_woo_categories(store)
        except Exception as _e:
            woo_cats = []
            await _log(db, job.id, LogLevel.warn,
                       f"  Could not load WooCommerce categories: {_e}")
        p2_cat_by_key:  dict[tuple, int] = {
            (c["name"].lower(), int(c.get("parent") or 0)): c["id"]
            for c in woo_cats
        }
        p2_cat_by_name: dict[str, int] = {
            c["name"].lower(): c["id"] for c in woo_cats
        }

        # ── Load Sunsky category disk cache ───────────────────────────────
        p2_cat_cache = _load_cat_cache()   # sunsky_id → {name, sunsky_parent_id, …}
        p2_woo_id_cache: dict[str, int] = {}   # sunsky_id → woo_cat_id (this run)

        # ── Names this store has deliberately configured for AI extraction ──
        # or attribute mapping. Sunsky's raw modelLabel/optionList (below)
        # is a generic variant-selector dump straight from their API -- for
        # many products modelLabel genuinely says "Color" with real color
        # values, but for others (confirmed live: a phone-case family whose
        # actual variant axis is "which device it fits") Sunsky itself
        # mislabels the axis "Color" while optionList holds device-
        # compatibility strings like "For Samsung Galaxy S23 Ultra 5G", not
        # colors at all. Trusting modelLabel blindly means that raw dump
        # collides with -- and silently overwrites -- a deliberately
        # configured "Color" extraction rule's correctly-extracted single
        # value with a pile of unrelated device labels. A protected name
        # from Extraction Rules or Attribute Mapping always wins; the raw
        # variant dump is skipped entirely for any attribute name a rule
        # already owns, and only still runs for genuinely un-configured
        # variant axes.
        p2_protected_attr_names: set[str] = set()
        try:
            from models.models import AIExtractionRule as _AIER, AttributeMappingRule as _AMR
            _rule_rows = (await db.execute(select(_AIER.woo_attr_name))).all()
            p2_protected_attr_names |= {r[0].strip().lower() for r in _rule_rows if r[0]}
            _map_rows = (await db.execute(
                select(_AMR.woo_attr_name).where(
                    (_AMR.store_id == job.store_id) | (_AMR.store_id.is_(None))
                )
            )).all()
            p2_protected_attr_names |= {r[0].strip().lower() for r in _map_rows if r[0]}
        except Exception as _pn_e:
            await _log(db, job.id, LogLevel.warn,
                       f"  Could not load protected attribute names: {_pn_e}")

        # ── Pre-load WooCommerce global attributes ────────────────────────
        try:
            woo_global_attrs = await woo_client.get_all_woo_attributes(store)
        except Exception as _e:
            woo_global_attrs = []
            await _log(db, job.id, LogLevel.warn,
                       f"  Could not load WooCommerce attributes: {_e}")
        p2_attr_lookup: dict[str, dict] = {
            a["name"].lower(): a for a in woo_global_attrs
        }
        p2_term_cache: dict[int, dict[str, int]] = {}

        # ── Pre-load normalisation dict for this store (enrich step output) ─
        p2_norm_lookup: dict[tuple[str, str], str] = {}
        p2_attr_name_override: dict[str, str] = {}  # attribute.lower() → woo_attr_name
        if job.pipeline_job_id:
            try:
                from models.models import NormalisationDict as _NormDict
                from sqlalchemy import select as _sel_nd
                _norm_rows = (await db.execute(
                    _sel_nd(_NormDict).where(_NormDict.store_id == job.store_id)
                )).scalars().all()
                p2_norm_lookup = {
                    (r.attribute.lower(), r.raw_value.lower()): r.woo_term
                    for r in _norm_rows
                }
                # Build attr-name override lookup (first non-null per attribute wins)
                for _nr in _norm_rows:
                    if _nr.woo_attr_name and _nr.attribute.lower() not in p2_attr_name_override:
                        p2_attr_name_override[_nr.attribute.lower()] = _nr.woo_attr_name
            except Exception as _ne:
                await _log(db, job.id, LogLevel.warn,
                           f"  Could not load normalisation dict: {_ne}")

        await _log(db, job.id, LogLevel.info,
                   f"  WooCommerce: {len(woo_cats)} categories, "
                   f"{len(woo_global_attrs)} global attributes loaded | "
                   f"Sunsky cache: {len(p2_cat_cache)} entries | "
                   f"Norm dict: {len(p2_norm_lookup)} entries")

        # ── Helper: ensure one category node exists in WooCommerce ──────
        async def _p2_ensure_cat(sunsky_id: str, _g: int = 0) -> Optional[int]:
            """
            Get-or-create the WooCommerce category for a single Sunsky ID.
            Creates parent categories first (recursive, max depth 8).
            Returns the WooCommerce category ID, or None on failure.
            """
            if _g > 8 or not sunsky_id or sunsky_id == "0":
                return None
            if sunsky_id in p2_woo_id_cache:
                return p2_woo_id_cache[sunsky_id]
            meta = p2_cat_cache.get(sunsky_id)
            if not meta:
                return None
            name = (meta.get("name") or "").strip()
            if not name:
                return None
            parent_sid = (meta.get("sunsky_parent_id") or "0").strip()
            woo_parent = 0
            if parent_sid and parent_sid != "0":
                woo_parent = await _p2_ensure_cat(parent_sid, _g + 1) or 0
            woo_id = (
                p2_cat_by_key.get((name.lower(), woo_parent))
                or (p2_cat_by_name.get(name.lower()) if woo_parent == 0 else None)
            )
            if not woo_id:
                try:
                    resp = await woo_client.create_woo_category(store, name, woo_parent)
                    woo_id = resp["id"]
                    p2_cat_by_key[(name.lower(), woo_parent)] = woo_id
                    p2_cat_by_name[name.lower()] = woo_id
                    await _log(db, job.id, LogLevel.info,
                               f"  {'  ' * _g}↳ Created WooCommerce category: "
                               f"{name!r} (parent woo_id={woo_parent}) → #{woo_id}")
                except Exception as _ce:
                    await _log(db, job.id, LogLevel.warn,
                               f"  Cannot create WooCommerce category {name!r}: {_ce}")
                    return None
            alias = meta.get("alias_id")
            if alias:
                p2_woo_id_cache[alias] = woo_id
            p2_woo_id_cache[sunsky_id] = woo_id
            return woo_id

        # ── Helper: collect full category hierarchy (root → leaf) ────────
        async def _p2_collect_hierarchy(
            sunsky_id: str,
        ) -> tuple[list[int], list[str]]:
            """
            Walk the cached Sunsky parent chain from root to leaf.
            Returns:
              woo_ids  — WooCommerce category IDs for every level
              names    — human-readable names, same order (for logging)
            Only disk-cache data is used — no Sunsky API calls.
            Example result: ([12, 47, 203], ["Electronics", "Mobile Accessories", "Chargers"])
            """
            # Build the chain leaf → root, then reverse to root → leaf
            chain: list[str] = []
            cur = sunsky_id
            visited: set[str] = set()
            while cur and cur != "0" and cur not in visited:
                visited.add(cur)
                chain.append(cur)
                meta = p2_cat_cache.get(cur)
                if not meta:
                    break
                cur = (meta.get("sunsky_parent_id") or "0").strip()
            chain.reverse()  # root → leaf

            woo_ids: list[int] = []
            names: list[str] = []
            for sid in chain:
                wid = await _p2_ensure_cat(sid)
                if wid:
                    woo_ids.append(wid)
                    names.append((p2_cat_cache.get(sid) or {}).get("name", sid))
            return woo_ids, names

        # ── Helper: get-or-create a global WooCommerce attribute ─────────
        async def _p2_get_or_create_attr(name: str) -> Optional[dict]:
            key = name.lower()
            if key in p2_attr_lookup:
                return p2_attr_lookup[key]
            try:
                created = await woo_client.create_woo_attribute(store, name)
                p2_attr_lookup[key] = created
                return created
            except Exception as _ae:
                await _log(db, job.id, LogLevel.warn,
                           f"  Cannot create attribute {name!r}: {_ae}")
                return None

        # ── Helper: get-or-create an attribute term ───────────────────────
        async def _p2_get_or_create_term(attr_id: int, term_name: str) -> Optional[int]:
            if attr_id not in p2_term_cache:
                try:
                    existing = await woo_client.get_attribute_terms(store, attr_id)
                    p2_term_cache[attr_id] = {t["name"].lower(): t["id"] for t in existing}
                except Exception:
                    p2_term_cache[attr_id] = {}
            key = term_name.lower()
            if key in p2_term_cache[attr_id]:
                return p2_term_cache[attr_id][key]
            try:
                created = await woo_client.create_attribute_term(store, attr_id, term_name)
                p2_term_cache[attr_id][key] = created["id"]
                return created["id"]
            except Exception:
                return None

        # ── Per-product assignment ─────────────────────────────────────────
        p2_cat_ok = p2_cat_miss = p2_attr_ok = p2_attr_miss = 0

        for prod in uploaded_products:
            raw = prod.raw_data or {}

            # ── Category ─────────────────────────────────────────────────
            sunsky_cat_id = (
                str(raw.get("categoryId") or "").strip()
                or str(raw.get("category_id") or "").strip()
                or str(prod.category_id or "").strip()
            )

            # Fast path: try known name fields that Sunsky search API may include
            cat_name_direct = None
            for _f in ("catName", "categoryName", "category_name", "cat_name"):
                _v = raw.get(_f)
                if _v and isinstance(_v, str) and _v.strip():
                    cat_name_direct = _v.strip()
                    break

            cat_woo_ids: list[int] = []
            cat_names:   list[str] = []
            p2_primary_woo_cat_id: Optional[int] = None

            # ── Priority 0 (highest): manual per-product override ──────────
            # Same gap as Sync's own category-assignment block: this
            # "Phase 2" hierarchy step had no awareness of manual overrides
            # at all, and its own comment below explicitly warns it can
            # overwrite whatever category Phase 1 (the initial create/
            # update payload) already set -- meaning a manual override
            # could get silently replaced even within a single Upload
            # run, not just on a later Sync.
            if getattr(prod, "cat_source", None) == "manual" and prod.manual_woo_cats_json:
                try:
                    import json as _p2_manual_json
                    _p2_manual = _p2_manual_json.loads(prod.manual_woo_cats_json)
                    cat_woo_ids = [c["id"] for c in _p2_manual if c.get("id")]
                    cat_names = [c.get("name", "") for c in _p2_manual if c.get("id")]
                    p2_primary_woo_cat_id = prod.manual_primary_woo_cat_id
                except Exception as _p2_manual_e:
                    await _log(db, job.id, LogLevel.warn,
                               f"  {prod.sku}: manual override lookup failed — {_p2_manual_e}")

            # ── Priority 1: SunskyCategoryMapping (user's explicit manual mapping) ──
            # This always wins — checked before any automatic Sunsky tree lookup.
            if job.store_id and not cat_woo_ids:
                try:
                    from models.models import SunskyCategoryMapping as _SCM2
                    from sqlalchemy import select as _sel_scm
                    # Build a deduplicated list of candidate lookup keys to try
                    _scm_candidates: list[str] = []
                    for _fld in ("catName", "categoryName", "categoryId", "catId"):
                        _v = str(raw.get(_fld) or "").strip()
                        if _v and _v not in _scm_candidates:
                            _scm_candidates.append(_v)
                    if sunsky_cat_id and sunsky_cat_id not in _scm_candidates:
                        _scm_candidates.append(sunsky_cat_id)
                    if cat_name_direct and cat_name_direct not in _scm_candidates:
                        _scm_candidates.append(cat_name_direct)
                    # Also try the human-readable name from disk cache for this Sunsky ID
                    if sunsky_cat_id:
                        _dc_name = (p2_cat_cache.get(sunsky_cat_id) or {}).get("name", "")
                        if _dc_name and _dc_name not in _scm_candidates:
                            _scm_candidates.append(_dc_name)
                        # Primary resolver: starred-category names + Sunsky
                        # tree walk (upload_category_name_map, loaded once
                        # for this whole run) -- the disk cache above is a
                        # separate, older mechanism only populated by a
                        # distinct Sync job most workflows never run, so it's
                        # typically empty. This is the one that's actually
                        # reliably populated (see get_effective_category_name_map).
                        _resolved_name = upload_category_name_map.get(sunsky_cat_id)
                        if _resolved_name and _resolved_name not in _scm_candidates:
                            _scm_candidates.append(_resolved_name)

                    _mapping2 = None
                    _matched_key = None
                    for _cand in _scm_candidates:
                        # Case-insensitive match — handles "mobile accessories" vs "Mobile Accessories"
                        _mapping2 = (await db.execute(
                            _sel_scm(_SCM2).where(
                                _SCM2.store_id == job.store_id,
                                _SCM2.sunsky_cat.ilike(_cand),
                            )
                        )).scalar_one_or_none()
                        if _mapping2:
                            _matched_key = _cand
                            break

                    if _mapping2:
                        if _mapping2.woo_cats_json:
                            import json as _json_m2
                            _m2_cats = _json_m2.loads(_mapping2.woo_cats_json)
                            cat_woo_ids = [c["id"] for c in _m2_cats if c.get("id")]
                            cat_names   = [c.get("name", "") for c in _m2_cats if c.get("id")]
                        elif _mapping2.woo_cat_id:
                            cat_woo_ids = [_mapping2.woo_cat_id]
                            cat_names   = [_mapping2.woo_cat_name or _matched_key or ""]
                        p2_primary_woo_cat_id = _mapping2.primary_woo_cat_id
                        if cat_woo_ids:
                            await _log(db, job.id, LogLevel.info,
                                       f"  {prod.sku}: SunskyCategoryMapping "
                                       f"{_matched_key!r} → {cat_woo_ids}")
                    else:
                        await _log(db, job.id, LogLevel.warn,
                                   f"  {prod.sku}: no SunskyCategoryMapping found for "
                                   f"candidates {_scm_candidates} — category not assigned")
                except Exception as _scm_e:
                    await _log(db, job.id, LogLevel.warn,
                               f"  {prod.sku}: SunskyCategoryMapping lookup failed — {_scm_e}")

            # ── Priority 2 (fallback): Sunsky disk cache → full category hierarchy ──
            if not cat_woo_ids and sunsky_cat_id:
                cat_woo_ids, cat_names = await _p2_collect_hierarchy(sunsky_cat_id)

            # ── Priority 3 (fallback): plain catName field in raw_data ──────────
            if not cat_woo_ids and cat_name_direct:
                woo_cat_id = p2_cat_by_name.get(cat_name_direct.lower())
                if not woo_cat_id:
                    try:
                        resp = await woo_client.create_woo_category(store, cat_name_direct, 0)
                        woo_cat_id = resp["id"]
                        p2_cat_by_name[cat_name_direct.lower()] = woo_cat_id
                    except Exception:
                        pass
                if woo_cat_id:
                    cat_woo_ids = [woo_cat_id]
                    cat_names   = [cat_name_direct]

            # Set the full category hierarchy on the product.
            # IMPORTANT: only call when we have IDs — passing [] would clear the
            # category that Phase 1 already set in the create/update payload.
            if cat_woo_ids:
                try:
                    await woo_client.set_product_categories(
                        store, prod.woo_product_id, cat_woo_ids, p2_primary_woo_cat_id
                    )
                    p2_cat_ok += 1
                    path_str = " → ".join(cat_names) or str(cat_woo_ids)
                    await _log(db, job.id, LogLevel.info,
                               f"  ✓ {prod.sku} → {len(cat_woo_ids)}-level category: "
                               f"{path_str} (woo ids: {cat_woo_ids})")
                except Exception as _ce:
                    await _log(db, job.id, LogLevel.warn,
                               f"  ✗ {prod.sku}: set_categories failed — {_ce}")
            else:
                p2_cat_miss += 1
                await _log(db, job.id, LogLevel.warn,
                           f"  ✗ {prod.sku}: no category resolved (disk cache empty for "
                           f"{sunsky_cat_id!r} and no manual mapping) — "
                           f"Phase 1 category preserved; run Sync to build cache")

            # ── Attributes (entirely from raw_data — no Sunsky API calls) ─
            woo_attrs: list[dict] = []
            seen_attr_ids: set[int] = set()   # deduplication guard

            # Variant attribute: modelLabel (name) + optionList (values)
            model_label = str(raw.get("modelLabel") or "").strip()
            option_list = raw.get("optionList") or {}
            if isinstance(option_list, str):
                try:
                    option_list = json.loads(option_list)
                except Exception:
                    option_list = {}
            option_items = (
                option_list.get("items", []) if isinstance(option_list, dict) else []
            )
            option_values = [
                str(item.get("keywords") or item.get("value") or "").strip()
                for item in option_items
                if isinstance(item, dict)
            ]
            option_values = [v for v in option_values if v]

            if model_label and option_values:
                if model_label.strip().lower() in p2_protected_attr_names:
                    await _log(db, job.id, LogLevel.info,
                               f"  {prod.sku}: skipping Sunsky's raw '{model_label}' variant "
                               f"dump ({len(option_values)} values) — a rule already owns this "
                               f"attribute name, protecting its correctly-extracted value")
                else:
                    attr = await _p2_get_or_create_attr(model_label)
                    if attr and attr["id"] not in seen_attr_ids:
                        seen_attr_ids.add(attr["id"])
                        for val in option_values:
                            await _p2_get_or_create_term(attr["id"], val)
                        woo_attrs.append({
                            "id": attr["id"],
                            "name": attr["name"],
                            "options": option_values,
                            "visible": True,
                            "variation": True,
                        })

            # Spec attributes: paramsTable key→value pairs
            params_html = str(raw.get("paramsTable") or "")
            if params_html:
                for spec_key, spec_val in _parse_params_table(params_html).items():
                    if not spec_key or not spec_val:
                        continue
                    if len(spec_key) > 60 or len(spec_val) > 200:
                        continue
                    if spec_key.strip().lower() in p2_protected_attr_names:
                        # Same collision risk as the modelLabel/optionList
                        # case above -- a raw spec-table key can coincide
                        # with a deliberately configured attribute name
                        # (e.g. paramsTable literally has a "Brand" row),
                        # and since this loop runs before the enrich-
                        # attribute application below, it would claim
                        # seen_attr_ids first and silently block the real
                        # extracted value from ever being applied.
                        continue
                    attr = await _p2_get_or_create_attr(spec_key)
                    if attr and attr["id"] not in seen_attr_ids:
                        seen_attr_ids.add(attr["id"])
                        await _p2_get_or_create_term(attr["id"], spec_val)
                        woo_attrs.append({
                            "id": attr["id"],
                            "name": attr["name"],
                            "options": [spec_val],
                            "visible": True,
                            "variation": False,
                        })

            # AI-extracted enrich attributes (Enrich step output)
            # Applied after Sunsky attrs so they don't overwrite existing entries;
            # normalisation dict values take priority over raw AI values.
            # NOTE: We query by product_id only (not pipeline_job_id) so that attrs
            # confirmed in a *previous* pipeline run are still applied — the common
            # case when re-running upload after an earlier Enrich+Confirm step.
            if job.pipeline_job_id or True:  # always attempt for every product
                try:
                    from models.models import ProductEnrichAttr as _PEA
                    from sqlalchemy import select as _sel_ea, desc as _desc_ea
                    # First try: attrs confirmed in *this* pipeline (most authoritative)
                    _enrich_attrs = (await db.execute(
                        _sel_ea(_PEA).where(
                            _PEA.pipeline_job_id == job.pipeline_job_id,
                            _PEA.product_id == prod.id,
                            _PEA.confirmed == True,  # noqa: E712
                        )
                    )).scalars().all()
                    # Fallback: any confirmed attrs for this product (across all pipelines)
                    if not _enrich_attrs:
                        _enrich_attrs = (await db.execute(
                            _sel_ea(_PEA).where(
                                _PEA.product_id == prod.id,
                                _PEA.confirmed == True,  # noqa: E712
                            ).order_by(_desc_ea(_PEA.id))
                        )).scalars().all()
                        if _enrich_attrs:
                            await _log(db, job.id, LogLevel.info,
                                       f"  {prod.sku}: using enrich attrs from a previous pipeline "
                                       f"({len(_enrich_attrs)} confirmed)")

                    if _enrich_attrs:
                        # Build a name-level dedup set from attrs already queued
                        _seen_names: set[str] = {a["name"].lower() for a in woo_attrs}
                        _enrich_added = 0
                        for _ea in _enrich_attrs:
                            _aname = (_ea.attribute or "").strip()
                            if not _aname:
                                continue

                            # WooCommerce attribute name resolution priority:
                            # 1. Per-product woo_attr_name saved during Enrich review
                            # 2. Store-level attr-name override from NormalisationDict
                            # 3. Original Sunsky attribute name
                            _woo_aname = (
                                (_ea.woo_attr_name or "").strip()
                                or p2_attr_name_override.get(_aname.lower(), "")
                                or _aname
                            )

                            if _woo_aname.lower() in _seen_names:
                                continue  # skip if Sunsky raw_data already covers this attr

                            # Value resolution priority:
                            # 1. NormalisationDict (store-level canonical term)
                            # 2. normalised_value set during Enrich review
                            # 3. raw_value from AI extraction
                            _raw = (_ea.raw_value or "").strip()
                            _woo_val = (
                                p2_norm_lookup.get((_aname.lower(), _raw.lower()))
                                or (_ea.normalised_value or "").strip()
                                or _raw
                            )
                            if not _woo_val:
                                continue

                            _attr = await _p2_get_or_create_attr(_woo_aname)
                            if _attr and _attr["id"] not in seen_attr_ids:
                                seen_attr_ids.add(_attr["id"])
                                _seen_names.add(_woo_aname.lower())
                                await _p2_get_or_create_term(_attr["id"], _woo_val)
                                woo_attrs.append({
                                    "id": _attr["id"],
                                    "name": _attr["name"],
                                    "options": [_woo_val],
                                    "visible": True,
                                    "variation": False,
                                })
                                _enrich_added += 1

                        if _enrich_added:
                            await _log(db, job.id, LogLevel.info,
                                       f"  + {prod.sku}: {_enrich_added} enrich attr(s) added "
                                       f"({', '.join(a['name'] for a in woo_attrs[-_enrich_added:])})")
                except Exception as _ea_err:
                    await _log(db, job.id, LogLevel.warn,
                               f"  ✗ {prod.sku}: enrich attrs error — {_ea_err}")

            if woo_attrs:
                try:
                    await woo_client.set_product_attributes(
                        store, prod.woo_product_id, woo_attrs
                    )
                    p2_attr_ok += 1
                    await _log(db, job.id, LogLevel.info,
                               f"  ✓ {prod.sku} → {len(woo_attrs)} attribute(s): "
                               f"{', '.join(a['name'] for a in woo_attrs)}")
                except Exception as _ae:
                    await _log(db, job.id, LogLevel.warn,
                               f"  ✗ {prod.sku}: set_attributes failed — {_ae}")
            else:
                p2_attr_miss += 1
                await _log(db, job.id, LogLevel.warn,
                           f"  ✗ {prod.sku}: no attributes to assign — "
                           f"check that Process step ran (paramsTable) and/or Enrich Review was confirmed")

        await _log(db, job.id, LogLevel.info,
                   f"  Phase 2 done — categories: {p2_cat_ok} ✓ / {p2_cat_miss} ✗  |  "
                   f"attributes: {p2_attr_ok} ✓ / {p2_attr_miss} ✗")

    # ── Job Summary
    await _log(db, job.id, LogLevel.info,
               f"\n{'='*50}\n"
               f"UPLOAD JOB SUMMARY\n"
               f"  Total processed : {len(products)}\n"
               f"  Created         : {created_count}\n"
               f"  Updated         : {updated_count}\n"
               f"  Skipped         : {skipped_count} (already up-to-date)\n"
               f"  Failed          : {failed_count}\n"
               f"{'='*50}")

    job.failed_items = failed_count
    await db.commit()


# ---------------------------------------------------------------------------
# SYNC — fetch from Sunsky + upload delta to WooCommerce in one job
# ---------------------------------------------------------------------------

def _parse_params_table(html: str) -> dict[str, str]:
    """Extract key-value spec pairs from Sunsky paramsTable HTML."""
    import re
    from html import unescape

    keys = [unescape(k.strip()) for k in re.findall(
        r'class=["\']params_key["\'][^>]*>\s*(.*?)\s*</td>', html, re.DOTALL | re.IGNORECASE
    )]
    vals = [unescape(re.sub(r'<[^>]+>', '', v).strip()) for v in re.findall(
        r'class=["\']params_val["\'][^>]*>\s*(.*?)\s*</td>', html, re.DOTALL | re.IGNORECASE
    )]
    return {k: v for k, v in zip(keys, vals) if k and v}


async def _run_sync(db, job):
    """
    Sync job: push Sunsky categories and/or product attributes into WooCommerce.

    Config keys:
      store_id          (int, required) — target WooCommerce store
      sync_categories   (bool, default True)  — create Sunsky categories in WooCommerce
      sync_attributes   (bool, default True)  — push product spec attributes to WooCommerce
      source_job_id     (int, optional)       — limit to products from a specific fetch job
      limit             (int, default 200)    — max products to update with attributes
    """
    import re
    from html import unescape
    from models.models import Product, ProductStatus, Store, LogLevel
    from pipeline import woo_client, sunsky_client
    from sqlalchemy import select

    cfg = job.config or {}
    store_id = cfg.get("store_id") or job.store_id
    do_categories = cfg.get("sync_categories", True)
    do_attributes = cfg.get("sync_attributes", True)
    limit = int(cfg.get("limit", 200))
    source_job_id = cfg.get("source_job_id") or job.source_job_id

    if not store_id:
        await _log(db, job.id, LogLevel.error, "Sync job requires a store_id in config")
        return

    store = await db.get(Store, store_id)
    if not store:
        await _log(db, job.id, LogLevel.error, f"Store #{store_id} not found")
        return

    # ── Resolve the fetch_job_id that products were stamped with ──────────
    #
    # The sync's source_job_id points at an UPLOAD job.
    # Products are stamped with fetch_job_id (not upload_job_id or process_job_id).
    # So we must follow the same two-hop chain the upload job used:
    #
    #   sync.source_job_id  →  upload job
    #   upload.source_job_id  →  may be a PROCESS job or a FETCH job
    #       if PROCESS: upload.source_job_id.source_job_id  →  the FETCH job
    #       if FETCH:   upload.source_job_id                →  the FETCH job
    #
    # This mirrors _run_upload's resolution logic exactly.
    from models.models import Job as JobModel, JobType as JobTypeEnum

    resolved_fetch_job_id: Optional[int] = None
    if source_job_id:
        upload_job = await db.get(JobModel, int(source_job_id))
        if upload_job and upload_job.source_job_id:
            mid_job = await db.get(JobModel, upload_job.source_job_id)
            if mid_job:
                if mid_job.type == JobTypeEnum.process and mid_job.source_job_id:
                    # upload → process → fetch  (two hops)
                    resolved_fetch_job_id = mid_job.source_job_id
                    await _log(db, job.id, LogLevel.info,
                               f"Scoped: upload #{source_job_id} → process #{mid_job.id} "
                               f"→ fetch #{resolved_fetch_job_id}")
                else:
                    # upload → fetch  (one hop)
                    resolved_fetch_job_id = mid_job.id
                    await _log(db, job.id, LogLevel.info,
                               f"Scoped: upload #{source_job_id} → fetch #{resolved_fetch_job_id}")
            else:
                await _log(db, job.id, LogLevel.warn,
                           f"Upload job #{source_job_id} source job not found "
                           f"— syncing ALL uploaded products")
        elif upload_job:
            await _log(db, job.id, LogLevel.info,
                       f"Upload job #{source_job_id} has no source — syncing ALL uploaded products")
        else:
            await _log(db, job.id, LogLevel.warn,
                       f"Upload job #{source_job_id} not found — syncing ALL uploaded products")
    else:
        await _log(db, job.id, LogLevel.info, "No source job — syncing ALL uploaded products")

    await _log(db, job.id, LogLevel.info,
               f"Starting sync → store: {store.name} | categories={do_categories} | attributes={do_attributes}")

    cats_synced = cats_created = 0
    attrs_synced = attrs_created = terms_created = 0
    products_updated = 0

    # ── Helper: build the product query scoped to the resolved job ──
    def _scoped_product_q(extra_filters=None):
        q = select(Product).where(
            Product.woo_product_id.isnot(None),
            Product.status == ProductStatus.uploaded,
        )
        if resolved_fetch_job_id:
            q = q.where(Product.fetch_job_id == resolved_fetch_job_id)
        if extra_filters:
            for f in extra_filters:
                q = q.where(f)
        return q.limit(limit)

    # ── Helper: extract Sunsky category_id from a product row ──
    def _get_sunsky_cat_id(prod) -> str:
        raw = prod.raw_data or {}
        return (
            str(raw.get("categoryId") or "").strip()
            or str(raw.get("category_id") or "").strip()
            or str(prod.category_id or "").strip()
        )

    # ─────────────────────────────────────────────────────────────────────────
    # STEP A: Sync only the categories actually used by the target products
    # ─────────────────────────────────────────────────────────────────────────
    # sunsky_cat_id → woo_cat_id mapping (used later for product category update)
    sunsky_to_woo_cat: dict[str, int] = {}

    if do_categories:
        await _log(db, job.id, LogLevel.info, "── Step A: Syncing categories ──")

        # 1. Collect the unique Sunsky category IDs from the target products
        target_products = (await db.execute(_scoped_product_q())).scalars().all()
        job.total_items = len(target_products)
        await db.commit()

        needed_cat_ids: set[str] = set()
        for prod in target_products:
            cid = _get_sunsky_cat_id(prod)
            if cid:
                needed_cat_ids.add(cid)

        if not needed_cat_ids:
            await _log(db, job.id, LogLevel.warn,
                       "  No Sunsky category IDs found on target products — skipping category sync")
        else:
            await _log(db, job.id, LogLevel.info,
                       f"  {len(target_products)} product(s) use {len(needed_cat_ids)} unique category ID(s): "
                       f"{', '.join(sorted(needed_cat_ids))}")

            # ── 2. Load existing WooCommerce categories ──────────────────────────
            existing_woo_cats = await woo_client.get_all_woo_categories(store)
            # (name_lower, parent_woo_id) → woo_cat_id  (exact match)
            woo_cat_by_key: dict[tuple, int] = {
                (c["name"].lower(), int(c.get("parent") or 0)): c["id"]
                for c in existing_woo_cats
            }
            # name_lower → woo_cat_id  (fallback when parent unknown)
            woo_cat_by_name: dict[str, int] = {
                c["name"].lower(): c["id"]
                for c in existing_woo_cats
            }
            await _log(db, job.id, LogLevel.info,
                       f"  {len(existing_woo_cats)} existing WooCommerce categories loaded")

            # ── 3. Find needed category IDs in the Sunsky tree ───────────────
            #
            # Strategy (fastest-first):
            #   a) Load the on-disk category cache  →  instant for known IDs
            #   b) BFS the Sunsky tree for any IDs NOT in cache, using rate-
            #      limit-aware batching (BATCH_SIZE=3, 1.5 s between batches,
            #      auto 62-s pause on UP_TO_API_CALL_LIMIT_IN_MINUTE)
            #   c) Merge newly discovered entries back into the cache
            #
            # bfs_meta[sunsky_id] = {id, alias_id, name, sunsky_parent_id}
            # ─────────────────────────────────────────────────────────────────

            BATCH_SIZE      = 3    # concurrent Sunsky requests per batch
            BATCH_DELAY     = 1.5  # seconds between batches (≈ 2 req/s → ~120/min, usually safe)
            MAX_DEPTH       = 4    # rarely deeper than 3; cap prevents exponential blowup
            MAX_BFS_FETCHES = 80   # hard cap on total API calls; if exceeded the ID isn't in tree
            RATE_LIMIT_PAUSE = 62  # seconds to wait after hitting the per-minute cap

            # ── a) Seed bfs_meta from disk cache ──────────────────────────────
            bfs_meta: dict[str, dict] = {}
            cat_cache = _load_cat_cache()
            cached_now = datetime.now(timezone.utc).isoformat()
            for cid, entry in cat_cache.items():
                bfs_meta[cid] = entry

            # ── a2) Also seed from starred categories ─────────────────────────
            # Confirmed live: a category can be fully starred (all ancestors
            # too) and still get skipped here entirely, because this BFS
            # never checked the starred table at all -- it only knew about
            # the on-disk cache. A deep leaf category can easily sit outside
            # whatever the BFS's fetch cap can reach, especially on a large
            # catalog, even though the exact same "starred = instant, no API
            # calls" shortcut already exists and works everywhere else in
            # the app (category name resolution during Enrich, the Sunsky
            # Categories picker, etc.) -- this sync job just never got the
            # same treatment. Reconstruct parent linkage from the starred
            # set itself (a starred child's parent_name matching another
            # starred row's name) so a fully-starred branch resolves with
            # correct WooCommerce nesting and zero API calls; a starred
            # category whose parent isn't also starred still resolves, just
            # as a top-level category (better than being skipped outright).
            try:
                from models.models import StarredSunskyCategory as _SSC
                starred_rows = (await db.execute(select(_SSC))).scalars().all()
                name_to_starred_id = {r.name: r.cat_id for r in starred_rows}
                for r in starred_rows:
                    if r.cat_id in bfs_meta:
                        continue  # disk cache already has it, don't override
                    parent_sid = name_to_starred_id.get(r.parent_name, "0") if r.parent_name else "0"
                    bfs_meta[r.cat_id] = {
                        "id": r.cat_id, "name": r.name,
                        "sunsky_parent_id": parent_sid, "_cached_at": cached_now,
                    }
                await _log(db, job.id, LogLevel.info,
                           f"  Starred categories: {len(starred_rows)} loaded (instant, no API calls)")
            except Exception as _star_e:
                await _log(db, job.id, LogLevel.warn, f"  Starred-category seed failed: {_star_e}")

            remaining = needed_cat_ids - set(bfs_meta.keys())
            cache_hits = len(needed_cat_ids) - len(remaining)
            await _log(db, job.id, LogLevel.info,
                       f"  Category cache: {len(cat_cache)} entries loaded "
                       f"({cache_hits}/{len(needed_cat_ids)} needed IDs already cached"
                       + (f", {len(remaining)} need BFS)" if remaining else ", all found ✓)"))

            # ── b) BFS for IDs not in cache ───────────────────────────────────
            if remaining:
                seen_fetch: set[str] = {"0"}

                FETCH_TIMEOUT = 20  # seconds per category API call

                async def _fetch_safe(pid: str) -> tuple[str, list]:
                    """Fetch with automatic retry on rate-limit (waits 62 s).
                    Each individual HTTP call is capped at FETCH_TIMEOUT seconds
                    so a hung connection can never stall the sync job forever."""
                    for attempt in range(3):
                        try:
                            kids = await asyncio.wait_for(
                                sunsky_client.get_categories(pid),
                                timeout=FETCH_TIMEOUT,
                            )
                            return (pid, kids)
                        except asyncio.TimeoutError:
                            await _log(db, job.id, LogLevel.warn,
                                       f"  Timeout fetching category {pid} "
                                       f"(>{FETCH_TIMEOUT}s) — skipping")
                            return (pid, [])
                        except ValueError as exc:
                            if "CALL_LIMIT" in str(exc):
                                await _log(db, job.id, LogLevel.warn,
                                           f"  Sunsky rate limit hit — waiting {RATE_LIMIT_PAUSE}s…")
                                await asyncio.sleep(RATE_LIMIT_PAUSE)
                                continue
                            return (pid, [])
                        except Exception:
                            return (pid, [])
                    return (pid, [])

                try:
                    root_cats = (await _fetch_safe("0"))[1]
                except Exception as e:
                    await _log(db, job.id, LogLevel.error, f"  Cannot fetch root categories: {e}")
                    root_cats = []

                current_level: list[tuple[str, list]] = [("0", root_cats)]
                newly_found: dict[str, dict] = {}  # entries discovered this run
                total_bfs_fetches = 0

                for depth in range(1, MAX_DEPTH + 1):
                    if not current_level or not remaining:
                        break

                    # record this level's categories
                    next_fetch_ids: list[str] = []
                    for parent_sid, cats in current_level:
                        for cat in cats:
                            all_ids = {cat["id"]}
                            if cat.get("alias_id"):
                                all_ids.add(cat["alias_id"])
                            entry = {**cat, "sunsky_parent_id": parent_sid,
                                     "_cached_at": cached_now}
                            for cid in all_ids:
                                if cid not in bfs_meta:
                                    bfs_meta[cid] = entry
                                    newly_found[cid] = entry
                            remaining -= all_ids

                            if cat["id"] not in seen_fetch:
                                seen_fetch.add(cat["id"])
                                next_fetch_ids.append(cat["id"])

                    if not remaining:
                        await _log(db, job.id, LogLevel.info,
                                   f"  All remaining IDs found at BFS depth {depth} ✓")
                        break

                    if not next_fetch_ids:
                        break

                    # Hard cap: if this level would push us over MAX_BFS_FETCHES, stop now.
                    # The target category isn't in a reachable part of the tree.
                    if total_bfs_fetches + len(next_fetch_ids) > MAX_BFS_FETCHES:
                        await _log(db, job.id, LogLevel.warn,
                                   f"  BFS cap reached ({MAX_BFS_FETCHES} fetches) at depth {depth} "
                                   f"— category IDs not found in Sunsky tree: "
                                   f"{', '.join(sorted(remaining))} (will skip)")
                        remaining.clear()
                        break

                    total_bfs_fetches += len(next_fetch_ids)
                    await _log(db, job.id, LogLevel.info,
                               f"  BFS depth {depth}: fetching {len(next_fetch_ids)} branches "
                               f"({len(remaining)} ID(s) still needed)…")

                    # batched fetch with delay and rate-limit retry
                    next_level: list[tuple[str, list]] = []
                    for i in range(0, len(next_fetch_ids), BATCH_SIZE):
                        batch = next_fetch_ids[i : i + BATCH_SIZE]
                        results = await asyncio.gather(*[_fetch_safe(pid) for pid in batch])
                        for pid, kids in results:
                            if kids:
                                next_level.append((pid, kids))
                        # early exit if we've already found all needed IDs in this batch
                        if not remaining:
                            break
                        if i + BATCH_SIZE < len(next_fetch_ids):
                            await asyncio.sleep(BATCH_DELAY)

                    current_level = next_level

                if remaining:
                    await _log(db, job.id, LogLevel.warn,
                               f"  Could not find in Sunsky tree: {', '.join(sorted(remaining))} "
                               f"— those products will have categories cleared")

                # ── c) Save newly discovered entries to disk cache ─────────────
                if newly_found:
                    _save_cat_cache(newly_found)
                    await _log(db, job.id, LogLevel.info,
                               f"  Cached {len(newly_found)} new category entries for future syncs")

            # ── 4. Ensure every needed category (+ its ancestors) exists in WooCommerce ──
            # We resolve the ancestor chain top-down so parents are always created first.
            woo_id_cache: dict[str, int] = {}   # sunsky_id → woo_cat_id (this run)

            async def _resolve_woo_cat(sunsky_id: str, _guard: int = 0) -> Optional[int]:
                """Ensure a Sunsky category and all its ancestors exist in WooCommerce."""
                if _guard > 8 or not sunsky_id or sunsky_id == "0":
                    return None
                if sunsky_id in woo_id_cache:
                    return woo_id_cache[sunsky_id]

                meta = bfs_meta.get(sunsky_id)
                if not meta:
                    return None
                name = (meta.get("name") or "").strip()
                if not name:
                    return None

                # resolve parent first (recursion)
                parent_sid = meta.get("sunsky_parent_id", "0")
                woo_parent = 0
                if parent_sid and parent_sid != "0":
                    woo_parent = await _resolve_woo_cat(parent_sid, _guard + 1) or 0

                # check WooCommerce: exact (name, parent) → fallback name-only
                woo_id = woo_cat_by_key.get((name.lower(), woo_parent))
                if not woo_id and woo_parent == 0:
                    woo_id = woo_cat_by_name.get(name.lower())

                if woo_id:
                    nonlocal cats_synced
                    cats_synced += 1
                    await _log(db, job.id, LogLevel.debug,
                               f"  {'  ' * _guard}↳ {name} — already in WooCommerce (#{woo_id})")
                else:
                    try:
                        resp = await woo_client.create_woo_category(store, name, woo_parent)
                        woo_id = resp["id"]
                        woo_cat_by_key[(name.lower(), woo_parent)] = woo_id
                        woo_cat_by_name[name.lower()] = woo_id
                        nonlocal cats_created
                        cats_created += 1
                        await _log(db, job.id, LogLevel.info,
                                   f"  {'  ' * _guard}↳ Created: {name} → WooCommerce #{woo_id}")
                    except Exception as e:
                        await _log(db, job.id, LogLevel.warn,
                                   f"  Cannot create WooCommerce category {name!r}: {e}")
                        return None

                woo_id_cache[sunsky_id] = woo_id
                alias = meta.get("alias_id")
                if alias:
                    woo_id_cache[alias] = woo_id
                return woo_id

            await _log(db, job.id, LogLevel.info,
                       f"  Resolving {len(needed_cat_ids) - len(remaining)} "
                       f"found category ID(s) in WooCommerce…")

            for cat_id in needed_cat_ids:
                if cat_id in bfs_meta:
                    woo_id = await _resolve_woo_cat(cat_id)
                    if woo_id:
                        sunsky_to_woo_cat[cat_id] = woo_id
                        await _log(db, job.id, LogLevel.info,
                                   f"  Mapped Sunsky {cat_id} → WooCommerce #{woo_id}")

                        # Keep the saved mapping table in sync with what Step A
                        # actually just resolved/created. Confirmed live: without
                        # this, a mapping saved once (e.g. via Category Review,
                        # possibly with a blank name from an earlier bug) stays
                        # permanently stale -- Priority 1 in the assignment step
                        # below always trusts this table over fresh resolution,
                        # so 10 products got silently assigned to a category ID
                        # that no longer matched anything real in WooCommerce,
                        # while a correct, newly-created nested category sat
                        # right there unused. Upsert on every successful
                        # resolution so this table is a live cache of the truth,
                        # not a one-time snapshot that can drift.
                        try:
                            from sqlalchemy.dialects.postgresql import insert as _pg_insert
                            from models.models import SunskyCategoryMapping as _SCM_SYNC
                            import json as _json_local  # local alias -- avoids
                            # UnboundLocalError: this same enclosing function has
                            # its own later "import json" elsewhere, which makes
                            # Python treat the bare "json" name as local to the
                            # WHOLE function regardless of execution order.
                            # Confirmed live: "cannot access local variable
                            # 'json' where it is not associated with a value"
                            # fired here every time, silently swallowed by this
                            # try/except -- meaning this fix never actually ran
                            # even once since it was first deployed.
                            _cat_name = (bfs_meta.get(cat_id, {}).get("name") or "").strip()
                            if _cat_name:
                                _stmt = _pg_insert(_SCM_SYNC).values(
                                    store_id=job.store_id,
                                    sunsky_cat=_cat_name,
                                    sunsky_cat_id=cat_id,
                                    woo_cat_id=woo_id,
                                    woo_cat_name=_cat_name,
                                    woo_cats_json=_json_local.dumps([{"id": woo_id, "name": _cat_name}]),
                                    primary_woo_cat_id=woo_id,
                                ).on_conflict_do_update(
                                    index_elements=["store_id", "sunsky_cat"],
                                    set_={
                                        "sunsky_cat_id": cat_id,
                                        "woo_cat_id": woo_id,
                                        "woo_cat_name": _cat_name,
                                        "woo_cats_json": _json_local.dumps([{"id": woo_id, "name": _cat_name}]),
                                        "primary_woo_cat_id": woo_id,
                                        "updated_at": datetime.now(timezone.utc),
                                    },
                                )
                                await db.execute(_stmt)
                                await db.commit()
                        except Exception as _sync_map_e:
                            await _log(db, job.id, LogLevel.warn,
                                       f"  Could not sync mapping table for {cat_id}: {_sync_map_e}")

            await _log(db, job.id, LogLevel.info,
                       f"  Categories: {cats_created} created, {cats_synced} already existed "
                       f"— {len(sunsky_to_woo_cat)} ready to assign")

            # ── 5. Assign WooCommerce categories ──────────────────────────────
            # Priority:
            #   1. SunskyCategoryMapping (user's explicit mapping — always wins)
            #   2. Sunsky BFS tree result (sunsky_to_woo_cat — automatic fallback)
            #   3. Skip — never clear if no mapping found (preserves any existing category)
            await _log(db, job.id, LogLevel.info, "  Assigning categories to products in WooCommerce…")
            cat_ok = cat_miss = 0
            for prod in target_products:
                if not prod.woo_product_id:
                    await _log(db, job.id, LogLevel.warn,
                               f"  ✗ {prod.sku}: not uploaded to WooCommerce yet — skipped")
                    continue

                raw_p = prod.raw_data or {}
                sunsky_cat_id = _get_sunsky_cat_id(prod)

                # Priority 0 (highest): manual per-product override.
                # Confirmed live this was the actual root cause behind
                # "in product categories its showing which one was
                # before the review change" -- Sync had its own,
                # completely separate category-resolution logic (this
                # loop) with NO awareness of manual_woo_cats_json at
                # all, only ever checking the store-wide
                # SunskyCategoryMapping table below. A manual override
                # set correctly during Upload would get silently
                # reverted back to the batch mapping the moment Sync ran
                # afterward, since Sync had no idea the override existed.
                # Matches the exact same priority Upload's own category
                # resolution already uses (job_tasks.py, ~line 928).
                woo_cat_ids: list[int] = []
                primary_woo_cat_id: Optional[int] = None
                woo_cat_source = ""
                if getattr(prod, "cat_source", None) == "manual" and prod.manual_woo_cats_json:
                    try:
                        import json as _sync_manual_json
                        _sync_manual = _sync_manual_json.loads(prod.manual_woo_cats_json)
                        woo_cat_ids = [c["id"] for c in _sync_manual if c.get("id")]
                        primary_woo_cat_id = prod.manual_primary_woo_cat_id
                        if woo_cat_ids:
                            woo_cat_source = "manual override"
                    except Exception as _sync_manual_e:
                        await _log(db, job.id, LogLevel.warn,
                                   f"  {prod.sku}: manual override lookup failed — {_sync_manual_e}")

                # Priority 1: SunskyCategoryMapping — user's explicit mapping always wins
                if store_id and not woo_cat_ids:
                    try:
                        from models.models import SunskyCategoryMapping as _SSCM
                        from sqlalchemy import select as _ssel_scm
                        _sync_candidates: list[str] = []
                        for _f in ("catName", "categoryName", "categoryId", "catId"):
                            _v = str(raw_p.get(_f) or "").strip()
                            if _v and _v not in _sync_candidates:
                                _sync_candidates.append(_v)
                        if sunsky_cat_id and sunsky_cat_id not in _sync_candidates:
                            _sync_candidates.append(sunsky_cat_id)
                        # Also try the human-readable name from the disk cache for this ID
                        if sunsky_cat_id:
                            _dcname = (bfs_meta.get(sunsky_cat_id) or {}).get("name", "")
                            if not _dcname:
                                _dcname = (p2_cat_cache.get(sunsky_cat_id) if 'p2_cat_cache' in dir() else None or {}).get("name", "")
                            if _dcname and _dcname not in _sync_candidates:
                                _sync_candidates.append(_dcname)

                        _smapping = None
                        _skey = None
                        for _cand in _sync_candidates:
                            # Case-insensitive match so "mobile accessories" == "Mobile Accessories"
                            _smapping = (await db.execute(
                                _ssel_scm(_SSCM).where(
                                    _SSCM.store_id == store_id,
                                    _SSCM.sunsky_cat.ilike(_cand),
                                )
                            )).scalar_one_or_none()
                            if _smapping:
                                _skey = _cand
                                break

                        if _smapping:
                            if _smapping.woo_cats_json:
                                import json as _sjson
                                _sm_cats = _sjson.loads(_smapping.woo_cats_json)
                                woo_cat_ids = [c["id"] for c in _sm_cats if c.get("id")]
                            elif _smapping.woo_cat_id:
                                woo_cat_ids = [_smapping.woo_cat_id]
                            primary_woo_cat_id = _smapping.primary_woo_cat_id
                            if woo_cat_ids:
                                woo_cat_source = f"SunskyCategoryMapping ({_skey!r})"
                    except Exception as _sme:
                        await _log(db, job.id, LogLevel.warn,
                                   f"  {prod.sku}: SunskyCategoryMapping lookup failed — {_sme}")

                # Priority 2 (fallback): Sunsky BFS tree result
                if not woo_cat_ids and sunsky_cat_id and sunsky_cat_id in sunsky_to_woo_cat:
                    woo_cat_ids = [sunsky_to_woo_cat[sunsky_cat_id]]
                    woo_cat_source = f"Sunsky BFS ({sunsky_cat_id})"

                if woo_cat_ids:
                    try:
                        await woo_client.set_product_categories(
                            store, prod.woo_product_id, woo_cat_ids, primary_woo_cat_id
                        )
                        products_updated += 1
                        cat_ok += 1
                        await _log(db, job.id, LogLevel.info,
                                   f"  ✓ {prod.sku} (woo #{prod.woo_product_id}) "
                                   f"→ category {woo_cat_ids} via {woo_cat_source}")
                    except Exception as e:
                        await _log(db, job.id, LogLevel.warn,
                                   f"  ✗ {prod.sku}: set_categories failed — {e}")
                else:
                    cat_miss += 1
                    candidates_str = ", ".join(
                        str(raw_p.get(f) or "") for f in ("catName","categoryName","categoryId","catId")
                        if raw_p.get(f)
                    ) or sunsky_cat_id or "none"
                    await _log(db, job.id, LogLevel.warn,
                               f"  ✗ {prod.sku}: no category mapping found "
                               f"(tried candidates: {candidates_str}) — existing category preserved")

            await _log(db, job.id, LogLevel.info,
                       f"  Category assignment: {cat_ok} assigned ✓  |  {cat_miss} skipped (no mapping)")

    # ─────────────────────────────────────────────────────────────────────────
    # STEP B: Sync product attributes → WooCommerce
    # ─────────────────────────────────────────────────────────────────────────
    if do_attributes:
        await _log(db, job.id, LogLevel.info, "── Step B: Syncing product attributes ──")

        # Same protected-name guard as the upload-time attribute step (see
        # that commit for the full story) -- Sunsky's raw modelLabel/
        # optionList variant dump can collide with a deliberately configured
        # attribute name (confirmed live again here: 'Color' attribute
        # showing device-compatibility strings like 'For Samsung Galaxy
        # S24/S25 5G' instead of a real color). This Step B is a completely
        # separate implementation from the one that fix touched -- it never
        # got the same protection, so the exact same bug resurfaced here.
        p2_protected_attr_names_b: set[str] = set()
        try:
            from models.models import AIExtractionRule as _AIER_B, AttributeMappingRule as _AMR_B
            _rule_rows_b = (await db.execute(select(_AIER_B.woo_attr_name))).all()
            p2_protected_attr_names_b |= {r[0].strip().lower() for r in _rule_rows_b if r[0]}
            _map_rows_b = (await db.execute(
                select(_AMR_B.woo_attr_name).where(
                    (_AMR_B.store_id == job.store_id) | (_AMR_B.store_id.is_(None))
                )
            )).all()
            p2_protected_attr_names_b |= {r[0].strip().lower() for r in _map_rows_b if r[0]}
        except Exception as _pn_e_b:
            await _log(db, job.id, LogLevel.warn,
                       f"  Could not load protected attribute names: {_pn_e_b}")

        # Pre-load all WooCommerce attributes: name_lower → {id, slug}
        existing_attrs = await woo_client.get_all_woo_attributes(store)
        attr_lookup: dict[str, dict] = {a["name"].lower(): a for a in existing_attrs}
        await _log(db, job.id, LogLevel.info,
                   f"  {len(existing_attrs)} existing WooCommerce attributes loaded")

        # Per-attribute term cache: attr_id → {term_name_lower: term_id}
        term_cache: dict[int, dict[str, int]] = {}

        async def get_or_create_attr(name: str) -> Optional[dict]:
            key = name.lower()
            if key in attr_lookup:
                return attr_lookup[key]
            try:
                created = await woo_client.create_woo_attribute(store, name)
                attr_lookup[key] = created
                nonlocal attrs_created
                attrs_created += 1
                return created
            except Exception as e:
                await _log(db, job.id, LogLevel.warn, f"  Could not create attribute {name!r}: {e}")
                return None

        async def get_or_create_term(attr_id: int, term_name: str) -> Optional[int]:
            nonlocal terms_created
            if attr_id not in term_cache:
                existing_terms = await woo_client.get_attribute_terms(store, attr_id)
                term_cache[attr_id] = {t["name"].lower(): t["id"] for t in existing_terms}

            key = term_name.lower()
            if key in term_cache[attr_id]:
                return term_cache[attr_id][key]
            try:
                created = await woo_client.create_attribute_term(store, attr_id, term_name)
                term_cache[attr_id][key] = created["id"]
                terms_created += 1
                return created["id"]
            except Exception as e:
                await _log(db, job.id, LogLevel.warn,
                           f"  Could not create term {term_name!r} for attr {attr_id}: {e}")
                return None

        # Query the same scoped product set used for categories
        attr_products = (await db.execute(_scoped_product_q())).scalars().all()

        # Only set total_items here if the category phase didn't already set it.
        # This avoids the double-count (4 products → total_items=8) that made
        # the dashboard show "4/8" instead of "4/4".
        if not do_categories or not job.total_items:
            job.total_items = len(attr_products)
        await db.commit()

        await _log(db, job.id, LogLevel.info,
                   f"  Processing attributes for {len(attr_products)} product(s)…")

        for prod in attr_products:
            raw = prod.raw_data or {}
            woo_attrs: list[dict] = []

            # ── If spec data is missing, fetch it now from the detail API ──
            if not raw.get("paramsTable") and not raw.get("optionList") and (prod.sku or prod.sunsky_id):
                item_no = prod.sku or prod.sunsky_id
                try:
                    detail = await sunsky_client.get_product_detail(item_no)
                    if detail:
                        detail_raw = detail.get("raw_data") or {}
                        raw = {
                            **raw,
                            "paramsTable": detail_raw.get("paramsTable", ""),
                            "optionList":  detail_raw.get("optionList", {}),
                            "modelLabel":  detail_raw.get("modelLabel", ""),
                        }
                        prod.raw_data = raw
                        await db.commit()
                        await _log(db, job.id, LogLevel.debug,
                                   f"  {prod.sku}: fetched detail spec data from Sunsky")
                except Exception as de:
                    await _log(db, job.id, LogLevel.warn,
                               f"  {prod.sku}: could not fetch detail for attributes: {de}")

            # ── Variant attribute: modelLabel + optionList ──
            model_label = str(raw.get("modelLabel") or "").strip()
            option_list = raw.get("optionList") or {}
            if isinstance(option_list, str):
                import json
                try:
                    option_list = json.loads(option_list)
                except Exception:
                    option_list = {}

            option_items = option_list.get("items", []) if isinstance(option_list, dict) else []
            option_values = [
                str(item.get("keywords") or item.get("value") or "").strip()
                for item in option_items
                if isinstance(item, dict)
            ]
            option_values = [v for v in option_values if v]

            if model_label and option_values:
                if model_label.strip().lower() in p2_protected_attr_names_b:
                    await _log(db, job.id, LogLevel.info,
                               f"  {prod.sku}: skipping Sunsky's raw '{model_label}' variant "
                               f"dump ({len(option_values)} values) — a rule already owns this "
                               f"attribute name, protecting its correctly-extracted value")
                else:
                    attr = await get_or_create_attr(model_label)
                    if attr:
                        for val in option_values:
                            await get_or_create_term(attr["id"], val)
                        woo_attrs.append({
                            "id": attr["id"],
                            "name": attr["name"],
                            "options": option_values[:10],
                            "visible": True,
                            "variation": True,
                        })
                        attrs_synced += 1

            # ── Spec attributes: paramsTable HTML key-value pairs ──
            params_html = str(raw.get("paramsTable") or "")
            if params_html:
                spec_pairs = _parse_params_table(params_html)
                for spec_key, spec_val in list(spec_pairs.items())[:15]:
                    if len(spec_key) > 60 or len(spec_val) > 100:
                        continue
                    if spec_key.strip().lower() in p2_protected_attr_names_b:
                        continue  # same collision risk as the modelLabel case above
                    attr = await get_or_create_attr(spec_key)
                    if attr:
                        await get_or_create_term(attr["id"], spec_val)
                        woo_attrs.append({
                            "id": attr["id"],
                            "name": attr["name"],
                            "options": [spec_val],
                            "visible": True,
                            "variation": False,
                        })
                        attrs_synced += 1

            # ── Enrich attrs (confirmed by user in Attribute Review) ──
            # Applied last so user-confirmed values take priority over Sunsky raw spec.
            # Query across ALL pipelines — enrich attrs may have been confirmed in a
            # previous pipeline run.
            seen_attr_ids_sync: set[int] = {a["id"] for a in woo_attrs}
            try:
                from models.models import ProductEnrichAttr as _SPEA
                from sqlalchemy import select as _ssel_ea, desc as _sdesc_ea
                _s_enrich = (await db.execute(
                    _ssel_ea(_SPEA).where(
                        _SPEA.product_id == prod.id,
                        _SPEA.confirmed == True,  # noqa: E712
                    ).order_by(_sdesc_ea(_SPEA.id))
                )).scalars().all()
                _s_enrich_added = 0
                _seen_names_s: set[str] = {a["name"].lower() for a in woo_attrs}
                for _sea in _s_enrich:
                    _s_aname = (_sea.attribute or "").strip()
                    if not _s_aname:
                        continue
                    _s_woo_name = (_sea.woo_attr_name or "").strip() or _s_aname
                    if _s_woo_name.lower() in _seen_names_s:
                        continue
                    _s_raw = (_sea.raw_value or "").strip()
                    _s_val = (_sea.normalised_value or "").strip() or _s_raw
                    if not _s_val:
                        continue
                    _s_attr = await get_or_create_attr(_s_woo_name)
                    if _s_attr and _s_attr["id"] not in seen_attr_ids_sync:
                        seen_attr_ids_sync.add(_s_attr["id"])
                        _seen_names_s.add(_s_woo_name.lower())
                        await get_or_create_term(_s_attr["id"], _s_val)
                        woo_attrs.append({
                            "id": _s_attr["id"],
                            "name": _s_attr["name"],
                            "options": [_s_val],
                            "visible": True,
                            "variation": False,
                        })
                        _s_enrich_added += 1
                if _s_enrich_added:
                    await _log(db, job.id, LogLevel.info,
                               f"  + {prod.sku}: {_s_enrich_added} enrich attr(s) added from review")
            except Exception as _sea_e:
                await _log(db, job.id, LogLevel.warn,
                           f"  {prod.sku}: enrich attrs error in sync — {_sea_e}")

            # Push to WooCommerce only if we have something to set.
            # Never send an empty list — that would clear user-confirmed attrs.
            if prod.woo_product_id:
                if woo_attrs:
                    try:
                        await woo_client.set_product_attributes(store, prod.woo_product_id, woo_attrs)
                        job.processed_items = (job.processed_items or 0) + 1
                        await db.commit()
                        attr_names = ", ".join(a["name"] for a in woo_attrs)
                        await _log(db, job.id, LogLevel.info,
                                   f"  ✓ {prod.sku} (woo #{prod.woo_product_id}) "
                                   f"→ {len(woo_attrs)} attribute(s): {attr_names}")
                        products_updated += 1
                    except Exception as e:
                        await _log(db, job.id, LogLevel.warn,
                                   f"  Failed to set attributes on {prod.sku} "
                                   f"(woo #{prod.woo_product_id}): {e}")
                else:
                    job.processed_items = (job.processed_items or 0) + 1
                    await db.commit()
                    await _log(db, job.id, LogLevel.warn,
                               f"  ✗ {prod.sku} (woo #{prod.woo_product_id}): "
                               f"no spec data or confirmed attrs — skipping (existing attrs preserved)")
            else:
                await _log(db, job.id, LogLevel.warn,
                           f"  {prod.sku}: not in WooCommerce yet — skipped")

        await _log(db, job.id, LogLevel.info,
                   f"  Attributes done: {attrs_created} new attributes, "
                   f"{terms_created} new terms, {products_updated} product(s) updated")

    await _log(db, job.id, LogLevel.info,
               f"Sync complete — categories: +{cats_created} new / {cats_synced} existing | "
               f"attributes: +{attrs_created} new | terms: +{terms_created} new | "
               f"products updated: {products_updated}")
