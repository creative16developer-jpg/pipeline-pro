---
name: Sunsky mock fallback
description: How mock mode works in sunsky_client.py — when it activates, the data it returns, and how auth errors trigger fallback.
---

## When mock mode activates

```python
def _use_mock() -> bool:
    key = (settings.sunsky_api_key or "").strip()
    return not key or key.upper() in ("TESTKEY", "TEST", "DEMO", "")
```

Called at the start of every public function (get_categories, search_products, etc.).

## Auth error fallback

`_post()` checks `resp.status_code in (401, 403)` and raises `_SunskyAuthError`.
All public functions catch `_SunskyAuthError` and fall back to mock data.

## Mock data

- `_MOCK_CATEGORIES`: 3 top-level (Phone Accessories, Audio, Wearables) + 7 sub-categories
- `_MOCK_PRODUCTS`: 10 realistic products across those categories
- `_mock_get_categories(parent_id)` filters by parent_id ("0"/None/empty → top-level)
- `_mock_search_products(category_id, keyword, page, page_size)` filters and paginates

## Placeholder image URL pattern

`https://placehold.co/400x400/1e293b/94a3b8?text=ProductName`

**Why:** Sunsky uses IP-whitelisting; TESTKEY/TESTSECRET always return 403 on non-whitelisted IPs. Mock fallback prevents crashing in development.
**How to apply:** If real credentials are set but the IP isn't whitelisted yet, the 401/403 catch ensures graceful fallback rather than an error.
