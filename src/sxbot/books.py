"""Sports-bettor cards: bot unique, your tickets, and the two together.

These are filters on two piles that must stay labeled:

- **bot** — `sxbot run` unique follow (paper assumes fills). MM stays out.
- **you** — tickets you logged with `sxbot bet add`. Each add is a ticket.
- **together** — those two piles on one card. Bot side still assumes fills.

A sports bettor reads W–L, win%, ROI, units, average price, break-even,
CLV, streak, and drawdown — not a single green number.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any


def _base_record(views: list[dict[str, Any]]) -> dict[str, Any]:
    settled = [v for v in views if v.get("result") in {"win", "lose"}]
    wins = sum(1 for v in settled if v["result"] == "win")
    losses = sum(1 for v in settled if v["result"] == "lose")
    n = wins + losses
    stake = sum(_f(v.get("stake_usdc")) for v in settled)
    pnl = sum(_f(v.get("pnl_usdc")) for v in settled)
    return {
        "n": len(views),
        "wins": wins,
        "losses": losses,
        "pending": sum(1 for v in views if v.get("result") == "pending"),
        "win_pct": round(100.0 * wins / n, 1) if n else None,
        "stake_usdc": round(stake, 2),
        "pnl_usdc": round(pnl, 2),
        "roi_pct": round(100.0 * pnl / stake, 1) if stake else None,
    }


def _clv_summary(views: list[dict[str, Any]]) -> dict[str, Any]:
    scored = [v for v in views if v.get("clv_pct") is not None]
    if not scored:
        return {"n": 0, "avg_clv_pct": None, "beat_close": 0, "lost_close": 0}
    values = [float(v["clv_pct"]) for v in scored]
    return {
        "n": len(scored),
        "avg_clv_pct": round(sum(values) / len(values), 3),
        "beat_close": sum(1 for v in values if v > 0),
        "lost_close": sum(1 for v in values if v < 0),
    }


def _f(value: Any, default: float = 0.0) -> float:
    try:
        if value in (None, ""):
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _settled(views: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [v for v in views if v.get("result") in {"win", "lose"}]


def _avg_decimal(views: list[dict[str, Any]]) -> float | None:
    prices = [_f(v.get("decimal")) for v in views if _f(v.get("decimal")) > 1]
    if not prices:
        return None
    return round(sum(prices) / len(prices), 3)


def _streak(settled: list[dict[str, Any]]) -> str | None:
    if not settled:
        return None
    ordered = sorted(settled, key=lambda v: _f(v.get("ts")))
    last = str(ordered[-1]["result"])
    n = 0
    for view in reversed(ordered):
        if view.get("result") != last:
            break
        n += 1
    mark = "W" if last == "win" else "L"
    return f"{mark}{n}"


def _max_drawdown(settled: list[dict[str, Any]]) -> float:
    ordered = sorted(settled, key=lambda v: _f(v.get("ts")))
    peak = 0.0
    equity = 0.0
    dd = 0.0
    for view in ordered:
        equity += _f(view.get("pnl_usdc"))
        if equity > peak:
            peak = equity
        drop = peak - equity
        if drop > dd:
            dd = drop
    return round(dd, 2)


def _by_league(views: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for view in views:
        name = str(view.get("league") or "unknown").strip() or "unknown"
        grouped[name].append(view)
    out = {name: _base_record(rows) for name, rows in grouped.items()}
    return dict(sorted(out.items(), key=lambda kv: kv[1].get("n") or 0, reverse=True))


def bettor_card(views: list[dict[str, Any]]) -> dict[str, Any]:
    """W–L / ROI plus the numbers a sports bettor actually checks."""
    rec = dict(_base_record(views))
    settled = _settled(views)
    voids = sum(1 for v in views if v.get("result") == "void")
    missing = sum(1 for v in views if v.get("result") == "missing")
    pending_views = [v for v in views if v.get("result") in {"pending", "missing"}]
    stake = _f(rec.get("stake_usdc"))
    pnl = _f(rec.get("pnl_usdc"))
    avg_stake = round(stake / len(settled), 2) if settled else None
    avg_dec = _avg_decimal(settled) or _avg_decimal(views)
    be = round(100.0 / avg_dec, 1) if avg_dec and avg_dec > 1 else None
    win_pct = rec.get("win_pct")
    units = round(pnl / avg_stake, 2) if avg_stake else None
    wins_pnl = [_f(v.get("pnl_usdc")) for v in settled if v.get("result") == "win"]
    lose_pnl = [_f(v.get("pnl_usdc")) for v in settled if v.get("result") == "lose"]
    clv = _clv_summary(views)
    rec.update(
        {
            "voids": voids,
            "missing": missing,
            "open_n": len(pending_views),
            "open_usdc": round(sum(_f(v.get("stake_usdc")) for v in pending_views), 2),
            "avg_stake_usdc": avg_stake,
            "avg_decimal": avg_dec,
            "breakeven_win_pct": be,
            "win_vs_be": (
                round(float(win_pct) - be, 1) if win_pct is not None and be is not None else None
            ),
            "units": units,
            "biggest_win_usdc": round(max(wins_pnl), 2) if wins_pnl else None,
            "biggest_loss_usdc": round(min(lose_pnl), 2) if lose_pnl else None,
            "streak": _streak(settled),
            "max_drawdown_usdc": _max_drawdown(settled) if settled else 0.0,
            "clv": clv,
            "by_league": _by_league(views),
        }
    )
    return rec


def format_card(rec: dict[str, Any] | None, title: str, *, note: str = "") -> str:
    if not rec:
        return f"{title}\n  no tickets"
    wp = rec.get("win_pct")
    wp_s = f"{wp:.1f}%" if wp is not None else "—"
    roi = rec.get("roi_pct")
    roi_s = f"{roi:+.1f}%" if roi is not None else "—"
    pnl = rec.get("pnl_usdc")
    units = rec.get("units")
    be = rec.get("breakeven_win_pct")
    vs_be = rec.get("win_vs_be")
    avg = rec.get("avg_decimal")
    clv = rec.get("clv") or {}
    voids = int(rec.get("voids") or 0)
    record = f"{rec.get('wins', 0)}–{rec.get('losses', 0)}"
    if voids:
        record += f"–{voids}"
    headline = f"  {record}   win {wp_s}   ROI {roi_s}"
    if pnl is not None:
        headline += f"   P&L {pnl:+.2f}"
    stake_line = f"  staked ${float(rec.get('stake_usdc') or 0):.2f} settled"
    if rec.get("avg_stake_usdc"):
        stake_line += f"   avg stake ${rec['avg_stake_usdc']:.2f}"
    if units is not None:
        stake_line += f"   units {units:+.2f}"
    price = f"  avg odds {avg:.2f}" if avg else "  avg odds —"
    if be is not None:
        price += f"   break-even {be:.1f}%"
    if vs_be is not None:
        price += f"   win−BE {vs_be:+.1f}"
    open_line = (
        f"  pending {rec.get('pending', 0)}"
        f"   open ${float(rec.get('open_usdc') or 0):.2f}"
        f"   n={rec.get('n', 0)}"
    )
    if rec.get("missing"):
        open_line += f"   missing {rec['missing']}"
    lines = [title, headline, stake_line, price, open_line]
    extra = []
    if rec.get("streak"):
        extra.append(f"streak {rec['streak']}")
    if rec.get("max_drawdown_usdc"):
        extra.append(f"max DD ${rec['max_drawdown_usdc']:.2f}")
    if rec.get("biggest_win_usdc") is not None:
        extra.append(f"best {rec['biggest_win_usdc']:+.2f}")
    if rec.get("biggest_loss_usdc") is not None:
        extra.append(f"worst {rec['biggest_loss_usdc']:+.2f}")
    if extra:
        lines.append("  " + "   ".join(extra))
    if clv.get("n"):
        avg_clv = clv.get("avg_clv_pct")
        avg_s = f"{avg_clv:+.2f} pts" if avg_clv is not None else "—"
        lines.append(
            f"  CLV {avg_s}   beat close {clv.get('beat_close', 0)} / "
            f"lost {clv.get('lost_close', 0)}  (n={clv.get('n')})"
        )
    leagues = rec.get("by_league") or {}
    if leagues:
        bits = []
        for name, row in list(leagues.items())[:6]:
            r = row.get("roi_pct")
            r_s = f" {r:+.0f}%" if r is not None else ""
            bits.append(f"{name} {row.get('wins', 0)}–{row.get('losses', 0)}{r_s}")
        lines.append("  by league  " + " · ".join(bits))
    if note:
        lines.append(f"  {note}")
    return "\n".join(lines)


def format_card_oneline(rec: dict[str, Any] | None) -> str:
    if not rec:
        return "no tickets"
    wp = rec.get("win_pct")
    wp_s = f"{wp:.0f}%" if wp is not None else "—"
    roi = rec.get("roi_pct")
    roi_s = f"  ROI {roi:+.0f}%" if roi is not None else ""
    units = rec.get("units")
    units_s = f"  {units:+.1f}u" if units is not None else ""
    avg = rec.get("avg_decimal")
    avg_s = f"  avg {avg:.2f}" if avg else ""
    be = rec.get("breakeven_win_pct")
    be_s = f"  BE {be:.0f}%" if be is not None else ""
    return (
        f"{rec.get('wins', 0)}–{rec.get('losses', 0)}  {wp_s}{roi_s}"
        f"  P&L {float(rec.get('pnl_usdc') or 0):+.2f}{units_s}{avg_s}{be_s}"
        f"   pending {rec.get('pending', 0)}  open ${float(rec.get('open_usdc') or 0):.0f}"
    )


def format_ticket(view: dict[str, Any]) -> str:
    stake = _f(view.get("stake_usdc"))
    dec = view.get("decimal")
    dec_s = f"{float(dec):.2f}" if dec not in (None, "") else "?"
    book = str(view.get("book") or view.get("style") or "").strip()
    book_s = f"  [{book}]" if book else ""
    result = str(view.get("result") or "")
    pnl = view.get("pnl_usdc")
    pnl_s = f"  {float(pnl):+.2f}" if pnl is not None else ""
    return (
        f"  {view.get('when') or ''}  ${stake:g} {view.get('picked')} @{dec_s}"
        f"{book_s}  {result}{pnl_s}  {view.get('label') or ''}"
    ).rstrip()


def format_wl(rec: dict[str, Any] | None, title: str) -> str:
    if not rec:
        return f"{title}  —"
    wp = rec.get("win_pct")
    wp_s = f"{wp:.1f}%" if wp is not None else "—"
    roi = rec.get("roi_pct")
    roi_s = f"  ROI {roi:+.1f}%" if roi is not None else ""
    pnl = rec.get("pnl_usdc")
    pnl_s = f"  P&L {pnl:+.2f}" if pnl is not None else ""
    return (
        f"{title}  {rec.get('wins', 0)}–{rec.get('losses', 0)}  "
        f"win {wp_s}{roi_s}{pnl_s}  pending {rec.get('pending', 0)}"
    )


def format_dashboard(snap: dict[str, Any]) -> str:
    """Phone recap: the performance cards, not the 5k tape table."""
    clv = snap.get("follow_clv") or (snap.get("bot_book") or {}).get("clv") or {}
    if clv.get("n"):
        avg = clv.get("avg_clv_pct")
        avg_s = f"{avg:+.2f} pts" if avg is not None else "—"
        clv_line = (
            f"CLV  {avg_s}  beat {clv.get('beat_close', 0)} / "
            f"lost {clv.get('lost_close', 0)}  (n={clv.get('n')})"
        )
    else:
        clv_line = "CLV  no kickoff closes stamped yet"
    lines = [
        f"sxbot dashboard  {snap.get('generated_at') or ''}",
        format_wl(snap.get("priced_ev"), "short+EV"),
        format_wl(snap.get("best_priced"), "best priced"),
        format_wl(snap.get("follow_record"), "bot unique $5"),
        format_wl(snap.get("live_record"), "live $1–$4"),
        clv_line,
        "",
        format_card(snap.get("bot_book"), "BOT"),
        "",
        format_card(snap.get("you_book"), "YOU"),
        "",
        format_card(snap.get("together_book"), "TOGETHER"),
    ]
    tickets = list(snap.get("you_tickets") or [])[:6]
    if tickets:
        lines.append("")
        lines.append("your tickets")
        for view in tickets:
            lines.append(format_ticket(view))
    text = "\n".join(lines)
    return text[:3900]


def telegram_books_due(last_sent: float, interval: float, now: float) -> bool:
    if interval <= 0:
        return False
    return last_sent <= 0 or (now - last_sent) >= interval


def format_books(
    bot: dict[str, Any] | None,
    you: dict[str, Any] | None,
    together: dict[str, Any] | None,
    *,
    you_tickets: list[dict[str, Any]] | None = None,
) -> str:
    lines = [
        "sxbot books  bot / you / together",
        format_card(
            bot,
            "BOT  sxbot run unique",
            note="paper assumes fills — live ROI will be worse",
        ),
        "",
        format_card(
            you,
            "YOU  tickets you logged",
            note="sxbot bet add  — real tickets, not mixed into bot unique",
        ),
        "",
        format_card(
            together,
            "TOGETHER  bot unique + your tickets",
            note="do not mix this ROI with the maker bot (sxbot mm)",
        ),
    ]
    tickets = list(you_tickets or [])
    if tickets:
        lines.append("")
        lines.append("your tickets (newest first)")
        for view in tickets[:12]:
            lines.append(format_ticket(view))
    return "\n".join(lines)
