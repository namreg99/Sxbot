"""Follow informed market-maker flow instead of copy-trading wallets.

SX Bet V3 hides maker/taker addresses on the public tape and aggregates the
book into anonymous price levels. Wallet copy-trading is no longer possible
from the public API.

Sharp flow is recovered from book microstructure — see sxbot.flow — and
turned into two actions:

1. take_stale — IOC leftover quotes sitting through the new mid.
2. join_maker — rest GTC one tick behind the new best on the informed side.
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

    spread_bps = curr.spread_bps()
    wide_enough = spread_bps is not None and spread_bps >= settings.min_spread_bps
    reason = "; ".join(report.reasons) or report.motive.value
    signals: list[Signal] = []

    if report.motive is Motive.CROSSED and settings.enable_take_stale:
        stale_best = curr.best(report.side.opposite())
        if stale_best is not None:
            signals.append(
                Signal(
                    market=market,
                    side=report.side,
                    action=Action.TAKE_STALE,
                    maker_odds=taker_odds(stale_best),
                    reason=reason,
                    mid_move_bps=report.move_bps,
                    imbalance=curr.imbalance,
                    confidence=report.confidence,
                    crossed=True,
                )
            )
            return signals

    if settings.enable_join_maker and (
        wide_enough or report.motive in {Motive.MAKER_STEAM, Motive.TOB_LAG, Motive.SIZE_ROTATION}
    ):
        price = _join_odds(curr, report.side, ladder, settings.join_ticks_behind)
        if price is not None:
            signals.append(
                Signal(
                    market=market,
                    side=report.side,
                    action=Action.JOIN_MAKER,
                    maker_odds=price,
                    reason=reason,
                    mid_move_bps=report.move_bps,
                    imbalance=curr.imbalance,
                    confidence=report.confidence,
                    crossed=curr.crossed,
                )
            )
    return signals
