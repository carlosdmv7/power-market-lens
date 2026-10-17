"""Settings, read once from the environment (and `.env` locally).

Everything configurable lives here, so the rest of the code never touches
`os.environ` and tests can build a `Settings` by hand. Pydantic validates the
values at startup: a typo in `APP_ENV` fails loudly here instead of silently
writing to the wrong database later.
"""

from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # extra="ignore": .env also holds keys read by other tools (dbt, Prefect).
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_env: Literal["dev", "prod"] = "dev"
    log_level: str = "INFO"

    # Secrets are optional here so that tests and commands that don't touch a
    # service can run without them; the code that needs one checks for it.
    motherduck_token: SecretStr | None = None
    entsoe_api_key: SecretStr | None = None

    # Days re-pulled on every run to capture publisher revisions (ADR-0003).
    trailing_window_days: int = Field(default=7, ge=1, le=60)

    @property
    def database(self) -> str:
        """MotherDuck database for this environment: power_dev or power_prod."""
        return f"power_{self.app_env}"


def load_settings(env_file: str | None = ".env") -> Settings:
    """Build settings from the environment; `env_file=None` skips `.env`."""
    return Settings(_env_file=env_file)
