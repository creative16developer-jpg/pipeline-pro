from sqlalchemy import (
    Column, Integer, String, Text, Boolean, Float, DateTime,
    ForeignKey, JSON, Enum as SAEnum, UniqueConstraint
)
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from database import Base
import enum


class StoreStatus(str, enum.Enum):
    active = "active"
    inactive = "inactive"
    error = "error"


class ProductStatus(str, enum.Enum):
    pending = "pending"
    processing = "processing"
    processed = "processed"
    uploaded = "uploaded"
    failed = "failed"


class JobType(str, enum.Enum):
    fetch = "fetch"
    process = "process"
    upload = "upload"
    sync = "sync"
    csv_import = "csv_import"


class JobStatus(str, enum.Enum):
    pending = "pending"
    running = "running"
    completed = "completed"
    failed = "failed"
    cancelled = "cancelled"


class ImageStatus(str, enum.Enum):
    pending = "pending"
    downloaded = "downloaded"
    compressed = "compressed"
    watermarked = "watermarked"
    uploaded = "uploaded"
    failed = "failed"


class LogLevel(str, enum.Enum):
    info = "info"
    warn = "warn"
    error = "error"
    debug = "debug"


class Store(Base):
    __tablename__ = "stores"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    url = Column(String, nullable=False)
    consumer_key = Column(String, nullable=False)
    consumer_secret = Column(String, nullable=False)
    wp_username = Column(String, nullable=True)
    wp_app_password = Column(String, nullable=True)
    status = Column(SAEnum(StoreStatus, name="store_status"), nullable=False, default=StoreStatus.inactive)
    last_tested_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    jobs = relationship("Job", back_populates="store")
    categories = relationship("WooCategory", back_populates="store", cascade="all, delete-orphan")
    pipeline_jobs = relationship("PipelineJob", back_populates="store", cascade="all, delete-orphan")


