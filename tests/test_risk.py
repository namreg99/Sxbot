from dataclasses import replace

from sxbot.models import Action, Side, Signal
from sxbot.risk import RiskGate
from sxbot.units import to_base_units
from tests.conftest import make_market, make_settings


def _signal(action: Action = Action.JOIN_MAKER, hash_: str = "0x1") -> Signal:
    market = make_market(market_hash=hash_, event_id=hash_)
    return Signal(
        market=market,
        side=Side.OUTCOME_ONE,
        action=action,
        maker_odds=50_000_000_000_000_000_000,
        reason="test",
        mid_move_bps=40,
        imbalance=0.3,
        confidence=0.8,
    )


def test_allows_first_join() -> None:
    gate = RiskGate(make_settings())
    assert gate.allow(_signal()) is None


def test_blocks_when_total_exposure_hit() -> None:
    gate = RiskGate(make_settings(max_exposure_usdc=5, stake_usdc=5))
    gate.record(_signal(hash_="0x1"))
    assert gate.allow(_signal(hash_="0x2")) == "max total exposure"


def test_blocks_max_open_markets() -> None:
    gate = RiskGate(make_settings(max_open_markets=1, max_exposure_usdc=1000))
    first = _signal(hash_="0x1")
    gate.record(first)
    assert gate.allow(_signal(hash_="0x2")) == "max open markets"
    opposite = replace(_signal(hash_="0x1"), side=Side.OUTCOME_TWO)
    assert gate.allow(opposite) == "already in this market"


def test_does_not_restack_the_same_join() -> None:
    gate = RiskGate(make_settings(max_per_market_usdc=25, max_exposure_usdc=1000))
    gate.record(_signal(hash_="0x1"))
    assert gate.allow(_signal(hash_="0x1")) == "already on this side"
    opposite = replace(_signal(hash_="0x1"), side=Side.OUTCOME_TWO)
    assert gate.allow(opposite) == "already in this market"


def test_does_not_restack_a_take_on_the_same_side() -> None:
    gate = RiskGate(make_settings(max_per_market_usdc=25, max_exposure_usdc=1000))
    gate.record(_signal(Action.TAKE_FLOW, hash_="0x1"))
    assert gate.allow(_signal(Action.TAKE_FLOW, hash_="0x1")) == "already on this side"


def test_hydrate_live_rested_allows_retry_take() -> None:
    gate = RiskGate(make_settings(dry_run=False, max_exposure_usdc=1000, max_per_market_usdc=25))
    gate.hydrate(
        [
            {
                "action": "join_maker",
                "market": "0x1",
                "side": "outcome_one",
                "dry_run": False,
                "live_filled": False,
                "result": {
                    "orders": [
                        {
                            "orderId": "0xdead",
                            "outcome": {"state": "RESTED", "remainingAmount": "1000000"},
                        }
                    ]
                },
            }
        ]
    )
    assert gate.needs_live_entry("0x1") is Side.OUTCOME_ONE
    assert gate.allow(_signal(Action.TAKE_FLOW, hash_="0x1")) is None
    opposite = replace(_signal(Action.TAKE_FLOW, hash_="0x1"), side=Side.OUTCOME_TWO)
    assert gate.allow(opposite) == "already in this market"


def test_hydrate_live_fill_blocks_restack() -> None:
    gate = RiskGate(make_settings(dry_run=False, max_exposure_usdc=1000, max_per_market_usdc=25))
    gate.hydrate(
        [
            {
                "action": "take_flow",
                "market": "0x1",
                "side": "outcome_one",
                "dry_run": False,
                "live_filled": True,
            }
        ]
    )
    assert gate.needs_live_entry("0x1") is None
    assert gate.allow(_signal(Action.TAKE_FLOW, hash_="0x1")) == "already on this side"
    gate = RiskGate(make_settings(max_exposure_usdc=1000, max_per_market_usdc=25))
    gate.hydrate(
        [
            {"action": "join_maker", "market": "0x1", "side": "outcome_one"},
            {"action": "take_flow", "market": "0x2", "side": "outcome_two"},
        ]
    )
    assert gate.allow(_signal(hash_="0x1")) == "already on this side"
    assert gate.allow(_signal(Action.TAKE_FLOW, hash_="0x2")) == "already in this market"
    take_two = replace(_signal(Action.TAKE_FLOW, hash_="0x2"), side=Side.OUTCOME_TWO)
    assert gate.allow(take_two) == "already on this side"
    assert gate.exposure.total() == 0


