from power_market_lens.config import load_settings


def test_defaults_when_env_is_empty(monkeypatch):
    monkeypatch.delenv("APP_ENV", raising=False)
    monkeypatch.delenv("LOG_LEVEL", raising=False)
    monkeypatch.setattr("power_market_lens.config.load_dotenv", lambda: None)

    settings = load_settings()

    assert settings.env == "dev"
    assert settings.log_level == "INFO"


def test_reads_environment(monkeypatch):
    monkeypatch.setenv("APP_ENV", "prod")
    monkeypatch.setattr("power_market_lens.config.load_dotenv", lambda: None)

    assert load_settings().env == "prod"
