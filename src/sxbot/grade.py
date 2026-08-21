"""Score paper bets after SX reports the winning side.

This is not a historical backtest of the order book. SX does not keep old
books, so we cannot rewind last season. What we can do is: if `sxbot run`
logged a paper quote, look the market up later and ask "if that quote had
been filled, did we win?"
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from sxbot.journal import load_jsonl
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


def _kickoff(game_time: int) -> str:
    if not game_time:
        return ""
    return datetime.fromtimestamp(game_time, tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


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
        )
    raw = market.get("outcome")
    outcome = int(raw) if raw is not None else None
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
    )


def grade_paper(
    rows: list[dict[str, Any]],
    markets: dict[str, dict[str, Any]],
    *,
    decimals: int = 6,
) -> list[GradedBet]:
    out: list[GradedBet] = []
    for row in rows:
        market_hash = str(row.get("market") or "")
        out.append(grade_row(row, markets.get(market_hash), decimals=decimals))
    return out


def format_grade(bets: list[GradedBet]) -> str:
    lines: list[str] = []
    if not bets:
        lines.append("No paper bets in the log yet. Leave `sxbot run` going first.")
        return "\n".join(lines)

    counts = Counter(b.result for b in bets)
    settled = [b for b in bets if b.result in {"win", "lose", "void"}]
    pnl = sum(b.pnl_usdc or 0.0 for b in settled)
    staked = sum(b.stake_usdc for b in settled)
    lines.append(
        "This is NOT a rewind of old order books. SX does not keep those. "
        "This scores paper quotes *after the game is reported*, assuming each quote got filled."
    )
    lines.append("Joining as a maker often does not fill — real results will usually be smaller.")
    lines.append("")
    lines.append(f"paper quotes     {len(bets)}")
    lines.append(f"  still pending  {counts.get('pending', 0)}")
    lines.append(f"  won            {counts.get('win', 0)}")
    lines.append(f"  lost           {counts.get('lose', 0)}")
    lines.append(f"  void           {counts.get('void', 0)}")
    if counts.get("missing"):
        lines.append(f"  not found      {counts['missing']}")
    if settled:
        lines.append(f"settled stake    {staked:.1f} USDC")
        lines.append(f"paper P&L        {pnl:+.2f} USDC   (if every quote filled)")
    else:
        lines.append("No games in this log have been reported yet. Run `sxbot grade` again after they finish.")

    pending = [b for b in bets if b.result == "pending"]
    if pending:
        lines.append("")
        lines.append("waiting on")
        seen: set[str] = set()
        for bet in pending:
            key = f"{bet.label}|{bet.game_time}"
            if key in seen:
                continue
            seen.add(key)
            when = _kickoff(bet.game_time) or "kickoff unknown"
            lines.append(f"  {bet.league:<16} {bet.label[:40]:<40}  {when}")

    if settled:
        lines.append("")
        lines.append("settled")
        for bet in settled:
            mark = {"win": "WIN ", "lose": "LOSE", "void": "VOID"}[bet.result]
            extra = f"  {bet.score}" if bet.score else ""
            pnl_s = f"{bet.pnl_usdc:+.2f}" if bet.pnl_usdc is not None else "n/a"
            lines.append(
                f"  {mark} {pnl_s:>8}  {bet.label[:36]:<36}  "
                f"{bet.side} @ {bet.odds_pct}%  {bet.motive}{extra}"
            )
    return "\n".join(lines)
