from dataclasses import dataclass, field
import os
from pathlib import Path

try:
    from dotenv import load_dotenv
except Exception:  # pragma: no cover - optional during early setup
    load_dotenv = None


BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"

if load_dotenv:
    load_dotenv(BASE_DIR / ".env")


@dataclass(frozen=True)
class Settings:
    app_name: str = "Indian Options Research Desk"
    database_path: Path = DATA_DIR / "research.db"

    # PostgreSQL (production) — empty = use SQLite fallback
    database_url: str = os.getenv("DATABASE_URL", "")

    # Redis cache — empty = NullCache fallback
    redis_url: str = os.getenv("REDIS_URL", "")

    # Structured JSON logging (production)
    json_logs: bool = os.getenv("JSON_LOGS", "false").lower() == "true"
    log_level: str  = os.getenv("LOG_LEVEL", "INFO")

    # Standard OpenAI (used only when Azure fields are not set)
    openai_api_key: str = os.getenv("OPENAI_API_KEY", "")
    openai_model: str   = os.getenv("OPENAI_MODEL", "gpt-4o-mini")

    # Azure OpenAI (takes priority over standard OpenAI when all three are set)
    azure_openai_api_key:    str = os.getenv("AZURE_OPENAI_API_KEY", "")
    azure_openai_endpoint:   str = os.getenv("AZURE_OPENAI_ENDPOINT", "")
    azure_openai_deployment: str = os.getenv("AZURE_OPENAI_DEPLOYMENT", "")
    azure_openai_api_version: str = os.getenv("AZURE_OPENAI_API_VERSION", "2025-01-01-preview")

    # Kite Connect (Zerodha)
    kite_api_key:    str = os.getenv("KITE_API_KEY",    "")
    kite_api_secret: str = os.getenv("KITE_API_SECRET", "")

    news_api_key: str       = os.getenv("NEWS_API_KEY", "")

    telegram_bot_token: str = os.getenv("TELEGRAM_BOT_TOKEN", "")
    telegram_chat_id: str   = os.getenv("TELEGRAM_CHAT_ID", "")
    enable_scheduler: bool  = os.getenv("ENABLE_SCHEDULER", "false").lower() == "true"

    def to_dependency_settings(self) -> dict:
        """Convert settings to the dict format expected by api.dependencies.init_dependencies()."""
        return {
            "redis_url":         self.redis_url,
            "database_url":      self.database_url or f"sqlite:///{self.database_path}",
            "accountCapital":    100_000,
            "riskPercent":       2.0,
            "telegram_token":    self.telegram_bot_token,
            "kite": {
                "api_key":    self.kite_api_key,
                "api_secret": self.kite_api_secret,
            },
            "azure_openai": {
                "api_key":    self.azure_openai_api_key,
                "endpoint":   self.azure_openai_endpoint,
                "deployment": self.azure_openai_deployment,
                "api_version": self.azure_openai_api_version,
            },
            "newsapi": {
                "api_key": self.news_api_key,
            },
        }


settings = Settings()

