from sxbot.filters import (
    bare_team_name,
    kickoff_skip_reason,
    longshot_skip_reason,
    mm_family,
    mm_quote_style,
    order_skip_reason,
    quote_family,
    quote_style,
    soccer_team_lock_token,
    soccer_team_lock_token_from_row,
)
from sxbot.models import Side
from sxbot.units import from_percent
from tests.conftest import make_market, make_settings


def test_quote_style_mlb_shorts_not_pickem() -> None:
    market = make_market(type=226, sport_id=3, league_label="MLB", league_id=3, sport_label="Baseball")
    assert quote_family(market) == "mlb"
    assert quote_style(market, from_percent(52.0), make_settings()) is None
    assert quote_style(market, from_percent(70.0), make_settings()) == "mlb"
    assert quote_style(market, from_percent(40.0), make_settings()) == "mlb_dog"


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
    assert quote_style(soccer, from_percent(50.0), make_settings()) is None
    assert quote_style(soccer, from_percent(40.0), make_settings()) == "soccer_dog"
    nfl = make_market(type=8, sport_id=8, league_id=243, league_label="NFL")
    assert quote_family(nfl) == "soccer"
    assert quote_style(nfl, from_percent(52.0), make_settings()) == "soccer"
    assert quote_style(nfl, from_percent(70.0), make_settings()) is None
    vs = make_market(
        type=52,
        sport_id=5,
        sport_label="Soccer",
        league_label="EPL",
        outcome_one="Arsenal",
        outcome_two="Chelsea",
    )
    assert quote_style(vs, from_percent(40.0), make_settings()) == "soccer_dog"
    skipped = make_settings(skip_styles=("soccer_dog",))
    assert quote_style(soccer, from_percent(40.0), skipped) is None


def test_quote_style_mlb_dog_is_moneyline_only() -> None:
    ml = make_market(type=226, sport_id=3, league_label="MLB", league_id=3, sport_label="Baseball")
    assert quote_style(ml, from_percent(40.0), make_settings()) == "mlb_dog"
    assert quote_style(ml, from_percent(52.0), make_settings()) is None
    assert quote_style(ml, from_percent(65.0), make_settings()) == "mlb"
    spread = make_market(
        type=342,
        sport_id=3,
        league_label="MLB",
        league_id=3,
        sport_label="Baseball",
        outcome_one="Dodgers +1.5",
        outcome_two="Pirates -1.5",
    )
    assert quote_style(spread, from_percent(40.0), make_settings()) is None
    assert quote_style(spread, from_percent(52.0), make_settings()) is None
    assert quote_style(spread, from_percent(70.0), make_settings()) == "mlb"


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


def test_soccer_team_lock_treats_not_team_as_the_same_team() -> None:
    assert bare_team_name("Not Nautico Capibaribe") == bare_team_name("Nautico Capibaribe")
    kick = 1_700_000_000
    vs = make_market(
        market_hash="0x52",
        event_id="L1",
        sport_id=5,
        sport_label="Soccer",
        type=52,
        league_id=99,
        league_label="Brasileiro Serie B",
        outcome_one="Nautico Capibaribe",
        outcome_two="Botafogo SP",
        game_time=kick,
    )
    not_team = make_market(
        market_hash="0x01",
        event_id="L2",
        sport_id=5,
        sport_label="Soccer",
        type=1,
        league_id=99,
        league_label="Brasileiro Serie B",
        outcome_one="Nautico Capibaribe",
        outcome_two="Not Nautico Capibaribe",
        game_time=kick,
    )
    assert soccer_team_lock_token(vs, Side.OUTCOME_ONE) == soccer_team_lock_token(
        not_team, Side.OUTCOME_TWO
    )


def test_soccer_dog_row_still_locks_the_team() -> None:
    token = soccer_team_lock_token_from_row(
        {
            "style": "soccer_dog",
            "side": "outcome_one",
            "outcome_one": "Nautico Capibaribe",
            "outcome_two": "Botafogo SP",
            "game_time": 1_700_000_000,
            "league": "Brasileiro Serie B",
        }
    )
    assert token == ("soccer", "brasileiro serie b", str(1_700_000_000 // 3600), "nautico capibaribe")
