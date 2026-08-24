"""Fractional Kelly sizing for paper takes.

Full Kelly is too jumpy. Half-Kelly (0.50) is the usual moderate setting;
three-quarter (0.75) is aggressive. Default 0.625 sits in the middle.

Fair probability is the book's mid — the same number the orange/global line
is trying to represent. SX's public orderbook snapshot does not include that
orange line, so mid is the proxy until the UI field is on the API.
"""

from __future__ import annotations

from sxbot.models import Action, Signal
from sxbot.units import decimal_odds, to_prob

TAKE_ACTIONS = {Action.TAKE_STALE, Action.TAKE_FLOW}


def full_kelly(p: float, decimal: float) -> float:
    """Share of bankroll to bet at decimal odds `decimal` with win prob `p`."""
    if p <= 0 or p >= 1 or decimal <= 1:
        return 0.0
    edge = p * decimal - 1.0
    if edge <= 0:
        return 0.0
    return edge / (decimal - 1.0)


def take_stake_usdc(
    *,
    p: float,
    decimal: float,
    bankroll: float,
    fraction: float,
    min_usdc: float,
    max_usdc: float,
    max_frac: float,
) -> float | None:
    """Sized take, or None when there is no edge / size would be below min order.

    Does not round a tiny edge up to the exchange minimum — that would overbet.
    """
    f_star = full_kelly(p, decimal)
    if f_star <= 0 or bankroll <= 0:
        return None
    frac = max(float(fraction), 0.0)
    cap = max(float(max_frac), 0.0)
    f = min(f_star * frac, cap) if cap > 0 else f_star * frac
    stake = bankroll * f
    if stake + 1e-12 < float(min_usdc):
        return None
    ceiling = float(max_usdc) if max_usdc > 0 else stake
    return min(stake, ceiling)


def fair_prob(signal: Signal) -> float | None:
    if signal.fair_odds <= 0:
        return None
    return to_prob(signal.fair_odds)


def sized_take_usdc(settings: object, signal: Signal) -> float | None:
    """Kelly size for a take, or None to skip. Joins are not sized here."""
    if signal.action not in TAKE_ACTIONS:
        return None
    if not bool(getattr(settings, "kelly_on_takes", True)):
        return float(getattr(settings, "stake_usdc", 5))
    p = fair_prob(signal)
    if p is None:
        return float(getattr(settings, "stake_usdc", 5))
    return take_stake_usdc(
        p=p,
        decimal=decimal_odds(to_prob(signal.maker_odds)),
        bankroll=float(getattr(settings, "bankroll_usdc", 1000)),
        fraction=float(getattr(settings, "kelly_fraction", 0.625)),
        min_usdc=float(getattr(settings, "stake_usdc", 5)),
        max_usdc=float(getattr(settings, "max_per_market_usdc", 25)),
        max_frac=float(getattr(settings, "kelly_max_frac", 0.05)),
    )
