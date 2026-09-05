from sxbot.units import (
    EXTRA_LADDER,
    ODDS_SCALE,
    OddsLadder,
    american,
    complement_decimal,
    from_percent,
    payout,
    taker_capacity,
    taker_odds,
    to_base_units,
    to_percent,
    to_usdc,
)


def test_percent_round_trip_docs_examples() -> None:
    assert to_percent(40_000_000_000_000_000_000) == 40.0
    assert from_percent(40) == 40_000_000_000_000_000_000
    assert from_percent(40.125) == 40_125_000_000_000_000_000


def test_taker_odds_and_capacity() -> None:
    maker = from_percent(52)
    assert taker_odds(maker) == from_percent(48)
    # 2 USDC at 52% -> taker capacity 2 * 48/52
    cap = taker_capacity(2_000_000, maker)
    assert abs(to_usdc(cap) - (2 * 48 / 52)) < 1e-6


def test_payout_40_percent() -> None:
    result = payout(1_000_000, from_percent(40))
    assert result == 2_500_000


def test_ladder_from_docs() -> None:
    ladder = OddsLadder(125)
    assert ladder.on_ladder(from_percent(40))
    assert ladder.on_ladder(from_percent(40.125))
    assert not ladder.on_ladder(from_percent(40.1))
    assert ladder.on_ladder(10**17)
    assert ladder.on_ladder(999 * 10**17)
    assert not ladder.on_ladder(0)
    assert not ladder.on_ladder(ODDS_SCALE)
    assert ladder.round_down(from_percent(40.1)) == from_percent(40)
    assert ladder.round_up(from_percent(40.1)) == from_percent(40.125)
    assert ladder.tick_down(from_percent(53), 1) == from_percent(52.875)
    assert 10**17 in EXTRA_LADDER


def test_american_and_usdc() -> None:
    assert american(0.4) == 150
    assert american(0.6) == -150
    assert to_base_units(5) == 5_000_000
    assert to_usdc(5_000_000) == 5.0


def test_complement_decimal_is_the_mirror_fill() -> None:
    # We make Cincy at 1.60 → someone betting SF at 2.67 is what fills us.
    assert abs(complement_decimal(1.60) - (1.60 / 0.60)) < 1e-9
    assert round(complement_decimal(1.60), 2) == 2.67
    assert complement_decimal(2.0) == 2.0
    assert complement_decimal(1.0) == 0.0
