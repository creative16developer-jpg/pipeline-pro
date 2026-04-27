"""
WooCommerce REST API v3 client.
Uses HTTP Basic Auth: consumer_key:consumer_secret (Base64) over HTTPS.
"""

import httpx
import base64
import mimetypes
from pathlib import Path
from typing import Optional
from models.models import Store


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
