from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, delete
from database import get_db
from models.models import Store, WooCategory, StoreStatus, WooAttribute, WooAttributeTerm
from schemas.schemas import StoreCreate, StoreUpdate, StoreOut, WooCategoryOut
from pipeline import woo_client
from datetime import datetime, timezone
import httpx

router = APIRouter(prefix="/stores", tags=["stores"])


@router.get("", response_model=list[StoreOut])
async def list_stores(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Store).order_by(Store.created_at.desc()))
    stores = result.scalars().all()
    return [StoreOut.from_orm_masked(s) for s in stores]


@router.post("", response_model=StoreOut)
async def create_store(body: StoreCreate, db: AsyncSession = Depends(get_db)):
    store = Store(**body.model_dump())
    db.add(store)
    await db.commit()
    await db.refresh(store)
    return StoreOut.from_orm_masked(store)


@router.get("/{store_id}", response_model=StoreOut)
async def get_store(store_id: int, db: AsyncSession = Depends(get_db)):
    store = await db.get(Store, store_id)
    if not store:
        raise HTTPException(404, "Store not found")
    return StoreOut.from_orm_masked(store)


@router.put("/{store_id}", response_model=StoreOut)
async def update_store(store_id: int, body: StoreUpdate, db: AsyncSession = Depends(get_db)):
    store = await db.get(Store, store_id)
    if not store:
        raise HTTPException(404, "Store not found")
    for field, value in body.model_dump(exclude_none=True).items():
        setattr(store, field, value)
    store.updated_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(store)
    return StoreOut.from_orm_masked(store)


@router.delete("/{store_id}")
async def delete_store(store_id: int, db: AsyncSession = Depends(get_db)):
    store = await db.get(Store, store_id)
    if not store:
        raise HTTPException(404, "Store not found")
    await db.delete(store)
    await db.commit()
    return {"success": True}


@router.post("/{store_id}/test")
async def test_store_connection(store_id: int, db: AsyncSession = Depends(get_db)):
    store = await db.get(Store, store_id)
    if not store:
        raise HTTPException(404, "Store not found")
    result = await woo_client.test_connection(store)
    if result["success"]:
        store.status = StoreStatus.active
        store.last_tested_at = datetime.now(timezone.utc)
    else:
        store.status = StoreStatus.error
    await db.commit()
    return result


@router.get("/{store_id}/categories", response_model=list[WooCategoryOut])
async def list_store_categories(store_id: int, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(WooCategory).where(WooCategory.store_id == store_id).order_by(WooCategory.name)
    )
    return result.scalars().all()


@router.post("/{store_id}/categories")
async def sync_store_categories(store_id: int, db: AsyncSession = Depends(get_db)):
    store = await db.get(Store, store_id)
    if not store:
        raise HTTPException(404, "Store not found")
    try:
        raw_cats = await woo_client.get_categories(store)
    except Exception as e:
        raise HTTPException(502, f"Failed to fetch categories from WooCommerce: {e}")

    await db.execute(delete(WooCategory).where(WooCategory.store_id == store_id))

    for c in raw_cats:
        cat = WooCategory(
            store_id=store_id,
            woo_id=c["id"],
            name=c["name"],
            slug=c["slug"],
            parent_id=c.get("parent") or None,
            count=c.get("count", 0),
        )
        db.add(cat)

    await db.commit()
    return {"synced": len(raw_cats)}


@router.get("/{store_id}/woo-attributes")
async def list_store_attributes(store_id: int, db: AsyncSession = Depends(get_db)):
    """Return synced WooCommerce product attributes with their terms."""
    attrs = (
        await db.execute(
            select(WooAttribute)
            .where(WooAttribute.store_id == store_id)
            .order_by(WooAttribute.name)
        )
    ).scalars().all()
    return [
        {
            "id": a.id,
            "woo_id": a.woo_id,
            "name": a.name,
            "slug": a.slug,
            "terms": [
                {"id": t.id, "woo_id": t.woo_id, "name": t.name, "slug": t.slug}
                for t in (a.terms or [])
            ],
        }
        for a in attrs
    ]


