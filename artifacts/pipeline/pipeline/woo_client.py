"""
WooCommerce REST API v3 client.
Uses HTTP Basic Auth: consumer_key:consumer_secret (Base64) over HTTPS.
"""

import re
import httpx
import base64
import mimetypes
from pathlib import Path
from typing import Optional
from models.models import Store


def _make_woo_slug(name: str, suffix: str = "") -> str:
    """
    Generate a WooCommerce-safe slug from a category or attribute name.

    Rules applied:
      - Decode common HTML entities (&amp; → and, etc.)
      - Lower-case everything
      - Replace every run of non-alphanumeric characters with a single hyphen
      - Strip leading / trailing hyphens
      - Truncate to 190 chars (WooCommerce limit is 200, we leave room for suffix)
      - Append suffix when provided (used to scope child-category slugs to their
        parent_woo_id so sibling categories with the same name are unique)

    Examples:
      "DIY Parts & Components"          → "diy-parts-components"
      "Mobile Accessories" (parent=12)  → "mobile-accessories-12"
      "AC/DC Adapters"                  → "ac-dc-adapters"
    """
    # Decode a handful of common HTML entities before slugifying
    entity_map = {
        "&amp;": "and", "&": "and",
        "/": "-", "\\": "-",
        "&lt;": "", "&gt;": "",
        "&quot;": "", "&#39;": "",
    }
    slug = name
    for entity, replacement in entity_map.items():
        slug = slug.replace(entity, f" {replacement} ")

    slug = slug.lower()
    slug = re.sub(r"[^a-z0-9]+", "-", slug)
    slug = slug.strip("-")
    slug = slug[:190]

    if suffix:
        slug = f"{slug}-{suffix}"

    # Final safety: collapse any double-hyphens introduced by substitution
    slug = re.sub(r"-{2,}", "-", slug)
    return slug or "category"


def _auth_header(store: Store) -> dict:
    creds = f"{store.consumer_key}:{store.consumer_secret}"
    token = base64.b64encode(creds.encode()).decode()
    return {"Authorization": f"Basic {token}"}


def _base_url(store: Store) -> str:
    return store.url.rstrip("/") + "/wp-json/wc/v3"


def _wp_base_url(store: Store) -> str:
    return store.url.rstrip("/") + "/wp-json/wp/v2"


async def test_connection(store: Store) -> dict:
    """Ping WooCommerce store and return basic info."""
    try:
        async with httpx.AsyncClient(timeout=10.0, verify=False) as client:
            resp = await client.get(
                f"{_base_url(store)}/system_status",
                headers=_auth_header(store),
            )
            resp.raise_for_status()
            data = resp.json()
            environment = data.get("environment", {})
            return {
                "success": True,
                "wp_version": environment.get("wp_version", "unknown"),
                "wc_version": environment.get("version", "unknown"),
                "site_url": environment.get("site_url", store.url),
            }
    except httpx.HTTPStatusError as e:
        return {"success": False, "error": f"HTTP {e.response.status_code}: {e.response.text[:200]}"}
    except Exception as e:
        return {"success": False, "error": str(e)}


async def get_categories(store: Store, per_page: int = 100) -> list[dict]:
    """Fetch all WooCommerce product categories."""
    categories = []
    page = 1
    async with httpx.AsyncClient(timeout=30.0, verify=False) as client:
        while True:
            resp = await client.get(
                f"{_base_url(store)}/products/categories",
                headers=_auth_header(store),
                params={"per_page": per_page, "page": page},
            )
            resp.raise_for_status()
            batch = resp.json()
            if not batch:
                break
            categories.extend(batch)
            if len(batch) < per_page:
                break
            page += 1
    return categories


async def upload_image_to_wordpress(
    store: Store,
    file_path: str,
    filename: Optional[str] = None,
) -> Optional[str]:
    """
    Upload a local image file to the WordPress media library.
    Returns the public URL of the uploaded attachment, or None on failure.

    Requires wp_username + wp_app_password on the Store (WordPress Application
    Password — NOT the WooCommerce consumer key/secret, which only work with
    /wp-json/wc/v3/* and cannot authenticate /wp-json/wp/v2/media).

    How to create a WordPress Application Password:
      WP Admin → Users → Profile → Application Passwords → Add New
    """
    if not store.wp_username or not store.wp_app_password:
        print(
            "[woo_client] Skipping WP media upload — "
            "wp_username / wp_app_password not set on store. "
            "Add them in the Stores page to enable image upload."
        )
        return None

    path = Path(file_path)
    if not path.exists():
        print(f"[woo_client] File not found: {file_path}")
        return None

    fname = filename or path.name
    mime_type, _ = mimetypes.guess_type(fname)
    if not mime_type:
        if fname.endswith(".webp"):
            mime_type = "image/webp"
        elif fname.endswith(".png"):
            mime_type = "image/png"
        else:
            mime_type = "image/jpeg"

    # WordPress Application Password: Basic auth with WP username + app password
    wp_creds = f"{store.wp_username}:{store.wp_app_password}"
    wp_token = base64.b64encode(wp_creds.encode()).decode()
    headers = {
        "Authorization": f"Basic {wp_token}",
        "Content-Disposition": f'attachment; filename="{fname}"',
        "Content-Type": mime_type,
    }

    try:
        async with httpx.AsyncClient(timeout=120.0, verify=False) as client:
            resp = await client.post(
                f"{_wp_base_url(store)}/media",
                headers=headers,
                content=path.read_bytes(),
            )
            if resp.is_success:
                data = resp.json()
                url = data.get("source_url") or (
                    data.get("guid", {}).get("rendered")
                    if isinstance(data.get("guid"), dict) else None
                )
                return url
            else:
                print(
                    f"[woo_client] WP media upload failed {resp.status_code}: "
                    f"{resp.text[:400]}"
                )
                return None
    except Exception as e:
        print(f"[woo_client] WP media upload error for {file_path}: {e}")
        return None


