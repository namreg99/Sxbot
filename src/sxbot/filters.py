"""Skip markets that the paper sample already showed are -EV or noise.

These are order gates, not flow-intel gates. `sxbot flow` / scoreboard can
still see totals; `run` and `mimic` should not bet them by default.
"""

from __future__ import annotations

from sxbot.config import Settings
from sxbot.models import Market
from sxbot.units import decimal_odds, to_prob

TOTAL_TYPES = {2, 28, 236}


def order_skip_reason(market: Market, settings: Settings) -> str | None:
    o1 = (market.outcome_one or "").lower()
    o2 = (market.outcome_two or "").lower()
    if settings.skip_totals and (
        market.type in TOTAL_TYPES or "over" in o1 or "under" in o1
    ):
        return "totals"
    if settings.skip_not_tie and (
        "not tie" in o1 or "not tie" in o2 or o1 in {"tie", "not tie"} or o2 in {"tie", "not tie"}
    ):
        return "not-tie"
    return None


def longshot_skip_reason(price: int, settings: Settings) -> str | None:
    cap = float(settings.max_order_decimal or 0)
    if cap <= 0 or price <= 0:
        return None
    dec = decimal_odds(to_prob(price))
    if dec > cap:
        return f"longshot {dec:.2f} > {cap}"
    return None
