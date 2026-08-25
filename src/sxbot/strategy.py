"""Turn classified flow into join (maker) and/or take (taker) actions.

SX_FOLLOW_STYLE:
- join  — rest behind makers (default). Still take leftover crossed quotes.
- take  — fill the heavy maker quotes now (you get the other team).
- mixed — take on strong steam/rotation; otherwise join.
"""

from __future__ import annotations

from sxbot.config import Settings
from sxbot.filters import longshot_skip_reason, order_skip_reason, quote_style
from sxbot.flow import FlowReport, Motive, classify
from sxbot.models import Action, Market, PublicTrade, Signal, Side
from sxbot.orderbook import BookView
from sxbot.units import ODDS_SCALE, OddsLadder, taker_odds


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


def _fill_maker_quotes(view: BookView, maker_side: Side) -> int | None:
    """Fill makers on `maker_side`. You get the other team at complementary odds.

    Makers on Cincinnati at 1.64 → this returns San Francisco at 2.56.
    """
    best = view.best(maker_side)
    if best is None:
        return None
    return taker_odds(best)


def _signal(
    market: Market,
    report: FlowReport,
    view: BookView,
    action: Action,
    price: int,
    *,
    crossed: bool,
    style: str,
    side: Side | None = None,
) -> Signal:
    assert report.side is not None
    pos = side if side is not None else report.side
    fair = 0
    if view.mid_one is not None:
        fair = view.mid_one if pos is Side.OUTCOME_ONE else ODDS_SCALE - view.mid_one
    return Signal(
        market=market,
        side=pos,
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
    side: Side | None = None,
    style_odds: int | None = None,
) -> Signal | None:
    if longshot_skip_reason(price, settings):
        return None
    style = quote_style(market, style_odds if style_odds is not None else price, settings)
    if not style:
        return None
    return _signal(
        market, report, view, action, price, crossed=crossed, style=style, side=side
    )


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

    if report.motive is Motive.CROSSED and settings.enable_take_stale:
        price = _take_against(curr, report.side)
        if price is not None:
            signal = _priced(
                market, report, curr, Action.TAKE_STALE, price, settings, crossed=True
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
        maker_best = curr.best(report.side)
        price = _fill_maker_quotes(curr, report.side)
        if price is not None and maker_best is not None:
            signal = _priced(
                market,
                report,
                curr,
                Action.TAKE_FLOW,
                price,
                settings,
                crossed=curr.crossed,
                side=report.side.opposite(),
                style_odds=maker_best,
            )
            if signal is not None:
                signals.append(signal)
                return signals

    if follow == "take":
        return signals

    join_motives = {Motive.MAKER_STEAM, Motive.SIZE_ROTATION}
    if settings.join_tob_lag:
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
                signals.append(signal)
    return signals
