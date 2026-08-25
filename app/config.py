from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    yuque_token: str = ""
    yuque_username: str = ""
    yuque_password: str = ""
    yuque_login: str = "wangyanling-xsdvq"
    yuque_repo: str = "vf1lt7"
    yuque_host: str = "https://www.yuque.com"

    yuque_feedback_login: str = "wangyanling-xsdvq"
    yuque_feedback_repo: str = "ezw77w"
    yuque_feedback_slug: str = "qr67p7rx343mvp9g"

    llm_api_key: str = ""
    llm_base_url: str = "https://openrouter.ai/api/v1"
    llm_model: str = "qwen/qwen3.7-plus"

    # 本地向量召回（文件缓存在 data/embeddings，原文仍在语雀）
    embedding_enabled: bool = True
    embedding_model: str = "qwen/qwen3-embedding-4b"
    data_dir: str = "data"

    # 语雀 Webhook：知识库「消息推送」填 http://你的域名:8787/api/yuque/webhook?token=密钥
    yuque_webhook_secret: str = ""
    yuque_webhook_debounce_seconds: float = 12.0

    host: str = "0.0.0.0"
    port: int = 8787
    sync_interval_seconds: int = 600
    app_title: str = "孚盟MX 智能检索助手"


@lru_cache
def get_settings() -> Settings:
    return Settings()
