import os
from typing import Optional

from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    APP_NAME: str = "PixiePinks Ledger"

    # Read DATABASE_URL from environment (Railway sets this automatically)
    DATABASE_URL: str = os.getenv("DATABASE_URL", "sqlite:///./ledger.db")

    # Fix for SQLAlchemy when Railway gives "postgres://"
    if DATABASE_URL.startswith("postgres://"):
        DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

    CURRENCY: str = "LKR"
    SECRET_KEY: str = os.getenv("SECRET_KEY", "change-this-in-railway")
    # Local fallback only. Set META_VERIFY_TOKEN in Railway for production.
    META_VERIFY_TOKEN: str = os.getenv(
        "META_VERIFY_TOKEN", "change-this-local-meta-verify-token"
    )
    META_WHATSAPP_ACCESS_TOKEN: Optional[str] = None
    META_WHATSAPP_PHONE_NUMBER_ID: Optional[str] = None
    META_GRAPH_API_VERSION: str = "v26.0"
    META_APP_SECRET: Optional[str] = None
    OPENAI_API_KEY: Optional[str] = None
    OPENAI_MODEL: str = "gpt-4.1-mini"
    SHOPIFY_SHOP: Optional[str] = None
    SHOPIFY_CLIENT_ID: Optional[str] = None
    SHOPIFY_CLIENT_SECRET: Optional[str] = None
    SHOPIFY_API_VERSION: str = "2026-07"


settings = Settings()
