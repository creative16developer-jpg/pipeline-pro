"""
Attribute Profiles router — /api/attr-profiles

CRUD for AttributeProfile + ProfileAttribute rows.
Each profile is a named collection of WooCommerce attribute names that
should be extracted/expected for a given product category.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from database import get_db
from models.models import AttributeProfile, ProfileAttribute

router = APIRouter(tags=["attr-profiles"])


# ─────────────────────────────────────────────────────────────────────────────
# Schemas
# ─────────────────────────────────────────────────────────────────────────────

class ProfileAttrIn(BaseModel):
    woo_attr_name: str
    required:      bool = True
    sort_order:    int = 0


class ProfileAttrOut(BaseModel):
    id:            int
    woo_attr_name: str
    required:      bool
    sort_order:    int


class ProfileIn(BaseModel):
    name:        str
    description: Optional[str] = None
    attributes:  list[ProfileAttrIn] = []


class ProfileOut(BaseModel):
    id:          int
    name:        str
    description: Optional[str]
    attributes:  list[ProfileAttrOut]
    created_at:  str
    updated_at:  str

    @classmethod
    def from_orm(cls, p: AttributeProfile) -> "ProfileOut":
        return cls(
            id=p.id,
            name=p.name,
            description=p.description,
            attributes=[
                ProfileAttrOut(
                    id=a.id,
                    woo_attr_name=a.woo_attr_name,
                    required=a.required,
                    sort_order=a.sort_order,
                )
                for a in (p.attributes or [])
            ],
            created_at=p.created_at.isoformat() if p.created_at else "",
            updated_at=p.updated_at.isoformat() if p.updated_at else "",
        )


# ─────────────────────────────────────────────────────────────────────────────
# Endpoints
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/attr-profiles")
async def list_profiles(db: AsyncSession = Depends(get_db)):
    rows = (
        await db.execute(
            select(AttributeProfile)
            .options(selectinload(AttributeProfile.attributes))
            .order_by(AttributeProfile.name)
        )
    ).scalars().all()
    return {"profiles": [ProfileOut.from_orm(p) for p in rows]}


@router.get("/attr-profiles/{profile_id}")
async def get_profile(profile_id: int, db: AsyncSession = Depends(get_db)):
    p = (
        await db.execute(
            select(AttributeProfile)
            .where(AttributeProfile.id == profile_id)
            .options(selectinload(AttributeProfile.attributes))
        )
    ).scalar_one_or_none()
    if not p:
        raise HTTPException(404, "Profile not found")
    return ProfileOut.from_orm(p)


@router.post("/attr-profiles", status_code=201)
async def create_profile(body: ProfileIn, db: AsyncSession = Depends(get_db)):
    clash = (
        await db.execute(select(AttributeProfile).where(AttributeProfile.name == body.name.strip()))
    ).scalar_one_or_none()
    if clash:
        raise HTTPException(400, f"Profile '{body.name}' already exists")

    profile = AttributeProfile(name=body.name.strip(), description=body.description)
    db.add(profile)
    await db.flush()  # get ID before adding children

    for i, attr in enumerate(body.attributes):
        db.add(ProfileAttribute(
            profile_id=profile.id,
            woo_attr_name=attr.woo_attr_name.strip(),
            required=attr.required,
            sort_order=attr.sort_order if attr.sort_order else i,
        ))

    await db.commit()
    # Reload with children
    p = (
        await db.execute(
            select(AttributeProfile)
            .where(AttributeProfile.id == profile.id)
            .options(selectinload(AttributeProfile.attributes))
        )
    ).scalar_one()
    return ProfileOut.from_orm(p)


@router.put("/attr-profiles/{profile_id}")
async def update_profile(profile_id: int, body: ProfileIn, db: AsyncSession = Depends(get_db)):
    p = (
        await db.execute(
            select(AttributeProfile)
            .where(AttributeProfile.id == profile_id)
            .options(selectinload(AttributeProfile.attributes))
        )
    ).scalar_one_or_none()
    if not p:
        raise HTTPException(404, "Profile not found")

    if p.name != body.name.strip():
        clash = (
            await db.execute(select(AttributeProfile).where(AttributeProfile.name == body.name.strip()))
        ).scalar_one_or_none()
        if clash:
            raise HTTPException(400, f"Profile '{body.name}' already exists")

    p.name        = body.name.strip()
    p.description = body.description
    p.updated_at  = datetime.now(timezone.utc)

    # Replace all attributes: delete existing, re-add
    for existing_attr in list(p.attributes):
        await db.delete(existing_attr)
    await db.flush()

    for i, attr in enumerate(body.attributes):
        db.add(ProfileAttribute(
            profile_id=p.id,
            woo_attr_name=attr.woo_attr_name.strip(),
            required=attr.required,
            sort_order=attr.sort_order if attr.sort_order else i,
        ))

    await db.commit()
    p2 = (
        await db.execute(
            select(AttributeProfile)
            .where(AttributeProfile.id == profile_id)
            .options(selectinload(AttributeProfile.attributes))
        )
    ).scalar_one()
    return ProfileOut.from_orm(p2)


@router.delete("/attr-profiles/{profile_id}", status_code=204)
async def delete_profile(profile_id: int, db: AsyncSession = Depends(get_db)):
    p = await db.get(AttributeProfile, profile_id)
    if not p:
        raise HTTPException(404, "Profile not found")
    await db.delete(p)
    await db.commit()