async def create_product(store: Store, product_data: dict) -> dict:
    """
    Create a WooCommerce product as a draft.
    product_data keys: name, sku, regular_price, description, status,
                       images (list of URLs), categories, stock_quantity, manage_stock
    """
    raw_images = product_data.get("images", [])
    images = [
        {"src": url} for url in raw_images
        if isinstance(url, str) and url.startswith("http")
    ]

    raw_cats = product_data.get("category_ids", [])
    categories = [{"id": int(cid)} for cid in raw_cats if cid]

    payload = {
        "name": product_data.get("name", "") or "Unnamed Product",
        "regular_price": str(product_data.get("price", "0") or "0"),
        "description": product_data.get("description", "") or "",
        "short_description": product_data.get("short_description", "") or "",
        "status": "draft",
        "manage_stock": True,
        "stock_quantity": int(product_data.get("stock_quantity", 0) or 0),
    }

    sku = (product_data.get("sku", "") or "").strip()
    if sku:
        payload["sku"] = sku

    if images:
        payload["images"] = images

    if categories:
        payload["categories"] = categories

    async with httpx.AsyncClient(timeout=60.0, verify=False) as client:
        resp = await client.post(
            f"{_base_url(store)}/products",
            headers=_auth_header(store),
            json=payload,
        )
        if not resp.is_success:
            raise httpx.HTTPStatusError(
                f"HTTP {resp.status_code}: {resp.text[:500]}",
                request=resp.request,
                response=resp,
            )
        return resp.json()


async def get_product_by_sku(store: Store, sku: str) -> Optional[dict]:
    """
    Look up a WooCommerce product by SKU.
    Returns the WooCommerce product dict if found, or None.
    """
    try:
        async with httpx.AsyncClient(timeout=20.0, verify=False) as client:
            resp = await client.get(
                f"{_base_url(store)}/products",
                headers=_auth_header(store),
                params={"sku": sku, "per_page": 1},
            )
            resp.raise_for_status()
            results = resp.json()
            if isinstance(results, list) and results:
                return results[0]
            return None
    except Exception:
        return None


async def update_product(store: Store, woo_id: int, product_data: dict) -> dict:
    """
    Update an existing WooCommerce product (full payload).
    """
    raw_images = product_data.get("images", [])
    images = [
        {"src": url} for url in raw_images
        if isinstance(url, str) and url.startswith("http")
    ]

    raw_cats = product_data.get("category_ids", [])
    categories = [{"id": int(cid)} for cid in raw_cats if cid]

    payload: dict = {}
    if "name" in product_data:
        payload["name"] = product_data["name"] or "Unnamed Product"
    if "price" in product_data:
        payload["regular_price"] = str(product_data["price"] or "0")
    if "description" in product_data:
        payload["description"] = product_data["description"] or ""
    if "short_description" in product_data:
        payload["short_description"] = product_data["short_description"] or ""
    if "stock_quantity" in product_data:
        payload["manage_stock"] = True
        payload["stock_quantity"] = int(product_data["stock_quantity"] or 0)
    if "sku" in product_data and product_data["sku"]:
        payload["sku"] = product_data["sku"].strip()
    if images:
        payload["images"] = images
    if categories:
        payload["categories"] = categories

    async with httpx.AsyncClient(timeout=60.0, verify=False) as client:
        resp = await client.put(
            f"{_base_url(store)}/products/{woo_id}",
            headers=_auth_header(store),
            json=payload,
        )
        if not resp.is_success:
            raise httpx.HTTPStatusError(
                f"HTTP {resp.status_code}: {resp.text[:500]}",
                request=resp.request,
                response=resp,
            )
        return resp.json()


