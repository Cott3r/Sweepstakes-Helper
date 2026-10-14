"""Application configuration and settings loader using pydantic-settings."""

import json
from pathlib import Path
from typing import Optional
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from models.sweepstake import UserProfile
from utils.logger import logger

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)


class Settings(BaseSettings):
    """Global configuration settings for Sweepstakes Helper."""

    model_config = SettingsConfigDict(
        env_file=str(BASE_DIR / ".env"),
        env_file_encoding="utf-8",
        extra="ignore"
    )

    # General app settings
    APP_NAME: str = "Sweepstakes-Helper-AT"
    DEBUG: bool = False
    LOG_LEVEL: str = "INFO"

    # Database
    DB_PATH: Path = DATA_DIR / "sweepstakes.db"

    # Execution Mode
    HEADLESS: bool = True
    INTERACTIVE_FALLBACK: bool = True  # Switch to headful when CAPTCHA/manual action needed
    PLAY_AUDIO_CHIME: bool = True
    BROWSER_TIMEOUT_MS: int = 30000
    ACTION_TIMEOUT_MS: int = 7000
    PAGE_LOAD_WAIT_MS: int = 2500

    # Rate limiting & Anti-Bot Delays
    MIN_REQUEST_DELAY: float = 1.5
    MAX_REQUEST_DELAY: float = 4.5
    MAX_RETRIES: int = 3
    BACKOFF_FACTOR: float = 1.8

    # Limits
    MAX_ENTRIES_PER_RUN: int = 30

    # Notification Webhooks
    DISCORD_WEBHOOK_URL: Optional[str] = None
    TELEGRAM_BOT_TOKEN: Optional[str] = None
    TELEGRAM_CHAT_ID: Optional[str] = None

    # Realistic User-Agent string
    DEFAULT_USER_AGENT: str = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
    )

    # Path to user profile JSON
    USER_PROFILE_PATH: Path = BASE_DIR / "user_profile.json"


def load_user_profile(profile_path: Optional[Path] = None) -> UserProfile:
    """Loads user profile data from JSON file or provides an informative fallback.

    Args:
        profile_path: Path to user profile JSON file.

    Returns:
        UserProfile instance.
    """
    path = profile_path or (BASE_DIR / "user_profile.json")
    example_path = BASE_DIR / "user_profile.json.example"

    if path.exists():
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return UserProfile(**data)
        except Exception as e:
            logger.error(f"Failed to parse user profile at {path}: {e}")
            raise

    # Fallback to example if available
    if example_path.exists():
        logger.warning(f"Profile {path} not found. Loading template from {example_path}!")
        with open(example_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return UserProfile(**data)

    # Default placeholder profile for Austria
    logger.warning("No user profile found. Using placeholder profile for Austria.")
    return UserProfile(
        first_name="Max",
        last_name="Mustermann",
        email="max.mustermann.gewinnspiele@gmail.com",
        phone="+436601234567",
        street="Mariahilfer Straße",
        house_number="101/2/5",
        postal_code="1060",
        city="Wien",
        country="Österreich",
        birth_date="15.06.1992",
        salutation="Herr",
    )


# Global settings singleton
settings = Settings()
