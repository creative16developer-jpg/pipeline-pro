---
name: Dashboard pipeline stats
description: DashboardStats new fields, PipelineRunOut schema, and dashboard.py query pattern for pipeline-focused stats.
---

## Schema additions (schemas/schemas.py)

New model before DashboardStats:
```python
class PipelineRunOut(BaseModel):
    model_config = ConfigDict(from_attributes=True, populate_by_name=True, alias_generator=to_camel)
    id: int
    store_name: str
    status: str
    products_total: int = 0
    products_uploaded: int = 0
    products_failed: int = 0
    created_at: datetime
    is_waiting: bool = False
```

New fields added to DashboardStats (all optional with defaults for backward compat):
- active_pipelines: int = 0
- waiting_for_input: int = 0
- uploaded_30d: int = 0
- failed_30d: int = 0
- recent_pipeline_runs: list[PipelineRunOut] = []

## dashboard.py query

- active_pipelines: PipelineJob.status.in_([running, queued])
- waiting_for_input: PipelineJob.status.in_([review, enrich_review, category_review])
- uploaded_30d / failed_30d: Product.updated_at >= now-30d
- recent_pipeline_runs: JOIN PipelineJob+Store, SUM(Job totals) per pipeline

**Why:** Client's user guide specifies dashboard shows pipeline stats, not raw product counts.
**How to apply:** Frontend uses stats.activePipelines, stats.waitingForInput, stats.uploaded30d, stats.failed30d, stats.recentPipelineRuns (camelCase from alias_generator).
