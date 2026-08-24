"""Score paper bets after SX reports the winning side.

This is not a historical backtest of the order book. SX does not keep old
books, so we cannot rewind last season. What we can do is: if `sxbot run`
logged a paper quote, look the market up later and ask "if that quote had
been filled, did we win?"

Finished on TV is not enough. SX only settles when `GET /markets/find`
returns an `outcome` (and usually `reportedDate`). Totals often report
before moneylines and spreads.
"""

from __future__ import annotations

import time
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from sxbot.api import lookup_market
from sxbot.filters import STYLE_MM
from sxbot.models import Action
from sxbot.units import payout, to_usdc


@dataclass(frozen=True)
class GradedBet:
    label: str
    league: str
    side: str
    action: str
    motive: str
    stake_usdc: float
    odds_pct: float
    result: str  # pending, win, lose, void, missing
    pnl_usdc: float | None
    winner: str | None
    game_time: int
    score: str | None = None
    reported_at: int | None = None
    picked: str | None = None


def _kickoff(game_time: int) -> str:
    if not game_time:
        return ""
    return datetime.fromtimestamp(game_time, tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def _as_outcome(raw: Any) -> int | None:
    if raw is None or raw == "":
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def _waiting_note(game_time: int, *, now: int | None = None) -> str:
    """SX outcome is missing. Distinguish not-started vs in-play/unreported."""
    when = _kickoff(game_time) or "kickoff unknown"
    if not game_time:
        return when
    now = now if now is not None else int(time.time())
    if game_time > now:
        return f"{when}  (not started)"
    elapsed = now - game_time
    if elapsed < 3600:
        return f"{when}  (started {elapsed // 60}m ago; SX not reported yet)"
    return f"{when}  (started {elapsed // 3600}h ago; SX not reported yet)"


def _picked_name(
    row: dict[str, Any],
    market: dict[str, Any] | None,
    side: str,
    label: str,
) -> str | None:
    """Name the side we quoted, not the first team in the market label."""
    if side == "outcome_one":
        for value in (
            (market or {}).get("outcomeOneName"),
            row.get("outcome_one"),
        ):
            name = str(value or "").strip()
            if name:
                return name
        parts = [p.strip() for p in label.split(" / ") if p.strip()]
        return parts[0] if parts else None
    if side == "outcome_two":
        for value in (
            (market or {}).get("outcomeTwoName"),
            row.get("outcome_two"),
        ):
            name = str(value or "").strip()
            if name:
                return name
        parts = [p.strip() for p in label.split(" / ") if p.strip()]
        return parts[1] if len(parts) > 1 else None
    return None


def _result_for(side: str, outcome: int | None) -> str:
    if outcome is None:
        return "pending"
    if outcome == 0:
        return "void"
    we_one = side == "outcome_one"
    if (outcome == 1 and we_one) or (outcome == 2 and not we_one):
        return "win"
    if outcome in {1, 2}:
        return "lose"
    return "pending"


def grade_row(row: dict[str, Any], market: dict[str, Any] | None, *, decimals: int = 6) -> GradedBet:
    label = str(row.get("label") or "")
    league = str(row.get("league") or "")
    side = str(row.get("side") or "")
    stake = int(row.get("stake") or 0)
    odds = int(row.get("odds") or 0)
    stake_usdc = float(row.get("stake_usdc") or to_usdc(stake, decimals))
    odds_pct = float(row.get("odds_pct") or 0)
    game_time = int((market or {}).get("gameTime") or row.get("game_time") or 0)
    picked = _picked_name(row, market, side, label)
    if market is None:
        return GradedBet(
            label=label,
            league=league,
            side=side,
            action=str(row.get("action") or ""),
            motive=str(row.get("motive") or ""),
            stake_usdc=stake_usdc,
            odds_pct=odds_pct,
            result="missing",
            pnl_usdc=None,
            winner=None,
            game_time=game_time,
            picked=picked,
        )
    outcome = _as_outcome(market.get("outcome"))
    winner = None
    if outcome == 1:
        winner = str(market.get("outcomeOneName") or "outcome one")
    elif outcome == 2:
        winner = str(market.get("outcomeTwoName") or "outcome two")
    elif outcome == 0:
        winner = "void"
    result = _result_for(side, outcome)
    pnl: float | None = None
    if result == "void":
        pnl = 0.0
    elif result == "win" and odds > 0:
        pnl = to_usdc(payout(stake, odds) - stake, decimals)
    elif result == "lose":
        pnl = -stake_usdc
    score = None
    if market.get("teamOneScore") is not None and market.get("teamTwoScore") is not None:
        score = f"{market.get('teamOneScore')}-{market.get('teamTwoScore')}"
    reported_raw = market.get("reportedDate")
    reported_at = int(reported_raw) if reported_raw not in (None, "") else None
    return GradedBet(
        label=label or f"{market.get('outcomeOneName')} / {market.get('outcomeTwoName')}",
        league=league or str(market.get("leagueLabel") or ""),
        side=side,
        action=str(row.get("action") or ""),
        motive=str(row.get("motive") or ""),
        stake_usdc=stake_usdc,
        odds_pct=odds_pct,
        result=result,
        pnl_usdc=pnl,
        winner=winner,
        game_time=game_time,
        score=score,
        reported_at=reported_at,
        picked=picked,
    )


def gradeable_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """MM paper is graded on tape fills only. Follow-bot still assumes joins fill."""
    mmish = any(
        str(row.get("style") or "") == STYLE_MM or str(row.get("action") or "") == Action.MM_FILL.value
        for row in rows
    )
    if mmish:
        return [row for row in rows if str(row.get("action") or "") == Action.MM_FILL.value]
    return [row for row in rows if str(row.get("action") or "") != Action.CANCEL.value]


def grade_paper(
    rows: list[dict[str, Any]],
    markets: dict[str, dict[str, Any]],
    *,
    decimals: int = 6,
) -> list[GradedBet]:
    out: list[GradedBet] = []
    for row in gradeable_rows(rows):
        market_hash = str(row.get("market") or "")
        out.append(grade_row(row, lookup_market(markets, market_hash), decimals=decimals))
    return out


def format_grade(bets: list[GradedBet], *, now: int | None = None) -> str:
    lines: list[str] = []
    if not bets:
        lines.append("No paper bets in the log yet. Leave `sxbot run` or `sxbot mm` going first.")
        return "\n".join(lines)

    counts = Counter(b.result for b in bets)
    settled = [b for b in bets if b.result in {"win", "lose", "void"}]
    pnl = sum(b.pnl_usdc or 0.0 for b in settled)
    staked = sum(b.stake_usdc for b in settled)
    mm_fills = bool(bets) and all(b.action == Action.MM_FILL.value for b in bets)
    if mm_fills:
        lines.append(
            "Pregame maker fills matched against the public tape (ghost quotes the "
            "opposite side had to take at/through). Unfilled resting quotes are not scored."
        )
    else:
        lines.append(
            "This is NOT a rewind of old order books. SX does not keep those. "
            "This scores paper quotes *after SX reports the outcome*, assuming each quote got filled."
        )
    lines.append(
        "A TV final is not enough — `sxbot grade` waits for SX `outcome`/`reportedDate`. "
        "Totals often report before moneylines and spreads."
    )
    if not mm_fills:
        lines.append("Joining as a maker often does not fill — real results will usually be smaller.")
    lines.append("")
    lines.append(f"paper quotes     {len(bets)}")
    lines.append(f"  still pending  {counts.get('pending', 0)}")
    lines.append(f"  won            {counts.get('win', 0)}")
    lines.append(f"  lost           {counts.get('lose', 0)}")
    lines.append(f"  void           {counts.get('void', 0)}")
    if counts.get("missing"):
        lines.append(f"  not found      {counts['missing']}")
    stacked = sum(
        n - 1
        for n in Counter((b.label, b.side) for b in bets if b.action == "join_maker").values()
        if n > 1
    )
    if stacked:
        lines.append(
            f"  stacked joins  {stacked} extra join_maker quote(s) on a side already joined "
            "(size flicker used to restack; classifier + risk gate now skip that)"
        )
    if settled:
        lines.append(f"settled stake    {staked:.1f} USDC")
        if mm_fills:
            lines.append(f"paper P&L        {pnl:+.2f} USDC   (tape-matched fills only)")
        else:
            lines.append(f"paper P&L        {pnl:+.2f} USDC   (if every quote filled)")
    else:
        lines.append(
            "No games in this log have an SX-reported outcome yet. "
            "Run `sxbot grade` again after find_markets shows `outcome` 1 or 2."
        )

    pending = [b for b in bets if b.result == "pending"]
    if pending:
        now = now if now is not None else int(time.time())
        not_started = [b for b in pending if b.game_time and b.game_time > now]
        waiting_sx = [b for b in pending if not (b.game_time and b.game_time > now)]
        lines.append("")
        if waiting_sx:
            lines.append("waiting on SX report (kickoff already passed)")
            seen: set[str] = set()
            for bet in waiting_sx:
                key = f"{bet.label}|{bet.game_time}"
                if key in seen:
                    continue
                seen.add(key)
                lines.append(f"  {bet.league:<16} {bet.label[:40]:<40}  {_waiting_note(bet.game_time, now=now)}")
        if not_started:
            lines.append("waiting on kickoff")
            seen = set()
            for bet in not_started:
                key = f"{bet.label}|{bet.game_time}"
                if key in seen:
                    continue
                seen.add(key)
                lines.append(f"  {bet.league:<16} {bet.label[:40]:<40}  {_kickoff(bet.game_time)}")

    if settled:
        lines.append("")
        lines.append("settled")
        for bet in settled:
            mark = {"win": "WIN ", "lose": "LOSE", "void": "VOID"}[bet.result]
            extra = f"  {bet.score}" if bet.score else ""
            pnl_s = f"{bet.pnl_usdc:+.2f}" if bet.pnl_usdc is not None else "n/a"
            pick = bet.picked or bet.side
            lines.append(
                f"  {mark} {pnl_s:>8}  picked {pick[:28]:<28}  "
                f"{bet.label[:32]}  @ {bet.odds_pct}%  {bet.motive}{extra}"
            )
    return "\n".join(lines)
