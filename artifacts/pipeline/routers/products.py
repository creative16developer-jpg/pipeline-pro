from __future__ import annotations

import json
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from database import get_db
from models.models import Product, ProductStatus
from schemas.schemas import ProductListOut, ProductOut

import math

router = APIRouter(prefix="/products", tags=["products"])


# ─────────────────────────────────────────────────────────────────────────────
# Schemas
# ─────────────────────────────────────────────────────────────────────────────

class WooCatItem(BaseModel):
    id: int
    name: str


class ProductCategoriesUpdate(BaseModel):
    woo_cats: list[WooCatItem] = []
    primary_woo_cat_id: Optional[int] = None


# ─────────────────────────────────────────────────────────────────────────────
# List + Detail
# ─────────────────────────────────────────────────────────────────────────────

@router.get("", response_model=ProductListOut)
async def list_products(
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=200),
    status: str = Query(None),
    search: str = Query(None),
    db: AsyncSession = Depends(get_db),
):
    q = select(Product)
    count_q = select(func.count(Product.id))

    if status:
        try:
            s = ProductStatus(status)
            q = q.where(Product.status == s)
            count_q = count_q.where(Product.status == s)
        except ValueError:
            pass

    if search:
        term = f"%{search}%"
        filter_clause = or_(Product.name.ilike(term), Product.sku.ilike(term))
        q = q.where(filter_clause)
        count_q = count_q.where(filter_clause)

    total = (await db.execute(count_q)).scalar_one()
    q = q.order_by(Product.created_at.desc()).offset((page - 1) * limit).limit(limit)
    products = (await db.execute(q)).scalars().all()

    return ProductListOut(
        products=[ProductOut.model_validate(p) for p in products],
        total=total,
        page=page,
        limit=limit,
        total_pages=max(1, math.ceil(total / limit)),
    )


@router.get("/{product_id}", response_model=ProductOut)
async def get_product(product_id: int, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(Product)
        .where(Product.id == product_id)
        .options(selectinload(Product.fetch_job))
    )
    product = result.scalar_one_or_none()
    if not product:
        raise HTTPException(404, "Product not found")
    out = ProductOut.model_validate(product)
    if product.fetch_job and product.fetch_job.store_id:
        out.store_id = product.fetch_job.store_id
    return out


# ─────────────────────────────────────────────────────────────────────────────
# Manual category override
# ─────────────────────────────────────────────────────────────────────────────

@router.patch("/{product_id}/categories")
async def update_product_categories(
    product_id: int,
    body: ProductCategoriesUpdate,
    db: AsyncSession = Depends(get_db),
):
    """
    Set a manual WooCommerce category override on a product.
    Sets cat_source='manual' — upload phase will always use this override
    instead of the store-wide SunskyCategoryMapping rule.
    """
    product = await db.get(Product, product_id)
    if not product:
        raise HTTPException(404, "Product not found")

    product.manual_woo_cats_json = json.dumps([{"id": c.id, "name": c.name} for c in body.woo_cats])
    product.manual_primary_woo_cat_id = body.primary_woo_cat_id or (body.woo_cats[0].id if body.woo_cats else None)
    product.cat_source = "manual"
    await db.commit()
    await db.refresh(product)

    out = ProductOut.model_validate(product)
    return out


@router.delete("/{product_id}/categories/override")
async def clear_product_category_override(
    product_id: int,
    db: AsyncSession = Depends(get_db),
):
    """Remove manual category override — product returns to auto-mapping."""
    product = await db.get(Product, product_id)
    if not product:
        raise HTTPException(404, "Product not found")
    product.manual_woo_cats_json = None
    product.manual_primary_woo_cat_id = None
    product.cat_source = "auto"
    await db.commit()
    return {"ok": True}
