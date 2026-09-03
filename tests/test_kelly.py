from sxbot.kelly import (
    full_kelly,
    shadow_kelly_from_row,
    shadow_kelly_usdc,
    sized_take_usdc,
    take_stake_usdc,
)
from sxbot.models import Action, Side, Signal
from sxbot.risk import RiskGate
from sxbot.units import from_percent, to_base_units
from tests.conftest import make_market, make_settings


def test_full_kelly_even_money() -> None:
    # p=0.55 at 2.00 → f* = 0.10
    assert abs(full_kelly(0.55, 2.0) - 0.10) < 1e-9
    assert full_kelly(0.50, 2.0) == 0.0
    assert full_kelly(0.40, 2.0) == 0.0


def test_mid_kelly_sizes_between_half_and_three_quarter() -> None:
    # f*=0.10, 5/8 Kelly on $1000 = $62.50, then cap at max_per_market $25
    stake = take_stake_usdc(
        p=0.55,
        decimal=2.0,
        bankroll=1000,
        fraction=0.625,
        min_usdc=5,
        max_usdc=25,
        max_frac=0.05,
    )
    assert stake == 25.0
    half = take_stake_usdc(
        p=0.55,
        decimal=2.0,
        bankroll=1000,
        fraction=0.5,
        min_usdc=5,
        max_usdc=100,
        max_frac=0.05,
    )
    assert abs(half - 50.0) < 1e-9  # 0.10 * 0.5 * 1000, then max_frac 5% → 50


def test_tiny_edge_is_skipped_not_rounded_up() -> None:
    # f* tiny → 5/8 Kelly on $1000 is below $5 min
    stake = take_stake_usdc(
        p=0.501,
        decimal=2.0,
        bankroll=1000,
        fraction=0.625,
        min_usdc=5,
        max_usdc=25,
        max_frac=0.05,
    )
    assert stake is None


def test_sized_take_uses_signal_fair_odds() -> None:
    market = make_market()
    signal = Signal(
        market=market,
        side=Side.OUTCOME_ONE,
        action=Action.TAKE_STALE,
        maker_odds=from_percent(50.0),
        reason="steam",
        mid_move_bps=40,
        imbalance=0.2,
        confidence=0.8,
        fair_odds=from_percent(55.0),
    )
    settings = make_settings(bankroll_usdc=1000, kelly_fraction=0.625, max_per_market_usdc=25)
    assert sized_take_usdc(settings, signal) == 25.0
    flow = Signal(
        market=market,
        side=Side.OUTCOME_TWO,
        action=Action.TAKE_FLOW,
        maker_odds=from_percent(47.0),
        reason="steam take",
        mid_move_bps=40,
        imbalance=0.2,
        confidence=0.8,
        fair_odds=from_percent(47.0),
    )
    # Botswana-style steam takes stay a flat $5. Kelly is only for stale leftover.
    assert sized_take_usdc(settings, flow) is None
    join = Signal(
        market=market,
        side=Side.OUTCOME_ONE,
        action=Action.JOIN_MAKER,
        maker_odds=from_percent(50.0),
        reason="join",
        mid_move_bps=40,
        imbalance=0.2,
        confidence=0.8,
        fair_odds=from_percent(55.0),
    )
    assert sized_take_usdc(settings, join) is None
    assert shadow_kelly_usdc(settings, join) == 25.0


def test_shadow_kelly_skips_join_with_no_edge() -> None:
    market = make_market()
    join = Signal(
        market=market,
        side=Side.OUTCOME_ONE,
        action=Action.JOIN_MAKER,
        maker_odds=from_percent(50.0),
        reason="join",
        mid_move_bps=40,
        imbalance=0.2,
        confidence=0.8,
        fair_odds=from_percent(50.0),
    )
    settings = make_settings(bankroll_usdc=1000, kelly_fraction=0.625)
    assert shadow_kelly_usdc(settings, join) is None


def test_shadow_kelly_from_row_uses_fair_pct_and_stamped_size() -> None:
    settings = make_settings(bankroll_usdc=1000, kelly_fraction=0.625, max_per_market_usdc=25)
    row = {
        "action": "join_maker",
        "odds": str(from_percent(50.0)),
        "fair_pct": 55.0,
    }
    assert shadow_kelly_from_row(settings, row) == 25.0
    row["kelly_stake_usdc"] = 12.5
    assert shadow_kelly_from_row(settings, row) == 12.5
    skip = {"action": "join_maker", "odds": str(from_percent(50.0))}
    assert shadow_kelly_from_row(settings, skip) is None


def test_risk_stake_for_skips_no_edge_take() -> None:
    gate = RiskGate(make_settings(bankroll_usdc=1000, kelly_on_takes=True))
    signal = Signal(
        market=make_market(),
        side=Side.OUTCOME_ONE,
        action=Action.TAKE_STALE,
        maker_odds=from_percent(50.0),
        reason="steam",
        mid_move_bps=40,
        imbalance=0.2,
        confidence=0.8,
        fair_odds=from_percent(50.0),
    )
    assert gate.stake_for(signal) is None
    flow = Signal(
        market=make_market(),
        side=Side.OUTCOME_TWO,
        action=Action.TAKE_FLOW,
        maker_odds=from_percent(39.0),
        reason="steam take",
        mid_move_bps=40,
        imbalance=0.2,
        confidence=0.8,
        fair_odds=from_percent(39.0),
    )
    assert gate.stake_for(flow) == to_base_units(5)


def test_kelly_live_cap_scales_shadow_25_to_4() -> None:
    gate = RiskGate(
        make_settings(
            stake_usdc=1,
            max_per_market_usdc=4,
            kelly_live_cap=True,
            bankroll_usdc=1000,
        )
    )
    join = Signal(
        market=make_market(),
        side=Side.OUTCOME_ONE,
        action=Action.JOIN_MAKER,
        maker_odds=from_percent(50.0),
        reason="makers shifted",
        mid_move_bps=80,
        imbalance=0.2,
        confidence=0.8,
        fair_odds=from_percent(55.0),
    )
    assert gate.stake_for(join) == to_base_units(4)
    skip = Signal(
        market=make_market(),
        side=Side.OUTCOME_ONE,
        action=Action.JOIN_MAKER,
        maker_odds=from_percent(50.0),
        reason="no edge",
        mid_move_bps=10,
        imbalance=0.1,
        confidence=0.5,
        fair_odds=from_percent(50.0),
    )
    assert gate.stake_for(skip) == to_base_units(1)


def test_allow_uses_passed_kelly_stake() -> None:
    gate = RiskGate(make_settings(max_per_market_usdc=10, max_exposure_usdc=1000, stake_usdc=5))
    signal = Signal(
        market=make_market(market_hash="0x1", event_id="e1"),
        side=Side.OUTCOME_ONE,
        action=Action.TAKE_FLOW,
        maker_odds=from_percent(50.0),
        reason="steam",
        mid_move_bps=40,
        imbalance=0.2,
        confidence=0.8,
        fair_odds=from_percent(55.0),
    )
    assert gate.allow(signal, stake=to_base_units(5)) is None
    assert gate.allow(signal, stake=to_base_units(11)) == "max per-market exposure"
