from sxbot.models import Action, Side, Signal
from sxbot.risk import RiskGate
from sxbot.units import to_base_units
from tests.conftest import make_market, make_settings


def _signal(action: Action = Action.JOIN_MAKER, hash_: str = "0x1") -> Signal:
    market = make_market(market_hash=hash_)
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
    assert gate.allow(_signal(hash_="0x1")) is None


def test_blocks_per_market_cap() -> None:
    gate = RiskGate(make_settings(max_per_market_usdc=5, stake_usdc=5, max_exposure_usdc=1000))
    gate.record(_signal(hash_="0x1"))
    assert gate.allow(_signal(hash_="0x1")) == "max per-market exposure"


def test_cancel_clears_exposure() -> None:
    gate = RiskGate(make_settings(max_open_markets=1))
    gate.record(_signal(hash_="0x1"))
    gate.record(_signal(Action.CANCEL, hash_="0x1"))
    assert gate.allow(_signal(hash_="0x2")) is None


def test_stake_matches_settings() -> None:
    gate = RiskGate(make_settings(stake_usdc=7.5))
    assert gate.stake() == to_base_units(7.5)
