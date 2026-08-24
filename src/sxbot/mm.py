"""Pregame paper maker: ghost-quote one side from liquidity, score the tape.

Two-sided making (rest both outcomes, lock an overround) is how you collect a
spread *if both sides fill*, plus maker rewards while unmatched. That is not
the better extract path in our sample: HedgeHog was 70% maker and still red
on fills, and only ~1 in 3 of their maker markets filled both ways.

Default is one-sided. We look at resting size, sit one tick behind the
**heavy** side (makers already parked there), and pretend that quote is live.
A paper fill fires only when the public tape takes the opposite outcome
through our price — not when we merely posted. After a fill we hold; we do
not auto-hedge the other side (that would turn an extract bet into a spread).

Set SX_MM_TWO_SIDED=true for the old both-sides loop.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, replace
from typing import Any

from sxbot.bot import Bot
from sxbot.config import Settings
from sxbot.filters import (
    STYLE_MM,
    kickoff_skip_reason,
    longshot_skip_reason,
    mm_family,
    mm_quote_style,
    order_skip_reason,
)
from sxbot.journal import load_jsonl, paper_log_for
from sxbot.models import Action, Market, PublicTrade, Side, Signal
from sxbot.orderbook import BookView
from sxbot.risk import RiskGate
from sxbot.units import ODDS_SCALE, OddsLadder, decimal_odds, odds_from_bps, to_prob

log = logging.getLogger("sxbot.mm")

# Ignore 1–2 tick top-of-book wobble so we do not cancel/rejoin every poll.
HOLD_TICKS = 2


@dataclass
class MMResting:
    odds_one: int | None = None
    odds_two: int | None = None
    filled_one: bool = False
    filled_two: bool = False

    def odds(self, side: Side) -> int | None:
        return self.odds_one if side is Side.OUTCOME_ONE else self.odds_two

    def filled(self, side: Side) -> bool:
        return self.filled_one if side is Side.OUTCOME_ONE else self.filled_two

    def set_odds(self, side: Side, price: int | None) -> None:
        if side is Side.OUTCOME_ONE:
            self.odds_one = price
        else:
            self.odds_two = price

    def mark_filled(self, side: Side) -> None:
        if side is Side.OUTCOME_ONE:
            self.filled_one = True
            self.odds_one = None
        else:
            self.filled_two = True
            self.odds_two = None

    @property
    def live_quote(self) -> bool:
        return self.odds_one is not None or self.odds_two is not None

    @property
    def both_filled(self) -> bool:
        return self.filled_one and self.filled_two


@dataclass(frozen=True)
class QuotePair:
    odds_one: int | None
    odds_two: int | None
    reason: str


def mm_eligible(market: Market, settings: Settings, *, now: int | None = None) -> str | None:
    """Why this market is not for the pregame maker, or None if it is."""
    if market.is_live(now):
        return "live"
    skipped = order_skip_reason(market, settings, now=now)
    if skipped:
        return skipped
    if mm_family(market) is None:
        return "sport"
    return None


def _decimal_ok(price: int, settings: Settings) -> bool:
    if price <= 0:
        return False
    if longshot_skip_reason(price, settings):
        return False
    dec = decimal_odds(to_prob(price))
    floor = float(settings.mm_min_decimal or 0)
    return dec + 1e-9 >= floor


def liquidity_side(view: BookView, settings: Settings) -> Side | None:
    """Side where more maker size is parked. None if the book is balanced."""
    if not view.two_sided:
        return None
    thresh = float(settings.min_imbalance or 0)
    if abs(view.imbalance) < thresh:
        return None
    return Side.OUTCOME_ONE if view.imbalance > 0 else Side.OUTCOME_TWO


def _join_price(view: BookView, side: Side, ladder: OddsLadder, ticks: int, settings: Settings) -> int | None:
    best = view.best(side)
    if best is None:
        return None
    price = ladder.tick_down(best, max(ticks, 0))
    if price <= 0 or not ladder.on_ladder(price) or not _decimal_ok(price, settings):
        return None
    return price


def _within_ticks(have: int, want: int, ladder: OddsLadder, ticks: int) -> bool:
    return abs(have - want) <= ticks * ladder.step


def _ghost_pair(market: Market, view: BookView, side: Side, price: int, *, held: bool) -> QuotePair:
    style = mm_quote_style(market, price) or mm_family(market) or "mm"
    imb = f"imb {view.imbalance:+.2f}"
    verb = "hold" if held else "ghost"
    if side is Side.OUTCOME_ONE:
        return QuotePair(price, None, f"{verb} {style} O1 ({imb})")
    return QuotePair(None, price, f"{verb} {style} O2 ({imb})")


def quote_pair(
    market: Market,
    view: BookView,
    ladder: OddsLadder,
    settings: Settings,
    resting: MMResting | None = None,
    *,
    now: int | None = None,
) -> QuotePair | None:
    """Ghost quote: one tick behind. Default one heavy side; optional two-sided."""
    why = mm_eligible(market, settings, now=now)
    if why:
        return None
    if not view.two_sided or view.best_one is None or view.best_two is None:
        return None
    if view.crossed:
        return None

    filled_one = bool(resting and resting.filled_one)
    filled_two = bool(resting and resting.filled_two)
    if filled_one and filled_two:
        return None

    behind = max(int(settings.join_ticks_behind), 0)

    if not bool(getattr(settings, "mm_two_sided", False)):
        if filled_one or filled_two:
            # Hold the filled side. Do not auto-hedge.
            return None
        live_side: Side | None = None
        have: int | None = None
        if resting is not None:
            if resting.odds_one is not None:
                live_side, have = Side.OUTCOME_ONE, resting.odds_one
            elif resting.odds_two is not None:
                live_side, have = Side.OUTCOME_TWO, resting.odds_two
        if live_side is not None and have:
            # Persist the posted side through size flicker. Do not flip.
            price = _join_price(view, live_side, ladder, behind, settings)
            if price is None:
                return None
            held = _within_ticks(have, price, ladder, HOLD_TICKS)
            keep = have if held else price
            if mm_quote_style(market, keep) is None:
                return None
            return _ghost_pair(market, view, live_side, keep, held=held)
        side = liquidity_side(view, settings)
        if side is None:
            return None
        price = _join_price(view, side, ladder, behind, settings)
        if price is None or mm_quote_style(market, price) is None:
            return None
        return _ghost_pair(market, view, side, price, held=False)

    want_one = not filled_one
    want_two = not filled_two
    max_extra = max(int(settings.mm_max_widen_ticks), 0)
    min_edge = odds_from_bps(max(int(settings.mm_min_overround_bps), 0))
    last_one: int | None = None
    last_two: int | None = None
    for extra in range(0, max_extra + 1):
        ticks = behind + extra
        p1 = ladder.tick_down(view.best_one, ticks) if want_one else None
        p2 = ladder.tick_down(view.best_two, ticks) if want_two else None
        if want_one and (p1 is None or p1 <= 0 or not ladder.on_ladder(p1) or not _decimal_ok(p1, settings)):
            return None
        if want_two and (p2 is None or p2 <= 0 or not ladder.on_ladder(p2) or not _decimal_ok(p2, settings)):
            return None
        last_one, last_two = p1, p2
        if want_one and want_two:
            if p1 is None or p2 is None:
                return None
            if p1 + p2 <= ODDS_SCALE - min_edge:
                return QuotePair(p1, p2, f"two-sided join {ticks} tick(s) behind")
            continue
        return QuotePair(p1, p2, f"hedge {ticks} tick(s) behind")
    if want_one and want_two:
        return None
    return QuotePair(last_one, last_two, "hedge join behind") if (last_one or last_two) else None


def taker_hits_maker(side: Side, trades: list[PublicTrade]) -> bool:
    """Taker betting the opposite outcome is what fills our resting maker quote."""
    want_taker_one = side is Side.OUTCOME_TWO
    return any(trade.is_betting_outcome_one is want_taker_one for trade in trades)


def _tape_took_our_price(resting_odds: int, side: Side, trades: list[PublicTrade]) -> bool:
    """True when an opposite-side take printed at or through our ghost line."""
    offered_taker = ODDS_SCALE - resting_odds
    want_taker_one = side is Side.OUTCOME_TWO
    for trade in trades:
        if trade.is_betting_outcome_one is not want_taker_one:
            continue
        if trade.odds >= offered_taker:
            return True
    return False


def quote_was_hit(
    resting_odds: int,
    side: Side,
    view: BookView,
    trades: list[PublicTrade],
) -> bool:
    """Ghost fill: opposite tape at/through our price, or the inside eaten to us."""
    if not taker_hits_maker(side, trades):
        return False
    if _tape_took_our_price(resting_odds, side, trades):
        return True
    best = view.best(side)
    if best is None:
        return True
    return best <= resting_odds


def _signal(
    market: Market,
    side: Side,
    action: Action,
    price: int,
    reason: str,
    view: BookView,
) -> Signal:
    return Signal(
        market=market,
        side=side,
        action=action,
        maker_odds=price,
        reason=reason,
        mid_move_bps=0,
        imbalance=view.imbalance,
        confidence=1.0,
        crossed=view.crossed,
        motive="mm_quote" if action is Action.JOIN_MAKER else action.value,
        style=STYLE_MM,
    )


class MakerBot:
    """Paper ghost-quoter: one heavy side by default, tape fills only."""

    def __init__(self, settings: Settings, client: Any) -> None:
        mm_log = paper_log_for(settings.paper_log, STYLE_MM)
        two_sided = bool(settings.mm_two_sided)
        mm_settings = replace(
            settings,
            paper_log=str(mm_log),
            watch_live=False,
            allow_live=False,
            one_side_per_market=not two_sided,
        )
        self.settings = mm_settings
        self.inner = Bot(mm_settings, client)
        self.inner.executor.paper_path = mm_log
        self.inner.executor._paper_path_locked = True
        self.inner.risk = RiskGate(mm_settings, self.inner.meta.decimals)
        self.inner.risk.hydrate(load_jsonl(mm_log))
        self.resting: dict[str, MMResting] = {}
        self._restore_resting(load_jsonl(mm_log))

    def _restore_resting(self, rows: list[dict[str, Any]]) -> None:
        last: dict[str, MMResting] = {}
        for row in rows:
            market = str(row.get("market") or "")
            if not market:
                continue
            slot = last.setdefault(market, MMResting())
            action = str(row.get("action") or "")
            side_raw = str(row.get("side") or "")
            try:
                side = Side(side_raw)
            except ValueError:
                if action == Action.CANCEL.value:
                    last.pop(market, None)
                continue
            if action == Action.CANCEL.value:
                slot.set_odds(side, None)
                if not slot.live_quote and not slot.filled_one and not slot.filled_two:
                    last.pop(market, None)
                continue
            if action == Action.MM_FILL.value:
                slot.mark_filled(side)
                continue
            if action == Action.JOIN_MAKER.value:
                try:
                    slot.set_odds(side, int(row.get("odds") or 0) or None)
                except (TypeError, ValueError):
                    continue
        self.resting = {key: slot for key, slot in last.items() if slot.live_quote or slot.filled_one or slot.filled_two}

    def step(self) -> int:
        now = int(time.time())
        freed = self.inner.risk.release_finished(now)
        if freed:
            for market_hash in freed:
                self.resting.pop(market_hash, None)
            log.info("freed %s mm slot(s)", len(freed))
        executed = 0
        markets = self.inner.qualifying_markets()
        tape = self.inner.pull_tape(markets) or {}
        for market, view in self.inner.scan_many(markets):
            executed += self._handle(market, view, tape.get(market.market_hash, []), now)
        return executed

    def _handle(
        self,
        market: Market,
        view: BookView,
        trades: list[PublicTrade],
        now: int,
    ) -> int:
        executed = 0
        executed += self._match_fills(market, view, trades)
        slot = self.resting.get(market.market_hash)
        live_or_gone = market.is_live(now) or kickoff_skip_reason(market, self.settings, now=now)
        if live_or_gone and slot and slot.live_quote:
            executed += self._cancel_resting(market, view, "kickoff — pregame maker pulls")
            return executed
        if live_or_gone:
            return executed
        desired = quote_pair(market, view, self.inner.ladder, self.settings, slot, now=now)
        if desired is None:
            if slot and slot.live_quote:
                executed += self._cancel_resting(market, view, "no longer quoteable")
            return executed
        executed += self._sync_side(market, view, Side.OUTCOME_ONE, desired.odds_one, desired.reason)
        executed += self._sync_side(market, view, Side.OUTCOME_TWO, desired.odds_two, desired.reason)
        return executed

    def _match_fills(self, market: Market, view: BookView, trades: list[PublicTrade]) -> int:
        slot = self.resting.get(market.market_hash)
        if slot is None or not trades:
            return 0
        executed = 0
        for side in (Side.OUTCOME_ONE, Side.OUTCOME_TWO):
            price = slot.odds(side)
            if price is None or slot.filled(side):
                continue
            if not quote_was_hit(price, side, view, trades):
                continue
            signal = _signal(market, side, Action.MM_FILL, price, "tape took through our ghost line", view)
            stake = max(self.inner.risk.stake(), self.inner.meta.min_order)
            self.inner.executor.execute(
                signal,
                stake,
                extra={
                    "ghost": True,
                    "size_one": view.size_one,
                    "size_two": view.size_two,
                },
            )
            slot.mark_filled(side)
            executed += 1
            log.info("MM FILL %s %s @ %s (tape)", side.value, market.label, price)
        if slot.both_filled:
            executed += self._cancel_resting(market, view, "both sides filled")
        return executed

    def _sync_side(
        self,
        market: Market,
        view: BookView,
        side: Side,
        want: int | None,
        reason: str,
    ) -> int:
        slot = self.resting.setdefault(market.market_hash, MMResting())
        have = slot.odds(side)
        if want is None:
            if have is None:
                return 0
            return self._cancel_side(market, view, side, "pull this side")
        if have == want:
            return 0
        replacing = (market.market_hash, side.value) in self.inner.risk.joined_sides
        if have is not None:
            self._cancel_side(market, view, side, "reprice")
        signal = _signal(market, side, Action.JOIN_MAKER, want, reason, view)
        blocked = self.inner.risk.allow(signal)
        if blocked:
            log.info("skip mm %s %s: %s", side.value, market.label, blocked)
            return 0
        stake = max(self.inner.risk.stake(), self.inner.meta.min_order)
        self.inner.executor.execute(
            signal,
            stake,
            extra={
                "ghost": True,
                "size_one": view.size_one,
                "size_two": view.size_two,
                "imbalance": view.imbalance,
                "mm_style": mm_quote_style(market, want),
            },
        )
        if not replacing:
            self.inner.risk.record(signal, stake)
        slot.set_odds(side, want)
        return 1

    def _cancel_side(self, market: Market, view: BookView, side: Side, reason: str) -> int:
        slot = self.resting.get(market.market_hash)
        price = (slot.odds(side) if slot else None) or view.best(side) or 0
        signal = _signal(market, side, Action.CANCEL, price, reason, view)
        stake = max(self.inner.risk.stake(), self.inner.meta.min_order)
        self.inner.executor.execute(signal, stake)
        if slot is not None:
            slot.set_odds(side, None)
        return 1

    def _cancel_resting(self, market: Market, view: BookView, reason: str) -> int:
        slot = self.resting.get(market.market_hash)
        if slot is None or not slot.live_quote:
            return 0
        n = 0
        if slot.odds_one is not None:
            n += self._cancel_side(market, view, Side.OUTCOME_ONE, reason)
        if slot.odds_two is not None:
            n += self._cancel_side(market, view, Side.OUTCOME_TWO, reason)
        # Free the cap. Per-side cancel is only a paper log; risk still holds the slot.
        cancel = _signal(
            market,
            Side.OUTCOME_ONE,
            Action.CANCEL,
            view.best_one or 0,
            reason,
            view,
        )
        self.inner.risk.record(cancel, 0)
        if slot.both_filled or not slot.live_quote:
            # Keep filled flags for hydrate; drop live quotes.
            if not slot.filled_one and not slot.filled_two:
                self.resting.pop(market.market_hash, None)
        return n

    def run(self) -> None:
        log.info(
            "starting pregame ghost maker dry_run=%s two_sided=%s stake=%s open_cap=%s",
            self.settings.dry_run,
            self.settings.mm_two_sided,
            self.settings.stake_usdc,
            self.settings.max_open_markets,
        )
        try:
            while True:
                try:
                    n = self.step()
                    if n:
                        log.info("mm executed %s action(s) this poll", n)
                    time.sleep(self.settings.poll_seconds)
                except KeyboardInterrupt:
                    raise
                except Exception:
                    log.exception("mm poll failed; retrying")
                    time.sleep(max(self.settings.poll_seconds, 8.0))
        except KeyboardInterrupt:
            log.info("mm shutting down")


def mm_log_path(settings: Settings) -> str:
    return str(paper_log_for(settings.paper_log, STYLE_MM))
