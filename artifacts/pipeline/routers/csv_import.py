"""
CSV Import router — /api/csv/*

New behaviour (v2): uploading a CSV creates a real Job (type=csv_import) and
upserts Product rows directly into the products table.  The resulting job
appears in the "source" selector on the New Pipeline page just like a Sunsky
fetch job — no Sunsky fetch required beforehand.

CSV Columns (required, case-sensitive):
  Sunsky SKU    — used as the product's unique identifier (sunsky_id / sku)
  Site SKU      — saved to Product.site_sku and used as the WooCommerce SKU
  Product Title — saved to Product.name; also stored in csv_mappings for
                  backward-compat lookup during the pipeline generate step

Backward compat: csv_mappings table is still populated so existing pipelines
that reference a Sunsky fetch job continue to benefit from CSV title/SKU
overrides during the generate step.
"""
from __future__ import annotations

import asyncio
import csv
import io
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
import models.models as M

router = APIRouter(prefix="/csv", tags=["csv"])

REQUIRED_COLUMNS = {"Sunsky SKU", "Site SKU", "Product Title"}
MAX_ROWS = 10_000


async def _enrich_csv_products_from_sunsky(job_id: int, skus: list[str]) -> None:
    """
    Background task: for each CSV-imported row, fetch the FULL product from
    Sunsky by its real item number (the "Sunsky SKU" column) — the same
    data a Sunsky category fetch would produce (images, category, spec
    table for attribute extraction) — just driven by an explicit SKU list
    from the CSV instead of browsing a category tree.

    Runs after upload_csv() has already returned a fast response with the
    bare products created; this fills them in over time, paced to respect
    Sunsky's per-minute rate limit (same pacing as sunsky_client's category
    tree walk). Products the operator's CSV Title/Price already supplied
    are NOT overwritten — only raw_data (and price/stock as a fallback when
    the CSV left them blank) get filled in from Sunsky.
    """
    from database import AsyncSessionLocal
    from pipeline.sunsky_client import get_product_detail

    ok = failed = 0
    async with AsyncSessionLocal() as db:
        for i, sku in enumerate(skus):
            try:
                detail = await get_product_detail(sku)
                if detail:
                    product = (
                        await db.execute(select(M.Product).where(M.Product.sunsky_id == sku))
                    ).scalar_one_or_none()
                    if not product:
                        # Fall back to sku, same reasoning as the fix in
                        # upload_csv()'s upsert — sunsky_id may have already
                        # been corrected to Sunsky's real internal id by a
                        # regular fetch that ran after this CSV row was
                        # created.
                        product = (
                            await db.execute(select(M.Product).where(M.Product.sku == sku))
                        ).scalar_one_or_none()
                    if product:
                        product.raw_data = detail.get("raw_data") or {}
                        # category_id was previously never set for
                        # CSV-imported products at all -- only raw_data was
                        # filled in, so anything reading Product.category_id
                        # directly (rather than digging through raw_data)
                        # found nothing.
                        if detail.get("category_id"):
                            product.category_id = detail["category_id"]
                        if not product.price and detail.get("price"):
                            product.price = detail["price"]
                        if detail.get("stock_status"):
                            product.stock_status = detail["stock_status"]
                        await db.commit()
                        ok += 1
                    else:
                        failed += 1
                else:
                    failed += 1
                    print(f"[csv_import] job #{job_id}: Sunsky SKU {sku!r} not found — "
                          f"product kept with CSV-only data (no images/category/spec).")
            except Exception as exc:
                failed += 1
                print(f"[csv_import] job #{job_id}: enrichment failed for {sku!r}: {exc}")

            if i and i % 20 == 0:
                print(f"[csv_import] job #{job_id}: enriched {i}/{len(skus)} so far…")

            # Sunsky enforces a per-minute call limit — same pacing used
            # elsewhere for per-item Sunsky API calls.
            await asyncio.sleep(0.3)

    print(f"[csv_import] job #{job_id}: Sunsky enrichment complete — {ok} ok, {failed} failed.")


# ---------------------------------------------------------------------------
# POST /api/csv/upload
# ---------------------------------------------------------------------------

