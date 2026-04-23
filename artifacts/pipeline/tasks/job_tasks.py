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
from celery_app import celery_app


def _run(coro):
    return asyncio.run(coro)


@celery_app.task(bind=True, name="tasks.run_job")
def run_job(self, job_id: int):
    # Re-apply path fix inside the worker process (ForkPoolWorker may not
    # inherit the module-level sys.path change reliably).
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

    # Use a fresh engine/session bound to THIS event loop (Celery creates a
    # new loop per task via asyncio.run, so the global engine would cause
    # "Future attached to a different loop" errors).
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
# FETCH — pull products from Sunsky, stamp each with this job's id
# ---------------------------------------------------------------------------

async def _run_fetch(db, job):
    from models.models import Product, ProductStatus, LogLevel
    from pipeline import sunsky_client
    from sqlalchemy import select

    cfg = job.config or {}
    page = cfg.get("page", 1)
    limit = cfg.get("limit", 50)
    category_id = cfg.get("category_id")
    keyword = cfg.get("keyword")

    await _log(
        db, job.id, LogLevel.info,
        f"Fetch started — page={page}, limit={limit}"
        + (f", keyword='{keyword}'" if keyword else "")
        + (f", category_id={category_id}" if category_id else ""),
    )

    result = await sunsky_client.search_products(
        category_id=category_id, keyword=keyword, page=page, limit=limit
    )
    products = result.get("products", [])

    await _log(db, job.id, LogLevel.info, f"Sunsky returned {len(products)} product(s)")

    job.total_items = len(products)
    await db.commit()

    saved = skipped = 0
    for i, p in enumerate(products):
        existing = (
            await db.execute(select(Product).where(Product.sunsky_id == str(p["id"])))
        ).scalar_one_or_none()

        if existing:
            skipped += 1
        else:
            images = p.get("images", [])
            raw_data = p.get("raw_data", {})
            db.add(Product(
                sunsky_id=str(p["id"]),
                sku=p["sku"],
                name=p["name"],
                description=p.get("description", ""),
                price=p.get("price", "0"),
                stock_status=p.get("stock_status", "in_stock"),
                category_id=p.get("category_id", ""),
                image_count=len(images),
                raw_data=raw_data,
                status=ProductStatus.pending,
                fetch_job_id=job.id,   # stamp which fetch job created this product
            ))
            saved += 1

        job.processed_items = i + 1
        job.progress_percent = round((i + 1) / len(products) * 100, 1)
        if (i + 1) % 10 == 0:
            await db.commit()

    await db.commit()
    await _log(
        db, job.id, LogLevel.info,
        f"Fetch complete — {saved} saved, {skipped} skipped (already in DB)",
    )


# ---------------------------------------------------------------------------
# PROCESS — download + compress images; scoped to a single fetch job
# ---------------------------------------------------------------------------

async def _run_process(db, job):
    from models.models import Product, ProductStatus, Image, ImageStatus, LogLevel
    from pipeline.image_processor import ImageProcessor
    from sqlalchemy import select

    cfg = job.config or {}

    # If a source_job_id is set, only process products from that fetch job
    q = select(Product).where(Product.status == ProductStatus.pending)
    if job.source_job_id:
        q = q.where(Product.fetch_job_id == job.source_job_id)
        await _log(db, job.id, LogLevel.info,
                   f"Process scoped to fetch job #{job.source_job_id}")
    else:
        await _log(db, job.id, LogLevel.info,
                   "No source job selected — processing ALL pending products")

    q = q.limit(cfg.get("limit", 50))
    products = (await db.execute(q)).scalars().all()

    if not products:
        await _log(db, job.id, LogLevel.info, "No pending products to process")
        return

    job.total_items = len(products)
    await db.commit()

    processor = ImageProcessor()

    for i, product in enumerate(products):
        product.status = ProductStatus.processing
        await db.commit()

        try:
            raw = product.raw_data or {}

            # Get the normalised absolute-URL list stored during fetch
            image_urls = raw.get("images", [])
            if isinstance(image_urls, str):
                image_urls = [image_urls]
            image_urls = [
                u for u in image_urls if isinstance(u, str) and u.startswith("http")
            ][:5]

            await _log(db, job.id, LogLevel.info,
                       f"Product {product.sku} (id={product.id}): "
                       f"{len(image_urls)} image(s) — {image_urls}")

            processed_count = 0
            for pos, url in enumerate(image_urls):
                try:
                    processed_path = await processor.process(url, product.sku, pos)
                    await _log(db, job.id, LogLevel.debug,
                               f"  [{pos}] {url} → {processed_path}")

                    img_status = ImageStatus.watermarked if processed_path else ImageStatus.failed
                    db.add(Image(
                        product_id=product.id,
                        original_url=url,
                        processed_path=processed_path,
                        position=pos,
                        status=img_status,
                        is_main=(pos == 0),
                    ))
                    if processed_path:
                        processed_count += 1
                        await _log(db, job.id, LogLevel.debug,
                                   f"  DB insert OK — image {pos} for product {product.id}")
                    else:
                        await _log(db, job.id, LogLevel.warn,
                                   f"  processor returned None for {url}")

                except Exception as img_err:
                    await _log(db, job.id, LogLevel.error,
                               f"  Image {pos} error for {product.sku}: {img_err}")
                    db.add(Image(
                        product_id=product.id,
                        original_url=url,
                        position=pos,
                        status=ImageStatus.failed,
                        is_main=(pos == 0),
                        error_message=str(img_err),
                    ))

                await db.commit()

            product.status = ProductStatus.processed
            await _log(db, job.id, LogLevel.info,
                       f"Product {product.sku}: {processed_count}/{len(image_urls)} images saved")

        except Exception as e:
            product.status = ProductStatus.failed
            product.error_message = str(e)
            await _log(db, job.id, LogLevel.error,
                       f"Process failed for {product.sku}: {e}")

        job.processed_items = i + 1
        job.progress_percent = round((i + 1) / len(products) * 100, 1)
        await db.commit()

    await _log(db, job.id, LogLevel.info,
               f"Process job done: {len(products)} product(s) handled")


