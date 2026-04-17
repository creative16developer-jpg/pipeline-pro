from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, or_
from database import get_db
from models.models import Product, ProductStatus
from schemas.schemas import ProductOut, ProductListOut
import math

router = APIRouter(prefix="/products", tags=["products"])


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
    product = await db.get(Product, product_id)
    if not product:
        raise HTTPException(404, "Product not found")
    return ProductOut.model_validate(product)
