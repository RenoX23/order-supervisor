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

    # ── LLM (agent step) ──────────────────────────────────────────────────────
    # OpenAI-compatible endpoint. Defaults target Groq's free tier; point these at
    # OpenAI, Gemini's OpenAI endpoint, or a local Ollama server to switch provider
    # with no code change. The agent call lives entirely inside one activity.
    llm_base_url: str = "https://api.groq.com/openai/v1"
    llm_api_key: str = ""
    llm_model: str = "llama-3.3-70b-versatile"
    llm_temperature: float = 0.2
    llm_timeout_seconds: float = 30.0


settings = Settings()
