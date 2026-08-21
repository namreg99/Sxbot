from sxbot.bot import RadarRow, format_radar, pick_universe
from sxbot.flow import FlowReport, Motive
from sxbot.models import Side
from sxbot.orderbook import analyze
from tests.conftest import make_book, make_market


def test_soonest_pregame_first_and_reserves_live_slots() -> None:
    now = 1_000
    markets = [
        make_market(market_hash="far", game_time=now + 10_000, outcome_one="Far"),
        make_market(market_hash="soon", game_time=now + 10, outcome_one="Soon"),
        make_market(market_hash="live1", game_time=now - 100, outcome_one="Live1"),
        make_market(market_hash="live2", game_time=now - 50, outcome_one="Live2"),
    ]
    picked = pick_universe(markets, cap=2, now=now, watch_live=True)
    hashes = [m.market_hash for m in picked]
    assert hashes[0] == "soon"
    assert "live1" in hashes or "live2" in hashes
    assert "far" not in hashes


def test_watch_live_off_drops_in_play() -> None:
    now = 1_000
    markets = [
        make_market(market_hash="soon", game_time=now + 10),
        make_market(market_hash="live1", game_time=now - 100),
    ]
    picked = pick_universe(markets, cap=5, now=now, watch_live=False)
    assert [m.market_hash for m in picked] == ["soon"]


def _radar_row(*, confidence: float) -> RadarRow:
    view = analyze(make_book(o1=((53.0, 10),), o2=((46.0, 10),)))
    report = FlowReport(
        motive=Motive.MAKER_STEAM,
        side=Side.OUTCOME_ONE,
        move_bps=80,
        persistence=0.4,
        tob_vs_dw_bps=None,
        tape_prints=0,
        steam_hits=1,
        confidence=confidence,
        reasons=("makers shifted mid",),
    )
    return RadarRow(market=make_market(), view=view, report=report)


def test_format_radar_empty() -> None:
    text = format_radar([])
    assert "No flow yet" in text


def test_format_radar_ranks_by_confidence() -> None:
    rows = [_radar_row(confidence=0.6), _radar_row(confidence=0.9)]
    text = format_radar(rows)
    first_idx = text.index("conf=0.90")
    second_idx = text.index("conf=0.60")
    assert first_idx < second_idx
    assert "1 actionable / 2 flagged" not in text  # both are actionable here
    assert "2 actionable / 2 flagged" in text
