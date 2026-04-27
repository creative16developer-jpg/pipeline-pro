from pydantic_settings import BaseSettings
from functools import lru_cache


class Settings(BaseSettings):
    database_url: str = ""
    sunsky_api_key: str = "TESTKEY"
    sunsky_api_secret: str = "TESTSECRET"
    sunsky_api_url: str = "https://open.sunsky-online.com/openapi"
    port: int = 8000
    cors_origins: list[str] = ["*"]
    # Public base URL of this server (e.g. https://xxxx.zrok.io)
    # Used to build public URLs for processed images so WooCommerce can sideload them.
    # Leave empty if you are using wp_username + wp_app_password for direct WP media upload.
    server_base_url: str = ""

    model_config = {"env_file": ".env", "extra": "ignore"}


@lru_cache
def get_settings() -> Settings:
    return Settings()
