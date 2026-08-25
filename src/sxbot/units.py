"""Odds and size conversions for the SX Bet V3 API.

Odds on the wire are implied probability * 10^20, sent as strings.
USDC amounts are integer base units (6 decimals on both mainnet and testnet today).
"""

from __future__ import annotations

ODDS_SCALE = 10**20
BPS_SCALE = 10_000  # 1 bp = 0.01 percentage points of implied probability
EXTRA_LADDER = frozenset({10**17, 999 * 10**17})  # 0.1% and 99.9%


def to_prob(odds: int) -> float:
    """Implied probability in [0, 1]. Display only — keep trading math in ints."""
    return odds / ODDS_SCALE


def to_percent(odds: int) -> float:
    """Implied probability as a percent, 3 dp."""
    return (odds * 100_000 // ODDS_SCALE) / 1000


def from_percent(pct: float) -> int:
    """Percent (e.g. 40.125) → 1e20-scaled odds. Does not snap to the ladder."""
    return round(pct * 1000) * ODDS_SCALE // 100_000


def from_prob(prob: float) -> int:
    return from_percent(prob * 100)


def taker_odds(maker_odds: int) -> int:
    return ODDS_SCALE - maker_odds


def taker_capacity(size: int, maker_odds: int) -> int:
    """Stake a taker can put up against a resting maker level."""
    if maker_odds <= 0:
        return 0
    return size * (ODDS_SCALE - maker_odds) // maker_odds


def payout(stake: int, own_odds: int) -> int:
    """Gross return (stake included) at the staker's own implied odds."""
    if own_odds <= 0:
        return 0
    return stake * ODDS_SCALE // own_odds


def to_base_units(usdc: float, decimals: int = 6) -> int:
    return round(usdc * 10**decimals)


def to_usdc(base: int, decimals: int = 6) -> float:
    return base / 10**decimals


def bps_of_odds(delta: int) -> int:
    """Convert an odds-scale delta into implied-probability basis points."""
    return delta * BPS_SCALE // ODDS_SCALE


def odds_from_bps(bps: int) -> int:
    return bps * ODDS_SCALE // BPS_SCALE


def american(prob: float) -> int:
    if prob <= 0 or prob >= 1:
        return 0
    if prob >= 0.5:
        return -round((prob / (1 - prob)) * 100)
    return round(((1 - prob) / prob) * 100)


def decimal_odds(prob: float) -> float:
    if prob <= 0:
        return 0.0
    return 1.0 / prob


def complement_decimal(decimal: float) -> float:
    """Taker decimal if you fill a maker quote at `decimal`.

    Makers on Cincinnati at 1.60 are betting the Reds. Taking that quote
    is San Francisco at 1.60 / 0.60 = 2.67.
    """
    if decimal <= 1.0:
        return 0.0
    return decimal / (decimal - 1.0)


class OddsLadder:
    """Snap prices onto the exchange ladder. Step size comes from GET /metadata/obv3."""

    def __init__(self, step_size: int) -> None:
        if step_size <= 0:
            raise ValueError("oddsLadderStepSize must be positive")
        self.step_size = step_size
        self.step = step_size * 10**15

    def on_ladder(self, odds: int) -> bool:
        if odds <= 0 or odds >= ODDS_SCALE:
            return False
        if odds in EXTRA_LADDER:
            return True
        return odds % self.step == 0

    def round_down(self, odds: int) -> int:
        snapped = (odds // self.step) * self.step
        return snapped if snapped > 0 else self.step

    def round_up(self, odds: int) -> int:
        if odds % self.step == 0:
            return odds
        snapped = ((odds // self.step) + 1) * self.step
        return snapped if snapped < ODDS_SCALE else ODDS_SCALE - self.step

    def clamp(self, odds: int) -> int:
        if self.on_ladder(odds):
            return odds
        return self.round_down(odds)

    def tick_down(self, odds: int, n: int = 1) -> int:
        """Worse price for a maker (lower implied probability)."""
        return self.clamp(odds - n * self.step)

    def tick_up(self, odds: int, n: int = 1) -> int:
        return self.clamp(odds + n * self.step)
