"""
WooCommerce REST API v3 client.
Uses HTTP Basic Auth: consumer_key:consumer_secret (Base64) over HTTPS.
"""

import httpx
import base64
from typing import Optional
from models.models import Store


def _auth_header(store: Store) -> dict:
    creds = f"{store.consumer_key}:{store.consumer_secret}"
    token = base64.b64encode(creds.encode()).decode()
    return {"Authorization": f"Basic {token}"}


def _base_url(store: Store) -> str:
    return store.url.rstrip("/") + "/wp-json/wc/v3"


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


async def create_product(store: Store, product_data: dict) -> dict:
    """
    Create a WooCommerce product as a draft.
    product_data keys: name, sku, regular_price, description, status,
                       images, categories, stock_quantity, manage_stock
    """
    # Only include images that look like real URLs
    raw_images = product_data.get("images", [])
    images = [
        {"src": url} for url in raw_images
        if isinstance(url, str) and url.startswith("http")
    ]

    # Only include categories that are valid ints
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

    # SKU is optional — omit if blank to avoid duplicate-SKU 400 errors
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
            # Include full WooCommerce error body so we can diagnose 400s
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
