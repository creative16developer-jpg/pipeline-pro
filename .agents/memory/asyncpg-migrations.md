---
name: asyncpg multi-statement migrations
description: asyncpg (used by async SQLAlchemy) rejects SQL strings with multiple statements; must split and execute each separately
---

asyncpg raises `PostgresSyntaxError: cannot insert multiple commands into a prepared statement` when you pass a SQL string containing more than one statement to `conn.execute(text(...))`.

**Rule:** Always split migration SQL into individual statements and loop:

```python
stmts = ["CREATE TABLE IF NOT EXISTS foo (...)", "CREATE TABLE IF NOT EXISTS bar (...)"]
async with engine.begin() as conn:
    for s in stmts:
        await conn.execute(text(s))
```

**Why:** asyncpg prepares statements before execution; PostgreSQL's prepare protocol only accepts single statements.

**How to apply:** Any time you run a `.sql` migration file programmatically via SQLAlchemy + asyncpg, split on `;` or maintain a list of individual statement strings. The `psql` CLI has no this restriction and can run multi-statement files directly.
