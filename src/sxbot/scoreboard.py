"""Grade the V3-native flow signal against real settled outcomes.

This answers the only question that matters: does "the book says smart
money is on side X" actually mean anything, or is it just noise / always
picking the favorite?

Two numbers, not one:

- **hit rate** — how often the flagged side won. Not enough on its own:
  a signal that only ever fires on -300 favorites "hits" 75% of the time
  and is worthless, because the price already said 75%.
- **edge** — actual outcome (1 or 0) minus the implied probability the book
  itself was already quoting for that side *at the moment the signal fired*
  (`mid_pct`, logged by `Bot._log_flow`). Averaged over many events, this is
  the part win rate cannot fake: it is zero if the signal adds nothing beyond
  the price that was already there, and positive only if it is genuinely
  predictive of where the price should have been.

Needs `sxbot run` (or `flow`) logging for a while, then real settlements.
There is no shortcut — this is evidence, not a guess.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any


@dataclass(frozen=True)
class GradedFlow:
    motive: str
    side: str
    confidence: float
    league: str
    label: str
    phase: str
    game_time: int
    mid_pct: float | None
    result: str  # win, lose, void, pending, missing
    edge: float | None  # actual - implied prob of the flagged side, only when settled+mid_pct known


CONFIDENCE_BUCKETS: tuple[tuple[str, float, float], ...] = (
    ("<0.60", 0.0, 0.60),
    ("0.60-0.75", 0.60, 0.75),
    ("0.75-0.90", 0.75, 0.90),
    (">=0.90", 0.90, 1.01),
)


def confidence_bucket(confidence: float) -> str:
    for name, lo, hi in CONFIDENCE_BUCKETS:
        if lo <= confidence < hi:
            return name
    return CONFIDENCE_BUCKETS[-1][0]


def _side_implied_prob(side: str, mid_pct: float | None) -> float | None:
    if mid_pct is None:
        return None
    prob_one = mid_pct / 100.0
    return prob_one if side == "outcome_one" else (1.0 - prob_one)


def _result_for(side: str, outcome: int | None) -> str:
    if outcome is None:
        return "pending"
    if outcome == 0:
        return "void"
    if outcome not in (1, 2):
        return "pending"
    won = (outcome == 1 and side == "outcome_one") or (outcome == 2 and side == "outcome_two")
    return "win" if won else "lose"


def grade_flow_row(row: dict[str, Any], market: dict[str, Any] | None) -> GradedFlow:
    side = str(row.get("side") or "")
    mid_pct = row.get("mid_pct")
    mid_pct = float(mid_pct) if mid_pct is not None else None
    game_time = int((market or {}).get("gameTime") or row.get("game_time") or 0)
    if market is None:
        return GradedFlow(
            motive=str(row.get("motive") or ""),
            side=side,
            confidence=float(row.get("confidence") or 0.0),
            league=str(row.get("league") or ""),
            label=str(row.get("label") or ""),
            phase=str(row.get("phase") or ""),
            game_time=game_time,
            mid_pct=mid_pct,
            result="missing",
            edge=None,
        )
    outcome = market.get("outcome")
    outcome = int(outcome) if outcome is not None else None
    result = _result_for(side, outcome)
    edge: float | None = None
    implied = _side_implied_prob(side, mid_pct)
    if implied is not None and result in {"win", "lose"}:
        actual = 1.0 if result == "win" else 0.0
        edge = actual - implied
    return GradedFlow(
        motive=str(row.get("motive") or ""),
        side=side,
        confidence=float(row.get("confidence") or 0.0),
        league=str(row.get("league") or market.get("leagueLabel") or ""),
        label=str(row.get("label") or ""),
        phase=str(row.get("phase") or ""),
        game_time=game_time,
        mid_pct=mid_pct,
        result=result,
        edge=edge,
    )


def grade_flow(rows: list[dict[str, Any]], markets: dict[str, dict[str, Any]]) -> list[GradedFlow]:
    out: list[GradedFlow] = []
    for row in rows:
        market_hash = str(row.get("market") or "")
        out.append(grade_flow_row(row, markets.get(market_hash)))
    return out


@dataclass
class Aggregate:
    n: int
    settled: int
    wins: int
    losses: int
    voids: int
    hit_rate: float | None
    avg_edge: float | None
    edge_n: int


def _aggregate(bets: list[GradedFlow]) -> Aggregate:
    settled = [b for b in bets if b.result in {"win", "lose", "void"}]
    wins = sum(1 for b in settled if b.result == "win")
    losses = sum(1 for b in settled if b.result == "lose")
    voids = sum(1 for b in settled if b.result == "void")
    decided = wins + losses
    edges = [b.edge for b in bets if b.edge is not None]
    return Aggregate(
        n=len(bets),
        settled=len(settled),
        wins=wins,
        losses=losses,
        voids=voids,
        hit_rate=(wins / decided) if decided else None,
        avg_edge=(sum(edges) / len(edges)) if edges else None,
        edge_n=len(edges),
    )


def aggregate_by(bets: list[GradedFlow], key: Any) -> dict[str, Aggregate]:
    grouped: dict[str, list[GradedFlow]] = defaultdict(list)
    for bet in bets:
        grouped[key(bet)].append(bet)
    return {name: _aggregate(rows) for name, rows in grouped.items()}


def _kickoff(game_time: int) -> str:
    if not game_time:
        return ""
    return datetime.fromtimestamp(game_time, tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def _fmt_row(name: str, agg: Aggregate, *, width: int = 16) -> str:
    hr = f"{agg.hit_rate:.0%}" if agg.hit_rate is not None else "-"
    edge = f"{agg.avg_edge:+.1%}" if agg.avg_edge is not None else "n/a"
    return (
        f"  {name:<{width}} n={agg.n:<5} settled={agg.settled:<5} "
        f"win/loss {agg.wins}/{agg.losses}  hit={hr:<5} avg edge={edge:>7} (n={agg.edge_n})"
    )


def format_scoreboard(bets: list[GradedFlow]) -> str:
    if not bets:
        return (
            "No flow logged yet. Leave `sxbot run` or `sxbot flow` going for a "
            "while, then run `sxbot scoreboard` after some of those games finish."
        )
    lines: list[str] = []
    lines.append(
        "Grading the SIGNAL itself, not paper orders: did the side the book "
        "flagged as informed actually win, and did it beat the price the book "
        "was already quoting at that moment?"
    )
    lines.append(
        "hit rate alone is misleading (favorites 'hit' >50% for free) — "
        "avg edge = actual result minus the implied probability already in "
        "the book when the signal fired. Near 0% edge means no proven signal yet."
    )
    lines.append("")

    overall = _aggregate(bets)
    lines.append("overall")
    lines.append(_fmt_row("all motives", overall, width=14))
    pending = sum(1 for b in bets if b.result == "pending")
    missing = sum(1 for b in bets if b.result == "missing")
    if pending:
        lines.append(f"  ({pending} still pending kickoff/result)")
    if missing:
        lines.append(f"  ({missing} could not be matched to a market — find_markets miss)")
    lines.append("")

    by_motive = aggregate_by(bets, lambda b: b.motive)
    if by_motive:
        lines.append("by motive")
        for name, agg in sorted(by_motive.items(), key=lambda kv: kv[1].n, reverse=True):
            lines.append(_fmt_row(name, agg))
        lines.append("")

    by_conf = aggregate_by(bets, lambda b: confidence_bucket(b.confidence))
    if by_conf:
        lines.append("by confidence bucket")
        order = [name for name, _, _ in CONFIDENCE_BUCKETS]
        for name in order:
            if name in by_conf:
                lines.append(_fmt_row(name, by_conf[name], width=10))
        lines.append("")

    by_phase = aggregate_by(bets, lambda b: b.phase or "unknown")
    if by_phase:
        lines.append("by phase")
        for name, agg in sorted(by_phase.items(), key=lambda kv: kv[1].n, reverse=True):
            lines.append(_fmt_row(name, agg, width=10))
        lines.append("")

    settled_with_edge = [b for b in bets if b.edge is not None]
    if len(settled_with_edge) < 30:
        lines.append(
            f"Only {len(settled_with_edge)} settled events with a usable edge so far. "
            "Treat every number above as noise until this is well past 100 — "
            "small samples in sports betting lie constantly."
        )
    return "\n".join(lines).rstrip()