@router.post("/{store_id}/woo-attributes/sync")
async def sync_store_attributes(store_id: int, db: AsyncSession = Depends(get_db)):
    """Sync product attributes and their terms from WooCommerce."""
    store = await db.get(Store, store_id)
    if not store:
        raise HTTPException(404, "Store not found")

    try:
        raw_attrs = await woo_client.get_product_attributes(store)
    except Exception as e:
        raise HTTPException(502, f"Failed to fetch attributes from WooCommerce: {e}")

    # Replace all attributes for this store
    await db.execute(delete(WooAttribute).where(WooAttribute.store_id == store_id))
    await db.flush()

    synced_terms = 0
    for a in raw_attrs:
        attr_obj = WooAttribute(
            store_id=store_id,
            woo_id=a["id"],
            name=a["name"],
            slug=a.get("slug", ""),
        )
        db.add(attr_obj)
        await db.flush()

        try:
            terms = await woo_client.get_attribute_terms(store, a["id"])
        except Exception:
            terms = []

        for t in terms:
            db.add(WooAttributeTerm(
                attribute_id=attr_obj.id,
                store_id=store_id,
                woo_id=t["id"],
                name=t["name"],
                slug=t.get("slug", ""),
            ))
            synced_terms += 1

    await db.commit()
    return {"synced_attributes": len(raw_attrs), "synced_terms": synced_terms}


class PullWooCommerceRequest(BaseModel):
    pull_categories: bool = True
    pull_attributes: bool = True


@router.post("/{store_id}/pull")
async def pull_from_woocommerce(
    store_id: int,
    body: PullWooCommerceRequest,
    db: AsyncSession = Depends(get_db),
):
    """
    Pull categories and/or product attributes from a WooCommerce store into
    PipelinePro.  Replaces all previously-pulled data for this store.
    """
    store = await db.get(Store, store_id)
    if not store:
        raise HTTPException(404, "Store not found")

    result: dict = {}

    if body.pull_categories:
        try:
            raw_cats = await woo_client.get_categories(store)
        except Exception as exc:
            raise HTTPException(502, f"Failed to fetch categories from WooCommerce: {exc}")

        await db.execute(delete(WooCategory).where(WooCategory.store_id == store_id))
        for c in raw_cats:
            db.add(WooCategory(
                store_id=store_id,
                woo_id=c["id"],
                name=c["name"],
                slug=c["slug"],
                parent_id=c.get("parent") or None,
                count=c.get("count", 0),
            ))
        result["synced_categories"] = len(raw_cats)

    if body.pull_attributes:
        try:
            raw_attrs = await woo_client.get_product_attributes(store)
        except Exception as exc:
            raise HTTPException(502, f"Failed to fetch attributes from WooCommerce: {exc}")

        await db.execute(delete(WooAttribute).where(WooAttribute.store_id == store_id))
        await db.flush()

        synced_terms = 0
        for a in raw_attrs:
            attr_obj = WooAttribute(
                store_id=store_id,
                woo_id=a["id"],
                name=a["name"],
                slug=a.get("slug", ""),
            )
            db.add(attr_obj)
            await db.flush()

            try:
                terms = await woo_client.get_attribute_terms(store, a["id"])
            except Exception:
                terms = []

            for t in terms:
                db.add(WooAttributeTerm(
                    attribute_id=attr_obj.id,
                    store_id=store_id,
                    woo_id=t["id"],
                    name=t["name"],
                    slug=t.get("slug", ""),
                ))
                synced_terms += 1

        result["synced_attributes"] = len(raw_attrs)
        result["synced_terms"] = synced_terms

    await db.commit()
    return result


@router.post("/{store_id}/test-product")
async def test_product_creation(store_id: int, db: AsyncSession = Depends(get_db)):
    """
    Send a minimal draft product to WooCommerce and return the full raw response.
    Use this to diagnose 400 errors — the response body shows the exact reason.
    """
    store = await db.get(Store, store_id)
    if not store:
        raise HTTPException(404, "Store not found")

    base_url = store.url.rstrip("/") + "/wp-json/wc/v3"
    import base64
    token = base64.b64encode(
        f"{store.consumer_key}:{store.consumer_secret}".encode()
    ).decode()
    headers = {"Authorization": f"Basic {token}"}

    payload = {
        "name": "PipelinePro Test Product",
        "status": "draft",
        "regular_price": "9.99",
        "description": "Diagnostic test — safe to delete.",
    }

    async with httpx.AsyncClient(timeout=30.0, verify=False) as client:
        resp = await client.post(
            f"{base_url}/products",
            headers=headers,
            json=payload,
        )
        return {
            "status_code": resp.status_code,
            "success": resp.is_success,
            "body": resp.json() if resp.headers.get("content-type", "").startswith("application/json") else resp.text,
        }
