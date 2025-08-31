import os
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


settings = Settings()

