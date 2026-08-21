"""Turn an aggregated V3 book into mid, spread, and maker-size imbalance.

V3 levels have no wallet ids. The "informed money" we can actually see is the
resting maker book: who is quoting, how they reprice, and which side they
keep size on.
"""

from __future__ import annotations

from dataclasses import dataclass

from sxbot.models import Book, Level, Side
from sxbot.units import bps_of_odds, taker_capacity, taker_odds, to_percent


@dataclass(frozen=True)
class BookView:
    market_hash: str
    version: str
    best_one: int | None
    best_two: int | None
    size_one: int
    size_two: int
    top_size_one: int
    top_size_two: int
    bid_one: int | None
    ask_one: int | None
    mid_one: int | None
    spread_one: int | None
    imbalance: float
    crossed: bool
    dw_mid: int | None
    levels_one: tuple[tuple[int, int], ...]
    levels_two: tuple[tuple[int, int], ...]

    @property
    def two_sided(self) -> bool:
        return self.best_one is not None and self.best_two is not None

    def spread_bps(self) -> int | None:
        if self.spread_one is None:
            return None
        return bps_of_odds(self.spread_one)

    def best(self, side: Side) -> int | None:
        return self.best_one if side is Side.OUTCOME_ONE else self.best_two

    def size(self, side: Side) -> int:
        return self.size_one if side is Side.OUTCOME_ONE else self.size_two


def _total(levels: tuple[Level, ...]) -> int:
    return sum(level.size for level in levels)


def _vwap(levels: tuple[Level, ...], *, invert: bool = False) -> int | None:
    num = 0
    den = 0
    for level in levels:
        price = taker_odds(level.percentage_odds) if invert else level.percentage_odds
        num += price * level.size
        den += level.size
    return (num // den) if den else None


def analyze(book: Book) -> BookView:
    o1 = book.outcome_one
    o2 = book.outcome_two
    best_one = o1[0].percentage_odds if o1 else None
    best_two = o2[0].percentage_odds if o2 else None
    bid_one = best_one
    ask_one = taker_odds(best_two) if best_two is not None else None
    mid_one = None
    spread_one = None
    crossed = False
    if bid_one is not None and ask_one is not None:
        mid_one = (bid_one + ask_one) // 2
        spread_one = ask_one - bid_one
        crossed = bid_one > ask_one
    size_one = _total(o1)
    size_two = _total(o2)
    denom = size_one + size_two
    imbalance = ((size_one - size_two) / denom) if denom else 0.0
    dw_bid = _vwap(o1)
    dw_ask = _vwap(o2, invert=True)
    dw_mid = (dw_bid + dw_ask) // 2 if dw_bid is not None and dw_ask is not None else None
    return BookView(
        market_hash=book.market_hash,
        version=book.version,
        best_one=best_one,
        best_two=best_two,
        size_one=size_one,
        size_two=size_two,
        top_size_one=o1[0].size if o1 else 0,
        top_size_two=o2[0].size if o2 else 0,
        bid_one=bid_one,
        ask_one=ask_one,
        mid_one=mid_one,
        spread_one=spread_one,
        imbalance=imbalance,
        crossed=crossed,
        dw_mid=dw_mid,
        levels_one=tuple((lvl.percentage_odds, lvl.size) for lvl in o1),
        levels_two=tuple((lvl.percentage_odds, lvl.size) for lvl in o2),
    )


def format_view(view: BookView) -> str:
    def pct(odds: int | None) -> str:
        return f"{to_percent(odds):6.3f}%" if odds is not None else "     n/a"

    spr = view.spread_bps()
    spr_s = f"{spr:4d}bp" if spr is not None else "   n/a"
    dw = pct(view.dw_mid)
    return (
        f"mid {pct(view.mid_one)}  dw {dw}  spr {spr_s}  "
        f"imb {view.imbalance:+.2f}  "
        f"O1 {pct(view.best_one)} {view.size_one / 1e6:8.1f}u  "
        f"O2 {pct(view.best_two)} {view.size_two / 1e6:8.1f}u"
    )


def depth_taker(book: Book, betting_outcome_one: bool) -> int:
    levels = book.outcome_two if betting_outcome_one else book.outcome_one
    return sum(taker_capacity(level.size, level.percentage_odds) for level in levels)
