"""Turn classified flow into join (maker) and/or take (taker) actions.

SX_FOLLOW_STYLE:
- join       — rest behind makers (default). Still take leftover crossed quotes.
- take       — hit the informed side now (pay the spread, same team as the steam).
- mixed      — take on strong steam/rotation; otherwise join.
- take_first — hit the other side to get the same team. Do not sit as a maker.

The follow-bot (`sxbot run`) uses maker bias to bet *with* the makers.
Filling the heavy quotes (the other team) is not this strategy.
The maker-bot (`sxbot mm`) is a separate process and stays a maker.
"""

from __future__ import annotations

from dataclasses import replace

from sxbot.config import Settings
from sxbot.filters import STEAM_DOG_STYLES, longshot_skip_reason, order_skip_reason, quote_style
from sxbot.flow import FlowReport, Motive, classify
from sxbot.models import Action, Market, PublicTrade, Signal, Side
from sxbot.orderbook import BookView
from sxbot.units import ODDS_SCALE, OddsLadder, decimal_odds, taker_odds, to_prob

# Same cutoff as the board "best priced" card. Non-EV shorts in this band
# were the −32% / −44% unique slices; we fade them instead of taking them.
_PRICED_SHORT_MAX = 1.80
_FADE_REASON = "fade non-EV short"


def _join_odds(view: BookView, side: Side, ladder: OddsLadder, ticks_behind: int) -> int | None:
    best = view.best(side)
    if best is None:
        return None
    price = ladder.tick_down(best, max(ticks_behind, 0))
    if price <= 0:
        return None
    opposite_best = view.best(side.opposite())
    if opposite_best is not None:
        ask_for_us = taker_odds(opposite_best)
        if price >= ask_for_us:
            price = ladder.tick_down(ask_for_us, 1)
    if not ladder.on_ladder(price) or price <= 0:
        return None
    return price


def _take_against(view: BookView, side: Side) -> int | None:
    """Hit the opposite book to *get* `side` (stale leftover / with the steam)."""
    opposite = view.best(side.opposite())
    if opposite is None:
        return None
    return taker_odds(opposite)


def _signal(
    market: Market,
    report: FlowReport,
    view: BookView,
    action: Action,
    price: int,
    *,
    crossed: bool,
    style: str,
    tracker_odds: int = 0,
) -> Signal:
    assert report.side is not None
    fair = 0
    if view.mid_one is not None:
        fair = view.mid_one if report.side is Side.OUTCOME_ONE else ODDS_SCALE - view.mid_one
    return Signal(
        market=market,
        side=report.side,
        action=action,
        maker_odds=price,
        reason="; ".join(report.reasons) or report.motive.value,
        mid_move_bps=report.move_bps,
        imbalance=view.imbalance,
        confidence=report.confidence,
        crossed=crossed,
        motive=report.motive.value,
        style=style,
        fair_odds=fair,
        tracker_odds=tracker_odds or price,
    )


def _priced(
    market: Market,
    report: FlowReport,
    view: BookView,
    action: Action,
    price: int,
    settings: Settings,
    *,
    crossed: bool,
    style_from: int | None = None,
) -> Signal | None:
    """`style_from` is the unique-follow touch. Takes can be a tick shorter and
    still belong in tennis_short / soccer; gating on the take decimal dropped them.
    """
    if longshot_skip_reason(price, settings):
        return None
    style = quote_style(market, style_from or price, settings)
    if not style and style_from:
        style = quote_style(market, price, settings)
    if not style:
        return None
    return _signal(
        market,
        report,
        view,
        action,
        price,
        crossed=crossed,
        style=style,
        tracker_odds=style_from or price,
    )


def _tob_lag_join_ok(price: int, settings: Settings, style: str | None) -> bool:
    """Parked-depth joins only on shorts/pick'em. Dogs are a trap on tape."""
    cap = float(settings.tob_lag_max_decimal or 2.20)
    if decimal_odds(to_prob(price)) > cap + 1e-9:
        return False
    if style in STEAM_DOG_STYLES:
        return False
    return True


def _size_on_side(imbalance: float, side: Side, min_imbalance: float) -> bool:
    if side is Side.OUTCOME_ONE:
        return imbalance >= min_imbalance
    return imbalance <= -min_imbalance