# ---------------------------------------------------------------------------
# UPLOAD — push to WooCommerce; scoped to a fetch job via source_job_id
# ---------------------------------------------------------------------------

async def _run_upload(db, job):
    from models.models import Product, ProductStatus, Store, LogLevel
    from pipeline import woo_client as wc
    from sqlalchemy import select, or_

    if not job.store_id:
        raise ValueError("store_id required for upload jobs")

    store = await db.get(Store, job.store_id)
    if not store:
        raise ValueError("Store not found")

    cfg = job.config or {}
    skip_images = cfg.get("skip_images", True)

    # Base filter: not yet uploaded to WooCommerce
    base_filter = [
        or_(
            Product.status == ProductStatus.processed,
            Product.status == ProductStatus.pending,
            Product.status == ProductStatus.failed,
        ),
        Product.woo_product_id.is_(None),
    ]

    # If a source_job_id is set, scope to products from that fetch job
    if job.source_job_id:
        # Resolve what fetch job to use:
        # If the source is a process job it scoped its products by a fetch job too —
        # follow it one level up. Otherwise treat it directly as a fetch job.
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
            base_filter.append(Product.fetch_job_id == fetch_job_id)
        else:
            await _log(db, job.id, LogLevel.warn,
                       f"Source job #{job.source_job_id} not found — uploading ALL eligible products")
    else:
        await _log(db, job.id, LogLevel.info,
                   "No source job selected — uploading ALL eligible products")

    products = (
        await db.execute(
            select(Product)
            .where(*base_filter)
            .limit(cfg.get("limit", 50))
        )
    ).scalars().all()

    if not products:
        await _log(db, job.id, LogLevel.info,
                   "No products to upload (all already uploaded or none match the filter)")
        return

    job.total_items = len(products)
    await db.commit()

    for i, product in enumerate(products):
        try:
            raw = product.raw_data or {}

            payload = {
                "name": product.name,
                "price": product.price or "0",
                "description": product.description or "",
                "stock_quantity": 10 if product.stock_status == "in_stock" else 0,
            }

            if not skip_images:
                raw_imgs = raw.get("images", [])
                if isinstance(raw_imgs, list):
                    payload["images"] = [
                        u for u in raw_imgs if isinstance(u, str) and u.startswith("http")
                    ]

            await _log(db, job.id, LogLevel.info,
                       f"Uploading {product.sku} to store #{job.store_id}…")
            result = await wc.create_product(store, payload)
            product.woo_product_id = result.get("id")
            product.status = ProductStatus.uploaded
            product.error_message = None
            await _log(db, job.id, LogLevel.info,
                       f"  ✓ {product.sku} → WooCommerce id={product.woo_product_id}")

        except Exception as e:
            product.status = ProductStatus.failed
            product.error_message = str(e)
            job.failed_items = (job.failed_items or 0) + 1
            await _log(db, job.id, LogLevel.error,
                       f"Failed to upload {product.sku}: {e}")

        job.processed_items = i + 1
        job.progress_percent = round((i + 1) / len(products) * 100, 1)
        await db.commit()

    uploaded = len(products) - (job.failed_items or 0)
    await _log(db, job.id, LogLevel.info,
               f"Upload complete — {uploaded} uploaded, {job.failed_items or 0} failed")


# ---------------------------------------------------------------------------
# SYNC (stub)
# ---------------------------------------------------------------------------

async def _run_sync(db, job):
    from models.models import LogLevel
    import asyncio
    await asyncio.sleep(1)
    await _log(db, job.id, LogLevel.info, "Sync job completed (stub — real sync in M3)")
