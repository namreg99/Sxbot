"""V2-only: join our book signals to labeled wallets still visible on the tape.

Addresses vanish from the public API on August 25. Until then, every
`maker_steam` / `size_rotation` / `tob_lag` can be tagged with *who* was
resting on that market (quote overlap) and *who* just took it (tape overlap).

That is the last labeled teacher for the four styles. After cutover the same
flow rows still grade against SX outcomes — wallets just stop being a column.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Any, Iterable

from sxbot.v2 import is_resting, remaining_size


def as_eth_address(raw: Any) -> str | None:
    if not isinstance(raw, str):
        return None
    value = raw.strip().lower()
    if value.startswith("0x") and len(value) == 42:
        return value
    return None


def _side_name(is_one: bool) -> str:
    return "outcome_one" if is_one else "outcome_two"


def _market_key(raw: dict[str, Any]) -> str:
    return str(raw.get("marketHash") or "").lower()


@dataclass(frozen=True)
class MarketQuotes:
    """Labeled resting size on one market, both sides."""

    on_one: tuple[str, ...]
    on_two: tuple[str, ...]
    size_one_labeled: int
    size_two_labeled: int
    size_one_total: int
    size_two_total: int

    def on_side(self, side: str | None) -> tuple[str, ...]:
        if side == "outcome_one":
            return self.on_one
        if side == "outcome_two":
            return self.on_two
        return ()

    def against(self, side: str | None) -> tuple[str, ...]:
        if side == "outcome_one":
            return self.on_two
        if side == "outcome_two":
            return self.on_one
        return ()

    def share_on_side(self, side: str | None) -> float | None:
        if side == "outcome_one":
            labeled, total = self.size_one_labeled, self.size_one_total
        elif side == "outcome_two":
            labeled, total = self.size_two_labeled, self.size_two_total
        else:
            return None
        if total <= 0:
            return None
        return labeled / total


def attribute_quotes(
    orders: Iterable[dict[str, Any]],
    labeled: dict[str, str],
    *,
    now: int | None = None,
) -> dict[str, MarketQuotes]:
    """Group resting V2 quotes by market. `labeled` is address → name."""
    labeled_lower = {key.lower(): value for key, value in labeled.items()}
    names_one: dict[str, set[str]] = defaultdict(set)
    names_two: dict[str, set[str]] = defaultdict(set)
    lab_one: dict[str, int] = defaultdict(int)
    lab_two: dict[str, int] = defaultdict(int)
    tot_one: dict[str, int] = defaultdict(int)
    tot_two: dict[str, int] = defaultdict(int)

    for order in orders:
        market = _market_key(order)
        if not market or not is_resting(order, now):
            continue
        size = remaining_size(order)
        if size <= 0:
            continue
        is_one = bool(order.get("isMakerBettingOutcomeOne"))
        if is_one:
            tot_one[market] += size
        else:
            tot_two[market] += size
        maker = as_eth_address(order.get("maker"))
        label = labeled_lower.get(maker or "")
        if not label:
            continue
        if is_one:
            names_one[market].add(label)
            lab_one[market] += size
        else:
            names_two[market].add(label)
            lab_two[market] += size

    markets = set(tot_one) | set(tot_two) | set(names_one) | set(names_two)
    return {
        market: MarketQuotes(
            on_one=tuple(sorted(names_one.get(market, ()))),
            on_two=tuple(sorted(names_two.get(market, ()))),
            size_one_labeled=lab_one.get(market, 0),
            size_two_labeled=lab_two.get(market, 0),
            size_one_total=tot_one.get(market, 0),
            size_two_total=tot_two.get(market, 0),
        )
        for market in markets
    }


def attribute_tape(
    trades: Iterable[dict[str, Any]],
    labeled: dict[str, str],
) -> dict[str, dict[str, tuple[str, ...]]]:
    """Labeled *takers* (maker=false) who printed, keyed by market then side."""
    labeled_lower = {key.lower(): value for key, value in labeled.items()}
    names: dict[str, dict[str, set[str]]] = defaultdict(lambda: defaultdict(set))
    for raw in trades:
        if raw.get("maker") is True:
            continue
        bettor = as_eth_address(raw.get("bettor") or raw.get("taker") or raw.get("user"))
        label = labeled_lower.get(bettor or "")
        if not label:
            continue
        market = _market_key(raw)
        if not market:
            continue
        side = _side_name(bool(raw.get("bettingOutcomeOne")))
        names[market][side].add(label)
    return {
        market: {side: tuple(sorted(labels)) for side, labels in sides.items()}
        for market, sides in names.items()
    }


def tag_signal(
    quotes: MarketQuotes | None,
    *,
    side: str | None,
    takers: tuple[str, ...] = (),
) -> dict[str, Any]:
    """Fields to stitch onto a flow jsonl row. Empty when V3 / no book."""
    quoted = quotes.on_side(side) if quotes is not None else ()
    against = quotes.against(side) if quotes is not None else ()
    share = quotes.share_on_side(side) if quotes is not None else None
    any_labeled = bool(quoted) or bool(against) or bool(takers)
    return {
        "quoted_by": list(quoted),
        "quoted_against": list(against),
        "quoted_share": None if share is None else round(share, 4),
        "takers": list(takers),
        "overlap": bool(quoted) or bool(takers),
        "overlap_any": any_labeled,
    }


def format_tag(tag: dict[str, Any]) -> str:
    """One-line note for `sxbot flow` / watch."""
    parts: list[str] = []
    quoted = tag.get("quoted_by") or []
    against = tag.get("quoted_against") or []
    takers = tag.get("takers") or []
    if quoted:
        parts.append("quoted_by=" + ",".join(quoted))
        share = tag.get("quoted_share")
        if share is not None:
            parts.append(f"share={share:.0%}")
    if against:
        parts.append("quoted_against=" + ",".join(against))
    if takers:
        parts.append("takers=" + ",".join(takers))
    if not parts:
        return "overlap=none"
    return " ".join(parts)


def format_overlap_report(rows: list[dict[str, Any]]) -> str:
    tagged = [row for row in rows if "quoted_by" in row or "overlap" in row]
    if not rows:
        return (
            "No flow logged yet. Leave `sxbot flow` or `sxbot run` going on V2 "
            "(until Aug 25) so each signal can be tagged with who was quoting."
        )
    if not tagged:
        return (
            f"{len(rows)} flow row(s) have no overlap tags — they were logged "
            "before this existed, or on V3 books with no maker addresses. "
            "Run `sxbot flow` on mainnet V2 to start tagging."
        )

    lines = [
        "V2 overlap: when our classifier fired, were labeled wallets on that book?",
        "Quote overlap = they were *resting* on the flagged side (HedgeHog-like MM).",
        "Tape overlap = they *took* that side in the same poll (Gary/Botswana-like).",
        "After August 25 this column disappears. Keep the rows; grade them on outcomes.",
        "",
        f"tagged flow      {len(tagged)} / {len(rows)}",
    ]

    actionable = [row for row in tagged if row.get("actionable")]
    sample = actionable or tagged
    on_side = sum(1 for row in sample if row.get("quoted_by"))
    on_tape = sum(1 for row in sample if row.get("takers"))
    hit = sum(1 for row in sample if row.get("overlap"))
    none = sum(1 for row in sample if not row.get("overlap_any"))
    n = len(sample)
    kind = "actionable" if actionable else "all tagged"
    lines.append(f"{kind:16} {n}")
    lines.append(f"  labeled maker on flagged side   {on_side}/{n}  {_pct(on_side, n)}")
    lines.append(f"  labeled taker on flagged side   {on_tape}/{n}  {_pct(on_tape, n)}")
    lines.append(f"  either (overlap)                {hit}/{n}  {_pct(hit, n)}")
    lines.append(f"  none of the four anywhere       {none}/{n}  {_pct(none, n)}")

    lines.append("")
    lines.append("by motive  (labeled maker on flagged side / n)")
    by_motive: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in sample:
        by_motive[str(row.get("motive") or "-")].append(row)
    for motive, group in sorted(by_motive.items(), key=lambda kv: -len(kv[1])):
        hits = sum(1 for row in group if row.get("quoted_by"))
        lines.append(f"  {motive:<16} {hits}/{len(group)}  {_pct(hits, len(group))}")

    lines.append("")
    lines.append("quoted on flagged side")
    _wallet_counts(lines, sample, "quoted_by")
    lines.append("quoted against the signal")
    _wallet_counts(lines, sample, "quoted_against")
    lines.append("taker tape on flagged side")
    _wallet_counts(lines, sample, "takers")

    if n < 30:
        lines.append("")
        lines.append(
            f"Only {n} tagged events so far. This is a calibration sample, "
            "not a ranking — leave it running through the last V2 days."
        )
    return "\n".join(lines)


def _wallet_counts(lines: list[str], rows: list[dict[str, Any]], key: str) -> None:
    counts: dict[str, int] = defaultdict(int)
    for row in rows:
        for name in row.get(key) or []:
            counts[str(name)] += 1
    if not counts:
        lines.append("  (none)")
        return
    for name, n in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])):
        lines.append(f"  {name:<18} {n}")


def _pct(hits: int, n: int) -> str:
    if n <= 0:
        return "-"
    return f"{hits / n:.0%}"
