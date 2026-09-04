"""Skip markets that the paper sample already showed are -EV or noise.

These are order gates, not flow-intel gates. `sxbot flow` / scoreboard can
still see totals and NPB; `run` should not bet them by default.
"""

from __future__ import annotations

import time

from typing import Any

from sxbot.config import Settings
from sxbot.models import Market, Side
from sxbot.units import decimal_odds, to_prob
from sxbot.wallets import SPORT_BASEBALL, SPORT_FOOTBALL, SPORT_SOCCER, SPORT_TENNIS

TOTAL_TYPES = {2, 28, 236}
# Paper styles. Each `sxbot run` join goes to sxbot-paper-{style}.jsonl.
QUOTE_STYLES = ("mlb", "mlb_dog", "soccer", "soccer_dog", "tennis_short", "tennis_dog", "mm")
STYLE_MM = "mm"

# Type 226 is SX's MLB moneyline. Type 1 is soccer Team / Not Team.
MLB_MONEYLINE_TYPE = 226
MLB_SPREAD_TYPE = 342
SOCCER_TWO_WAY_TYPE = 1
NFL_LEAGUE_ID = 243
# Tennis set handicaps / totals / games — not the match moneyline.
TENNIS_NON_ML_TYPES = {3, 166, 201, 236, 342, 866} | TOTAL_TYPES

STYLE_TENNIS_DOG = "tennis_dog"
STYLE_SOCCER_DOG = "soccer_dog"
STYLE_MLB_DOG = "mlb_dog"
# Steam/rotation dogs. Parked-depth (tob_lag) on these was a trap on tape.
STEAM_DOG_STYLES = frozenset({STYLE_TENNIS_DOG, STYLE_SOCCER_DOG, STYLE_MLB_DOG})
# No unique W–L yet — live stays the $1 floor even when Kelly would want $4.
FLAT_LIVE_DOG_STYLES = frozenset({STYLE_SOCCER_DOG, STYLE_MLB_DOG})


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


def bare_team_name(name: str) -> str:
    """'Not Náutico Capibaribe' and 'Nautico Capibaribe' lock as the same team."""
    text = " ".join((name or "").casefold().split())
    if text.startswith("not "):
        text = text[4:].strip()
    return text


