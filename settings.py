import os
from typing import Optional

from pydantic_settings import BaseSettings
from pydantic import Field

class Settings(BaseSettings):
    APP_NAME: str = "PixiePinks Ledger"

    # Read DATABASE_URL from environment (Railway sets this automatically)
    DATABASE_URL: str = os.getenv("DATABASE_URL", "sqlite:///./ledger.db")

    # Fix for SQLAlchemy when Railway gives "postgres://"
    if DATABASE_URL.startswith("postgres://"):
        DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

    # Enable only AFTER exclusive ingress isolation and edge sanitation are verified.
    PRINT_AGENT_RAILWAY_ISOLATED_INGRESS_ENABLED: bool = False
    # Legacy exact-peer policy for other deployments with verifiable proxy IPs.
    PRINT_AGENT_TRUSTED_PROXY_IPS: str = ""
    PRINT_AGENT_PUBLIC_ORIGIN: str = ""
    POS_LOCAL_PRINT_ENABLED: bool = False
    POS_CASH_DRAWER_AUTOMATION_ENABLED: bool = False
    RETAIL_HARDWARE_LABEL_PRINT_ENABLED: bool = False
    POS_ENABLED: bool = False
    POS_CHECKOUT_WRITE_ENABLED: bool = False
    POS_REFUND_WRITE_ENABLED: bool = False
    RETAIL_ENABLED: bool = False
    INVENTORY_ENABLED: bool = False
    SHOPIFY_INVENTORY_WRITE_ENABLED: bool = False
    SHOPIFY_PRODUCT_WRITE_ENABLED: bool = False
    RETAIL_BARCODE_SYNC_ENABLED: bool = False
    RETAIL_BARCODE_SYNC_BATCH_LIMIT: int = Field(5, ge=1, le=5)
    RETAIL_LABEL_DIR: str = "/data/retail-labels"
    RETAIL_MAX_LABELS: int = Field(5000, ge=1, le=10000)
    RETAIL_MAX_VARIANTS: int = Field(50000, ge=1500, le=100000)
    CURRENCY: str = "LKR"
    SECRET_KEY: str = os.getenv("SECRET_KEY", "change-this-in-railway")
    # Local fallback only. Set META_VERIFY_TOKEN in Railway for production.
    META_VERIFY_TOKEN: str = os.getenv(
        "META_VERIFY_TOKEN", "change-this-local-meta-verify-token"
    )
    META_WHATSAPP_ACCESS_TOKEN: Optional[str] = None
    META_WHATSAPP_PHONE_NUMBER_ID: Optional[str] = None
    META_GRAPH_API_VERSION: str = "v26.0"
    WHATSAPP_OUTBOUND_IMAGE_MAX_BYTES: int = Field(5_000_000, ge=1024, le=5_000_000)
    WHATSAPP_OUTBOUND_VIDEO_MAX_BYTES: int = Field(16_000_000, ge=1024, le=16_000_000)
    WHATSAPP_OUTBOUND_MEDIA_SOURCE_MAX_BYTES: int = Field(64 * 1024 * 1024, ge=1024, le=64 * 1024 * 1024)
    WHATSAPP_OUTBOUND_DOCUMENT_MAX_BYTES: int = Field(8 * 1024 * 1024, ge=1024, le=16_000_000)
    META_APP_SECRET: Optional[str] = None
    SAVED_VOICES_DIR: str = "/data/saved-voices"
    OPENAI_API_KEY: Optional[str] = None
    LEARNING_ENABLED: bool = False
    LEARNING_RETRIEVAL_ENABLED: bool = False
    LEARNING_PUBLICATION_ENABLED: bool = False
    LEARNING_AUTOPUBLISH_ENABLED: bool = False
    OPENAI_MODEL: str = "gpt-4.1-mini"
    SHOPIFY_SHOP: Optional[str] = None
    SHOPIFY_CLIENT_ID: Optional[str] = None
    SHOPIFY_CLIENT_SECRET: Optional[str] = None
    SHOPIFY_WEBHOOK_SECRET: Optional[str] = None
    SHOPIFY_API_VERSION: str = "2026-07"
    SALES_ORDERS_ENABLED: bool = False
    SHOPIFY_ORDER_SYNC_ENABLED: bool = False
    SHOPIFY_ORDER_LIFECYCLE_SYNC_ENABLED: bool = False
    SALES_PAYMENT_TRACKING_ENABLED: bool = False
    SHOPIFY_PAYMENT_SYNC_ENABLED: bool = False
    SALES_FULFILLMENT_ENABLED: bool = False
    SHOPIFY_FULFILLMENT_SYNC_ENABLED: bool = False
    SALES_DELIVERY_TRACKING_ENABLED: bool = False
    SALES_ACCOUNTING_AUTO_POST_ENABLED: bool = False


settings = Settings()
