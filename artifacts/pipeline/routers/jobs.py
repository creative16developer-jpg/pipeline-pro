import asyncio
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from database import get_db
from models.models import Job, JobStatus, JobType, JobLog, LogLevel, Product, ProductStatus
from schemas.schemas import JobCreate, JobOut, JobListOut
from pipeline import sunsky_client

router = APIRouter(prefix="/jobs", tags=["jobs"])

_running_tasks: dict[int, asyncio.Task] = {}


@router.get("", response_model=JobListOut)
async def list_jobs(
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
):
    total = (await db.execute(select(func.count(Job.id)))).scalar_one()
    jobs = (
        await db.execute(
            select(Job).order_by(Job.created_at.desc()).offset((page - 1) * limit).limit(limit)
        )
    ).scalars().all()
    return JobListOut(
        jobs=[JobOut.model_validate(j) for j in jobs],
        total=total,
        page=page,
        limit=limit,
    )


@router.post("", response_model=JobOut)
async def create_job(body: JobCreate, db: AsyncSession = Depends(get_db)):
    try:
        job_type = JobType(body.type)
    except ValueError:
        raise HTTPException(400, f"Invalid job type: {body.type}")

    job = Job(
        type=job_type,
        status=JobStatus.pending,
        store_id=body.store_id,
        config=body.config or {},
        started_at=datetime.now(timezone.utc),
    )
    db.add(job)
    await db.commit()
    await db.refresh(job)

    task = asyncio.create_task(_run_job(job.id))
    _running_tasks[job.id] = task

    return JobOut.model_validate(job)


@router.get("/{job_id}", response_model=JobOut)
async def get_job(job_id: int, db: AsyncSession = Depends(get_db)):
    job = await db.get(Job, job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    return JobOut.model_validate(job)


@router.post("/{job_id}/cancel", response_model=JobOut)
async def cancel_job(job_id: int, db: AsyncSession = Depends(get_db)):
    job = await db.get(Job, job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    if job.status not in (JobStatus.pending, JobStatus.running):
        raise HTTPException(400, "Job cannot be cancelled")

    task = _running_tasks.get(job_id)
    if task:
        task.cancel()

    job.status = JobStatus.cancelled
    job.completed_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(job)
    return JobOut.model_validate(job)


async def _run_job(job_id: int):
    """Background task — run a pipeline job."""
    from database import AsyncSessionLocal

    async with AsyncSessionLocal() as db:
        job = await db.get(Job, job_id)
        if not job:
            return

        job.status = JobStatus.running
        job.started_at = datetime.now(timezone.utc)
        await db.commit()

        try:
            if job.type == JobType.fetch:
                await _run_fetch_job(db, job)
            elif job.type == JobType.process:
                await _run_process_job(db, job)
            elif job.type == JobType.upload:
                await _run_upload_job(db, job)
            elif job.type == JobType.sync:
                await _run_sync_job(db, job)

            job.status = JobStatus.completed
            job.progress_percent = 100.0
        except asyncio.CancelledError:
            pass
        except Exception as e:
            job.status = JobStatus.failed
            job.error_message = str(e)
            await _log(db, job.id, LogLevel.error, f"Job failed: {e}")

        job.completed_at = datetime.now(timezone.utc)
        await db.commit()


async def _run_fetch_job(db: AsyncSession, job: Job):
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
    total = result.get("total", len(products))

    job.total_items = len(products)
    await db.commit()

    saved = 0
    skipped = 0

    for i, p in enumerate(products):
        from sqlalchemy import select as sql_select
        existing = (
            await db.execute(sql_select(Product).where(Product.sunsky_id == str(p["id"])))
        ).scalar_one_or_none()

        if existing:
            skipped += 1
        else:
            product = Product(
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
            )
            db.add(product)
            saved += 1

        job.processed_items = i + 1
        job.progress_percent = round((i + 1) / len(products) * 100, 1)
        if (i + 1) % 10 == 0:
            await db.commit()

    await db.commit()
    await _log(db, job.id, LogLevel.info, f"Fetch complete: {saved} saved, {skipped} skipped")


async def _run_process_job(db: AsyncSession, job: Job):
    from sqlalchemy import select as sql_select
    from pipeline.image_processor import ImageProcessor

    products = (
        await db.execute(
            sql_select(Product)
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
            images = raw.get("images", [])
            for pos, url in enumerate(images[:5]):
                await processor.process(url, product.sku, pos)
            product.status = ProductStatus.processed
        except Exception as e:
            product.status = ProductStatus.failed
            product.error_message = str(e)

        job.processed_items = i + 1
        job.progress_percent = round((i + 1) / len(products) * 100, 1)
        await db.commit()

    await _log(db, job.id, LogLevel.info, f"Processed {len(products)} products")


async def _run_upload_job(db: AsyncSession, job: Job):
    from sqlalchemy import select as sql_select
    from pipeline import woo_client as wc

    if not job.store_id:
        raise ValueError("store_id required for upload jobs")

    from models.models import Store
    store = await db.get(Store, job.store_id)
    if not store:
        raise ValueError("Store not found")

    products = (
        await db.execute(
            sql_select(Product)
            .where(Product.status == ProductStatus.processed)
            .limit(job.config.get("limit", 50) if job.config else 50)
        )
    ).scalars().all()

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
            job.failed_items += 1

        job.processed_items = i + 1
        job.progress_percent = round((i + 1) / len(products) * 100, 1)
        await db.commit()

    await _log(db, job.id, LogLevel.info, f"Upload complete for {len(products)} products")


async def _run_sync_job(db: AsyncSession, job: Job):
    await asyncio.sleep(2)
    await _log(db, job.id, LogLevel.info, "Sync job completed (stub — real sync in M3)")


async def _log(db: AsyncSession, job_id: int, level: LogLevel, message: str):
    db.add(JobLog(job_id=job_id, level=level, message=message))
    await db.commit()
