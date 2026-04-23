"""
Sunsky Open API client.

Authentication (from official docs):
  1. Collect all request parameters including 'key' (your API key).
  2. Sort them alphabetically by parameter name.
  3. Concatenate the values (keeping whitespace as-is).
  4. Append '@' + your secret.
  5. MD5-hash the resulting string (hex, lowercase).
  6. Send as POST with key= and signature= added to the body.

Base URL: https://www.sunsky-online.com/api
"""

import hashlib
import httpx
from typing import Optional
from config import get_settings

settings = get_settings()

SUNSKY_BASE = settings.sunsky_api_url.rstrip("/")
SUNSKY_CDN  = "https://www.sunsky-online.com"


def _build_signature(params: dict) -> str:
    """Generate Sunsky request signature."""
    sorted_keys = sorted(params.keys())
    value_string = "".join(str(params[k]) for k in sorted_keys)
    raw = value_string + "@" + settings.sunsky_api_secret
    return hashlib.md5(raw.encode("utf-8")).hexdigest()


async def _post(endpoint: str, params: dict) -> dict:
    """
    Make an authenticated POST request to the Sunsky API.
    params should NOT include key or signature yet.
    """
    params["key"] = settings.sunsky_api_key
    params["signature"] = _build_signature(params)

    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(f"{SUNSKY_BASE}/{endpoint}", data=params)
        resp.raise_for_status()
        return resp.json()


def _normalise_images(raw: dict) -> list[str]:
    """
    Extract and normalise image URLs from a raw Sunsky product dict.
    Tries every known field name and ensures all URLs are absolute.
    Returns up to 5 URLs.
    """
    # Try list fields first (most common)
    images: list = []
    for field in ("images", "imageList", "imgs", "picList", "imageUrls", "pics"):
        val = raw.get(field)
        if val:
            images = val if isinstance(val, list) else [val]
            break

    # Fall back to single-image fields
    if not images:
        for field in ("picUrl", "mainImage", "image", "pic", "thumbnail"):
            val = raw.get(field)
            if val:
                images = [val]
                break

    # Normalise each entry to an absolute URL string
    result: list[str] = []
    for img in images:
        if isinstance(img, str):
            url = img.strip()
        elif isinstance(img, dict):
            url = (
                img.get("url") or img.get("src") or
                img.get("pic") or img.get("path") or ""
            ).strip()
        else:
            continue

        if not url:
            continue

        # Make absolute
        if url.startswith("//"):
            url = "https:" + url
        elif url.startswith("/"):
            url = SUNSKY_CDN + url

        if url.startswith("http"):
            result.append(url)

        if len(result) >= 5:
            break

    return result


async def get_categories(parent_id: str = "0") -> list[dict]:
    """
    Fetch child categories of a given parent.
    Endpoint: category/children  param: parentId
    Returns list of {id, name, parentId}.
    """
    try:
        data = await _post("category/children", {"parentId": parent_id})
        categories = data.get("data", data.get("result", data))
        if isinstance(categories, list):
            return [
                {
                    "id": str(c.get("id", c.get("categoryId", ""))),
                    "name": c.get("name", c.get("title", "")),
                    "parent_id": parent_id if parent_id != "0" else None,
                }
                for c in categories
            ]
        return []
    except Exception:
        return _mock_categories()


async def search_products(
    category_id: Optional[str] = None,
    keyword: Optional[str] = None,
    page: int = 1,
    limit: int = 50,
) -> dict:
    """
    Search products.
    Endpoint: product/list
    Returns {"products": [...], "total": int}
    """
    params: dict = {"pageNum": page, "pageSize": limit}
    if category_id:
        params["categoryId"] = category_id
    if keyword:
        params["keyword"] = keyword

    try:
        data = await _post("product/list", params)
        raw_products = data.get("data", data.get("result", []))
        total = data.get("total", len(raw_products))

        if isinstance(raw_products, dict):
            total = raw_products.get("total", total)
            raw_products = raw_products.get("list", raw_products.get("items", []))

        products = [_normalise_product(p) for p in raw_products]
        return {"products": products, "total": total}
    except Exception:
        return {"products": _mock_products(page, limit), "total": limit * 3}


async def get_product_detail(product_id: str) -> Optional[dict]:
    """
    Get full details for a single product.
    Endpoint: product/detail
    """
    try:
        data = await _post("product/detail", {"id": product_id})
        raw = data.get("data", data.get("result", {}))
        return _normalise_product(raw)
    except Exception:
        return None


def _normalise_product(raw: dict) -> dict:
    """Map Sunsky raw product fields to our internal schema."""
    images = _normalise_images(raw)

    # Merge normalised images back into raw_data so the process job
    # can find them via raw_data["images"] without re-parsing.
    merged_raw = {**raw, "images": images}

    return {
        "id": str(raw.get("id", raw.get("itemNo", raw.get("sku", "")))),
        "sku": str(raw.get("itemNo", raw.get("sku", raw.get("id", "")))),
        "name": raw.get("name", raw.get("title", "")),
        "description": raw.get("description", raw.get("desc", "")),
        "price": str(raw.get("price", raw.get("sellPrice", "0.00"))),
        "stock_status": "in_stock" if raw.get("stockNum", raw.get("stock", 1)) else "out_of_stock",
        "category_id": str(raw.get("categoryId", raw.get("catId", ""))),
        "images": images,
        "raw_data": merged_raw,
    }


def _mock_products(page: int, limit: int) -> list[dict]:
    """Return mock products when API is unavailable."""
    adjectives = ["Smart", "Premium", "Ultra", "Pro", "Wireless", "Portable", "Digital", "Mini"]
    nouns = ["Watch", "Speaker", "Earbuds", "Charger", "Stand", "Case", "Light", "Camera"]
    categories = ["electronics", "accessories", "gadgets", "toys", "sports"]

    products = []
    start = (page - 1) * limit
    for i in range(limit):
        idx = start + i
        adj = adjectives[idx % len(adjectives)]
        noun = nouns[(idx // len(adjectives)) % len(nouns)]
        cat = categories[idx % len(categories)]
        sku = f"SK-{1000 + idx:06d}"
        images = [
            f"https://placehold.co/800x800/png?text={noun}+1",
            f"https://placehold.co/800x800/png?text={noun}+2",
        ]
        products.append({
            "id": f"sunsky-{idx + 1}",
            "sku": sku,
            "name": f"{adj} {noun} {idx + 1}",
            "description": (
                f"High-quality {adj.lower()} {noun.lower()} with advanced features. "
                "Perfect for everyday use. 12-month warranty included."
            ),
            "price": f"{5 + (idx * 7.3) % 95:.2f}",
            "stock_status": "in_stock" if idx % 5 != 0 else "out_of_stock",
            "category_id": cat,
            "images": images,
            "raw_data": {"source": "mock", "category": cat, "images": images},
        })
    return products


def _mock_categories() -> list[dict]:
    return [
        {"id": "electronics", "name": "Electronics", "parent_id": None},
        {"id": "accessories", "name": "Accessories", "parent_id": None},
        {"id": "gadgets", "name": "Gadgets", "parent_id": "electronics"},
        {"id": "audio", "name": "Audio", "parent_id": "electronics"},
        {"id": "wearables", "name": "Wearables", "parent_id": "electronics"},
        {"id": "toys", "name": "Toys & Games", "parent_id": None},
        {"id": "mobile", "name": "Mobile Accessories", "parent_id": "accessories"},
        {"id": "smart-home", "name": "Smart Home", "parent_id": "electronics"},
    ]