async def update_product_stock(store: Store, woo_id: int, price: str, stock_qty: int) -> dict:
    """Update price and stock for an existing WooCommerce product."""
    async with httpx.AsyncClient(timeout=30.0, verify=False) as client:
        resp = await client.put(
            f"{_base_url(store)}/products/{woo_id}",
            headers=_auth_header(store),
            json={"regular_price": price, "stock_quantity": stock_qty},
        )
        resp.raise_for_status()
        return resp.json()


# ---------------------------------------------------------------------------
# Category sync helpers
# ---------------------------------------------------------------------------

async def get_all_woo_categories(store: Store) -> list[dict]:
    """Fetch every WooCommerce category (all pages)."""
    results = []
    page = 1
    async with httpx.AsyncClient(timeout=30.0, verify=False) as client:
        while True:
            resp = await client.get(
                f"{_base_url(store)}/products/categories",
                headers=_auth_header(store),
                params={"per_page": 100, "page": page},
            )
            resp.raise_for_status()
            batch = resp.json()
            if not batch:
                break
            results.extend(batch)
            if len(batch) < 100:
                break
            page += 1
    return results


async def create_woo_category(store: Store, name: str, parent_woo_id: int = 0) -> dict:
    """
    Get-or-create a WooCommerce product category, handling all edge cases:

    • Special characters (&, /, accents, HTML entities) — cleaned slug sent
      explicitly so WooCommerce never tries to auto-generate one.
    • Duplicate / already-existing category — WooCommerce returns
      code=term_exists (400).  We recover by fetching the existing category
      and returning it as-is (idempotent, no error raised).
    • Slug collision between siblings — when two categories at the same level
      share a clean slug (e.g. two "Others"), the parent_woo_id is appended to
      make the slug unique, e.g. "others-47".
    • Invalid / too-long names — truncated at 190 chars in the slug layer.

    Always returns a dict with at least {"id": int, "name": str, "parent": int}.
    """
    # --- Build an explicit, WooCommerce-safe slug -------------------------
    # Child categories include the parent ID in the slug so that siblings with
    # the same name (e.g. "Others" under many parents) never collide.
    suffix = str(parent_woo_id) if parent_woo_id else ""
    slug = _make_woo_slug(name, suffix=suffix)

    payload: dict = {"name": name, "slug": slug}
    if parent_woo_id:
        payload["parent"] = parent_woo_id

    async with httpx.AsyncClient(timeout=30.0, verify=False) as client:
        url = f"{_base_url(store)}/products/categories"
        auth = _auth_header(store)

        resp = await client.post(url, headers=auth, json=payload)

        # ── Success ───────────────────────────────────────────────────────
        if resp.is_success:
            return resp.json()

        # ── Parse the error body ──────────────────────────────────────────
        try:
            err = resp.json()
        except Exception:
            err = {}
        wc_code  = err.get("code", "")
        wc_data  = err.get("data") or {}

        # ── term_exists: category already present — return the existing one ─
        if wc_code in ("term_exists", "woocommerce_rest_term_exists"):
            # WooCommerce sometimes embeds the existing term's ID in data
            existing_id = wc_data.get("resource_id") if isinstance(wc_data, dict) else None
            if existing_id:
                r2 = await client.get(f"{url}/{existing_id}", headers=auth)
                if r2.is_success:
                    return r2.json()

            # Fallback: search by exact name + parent
            r2 = await client.get(url, headers=auth,
                                  params={"search": name, "per_page": 20,
                                          "parent": parent_woo_id})
            if r2.is_success:
                for cat in r2.json():
                    if (cat.get("name", "").lower() == name.lower()
                            and int(cat.get("parent") or 0) == parent_woo_id):
                        return cat
                # Also accept any match by name if parent doesn't matter
                for cat in r2.json():
                    if cat.get("name", "").lower() == name.lower():
                        return cat

            # Last resort: search by slug
            r3 = await client.get(url, headers=auth, params={"slug": slug, "per_page": 5})
            if r3.is_success and r3.json():
                return r3.json()[0]

        # ── Slug collision (not term_exists): retry with a unique fallback slug
        if resp.status_code in (400, 422) and wc_code != "term_exists":
            # Append a short hash of name+parent to guarantee uniqueness
            import hashlib
            uid = hashlib.md5(f"{name}{parent_woo_id}".encode()).hexdigest()[:6]
            payload2 = {**payload, "slug": f"{slug[:180]}-{uid}"}
            r2 = await client.post(url, headers=auth, json=payload2)
            if r2.is_success:
                return r2.json()
            # If still failing, raise with the original error body for clarity
            raise httpx.HTTPStatusError(
                f"HTTP {resp.status_code} creating category {name!r}: {err}",
                request=resp.request, response=resp,
            )

        # ── All other errors ──────────────────────────────────────────────
        resp.raise_for_status()
        return resp.json()


