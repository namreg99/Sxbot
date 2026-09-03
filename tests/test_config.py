from sxbot.config import Settings


def test_load_enables_tob_lag_joins_by_default(monkeypatch) -> None:
    monkeypatch.delenv("SX_JOIN_TOB_LAG", raising=False)
    monkeypatch.delenv("SX_TOB_LAG_MAX_DECIMAL", raising=False)
    # Ignore a local .env so this asserts the product default, not a laptop file.
    monkeypatch.setattr("sxbot.config.load_dotenv", lambda *a, **k: None)
    settings = Settings.load()
    assert settings.join_tob_lag is True
    assert settings.tob_lag_max_decimal == 2.20


def test_load_tob_lag_env_overrides(monkeypatch) -> None:
    monkeypatch.setenv("SX_JOIN_TOB_LAG", "false")
    monkeypatch.setenv("SX_TOB_LAG_MAX_DECIMAL", "1.80")
    monkeypatch.setattr("sxbot.config.load_dotenv", lambda *a, **k: None)
    settings = Settings.load()
    assert settings.join_tob_lag is False
    assert settings.tob_lag_max_decimal == 1.80


def test_load_skip_styles(monkeypatch) -> None:
    monkeypatch.setenv("SX_SKIP_STYLES", "tennis_dog, mlb")
    monkeypatch.setattr("sxbot.config.load_dotenv", lambda *a, **k: None)
    settings = Settings.load()
    assert settings.skip_styles == ("tennis_dog", "mlb")
