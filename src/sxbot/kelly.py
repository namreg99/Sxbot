"""Fractional Kelly sizing for paper takes and a shadow book on every unique.

Full Kelly is too jumpy. Half-Kelly (0.50) is the usual moderate setting;
three-quarter (0.75) is aggressive. Default 0.625 sits in the middle.

Fair probability is the book's mid — the same number the orange/global line
is trying to represent. SX's public orderbook snapshot does not include that
orange line, so mid is the proxy until the UI field is on the API.

Joins, MM, and Botswana-style steam takes (`TAKE_FLOW`) still *execute* at
the flat `SX_STAKE_USDC` ($5). Kelly is the comparable shadow book on the
board. Leftover crossed quotes (`TAKE_STALE`) still size live with Kelly.
A no-edge shadow does not count as a Kelly loss.
"""

from __future__ import annotations

from typing import Any

from sxbot.models import Action, Signal
from sxbot.units import decimal_odds, to_prob

TAKE_ACTIONS = {Action.TAKE_STALE, Action.TAKE_FLOW}
KELLY_ACTIONS = {Action.TAKE_STALE}
TRADE_ACTIONS = {Action.JOIN_MAKER, Action.TAKE_STALE, Action.TAKE_FLOW, Action.MM_FILL}
_TRADE_ACTION_VALUES = {action.value for action in TRADE_ACTIONS}


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


def kelly_stake_usdc(settings: object, *, p: float, decimal: float) -> float | None:
    """⅝ Kelly size for any action given fair p vs posted decimal odds."""
    return take_stake_usdc(
        p=p,
        decimal=decimal,
        bankroll=float(getattr(settings, "bankroll_usdc", 1000)),
        fraction=float(getattr(settings, "kelly_fraction", 0.625)),
        min_usdc=float(getattr(settings, "stake_usdc", 5)),
        max_usdc=float(getattr(settings, "max_per_market_usdc", 25)),
        max_frac=float(getattr(settings, "kelly_max_frac", 0.05)),
    )


def sized_take_usdc(settings: object, signal: Signal) -> float | None:
    """Kelly size for a stale leftover take, or None to skip.

    Steam takes (`TAKE_FLOW`) stay the flat $5 — same team as the makers.
    """
    if signal.action not in KELLY_ACTIONS:
        return None
    if not bool(getattr(settings, "kelly_on_takes", True)):
        return float(getattr(settings, "stake_usdc", 5))
    p = fair_prob(signal)
    if p is None:
        return float(getattr(settings, "stake_usdc", 5))
    return kelly_stake_usdc(
        settings,
        p=p,
        decimal=decimal_odds(to_prob(signal.maker_odds)),
    )


def shadow_kelly_usdc(settings: object, signal: Signal) -> float | None:
    """Kelly size for the shadow unique book. None = skip, not a loss.

    Used for joins, takes, and MM fills. Execution of joins/MM stays flat.
    """
    if signal.action not in TRADE_ACTIONS:
        return None
    p = fair_prob(signal)
    if p is None:
        return None
    return kelly_stake_usdc(
        settings,
        p=p,
        decimal=decimal_odds(to_prob(signal.maker_odds)),
    )


def _row_fair_prob(row: dict[str, Any]) -> float | None:
    fair_pct = row.get("fair_pct")
    if fair_pct not in (None, ""):
        try:
            return float(fair_pct) / 100.0
        except (TypeError, ValueError):
            pass
    fair_odds = row.get("fair_odds")
    if fair_odds not in (None, "", 0, "0"):
        try:
            return to_prob(int(fair_odds))
        except (TypeError, ValueError):
            pass
    return None


def shadow_kelly_from_row(settings: object, row: dict[str, Any]) -> float | None:
    """Replay Kelly from a paper row. Prefer the stamped size, else fair vs odds."""
    action = str(row.get("action") or "")
    if action not in _TRADE_ACTION_VALUES:
        return None
    stamped = row.get("kelly_stake_usdc")
    if stamped not in (None, ""):
        try:
            value = float(stamped)
        except (TypeError, ValueError):
            value = 0.0
        if value > 0:
            return value
        return None
    p = _row_fair_prob(row)
    if p is None:
        return None
    try:
        odds = int(row.get("odds") or 0)
    except (TypeError, ValueError):
        return None
    if odds <= 0:
        return None
    return kelly_stake_usdc(settings, p=p, decimal=decimal_odds(to_prob(odds)))
