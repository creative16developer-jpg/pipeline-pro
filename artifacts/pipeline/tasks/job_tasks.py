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


async def _run_fetch(db, job):
    from models.models import Product, ProductStatus, LogLevel
    from pipeline import sunsky_client
    from sqlalchemy import select

    cfg = job.config or {}
    page = cfg.get("page", 1)
    limit = cfg.get("limit", 50)
    category_id = cfg.get("category_id")
    keyword = cfg.get("keyword")

    await _log(db, job.id, LogLevel.info, f"Fetching from Sunsky (page={page}, limit={limit})")

    result = await sunsky_client.search_products(
        category_id=category_id, keyword=keyword, page=page, limit=limit
    )
    products = result.get("products", [])

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
            db.add(Product(
                sunsky_id=str(p["id"]),
                sku=p["sku"],
                name=p["name"],
                description=p.get("description", ""),
                price=p.get("price", "0"),
                stock_status=p.get("stock_status", "in_stock"),
                category_id=p.get("category_id", ""),
                image_count=len(p.get("images", [])),
                raw_data=p.get("raw_data", {}),
                status=ProductStatus.pending,
            ))
            saved += 1

        job.processed_items = i + 1
        job.progress_percent = round((i + 1) / len(products) * 100, 1)
        if (i + 1) % 10 == 0:
            await db.commit()

    await db.commit()
    await _log(db, job.id, LogLevel.info, f"Fetch complete: {saved} saved, {skipped} skipped")


async def _run_process(db, job):
    from models.models import Product, ProductStatus, LogLevel
    from pipeline.image_processor import ImageProcessor
    from sqlalchemy import select

    products = (
        await db.execute(
            select(Product)
            .where(Product.status == ProductStatus.pending)
            .limit(job.config.get("limit", 50) if job.config else 50)
        )
    ).scalars().all()

    job.total_items = len(products)
    await db.commit()

    processor = ImageProcessor()
    for i, product in enumerate(products):
        product.status = ProductStatus.processing
        await db.commit()
        try:
            raw = product.raw_data or {}
            for pos, url in enumerate((raw.get("images", []))[:5]):
                await processor.process(url, product.sku, pos)
            product.status = ProductStatus.processed
        except Exception as e:
            product.status = ProductStatus.failed
            product.error_message = str(e)

        job.processed_items = i + 1
        job.progress_percent = round((i + 1) / len(products) * 100, 1)
        await db.commit()

    await _log(db, job.id, LogLevel.info, f"Processed {len(products)} products")


async def _run_upload(db, job):
    from models.models import Product, ProductStatus, Store, LogLevel
    from pipeline import woo_client as wc
    from sqlalchemy import select

    if not job.store_id:
        raise ValueError("store_id required for upload jobs")

    store = await db.get(Store, job.store_id)
    if not store:
        raise ValueError("Store not found")

    from sqlalchemy import or_

    # Upload both processed products (went through image processing) and
    # plain pending products (skipped processing step — upload as-is).
    products = (
        await db.execute(
            select(Product)
            .where(
                or_(
                    Product.status == ProductStatus.processed,
                    Product.status == ProductStatus.pending,
                ),
                Product.woo_product_id.is_(None),   # skip already-uploaded
            )
            .limit(job.config.get("limit", 50) if job.config else 50)
        )
    ).scalars().all()

    if not products:
        await _log(db, job.id, LogLevel.info, "No products to upload (all already uploaded or none fetched yet)")
        return

    job.total_items = len(products)
    await db.commit()

    for i, product in enumerate(products):
        try:
            raw = product.raw_data or {}
            result = await wc.create_product(store, {
                "name": product.name,
                "sku": product.sku,
                "price": product.price or "0",
                "description": product.description or "",
                "images": raw.get("images", []),
                "stock_quantity": 10 if product.stock_status == "in_stock" else 0,
            })
            product.woo_product_id = result.get("id")
            product.status = ProductStatus.uploaded
        except Exception as e:
            product.status = ProductStatus.failed
            product.error_message = str(e)
            job.failed_items = (job.failed_items or 0) + 1
            await _log(db, job.id, LogLevel.error, f"Failed to upload {product.sku}: {e}")

        job.processed_items = i + 1
        job.progress_percent = round((i + 1) / len(products) * 100, 1)
        await db.commit()

    await _log(db, job.id, LogLevel.info, f"Upload complete: {len(products) - (job.failed_items or 0)} uploaded, {job.failed_items or 0} failed")


async def _run_sync(db, job):
    from models.models import LogLevel
    import asyncio
    await asyncio.sleep(1)
    await _log(db, job.id, LogLevel.info, "Sync job completed (stub — real sync in M3)")
