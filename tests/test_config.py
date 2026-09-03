from sxbot.config import Settings
import os


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


def test_load_manual_log(monkeypatch) -> None:
    monkeypatch.setattr("sxbot.config.load_dotenv", lambda *a, **k: None)
    monkeypatch.delenv("SX_MANUAL_LOG", raising=False)
    assert Settings.load().manual_log == "sxbot-manual.jsonl"
    monkeypatch.setenv("SX_MANUAL_LOG", "my-tickets.jsonl")
    assert Settings.load().manual_log == "my-tickets.jsonl"


def test_load_skip_styles(monkeypatch) -> None:
    monkeypatch.setenv("SX_SKIP_STYLES", "tennis_dog, mlb")
    monkeypatch.setattr("sxbot.config.load_dotenv", lambda *a, **k: None)
    settings = Settings.load()
    assert settings.skip_styles == ("tennis_dog", "mlb")


def test_load_dry_run_strips_null_bytes(monkeypatch) -> None:
    monkeypatch.setattr("sxbot.config.load_dotenv", lambda *a, **k: None)
    real_getenv = os.getenv

    def getenv(name: str, default=None):
        if name == "SX_DRY_RUN":
            return "false\x00"
        return real_getenv(name, default)

    monkeypatch.setattr("sxbot.config.os.getenv", getenv)
    settings = Settings.load()
    assert settings.dry_run is False
