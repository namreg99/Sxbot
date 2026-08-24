from sxbot.models import Action, Side
from sxbot.orderbook import analyze
from sxbot.strategy import evaluate
from sxbot.units import OddsLadder, from_percent
from tests.conftest import make_book, make_market, make_settings


def _signals(prev_book, curr_book, **setting_kw):
    return evaluate(
        make_market(),
        analyze(prev_book),
        analyze(curr_book),
        make_settings(**setting_kw),
        OddsLadder(125),
    )


def test_unchanged_book_is_silent() -> None:
    book = make_book(o1=((50.0, 10),), o2=((49.0, 10),), version="5")
    assert _signals(book, book) == []


def test_maker_reprice_joins_outcome_one() -> None:
    prev = make_book(o1=((50.0, 10),), o2=((49.0, 10),), version="1")
    curr = make_book(o1=((53.0, 10),), o2=((46.0, 10),), version="2")
    signals = _signals(prev, curr)
    joins = [s for s in signals if s.action is Action.JOIN_MAKER]
    assert len(joins) == 1
    assert joins[0].side is Side.OUTCOME_ONE
    assert joins[0].maker_odds == from_percent(52.875)
    assert joins[0].mid_move_bps > 0
    assert joins[0].style == "mlb"


def test_maker_reprice_joins_outcome_two() -> None:
    prev = make_book(o1=((50.0, 10),), o2=((49.0, 10),), version="1")
    curr = make_book(o1=((47.0, 10),), o2=((52.0, 10),), version="2")
    signals = _signals(prev, curr)
    joins = [s for s in signals if s.action is Action.JOIN_MAKER]
    assert len(joins) == 1
    assert joins[0].side is Side.OUTCOME_TWO
    assert joins[0].maker_odds == from_percent(51.875)


def test_small_move_is_ignored() -> None:
    prev = make_book(o1=((50.0, 10),), o2=((49.0, 10),), version="1")
    curr = make_book(o1=((50.125, 10),), o2=((48.875, 10),), version="2")
    assert _signals(prev, curr, min_mid_move_bps=20) == []


def test_crossed_book_takes_stale_with_the_makers() -> None:
    prev = make_book(o1=((50.0, 10),), o2=((49.0, 10),), version="1")
    curr = make_book(o1=((53.0, 12),), o2=((49.0, 2),), version="2")
    signals = _signals(prev, curr)
    assert signals
    assert signals[0].action is Action.TAKE_STALE
    assert signals[0].side is Side.OUTCOME_ONE
    # Taking leftover O2 @ 49% means betting O1 at 51%.
    assert signals[0].maker_odds == from_percent(51.0)
    assert signals[0].crossed


def test_size_flow_joins_the_heavy_side() -> None:
    prev = make_book(o1=((50.0, 5),), o2=((48.0, 20),), version="1")
    curr = make_book(o1=((50.0, 40),), o2=((48.0, 5),), version="2")
    signals = _signals(prev, curr)
    joins = [s for s in signals if s.action is Action.JOIN_MAKER]
    assert len(joins) == 1
    assert joins[0].side is Side.OUTCOME_ONE


def test_one_sided_book_no_signal() -> None:
    prev = make_book(o1=((50.0, 10),), o2=((49.0, 10),), version="1")
    curr = make_book(o1=((53.0, 10),), o2=(), version="2")
    assert _signals(prev, curr) == []


def test_take_style_hits_steam_instead_of_joining() -> None:
    prev = make_book(o1=((50.0, 10),), o2=((49.0, 10),), version="1")
    curr = make_book(o1=((53.0, 10),), o2=((46.0, 10),), version="2")
    signals = _signals(prev, curr, follow_style="take")
    assert len(signals) == 1
    assert signals[0].action is Action.TAKE_FLOW
    assert signals[0].side is Side.OUTCOME_ONE
    # Hit leftover O2 @ 46% → betting O1 at 54%.
    assert signals[0].maker_odds == from_percent(54.0)
    assert signals[0].fair_odds > 0


def test_mixed_style_skips_tob_lag_unless_enabled() -> None:
    prev = make_book(o1=((50.0, 10),), o2=((49.0, 10),), version="1")
    curr = make_book(o1=((50.0, 1), (54.0, 20)), o2=((49.0, 10),), version="2")
    assert _signals(prev, curr, follow_style="mixed") == []
    signals = _signals(prev, curr, follow_style="mixed", join_tob_lag=True)
    assert signals
    assert signals[0].action is Action.JOIN_MAKER