# ---------------------------------------------------------------------------
# Attribute sync helpers
# ---------------------------------------------------------------------------

async def get_all_woo_attributes(store: Store) -> list[dict]:
    """List all WooCommerce global product attributes."""
    async with httpx.AsyncClient(timeout=30.0, verify=False) as client:
        resp = await client.get(
            f"{_base_url(store)}/products/attributes",
            headers=_auth_header(store),
            params={"per_page": 100},
        )
        resp.raise_for_status()
        return resp.json()


async def create_woo_attribute(store: Store, name: str) -> dict:
    """
    Get-or-create a WooCommerce global product attribute.

    Handles special characters in name via explicit clean slug, and recovers
    gracefully from duplicate-slug / term_exists 400 errors by returning the
    existing attribute rather than raising.
    """
    slug = _make_woo_slug(name)
    payload = {
        "name": name,
        "slug": slug,
        "type": "select",
        "order_by": "menu_order",
        "has_archives": False,
    }
    async with httpx.AsyncClient(timeout=30.0, verify=False) as client:
        url = f"{_base_url(store)}/products/attributes"
        auth = _auth_header(store)

        resp = await client.post(url, headers=auth, json=payload)

        if resp.is_success:
            return resp.json()

        try:
            err = resp.json()
        except Exception:
            err = {}
        wc_code = err.get("code", "")
        wc_data = err.get("data") or {}

        # Attribute already exists — return it
        if wc_code in ("term_exists", "woocommerce_rest_term_exists",
                        "invalid_attribute_slug"):
            existing_id = wc_data.get("resource_id") if isinstance(wc_data, dict) else None
            if existing_id:
                r2 = await client.get(f"{url}/{existing_id}", headers=auth)
                if r2.is_success:
                    return r2.json()

            # Search all attributes for a name match
            r2 = await client.get(url, headers=auth, params={"per_page": 100})
            if r2.is_success:
                for attr in r2.json():
                    if attr.get("name", "").lower() == name.lower():
                        return attr

        # Slug collision: retry with a unique hash suffix
        if resp.status_code in (400, 422):
            import hashlib
            uid = hashlib.md5(name.encode()).hexdigest()[:6]
            payload2 = {**payload, "slug": f"{slug[:183]}-{uid}"}
            r2 = await client.post(url, headers=auth, json=payload2)
            if r2.is_success:
                return r2.json()

        resp.raise_for_status()
        return resp.json()


async def get_attribute_terms(store: Store, attr_id: int) -> list[dict]:
    """List all terms for a given WooCommerce product attribute."""
    results = []
    page = 1
    async with httpx.AsyncClient(timeout=30.0, verify=False) as client:
        while True:
            resp = await client.get(
                f"{_base_url(store)}/products/attributes/{attr_id}/terms",
                headers=_auth_header(store),
                params={"per_page": 100, "page": page},
            )
            resp.raise_for_status()
            batch = resp.json()
            if not batch:
                break
            results.extend(batch)
            if len(batch) < 100:
                break
            page += 1
    return results


async def create_attribute_term(store: Store, attr_id: int, term_name: str) -> dict:
    """Create a term for a WooCommerce product attribute."""
    async with httpx.AsyncClient(timeout=30.0, verify=False) as client:
        resp = await client.post(
            f"{_base_url(store)}/products/attributes/{attr_id}/terms",
            headers=_auth_header(store),
            json={"name": term_name},
        )
        resp.raise_for_status()
        return resp.json()


async def set_product_attributes(store: Store, woo_id: int, attributes: list[dict]) -> dict:
    """
    Update the attributes on an existing WooCommerce product.
    attributes = [{"id": attr_id, "name": name, "options": ["val1","val2"], "visible": True}]
    """
    async with httpx.AsyncClient(timeout=60.0, verify=False) as client:
        resp = await client.put(
            f"{_base_url(store)}/products/{woo_id}",
            headers=_auth_header(store),
            json={"attributes": attributes},
        )
        if not resp.is_success:
            raise httpx.HTTPStatusError(
                f"HTTP {resp.status_code}: {resp.text[:500]}",
                request=resp.request,
                response=resp,
            )
        return resp.json()


async def set_product_categories(store: Store, woo_id: int, category_woo_ids: list[int]) -> dict:
    """Update the categories on an existing WooCommerce product."""
    cats = [{"id": cid} for cid in category_woo_ids if cid]
    async with httpx.AsyncClient(timeout=60.0, verify=False) as client:
        resp = await client.put(
            f"{_base_url(store)}/products/{woo_id}",
            headers=_auth_header(store),
            json={"categories": cats},
        )
        if not resp.is_success:
            raise httpx.HTTPStatusError(
                f"HTTP {resp.status_code}: {resp.text[:500]}",
                request=resp.request,
                response=resp,
            )
        return resp.json()