def test_blocks_per_market_cap() -> None:
    gate = RiskGate(make_settings(max_per_market_usdc=5, stake_usdc=5, max_exposure_usdc=1000))
    gate.record(_signal(Action.TAKE_STALE, hash_="0x1"))
    assert gate.allow(_signal(Action.TAKE_STALE, hash_="0x1")) == "max per-market exposure"


def test_cancel_clears_exposure() -> None:
    gate = RiskGate(make_settings(max_open_markets=1))
    gate.record(_signal(hash_="0x1"))
    gate.record(_signal(Action.CANCEL, hash_="0x1"))
    assert gate.allow(_signal(hash_="0x2")) is None


def test_stake_matches_settings() -> None:
    gate = RiskGate(make_settings(stake_usdc=7.5))
    assert gate.stake() == to_base_units(7.5)


def test_record_uses_the_stake_that_was_actually_sent() -> None:
    gate = RiskGate(make_settings(stake_usdc=5, max_exposure_usdc=1000, max_per_market_usdc=25))
    gate.record(_signal(hash_="0x1"), stake=7_000_000)
    assert gate.exposure.net("0x1") == 7_000_000


def test_releases_slot_after_kickoff_when_live_is_blocked() -> None:
    gate = RiskGate(make_settings(allow_live=False, max_open_markets=1, max_exposure_usdc=1000))
    live_soon = replace(_signal(hash_="0x1"), market=make_market(market_hash="0x1", game_time=1_000))
    gate.record(live_soon)
    assert gate.allow(_signal(hash_="0x2")) == "max open markets"
    assert gate.release_finished(now=1_001) == ["0x1"]
    assert gate.allow(_signal(hash_="0x2")) is None
    assert gate.allow(_signal(hash_="0x1")) == "already on this side"


def test_releases_paper_slot_after_ttl() -> None:
    gate = RiskGate(
        make_settings(dry_run=True, paper_slot_seconds=10, max_open_markets=1, max_exposure_usdc=1000)
    )
    gate.record(_signal(hash_="0x1"))
    gate.quoted_at["0x1"] = 1_000.0
    assert gate.release_finished(now=1_009) == []
    assert gate.release_finished(now=1_010) == ["0x1"]
    assert gate.allow(_signal(hash_="0x2")) is None


def test_blocks_the_other_market_on_the_same_event() -> None:
    gate = RiskGate(make_settings(max_exposure_usdc=1000, max_per_market_usdc=25))
    guemes = replace(
        _signal(hash_="0x1"),
        market=make_market(market_hash="0x1", event_id="evt-9", outcome_one="Guemes"),
    )
    not_guemes = replace(
        _signal(hash_="0x2"),
        market=make_market(market_hash="0x2", event_id="evt-9", outcome_one="Not Guemes"),
    )
    gate.record(guemes)
    assert gate.allow(not_guemes) == "already in this event"
    other_event = replace(
        _signal(hash_="0x3"),
        market=make_market(market_hash="0x3", event_id="evt-10"),
    )
    assert gate.allow(other_event) is None


def test_keeps_tennis_dog_slot_through_kickoff() -> None:
    gate = RiskGate(
        make_settings(
            allow_live=False,
            max_open_markets=1,
            max_exposure_usdc=1000,
            tennis_dog_live_hours=5,
        )
    )
    join = replace(
        _signal(hash_="0x1"),
        market=make_market(market_hash="0x1", game_time=1_000),
        style="tennis_dog",
    )
    gate.record(join)
    assert gate.release_finished(now=1_001) == []
    assert gate.allow(_signal(hash_="0x2")) == "max open markets"
    assert gate.release_finished(now=1_000 + 5 * 3600) == ["0x1"]
    assert gate.allow(_signal(hash_="0x2")) is None


