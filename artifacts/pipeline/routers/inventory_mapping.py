"""
Inventory Mapping Config router — /api/stores/{id}/inventory-mapping

Per-store config controlling how Sunsky weight/dimensions are mapped to
WooCommerce product shipping fields.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from models.models import InventoryMappingConfig

router = APIRouter(tags=["inventory-mapping"])


# ─────────────────────────────────────────────────────────────────────────────
# Schemas
# ─────────────────────────────────────────────────────────────────────────────

class InventoryMappingIn(BaseModel):
    weight_unit:    str = "kg"
    dimension_unit: str = "cm"
    weight_null:    str = "leave_blank"   # "leave_blank" | "use_default" | "skip"
    length_null:    str = "leave_blank"
    width_null:     str = "leave_blank"
    height_null:    str = "leave_blank"
    weight_default:    Optional[str] = None
    length_default:    Optional[str] = None
    width_default:     Optional[str] = None
    height_default:    Optional[str] = None


class InventoryMappingOut(BaseModel):
    id:             int
    store_id:       int
    weight_unit:    str
    dimension_unit: str
    weight_null:    str
    length_null:    str
    width_null:     str
    height_null:    str
    weight_default:    Optional[str]
    length_default:    Optional[str]
    width_default:     Optional[str]
    height_default:    Optional[str]
    updated_at:     str

    @classmethod
    def from_orm(cls, c: InventoryMappingConfig) -> "InventoryMappingOut":
        return cls(
            id=c.id,
            store_id=c.store_id,
            weight_unit=c.weight_unit,
            dimension_unit=c.dimension_unit,
            weight_null=c.weight_null,
            length_null=c.length_null,
            width_null=c.width_null,
            height_null=c.height_null,
            weight_default=c.weight_default,
            length_default=c.length_default,
            width_default=c.width_default,
            height_default=c.height_default,
            updated_at=c.updated_at.isoformat() if c.updated_at else "",
        )


# ─────────────────────────────────────────────────────────────────────────────
# Endpoints
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/stores/{store_id}/inventory-mapping")
async def get_inventory_mapping(store_id: int, db: AsyncSession = Depends(get_db)):
    row = (
        await db.execute(
            select(InventoryMappingConfig).where(InventoryMappingConfig.store_id == store_id)
        )
    ).scalar_one_or_none()

    if not row:
        # Return defaults — not yet saved
        return {
            "id": None,
            "store_id": store_id,
            "weight_unit": "kg",
            "dimension_unit": "cm",
            "weight_null": "leave_blank",
            "length_null": "leave_blank",
            "width_null": "leave_blank",
            "height_null": "leave_blank",
            "weight_default": None,
            "length_default": None,
            "width_default": None,
            "height_default": None,
            "updated_at": None,
        }

    return InventoryMappingOut.from_orm(row)


@router.put("/stores/{store_id}/inventory-mapping")
async def upsert_inventory_mapping(
    store_id: int,
    body: InventoryMappingIn,
    db: AsyncSession = Depends(get_db),
):
    now = datetime.now(timezone.utc)
    stmt = (
        pg_insert(InventoryMappingConfig)
        .values(
            store_id=store_id,
            weight_unit=body.weight_unit,
            dimension_unit=body.dimension_unit,
            weight_null=body.weight_null,
            length_null=body.length_null,
            width_null=body.width_null,
            height_null=body.height_null,
            weight_default=body.weight_default,
            length_default=body.length_default,
            width_default=body.width_default,
            height_default=body.height_default,
            updated_at=now,
        )
        .on_conflict_do_update(
            index_elements=["store_id"],
            set_={
                "weight_unit":    body.weight_unit,
                "dimension_unit": body.dimension_unit,
                "weight_null":    body.weight_null,
                "length_null":    body.length_null,
                "width_null":     body.width_null,
                "height_null":    body.height_null,
                "weight_default":    body.weight_default,
                "length_default":    body.length_default,
                "width_default":     body.width_default,
                "height_default":    body.height_default,
                "updated_at":     now,
            },
        )
        .returning(InventoryMappingConfig.id)
    )
    result = await db.execute(stmt)
    new_id = result.scalar_one()
    await db.commit()

    row = await db.get(InventoryMappingConfig, new_id)
    return InventoryMappingOut.from_orm(row)
