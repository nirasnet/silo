"""Silo platform configuration via environment / .env file."""

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # Database
    database_url: str = "sqlite:///data/silo.db"

    # AI providers
    anthropic_api_key: str = ""
    ai_model: str = "claude-haiku-4-5-20251001"
    gemini_api_key: str = ""

    # Auth
    secret_key: str = "change-me-in-production"
    web_password: str = "silo2026"
    api_keys: list[str] = ["silo-default-key"]

    # LINE Official Account (default OA)
    line_channel_secret: str = ""
    line_channel_token: str = ""

    # OCR service
    ocr_url: str = "http://ocr-service:9091"

    # Platform
    domain: str = "silo.m4app.online"
    host: str = "0.0.0.0"
    port: int = 8000

    # Digest scheduler
    digest_hour: int = 23  # run daily digest at 23:00 ICT

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


settings = Settings()
