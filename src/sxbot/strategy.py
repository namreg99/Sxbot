"""Turn classified flow into join (maker) and/or take (taker) actions.

SX_FOLLOW_STYLE:
- join       — rest behind makers who have size on that side (not steam alone).
- take       — hit the informed side now (pay the spread, same team as the steam).
- mixed      — take on strong steam/rotation; otherwise join.
- take_first — hit the other side to get the same team when the ask is
               close to the touch. MLB pick'em follows steam even without
               parked size. Tennis shorts are paper-only (live does not
               pay that spread). Pregame uniques are confirmed, not faded.

The follow-bot (`sxbot run`) uses maker bias to bet *with* the makers.
Filling the heavy quotes (the other team) is not this strategy.
The maker-bot (`sxbot mm`) is a separate process and stays a maker.
"""

from __future__ import annotations

from dataclasses import replace

from sxbot.config import Settings
from sxbot.filters import (
    STEAM_DOG_STYLES,
    STYLE_MLB,
    STYLE_TENNIS_SHORT,
    longshot_skip_reason,
    order_skip_reason,
    quote_style,
)
from sxbot.flow import FlowReport, Motive, classify
from sxbot.models import Action, Market, PublicTrade, Signal, Side
from sxbot.orderbook import BookView
from sxbot.units import ODDS_SCALE, OddsLadder, bps_of_odds, decimal_odds, taker_odds, to_prob

# Same cutoff as the board "best priced" card. A short in this band with no
# maker size was the not-EV unique book; we fade to the in-band side that
# actually has size (every sport). Tennis/soccer/dog steam without inventory
# is skipped; MLB pick'em steam is not.
_PRICED_SHORT_MAX = 1.80
_FADE_REASON = "fade non-EV short"
_THESIS_REASON = "pregame unique agrees"


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


def _maker_lean(view: BookView, side: Side, settings: Settings) -> bool:
    """True when parked maker size is on `side` — the board's real EV check."""
    return _size_on_side(view.imbalance, side, float(settings.min_imbalance or 0.15))


def _maker_lean_required(style: str | None) -> bool:
    """MLB pick'em steam was +EV without inventory; tennis/soccer/dogs were not."""
    return style != STYLE_MLB


def _live_take_style_ok(style: str | None) -> bool:
    """tennis_short unique is high win% but -ROI; do not pay the spread live."""
    return style != STYLE_TENNIS_SHORT


def _side_quote_style(
    market: Market,
    view: BookView,
    side: Side,
    settings: Settings,
    fallback: int = 0,
) -> str | None:
    touch = view.best(side) or fallback
    style = quote_style(market, touch, settings) if touch else None
    if not style and fallback and fallback != touch:
        style = quote_style(market, fallback, settings)
    return style


def _take_through_too_wide(touch: int | None, take_price: int, max_bps: int) -> bool:
    """Skip takes that pay a junk-wide spread vs the unique-follow touch."""
    if not touch or take_price <= 0 or max_bps <= 0:
        return False
    worse = take_price - touch
    if worse <= 0:
        return False
    return bps_of_odds(worse) > max_bps


def _take_first_quality_ok(
    report: FlowReport,
    view: BookView,
    take_price: int,
    settings: Settings,
    style: str | None,
) -> bool:
    """take_first: skip tennis shorts; lean required except MLB pick'em; ask near touch."""
    if report.side is None:
        return False
    if not _live_take_style_ok(style):
        return False
    if _maker_lean_required(style) and not _maker_lean(view, report.side, settings):
        return False
    cap = int(getattr(settings, "max_take_through_bps", 250) or 0)
    return not _take_through_too_wide(view.best(report.side), take_price, cap)


def _non_ev_priced_short(
    market: Market,
    report: FlowReport,
    view: BookView,
    settings: Settings,
) -> bool:
    """Favorite (≤1.80) with no maker size; the other side has size and is in-band.

    Follow the inventory, every sport — not steam into a thin short.
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
    if _maker_lean(view, report.side, settings):
        return False
    other = report.side.opposite()
    if not _maker_lean(view, other, settings):
        return False
    other_price = view.best(other)
    return bool(other_price) and quote_style(market, other_price, settings) is not None


def _apply_thesis(report: FlowReport, thesis_side: Side | None) -> FlowReport | None:
    """Pregame unique is the thesis. Fade/steam the other way is a skip."""
    if thesis_side is None or report.side is None:
        return report
    if report.side is not thesis_side:
        return None
    if _THESIS_REASON in report.reasons:
        return report
    return replace(report, reasons=(*report.reasons, _THESIS_REASON))


def evaluate(
    market: Market,
    prev: BookView,
    curr: BookView,
    settings: Settings,
    ladder: OddsLadder,
    trades: list[PublicTrade] | None = None,
    steam_hits: int = 0,
    report: FlowReport | None = None,
    thesis_side: Side | None = None,
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

    aligned = _apply_thesis(report, thesis_side)
    if aligned is None:
        return []
    report = aligned

    follow_price = curr.best(report.side) if report.side is not None else None
    style = quote_style(market, follow_price, settings) if follow_price else None
    if report.motive in {Motive.MAKER_STEAM, Motive.SIZE_ROTATION, Motive.TOB_LAG}:
        if _maker_lean_required(style) and not _maker_lean(curr, report.side, settings):
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
            take_style = style or quote_style(market, take_price, settings)
            if not skip_tob_dog and _take_first_quality_ok(
                report, curr, take_price, settings, take_style
            ):
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
