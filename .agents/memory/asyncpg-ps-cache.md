---
name: asyncpg prepared statement cache
description: Adding prepared_statement_cache_size=0 to asyncpg connect_args prevents stale-cache 500 errors after any DDL change
---

## Rule
Always set `prepared_statement_cache_size=0` in the asyncpg `connect_args` dict inside `_build_engine_url()` in `database.py`.

**Why:** asyncpg caches prepared statements per connection. After any DDL change (CREATE TABLE, ALTER TABLE, DROP TABLE — including migrations), the schema version bumps and SQLAlchemy tries to invalidate the cache via `_invalidate_schema_cache_asof`. This sometimes raises an unhandled 500 in FastAPI before the query can be retried. With cache size 0, asyncpg never caches prepared statements so schema changes are always safe.

**How to apply:** In `artifacts/pipeline/database.py`, inside `_build_engine_url()`:
```python
connect_args: dict = {
    "prepared_statement_cache_size": 0,
}
```
The `make_session_factory()` function calls `_build_engine_url()` so it inherits the fix automatically — no duplicate changes needed there.
