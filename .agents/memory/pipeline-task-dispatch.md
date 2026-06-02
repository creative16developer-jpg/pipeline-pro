---
name: Pipeline task dispatch — no Celery/Redis
description: Root cause and fix for pipeline tasks never executing in Replit environment
---

## Rule
Do NOT use Celery `.delay()` for background tasks. Redis is not available in this Replit environment and Celery is not installed. Use `asyncio.create_task(async_fn(id))` instead.

**Why:** `celery` and `redis` Python packages appear in requirements.txt but are not installed in `.pythonlibs`. Redis port 6379 is closed. All `.delay()` calls silently failed — pipeline tasks never ran, so categories/attributes were never pushed to WooCommerce.

**How to apply:**
- In routers: `asyncio.create_task(_execute_pipeline(id))` instead of `run_pipeline_job.delay(id)`
- In routers: `asyncio.create_task(_resume_pipeline(id))` instead of `resume_pipeline_job.delay(id)`
- In routers: `asyncio.create_task(_enrich_resume_pipeline(id))` instead of `enrich_resume_pipeline_job.delay(id)`
- In routers: `asyncio.create_task(_execute_job(id))` instead of `run_job.delay(id)`
- The async entry points (`_execute_pipeline`, `_resume_pipeline`, `_enrich_resume_pipeline`, `_execute_job`) create their own DB sessions via `make_session_factory()` — this is fine inside asyncio tasks.
- `celery_app.py` can stay (not imported anywhere critical now). Both task files no longer import it.
