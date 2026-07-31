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


class _SunskyAuthError(Exception):
    """Raised when Sunsky returns 401/403 — IP not whitelisted for this key."""


def _build_signature(params: dict) -> str:
    sorted_keys = sorted(params.keys())
    value_string = "".join(str(params[k]) for k in sorted_keys)
    raw = value_string + "@" + settings.sunsky_api_secret
    return hashlib.md5(raw.encode("utf-8")).hexdigest()


# T01 — Mock fallback: when no real Sunsky credentials are configured
# (IP-whitelist restriction means most dev/staging environments can't reach
# the real API), return realistic mock data instead of failing.
_MOCK_KEYS = {"", "TESTKEY"}


def _is_mock_mode() -> bool:
    return settings.sunsky_api_key in _MOCK_KEYS


_MOCK_CATEGORY_NAMES = ["Mobile Phones", "Phone Accessories", "Tablets", "Wearables", "Audio"]


def _mock_products(count: int = 20, category_id: Optional[str] = None, keyword: Optional[str] = None) -> list[dict]:
    """Generate mock product dicts in the exact shape _normalise_product() returns."""
    products = []
    for i in range(1, count + 1):
        cat_id = category_id or str(1000 + (i % len(_MOCK_CATEGORY_NAMES)))
        cat_name = _MOCK_CATEGORY_NAMES[i % len(_MOCK_CATEGORY_NAMES)]
        name = f"Mock {cat_name} Item {i}"
        if keyword:
            name = f"{keyword.title()} {name}"
        sku = f"MOCK-{i:05d}"
        products.append({
            "id": sku,
            "sku": sku,
            "name": name,
            "description": f"Mock product description for {name}. Generated because SUNSKY_API_KEY is unset or TESTKEY.",
            "price": f"{9.99 + i:.2f}",
            "stock_status": "in_stock" if i % 5 != 0 else "out_of_stock",
            "category_id": cat_id,
            "images": [f"https://placehold.co/600x600?text=Mock+{i}"],
            "raw_data": {"mock": True, "itemNo": sku, "categoryId": cat_id},
        })
    return products


def _mock_categories() -> list[dict]:
    return [
        {"id": str(1000 + idx), "alias_id": "", "name": name, "parent_id": None}
        for idx, name in enumerate(_MOCK_CATEGORY_NAMES)
    ]


