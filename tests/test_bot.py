from sxbot.bot import pick_universe
from tests.conftest import make_market


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
