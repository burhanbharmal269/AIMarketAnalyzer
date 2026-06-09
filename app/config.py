from dataclasses import dataclass
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

    # Standard OpenAI (used only when Azure fields are not set)
    openai_api_key: str = os.getenv("OPENAI_API_KEY", "")
    openai_model: str   = os.getenv("OPENAI_MODEL", "gpt-4o-mini")

    # Azure OpenAI (takes priority over standard OpenAI when all three are set)
    azure_openai_api_key:    str = os.getenv("AZURE_OPENAI_API_KEY", "")
    azure_openai_endpoint:   str = os.getenv("AZURE_OPENAI_ENDPOINT", "")
    azure_openai_deployment: str = os.getenv("AZURE_OPENAI_DEPLOYMENT", "")
    azure_openai_api_version: str = os.getenv("AZURE_OPENAI_API_VERSION", "2025-01-01-preview")

    # Angel One SmartAPI
    angel_api_key:     str = os.getenv("ANGEL_API_KEY", "")
    angel_client_id:   str = os.getenv("ANGEL_CLIENT_ID", "")
    angel_pin:         str = os.getenv("ANGEL_PIN", "")
    angel_totp_secret: str = os.getenv("ANGEL_TOTP_SECRET", "")

    news_api_key: str       = os.getenv("NEWS_API_KEY", "")

    telegram_bot_token: str = os.getenv("TELEGRAM_BOT_TOKEN", "")
    telegram_chat_id: str   = os.getenv("TELEGRAM_CHAT_ID", "")
    enable_scheduler: bool  = os.getenv("ENABLE_SCHEDULER", "false").lower() == "true"


settings = Settings()