async def _post(endpoint: str, params: dict) -> dict:
    params = dict(params)
    params["key"] = settings.sunsky_api_key
    params["signature"] = _build_signature(params)

    last_error: Exception = RuntimeError("No attempts made")
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
                resp = await client.post(f"{SUNSKY_BASE}/{endpoint.lstrip('/')}", data=params)
                if resp.status_code in (401, 403):
                    # Secondary fallback: whitelist restriction on this IP —
                    # signal to callers to use mock data instead of failing.
                    raise _SunskyAuthError(f"Sunsky API returned {resp.status_code} for {endpoint}")
                resp.raise_for_status()
                data = resp.json()

                # Check for business-logic error (no retry needed)
                result_field = str(data.get("result", "")).lower()
                if result_field == "error":
                    msgs = data.get("messages", data.get("message", data.get("msg", "")))
                    raise ValueError(f"Sunsky API error for {endpoint}: {msgs}")

                code = data.get("code", 0)
                if code not in (0, 200, None):
                    msg = data.get("message", data.get("msg", str(data)))
                    raise ValueError(f"Sunsky API error (code={code}): {msg}")
                return data
        except ValueError:
            # Business-logic errors — don't retry
            raise
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
            for nested in ("result", "list", "items", "rows", "data"):
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
    # Expose both possible ID fields so callers can map either one.
    # Sunsky sometimes uses "id" and sometimes "categoryId"; products always
    # store the value from their "categoryId" field.
    raw_id       = str(raw.get("id", "")).strip()
    raw_cat_id   = str(raw.get("categoryId", "")).strip()
    primary_id   = raw_cat_id or raw_id  # prefer categoryId to match product data
    alias_id     = raw_id if raw_id and raw_id != primary_id else ""
    return {
        "id":       primary_id,
        "alias_id": alias_id,           # secondary key (may be empty)
        "name":     raw.get("name", raw.get("title", "")),
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
    if _is_mock_mode():
        return _mock_categories() if parent_id in ("0", "", None) else []
    try:
        data = await _post("category!getChildren.do", {"parentId": parent_id})
        categories = _extract_list(data)
        return [_normalise_category(c) for c in categories if c.get("id") or c.get("categoryId")]
    except _SunskyAuthError:
        return _mock_categories() if parent_id in ("0", "", None) else []


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
    if _is_mock_mode():
        mock = _mock_products(count=min(page_size, 20), category_id=category_id, keyword=keyword)
        return {"products": mock, "total": len(mock), "pages": 1}

    params: dict = {"pageNo": page, "pageSize": page_size}
    if category_id:
        params["categoryId"] = category_id
    if keyword:
        params["keyword"] = keyword

    try:
        data = await _post("product!search.do", params)
    except _SunskyAuthError:
        # Real key configured but this IP isn't whitelisted — fall back to mock.
        mock = _mock_products(count=min(page_size, 20), category_id=category_id, keyword=keyword)
        return {"products": mock, "total": len(mock), "pages": 1}

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


async def _post_binary(endpoint: str, params: dict) -> Optional[bytes]:
    """POST request that expects a binary (e.g. ZIP) response instead of JSON."""
    params = dict(params)
    params["key"] = settings.sunsky_api_key
    params["signature"] = _build_signature(params)

    async with httpx.AsyncClient(timeout=120.0, follow_redirects=True) as client:
        resp = await client.post(f"{SUNSKY_BASE}/{endpoint.lstrip('/')}", data=params)

        if resp.status_code == 404:
            return None

        resp.raise_for_status()

        content_type = resp.headers.get("content-type", "")
        if "application/json" in content_type or "text/" in content_type:
            # API returned JSON instead of binary — check for error
            try:
                data = resp.json()
                result_field = str(data.get("result", "")).lower()
                if result_field == "error":
                    msgs = data.get("messages", data.get("message", ""))
                    raise ValueError(f"Sunsky API error for {endpoint}: {msgs}")
            except (ValueError, AttributeError):
                raise
            return None

        return resp.content


async def get_products_by_spus(spus: list[str]) -> list[dict]:
    """
    Fetch multiple products by SPU / itemNo in parallel using product!detail.do.
    Returns a flat list of normalised product dicts (skips any that 404 or error).
    """
    import asyncio

    clean = [s.strip() for s in spus if s.strip()]
    if not clean:
        return []
    tasks = [get_product_detail(spu) for spu in clean]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    return [r for r in results if isinstance(r, dict)]


async def get_product_detail(item_no: str) -> Optional[dict]:
    """
    Fetch full product information using the correct endpoint:
      POST product!detail.do  with param itemNo=<SKU>
    """
    if _is_mock_mode():
        mocks = _mock_products(count=1)
        mocks[0]["id"] = mocks[0]["sku"] = item_no
        return mocks[0]
    try:
        data = await _post("product!detail.do", {"itemNo": item_no, "lang": "en"})
        raw = data.get("data", {})
        if not raw or not isinstance(raw, dict):
            return None
        return _normalise_product(raw)
    except _SunskyAuthError:
        mocks = _mock_products(count=1)
        mocks[0]["id"] = mocks[0]["sku"] = item_no
        return mocks[0]
    except Exception as exc:
        print(f"[sunsky_client] get_product_detail({item_no!r}) failed: {exc}")
        return None


async def download_product_images(item_no: str, size: str = "middle", watermark: int = 0) -> Optional[bytes]:
    """
    Download all product images as a ZIP archive.
      POST product!getImages.do  with params itemNo, size, watermark
    Returns raw ZIP bytes, or None if the product has no images / not found.
    """
    try:
        return await _post_binary(
            "product!getImages.do",
            {"itemNo": item_no, "size": size, "watermark": watermark},
        )
    except Exception as exc:
        print(f"[sunsky_client] download_product_images({item_no!r}) failed: {exc}")
        return None


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