def test_hydrate_restores_event_lock_and_tennis_dog_watch() -> None:
    gate = RiskGate(make_settings(max_exposure_usdc=1000, max_per_market_usdc=25, tennis_dog_live_hours=5))
    now = 2_000
    gate.hydrate(
        [
            {
                "action": "join_maker",
                "market": "0x1",
                "side": "outcome_one",
                "event_id": "evt-9",
                "style": "tennis_dog",
                "game_time": now + 60,
                "ts": now - 10,
            }
        ],
        now=now,
    )
    assert gate.allow(_signal(hash_="0x1")) == "already on this side"
    other = replace(
        _signal(hash_="0x2"),
        market=make_market(market_hash="0x2", event_id="evt-9"),
    )
    assert gate.allow(other) == "already in this event"
    assert "0x1" in gate.quoted


def test_blocks_live_market_even_when_watched() -> None:
    gate = RiskGate(make_settings(allow_live=False, watch_live=True))
    signal = _signal()
    live = make_market(market_hash="0x1", game_time=1)
    live_signal = replace(signal, market=live)
    assert gate.allow(live_signal) == "live market disabled"


def _soccer_signal(
    *,
    hash_: str,
    event_id: str,
    outcome_one: str,
    outcome_two: str,
    side: Side,
    game_time: int,
    type_: int = 52,
) -> Signal:
    return Signal(
        market=make_market(
            market_hash=hash_,
            event_id=event_id,
            sport_id=5,
            sport_label="Soccer",
            type=type_,
            league_id=99,
            league_label="Brasileiro Serie B",
            outcome_one=outcome_one,
            outcome_two=outcome_two,
            team_one=outcome_one,
            team_two=outcome_two,
            game_time=game_time,
        ),
        side=side,
        action=Action.TAKE_FLOW,
        maker_odds=50_000_000_000_000_000_000,
        reason="test",
        mid_move_bps=40,
        imbalance=0.3,
        confidence=0.8,
        style="soccer",
    )


def test_blocks_soccer_win_and_not_win_across_markets() -> None:
    """Náutico Capibaribe vs Botafogo and Capibaribe / Not Capibaribe are one match."""
    import time as time_mod

    kick = int(time_mod.time()) + 4 * 3600
    gate = RiskGate(make_settings(max_exposure_usdc=1000, max_per_market_usdc=25))
    capibaribe = _soccer_signal(
        hash_="0x52",
        event_id="L19935126",
        outcome_one="Nautico Capibaribe",
        outcome_two="Botafogo SP",
        side=Side.OUTCOME_ONE,
        game_time=kick,
        type_=52,
    )
    not_win = _soccer_signal(
        hash_="0x01",
        event_id="L-OTHER",
        outcome_one="Nautico Capibaribe",
        outcome_two="Not Nautico Capibaribe",
        side=Side.OUTCOME_TWO,
        game_time=kick,
        type_=1,
    )
    gate.record(capibaribe)
    assert gate.allow(not_win) == "already on this team"
    assert gate.allow(capibaribe) == "already on this side"


def test_hydrate_soccer_blocks_the_not_team_market() -> None:
    import time as time_mod

    kick = int(time_mod.time()) + 4 * 3600
    gate = RiskGate(make_settings(dry_run=False, max_exposure_usdc=1000, max_per_market_usdc=25))
    gate.hydrate(
        [
            {
                "action": "take_flow",
                "market": "0x52",
                "side": "outcome_one",
                "style": "soccer",
                "event_id": "L19935126",
                "game_time": kick,
                "league": "Brasileiro Serie B",
                "outcome_one": "Nautico Capibaribe",
                "outcome_two": "Botafogo SP",
                "dry_run": False,
                "live_filled": True,
                "stake": "1000000",
            }
        ]
    )
    not_win = _soccer_signal(
        hash_="0x01",
        event_id="L-OTHER",
        outcome_one="Nautico Capibaribe",
        outcome_two="Not Nautico Capibaribe",
        side=Side.OUTCOME_TWO,
        game_time=kick,
        type_=1,
    )
    assert gate.allow(not_win) == "already on this team"
