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


class _SunskyRateLimitError(Exception):
    """Raised when Sunsky returns UP_TO_API_CALL_LIMIT_IN_MINUTE — transient, retryable."""


def _build_signature(params: dict) -> str:
    sorted_keys = sorted(params.keys())
    value_string = "".join(str(params[k]) for k in sorted_keys)
    raw = value_string + "@" + settings.sunsky_api_secret
    return hashlib.md5(raw.encode("utf-8")).hexdigest()


# T01 — Mock fallback: when there are no credentials at all, return
# realistic mock data instead of failing.
#
# IMPORTANT: TESTKEY/TESTSECRET are NOT a dead placeholder — they are a
# real, working Sunsky-issued demo credential. Verified directly against
# Sunsky's live API (2026-08-01): a correctly-signed request with
# key=TESTKEY returned real category data ("result": "success", full
# category list). Sunsky's own team also confirmed they don't IP-block
# normal requests. So TESTKEY must always attempt the real signed API call
# first, exactly like any other key — it must NEVER be treated as an
# automatic mock trigger. Only a genuinely empty key (no credentials
# configured at all) skips the network call outright. The secondary
# fallback (mock data on an actual 401/403 from Sunsky) still applies to
# TESTKEY same as any other key, in case it's ever rotated/revoked.
def _is_mock_mode() -> bool:
    return not settings.sunsky_api_key


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

                # Check for business-logic error
                result_field = str(data.get("result", "")).lower()
                if result_field == "error":
                    msgs = data.get("messages", data.get("message", data.get("msg", "")))
                    msg_text = str(msgs)
                    if "UP_TO_API_CALL_LIMIT" in msg_text.upper():
                        # Rate limit — genuinely transient, worth retrying
                        # with backoff rather than failing immediately like
                        # other business-logic errors.
                        raise _SunskyRateLimitError(f"Sunsky API rate limit for {endpoint}: {msg_text}")
                    raise ValueError(f"Sunsky API error for {endpoint}: {msgs}")

                code = data.get("code", 0)
                if code not in (0, 200, None):
                    msg = data.get("message", data.get("msg", str(data)))
                    raise ValueError(f"Sunsky API error (code={code}): {msg}")
                return data
        except ValueError:
            # Business-logic errors (excluding rate limits, handled below) — don't retry
            raise
        except _SunskyRateLimitError as exc:
            last_error = exc
            if attempt < MAX_RETRIES:
                # Rate limits reset per-minute — wait long enough to clear
                # the window rather than the short network-error backoff.
                await asyncio.sleep(20 * attempt)
            continue
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
    except _SunskyAuthError as exc:
        print(f"[sunsky_client] {exc} — falling back to mock category data. "
              f"This means real credentials were configured but Sunsky rejected the "
              f"request (401/403) — check the key/secret and IP whitelist status.")
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
            # Sunsky enforces a per-minute call limit; walking a deep tree
            # can easily make dozens of rapid sequential calls otherwise.
            await asyncio.sleep(0.3)
            await _recurse(cat["id"])

    await _recurse("0")
    return all_cats


# Category ID -> name cache. Real Sunsky product responses only include a
# numeric categoryId, not a category name, so anywhere in the app that needs
# to match/display a category by name (Category Mapping, Attribute Mapping
# "if category" conditions, the Category Review pause) needs this lookup.
# get_category_tree() walks the whole tree recursively (many API calls) so
# it's cached in-process rather than called per-product or per-pipeline-run.
_category_name_cache: dict[str, str] = {}
_category_cache_fetched_at: float = 0.0
_CATEGORY_CACHE_TTL_SECONDS = 6 * 3600  # 6 hours — category trees rarely change,
# and walking the tree is itself rate-limit-sensitive, so don't do it more than needed


async def get_category_name_map(force_refresh: bool = False) -> dict[str, str]:
    """Return a {category_id: category_name} map, cached for 6 hours."""
    import time
    global _category_name_cache, _category_cache_fetched_at
    now = time.monotonic()
    if not force_refresh and _category_name_cache and (now - _category_cache_fetched_at) < _CATEGORY_CACHE_TTL_SECONDS:
        return _category_name_cache
    try:
        tree = await get_category_tree()
        _category_name_cache = {str(c["id"]): c["name"] for c in tree if c.get("id") and c.get("name")}
        _category_cache_fetched_at = now
    except Exception as exc:
        print(f"[sunsky_client] get_category_name_map() failed: {exc} — "
              f"using stale/empty cache as fallback.")
    return _category_name_cache


async def get_category_name_map_safe(timeout: float = 20.0) -> dict[str, str]:
    """
    Same as get_category_name_map(), but hard-capped at `timeout` seconds.

    get_category_tree() walks the whole Sunsky category tree with a
    deliberate pacing delay between calls (see get_category_tree) to avoid
    the per-minute rate limit — but on a large tree, or if a rate-limit
    retry triggers mid-walk, that can legitimately take minutes, and it
    logs nothing while in progress. A category-name resolution failure
    should degrade to raw-ID matching (the old behavior), not block an
    entire pipeline run indefinitely. Callers should treat an empty dict
    here the same as "no name available for this ID."
    """
    try:
        return await asyncio.wait_for(get_category_name_map(), timeout=timeout)
    except asyncio.TimeoutError:
        print(f"[sunsky_client] get_category_name_map() exceeded {timeout}s — "
              f"proceeding with whatever cache exists (possibly empty). "
              f"Category-name-based matching may be incomplete this run.")
        return _category_name_cache


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
    except _SunskyAuthError as exc:
        # Real key configured but Sunsky rejected the request (401/403) —
        # don't assume why (could be IP restriction, revoked key, bad
        # signature, etc.) — just log it clearly and fall back to mock.
        print(f"[sunsky_client] {exc} — falling back to mock product data.")
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
    except _SunskyAuthError as exc:
        print(f"[sunsky_client] {exc} — falling back to mock product data for {item_no!r}.")
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
