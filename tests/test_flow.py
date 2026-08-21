from sxbot.flow import Motive, SteamTracker, classify, persistence
from sxbot.models import PublicTrade, Side
from sxbot.orderbook import analyze
from tests.conftest import make_book, make_settings


def _report(prev_book, curr_book, trades=None, steam_hits=0, **kw):
    return classify(
        analyze(prev_book),
        analyze(curr_book),
        make_settings(**kw),
        trades=trades,
        steam_hits=steam_hits,
    )


def test_maker_steam_without_tape() -> None:
    prev = make_book(o1=((50.0, 10),), o2=((49.0, 10),), version="1")
    curr = make_book(o1=((53.0, 10),), o2=((46.0, 10),), version="2")
    report = _report(prev, curr, trades=[])
    assert report.motive is Motive.MAKER_STEAM
    assert report.side is Side.OUTCOME_ONE
    assert report.actionable
    assert any("no tape print" in r for r in report.reasons)


def test_taker_hit_is_not_actionable() -> None:
    prev = make_book(o1=((50.0, 10),), o2=((49.0, 10),), version="1")
    curr = make_book(o1=((50.0, 10),), o2=((49.0, 4),), version="2")
    tape = [
        PublicTrade(
            trade_id="0x1",
            market_hash="0xabc",
            is_betting_outcome_one=True,
            stake=6_000_000,
            odds=51_000_000_000_000_000_000,
            bet_time="2026-08-21T00:00:00Z",
        )
    ]
    report = _report(prev, curr, trades=tape)
    assert report.motive is Motive.TAKER_HIT
    assert not report.actionable


def test_unknown_tape_does_not_call_a_size_drop_a_taker_hit() -> None:
    prev = make_book(o1=((50.0, 10),), o2=((49.0, 10),), version="1")
    curr = make_book(o1=((50.0, 10),), o2=((49.0, 4),), version="2")
    report = _report(prev, curr, trades=None)
    assert report.motive is not Motive.TAKER_HIT


def test_size_rotation() -> None:
    prev = make_book(o1=((50.0, 5),), o2=((48.0, 20),), version="1")
    curr = make_book(o1=((50.0, 40),), o2=((48.0, 5),), version="2")
    report = _report(prev, curr)
    assert report.motive is Motive.SIZE_ROTATION
    assert report.side is Side.OUTCOME_ONE
    assert report.actionable


def test_static_imbalance_is_not_a_fresh_rotation() -> None:
    prev = make_book(o1=((50.0, 40),), o2=((48.0, 5),), version="1")
    curr = make_book(o1=((50.0, 40),), o2=((48.0, 5),), version="2")
    report = _report(prev, curr)
    assert report.motive is Motive.NONE


def test_tob_lag_follows_the_body() -> None:
    # Thin top at 50 vs a heavy body at 54 — depth-weighted mid leads TOB.
    # Prev must *not* already have that lag or the signal is treated as sticky.
    prev = make_book(o1=((50.0, 10),), o2=((49.0, 10),), version="1")
    curr = make_book(o1=((50.0, 1), (54.0, 20),), o2=((49.0, 10),), version="2")
    report = _report(prev, curr, min_mid_move_bps=20)
    assert report.motive is Motive.TOB_LAG
    assert report.side is Side.OUTCOME_ONE


def test_sticky_tob_lag_does_not_re_fire_on_size_flicker() -> None:
    lagged = ((50.0, 1), (54.0, 20))
    prev = make_book(o1=lagged, o2=((49.0, 10),), version="1")
    curr = make_book(o1=lagged, o2=((49.0, 9.5),), version="2")
    report = _report(prev, curr, min_mid_move_bps=20)
    assert report.motive is Motive.NONE


def test_tiny_size_flicker_is_not_a_rotation() -> None:
    prev = make_book(o1=((50.0, 40),), o2=((48.0, 40),), version="1")
    curr = make_book(o1=((50.0, 40.2),), o2=((48.0, 39.8),), version="2")
    report = _report(prev, curr)
    assert report.motive is not Motive.SIZE_ROTATION


def test_hex_book_version_is_not_compared_as_a_number() -> None:
    # V3 versions are content hashes. Lexicographic `new <= old` used to drop real updates.
    prev = make_book(o1=((50.0, 10),), o2=((49.0, 10),), version="aa")
    curr = make_book(o1=((53.0, 10),), o2=((46.0, 10),), version="09")
    report = _report(prev, curr)
    assert report.motive is Motive.MAKER_STEAM


def test_older_numeric_version_is_ignored() -> None:
    prev = make_book(o1=((50.0, 10),), o2=((49.0, 10),), version="9")
    curr = make_book(o1=((53.0, 10),), o2=((46.0, 10),), version="8")
    report = _report(prev, curr)
    assert report.motive is Motive.NONE


def test_numeric_version_ten_is_newer_than_nine() -> None:
    # String compare would treat "10" <= "9" as true and drop a real update.
    prev = make_book(o1=((50.0, 10),), o2=((49.0, 10),), version="9")
    curr = make_book(o1=((53.0, 10),), o2=((46.0, 10),), version="10")
    report = _report(prev, curr)
    assert report.motive is Motive.MAKER_STEAM


def test_persistence_full_when_unchanged() -> None:
    book = make_book(o1=((50.0, 10), (48.0, 5)), o2=((49.0, 8),))
    view = analyze(book)
    assert persistence(view, view) == 1.0


def test_steam_tracker_counts_same_side() -> None:
    tracker = SteamTracker(window_s=30)
    assert tracker.record("m", Side.OUTCOME_ONE, 1.0) == 1
    assert tracker.record("m", Side.OUTCOME_ONE, 2.0) == 2
    assert tracker.record("m", Side.OUTCOME_TWO, 3.0) == 1
    assert tracker.record("m", None, 4.0) == 0


def test_conflicting_steam_and_rotation_is_dropped() -> None:
    prev = make_book(o1=((50.0, 40),), o2=((49.0, 5),), version="1")
    curr = make_book(o1=((53.0, 5),), o2=((46.0, 40),), version="2")
    report = _report(prev, curr)
    assert report.motive is Motive.NONE
