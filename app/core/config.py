from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    database_url: str = Field(min_length=1)
    jwt_secret_key: SecretStr = Field(min_length=32)
    jwt_algorithm: Literal["HS256"] = "HS256"
    access_token_expire_minutes: int = Field(default=15, gt=0)
    refresh_token_expire_days: int = Field(default=7, gt=0)

    google_client_id: str = ""
    google_client_secret: SecretStr = SecretStr("")
    google_redirect_uri: str = "http://127.0.0.1:8000/api/v1/auth/google/callback"

    smtp_host: str = ""
    smtp_port: int = Field(default=587, gt=0)
    smtp_username: str = ""
    smtp_password: SecretStr = SecretStr("")
    email_from: str = ""

    frontend_url: str = "http://127.0.0.1:3000"
    password_reset_expire_minutes: int = Field(default=30, gt=0)
    email_verification_expire_minutes: int = Field(default=60, gt=0)
    cookie_secure: bool = False
    storage_backend: Literal["local", "cloudinary"] = "local"
    cloudinary_cloud_name: str = ""
    cloudinary_api_key: str = ""
    cloudinary_api_secret: SecretStr = SecretStr("")
    upload_dir: Path = Path("uploads")
    max_upload_mb: int = Field(default=10, gt=0)
    summary_max_chars: int = Field(default=1000, gt=0)
    quiz_question_text_max_chars: int = Field(default=1000, gt=0)
    quiz_options_min_count: int = Field(default=2, ge=1)
    quiz_options_max_count: int = Field(default=6, ge=1)
    quiz_option_max_chars: int = Field(default=300, gt=0)
    quiz_explanation_max_chars: int = Field(default=1500, gt=0)
    quiz_questions_min_count: int = Field(default=1, ge=1)
    quiz_questions_max_count: int = Field(default=30, ge=1)
    n8n_base_url: str = ""
    n8n_webhook_secret: SecretStr = SecretStr("")
    n8n_timeout_seconds: int = Field(default=90, gt=0)
    ai_max_input_chars: int = Field(default=30000, gt=0)
    ai_daily_limit_free: int = Field(default=3, ge=0)
    ai_daily_limit_plus: int = Field(default=30, ge=0)
    ai_chat_daily_limit_free: int = Field(default=50, ge=0)
    ai_chat_daily_limit_plus: int = Field(default=500, ge=0)
    chat_history_limit: int = Field(default=10, gt=0)
    chat_top_k: int = Field(default=4, gt=0)
    chat_max_note_ids: int = Field(default=200, gt=0)
    ai_summary_max_cards: int = Field(default=15, ge=1, le=50)
    ai_card_target_chars: int = Field(default=400, gt=0)
    ai_output_language: str = "tr"
    index_max_input_chars: int = Field(default=20000, gt=0)


@lru_cache
def get_settings() -> Settings:
    return Settings()
