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
from pathlib import Path
from typing import Optional
from celery_app import celery_app


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

    await _log(db, job.id, LogLevel.info,
               f"Starting sync → store: {store.name} | categories={do_categories} | attributes={do_attributes}")

    cats_synced = cats_created = 0
    attrs_synced = attrs_created = terms_created = 0
    products_updated = 0

    # ─────────────────────────────────────────────────────────────────────────
    # STEP A: Sync Sunsky categories → WooCommerce
    # ─────────────────────────────────────────────────────────────────────────
    # sunsky_cat_id → woo_cat_id mapping (used later for product category update)
    sunsky_to_woo_cat: dict[str, int] = {}

    if do_categories:
        await _log(db, job.id, LogLevel.info, "── Step A: Syncing categories ──")

        # Load all existing WooCommerce categories into a lookup: (name_lower, parent_woo_id) → woo_id
        existing_woo_cats = await woo_client.get_all_woo_categories(store)
        woo_cat_lookup: dict[tuple, int] = {
            (c["name"].lower(), int(c.get("parent") or 0)): c["id"]
            for c in existing_woo_cats
        }
        await _log(db, job.id, LogLevel.info, f"  {len(existing_woo_cats)} existing WooCommerce categories loaded")

        # Fetch Sunsky parent categories
        try:
            parents = await sunsky_client.get_categories("0")
        except Exception as e:
            await _log(db, job.id, LogLevel.error, f"  Failed to fetch Sunsky parent categories: {e}")
            parents = []

        job.total_items = (job.total_items or 0) + len(parents)
        await db.commit()

        for parent in parents:
            parent_woo_id = woo_cat_lookup.get((parent["name"].lower(), 0))
            if not parent_woo_id:
                try:
                    created = await woo_client.create_woo_category(store, parent["name"], 0)
                    parent_woo_id = created["id"]
                    woo_cat_lookup[(parent["name"].lower(), 0)] = parent_woo_id
                    cats_created += 1
                    await _log(db, job.id, LogLevel.debug,
                               f"  Created WooCommerce category: {parent['name']} (id={parent_woo_id})")
                except Exception as e:
                    await _log(db, job.id, LogLevel.warn,
                               f"  Could not create category {parent['name']!r}: {e}")
                    continue
            else:
                cats_synced += 1

            sunsky_to_woo_cat[parent["id"]] = parent_woo_id
            if parent.get("alias_id"):
                sunsky_to_woo_cat[parent["alias_id"]] = parent_woo_id

            # Fetch children of this parent — use raw primary ID for API call
            fetch_parent_id = parent["id"] or parent.get("alias_id", "")
            try:
                children = await sunsky_client.get_categories(fetch_parent_id)
            except Exception as e:
                await _log(db, job.id, LogLevel.warn, f"  Could not fetch children of {parent['name']}: {e}")
                children = []

            for child in children:
                child_woo_id = woo_cat_lookup.get((child["name"].lower(), parent_woo_id))
                if not child_woo_id:
                    try:
                        created = await woo_client.create_woo_category(store, child["name"], parent_woo_id)
                        child_woo_id = created["id"]
                        woo_cat_lookup[(child["name"].lower(), parent_woo_id)] = child_woo_id
                        cats_created += 1
                        await _log(db, job.id, LogLevel.debug,
                                   f"    Created sub-category: {child['name']} (id={child_woo_id})")
                    except Exception as e:
                        await _log(db, job.id, LogLevel.warn,
                                   f"    Could not create sub-category {child['name']!r}: {e}")
                        continue
                else:
                    cats_synced += 1

                sunsky_to_woo_cat[child["id"]] = child_woo_id
                if child.get("alias_id"):
                    sunsky_to_woo_cat[child["alias_id"]] = child_woo_id

        await _log(db, job.id, LogLevel.info,
                   f"  Categories done: {cats_created} created, {cats_synced} already existed "
                   f"({len(sunsky_to_woo_cat)} total mapped)")

        # Update WooCommerce products with the correct category
        await _log(db, job.id, LogLevel.info, "  Updating product categories in WooCommerce…")
        cat_q = select(Product).where(
            Product.woo_product_id.isnot(None),
            Product.status == ProductStatus.uploaded,
        )
        if source_job_id:
            cat_q = cat_q.where(Product.fetch_job_id == source_job_id)
        cat_q = cat_q.limit(limit)
        cat_products = (await db.execute(cat_q)).scalars().all()

        cat_ok = cat_miss = 0
        for prod in cat_products:
            raw = prod.raw_data or {}
            # Try multiple sources for the Sunsky category ID
            sunsky_cat_id = (
                str(raw.get("categoryId") or "").strip()          # from raw Sunsky search data
                or str(raw.get("category_id") or "").strip()      # normalized field stored in raw_data
                or str(prod.category_id or "").strip()            # Product model column
            )
            woo_cat_id = sunsky_to_woo_cat.get(sunsky_cat_id)
            if woo_cat_id and prod.woo_product_id:
                try:
                    await woo_client.set_product_categories(store, prod.woo_product_id, [woo_cat_id])
                    products_updated += 1
                    cat_ok += 1
                except Exception as e:
                    await _log(db, job.id, LogLevel.warn,
                               f"  Could not update category for product {prod.sku}: {e}")
            elif not woo_cat_id and sunsky_cat_id:
                cat_miss += 1
                await _log(db, job.id, LogLevel.debug,
                           f"  {prod.sku}: Sunsky cat_id={sunsky_cat_id!r} not in mapping (skipped)")
            elif not sunsky_cat_id:
                await _log(db, job.id, LogLevel.debug,
                           f"  {prod.sku}: no category_id found on product (skipped)")
        if cat_miss:
            await _log(db, job.id, LogLevel.warn,
                       f"  {cat_miss} product(s) had a category_id not found in the Sunsky→WooCommerce map "
                       f"(they may belong to a sub-category not returned by the top-2-level tree)")

        await _log(db, job.id, LogLevel.info,
                   f"  Category update done: {products_updated} product(s) updated")

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

        # Query uploaded products with a woo_product_id
        attr_q = select(Product).where(Product.woo_product_id.isnot(None))
        if source_job_id:
            attr_q = attr_q.where(Product.fetch_job_id == source_job_id)
        attr_q = attr_q.limit(limit)
        attr_products = (await db.execute(attr_q)).scalars().all()

        job.total_items = (job.total_items or 0) + len(attr_products)
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

            if woo_attrs and prod.woo_product_id:
                try:
                    await woo_client.set_product_attributes(store, prod.woo_product_id, woo_attrs)
                    products_updated += 1
                    job.processed_items = (job.processed_items or 0) + 1
                    await db.commit()
                except Exception as e:
                    await _log(db, job.id, LogLevel.warn,
                               f"  Failed to set attributes on product {prod.sku} "
                               f"(woo_id={prod.woo_product_id}): {e}")
            elif not woo_attrs:
                await _log(db, job.id, LogLevel.debug,
                           f"  {prod.sku}: no attributes extracted, skipping")

        await _log(db, job.id, LogLevel.info,
                   f"  Attributes done: {attrs_created} new attributes, "
                   f"{terms_created} new terms, {products_updated} product(s) updated")

    await _log(db, job.id, LogLevel.info,
               f"Sync complete — categories: +{cats_created} new / {cats_synced} existing | "
               f"attributes: +{attrs_created} new | terms: +{terms_created} new | "
               f"products updated: {products_updated}")
