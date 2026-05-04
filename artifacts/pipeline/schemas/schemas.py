from pydantic import BaseModel, ConfigDict, Field, AliasChoices
from pydantic.alias_generators import to_camel
from typing import Optional, Any
from datetime import datetime


class StoreCreate(BaseModel):
    name: str
    url: str
    consumer_key: str = Field(
        validation_alias=AliasChoices("consumer_key", "consumerKey")
    )
    consumer_secret: str = Field(
        validation_alias=AliasChoices("consumer_secret", "consumerSecret")
    )
    wp_username: Optional[str] = Field(
        None, validation_alias=AliasChoices("wp_username", "wpUsername")
    )
    wp_app_password: Optional[str] = Field(
        None, validation_alias=AliasChoices("wp_app_password", "wpAppPassword")
    )


class StoreUpdate(BaseModel):
    name: Optional[str] = None
    url: Optional[str] = None
    consumer_key: Optional[str] = Field(
        None, validation_alias=AliasChoices("consumer_key", "consumerKey")
    )
    consumer_secret: Optional[str] = Field(
        None, validation_alias=AliasChoices("consumer_secret", "consumerSecret")
    )
    wp_username: Optional[str] = Field(
        None, validation_alias=AliasChoices("wp_username", "wpUsername")
    )
    wp_app_password: Optional[str] = Field(
        None, validation_alias=AliasChoices("wp_app_password", "wpAppPassword")
    )


class StoreOut(BaseModel):
    model_config = ConfigDict(from_attributes=True, populate_by_name=True, alias_generator=to_camel)

    id: int
    name: str
    url: str
    consumer_key: str
    wp_username: Optional[str] = None
    status: str
    last_tested_at: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_orm_masked(cls, store):
        obj = cls.model_validate(store)
        key = store.consumer_key
        obj.consumer_key = key[:8] + "..." if len(key) > 8 else "***"
        return obj


class WooCategoryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True, populate_by_name=True, alias_generator=to_camel)

    id: int
    store_id: int
    woo_id: int
    name: str
    slug: str
    parent_id: Optional[int] = None
    count: int
    created_at: datetime


class ProductOut(BaseModel):
    model_config = ConfigDict(from_attributes=True, populate_by_name=True, alias_generator=to_camel)

    id: int
    sunsky_id: str
    sku: str
    name: str
    description: Optional[str] = None
    price: Optional[str] = None
    stock_status: Optional[str] = None
    status: str
    category_id: Optional[str] = None
    image_count: int
    woo_product_id: Optional[int] = None
    error_message: Optional[str] = None
    raw_data: Optional[Any] = None
    fetch_job_id: Optional[int] = None
    created_at: datetime
    updated_at: datetime


class ProductListOut(BaseModel):
    model_config = ConfigDict(populate_by_name=True, alias_generator=to_camel)

    products: list[ProductOut]
    total: int
    page: int
    limit: int
    total_pages: int


class JobCreate(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    type: str
    store_id: Optional[int] = Field(
        None, validation_alias=AliasChoices("store_id", "storeId")
    )
    config: Optional[dict] = None
    # Link to the preceding job in the pipeline
    # e.g. process job points to a fetch job_id; upload job points to a process job_id
    source_job_id: Optional[int] = Field(
        None, validation_alias=AliasChoices("source_job_id", "sourceJobId")
    )


class JobOut(BaseModel):
    model_config = ConfigDict(from_attributes=True, populate_by_name=True, alias_generator=to_camel)

    id: int
    type: str
    status: str
    store_id: Optional[int] = None
    total_items: int
    processed_items: int
    failed_items: int
    progress_percent: float
    error_message: Optional[str] = None
    config: Optional[Any] = None
    source_job_id: Optional[int] = None
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    created_at: datetime


class JobListOut(BaseModel):
    model_config = ConfigDict(populate_by_name=True, alias_generator=to_camel)

    jobs: list[JobOut]
    total: int
    page: int
    limit: int
    total_pages: int


class DashboardStats(BaseModel):
    model_config = ConfigDict(populate_by_name=True, alias_generator=to_camel)

    total_products: int
    pending_products: int
    processing_products: int
    processed_products: int
    uploaded_products: int
    failed_products: int
    active_jobs: int
    total_stores: int
    recent_jobs: list[JobOut]


class SunskyFetchRequest(BaseModel):
    category_id: Optional[str] = None
    keyword: Optional[str] = None
    page: int = 1
    limit: int = 50
    store_id: Optional[int] = None


class SunskyFetchResult(BaseModel):
    fetched: int
    saved: int
    skipped: int
    job_id: int


class SunskyCategoryOut(BaseModel):
    id: str
    name: str
    parent_id: Optional[str] = None
