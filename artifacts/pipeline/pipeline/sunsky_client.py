"""
Sunsky Open API client.

Authentication (official docs):
  1. Collect all request parameters including 'key' (your API key).
  2. Sort them alphabetically by parameter name.
  3. Concatenate the values (keeping whitespace as-is).
  4. Append '@' + your secret.
  5. MD5-hash the resulting string (hex, lowercase).
  6. Send as POST with key= and signature= added to the body.

Base URL: https://www.sunsky-online.com/api
"""

import hashlib
import asyncio
import httpx
from typing import Optional
from config import get_settings

settings = get_settings()

SUNSKY_BASE = settings.sunsky_api_url.rstrip("/")
SUNSKY_CDN  = "https://www.sunsky-online.com"

# How many times to retry a failed API call before giving up
MAX_RETRIES = 3
RETRY_DELAY = 2.0  # seconds between retries


def _build_signature(params: dict) -> str:
    """Generate Sunsky request signature."""
    sorted_keys = sorted(params.keys())
    value_string = "".join(str(params[k]) for k in sorted_keys)
    raw = value_string + "@" + settings.sunsky_api_secret
    return hashlib.md5(raw.encode("utf-8")).hexdigest()


async def _post(endpoint: str, params: dict) -> dict:
    """
    Make an authenticated POST request to the Sunsky API.
    Retries up to MAX_RETRIES times on network/server errors.
    Raises on HTTP errors (including 403 bad credentials).
    """
    params = dict(params)
    params["key"] = settings.sunsky_api_key
    params["signature"] = _build_signature(params)

    last_error: Exception = RuntimeError("No attempts made")
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.post(
                    f"{SUNSKY_BASE}/{endpoint}",
                    data=params,
                )
                resp.raise_for_status()
                data = resp.json()
                # Sunsky wraps errors in a 200 response with {"code": <non-0>}
                code = data.get("code", 0)
                if code not in (0, 200, None):
                    msg = data.get("message", data.get("msg", str(data)))
                    raise ValueError(f"Sunsky API error (code={code}): {msg}")
                return data
        except (httpx.TimeoutException, httpx.NetworkError) as exc:
            last_error = exc
            if attempt < MAX_RETRIES:
                await asyncio.sleep(RETRY_DELAY * attempt)
            continue
        except Exception as exc:
            raise  # Non-retryable: auth errors, bad params, etc.

    raise last_error


def _normalise_images(raw: dict) -> list[str]:
    """
    Extract and normalise image URLs from a raw Sunsky product dict.
    Tries every known field name and ensures all URLs are absolute.
    Returns up to 5 URLs.
    """
    images: list = []
    for field in ("images", "imageList", "imgs", "picList", "imageUrls", "pics"):
        val = raw.get(field)
        if val:
            images = val if isinstance(val, list) else [val]
            break

    if not images:
        for field in ("picUrl", "mainImage", "image", "pic", "thumbnail"):
            val = raw.get(field)
            if val:
                images = [val]
                break

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
    Returns list of {id, name, parent_id}.
    Raises on API error — no mock fallback.
    """
    data = await _post("category/children", {"parentId": parent_id})
    categories = data.get("data", data.get("result", data))
    if not isinstance(categories, list):
        categories = []
    return [
        {
            "id": str(c.get("id", c.get("categoryId", ""))),
            "name": c.get("name", c.get("title", "")),
            "parent_id": parent_id if parent_id != "0" else None,
        }
        for c in categories
        if c.get("id") or c.get("categoryId")
    ]


async def get_category_tree() -> list[dict]:
    """
    Recursively fetch the full category hierarchy.
    Returns all categories (root + all descendants) in a flat list.
    Each item: {id, name, parent_id}.
    """
    all_cats: list[dict] = []

    async def _recurse(parent_id: str):
        children = await get_categories(parent_id)
        for cat in children:
            all_cats.append(cat)
            # Recurse into children
            await _recurse(cat["id"])

    await _recurse("0")
    return all_cats


async def search_products(
    category_id: Optional[str] = None,
    keyword: Optional[str] = None,
    page: int = 1,
    page_size: int = 50,
) -> dict:
    """
    Search products — single page.
    Returns {"products": [...], "total": int, "pages": int}.
    Raises on API error — no mock fallback.
    """
    params: dict = {"pageNum": page, "pageSize": page_size}
    if category_id:
        params["categoryId"] = category_id
    if keyword:
        params["keyword"] = keyword

    data = await _post("product/list", params)

    raw_products = data.get("data", data.get("result", []))
    total = int(data.get("total", 0))

    if isinstance(raw_products, dict):
        total = int(raw_products.get("total", total))
        raw_products = raw_products.get("list", raw_products.get("items", []))

    if not isinstance(raw_products, list):
        raw_products = []

    products = [_normalise_product(p) for p in raw_products]

    # Calculate total pages
    pages = max(1, (total + page_size - 1) // page_size) if total else 1

    return {"products": products, "total": total, "pages": pages}


async def get_all_products(
    category_id: Optional[str] = None,
    keyword: Optional[str] = None,
    page_size: int = 50,
    max_pages: Optional[int] = None,
    on_page: Optional[object] = None,
) -> list[dict]:
    """
    Fetch ALL products by iterating through all pages.

    Args:
        category_id: Filter by category
        keyword: Search keyword
        page_size: Products per API page (max supported by Sunsky)
        max_pages: Optional cap on pages (for testing / partial sync)
        on_page: Optional async callback(page, products, total) called after each page

    Returns: flat list of all normalised products.
    Raises on API error.
    """
    all_products: list[dict] = []
    page = 1

    while True:
        result = await search_products(
            category_id=category_id,
            keyword=keyword,
            page=page,
            page_size=page_size,
        )
        batch = result["products"]
        total = result["total"]
        total_pages = result["pages"]

        all_products.extend(batch)

        if on_page:
            await on_page(page, batch, total)

        if not batch:
            break

        if max_pages and page >= max_pages:
            break

        if page >= total_pages:
            break

        page += 1

    return all_products


async def get_product_detail(product_id: str) -> Optional[dict]:
    """
    Get full details for a single product.
    Endpoint: product/detail
    Raises on API error.
    """
    data = await _post("product/detail", {"id": product_id})
    raw = data.get("data", data.get("result", {}))
    if not raw:
        return None
    return _normalise_product(raw)


def _normalise_product(raw: dict) -> dict:
    """Map Sunsky raw product fields to our internal schema."""
    images = _normalise_images(raw)
    merged_raw = {**raw, "images": images}

    return {
        "id":           str(raw.get("id", raw.get("itemNo", raw.get("sku", "")))),
        "sku":          str(raw.get("itemNo", raw.get("sku", raw.get("id", "")))),
        "name":         raw.get("name", raw.get("title", "")),
        "description":  raw.get("description", raw.get("desc", "")),
        "price":        str(raw.get("price", raw.get("sellPrice", "0.00"))),
        "stock_status": "in_stock" if raw.get("stockNum", raw.get("stock", 1)) else "out_of_stock",
        "category_id":  str(raw.get("categoryId", raw.get("catId", ""))),
        "images":       images,
        "raw_data":     merged_raw,
    }
