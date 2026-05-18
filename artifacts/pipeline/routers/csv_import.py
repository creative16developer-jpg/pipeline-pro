"""
CSV Import router — /api/csv/*

Allows uploading a CSV mapping file before a pipeline run.

CSV Columns (required, case-sensitive):
  Sunsky SKU   — used to match fetched products
  Site SKU     — saved to WooCommerce as the product SKU
  Product Title — replaces the original Sunsky title

Title resolution priority (in generation step):
  1. CSV Import Title
  2. Manual Dashboard Override
  3. Cleaned Sunsky Title
  4. Original Sunsky Title fallback
"""
from __future__ import annotations

import csv
import io

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
import models.models as M

router = APIRouter(prefix="/csv", tags=["csv"])

REQUIRED_COLUMNS = {"Sunsky SKU", "Site SKU", "Product Title"}
MAX_ROWS = 10_000


@router.post("/upload")
async def upload_csv(
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
):
    """
    Upload a CSV mapping file. Upserts into csv_mappings table.
    Returns import count, errors, and a preview of first 5 rows.
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
        site_sku = (row.get("Site SKU") or "").strip()
        csv_title = (row.get("Product Title") or "").strip()

        if not sunsky_sku:
            errors.append(f"Row {i + 2}: missing Sunsky SKU — skipped")
            continue

        rows.append({"sunsky_sku": sunsky_sku, "site_sku": site_sku, "csv_title": csv_title})

    if not rows:
        raise HTTPException(400, f"No valid rows found. Errors: {errors[:5]}")

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
        "errors": errors[:20],
        "preview": rows[:5],
    }


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


@router.delete("/mappings")
async def clear_mappings(db: AsyncSession = Depends(get_db)):
    """Clear all CSV mappings."""
    result = await db.execute(delete(M.CsvMapping))
    await db.commit()
    return {"deleted": result.rowcount}
