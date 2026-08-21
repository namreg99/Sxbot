"""Follow informed market-maker flow instead of copy-trading wallets.

SX Bet V3 hides maker/taker addresses on the public tape and aggregates the
book into anonymous price levels. Wallet copy-trading is no longer possible
from the public API.

What *is* visible is how professional market makers manage the line:

- They rest GTC quotes on both sides.
- When information arrives they pull one side and lift the other, shifting
  the mid without a matching taker print.
- They park more size on the outcome they want to be long.

This module turns consecutive book snapshots into two actions:

1. take_stale — IOC the leftover quotes on the abandoned side. Those prices
   are stale versus the new mid; taking them *is* betting with the makers.
2. join_maker — rest GTC one tick behind the new best on the informed side
   so we support that line and earn the spread / maker rewards.
"""

from __future__ import annotations

from sxbot.config import Settings
from sxbot.models import Action, Market, Signal, Side
from sxbot.orderbook import BookView
from sxbot.units import OddsLadder, bps_of_odds, taker_odds


def _side_from_imbalance(imbalance: float, threshold: float) -> Side | None:
    if imbalance >= threshold:
        return Side.OUTCOME_ONE
    if imbalance <= -threshold:
        return Side.OUTCOME_TWO
    return None


def _reprice_side(prev: BookView, curr: BookView, min_move_bps: int) -> Side | None:
    """Both sides of the book shifted in the same direction = makers moved the line."""
    if not prev.two_sided or not curr.two_sided:
        return None
    assert prev.mid_one is not None and curr.mid_one is not None
    move_bps = bps_of_odds(curr.mid_one - prev.mid_one)
    if abs(move_bps) < min_move_bps:
        return None

    o1_up = curr.best_one is not None and prev.best_one is not None and curr.best_one > prev.best_one
    o1_down = curr.best_one is not None and prev.best_one is not None and curr.best_one < prev.best_one
    o2_up = curr.best_two is not None and prev.best_two is not None and curr.best_two > prev.best_two
    o2_down = curr.best_two is not None and prev.best_two is not None and curr.best_two < prev.best_two

    # Mid up for outcome one: makers bid O1 higher and/or bid O2 lower.
    if move_bps > 0 and (o1_up or o2_down) and not o1_down and not o2_up:
        return Side.OUTCOME_ONE
    if move_bps < 0 and (o2_up or o1_down) and not o2_down and not o1_up:
        return Side.OUTCOME_TWO
    return None


def _size_flow_side(prev: BookView, curr: BookView, min_imbalance: float) -> Side | None:
    """Makers added size on one outcome and pulled it on the other."""
    d1 = curr.size_one - prev.size_one
    d2 = curr.size_two - prev.size_two
    if d1 > 0 and d2 < 0:
        return Side.OUTCOME_ONE
    if d2 > 0 and d1 < 0:
        return Side.OUTCOME_TWO
    return _side_from_imbalance(curr.imbalance, min_imbalance)


def _join_odds(view: BookView, side: Side, ladder: OddsLadder, ticks_behind: int) -> int | None:
    best = view.best(side)
    if best is None:
        return None
    price = ladder.tick_down(best, max(ticks_behind, 0))
    if price <= 0:
        return None
    # Do not cross the book: a GTC that is through the opposite best takes immediately.
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
) -> list[Signal]:
    if curr.version and prev.version and curr.version <= prev.version:
        return []
    if not curr.two_sided:
        return []

    move_bps = 0
    if prev.mid_one is not None and curr.mid_one is not None:
        move_bps = bps_of_odds(curr.mid_one - prev.mid_one)

    informed = _reprice_side(prev, curr, settings.min_mid_move_bps)
    flow = _size_flow_side(prev, curr, settings.min_imbalance)
    side = informed or flow
    if side is None:
        return []

    # A lone imbalance with no reprice still needs a two-sided book that is
    # actually being *made* — skip razor-thin spreads unless we are taking a
    # crossed/stale book.
    spread_bps = curr.spread_bps()
    wide_enough = spread_bps is not None and spread_bps >= settings.min_spread_bps

    signals: list[Signal] = []
    confidence = 0.55
    reasons: list[str] = []
    if informed is not None:
        confidence += 0.25
        reasons.append(f"makers shifted mid {move_bps:+d}bp toward {side.value}")
    if flow is not None and flow is side:
        confidence += 0.15
        reasons.append(
            f"maker size {side.value} imb={curr.imbalance:+.2f} "
            f"(ΔO1={curr.size_one - prev.size_one}, ΔO2={curr.size_two - prev.size_two})"
        )
    if informed is not None and flow is not None and informed is not flow:
        # Conflicting tape: do not join.
        return []
    if not reasons:
        return []

    if curr.crossed and settings.enable_take_stale:
        stale_best = curr.best(side.opposite())
        if stale_best is not None:
            signals.append(
                Signal(
                    market=market,
                    side=side,
                    action=Action.TAKE_STALE,
                    maker_odds=taker_odds(stale_best),
                    reason="; ".join(["crossed/stale book"] + reasons),
                    mid_move_bps=move_bps,
                    imbalance=curr.imbalance,
                    confidence=min(confidence + 0.1, 0.99),
                    crossed=True,
                )
            )
            return signals

    if settings.enable_join_maker and (wide_enough or informed is not None):
        price = _join_odds(curr, side, ladder, settings.join_ticks_behind)
        if price is not None:
            signals.append(
                Signal(
                    market=market,
                    side=side,
                    action=Action.JOIN_MAKER,
                    maker_odds=price,
                    reason="; ".join(reasons),
                    mid_move_bps=move_bps,
                    imbalance=curr.imbalance,
                    confidence=min(confidence, 0.99),
                    crossed=curr.crossed,
                )
            )
    return signals
