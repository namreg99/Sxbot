"""Skip markets that the paper sample already showed are -EV or noise.

These are order gates, not flow-intel gates. `sxbot flow` / scoreboard can
still see totals and NPB; `run` should not bet them by default.
"""

from __future__ import annotations

import time

from sxbot.config import Settings
from sxbot.models import Market
from sxbot.units import decimal_odds, to_prob
from sxbot.wallets import SPORT_BASEBALL, SPORT_FOOTBALL, SPORT_SOCCER, SPORT_TENNIS

TOTAL_TYPES = {2, 28, 236}
# Paper styles. Each `sxbot run` join goes to sxbot-paper-{style}.jsonl.
QUOTE_STYLES = ("mlb", "soccer", "tennis_short", "tennis_dog", "mm")
STYLE_MM = "mm"

# Type 226 is SX's MLB moneyline. Type 1 is soccer Team / Not Team.
MLB_MONEYLINE_TYPE = 226
SOCCER_TWO_WAY_TYPE = 1
NFL_LEAGUE_ID = 243

STYLE_TENNIS_DOG = "tennis_dog"


def order_skip_reason(market: Market, settings: Settings, *, now: int | None = None) -> str | None:
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
    far = kickoff_skip_reason(market, settings, now=now)
    if far:
        return far
    return None


def kickoff_skip_reason(market: Market, settings: Settings, *, now: int | None = None) -> str | None:
    """Sept NFL / Oct NBA futures eat the 8-slot cap if we join them in August."""
    hours = float(settings.max_kickoff_hours or 0)
    if hours <= 0 or not market.game_time:
        return None
    now = int(now if now is not None else time.time())
    if market.game_time <= now:
        return None
    if (market.game_time - now) / 3600.0 > hours:
        return "futures"
    return None


def longshot_skip_reason(price: int, settings: Settings) -> str | None:
    cap = float(settings.max_order_decimal or 0)
    if cap <= 0 or price <= 0:
        return None
    dec = decimal_odds(to_prob(price))
    if dec > cap:
        return f"longshot {dec:.2f} > {cap}"
    return None


def _league(market: Market) -> str:
    return (market.league_label or "").strip().lower()


def _sport(market: Market) -> str:
    return (market.sport_label or "").strip().lower()


def is_not_tie(market: Market) -> bool:
    o1 = (market.outcome_one or "").lower()
    o2 = (market.outcome_two or "").lower()
    return "not tie" in o1 or "not tie" in o2 or o1 in {"tie", "not tie"} or o2 in {"tie", "not tie"}


def is_mlb(market: Market) -> bool:
    label = _league(market)
    if any(tok in label for tok in ("npb", "kbo", "nippon professional", "korea baseball")):
        return False
    if "mlb" in label or "major league baseball" in label:
        return True
    if market.type == MLB_MONEYLINE_TYPE:
        return True
    return False


def is_nfl(market: Market) -> bool:
    label = _league(market)
    if "nfl" in label:
        return True
    if market.sport_id != SPORT_FOOTBALL:
        return False
    return market.league_id == NFL_LEAGUE_ID


def is_soccer_ml(market: Market) -> bool:
    if is_not_tie(market):
        return False
    if market.type == SOCCER_TWO_WAY_TYPE:
        return True
    if market.sport_id == SPORT_SOCCER and market.type in {1, 52}:
        return True
    return False


def is_tennis(market: Market) -> bool:
    if market.sport_id == SPORT_TENNIS:
        return True
    return "tennis" in _sport(market)


def quote_family(market: Market) -> str | None:
    """Sport/league bucket for scan preference. Price bands are applied later."""
    if is_soccer_ml(market):
        return "soccer"
    if is_mlb(market):
        return "mlb"
    if is_nfl(market):
        return "soccer"
    if is_tennis(market):
        return "tennis"
    return None


def _in_band(dec: float, low: float, high: float) -> bool:
    return low - 1e-9 <= dec <= high + 1e-9


def quote_style(market: Market, price: int, settings: Settings) -> str | None:
    """Which paper log a join belongs in, or None if we should not quote.

    mlb           — MLB ML/spread 1.80–2.20
    soccer        — soccer type-1 (not Not-tie) 1.12–2.20, plus NFL 1.80–2.20
    tennis_short  — tennis 1.12–1.80
    tennis_dog    — tennis 2.20–3.50, pregame entry, live exit
    """
    if price <= 0:
        return None
    if quote_family(market) is None:
        return None
    dec = decimal_odds(to_prob(price))
    cap = float(settings.max_order_decimal or 3.5)
    if is_soccer_ml(market) and _in_band(dec, 1.12, 2.20):
        return "soccer"
    if is_mlb(market) and _in_band(dec, 1.80, 2.20):
        return "mlb"
    if is_nfl(market) and _in_band(dec, 1.80, 2.20):
        return "soccer"
    if is_tennis(market):
        if _in_band(dec, 1.12, 1.80):
            return "tennis_short"
        dog_hi = min(3.50, cap) if cap > 0 else 3.50
        if _in_band(dec, 2.20, dog_hi):
            return "tennis_dog"
    return None
