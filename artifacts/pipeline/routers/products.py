from __future__ import annotations

import json
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from database import get_db
from models.models import Product, ProductStatus, Image
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


class ProductFieldsUpdate(BaseModel):
    name: Optional[str] = None
    site_sku: Optional[str] = None
    price: Optional[str] = None
    sale_price: Optional[str] = None
    stock_quantity: Optional[int] = None
    description: Optional[str] = None
    short_description: Optional[str] = None
    slug: Optional[str] = None
    meta_title: Optional[str] = None
    meta_description: Optional[str] = None
    focus_keyword: Optional[str] = None
    tags: Optional[str] = None


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

    # Resolve category_id -> human-readable name once for this whole page,
    # not per product. Products only ever store the raw numeric Sunsky
    # category id (confirmed: real Sunsky data has no name field at all) --
    # without this, the Category column showed a bare number at best, or
    # nothing for older rows created before category_id was reliably set.
    from services.enrich_service import get_effective_category_name_map
    category_name_map = await get_effective_category_name_map(db)

    out_products = []
    for p in products:
        po = ProductOut.model_validate(p)
        if p.category_id:
            po.category_name = category_name_map.get(str(p.category_id))
        out_products.append(po)

    return ProductListOut(
        products=out_products,
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
    if product.category_id:
        from services.enrich_service import get_effective_category_name_map
        category_name_map = await get_effective_category_name_map(db)
        out.category_name = category_name_map.get(str(product.category_id))
    return out


@router.patch("/{product_id}/fields")
async def update_product_fields(
    product_id: int,
    body: ProductFieldsUpdate,
    db: AsyncSession = Depends(get_db),
):
    """Save inline edits from the Content Review screen. Client feedback
    item #8 (Baselinker reference video): "options available for manual
    editing: quantity, sales and regular prices, woo sku and to be able
    to edit all details."

    Partial update -- only fields actually present in the request body
    are changed (model_fields_set, not just non-None, so a deliberate
    clear-to-empty-string is distinguished from "field not sent").
    """
    product = await db.get(Product, product_id)
    if not product:
        raise HTTPException(404, "Product not found")

    for field in body.model_fields_set:
        setattr(product, field, getattr(body, field))

    await db.commit()
    await db.refresh(product)

    out = ProductOut.model_validate(product)
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


# ─────────────────────────────────────────────────────────────────────────────
# Per-image editing (client feedback item #8 -- last piece of the Review-
# screen overhaul: exclude or reorder individual images in a product's
# gallery from Content Review).
# ─────────────────────────────────────────────────────────────────────────────

@router.delete("/{product_id}/images/{image_id}")
async def delete_product_image(product_id: int, image_id: int, db: AsyncSession = Depends(get_db)):
    """Remove a single image from a product's gallery. A hard delete
    (not a status flag) -- images are re-created fresh on every Process
    run anyway (see patch 42's fix for the duplicate-image bug), so
    there's no meaningful "excluded but still present" state to track
    between runs; the row simply won't exist until the next Process run
    recreates the full set.
    """
    img = await db.get(Image, image_id)
    if not img or img.product_id != product_id:
        raise HTTPException(404, "Image not found on this product")
    await db.delete(img)
    await db.commit()
    return {"ok": True}


class ImageReorderRequest(BaseModel):
    image_ids: list[int]  # full ordered list of this product's image IDs


@router.post("/{product_id}/images/reorder")
async def reorder_product_images(
    product_id: int,
    body: ImageReorderRequest,
    db: AsyncSession = Depends(get_db),
):
    """Set gallery order from a full ordered list of image IDs.
    position=0 also becomes the new is_main (first image is the product's
    primary/featured image in WooCommerce)."""
    rows = (
        await db.execute(select(Image).where(Image.product_id == product_id))
    ).scalars().all()
    by_id = {r.id: r for r in rows}

    missing = [iid for iid in body.image_ids if iid not in by_id]
    if missing:
        raise HTTPException(400, f"Image ID(s) not found on this product: {missing}")

    for position, image_id in enumerate(body.image_ids):
        img = by_id[image_id]
        img.position = position
        img.is_main = (position == 0)

    await db.commit()
    return {"ok": True}
