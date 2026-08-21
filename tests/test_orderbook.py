from sxbot.orderbook import analyze, depth_taker
from tests.conftest import make_book


def test_docs_book_mid_spread_imbalance() -> None:
    book = make_book(
        o1=((52.0, 2), (51.5, 1), (50.0, 3)),
        o2=((46.75, 1), (46.0, 3), (42.0, 1)),
    )
    view = analyze(book)
    assert view.best_one == 52_000_000_000_000_000_000
    assert view.best_two == 46_750_000_000_000_000_000
    # bid 52, ask 53.25, mid 52.625, spread 1.25%
    assert view.bid_one == 52_000_000_000_000_000_000
    assert view.ask_one == 53_250_000_000_000_000_000
    assert view.mid_one == 52_625_000_000_000_000_000
    assert view.spread_bps() == 125
    assert view.size_one == 6_000_000
    assert view.size_two == 5_000_000
    assert round(view.imbalance, 4) == round((6 - 5) / 11, 4)
    assert not view.crossed
    assert view.two_sided
    assert view.dw_mid is not None


def test_one_sided_has_no_mid() -> None:
    view = analyze(make_book(o1=((50.0, 5),), o2=()))
    assert view.mid_one is None
    assert not view.two_sided


def test_crossed_book() -> None:
    view = analyze(make_book(o1=((53.0, 10),), o2=((49.0, 2),)))
    # ask = 51, bid = 53
    assert view.crossed
    assert view.bid_one > view.ask_one


def test_taker_depth() -> None:
    book = make_book(o1=((52.0, 2),), o2=((46.75, 1),))
    # betting outcome one consumes O2
    assert depth_taker(book, True) > 0
    assert depth_taker(book, False) > 0
