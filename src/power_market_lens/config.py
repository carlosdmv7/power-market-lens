"""Settings, read once from the environment (and `.env` locally).

Everything configurable lives here, so the rest of the code never touches
`os.environ` and tests can build a `Settings` by hand.
"""

from dataclasses import dataclass
import os

from dotenv import load_dotenv


@dataclass(frozen=True)
class Settings:
    env: str
    log_level: str


def load_settings() -> Settings:
    load_dotenv()
    return Settings(
        env=os.getenv("APP_ENV", "dev"),
        log_level=os.getenv("LOG_LEVEL", "INFO"),
    )
