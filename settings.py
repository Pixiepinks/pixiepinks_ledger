from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    APP_NAME: str = "PixiePinks Ledger"
    DATABASE_URL: str = "sqlite:///./ledger.db"
    CURRENCY: str = "LKR"

settings = Settings()