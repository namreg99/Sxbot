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


def test_older_version_is_ignored() -> None:
    prev = make_book(o1=((50.0, 10),), o2=((49.0, 10),), version="9")
    curr = make_book(o1=((53.0, 10),), o2=((46.0, 10),), version="8")
    assert _signals(prev, curr) == []
