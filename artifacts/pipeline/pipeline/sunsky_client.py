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


# ── T01: Mock mode ────────────────────────────────────────────────────────────

class _SunskyAuthError(Exception):
    """Raised when the Sunsky API returns 401 or 403 (key not whitelisted)."""


def _use_mock() -> bool:
    """Return True when the API key is absent or clearly a test/demo key."""
    key = (settings.sunsky_api_key or "").strip()
    return not key or key.upper() in ("TESTKEY", "TEST", "DEMO", "")


_MOCK_CATEGORIES: list[dict] = [
    {"id": "101",  "name": "Phone Accessories",      "parent_id": None,  "alias_id": ""},
    {"id": "102",  "name": "Audio",                  "parent_id": None,  "alias_id": ""},
    {"id": "103",  "name": "Wearables",              "parent_id": None,  "alias_id": ""},
    {"id": "1011", "name": "Phone Cases & Covers",   "parent_id": "101", "alias_id": ""},
    {"id": "1012", "name": "Screen Protectors",      "parent_id": "101", "alias_id": ""},
    {"id": "1013", "name": "Wireless Chargers",      "parent_id": "101", "alias_id": ""},
    {"id": "1021", "name": "Bluetooth Earbuds",      "parent_id": "102", "alias_id": ""},
    {"id": "1022", "name": "Bluetooth Speakers",     "parent_id": "102", "alias_id": ""},
    {"id": "1031", "name": "Smart Watches",          "parent_id": "103", "alias_id": ""},
    {"id": "1032", "name": "Fitness Trackers",       "parent_id": "103", "alias_id": ""},
]

_IMG = "https://placehold.co/400x400/1e293b/94a3b8?text="

_MOCK_PRODUCTS: list[dict] = [
    {"id": "MOCK001", "sku": "MOCK001", "name": "Waterproof Bluetooth Earbuds 5.0 IPX5",
     "description": "Premium wireless earbuds with IPX5 waterproof rating and 20-hour playtime.",
     "price": "12.50", "stock_status": "in_stock", "category_id": "1021",
     "images": [_IMG + "Earbuds"],
     "raw_data": {"paramsTable": "<tr><td>Connectivity</td><td>Bluetooth 5.0</td></tr><tr><td>Waterproof</td><td>IPX5</td></tr><tr><td>Playtime</td><td>6h (20h case)</td></tr><tr><td>Color</td><td>Black / White / Blue</td></tr>"}},
    {"id": "MOCK002", "sku": "MOCK002", "name": "Tempered Glass Screen Protector 9H Hardness",
     "description": "Ultra-clear 9H hardness tempered glass for full-coverage smartphone protection.",
     "price": "2.30", "stock_status": "in_stock", "category_id": "1012",
     "images": [_IMG + "Glass"],
     "raw_data": {"paramsTable": "<tr><td>Material</td><td>Tempered Glass</td></tr><tr><td>Hardness</td><td>9H</td></tr><tr><td>Thickness</td><td>0.33mm</td></tr>"}},
    {"id": "MOCK003", "sku": "MOCK003", "name": "360 Degree Protective Phone Case with Stand",
     "description": "Full-body shockproof case with built-in kickstand and screen protector.",
     "price": "4.80", "stock_status": "in_stock", "category_id": "1011",
     "images": [_IMG + "PhoneCase"],
     "raw_data": {"paramsTable": "<tr><td>Material</td><td>TPU + PC</td></tr><tr><td>Feature</td><td>360° Protection</td></tr><tr><td>Kickstand</td><td>Yes</td></tr><tr><td>Color</td><td>Black / Red / Blue</td></tr>"}},
    {"id": "MOCK004", "sku": "MOCK004", "name": "15W Fast Wireless Charging Pad Qi",
     "description": "Universal Qi wireless charger with 15W fast charging for all Qi-enabled devices.",
     "price": "8.90", "stock_status": "in_stock", "category_id": "1013",
     "images": [_IMG + "Charger"],
     "raw_data": {"paramsTable": "<tr><td>Power Output</td><td>15W Max</td></tr><tr><td>Standard</td><td>Qi</td></tr><tr><td>Input</td><td>USB-C</td></tr>"}},
    {"id": "MOCK005", "sku": "MOCK005", "name": "Portable Bluetooth Speaker IPX7 Waterproof",
     "description": "Compact waterproof bluetooth speaker with 360° surround sound and 12-hour battery.",
     "price": "22.40", "stock_status": "in_stock", "category_id": "1022",
     "images": [_IMG + "Speaker"],
     "raw_data": {"paramsTable": "<tr><td>Bluetooth</td><td>5.0</td></tr><tr><td>Waterproof</td><td>IPX7</td></tr><tr><td>Battery</td><td>2000mAh</td></tr><tr><td>Color</td><td>Black / Blue / Red</td></tr>"}},
    {"id": "MOCK006", "sku": "MOCK006", "name": "Smart Watch with Heart Rate & SpO2 Monitor",
     "description": "Feature-packed smartwatch with health monitoring, GPS, and 7-day battery life.",
     "price": "35.00", "stock_status": "in_stock", "category_id": "1031",
     "images": [_IMG + "SmartWatch"],
     "raw_data": {"paramsTable": "<tr><td>Display</td><td>1.4\" AMOLED</td></tr><tr><td>Battery</td><td>7 days</td></tr><tr><td>Sensors</td><td>HR, SpO2, GPS</td></tr>"}},
    {"id": "MOCK007", "sku": "MOCK007", "name": "Sport Fitness Tracker Band with OLED Display",
     "description": "Slim fitness band with step counter, sleep tracker, and notification alerts.",
     "price": "14.20", "stock_status": "in_stock", "category_id": "1032",
     "images": [_IMG + "FitnessTracker"],
     "raw_data": {"paramsTable": "<tr><td>Display</td><td>OLED 0.96\"</td></tr><tr><td>Battery</td><td>10 days</td></tr><tr><td>Sensors</td><td>Accelerometer, HR</td></tr>"}},
    {"id": "MOCK008", "sku": "MOCK008", "name": "True Wireless Earbuds with Active Noise Cancellation",
     "description": "ANC earbuds with transparency mode and 30-hour total playtime with charging case.",
     "price": "28.90", "stock_status": "in_stock", "category_id": "1021",
     "images": [_IMG + "ANC+Earbuds"],
     "raw_data": {"paramsTable": "<tr><td>ANC</td><td>Yes, -25dB</td></tr><tr><td>Bluetooth</td><td>5.2</td></tr><tr><td>Playtime</td><td>8h (30h case)</td></tr><tr><td>Color</td><td>White / Black</td></tr>"}},
    {"id": "MOCK009", "sku": "MOCK009", "name": "Carbon Fibre Wallet Phone Case for iPhone",
     "description": "Premium carbon fibre pattern case with card holder and magnetic closure.",
     "price": "6.50", "stock_status": "in_stock", "category_id": "1011",
     "images": [_IMG + "WalletCase"],
     "raw_data": {"paramsTable": "<tr><td>Material</td><td>PU Leather</td></tr><tr><td>Card Slots</td><td>3</td></tr><tr><td>Pattern</td><td>Carbon Fibre</td></tr>"}},
    {"id": "MOCK010", "sku": "MOCK010", "name": "Privacy Screen Protector Anti-Spy Film",
     "description": "Anti-spy privacy filter screen protector with 180° viewing angle restriction.",
     "price": "3.10", "stock_status": "in_stock", "category_id": "1012",
     "images": [_IMG + "PrivacyScreen"],
     "raw_data": {"paramsTable": "<tr><td>Type</td><td>Privacy / Anti-Spy</td></tr><tr><td>View Angle</td><td>180° restricted</td></tr><tr><td>Material</td><td>PET Film</td></tr>"}},
]


