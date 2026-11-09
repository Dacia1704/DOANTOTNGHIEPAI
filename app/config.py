"""Cấu hình ứng dụng, đọc từ biến môi trường / file .env."""

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- DB ---
    core_db_url: str = "mysql+pymysql://ai_svc:ai_svc@localhost:3306/rental_db"
    ai_db_url: str = "mysql+pymysql://ai_svc:ai_svc@localhost:3306/rental_ai"

    # --- JWT (khớp với app.jwt.* của Spring) ---
    jwt_alg: str = "HS512"
    jwt_secret: str = ""
    jwt_issuer: str = "rental-management"
    auth_version_cache_seconds: int = 30
    cors_origins: list[str] = ["http://localhost:4200", "http://localhost"]

    # --- LLM local (Ollama) ---
    local_llm_base_url: str = "http://localhost:11434/v1"
    local_llm_model: str = "qwen3:4b-instruct-2507-q4_K_M"
    local_llm_api_key: str = "ollama"
    local_timeout_seconds: int = 90
    chat_nearby_radius_km: float = Field(default=3, ge=0.5, le=20)
    chat_price_tolerance_pct: int = Field(default=20, ge=0, le=50)

    # --- LLM ngoài (fallback) ---
    allow_external_fallback: bool = True
    external_provider: str = "gemini"
    external_model: str = "gemini-3.1-flash-lite"
    gemini_api_key: str = ""
    anthropic_api_key: str = ""
    external_daily_cap: int = 2000

    # --- Embedding ---
    embedding_model: str = "intfloat/multilingual-e5-small"
    embedding_dim: int = 384
    embedding_reload_seconds: int = 60

    # --- Giới hạn ---
    chat_daily_limit_per_user: int = 30
    max_concurrent_per_user: int = Field(default=3, ge=1)
    chat_timeout_seconds: int = Field(default=240, ge=1)
    quota_timezone: str = "Asia/Ho_Chi_Minh"


@lru_cache
def get_settings() -> Settings:
    return Settings()