def soccer_team_lock_token(market: Market, side: Side) -> tuple[str, ...] | None:
    """Hour + league + team. Type-1 Not-Team shares this with Team vs Team."""
    if not is_soccer_ml(market):
        return None
    picked = market.outcome_one if side is Side.OUTCOME_ONE else market.outcome_two
    team = bare_team_name(picked)
    if not team:
        return None
    kick = int(market.game_time or 0)
    hour = str(kick // 3600) if kick else "0"
    league = (market.league_label or str(market.league_id or "")).casefold()
    return ("soccer", league, hour, team)


def soccer_team_lock_token_from_row(row: dict[str, Any]) -> tuple[str, ...] | None:
    if str(row.get("style") or "") not in {"soccer", STYLE_SOCCER_DOG}:
        o1 = str(row.get("outcome_one") or "")
        o2 = str(row.get("outcome_two") or "")
        if "not " not in f"{o1} {o2}".casefold():
            return None
    side = str(row.get("side") or "")
    picked = str(row.get("outcome_two") or "") if side == Side.OUTCOME_TWO.value else str(
        row.get("outcome_one") or ""
    )
    team = bare_team_name(picked)
    if not team:
        return None
    kick = int(row.get("game_time") or 0)
    hour = str(kick // 3600) if kick else "0"
    league = str(row.get("league") or "").casefold()
    return ("soccer", league, hour, team)


def is_tennis(market: Market) -> bool:
    if market.sport_id == SPORT_TENNIS:
        return True
    return "tennis" in _sport(market)


def is_tennis_ml(market: Market) -> bool:
    if not is_tennis(market):
        return False
    if market.type in TENNIS_NON_ML_TYPES:
        return False
    o1 = (market.outcome_one or "").lower()
    if "over" in o1 or "under" in o1:
        return False
    if "+" in o1 or "-1.5" in o1:
        return False
    return True


def mm_family(market: Market) -> str | None:
    """Pregame maker universe. Tighter than quote_family (follow bot).

    Soccer type-52 (Team / Team) and NFL making were red in the archive.
    Basketball pregame making was red. Totals stay out.
    """
    if is_soccer_ml(market) and market.type == SOCCER_TWO_WAY_TYPE:
        return "soccer"
    if is_mlb(market) and market.type in {MLB_MONEYLINE_TYPE, MLB_SPREAD_TYPE}:
        return "mlb"
    if is_tennis_ml(market):
        return "tennis"
    return None


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
    mlb_dog       — MLB moneyline 2.20–3.50 (steam/rotation only; flat $1 live)
    soccer        — soccer favs 1.12–1.80, plus NFL pick'em 1.80–2.20
    soccer_dog    — soccer 2.20–3.50 (steam/rotation only; flat $1 live)
    tennis_short  — tennis 1.12–1.80
    tennis_dog    — tennis 2.20–3.50, pregame entry, live exit

    Soccer pick'em 1.80–2.20 is dropped (unique follow was coin-flip / slightly red).
    """
    if price <= 0:
        return None
    if quote_family(market) is None:
        return None
    dec = decimal_odds(to_prob(price))
    cap = float(settings.max_order_decimal or 3.5)
    dog_hi = min(3.50, cap) if cap > 0 else 3.50
    style: str | None = None
    if is_soccer_ml(market):
        if _in_band(dec, 1.12, 1.80):
            style = "soccer"
        elif _in_band(dec, 2.20, dog_hi):
            style = STYLE_SOCCER_DOG
    elif is_mlb(market) and market.type == MLB_MONEYLINE_TYPE and _in_band(dec, 2.20, dog_hi):
        style = STYLE_MLB_DOG
    elif is_mlb(market) and _in_band(dec, 1.80, 2.20):
        style = "mlb"
    elif is_nfl(market) and _in_band(dec, 1.80, 2.20):
        style = "soccer"
    elif is_tennis(market):
        if _in_band(dec, 1.12, 1.80):
            style = "tennis_short"
        elif _in_band(dec, 2.20, dog_hi):
            style = "tennis_dog"
    skipped = {s.strip().lower() for s in settings.skip_styles if s}
    if style and style in skipped:
        return None
    return style


def _half_open(dec: float, low: float, high: float) -> bool:
    """low <= decimal < high. Same edges as archive ODDS_BUCKETS."""
    return low - 1e-12 <= dec < high - 1e-12


def mm_quote_style(market: Market, price: int) -> str | None:
    """Where pregame *maker fills* were not red. Not the follow-bot bands.

    Pick'em (1.80–2.20) is the worst maker bucket in soccer, MLB ML, and tennis.
    Follow-bot soccer pick'em is dropped; NFL/MLB pick'em still steam-join.
    """
    if price <= 0:
        return None
    if mm_family(market) is None:
        return None
    dec = decimal_odds(to_prob(price))
    if is_soccer_ml(market) and market.type == SOCCER_TWO_WAY_TYPE:
        # type-1 shorts +5%, dogs +17%; fav/pick red.
        if _half_open(dec, 1.12, 1.40) or _half_open(dec, 2.20, 3.50):
            return "soccer"
        return None
    if is_mlb(market) and market.type == MLB_MONEYLINE_TYPE:
        # fav +15%, dog +48%; pick'em −7%.
        if _half_open(dec, 1.40, 1.80) or _half_open(dec, 2.20, 3.50):
            return "mlb"
        return None
    if is_mlb(market) and market.type == MLB_SPREAD_TYPE:
        # run-line shorts/favs +11–16%; pick/dog red.
        if _half_open(dec, 1.12, 1.80):
            return "mlb"
        return None
    if is_tennis_ml(market):
        # fav +7.5%, dog +5.6%; pick'em −9.5%.
        if _half_open(dec, 1.40, 1.80) or _half_open(dec, 2.20, 3.50):
            return "tennis"
        return None
    return None
