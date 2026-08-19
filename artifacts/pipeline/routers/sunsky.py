from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, delete
from database import get_db
from models.models import Job, JobStatus, JobType, ProductStatus, StarredSunskyCategory
from schemas.schemas import SunskyFetchRequest, SunskyFetchResult, SunskyCategoryOut
from pipeline import sunsky_client
from datetime import datetime, timezone
from pydantic import BaseModel
from typing import Optional
import asyncio

router = APIRouter(prefix="/sunsky", tags=["sunsky"])


# ─────────────────────────────────────────────────────────────────────────────
# Starred Sunsky categories
# ─────────────────────────────────────────────────────────────────────────────

class StarCategoryBody(BaseModel):
    id: str
    name: str
    parentName: Optional[str] = None


@router.get("/starred-categories")
async def list_starred_categories(db: AsyncSession = Depends(get_db)):
    rows = (
        await db.execute(
            select(StarredSunskyCategory).order_by(StarredSunskyCategory.name)
        )
    ).scalars().all()
    return [{"id": r.cat_id, "name": r.name, "parentName": r.parent_name} for r in rows]


@router.post("/starred-categories", status_code=200)
async def star_category(body: StarCategoryBody, db: AsyncSession = Depends(get_db)):
    existing = (
        await db.execute(
            select(StarredSunskyCategory).where(StarredSunskyCategory.cat_id == body.id)
        )
    ).scalar_one_or_none()
    if not existing:
        db.add(StarredSunskyCategory(cat_id=body.id, name=body.name, parent_name=body.parentName))
        await db.commit()
    return {"ok": True}


@router.delete("/starred-categories/{cat_id}", status_code=200)
async def unstar_category(cat_id: str, db: AsyncSession = Depends(get_db)):
    row = (
        await db.execute(
            select(StarredSunskyCategory).where(StarredSunskyCategory.cat_id == cat_id)
        )
    ).scalar_one_or_none()
    if row:
        await db.delete(row)
        await db.commit()
    return {"ok": True}



@router.get("/categories", response_model=list[SunskyCategoryOut])
async def get_categories(
    parent_id: str = Query(default="0"),
    response: Response = None,
    db: AsyncSession = Depends(get_db),
):
    """
    Fetch ONE level of Sunsky categories.
    Pass parent_id=0 (default) to get root categories.
    Pass parent_id=<id> to get direct children of that category.
    This is a single API call — fast and lazy.

    Falls back to starred categories (zero API calls, always available)
    when Sunsky's own API is rate-limited, instead of failing the whole
    page. Confirmed live: category!getChildren.do has a daily call quota
    that a heavy testing day can genuinely exhaust — that's an external
    condition, not something retrying or fixing our code can work around,
    so the only real option is degrading gracefully rather than hard-
    failing every category-dependent screen until Sunsky's quota resets.
    Sets X-Sunsky-Fallback: true on the response when this fallback is
    actually used, so the frontend can show a clear note rather than
    silently presenting a partial (starred-only) list as if it were the
    complete live tree.
    """
    try:
        cats = await sunsky_client.get_categories(parent_id=parent_id)
        return [
            SunskyCategoryOut(id=c["id"], name=c["name"], parent_id=c.get("parent_id"))
            for c in cats
        ]
    except Exception as e:
        err_str = str(e)
        is_rate_limit = "UP_TO_API_CALL_LIMIT" in err_str or "rate limit" in err_str.lower()
        if not is_rate_limit:
            raise HTTPException(502, f"Sunsky API error fetching categories: {e}")

        starred_rows = (await db.execute(select(StarredSunskyCategory))).scalars().all()
        name_to_id = {r.name: r.cat_id for r in starred_rows}

        if parent_id == "0":
            # Root level: starred categories whose parent isn't itself starred
            # (best available signal for "this is a top-level entry" without
            # a live tree to check against).
            fallback = [r for r in starred_rows if not r.parent_name or r.parent_name not in name_to_id]
        else:
            parent_name = next((r.name for r in starred_rows if r.cat_id == parent_id), None)
            fallback = [r for r in starred_rows if parent_name and r.parent_name == parent_name]

        if response is not None:
            response.headers["X-Sunsky-Fallback"] = "true"
        return [
            SunskyCategoryOut(id=r.cat_id, name=r.name, parent_id=None)
            for r in fallback
        ]


