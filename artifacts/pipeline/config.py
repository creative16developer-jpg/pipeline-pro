from pydantic_settings import BaseSettings
from functools import lru_cache


class Settings(BaseSettings):
    database_url: str = ""
    sunsky_api_key: str = "TESTKEY"
    sunsky_api_secret: str = "TESTSECRET"
    sunsky_api_url: str = "https://www.sunsky-online.com/api"
    port: int = 8000
    cors_origins: list[str] = ["*"]

    model_config = {"env_file": ".env", "extra": "ignore"}


@lru_cache
def get_settings() -> Settings:
    return Settings()
