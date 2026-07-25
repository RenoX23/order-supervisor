"""Application settings, loaded from environment / the repo-root `.env` file.

A single `settings` object is the one source of truth for configuration across
the backend (API, Temporal worker, migration runner).
"""

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# config.py -> app -> backend -> <repo root>
ROOT_DIR = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(ROOT_DIR / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ── Database ──────────────────────────────────────────────────────────────
    database_url: str = "postgresql://supervisor:supervisor@localhost:5442/order_supervisor"

    # ── Temporal ──────────────────────────────────────────────────────────────
    temporal_address: str = "localhost:7233"
    temporal_namespace: str = "default"
    temporal_task_queue: str = "order-supervisor"

    # ── LLM (used from the agent stage onward) ────────────────────────────────
    anthropic_api_key: str = ""
    llm_model: str = ""


settings = Settings()