@router.get("/browse")
async def browse_products(
    category_id: Optional[str] = Query(default=None),
    keyword: Optional[str] = Query(default=None),
    page: int = Query(default=1),
    page_size: int = Query(default=20, le=50),
):
    """
    Read-only product search/preview — mirrors Sunsky's own "Select a
    Product" picker (search by SPU/SKU/keyword or browse a category,
    tick the ones you want). Does NOT touch the database or create a Job;
    it's purely for populating the SKU list before a real fetch. The
    actual import still goes through POST /sunsky/fetch with those SKUs,
    same as manually typing them in.

    `keyword` doubles as the SPU/SKU search box — Sunsky's search API
    matches on item code as well as title, so a pasted SPU/SKU generally
    finds the exact product without needing a separate lookup path.
    """
    try:
        result = await sunsky_client.search_products(
            category_id=category_id, keyword=keyword, page=page, page_size=page_size,
        )
    except Exception as e:
        raise HTTPException(502, f"Sunsky API error searching products: {e}")

    print(f"[browse_products] patch 52 active — {len(result['products'])} result(s), "
          f"checking which need a detail-fetch fallback for images")

    # Confirmed via server logs: Sunsky's search API only ever returns
    # picCount/baseImgCount (counts), never actual image URLs -- no field
    # name was ever going to fix this, the data simply isn't in the
    # search response. Real images require a separate per-product detail
    # call. Bounded concurrency (not all N at once) to avoid making the
    # rate-limit situation worse; each call is isolated so one failure
    # doesn't take down the whole page -- that product just falls back
    # to no image, same as before, rather than erroring the request.
    sem = asyncio.Semaphore(5)

    async def _fetch_image(p: dict) -> Optional[str]:
        existing = (p.get("images") or [None])[0]
        if existing:
            return existing
        async with sem:
            try:
                detail = await sunsky_client.get_product_detail(p["sku"])
            except Exception as e:
                print(f"[browse_products] detail-fetch for {p['sku']} failed: {e}")
                return None
        if detail and detail.get("images"):
            print(f"[browse_products] detail-fetch for {p['sku']} found {len(detail['images'])} image(s)")
            return detail["images"][0]
        print(f"[browse_products] detail-fetch for {p['sku']} returned no images either")
        return None

    fetched_images = await asyncio.gather(*[_fetch_image(p) for p in result["products"]])

    return {
        "products": [
            {
                "sku": p["sku"],
                "name": p["name"],
                "price": p.get("price"),
                "image": img,
            }
            for p, img in zip(result["products"], fetched_images)
        ],
        "total": result["total"],
        "pages": result["pages"],
    }


