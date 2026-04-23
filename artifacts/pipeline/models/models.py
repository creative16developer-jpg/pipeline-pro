from sqlalchemy import (
    Column, Integer, String, Text, Boolean, Float, DateTime,
    ForeignKey, JSON, Enum as SAEnum
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


class Product(Base):
    __tablename__ = "products"

    id = Column(Integer, primary_key=True, index=True)
    sunsky_id = Column(String, nullable=False, unique=True)
    sku = Column(String, nullable=False)
    name = Column(String, nullable=False)
    description = Column(Text, nullable=True)
    price = Column(String, nullable=True)
    stock_status = Column(String, nullable=True)
    status = Column(SAEnum(ProductStatus, name="product_status"), nullable=False, default=ProductStatus.pending)
    category_id = Column(String, nullable=True)
    image_count = Column(Integer, nullable=False, default=0)
    woo_product_id = Column(Integer, nullable=True)
    error_message = Column(Text, nullable=True)
    raw_data = Column(JSON, nullable=True)

    # Which fetch job created this product — used to scope process/upload jobs
    fetch_job_id = Column(Integer, ForeignKey("jobs.id", ondelete="SET NULL"), nullable=True, index=True)

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

    # Links this job to a preceding job in the pipeline:
    #   process job → source is a fetch job
    #   upload job  → source is a process or fetch job
    source_job_id = Column(Integer, ForeignKey("jobs.id", ondelete="SET NULL"), nullable=True, index=True)

    started_at = Column(DateTime(timezone=True), nullable=True)
    completed_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    store = relationship("Store", back_populates="jobs")
    logs = relationship("JobLog", back_populates="job", cascade="all, delete-orphan")
    source_job = relationship("Job", foreign_keys=[source_job_id], remote_side="Job.id")


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


class JobLog(Base):
    __tablename__ = "job_logs"

    id = Column(Integer, primary_key=True, index=True)
    job_id = Column(Integer, ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False)
    level = Column(SAEnum(LogLevel, name="log_level"), nullable=False, default=LogLevel.info)
    message = Column(Text, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    job = relationship("Job", back_populates="logs")
