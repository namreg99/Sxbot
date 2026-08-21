"""Turn classified flow into join (maker) and/or take (taker) actions.

SX_FOLLOW_STYLE:
- join  — rest behind makers (default). Still take leftover crossed quotes.
- take  — hit the informed side now (pay the spread, get the position).
- mixed — take on strong steam/rotation; otherwise join.
"""

from __future__ import annotations

from sxbot.config import Settings
from sxbot.flow import FlowReport, Motive, classify
from sxbot.models import Action, Market, PublicTrade, Signal, Side
from sxbot.orderbook import BookView
from sxbot.units import OddsLadder, taker_odds


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
) -> Signal:
    assert report.side is not None
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
    report = report or classify(prev, curr, settings, trades=trades, steam_hits=steam_hits)
    if not report.actionable or report.side is None:
        return []

    style = (settings.follow_style or "join").strip().lower()
    spread_bps = curr.spread_bps()
    wide_enough = spread_bps is not None and spread_bps >= settings.min_spread_bps
    signals: list[Signal] = []

    if report.motive is Motive.CROSSED and settings.enable_take_stale:
        price = _take_against(curr, report.side)
        if price is not None:
            signals.append(
                _signal(market, report, curr, Action.TAKE_STALE, price, crossed=True)
            )
            return signals

    take_now = settings.enable_take_stale and report.motive in {
        Motive.MAKER_STEAM,
        Motive.SIZE_ROTATION,
    } and (
        style == "take"
        or (style == "mixed" and report.confidence >= 0.7)
    )
    if take_now:
        price = _take_against(curr, report.side)
        if price is not None:
            signals.append(
                _signal(market, report, curr, Action.TAKE_FLOW, price, crossed=curr.crossed)
            )
            return signals

    if style == "take":
        return signals

    if settings.enable_join_maker and (
        wide_enough or report.motive in {Motive.MAKER_STEAM, Motive.TOB_LAG, Motive.SIZE_ROTATION}
    ):
        price = _join_odds(curr, report.side, ladder, settings.join_ticks_behind)
        if price is not None:
            signals.append(
                _signal(market, report, curr, Action.JOIN_MAKER, price, crossed=curr.crossed)
            )
    return signals
