"""V3-native sharp-money detection.

Wallet ids do not survive V3. The public tape is anonymized and the book is
aggregated price levels. Sharp flow still shows up as *how the book is
managed*:

- **Maker steam** — both sides reprice in the same direction, usually
  without a matching tape print. Makers moved fair value.
- **Size rotation** — resting size is pulled off one outcome and added to
  the other. Makers want to be long that outcome.
- **Top-of-book lag** — the displayed mid disagrees with the depth-weighted
  body of the book. The body is the informed quote; the top is leftover or
  a thin probe.
- **Taker hit** — size disappears at the best *and* the anonymized tape
  printed on that side. That is retail (or a taker bot) lifting offers, not
  makers moving the line. We do not follow it.
- **Crossed / stale** — leftover quotes sitting through the new mid after
  a steam. Taking them *is* betting with the makers.

None of these need an address. They work on testnet today and on mainnet
the moment V3 is live.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from sxbot.config import Settings
from sxbot.models import PublicTrade, Side
from sxbot.orderbook import BookView
from sxbot.units import bps_of_odds


class Motive(str, Enum):
    MAKER_STEAM = "maker_steam"
    SIZE_ROTATION = "size_rotation"
    TOB_LAG = "tob_lag"
    TAKER_HIT = "taker_hit"
    CROSSED = "crossed"
    NONE = "none"


@dataclass(frozen=True)
class FlowReport:
    motive: Motive
    side: Side | None
    move_bps: int
    persistence: float
    tob_vs_dw_bps: int | None
    tape_prints: int
    steam_hits: int
    confidence: float
    reasons: tuple[str, ...] = ()

    @property
    def actionable(self) -> bool:
        return self.motive in {
            Motive.MAKER_STEAM,
            Motive.SIZE_ROTATION,
            Motive.TOB_LAG,
            Motive.CROSSED,
        } and self.side is not None


@dataclass
class SteamTracker:
    """Count consecutive same-direction maker steams per market."""

    window_s: float = 45.0
    _hits: dict[str, list[tuple[float, Side]]] = field(default_factory=dict)

    def record(self, market_hash: str, side: Side | None, ts: float) -> int:
        trail = [item for item in self._hits.get(market_hash, []) if ts - item[0] <= self.window_s]
        if side is None:
            self._hits[market_hash] = trail
            return 0
        trail.append((ts, side))
        trail = [item for item in trail if item[1] is side]
        self._hits[market_hash] = trail
        return len(trail)


def persistence(prev: BookView, curr: BookView) -> float:
    """Fraction of previous resting size still sitting at the same prices."""
    p1 = _persist(prev.levels_one, curr.levels_one)
    p2 = _persist(prev.levels_two, curr.levels_two)
    return (p1 + p2) / 2


def _persist(
    prev_levels: tuple[tuple[int, int], ...],
    curr_levels: tuple[tuple[int, int], ...],
) -> float:
    prev = {odds: size for odds, size in prev_levels}
    curr = {odds: size for odds, size in curr_levels}
    total = sum(prev.values())
    if total <= 0:
        return 1.0
    kept = sum(min(size, curr.get(odds, 0)) for odds, size in prev.items())
    return kept / total


def steam_direction(prev: BookView, curr: BookView, min_move_bps: int) -> Side | None:
    side = _both_sides_shifted(prev, curr)
    if side is None or prev.mid_one is None or curr.mid_one is None:
        return None
    if abs(bps_of_odds(curr.mid_one - prev.mid_one)) < min_move_bps:
        return None
    return side


def _both_sides_shifted(prev: BookView, curr: BookView) -> Side | None:
    if not prev.two_sided or not curr.two_sided:
        return None
    assert prev.mid_one is not None and curr.mid_one is not None
    o1_up = curr.best_one is not None and prev.best_one is not None and curr.best_one > prev.best_one
    o1_down = curr.best_one is not None and prev.best_one is not None and curr.best_one < prev.best_one
    o2_up = curr.best_two is not None and prev.best_two is not None and curr.best_two > prev.best_two
    o2_down = curr.best_two is not None and prev.best_two is not None and curr.best_two < prev.best_two
    move_bps = bps_of_odds(curr.mid_one - prev.mid_one)
    if move_bps > 0 and (o1_up or o2_down) and not o1_down and not o2_up:
        return Side.OUTCOME_ONE
    if move_bps < 0 and (o2_up or o1_down) and not o2_down and not o1_up:
        return Side.OUTCOME_TWO
    return None


def _size_rotation(prev: BookView, curr: BookView, min_imbalance: float) -> Side | None:
    """True rotation, not a $1 flicker on a book that was already lopsided.

    `min_imbalance` is a real gate (it used to be ignored). Also require the
    size that actually moved to be at least 5% of the previous book so a
    one-lot cancel cannot look like makers flipping the inventory.
    """
    d1 = curr.size_one - prev.size_one
    d2 = curr.size_two - prev.size_two
    if d1 > 0 and d2 < 0:
        side = Side.OUTCOME_ONE
        moved = min(d1, -d2)
    elif d2 > 0 and d1 < 0:
        side = Side.OUTCOME_TWO
        moved = min(d2, -d1)
    else:
        return None
    if abs(curr.imbalance) < min_imbalance:
        return None
    prev_total = prev.size_one + prev.size_two
    if prev_total > 0 and moved < prev_total * 0.05:
        return None
    return side


def _tob_lag_side(curr: BookView, min_bps: int) -> tuple[Side | None, int | None]:
    if curr.mid_one is None or curr.dw_mid is None:
        return None, None
    delta = curr.dw_mid - curr.mid_one
    bps = bps_of_odds(delta)
    if abs(bps) < min_bps:
        return None, bps
    return (Side.OUTCOME_ONE if bps > 0 else Side.OUTCOME_TWO), bps


def _taker_hit(
    prev: BookView,
    curr: BookView,
    trades: list[PublicTrade] | None,
) -> Side | None:
    """Return the outcome *takers* bought, if the tape explains a one-sided size drop."""
    if trades is None:
        return None
    steam = _both_sides_shifted(prev, curr)
    if steam is not None:
        return None
    tape_one = sum(t.stake for t in trades if t.is_betting_outcome_one)
    tape_two = sum(t.stake for t in trades if not t.is_betting_outcome_one)
    drop_two = prev.size_two - curr.size_two
    drop_one = prev.size_one - curr.size_one
    # Takers betting outcome one consume resting outcome-two makers.
    if tape_one > 0 and drop_two > 0 and drop_two >= drop_one:
        return Side.OUTCOME_ONE
    if tape_two > 0 and drop_one > 0 and drop_one >= drop_two:
        return Side.OUTCOME_TWO
    return None


def _version_is_stale(prev: str, curr: str) -> bool:
    """Numeric book versions only. V3 hashes are not ordered — never `hash <= hash`."""
    if not prev or not curr:
        return False
    if prev.isdigit() and curr.isdigit():
        return int(curr) <= int(prev)
    return False


def classify(
    prev: BookView,
    curr: BookView,
    settings: Settings,
    trades: list[PublicTrade] | None = None,
    steam_hits: int = 0,
) -> FlowReport:
    if _version_is_stale(prev.version, curr.version):
        return FlowReport(Motive.NONE, None, 0, 1.0, None, 0, steam_hits, 0.0)
    if not curr.two_sided:
        return FlowReport(Motive.NONE, None, 0, persistence(prev, curr), None, 0, steam_hits, 0.0)

    move_bps = 0
    if prev.mid_one is not None and curr.mid_one is not None:
        move_bps = bps_of_odds(curr.mid_one - prev.mid_one)
    persist = persistence(prev, curr)
    lag_side, lag_bps = _tob_lag_side(curr, settings.min_mid_move_bps)
    prev_lag, prev_lag_bps = _tob_lag_side(prev, settings.min_mid_move_bps)
    steam = _both_sides_shifted(prev, curr)
    if steam is not None and abs(move_bps) < settings.min_mid_move_bps:
        steam = None
    rotation = _size_rotation(prev, curr, settings.min_imbalance)
    taker = _taker_hit(prev, curr, trades)
    tape_n = 0 if trades is None else len(trades)

    reasons: list[str] = []
    confidence = 0.0
    motive = Motive.NONE
    side: Side | None = None

    if curr.crossed:
        side = steam or rotation or lag_side
        if side is not None:
            motive = Motive.CROSSED
            confidence = 0.8
            reasons.append("crossed/stale book after a maker move")

    if motive is Motive.NONE and steam is not None:
        motive = Motive.MAKER_STEAM
        side = steam
        confidence = 0.7
        reasons.append(f"makers shifted mid {move_bps:+d}bp toward {side.value}")
        if trades is not None and tape_n == 0:
            confidence += 0.08
            reasons.append("no tape print — this was a cancel/reprice, not a hit")
        if persist < 0.5:
            confidence += 0.07
            reasons.append(f"book body repriced (persistence {persist:.2f})")

    if motive is Motive.NONE and rotation is not None:
        motive = Motive.SIZE_ROTATION
        side = rotation
        confidence = 0.62
        reasons.append(
            f"maker size rotated to {side.value} imb={curr.imbalance:+.2f} "
            f"(ΔO1={curr.size_one - prev.size_one}, ΔO2={curr.size_two - prev.size_two})"
        )

    if motive is Motive.NONE and lag_side is not None:
        # tob_lag is a *state* (body still ahead of TOB). Size flicker used to
        # re-fire it every poll and restack the same join. Only emit when the
        # lag is new or getting worse.
        sticky = (
            prev_lag is lag_side
            and prev_lag_bps is not None
            and lag_bps is not None
            and abs(lag_bps) <= abs(prev_lag_bps)
        )
        if not sticky:
            motive = Motive.TOB_LAG
            side = lag_side
            confidence = 0.58
            reasons.append(f"depth-weighted mid leads top-of-book by {lag_bps:+d}bp")

    if motive is Motive.NONE and taker is not None:
        motive = Motive.TAKER_HIT
        side = taker
        confidence = 0.35
        reasons.append(f"tape lifted {taker.value} — taker flow, not a maker reprice")

    if steam is not None and rotation is not None and steam is not rotation:
        return FlowReport(
            Motive.NONE, None, move_bps, persist, lag_bps, tape_n, steam_hits, 0.0,
            ("conflicting steam vs size rotation",),
        )

    if motive is Motive.MAKER_STEAM and rotation is not None and rotation is side:
        confidence += 0.1
        reasons.append("size rotation agrees with the steam")

    if motive in {Motive.MAKER_STEAM, Motive.SIZE_ROTATION} and lag_side is side:
        confidence += 0.05
        reasons.append("depth-weighted mid agrees")

    if steam_hits >= settings.min_steam_hits and motive is Motive.MAKER_STEAM:
        confidence += 0.1
        reasons.append(f"steam {steam_hits}x in the last window")

    return FlowReport(
        motive=motive,
        side=side,
        move_bps=move_bps,
        persistence=persist,
        tob_vs_dw_bps=lag_bps,
        tape_prints=tape_n,
        steam_hits=steam_hits,
        confidence=min(confidence, 0.99),
        reasons=tuple(reasons),
    )