def test_skips_totals_even_on_steam() -> None:
    prev = make_book(o1=((50.0, 10),), o2=((49.0, 10),), version="1")
    curr = make_book(o1=((53.0, 10),), o2=((46.0, 10),), version="2")
    market = make_market(type=28, outcome_one="Over 2.5", outcome_two="Under 2.5")
    from sxbot.orderbook import analyze
    from sxbot.strategy import evaluate
    from sxbot.units import OddsLadder
    from tests.conftest import make_settings

    signals = evaluate(
        market,
        analyze(prev),
        analyze(curr),
        make_settings(),
        OddsLadder(125),
    )
    assert signals == []


def test_skips_longshot_steam() -> None:
    prev = make_book(o1=((18.0, 10),), o2=((80.0, 10),), version="1")
    curr = make_book(o1=((22.0, 10),), o2=((76.0, 10),), version="2")
    assert _signals(prev, curr) == []
    # Raising the hard cap is not enough — 4.5 decimal is outside every style band.
    assert _signals(prev, curr, max_order_decimal=6.0) == []


def test_skips_not_tie() -> None:
    prev = make_book(o1=((50.0, 10),), o2=((49.0, 10),), version="1")
    curr = make_book(o1=((53.0, 10),), o2=((46.0, 10),), version="2")
    market = make_market(type=1, outcome_one="Tie", outcome_two="Not tie")
    from sxbot.orderbook import analyze
    from sxbot.strategy import evaluate
    from sxbot.units import OddsLadder
    from tests.conftest import make_settings

    signals = evaluate(
        market,
        analyze(prev),
        analyze(curr),
        make_settings(),
        OddsLadder(125),
    )
    assert signals == []


def test_skips_npb_and_kbo() -> None:
    prev = make_book(o1=((50.0, 10),), o2=((49.0, 10),), version="1")
    curr = make_book(o1=((53.0, 10),), o2=((46.0, 10),), version="2")
    market = make_market(
        type=52,
        sport_id=3,
        sport_label="Baseball",
        league_id=19,
        league_label="NPB",
    )
    signals = evaluate(
        market,
        analyze(prev),
        analyze(curr),
        make_settings(),
        OddsLadder(125),
    )
    assert signals == []


def test_skips_empty_book_flicker() -> None:
    prev = make_book(o1=((50.0, 10),), o2=((49.0, 10),), version="1")
    curr = make_book(o1=((70.0, 10),), o2=((29.0, 10),), version="2")
    market = make_market(
        type=1,
        sport_id=5,
        sport_label="Soccer",
        league_label="EPL",
        outcome_one="Arsenal",
        outcome_two="Not Arsenal",
    )
    signals = evaluate(
        market,
        analyze(prev),
        analyze(curr),
        make_settings(),
        OddsLadder(125),
    )
    assert signals == []


def test_skips_futures_kickoff() -> None:
    prev = make_book(o1=((50.0, 10),), o2=((49.0, 10),), version="1")
    curr = make_book(o1=((53.0, 10),), o2=((46.0, 10),), version="2")
    market = make_market(game_time=int(__import__("time").time()) + 30 * 24 * 3600)
    signals = evaluate(
        market,
        analyze(prev),
        analyze(curr),
        make_settings(),
        OddsLadder(125),
    )
    assert signals == []


def test_tennis_dog_joins_in_band() -> None:
    prev = make_book(o1=((30.0, 10),), o2=((69.0, 10),), version="1")
    curr = make_book(o1=((32.0, 10),), o2=((67.0, 10),), version="2")
    market = make_market(
        type=3,
        sport_id=6,
        sport_label="Tennis",
        league_id=2,
        league_label="ATP",
        outcome_one="Player A",
        outcome_two="Player B",
    )
    signals = evaluate(
        market,
        analyze(prev),
        analyze(curr),
        make_settings(),
        OddsLadder(125),
    )
    assert signals
    assert signals[0].style == "tennis_dog"


def test_soccer_type1_joins_favorite_band() -> None:
    prev = make_book(o1=((62.0, 10),), o2=((37.0, 10),), version="1")
    curr = make_book(o1=((65.0, 10),), o2=((34.0, 10),), version="2")
    market = make_market(
        type=1,
        sport_id=5,
        sport_label="Soccer",
        league_id=1,
        league_label="EPL",
        outcome_one="Arsenal",
        outcome_two="Not Arsenal",
    )
    signals = evaluate(
        market,
        analyze(prev),
        analyze(curr),
        make_settings(),
        OddsLadder(125),
    )
    assert signals
    assert signals[0].style == "soccer"


def test_older_version_is_ignored() -> None:
    prev = make_book(o1=((50.0, 10),), o2=((49.0, 10),), version="9")
    curr = make_book(o1=((53.0, 10),), o2=((46.0, 10),), version="8")
    assert _signals(prev, curr) == []
