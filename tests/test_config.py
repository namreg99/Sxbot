from sxbot.config import Settings
import os


def test_load_disables_tob_lag_joins_by_default(monkeypatch) -> None:
    monkeypatch.delenv("SX_JOIN_TOB_LAG", raising=False)
    monkeypatch.delenv("SX_TOB_LAG_MAX_DECIMAL", raising=False)
    # Ignore a local .env so this asserts the product default, not a laptop file.
    monkeypatch.setattr("sxbot.config.load_dotenv", lambda *a, **k: None)
    settings = Settings.load()
    assert settings.join_tob_lag is False
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


def test_live_run_keeps_tennis_dog_unique() -> None:
    from sxbot.cli import apply_live_run
    from tests.conftest import make_settings

    skipped = make_settings(skip_styles=("tennis_dog", "mlb"), dry_run=True, follow_style="join")
    live = apply_live_run(skipped)
    assert live.dry_run is False
    assert live.follow_style == "take_first"
    assert live.enable_take_stale is True
    assert "tennis_dog" not in live.skip_styles
    assert live.skip_styles == ("mlb",)
    assert live.join_tob_lag is False
    assert live.max_take_through_bps == 250


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