@router.post("/upload")
async def upload_csv(
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
):
    """
    Upload a CSV file.  Creates a csv_import Job + upserts Product rows so the
    batch is immediately available as a pipeline source and in Content Generation.

    Returns: { imported, job_id, errors, preview }
    """
    if not (file.filename or "").lower().endswith(".csv"):
        raise HTTPException(400, "File must be a .csv")

    content = await file.read()
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = content.decode("latin-1", errors="replace")

    reader = csv.DictReader(io.StringIO(text))

    if not reader.fieldnames:
        raise HTTPException(400, "CSV appears empty or has no headers")

    normalized = {(f or "").strip() for f in reader.fieldnames}
    missing = REQUIRED_COLUMNS - normalized
    if missing:
        raise HTTPException(
            400,
            f"Missing columns: {', '.join(sorted(missing))}. "
            f"Found: {', '.join(sorted(normalized))}",
        )

    rows: list[dict] = []
    errors: list[str] = []

    for i, row in enumerate(reader):
        if i >= MAX_ROWS:
            break
        sunsky_sku = (row.get("Sunsky SKU") or "").strip()
        site_sku   = (row.get("Site SKU") or "").strip()
        csv_title  = (row.get("Product Title") or "").strip()
        price_raw  = (row.get("Price") or "").strip()

        if not sunsky_sku:
            errors.append(f"Row {i + 2}: missing Sunsky SKU — skipped")
            continue

        # Validate price if provided
        price: str | None = None
        if price_raw:
            try:
                price = str(round(float(price_raw.replace(",", ".")), 2))
            except ValueError:
                errors.append(f"Row {i + 2}: invalid price '{price_raw}' — price ignored")

        rows.append({"sunsky_sku": sunsky_sku, "site_sku": site_sku, "csv_title": csv_title, "price": price})

    if not rows:
        raise HTTPException(400, f"No valid rows found. Errors: {errors[:5]}")

    filename = file.filename or "import.csv"

    # ── 1. Create a csv_import Job (status=completed, acts as the source batch)
    job = M.Job(
        type=M.JobType.csv_import,
        status=M.JobStatus.completed,
        store_id=None,
        total_items=len(rows),
        processed_items=len(rows),
        failed_items=0,
        progress_percent=100.0,
        started_at=datetime.now(timezone.utc),
        completed_at=datetime.now(timezone.utc),
        config={"filename": filename, "source": "csv"},
    )
    db.add(job)
    await db.flush()  # get job.id

    # ── 2. Upsert Products, matched by SKU (item number) — not sunsky_id.
    #
    # Found live: a product fetched normally via "Fetch from Sunsky" and the
    # SAME real product later imported via CSV ended up as TWO separate rows
    # for the same real item, sharing the same woo_product_id (WooCommerce
    # itself correctly recognized the SKU and updated in place — no
    # duplicate there), but the local products table had a genuine
    # duplicate. Root cause: _normalise_product (used by the regular fetch
    # path) sets sunsky_id from Sunsky's raw "id" field, which is Sunsky's
    # own internal record id -- a DIFFERENT value from "itemNo" (the actual
    # SKU/item number). CSV import instead set sunsky_id = the CSV's
    # "Sunsky SKU" column value directly. Same real product, two different
    # sunsky_id values depending on which path created it -- the unique
    # constraint (on sunsky_id) never caught the overlap.
    #
    # Fix: look up by `sku` (item number) first, since that's the one value
    # that's actually consistent across both import paths. If found, update
    # the existing row in place regardless of its sunsky_id. Only create a
    # new row (with sunsky_id = the CSV SKU, as a reasonable placeholder
    # until the background Sunsky enrichment fills in real data) when truly
    # nothing exists for that SKU yet.
    for r in rows:
        name = r["csv_title"] or r["sunsky_sku"]

        existing = (
            await db.execute(select(M.Product).where(M.Product.sku == r["sunsky_sku"]))
        ).scalar_one_or_none()

        if existing:
            existing.name = name
            existing.site_sku = r["site_sku"] or existing.site_sku
            existing.fetch_job_id = job.id
            existing.status = M.ProductStatus.pending
            existing.woo_product_id = None
            existing.error_message = None
            if r["price"] is not None:
                existing.price = r["price"]
            continue

        values: dict = dict(
            sunsky_id=r["sunsky_sku"],
            sku=r["sunsky_sku"],
            name=name,
            site_sku=r["site_sku"] or None,
            status=M.ProductStatus.pending,
            fetch_job_id=job.id,
            raw_data={},
        )
        if r["price"] is not None:
            values["price"] = r["price"]

        conflict_set: dict = {
            "name": name,
            "site_sku": r["site_sku"] or None,
            "fetch_job_id": job.id,
            "status": M.ProductStatus.pending,
            "woo_product_id": None,
            "error_message": None,
        }
        if r["price"] is not None:
            conflict_set["price"] = r["price"]

        stmt = (
            pg_insert(M.Product)
            .values(**values)
            .on_conflict_do_update(
                index_elements=["sunsky_id"],
                set_=conflict_set,
            )
        )
        await db.execute(stmt)

    # ── 3. Upsert csv_mappings (backward compat: generate step uses this lookup)
    skus = [r["sunsky_sku"] for r in rows]
    await db.execute(delete(M.CsvMapping).where(M.CsvMapping.sunsky_sku.in_(skus)))
    for r in rows:
        db.add(M.CsvMapping(
            sunsky_sku=r["sunsky_sku"],
            site_sku=r["site_sku"] or None,
            csv_title=r["csv_title"] or None,
        ))

    await db.commit()

    # Fetch full Sunsky product data (images, category, spec table) in the
    # background for each row, keyed by the "Sunsky SKU" column — makes CSV
    # import behave like a real Sunsky fetch instead of leaving raw_data
    # empty. Doesn't block this response; products are usable immediately
    # with just their CSV-supplied name/SKU/price, and get filled in as
    # this completes (paced to respect Sunsky's rate limit, so a large CSV
    # takes a while in the background rather than blocking the upload).
    asyncio.create_task(_enrich_csv_products_from_sunsky(job.id, [r["sunsky_sku"] for r in rows]))

    return {
        "imported": len(rows),
        "job_id": job.id,
        "errors": errors[:20],
        "preview": rows[:5],
    }


# ---------------------------------------------------------------------------
# GET /api/csv/mappings  — list current mappings (backward compat)
# ---------------------------------------------------------------------------

@router.get("/mappings")
async def list_mappings(db: AsyncSession = Depends(get_db)):
    """List all current CSV mappings (newest first, max 200)."""
    result = await db.execute(
        select(M.CsvMapping).order_by(M.CsvMapping.id.desc()).limit(200)
    )
    rows = result.scalars().all()
    return {
        "count": len(rows),
        "mappings": [
            {
                "id": r.id,
                "sunsky_sku": r.sunsky_sku,
                "site_sku": r.site_sku,
                "csv_title": r.csv_title,
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
            for r in rows
        ],
    }


# ---------------------------------------------------------------------------
# DELETE /api/csv/mappings  — clear all mappings (backward compat)
# ---------------------------------------------------------------------------

@router.delete("/mappings")
async def clear_mappings(db: AsyncSession = Depends(get_db)):
    """Clear all CSV mappings."""
    result = await db.execute(delete(M.CsvMapping))
    await db.commit()
    return {"deleted": result.rowcount}
