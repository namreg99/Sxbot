from sxbot.filters import (
    kickoff_skip_reason,
    longshot_skip_reason,
    mm_family,
    mm_quote_style,
    order_skip_reason,
    quote_family,
    quote_style,
)
from sxbot.units import from_percent
from tests.conftest import make_market, make_settings


def test_quote_style_mlb_pickem() -> None:
    market = make_market(type=226, sport_id=3, league_label="MLB", league_id=3, sport_label="Baseball")
    assert quote_family(market) == "mlb"
    assert quote_style(market, from_percent(52.0), make_settings()) == "mlb"
    assert quote_style(market, from_percent(70.0), make_settings()) is None


def test_quote_style_soccer_and_nfl() -> None:
    soccer = make_market(
        type=1,
        sport_id=5,
        sport_label="Soccer",
        league_label="EPL",
        outcome_one="Arsenal",
        outcome_two="Not Arsenal",
    )
    assert quote_style(soccer, from_percent(65.0), make_settings()) == "soccer"
    assert quote_style(soccer, from_percent(40.0), make_settings()) is None
    nfl = make_market(type=8, sport_id=8, league_id=243, league_label="NFL")
    assert quote_family(nfl) == "soccer"
    assert quote_style(nfl, from_percent(52.0), make_settings()) == "soccer"
    assert quote_style(nfl, from_percent(70.0), make_settings()) is None


def test_quote_style_tennis_bands() -> None:
    tennis = make_market(type=3, sport_id=6, sport_label="Tennis", league_label="ATP")
    assert quote_style(tennis, from_percent(80.0), make_settings()) == "tennis_short"
    assert quote_style(tennis, from_percent(50.0), make_settings()) is None
    assert quote_style(tennis, from_percent(32.0), make_settings()) == "tennis_dog"
    assert quote_style(tennis, from_percent(18.0), make_settings()) is None
    skipped = make_settings(skip_styles=("tennis_dog",))
    assert quote_style(tennis, from_percent(80.0), skipped) == "tennis_short"
    assert quote_style(tennis, from_percent(32.0), skipped) is None


def test_quote_family_skips_npb_kbo() -> None:
    npb = make_market(type=52, sport_id=3, league_id=19, league_label="NPB", sport_label="Baseball")
    kbo = make_market(type=52, sport_id=3, league_id=20, league_label="KBO League", sport_label="Baseball")
    assert quote_family(npb) is None
    assert quote_family(kbo) is None


def test_kickoff_horizon_skips_futures() -> None:
    now = 1_000
    soon = make_market(game_time=now + 3 * 3600)
    far = make_market(game_time=now + 30 * 24 * 3600)
    settings = make_settings()
    assert kickoff_skip_reason(soon, settings, now=now) is None
    assert kickoff_skip_reason(far, settings, now=now) == "futures"
    assert order_skip_reason(far, settings, now=now) == "futures"


def test_longshot_helper_uses_max_order_decimal() -> None:
    settings = make_settings(max_order_decimal=3.5)
    assert longshot_skip_reason(from_percent(22.0), settings)
    assert longshot_skip_reason(from_percent(52.0), settings) is None


def test_mm_quote_style_skips_pickem_and_nfl() -> None:
    mlb = make_market(type=226, sport_id=3, league_label="MLB", league_id=3, sport_label="Baseball")
    assert mm_family(mlb) == "mlb"
    assert mm_quote_style(mlb, from_percent(52.0)) is None
    assert mm_quote_style(mlb, from_percent(62.0)) == "mlb"
    assert mm_quote_style(mlb, from_percent(32.0)) == "mlb"
    soccer = make_market(
        type=1,
        sport_id=5,
        sport_label="Soccer",
        league_label="EPL",
        outcome_one="Arsenal",
        outcome_two="Not Arsenal",
    )
    assert mm_quote_style(soccer, from_percent(55.0)) is None
    assert mm_quote_style(soccer, from_percent(32.0)) == "soccer"
    assert mm_quote_style(soccer, from_percent(75.0)) == "soccer"
    vs = make_market(
        type=52,
        sport_id=5,
        sport_label="Soccer",
        outcome_one="Fulham",
        outcome_two="Chelsea",
    )
    assert mm_family(vs) is None
    tennis = make_market(type=52, sport_id=6, sport_label="Tennis", league_label="ATP")
    assert mm_quote_style(tennis, from_percent(62.0)) == "tennis"
    assert mm_quote_style(tennis, from_percent(50.0)) is None
    nfl = make_market(type=8, sport_id=8, league_id=243, league_label="NFL")
    assert mm_family(nfl) is None
    spread = make_market(
        type=342,
        sport_id=3,
        league_label="MLB",
        outcome_one="Dodgers +1.5",
        outcome_two="Pirates -1.5",
    )
    assert mm_quote_style(spread, from_percent(70.0)) == "mlb"
    assert mm_quote_style(spread, from_percent(52.0)) is None
