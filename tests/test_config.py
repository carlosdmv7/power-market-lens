from pydantic import ValidationError
import pytest

from power_market_lens.config import load_settings

ENV_VARS = ["APP_ENV", "LOG_LEVEL", "MOTHERDUCK_TOKEN", "ENTSOE_API_KEY", "TRAILING_WINDOW_DAYS"]


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for var in ENV_VARS:
        monkeypatch.delenv(var, raising=False)


def test_defaults_when_env_is_empty():
    settings = load_settings(env_file=None)

    assert settings.app_env == "dev"
    assert settings.database == "power_dev"
    assert settings.log_level == "INFO"
    assert settings.trailing_window_days == 7
    assert settings.entsoe_api_key is None


def test_reads_environment(monkeypatch):
    monkeypatch.setenv("APP_ENV", "prod")
    monkeypatch.setenv("TRAILING_WINDOW_DAYS", "10")

    settings = load_settings(env_file=None)

    assert settings.database == "power_prod"
    assert settings.trailing_window_days == 10


def test_unknown_environment_fails_loudly(monkeypatch):
    monkeypatch.setenv("APP_ENV", "staging")

    with pytest.raises(ValidationError):
        load_settings(env_file=None)


def test_secrets_are_not_printed(monkeypatch):
    monkeypatch.setenv("ENTSOE_API_KEY", "super-secret")

    settings = load_settings(env_file=None)

    assert "super-secret" not in repr(settings)
    assert settings.entsoe_api_key.get_secret_value() == "super-secret"
