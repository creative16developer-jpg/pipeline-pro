"""
Inventory Mapping Config router — /api/stores/{id}/inventory-mapping,
/api/inventory-mapping/global

Config controlling how Sunsky weight/dimensions are mapped to
WooCommerce product shipping fields. Client feedback: "global and per
store both rules options are there, so if client want to do per store
or global that is his choice." store_id=NULL is the global default,
used by any store with no config of its own (see job_tasks.py's
upload logic for where this fallback is actually applied); a specific
store_id overrides it for just that store.
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

_DEFAULTS = dict(
    weight_unit="kg", dimension_unit="cm",
    weight_null="leave_blank", length_null="leave_blank",
    width_null="leave_blank", height_null="leave_blank",
    weight_default=None, length_default=None, width_default=None, height_default=None,
)


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
    id:             Optional[int]
    store_id:       Optional[int]
    # True when this response is the store's OWN saved config; False
    # when it's showing the global default because this store has no
    # config of its own yet (matches the exact fallback job_tasks.py's
    # upload logic actually applies) -- lets the frontend show "using
    # global default" instead of silently implying a store-specific
    # config exists when it doesn't.
    is_own_config:  bool
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
    updated_at:     Optional[str]

    @classmethod
    def from_orm(cls, c: Optional[InventoryMappingConfig], *, requested_store_id: Optional[int]) -> "InventoryMappingOut":
        if c is None:
            return cls(id=None, store_id=requested_store_id, is_own_config=False, updated_at=None, **_DEFAULTS)
        return cls(
            id=c.id,
            store_id=requested_store_id,
            is_own_config=(c.store_id == requested_store_id),
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
# Per-store endpoints
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/stores/{store_id}/inventory-mapping")
async def get_inventory_mapping(store_id: int, db: AsyncSession = Depends(get_db)):
    row = (
        await db.execute(
            select(InventoryMappingConfig).where(InventoryMappingConfig.store_id == store_id)
        )
    ).scalar_one_or_none()

    if row is None:
        # No config of its own -- fall back to the global default, if
        # one exists, so the UI can show what will actually be used
        # (matching job_tasks.py's real upload-time fallback) rather
        # than just always showing hardcoded factory defaults.
        row = (
            await db.execute(
                select(InventoryMappingConfig).where(InventoryMappingConfig.store_id.is_(None))
            )
        ).scalar_one_or_none()

    return InventoryMappingOut.from_orm(row, requested_store_id=store_id)


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
            # Client feedback confirmed live (multi-store test): the old
            # plain UNIQUE(store_id) index this originally targeted was
            # replaced by two separate PARTIAL indexes (see the
            # migration) so a global NULL row and per-store rows can
            # coexist correctly. Postgres's ON CONFLICT conflict-target
            # inference needs index_where to match a partial index --
            # confirmed by testing directly that omitting it fails to
            # match, while including it (matching the partial index's
            # own WHERE clause exactly) works correctly and performs a
            # genuine UPDATE, not a duplicate insert.
            index_elements=["store_id"],
            index_where=InventoryMappingConfig.store_id.is_not(None),
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
    return InventoryMappingOut.from_orm(row, requested_store_id=store_id)


# ─────────────────────────────────────────────────────────────────────────────
# Global default endpoints
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/inventory-mapping/global")
async def get_global_inventory_mapping(db: AsyncSession = Depends(get_db)):
    row = (
        await db.execute(select(InventoryMappingConfig).where(InventoryMappingConfig.store_id.is_(None)))
    ).scalar_one_or_none()
    return InventoryMappingOut.from_orm(row, requested_store_id=None)


@router.put("/inventory-mapping/global")
async def upsert_global_inventory_mapping(body: InventoryMappingIn, db: AsyncSession = Depends(get_db)):
    # The global row's uniqueness is enforced by a partial index on a
    # constant expression (WHERE store_id IS NULL), not a real column --
    # ON CONFLICT's index_elements mechanism expects genuine column
    # references, not arbitrary expressions, so a plain
    # check-existing-then-update-or-insert is used here instead of
    # ON CONFLICT. There's only ever at most one such row, so there's
    # no meaningful concurrency concern this simpler approach loses out
    # on for a low-traffic settings write like this one.
    now = datetime.now(timezone.utc)
    row = (
        await db.execute(select(InventoryMappingConfig).where(InventoryMappingConfig.store_id.is_(None)))
    ).scalar_one_or_none()

    if row is None:
        row = InventoryMappingConfig(store_id=None)
        db.add(row)

    row.weight_unit = body.weight_unit
    row.dimension_unit = body.dimension_unit
    row.weight_null = body.weight_null
    row.length_null = body.length_null
    row.width_null = body.width_null
    row.height_null = body.height_null
    row.weight_default = body.weight_default
    row.length_default = body.length_default
    row.width_default = body.width_default
    row.height_default = body.height_default
    row.updated_at = now

    await db.commit()
    await db.refresh(row)
    return InventoryMappingOut.from_orm(row, requested_store_id=None)
