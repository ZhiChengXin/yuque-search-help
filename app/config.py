from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    yuque_username: str
    yuque_password: str
    yuque_login: str = "fumenggonggongzhanghao"
    yuque_repo: str = "eggylu"
    yuque_host: str = "https://www.yuque.com"

    host: str = "0.0.0.0"
    port: int = 8787
    sync_interval_seconds: int = 600
    app_title: str = "孚盟MX 智能检索助手"


@lru_cache
def get_settings() -> Settings:
    return Settings()
