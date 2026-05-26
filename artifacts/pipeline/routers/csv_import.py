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

        if not sunsky_sku:
            errors.append(f"Row {i + 2}: missing Sunsky SKU — skipped")
            continue

        rows.append({"sunsky_sku": sunsky_sku, "site_sku": site_sku, "csv_title": csv_title})

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

    # ── 2. Upsert Products (sunsky_id = sunsky_sku as unique key)
    #       ON CONFLICT: update name/site_sku/fetch_job_id so the latest CSV
    #       always wins; reset status→pending so the pipeline re-processes.
    for r in rows:
        name = r["csv_title"] or r["sunsky_sku"]
        stmt = (
            pg_insert(M.Product)
            .values(
                sunsky_id=r["sunsky_sku"],
                sku=r["sunsky_sku"],
                name=name,
                site_sku=r["site_sku"] or None,
                status=M.ProductStatus.pending,
                fetch_job_id=job.id,
                raw_data={},
            )
            .on_conflict_do_update(
                index_elements=["sunsky_id"],
                set_={
                    "name": name,
                    "site_sku": r["site_sku"] or None,
                    "fetch_job_id": job.id,
                    "status": M.ProductStatus.pending,
                    "woo_product_id": None,
                    "error_message": None,
                },
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