def _mock_get_categories(parent_id: str = "0") -> list[dict]:
    if parent_id in ("0", "", None, "None"):
        return [c for c in _MOCK_CATEGORIES if not c["parent_id"]]
    return [c for c in _MOCK_CATEGORIES if c["parent_id"] == parent_id]


def _mock_search_products(
    category_id: Optional[str] = None,
    keyword: Optional[str] = None,
    page: int = 1,
    page_size: int = 50,
) -> dict:
    products = list(_MOCK_PRODUCTS)
    if category_id:
        products = [p for p in products if p["category_id"] == category_id]
    if keyword:
        kw = keyword.lower()
        products = [p for p in products if kw in p["name"].lower() or kw in p["description"].lower()]
    total = len(products)
    start = (page - 1) * page_size
    return {
        "products": products[start : start + page_size],
        "total": total,
        "pages": max(1, (total + page_size - 1) // page_size),
    }


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
                if resp.status_code in (401, 403):
                    raise _SunskyAuthError(
                        f"Sunsky API returned {resp.status_code} — key not whitelisted. Using mock data."
                    )
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
        except (_SunskyAuthError, ValueError):
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
    if _use_mock():
        return _mock_get_categories(parent_id)
    try:
        data = await _post("category!getChildren.do", {"parentId": parent_id})
        categories = _extract_list(data)
        return [_normalise_category(c) for c in categories if c.get("id") or c.get("categoryId")]
    except _SunskyAuthError:
        return _mock_get_categories(parent_id)


async def get_category_tree() -> list[dict]:
    if _use_mock():
        return list(_MOCK_CATEGORIES)

    all_cats: list[dict] = []
    seen: set[str] = set()

    async def _recurse(parent_id: str):
        try:
            children = await get_categories(parent_id)
        except _SunskyAuthError:
            return
        for cat in children:
            if cat["id"] in seen:
                continue
            seen.add(cat["id"])
            all_cats.append(cat)
            await _recurse(cat["id"])

    await _recurse("0")
    return all_cats if all_cats else list(_MOCK_CATEGORIES)


async def search_products(
    category_id: Optional[str] = None,
    keyword: Optional[str] = None,
    page: int = 1,
    page_size: int = 50,
) -> dict:
    if _use_mock():
        return _mock_search_products(category_id, keyword, page, page_size)
    try:
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
    except _SunskyAuthError:
        return _mock_search_products(category_id, keyword, page, page_size)


async def get_all_products(
    category_id: Optional[str] = None,
    keyword: Optional[str] = None,
    page_size: int = 50,
    max_pages: Optional[int] = None,
    on_page: Optional[object] = None,
) -> list[dict]:
    if _use_mock():
        result = _mock_search_products(category_id, keyword, 1, 999)
        products = result["products"]
        if on_page:
            await on_page(1, products, len(products))
        return products

    all_products: list[dict] = []
    page = 1
    while True:
        try:
            result = await search_products(category_id=category_id, keyword=keyword, page=page, page_size=page_size)
        except _SunskyAuthError:
            result = _mock_search_products(category_id, keyword, page, page_size)
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
    if _use_mock():
        match = next((p for p in _MOCK_PRODUCTS if p["sku"] == item_no or p["id"] == item_no), None)
        return match or _MOCK_PRODUCTS[0]
    try:
        data = await _post("product!detail.do", {"itemNo": item_no, "lang": "en"})
        raw = data.get("data", {})
        if not raw or not isinstance(raw, dict):
            return None
        return _normalise_product(raw)
    except _SunskyAuthError:
        match = next((p for p in _MOCK_PRODUCTS if p["sku"] == item_no or p["id"] == item_no), None)
        return match or _MOCK_PRODUCTS[0]
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
