"""Application configuration.

Keeps all environment-based settings in one place so later phases
(nutrition APIs, storage) have an obvious place to grow.
"""

import os

from dotenv import load_dotenv

load_dotenv()

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.6-flash").strip()

LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()


class ConfigError(RuntimeError):
    """Raised when required configuration is missing or invalid."""


def validate() -> None:
    """Fail fast with a readable message instead of a confusing API error."""
    missing = []
    if not TELEGRAM_BOT_TOKEN:
        missing.append("TELEGRAM_BOT_TOKEN")
    if not GEMINI_API_KEY:
        missing.append("GEMINI_API_KEY")

    if missing:
        raise ConfigError(
            f"Missing required environment variable(s): {', '.join(missing)}. "
            "Copy .env.example to .env and fill in the values."
        )
