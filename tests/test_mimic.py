from sxbot.mimic import should_copy_taker
from sxbot.units import from_percent
from tests.conftest import make_market, make_settings


def _raw(pct: float) -> dict:
    return {"odds": str(from_percent(pct)), "bettingOutcomeOne": True, "stake": "5000000"}


def test_skips_longshot_alts() -> None:
    market = make_market(sport_id=5, sport_label="Soccer")
    settings = make_settings(sport_ids=(5, 6), mimic_max_decimal=3.5, allow_live=False)
    ok, why = should_copy_taker(_raw(12.75), market, settings)  # ~7.84 decimal
    assert ok is False
    assert "longshot" in why


def test_copies_tennis_favorite() -> None:
    market = make_market(
        sport_id=6,
        sport_label="Tennis",
        league_label="ATP",
        game_time=2_000_000_000,
    )
    settings = make_settings(sport_ids=(8, 1, 3, 2, 5, 6), mimic_max_decimal=3.5)
    ok, why = should_copy_taker(_raw(88.5), market, settings)  # ~1.13
    assert ok is True
    assert why == "ok"


def test_live_blocked_until_allow_live() -> None:
    market = make_market(sport_id=6, game_time=1_000)
    settings = make_settings(sport_ids=(6,), allow_live=False)
    ok, why = should_copy_taker(_raw(55.0), market, settings)
    assert ok is False
    assert "live" in why


def test_wrong_sport_skipped() -> None:
    market = make_market(sport_id=14, sport_label="Crypto")
    settings = make_settings(sport_ids=(8, 1, 3, 2, 5, 6))
    ok, why = should_copy_taker(_raw(55.0), market, settings)
    assert ok is False
    assert "sport" in why
