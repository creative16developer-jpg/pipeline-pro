"""
Celery tasks for background job processing.
Each task wraps an async runner using asyncio.run().
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
from celery_app import celery_app

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


def _run(coro):
    return asyncio.run(coro)


@celery_app.task(bind=True, name="tasks.run_job")
def run_job(self, job_id: int):
    import sys
    from pathlib import Path
    _d = str(Path(__file__).parent.parent.resolve())
    if _d not in sys.path:
        sys.path.insert(0, _d)
    _run(_execute_job(job_id))


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
    finally:
        await celery_engine.dispose()


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
                    await _log(db, job.id, LogLevel.info,
                               f"  Updated {p['sku']}: {', '.join(changed_fields)} changed")
                    updated += 1
                else:
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
    from sqlalchemy import select

    cfg = job.config or {}
    limit = cfg.get("limit", 200)

    base_q = select(Product).where(Product.status == ProductStatus.pending)

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
                       f"falling back to un-linked pending products")
            fallback_q = base_q.where(Product.fetch_job_id.is_(None)).limit(limit)
            products = (await db.execute(fallback_q)).scalars().all()
            if products:
                await _log(db, job.id, LogLevel.info,
                           f"Found {len(products)} un-linked pending product(s) to process")
    else:
        await _log(db, job.id, LogLevel.info,
                   "No source job selected — processing ALL pending products")
        products = (await db.execute(base_q.limit(limit))).scalars().all()

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
                                status=ImageStatus.watermarked,
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
                        status=ImageStatus.watermarked,
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
      1. Upload processed/watermarked WebP files to WordPress media library
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
            Image.status == ImageStatus.watermarked,
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
            for img in processed_images:
                wp_url = await wc.upload_image_to_wordpress(store, img.processed_path)
                if wp_url:
                    urls.append(wp_url)
                    await _log(db, job.id, LogLevel.debug,
                               f"    pos={img.position} → {wp_url}")
                else:
                    await _log(db, job.id, LogLevel.warn,
                               f"    pos={img.position} WP upload failed — {img.processed_path}")
            if urls:
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

    if not products:
        await _log(db, job.id, LogLevel.info,
                   "No products to upload (all already uploaded or none match filter)")
        return

    job.total_items = len(products)
    await db.commit()

    created_count = updated_count = skipped_count = failed_count = 0

    for i, product in enumerate(products):
        action = "?"
        try:
            raw = product.raw_data or {}

            payload = {
                "name":           product.name,
                "sku":            product.sku,
                "price":          product.price or "0",
                "description":    product.description or "",
                "stock_quantity": 10 if product.stock_status == "in_stock" else 0,
            }

            if not skip_images:
                image_urls = await _resolve_product_images(db, job, product, raw, wc, store)
                if image_urls:
                    payload["images"] = image_urls

            # ── Check if SKU already exists in WooCommerce (prevents duplicates)
            existing_woo = await wc.get_product_by_sku(store, product.sku)

            if existing_woo:
                woo_id   = existing_woo["id"]
                woo_name = existing_woo.get("name", "")
                woo_price = str(existing_woo.get("regular_price") or existing_woo.get("price") or "")
                woo_stock = existing_woo.get("stock_quantity", 0)

                local_price  = str(product.price or "0")
                local_stock  = 10 if product.stock_status == "in_stock" else 0
                local_name   = product.name or ""

                # Compare — only update if something changed
                needs_update = (
                    local_name  != woo_name or
                    local_price != woo_price or
                    local_stock != woo_stock
                )

                if needs_update:
                    await wc.update_product(store, woo_id, payload)
                    product.woo_product_id = woo_id
                    product.status = ProductStatus.uploaded
                    product.error_message = None
                    action = "updated"
                    updated_count += 1
                    await _log(db, job.id, LogLevel.info,
                               f"  {product.sku} → UPDATED woo_id={woo_id} "
                               f"(price: {woo_price}→{local_price}, stock: {woo_stock}→{local_stock})")
                else:
                    product.woo_product_id = woo_id
                    product.status = ProductStatus.uploaded
                    product.error_message = None
                    action = "skipped"
                    skipped_count += 1
                    await _log(db, job.id, LogLevel.info,
                               f"  {product.sku} → SKIPPED (already up-to-date in WooCommerce, woo_id={woo_id})")
            else:
                # Create new product in WooCommerce
                await _log(db, job.id, LogLevel.info,
                           f"  {product.sku} → creating in WooCommerce…")
                result = await wc.create_product(store, payload)
                product.woo_product_id = result.get("id")
                product.status = ProductStatus.uploaded
                product.error_message = None
                action = "created"
                created_count += 1
                await _log(db, job.id, LogLevel.info,
                           f"  {product.sku} → CREATED woo_id={product.woo_product_id}")

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
    # Only process products that were successfully uploaded this run
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

        await _log(db, job.id, LogLevel.info,
                   f"  WooCommerce: {len(woo_cats)} categories, "
                   f"{len(woo_global_attrs)} global attributes loaded | "
                   f"Sunsky cache: {len(p2_cat_cache)} entries")

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

            # Primary: resolve via disk cache → full hierarchy root → leaf
            cat_woo_ids: list[int] = []
            cat_names:   list[str] = []
            if sunsky_cat_id:
                cat_woo_ids, cat_names = await _p2_collect_hierarchy(sunsky_cat_id)

            # Fallback: if cache miss but raw_data has a plain category name
            if not cat_woo_ids and cat_name_direct:
                woo_cat_id = p2_cat_by_name.get(cat_name_direct.lower())
                if not woo_cat_id:
                    try:
                        resp = await woo_client.create_woo_category(
                            store, cat_name_direct, 0
                        )
                        woo_cat_id = resp["id"]
                        p2_cat_by_name[cat_name_direct.lower()] = woo_cat_id
                        await _log(db, job.id, LogLevel.info,
                                   f"  Created WooCommerce category (from catName field): "
                                   f"{cat_name_direct!r} → #{woo_cat_id}")
                    except Exception as _ce:
                        await _log(db, job.id, LogLevel.warn,
                                   f"  Cannot create fallback category {cat_name_direct!r}: {_ce}")
                if woo_cat_id:
                    cat_woo_ids = [woo_cat_id]
                    cat_names   = [cat_name_direct]

            # Set the full category hierarchy on the product
            try:
                await woo_client.set_product_categories(
                    store, prod.woo_product_id, cat_woo_ids
                )
                if cat_woo_ids:
                    p2_cat_ok += 1
                    path_str = " → ".join(cat_names) or str(cat_woo_ids)
                    await _log(db, job.id, LogLevel.info,
                               f"  ✓ {prod.sku} → {len(cat_woo_ids)}-level hierarchy: "
                               f"{path_str} "
                               f"(woo ids: {cat_woo_ids})")
                else:
                    p2_cat_miss += 1
                    await _log(db, job.id, LogLevel.warn,
                               f"  ✗ {prod.sku}: category {sunsky_cat_id!r} not in cache "
                               f"— run a Sync job once to build the category cache")
            except Exception as _ce:
                await _log(db, job.id, LogLevel.warn,
                           f"  ✗ {prod.sku}: set_categories failed — {_ce}")

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

            try:
                await woo_client.set_product_attributes(
                    store, prod.woo_product_id, woo_attrs
                )
                if woo_attrs:
                    p2_attr_ok += 1
                    await _log(db, job.id, LogLevel.info,
                               f"  ✓ {prod.sku} → {len(woo_attrs)} attribute(s): "
                               f"{', '.join(a['name'] for a in woo_attrs)}")
                else:
                    p2_attr_miss += 1
                    await _log(db, job.id, LogLevel.warn,
                               f"  ✗ {prod.sku}: no spec data in raw_data "
                               f"(run Process job to fetch detail from Sunsky)")
            except Exception as _ae:
                await _log(db, job.id, LogLevel.warn,
                           f"  ✗ {prod.sku}: set_attributes failed — {_ae}")

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

            BATCH_SIZE   = 3    # concurrent Sunsky requests per batch
            BATCH_DELAY  = 1.5  # seconds between batches (≈ 2 req/s → ~120/min, usually safe)
            MAX_DEPTH    = 6
            RATE_LIMIT_PAUSE = 62  # seconds to wait after hitting the per-minute cap

            # ── a) Seed bfs_meta from disk cache ──────────────────────────────
            bfs_meta: dict[str, dict] = {}
            cat_cache = _load_cat_cache()
            cached_now = datetime.now(timezone.utc).isoformat()
            for cid, entry in cat_cache.items():
                bfs_meta[cid] = entry

            remaining = needed_cat_ids - set(bfs_meta.keys())
            cache_hits = len(needed_cat_ids) - len(remaining)
            await _log(db, job.id, LogLevel.info,
                       f"  Category cache: {len(cat_cache)} entries loaded "
                       f"({cache_hits}/{len(needed_cat_ids)} needed IDs already cached"
                       + (f", {len(remaining)} need BFS)" if remaining else ", all found ✓)"))

            # ── b) BFS for IDs not in cache ───────────────────────────────────
            if remaining:
                seen_fetch: set[str] = {"0"}

                async def _fetch_safe(pid: str) -> tuple[str, list]:
                    """Fetch with automatic retry on rate-limit (waits 62 s)."""
                    for attempt in range(3):
                        try:
                            kids = await sunsky_client.get_categories(pid)
                            return (pid, kids)
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

            await _log(db, job.id, LogLevel.info,
                       f"  Categories: {cats_created} created, {cats_synced} already existed "
                       f"— {len(sunsky_to_woo_cat)} ready to assign")

            # ── 5. Assign WooCommerce categories — strictly from Sunsky data ─
            # Rule: each product gets ONLY its Sunsky leaf category.
            #   • Mapped    → PUT categories=[{id: woo_cat_id}]   (replaces all)
            #   • Not found → PUT categories=[]                    (clears unrelated cats)
            #   • No woo_id → skip (product not in WooCommerce yet)
            await _log(db, job.id, LogLevel.info, "  Assigning categories to products in WooCommerce…")
            cat_ok = cat_miss = 0
            for prod in target_products:
                if not prod.woo_product_id:
                    await _log(db, job.id, LogLevel.warn,
                               f"  ✗ {prod.sku}: not uploaded to WooCommerce yet — skipped")
                    continue

                sunsky_cat_id = _get_sunsky_cat_id(prod)
                woo_cat_id = sunsky_to_woo_cat.get(sunsky_cat_id) if sunsky_cat_id else None
                cat_payload = [woo_cat_id] if woo_cat_id else []

                try:
                    await woo_client.set_product_categories(
                        store, prod.woo_product_id, cat_payload
                    )
                    if woo_cat_id:
                        products_updated += 1
                        cat_ok += 1
                        await _log(db, job.id, LogLevel.info,
                                   f"  ✓ {prod.sku} (woo #{prod.woo_product_id}) "
                                   f"→ category #{woo_cat_id} (Sunsky {sunsky_cat_id})")
                    else:
                        cat_miss += 1
                        reason = (
                            "no categoryId in Sunsky data"
                            if not sunsky_cat_id
                            else f"Sunsky ID {sunsky_cat_id!r} not found in tree"
                        )
                        await _log(db, job.id, LogLevel.warn,
                                   f"  ✗ {prod.sku} (woo #{prod.woo_product_id}): {reason} "
                                   f"— categories cleared")
                except Exception as e:
                    await _log(db, job.id, LogLevel.warn,
                               f"  ✗ {prod.sku}: set_categories failed — {e}")

            await _log(db, job.id, LogLevel.info,
                       f"  Category assignment: {cat_ok} assigned ✓  |  {cat_miss} cleared (no mapping)")

    # ─────────────────────────────────────────────────────────────────────────
    # STEP B: Sync product attributes → WooCommerce
    # ─────────────────────────────────────────────────────────────────────────
    if do_attributes:
        await _log(db, job.id, LogLevel.info, "── Step B: Syncing product attributes ──")

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

            # Always push to WooCommerce — even an empty list clears any
            # previously-set unrelated attributes (strict Sunsky-only rule).
            if prod.woo_product_id:
                try:
                    await woo_client.set_product_attributes(store, prod.woo_product_id, woo_attrs)
                    job.processed_items = (job.processed_items or 0) + 1
                    await db.commit()
                    if woo_attrs:
                        attr_names = ", ".join(a["name"] for a in woo_attrs)
                        await _log(db, job.id, LogLevel.info,
                                   f"  ✓ {prod.sku} (woo #{prod.woo_product_id}) "
                                   f"→ {len(woo_attrs)} attribute(s): {attr_names}")
                        products_updated += 1
                    else:
                        await _log(db, job.id, LogLevel.warn,
                                   f"  ✗ {prod.sku} (woo #{prod.woo_product_id}): "
                                   f"no Sunsky spec data — attributes cleared")
                except Exception as e:
                    await _log(db, job.id, LogLevel.warn,
                               f"  Failed to set attributes on {prod.sku} "
                               f"(woo #{prod.woo_product_id}): {e}")
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