def _non_ev_priced_short(
    market: Market,
    report: FlowReport,
    view: BookView,
    settings: Settings,
) -> bool:
    """Favorite (≤1.80) whose parked-depth side does not have maker size.

    Unique tape: those shorts were −32% / tennis −44%. Opposite side is the fade.
    """
    if report.side is None:
        return False
    price = view.best(report.side)
    if not price:
        return False
    if quote_style(market, price, settings) is None:
        return False
    if decimal_odds(to_prob(price)) > _PRICED_SHORT_MAX + 1e-9:
        return False
    min_imb = float(settings.min_imbalance or 0.15)
    return not _size_on_side(view.imbalance, report.side, min_imb)


def evaluate(
    market: Market,
    prev: BookView,
    curr: BookView,
    settings: Settings,
    ladder: OddsLadder,
    trades: list[PublicTrade] | None = None,
    steam_hits: int = 0,
    report: FlowReport | None = None,
) -> list[Signal]:
    if order_skip_reason(market, settings):
        return []
    report = report or classify(prev, curr, settings, trades=trades, steam_hits=steam_hits)
    if not report.actionable or report.side is None:
        return []
    if (
        report.motive is Motive.MAKER_STEAM
        and report.persistence < float(settings.min_persistence or 0)
        and abs(report.move_bps) >= int(settings.flicker_bps or 0)
    ):
        return []

    follow = (settings.follow_style or "join").strip().lower()
    spread_bps = curr.spread_bps()
    wide_enough = spread_bps is not None and spread_bps >= settings.min_spread_bps
    signals: list[Signal] = []
    faded = False
    if report.motive is Motive.TOB_LAG and report.side is not None:
        if _non_ev_priced_short(market, report, curr, settings):
            report = replace(
                report,
                side=report.side.opposite(),
                reasons=(*report.reasons, _FADE_REASON),
            )
            faded = True
        elif not settings.join_tob_lag:
            return []

    if report.motive is Motive.CROSSED and settings.enable_take_stale:
        price = _take_against(curr, report.side)
        if price is not None:
            signal = _priced(
                market, report, curr, Action.TAKE_STALE, price, settings, crossed=True
            )
            if signal is not None:
                signals.append(signal)
                return signals

    take_first_motives = {Motive.MAKER_STEAM, Motive.SIZE_ROTATION}
    if settings.join_tob_lag or faded:
        take_first_motives.add(Motive.TOB_LAG)

    if (
        follow == "take_first"
        and settings.enable_take_stale
        and report.motive in take_first_motives
    ):
        take_price = _take_against(curr, report.side)
        if take_price is not None:
            skip_tob_dog = (
                report.motive is Motive.TOB_LAG
                and not faded
                and not _tob_lag_join_ok(
                    take_price, settings, quote_style(market, take_price, settings)
                )
            )
            if not skip_tob_dog:
                signal = _priced(
                    market,
                    report,
                    curr,
                    Action.TAKE_FLOW,
                    take_price,
                    settings,
                    crossed=curr.crossed,
                    style_from=curr.best(report.side),
                )
                if signal is not None:
                    signals.append(signal)
                    return signals

    take_now = settings.enable_take_stale and report.motive in {
        Motive.MAKER_STEAM,
        Motive.SIZE_ROTATION,
    } and (
        follow == "take"
        or (follow == "mixed" and report.confidence >= 0.7)
    )
    if take_now:
        price = _take_against(curr, report.side)
        if price is not None:
            signal = _priced(
                market, report, curr, Action.TAKE_FLOW, price, settings, crossed=curr.crossed,
                style_from=curr.best(report.side),
            )
            if signal is not None:
                signals.append(signal)
                return signals

    if follow in {"take", "take_first"}:
        return signals

    join_motives = {Motive.MAKER_STEAM, Motive.SIZE_ROTATION}
    if settings.join_tob_lag or faded:
        join_motives.add(Motive.TOB_LAG)
    if settings.enable_join_maker and report.motive in join_motives and (
        wide_enough or report.motive is not Motive.TOB_LAG
    ):
        price = _join_odds(curr, report.side, ladder, settings.join_ticks_behind)
        if price is not None:
            signal = _priced(
                market, report, curr, Action.JOIN_MAKER, price, settings, crossed=curr.crossed
            )
            if signal is not None:
                if (
                    report.motive is Motive.TOB_LAG
                    and not faded
                    and not _tob_lag_join_ok(price, settings, signal.style)
                ):
                    return signals
                signals.append(signal)
    return signals