@router.post("/fetch", response_model=SunskyFetchResult)
async def fetch_products(body: SunskyFetchRequest, db: AsyncSession = Depends(get_db)):
    """
    Fetch products from Sunsky using one or more criteria (OR logic).
    Supported criteria: category, keyword, comma-separated SKU/SPU list.
    All active criteria are searched in parallel and results are deduplicated.
    """
    import asyncio
    from sqlalchemy import select
    from models.models import Product

    # ── Parse SKU list ───────────────────────────────────────────────────────
    sku_list = [s.strip() for s in (body.skus or "").split(",") if s.strip()]

    # ── Build parallel search tasks ──────────────────────────────────────────
    # Each task returns either a list[dict] (SPU path) or a search-result dict
    async def _cat_search():
        return await sunsky_client.search_products(
            category_id=body.category_id,
            page=body.page,
            page_size=body.limit,
        )

    async def _kw_search():
        return await sunsky_client.search_products(
            keyword=body.keyword,
            page=body.page,
            page_size=body.limit,
        )

    tasks = []
    if body.category_id:
        tasks.append(_cat_search())
    if body.keyword:
        tasks.append(_kw_search())
    if sku_list:
        tasks.append(sunsky_client.get_products_by_spus(sku_list))

    # Fall back to a plain (unconstrained) page fetch if no criteria given
    if not tasks:
        tasks.append(_cat_search())

    # ── Record job ───────────────────────────────────────────────────────────
    job = Job(
        type=JobType.fetch,
        status=JobStatus.running,
        store_id=body.store_id,
        started_at=datetime.now(timezone.utc),
        config={
            "category_id": body.category_id,
            "keyword":     body.keyword,
            "skus":        sku_list or None,
            "page_size":   body.limit,
            "page":        body.page,
        },
    )
    db.add(job)
    await db.commit()
    await db.refresh(job)

    try:
        raw_results = await asyncio.gather(*tasks, return_exceptions=True)
    except Exception as e:
        job.status = JobStatus.failed
        job.error_message = str(e)
        job.completed_at = datetime.now(timezone.utc)
        await db.commit()
        raise HTTPException(502, f"Sunsky API error: {e}")

    # ── Merge + deduplicate ───────────────────────────────────────────────────
    seen_ids: set[str] = set()
    products: list[dict] = []
    errors: list[str] = []
    for r in raw_results:
        if isinstance(r, Exception):
            errors.append(str(r))
            continue
        batch: list[dict] = r if isinstance(r, list) else r.get("products", [])
        for p in batch:
            pid = str(p.get("id", ""))
            if pid and pid not in seen_ids:
                seen_ids.add(pid)
                products.append(p)

    if errors and not products:
        job.status = JobStatus.failed
        job.error_message = "; ".join(errors)
        job.completed_at = datetime.now(timezone.utc)
        await db.commit()
        raise HTTPException(502, f"Sunsky API error(s): {'; '.join(errors)}")

    # ── Persist products ─────────────────────────────────────────────────────
    saved = skipped = updated = 0

    for p in products:
        sunsky_id = str(p["id"])
        existing = (
            await db.execute(select(Product).where(Product.sunsky_id == sunsky_id))
        ).scalar_one_or_none()

        if not existing:
            # Not found by Sunsky's internal id -- but a CSV import may have
            # already created a row for this exact item, keyed by SKU
            # instead (CSV import sets sunsky_id = the SKU string directly,
            # which differs from Sunsky's own internal "id" field used
            # here). Check by SKU too before creating a genuine duplicate
            # row for the same real product -- same root cause as the fix
            # in routers/csv_import.py, applied from the other direction.
            existing = (
                await db.execute(select(Product).where(Product.sku == p["sku"]))
            ).scalar_one_or_none()
            if existing:
                # Now that we know the real Sunsky id, correct it — future
                # fetches will then match directly via sunsky_id above.
                existing.sunsky_id = sunsky_id

        images   = p.get("images", [])
        raw_data = p.get("raw_data", {})

        if existing:
            changed = False
            if p["name"] and existing.name != p["name"]:
                existing.name = p["name"]; changed = True
            if p.get("price") and existing.price != p["price"]:
                existing.price = p["price"]; changed = True
            if p.get("stock_status") and existing.stock_status != p["stock_status"]:
                existing.stock_status = p["stock_status"]; changed = True
            if p.get("stock_quantity") is not None and existing.stock_quantity != p["stock_quantity"]:
                existing.stock_quantity = p["stock_quantity"]; changed = True

            # Re-stamp fetch_job_id regardless of whether any field changed —
            # this product IS part of the current fetch's batch, and every
            # downstream step (Process, Enrich, Upload) scopes its product
            # queries to `Product.fetch_job_id == pl.fetch_job_id`. Without
            # this, a product fetched again later (same SKU, no data changes)
            # stayed linked only to whichever pipeline first created it, so
            # every later pipeline saw 0 products even with Force Re-run on.
            # Same fix as tasks/job_tasks.py::_run_fetch — this endpoint is a
            # separate, independent fetch implementation with its own copy
            # of this upsert logic.
            existing.fetch_job_id = job.id

            if changed:
                existing.raw_data = raw_data
                updated += 1
            else:
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
            saved += 1

    job.status = JobStatus.completed
    job.total_items = len(products)
    job.processed_items = saved + updated
    job.failed_items = 0
    job.progress_percent = 100.0
    job.completed_at = datetime.now(timezone.utc)
    await db.commit()

    return SunskyFetchResult(
        fetched=len(products),
        saved=saved,
        skipped=skipped,
        job_id=job.id,
    )
