---
name: Map + Enrich pipeline steps
description: Architecture decisions for the Map (category mapping) and Enrich (AI attribute extraction) pipeline steps added in this session.
---

## Pipeline Flow
Process → Enrich (opt, pauses at `enrich_review`) → Generate (opt) → Review pause → Upload → Sync

## DB Tables (migrated: add_map_enrich_tables.sql)
- `sunsky_category_mappings(store_id, sunsky_cat)` — persistent mapping, applied at upload time
- `product_enrich_attrs(pipeline_job_id, product_id, attribute)` — per-run AI extractions
- `normalisation_dict(store_id, attribute, raw_value)` — persistent raw→WooTerm dictionary
- `variant_groups(pipeline_job_id)` — AI-suggested variant groups per run

## Status Transitions
- `enrich_review` added to `ACTIVE_STATUSES` in pipeline.py (blocks queue start)
- `enrich_review` is a new status alongside `running/review/queued/completed/failed/cancelled`

## Enrich Task Resume
- `enrich_resume_pipeline_job` Celery task → `_enrich_resume_pipeline` continues from `enrich_review`
- Triggered by `POST /api/pipelines/{id}/enrich-confirm`

## Map Step Resume
- `POST /api/pipelines/{id}/map-confirm` → saves mappings then calls `resume_pipeline_job.delay()` directly
- Plain `/resume` still works for skipping category mapping

## Node.js Proxy Rule
- `/api/pipelines/*` — pipeline.ts catches all; new pipeline-scoped endpoints work automatically
- `/api/stores/*` — stores.ts has a catch-all `router.all("/{*path}", proxyToPython)` for unhandled paths (added for category-mappings, normalisation-dict)

**Why:** Node.js api-server owns /api routing; Python owns the business logic. New Python-only store endpoints must be proxied in stores.ts.

## AI Prompt Override
`generate_with_ai(field, product, provider, model, options)` now checks for `options["_prompt_override"]`; if present, uses that string verbatim as the prompt instead of building one from field/product. Used by enrich_service.py for JSON extraction prompts.
