"""
Sunsky Open API client.

Authentication (official docs):
  1. Collect all request parameters including 'key' (your API key).
  2. Sort them alphabetically by parameter name.
  3. Concatenate the values (keeping whitespace as-is).
  4. Append '@' + your secret.
  5. MD5-hash the resulting string (hex, lowercase).
  6. Send as POST with key= and signature= added to the body.

Open API Base URL: https://open.sunsky-online.com/openapi
"""

import asyncio
import hashlib
import httpx
from typing import Optional
from config import get_settings

settings = get_settings()

SUNSKY_BASE = (settings.sunsky_api_url or "https://open.sunsky-online.com/openapi").rstrip("/")
SUNSKY_CDN = "https://www.sunsky-online.com"
MAX_RETRIES = 3
RETRY_DELAY = 2.0


def _build_signature(params: dict) -> str:
    sorted_keys = sorted(params.keys())
    value_string = "".join(str(params[k]) for k in sorted_keys)
    raw = value_string + "@" + settings.sunsky_api_secret
    return hashlib.md5(raw.encode("utf-8")).hexdigest()


async def _post(endpoint: str, params: dict) -> dict:
    params = dict(params)
    params["key"] = settings.sunsky_api_key
    params["signature"] = _build_signature(params)

    last_error: Exception = RuntimeError("No attempts made")
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
                resp = await client.post(f"{SUNSKY_BASE}/{endpoint.lstrip('/')}", data=params)
                resp.raise_for_status()
                data = resp.json()
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
        except Exception:
            raise

    raise last_error


def _extract_list(data: dict) -> list:
    for key in ("data", "result", "rows", "list", "items"):
        val = data.get(key)
        if isinstance(val, list):
            return val
        if isinstance(val, dict):
            for nested in ("list", "items", "rows", "data"):
                nested_val = val.get(nested)
                if isinstance(nested_val, list):
                    return nested_val
    return []


def _extract_total(data: dict, fallback: int) -> int:
    for key in ("total", "totalCount", "count", "recordsTotal"):
        val = data.get(key)
        if isinstance(val, int):
            return val
        if isinstance(val, str) and val.isdigit():
            return int(val)
    for key in ("data", "result"):
        val = data.get(key)
        if isinstance(val, dict):
            for nested in ("total", "totalCount", "count"):
                nested_val = val.get(nested)
                if isinstance(nested_val, int):
                    return nested_val
                if isinstance(nested_val, str) and nested_val.isdigit():
                    return int(nested_val)
    return fallback


def _normalise_category(raw: dict) -> dict:
    return {
        "id": str(raw.get("id", raw.get("categoryId", ""))),
        "name": raw.get("name", raw.get("title", "")),
        "parent_id": str(raw.get("parentId", raw.get("parent_id", "0"))) if raw.get("parentId", raw.get("parent_id")) not in (None, "", 0, "0") else None,
    }


def _normalise_images(raw: dict) -> list[str]:
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
            url = (img.get("url") or img.get("src") or img.get("pic") or img.get("path") or "").strip()
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
    data = await _post("category!getChildren.do", {"parentId": parent_id})
    categories = _extract_list(data)
    return [_normalise_category(c) for c in categories if c.get("id") or c.get("categoryId")]


async def get_category_tree() -> list[dict]:
    all_cats: list[dict] = []
    seen: set[str] = set()

    async def _recurse(parent_id: str):
        children = await get_categories(parent_id)
        for cat in children:
            if cat["id"] in seen:
                continue
            seen.add(cat["id"])
            all_cats.append(cat)
            await _recurse(cat["id"])

    await _recurse("0")
    return all_cats


async def search_products(
    category_id: Optional[str] = None,
    keyword: Optional[str] = None,
    page: int = 1,
    page_size: int = 50,
) -> dict:
    params: dict = {"pageNo": page, "pageSize": page_size}
    if category_id:
        params["categoryId"] = category_id
    if keyword:
        params["keyword"] = keyword

    data = await _post("product!search.do", params)
    raw_products = _extract_list(data)
    total = _extract_total(data, len(raw_products))

    products = [_normalise_product(p) for p in raw_products]
    pages = max(1, (total + page_size - 1) // page_size) if total else 1
    return {"products": products, "total": total, "pages": pages}


async def get_all_products(
    category_id: Optional[str] = None,
    keyword: Optional[str] = None,
    page_size: int = 50,
    max_pages: Optional[int] = None,
    on_page: Optional[object] = None,
) -> list[dict]:
    all_products: list[dict] = []
    page = 1
    while True:
        result = await search_products(category_id=category_id, keyword=keyword, page=page, page_size=page_size)
        batch = result["products"]
        total_pages = result["pages"]
        total = result["total"]
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
    data = await _post("product!getDetail.do", {"id": product_id})
    raw = data.get("data", data.get("result", {}))
    if not raw:
        return None
    return _normalise_product(raw)


def _normalise_product(raw: dict) -> dict:
    images = _normalise_images(raw)
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
