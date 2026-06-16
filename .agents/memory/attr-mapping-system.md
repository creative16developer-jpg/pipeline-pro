---
name: Attribute mapping system
description: Architecture of the AI extraction rules, attribute profiles, and inventory mapping system added in the full attribute mapping feature.
---

## New DB tables (migration 004_attr_mapping.sql — idempotent)
- `attribute_profiles` — named sets of expected WooCommerce attributes for a category
- `profile_attributes` — FK → attribute_profiles; one row per woo_attr_name per profile
- `ai_extraction_rules` — per-attribute AI extraction config (source_fields, instruction, confidence_threshold, if_not_found, default_value, sort_order)
- `inventory_mapping_configs` — per-store weight/dimension unit + null-handling config
- `sunsky_category_mappings.profile_id` — FK → attribute_profiles (SET NULL on delete)
- `product_enrich_attrs.source` VARCHAR(20) DEFAULT 'ai'
- `product_enrich_attrs.flagged` BOOLEAN DEFAULT FALSE

## Python routers (all registered in main.py under /api prefix)
- `routers/attr_rules.py` → GET/POST /api/attr-rules, GET/PUT/DELETE /api/attr-rules/{id}
- `routers/attr_profiles.py` → GET/POST /api/attr-profiles, GET/PUT/DELETE /api/attr-profiles/{id}
- `routers/inventory_mapping.py` → GET/PUT /api/stores/{store_id}/inventory-mapping

## Express proxy rules (api-server/src/routes/)
- New file: `attr-proxy.ts` — exports attrRulesRouter + attrProfilesRouter (generic makePythonProxy helper)
- `index.ts` registers them at `/attr-rules` and `/attr-profiles`
- `/api/stores/:id/inventory-mapping` is already covered by the catch-all in `stores.ts` (router.all("/{*path}", proxyToPython))

## enrich_service.py pattern
- `extract_attributes(product, gen_cfg, db=None)` — loads AIExtractionRule rows from DB via `_load_rules(db)`, builds DB-driven prompt, applies if_not_found rules, sets source/flagged on each result
- Falls back to `_rule_based_extract()` (paramsTable parsing, confidence 0.75) when no AI
- pipeline_tasks.py must pass `db=db` to extract_attributes and save source/flagged columns

## Map step (CategoryMapPanel in Pipelines.tsx)
- map-data response includes `profiles: [{id, name, description, attributes}]` array
- RowSel interface has `profile_id: number | null`
- New "Panel B" profile dropdown shown inside each new-category row (and All Products mode)
- auto-mapped (known) category rows show a violet profile badge if profile_id is set
- map-confirm payload includes `profile_id` per mapping entry

**Why:** profile_id links a Sunsky category → attribute profile → tells the AI which WooCommerce attributes to extract for products in that category during the Enrich step.
