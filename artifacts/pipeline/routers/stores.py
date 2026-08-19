from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, delete
from database import get_db
from models.models import Store, WooCategory, StoreStatus, WooAttribute, WooAttributeTerm
from schemas.schemas import StoreCreate, StoreUpdate, StoreOut, WooCategoryOut
from pipeline import woo_client
from datetime import datetime, timezone
import httpx
import re

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


@router.post("/{store_id}/categories/translate")
async def translate_store_categories(store_id: int, db: AsyncSession = Depends(get_db)):
    """Populate name_en for any of this store's categories that don't have
    one yet, using whichever AI provider is already configured (reuses the
    same setup as Content Generation -- no separate translation API/key
    needed). Client feedback item #8: an English-speaking operator mapping
    Bulgarian WooCommerce categories has no way to know what a Bulgarian
    category name means without this hint.
    """
    import json as _json
    from pipeline.ai_generator import generate_with_ai, get_provider_status, AIGenerationError

    rows = (
        await db.execute(
            select(WooCategory).where(WooCategory.store_id == store_id, WooCategory.name_en.is_(None))
        )
    ).scalars().all()
    if not rows:
        return {"translated": 0, "message": "Nothing to translate — all categories already have an English name cached."}

    status = get_provider_status()
    provider = next((p for p in ("openai", "anthropic", "gemini") if status.get(p, {}).get("configured")), None)
    if not provider:
        raise HTTPException(400, "No AI provider is configured (Settings → AI Provider Keys) — translation needs one, same as Content Generation.")

    # One batched call for every untranslated name, rather than one call per
    # category -- much cheaper and faster for a store with many categories.
    names = [r.name for r in rows]
    prompt = (
        "Translate each of the following e-commerce category names into English. "
        "If a name is already in English, return it unchanged. "
        "Keep translations short and natural, matching normal category-name style "
        "(e.g. Title Case, no trailing punctuation). "
        "Return ONLY a JSON object mapping each ORIGINAL name to its English translation, "
        "with no explanation, no markdown, no code fences.\n\n"
        + _json.dumps(names, ensure_ascii=False)
    )
    try:
        raw = await generate_with_ai(
            field="category_translation", product={}, provider=provider, model=None,
            options={"_prompt_override": prompt},
        )
    except AIGenerationError as e:
        raise HTTPException(502, f"Translation failed: {e}")

    cleaned = raw.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", cleaned.strip())
    try:
        translations: dict = _json.loads(cleaned)
    except Exception:
        raise HTTPException(502, f"Translation response wasn't valid JSON: {raw[:200]}")

    translated_count = 0
    for r in rows:
        en = translations.get(r.name)
        if en and isinstance(en, str) and en.strip():
            r.name_en = en.strip()
            translated_count += 1
    await db.commit()

    return {"translated": translated_count, "requested": len(rows)}


@router.post("/{store_id}/categories")
async def sync_store_categories(store_id: int, db: AsyncSession = Depends(get_db)):
    store = await db.get(Store, store_id)
    if not store:
        raise HTTPException(404, "Store not found")
    try:
        raw_cats = await woo_client.get_categories(store)
    except Exception as e:
        raise HTTPException(502, f"Failed to fetch categories from WooCommerce: {e}")

    # Preserve cached English translations (name_en) across this delete-and-
    # recreate cycle, keyed by the stable woo_id -- otherwise every re-sync
    # would silently wipe them, forcing a re-translate (and re-spending AI
    # calls) on every single "Sync Categories" click.
    existing = (
        await db.execute(select(WooCategory).where(WooCategory.store_id == store_id))
    ).scalars().all()
    name_en_by_woo_id = {c.woo_id: (c.name, c.name_en) for c in existing if c.name_en}

    await db.execute(delete(WooCategory).where(WooCategory.store_id == store_id))

    for c in raw_cats:
        prev = name_en_by_woo_id.get(c["id"])
        # Only reuse the cached translation if the underlying name hasn't
        # changed since it was translated -- a renamed category should be
        # retranslated, not keep a stale translation of its old text.
        carried_name_en = prev[1] if prev and prev[0] == c["name"] else None
        cat = WooCategory(
            store_id=store_id,
            woo_id=c["id"],
            name=c["name"],
            name_en=carried_name_en,
            slug=c["slug"],
            parent_id=c.get("parent") or None,
            count=c.get("count", 0),
        )
        db.add(cat)

    await db.commit()
    return {"synced": len(raw_cats)}


from pydantic import BaseModel as _BaseModel

class NewCategoryRequest(_BaseModel):
    name: str
    parent_woo_id: int = 0


@router.post("/{store_id}/categories/new")
async def create_store_category(store_id: int, body: NewCategoryRequest, db: AsyncSession = Depends(get_db)):
    """Create a new WooCommerce category and save it locally."""
    store = await db.get(Store, store_id)
    if not store:
        raise HTTPException(404, "Store not found")
    try:
        created = await woo_client.create_woo_category(store, body.name, body.parent_woo_id)
    except Exception as e:
        raise HTTPException(502, f"WooCommerce category creation failed: {e}")

    cat = WooCategory(
        store_id=store_id,
        woo_id=created["id"],
        name=created["name"],
        slug=created.get("slug", ""),
        parent_id=created.get("parent") or None,
        count=0,
    )
    db.add(cat)
    await db.commit()
    await db.refresh(cat)
    # NOTE: "id" here MUST be the real WooCommerce category ID (cat.woo_id),
    # not the local DB primary key (cat.id) — this matches the convention
    # used by GET /pipelines/{id}/map-data's woo_options, which the frontend
    # treats as interchangeable with this endpoint's response. Returning
    # cat.id previously caused newly-created categories to save the local
    # DB row number into SunskyCategoryMapping.woo_cat_id, which WooCommerce
    # silently rejects as an unrecognized category ID at Upload/Sync —
    # products uploaded with no category applied.
    return {"id": cat.woo_id, "db_id": cat.id, "woo_id": cat.woo_id, "name": cat.name, "slug": cat.slug}


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