class Product(Base):
    __tablename__ = "products"

    id = Column(Integer, primary_key=True, index=True)
    sunsky_id = Column(String, nullable=False, unique=True)
    sku = Column(String, nullable=False)
    name = Column(String, nullable=False)
    description = Column(Text, nullable=True)
    short_description = Column(Text, nullable=True)
    slug = Column(String, nullable=True)
    meta_title = Column(String, nullable=True)
    meta_description = Column(Text, nullable=True)
    tags = Column(Text, nullable=True)
    image_alt = Column(Text, nullable=True)
    image_names = Column(Text, nullable=True)
    # Tracks source of each field: {"description": "ai:openai", "short_description": "logic", ...}
    content_source = Column(JSON, nullable=True)
    price = Column(String, nullable=True)
    stock_status = Column(String, nullable=True)
    status = Column(SAEnum(ProductStatus, name="product_status"), nullable=False, default=ProductStatus.pending)
    category_id = Column(String, nullable=True)
    image_count = Column(Integer, nullable=False, default=0)
    woo_product_id = Column(Integer, nullable=True)
    error_message = Column(Text, nullable=True)
    raw_data = Column(JSON, nullable=True)

    fetch_job_id = Column(Integer, ForeignKey("jobs.id", ondelete="SET NULL"), nullable=True, index=True)

    # Manual product-level WooCommerce category override (never overwritten by batch rules)
    manual_woo_cats_json      = Column(Text, nullable=True)   # JSON: [{id, name}, ...]
    manual_primary_woo_cat_id = Column(Integer, nullable=True)
    cat_source                = Column(String(20), nullable=False, default="auto")  # 'auto' | 'manual'

    # CSV Import fields — set when a CSV mapping file is uploaded before pipeline
    csv_title = Column(String(200), nullable=True)
    site_sku  = Column(String(100), nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    images = relationship("Image", back_populates="product", cascade="all, delete-orphan")
    fetch_job = relationship("Job", foreign_keys=[fetch_job_id])


class Job(Base):
    __tablename__ = "jobs"

    id = Column(Integer, primary_key=True, index=True)
    type = Column(SAEnum(JobType, name="job_type"), nullable=False)
    status = Column(SAEnum(JobStatus, name="job_status"), nullable=False, default=JobStatus.pending)
    store_id = Column(Integer, ForeignKey("stores.id"), nullable=True)
    total_items = Column(Integer, nullable=False, default=0)
    processed_items = Column(Integer, nullable=False, default=0)
    failed_items = Column(Integer, nullable=False, default=0)
    progress_percent = Column(Float, nullable=False, default=0.0)
    error_message = Column(Text, nullable=True)
    config = Column(JSON, nullable=True)

    source_job_id = Column(Integer, ForeignKey("jobs.id", ondelete="SET NULL"), nullable=True, index=True)

    # Which pipeline run owns this step job (nullable for standalone jobs)
    pipeline_job_id = Column(
        Integer,
        ForeignKey("pipeline_jobs.id", ondelete="SET NULL", use_alter=True, name="fk_jobs_pipeline_job_id"),
        nullable=True,
        index=True,
    )

    started_at = Column(DateTime(timezone=True), nullable=True)
    completed_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    store = relationship("Store", back_populates="jobs")
    logs = relationship("JobLog", back_populates="job", cascade="all, delete-orphan")
    source_job = relationship("Job", foreign_keys=[source_job_id], remote_side="Job.id")


class PipelineJobStatus(str, enum.Enum):
    queued          = "queued"
    running         = "running"
    review          = "review"
    enrich_review   = "enrich_review"
    category_review = "category_review"
    completed       = "completed"
    failed          = "failed"
    cancelled       = "cancelled"


class PipelineJob(Base):
    """
    Represents one full pipeline run:
    Fetch → Process → [Enrich] → [Generate] → [Category Review pause] → [Review pause] → Upload → Sync

    The `fetch_job_id` points to the fetch job whose products this pipeline
    will process.  Multiple pipelines can share the same store but only ONE
    may be in status='running' or 'review' or 'category_review' per store at
    a time — others are queued and auto-started when the current one finishes.
    """
    __tablename__ = "pipeline_jobs"

    id = Column(Integer, primary_key=True, index=True)
    store_id = Column(Integer, ForeignKey("stores.id", ondelete="CASCADE"), nullable=False, index=True)
    fetch_job_id = Column(Integer, ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False, index=True)

    status = Column(
        SAEnum(PipelineJobStatus, name="pipeline_job_status"),
        nullable=False,
        default=PipelineJobStatus.queued,
        index=True,
    )
    # Current execution step: process | generate | review | upload | sync
    current_step = Column(String(30), nullable=True)

    config = Column(JSON, nullable=True)
    # Content-gen review stats: {total, ok, fallback, failed}
    stats_json = Column(JSON, nullable=True)
    error_message = Column(Text, nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    store = relationship("Store", back_populates="pipeline_jobs")
    fetch_job = relationship("Job", foreign_keys=[fetch_job_id])
    logs = relationship("PipelineLog", back_populates="pipeline_job",
                        cascade="all, delete-orphan", order_by="PipelineLog.created_at")


class PipelineLog(Base):
    __tablename__ = "pipeline_logs"

    id = Column(Integer, primary_key=True, index=True)
    pipeline_job_id = Column(Integer, ForeignKey("pipeline_jobs.id", ondelete="CASCADE"),
                             nullable=False, index=True)
    step = Column(String(50), nullable=True)
    level = Column(String(20), nullable=False, default="info")
    message = Column(Text, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    pipeline_job = relationship("PipelineJob", back_populates="logs")


class Image(Base):
    __tablename__ = "images"

    id = Column(Integer, primary_key=True, index=True)
    product_id = Column(Integer, ForeignKey("products.id", ondelete="CASCADE"), nullable=False)
    original_url = Column(String, nullable=False)
    local_path = Column(String, nullable=True)
    processed_path = Column(String, nullable=True)
    woo_image_id = Column(Integer, nullable=True)
    position = Column(Integer, nullable=False, default=0)
    status = Column(SAEnum(ImageStatus, name="image_status"), nullable=False, default=ImageStatus.pending)
    is_main = Column(Boolean, nullable=False, default=False)
    error_message = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    product = relationship("Product", back_populates="images")


class CsvMapping(Base):
    """
    Stores SKU → title/site-SKU mappings uploaded via CSV before a pipeline run.
    The sunsky_sku column matches Product.sku (the Sunsky product SKU).
    """
    __tablename__ = "csv_mappings"

    id         = Column(Integer, primary_key=True, index=True)
    sunsky_sku = Column(String(100), nullable=False, unique=True, index=True)
    site_sku   = Column(String(100), nullable=True)
    csv_title  = Column(String(200), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class WooCategory(Base):
    __tablename__ = "woo_categories"

    id = Column(Integer, primary_key=True, index=True)
    store_id = Column(Integer, ForeignKey("stores.id", ondelete="CASCADE"), nullable=False)
    woo_id = Column(Integer, nullable=False)
    name = Column(String, nullable=False)
    slug = Column(String, nullable=False)
    parent_id = Column(Integer, nullable=True)
    count = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    store = relationship("Store", back_populates="categories")


# ─────────────────────────────────────────────────────────────────────────────
# WooCommerce product attributes + terms (synced cache)
# ─────────────────────────────────────────────────────────────────────────────

class WooAttribute(Base):
    """WooCommerce product attribute synced from the store (e.g. pa_color → Color)."""
    __tablename__ = "woo_attributes"
    __table_args__ = (UniqueConstraint("store_id", "woo_id"),)

    id         = Column(Integer, primary_key=True, index=True)
    store_id   = Column(Integer, ForeignKey("stores.id", ondelete="CASCADE"), nullable=False, index=True)
    woo_id     = Column(Integer, nullable=False)
    name       = Column(String(200), nullable=False)
    slug       = Column(String(200), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    store = relationship("Store")
    terms = relationship(
        "WooAttributeTerm", back_populates="attribute",
        cascade="all, delete-orphan",
        lazy="selectin",
    )


class WooAttributeTerm(Base):
    """One allowed value (term) for a WooCommerce product attribute."""
    __tablename__ = "woo_attribute_terms"
    __table_args__ = (UniqueConstraint("attribute_id", "woo_id"),)

    id           = Column(Integer, primary_key=True, index=True)
    attribute_id = Column(Integer, ForeignKey("woo_attributes.id", ondelete="CASCADE"), nullable=False, index=True)
    store_id     = Column(Integer, ForeignKey("stores.id", ondelete="CASCADE"), nullable=False, index=True)
    woo_id       = Column(Integer, nullable=False)
    name         = Column(String(200), nullable=False)
    slug         = Column(String(200), nullable=False)

    attribute = relationship("WooAttribute", back_populates="terms")


class JobLog(Base):
    __tablename__ = "job_logs"

    id = Column(Integer, primary_key=True, index=True)
    job_id = Column(Integer, ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False)
    level = Column(SAEnum(LogLevel, name="log_level"), nullable=False, default=LogLevel.info)
    message = Column(Text, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    job = relationship("Job", back_populates="logs")


# ─────────────────────────────────────────────────────────────────────────────
# Map step
# ─────────────────────────────────────────────────────────────────────────────

class SunskyCategoryMapping(Base):
    """Persistent Sunsky-category → WooCommerce-category mapping per store."""
    __tablename__ = "sunsky_category_mappings"
    __table_args__ = (UniqueConstraint("store_id", "sunsky_cat"),)

    id                 = Column(Integer, primary_key=True, index=True)
    store_id           = Column(Integer, ForeignKey("stores.id", ondelete="CASCADE"), nullable=False, index=True)
    sunsky_cat         = Column(Text, nullable=False)
    woo_cat_id         = Column(Integer, nullable=True)   # backward-compat: primary cat id
    woo_cat_name       = Column(Text, nullable=True)      # backward-compat: primary cat name
    woo_cats_json      = Column(Text, nullable=True)      # JSON: [{id, name}, ...]
    primary_woo_cat_id = Column(Integer, nullable=True)
    # Attribute profile assigned to products in this Sunsky category
    profile_id         = Column(Integer, ForeignKey("attribute_profiles.id", ondelete="SET NULL"), nullable=True)
    times_used         = Column(Integer, nullable=False, default=0)
    last_used_at       = Column(DateTime(timezone=True), nullable=True)
    created_at         = Column(DateTime(timezone=True), server_default=func.now())
    updated_at         = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    store   = relationship("Store")
    profile = relationship("AttributeProfile")


# ─────────────────────────────────────────────────────────────────────────────
# Enrich step
# ─────────────────────────────────────────────────────────────────────────────

class ProductEnrichAttr(Base):
    """AI-extracted attribute per product per pipeline run."""
    __tablename__ = "product_enrich_attrs"
    __table_args__ = (UniqueConstraint("pipeline_job_id", "product_id", "attribute"),)

    id               = Column(Integer, primary_key=True, index=True)
    pipeline_job_id  = Column(Integer, ForeignKey("pipeline_jobs.id", ondelete="CASCADE"), nullable=False, index=True)
    product_id       = Column(Integer, ForeignKey("products.id",      ondelete="CASCADE"), nullable=False, index=True)
    attribute        = Column(Text, nullable=False)
    raw_value        = Column(Text, nullable=False)
    normalised_value = Column(Text, nullable=True)
    woo_attr_name    = Column(Text, nullable=True)   # override WooCommerce attribute name
    confidence       = Column(Float, nullable=True)
    confirmed        = Column(Boolean, nullable=False, default=False)
    # "ai" | "rule_based" | "default" | "manual"
    source           = Column(String(20), nullable=False, default="ai")
    # True when confidence < rule threshold
    flagged          = Column(Boolean, nullable=False, default=False)
    created_at       = Column(DateTime(timezone=True), server_default=func.now())

    product      = relationship("Product")
    pipeline_job = relationship("PipelineJob")


class NormalisationDict(Base):
    """Persistent raw-value → WooCommerce-term mapping per store per attribute."""
    __tablename__ = "normalisation_dict"
    __table_args__ = (UniqueConstraint("store_id", "attribute", "raw_value"),)

    id            = Column(Integer, primary_key=True, index=True)
    store_id      = Column(Integer, ForeignKey("stores.id", ondelete="CASCADE"), nullable=False, index=True)
    attribute     = Column(Text, nullable=False)
    raw_value     = Column(Text, nullable=False)
    woo_term      = Column(Text, nullable=False)
    woo_attr_name = Column(Text, nullable=True)   # override WooCommerce attribute name
    created_at    = Column(DateTime(timezone=True), server_default=func.now())

    store = relationship("Store")


class VariantGroup(Base):
    """AI-suggested variant group (SKUs that form one WooCommerce variable product)."""
    __tablename__ = "variant_groups"

    id              = Column(Integer, primary_key=True, index=True)
    pipeline_job_id = Column(Integer, ForeignKey("pipeline_jobs.id", ondelete="CASCADE"), nullable=False, index=True)
    attribute       = Column(Text, nullable=False)
    product_ids     = Column(JSON, nullable=False, default=list)
    confirmed       = Column(Boolean, nullable=False, default=False)
    pattern         = Column(Text, nullable=True)
    created_at      = Column(DateTime(timezone=True), server_default=func.now())

    pipeline_job = relationship("PipelineJob")


# ─────────────────────────────────────────────────────────────────────────────
# Attribute Profiles  (defined before SunskyCategoryMapping — FK dependency)
# ─────────────────────────────────────────────────────────────────────────────

class AttributeProfile(Base):
    """Named set of WooCommerce attributes expected for a product category."""
    __tablename__ = "attribute_profiles"

    id          = Column(Integer, primary_key=True, index=True)
    name        = Column(Text, nullable=False, unique=True)
    description = Column(Text, nullable=True)
    created_at  = Column(DateTime(timezone=True), server_default=func.now())
    updated_at  = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    attributes = relationship(
        "ProfileAttribute", back_populates="profile",
        cascade="all, delete-orphan", order_by="ProfileAttribute.sort_order"
    )


class ProfileAttribute(Base):
    """One WooCommerce attribute slot within an AttributeProfile."""
    __tablename__ = "profile_attributes"
    __table_args__ = (UniqueConstraint("profile_id", "woo_attr_name"),)

    id            = Column(Integer, primary_key=True, index=True)
    profile_id    = Column(Integer, ForeignKey("attribute_profiles.id", ondelete="CASCADE"),
                           nullable=False, index=True)
    woo_attr_name = Column(Text, nullable=False)
    required      = Column(Boolean, nullable=False, default=True)
    sort_order    = Column(Integer, nullable=False, default=0)
    created_at    = Column(DateTime(timezone=True), server_default=func.now())

    profile = relationship("AttributeProfile", back_populates="attributes")


# ─────────────────────────────────────────────────────────────────────────────
# AI Extraction Rules
# ─────────────────────────────────────────────────────────────────────────────

class AIExtractionRule(Base):
    """
    Per-attribute rule controlling how AI extracts a WooCommerce attribute value.
    source_fields: "title" | "specs" | "both"
    if_not_found:  "leave_blank" | "flag" | "use_default"
    """
    __tablename__ = "ai_extraction_rules"

    id                   = Column(Integer, primary_key=True, index=True)
    woo_attr_name        = Column(Text, nullable=False, unique=True)
    source_fields        = Column(String(20), nullable=False, default="both")
    instruction          = Column(Text, nullable=False, default="")
    confidence_threshold = Column(Float, nullable=False, default=0.7)
    if_not_found         = Column(String(30), nullable=False, default="flag")
    default_value        = Column(Text, nullable=True)
    sort_order           = Column(Integer, nullable=False, default=0)
    created_at           = Column(DateTime(timezone=True), server_default=func.now())
    updated_at           = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


# ─────────────────────────────────────────────────────────────────────────────
# Inventory Mapping Config (per store)
# ─────────────────────────────────────────────────────────────────────────────

class InventoryMappingConfig(Base):
    """Per-store config for mapping Sunsky inventory/weight/dimension fields to WooCommerce."""
    __tablename__ = "inventory_mapping_configs"

    id             = Column(Integer, primary_key=True, index=True)
    store_id       = Column(Integer, ForeignKey("stores.id", ondelete="CASCADE"),
                            nullable=False, unique=True, index=True)
    weight_unit    = Column(String(10), nullable=False, default="kg")
    dimension_unit = Column(String(10), nullable=False, default="cm")
    weight_null    = Column(String(20), nullable=False, default="leave_blank")
    length_null    = Column(String(20), nullable=False, default="leave_blank")
    width_null     = Column(String(20), nullable=False, default="leave_blank")
    height_null    = Column(String(20), nullable=False, default="leave_blank")
    weight_default    = Column(String(30), nullable=True)
    length_default    = Column(String(30), nullable=True)
    width_default     = Column(String(30), nullable=True)
    height_default    = Column(String(30), nullable=True)
    created_at     = Column(DateTime(timezone=True), server_default=func.now())
    updated_at     = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    store = relationship("Store")
