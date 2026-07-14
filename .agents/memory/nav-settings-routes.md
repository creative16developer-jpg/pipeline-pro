---
name: Nav + settings route structure
description: New sidebar navigation layout, all settings sub-routes, and stub page file locations.
---

## Sidebar layout (Layout.tsx)

Top: Dashboard (/)

Section — PIPELINES:
- All Runs → /pipelines  (exact match)
- Products → /pipelines/products

Section — SETTINGS (collapsible, auto-expands when any settings/* or /content route is active):

  Group: Connections
  - Stores → /settings/stores
  - AI Provider Keys → /settings/ai-keys

  Group: Mapping & Rules
  - Sunsky Categories → /settings/sunsky-categories
  - WooCommerce Categories → /settings/woo-categories
  - Category Mapping → /settings/category-mapping
  - Attribute Mapping → /settings/attribute-mapping
  - Attribute Profiles → /settings/attribute-profiles
  - Extraction Rules → /settings/extraction-rules
  - Inventory Mapping → /settings/inventory-mapping

  Group: Pipeline Defaults
  - Content Generation → /content  (existing ContentGeneration page)
  - Images → /settings/images
  - Pipeline Options → /settings/pipeline-defaults

## Stub page locations

All in `artifacts/dashboard/src/pages/settings/`:
- SunskyCategories.tsx, WooCategories.tsx, CategoryMapping.tsx
- AttributeMapping.tsx, AttributeProfiles.tsx, ExtractionRules.tsx
- InventoryMapping.tsx, ImagesSettings.tsx, PipelineDefaults.tsx

## App.tsx route registrations

New pipeline routes: /pipelines/new → Pipeline, /pipelines/products → Products
Settings routes: all /settings/* paths registered as well as legacy /stores, /pipeline, /content etc.

**Why:** Client's user guide specified this exact nav structure. Settings section is collapsible to avoid sidebar overflow.
**How to apply:** When adding new settings pages, add the route to App.tsx and a SubNavItem entry in SETTINGS_GROUPS in Layout.tsx.
