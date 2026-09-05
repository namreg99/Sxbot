"""Auto-refresh dashboard: 5k+ tape + paper feed.

This is the thing you reload yourself. Telegram is optional push on top of
the same snapshot — tables belong in a browser, not a chat bubble.
"""

from __future__ import annotations

import html
import json
import logging
import threading
import time
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx

from sxbot.api import SxClient, index_markets, lookup_market
from sxbot.config import Settings
from sxbot.fingerprint import trade_pnl_usdc
from sxbot.filters import STYLE_MM
from sxbot.grade import _picked_name, grade_row, opposite_side
from sxbot.journal import load_all_live, load_all_paper, load_jsonl
from sxbot.kelly import shadow_kelly_from_row
from sxbot.units import complement_decimal, decimal_odds, to_percent, to_prob
from sxbot.wallets import CANDIDATE_WALLETS, KNOWN_WALLETS, labeled_addresses

log = logging.getLogger("sxbot.board")

_TRADE_ACTIONS = {"join_maker", "take_stale", "take_flow", "mm_fill"}
BEST_PRICED_MAX_DECIMAL = 1.80
DEFAULT_MIN_IMBALANCE = 0.15
LIVE_TEST_TRADES = 100
# Follow styles we always show on the not-EV card, even at 0–0.
NOT_EV_STYLES = ("mlb", "mlb_dog", "soccer", "soccer_dog", "tennis_short", "tennis_dog", "totals")


def _utc(ts: int | float | None) -> str:
    if not ts:
        return ""
    return datetime.fromtimestamp(int(ts), tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def _day_bounds(now: datetime | None = None) -> tuple[datetime, datetime, datetime]:
    now = now if now is not None else datetime.now(tz=timezone.utc)
    today = now.replace(hour=0, minute=0, second=0, microsecond=0)
    yesterday = today - timedelta(days=1)
    return yesterday, today, now


def _who_map(settings: Settings) -> dict[str, str]:
    labels = dict(labeled_addresses(settings))
    for name, addr in {**KNOWN_WALLETS, **CANDIDATE_WALLETS}.items():
        labels.setdefault(addr, name)
    return labels


def _short_addr(addr: str) -> str:
    a = (addr or "").lower()
    if a.startswith("0x") and len(a) >= 10:
        return a[:6] + "…" + a[-4:]
    return a or "?"


def _windows(start: int, end: int, step: int, *, newest_first: bool = False) -> list[tuple[int, int]]:
    out: list[tuple[int, int]] = []
    cur = start
    while cur < end:
        nxt = min(cur + step, end)
        out.append((cur, nxt))
        cur = nxt
    if newest_first:
        out.reverse()
    return out


def _tape_windows(start: int, end: int) -> list[tuple[int, int]]:
    """Newest first. Tight recent slices so oldest-first /trades still reaches now."""
    start, end = int(start), int(end)
    if end <= start:
        return []
    out: list[tuple[int, int]] = []
    t = end
    recent = max(start, end - 2 * 3600)
    while t > recent:
        w0 = max(recent, t - 600)
        out.append((w0, t))
        t = w0
    mid = max(start, end - 12 * 3600)
    while t > mid:
        w0 = max(mid, t - 3600)
        out.append((w0, t))
        t = w0
    while t > start:
        w0 = max(start, t - 3 * 3600)
        out.append((w0, t))
        t = w0
    return out


def unique_open_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Last join/take still live. A later cancel drops that market+side+style."""
    last: dict[tuple[str, str, str], dict[str, Any]] = {}
    for row in rows:
        market = str(row.get("market") or "")
        side = str(row.get("side") or "")
        if not market or not side:
            continue
        key = (market, side, str(row.get("style") or ""))
        action = str(row.get("action") or "")
        if action == "cancel":
            last.pop(key, None)
            continue
        if action in _TRADE_ACTIONS:
            last[key] = row
    return list(last.values())


def unique_lifetime_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Last join/take/mm_fill per market+side+style. Kickoff cancel does not erase."""
    last: dict[tuple[str, str, str], dict[str, Any]] = {}
    for row in rows:
        market = str(row.get("market") or "")
        side = str(row.get("side") or "")
        if not market or not side:
            continue
        action = str(row.get("action") or "")
        if action in _TRADE_ACTIONS:
            last[(market, side, str(row.get("style") or ""))] = row
    return list(last.values())


def scrape_big_fills(
    client: SxClient,
    *,
    start: int,
    end: int,
    min_usdc: float,
    step: int = 3 * 3600,
    max_pages: int = 20,
    newest_first: bool = True,
    deadline: float | None = None,
) -> list[dict[str, Any]]:
    """Pull V2 public fills ≥ min_usdc in [start, end). Newest windows first."""
    now = int(time.time())
    end = min(int(end), now)
    start = int(start)
    if end <= start:
        return []
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    windows = _tape_windows(start, end) if newest_first else _windows(start, end, step)
    for w0, w1 in windows:
        if deadline is not None and time.time() >= deadline:
            break
        try:
            rows = client.v2_public_trades(start_date=w0, end_date=w1, max_pages=max_pages)
        except Exception:
            log.exception("tape window %s–%s failed", w0, w1)
            continue
        for raw in rows:
            fh = str(raw.get("fillHash") or "")
            if not fh or fh in seen:
                continue
            stake = int(raw.get("stake") or 0) / 1e6
            if stake < min_usdc:
                continue
            seen.add(fh)
            out.append(raw)
    return out


def _fill_view(
    raw: dict[str, Any],
    market: dict[str, Any] | None,
    who: dict[str, str],
) -> dict[str, Any]:
    odds = int(raw.get("odds") or 0)
    stake = int(raw.get("stake") or 0)
    side = "outcome_one" if raw.get("bettingOutcomeOne") else "outcome_two"
    o1 = str((market or {}).get("outcomeOneName") or "")
    o2 = str((market or {}).get("outcomeTwoName") or "")
    label = f"{o1} / {o2}" if o1 or o2 else str(raw.get("marketHash") or "")[:16]
    row = {
        "label": label,
        "league": str((market or {}).get("leagueLabel") or ""),
        "side": side,
        "action": "join_maker" if raw.get("maker") else "take_flow",
        "stake": str(stake),
        "odds": str(odds),
        "odds_pct": to_percent(odds) if odds else 0.0,
        "stake_usdc": stake / 1e6,
        "outcome_one": o1,
        "outcome_two": o2,
        "game_time": int((market or {}).get("gameTime") or 0),
    }
    graded = grade_row(row, market)
    addr = str(raw.get("bettor") or "").lower()
    dec = decimal_odds(to_prob(odds)) if odds else 0.0
    pnl = trade_pnl_usdc(raw)
    if pnl is None and graded.pnl_usdc is not None:
        pnl = graded.pnl_usdc
    return {
        "ts": int(raw.get("betTime") or 0),
        "when": _utc(raw.get("betTime")),
        "who": who.get(addr) or _short_addr(addr),
        "role": "maker" if raw.get("maker") else "taker",
        "stake_usdc": round(stake / 1e6, 1),
        "decimal": round(dec, 2) if dec else None,
        "odds_pct": graded.odds_pct,
        "picked": graded.picked or side,
        "label": graded.label,
        "league": graded.league,
        "result": graded.result,
        "pnl_usdc": None if pnl is None else round(float(pnl), 2),
        "score": graded.score,
        "market": str(raw.get("marketHash") or ""),
    }


def is_best_priced(bet: dict[str, Any], *, max_decimal: float = BEST_PRICED_MAX_DECIMAL) -> bool:
    dec = bet.get("decimal")
    try:
        return bool(dec) and float(dec) <= max_decimal + 1e-9
    except (TypeError, ValueError):
        return False


def is_maker_ev(row: dict[str, Any], *, min_imbalance: float = DEFAULT_MIN_IMBALANCE) -> bool:
    """True when the quote sat with parked maker size, not a steam label alone.

    `maker_steam` without inventory was mixed into this card and made live
    look like EV when it was the −32% priced-not-EV book. Leftover takes
    and MM quotes still count by motive.
    """
    motive = str(row.get("motive") or "")
    if motive in {"take_stale", "mm_quote"}:
        return True
    try:
        imb = float(row.get("imbalance") or 0)
    except (TypeError, ValueError):
        imb = 0.0
    side = str(row.get("side") or "")
    if side == "outcome_one" and imb >= min_imbalance:
        return True
    if side == "outcome_two" and imb <= -min_imbalance:
        return True
    return False


def scale_pnl(
    pnl: float | None,
    src_stake: float,
    dest_stake: float | None,
    result: str,
) -> float | None:
    """Replay P&L at another stake. Lose = −stake; win scales from graded P&L."""
    if dest_stake is None or dest_stake <= 0:
        return None
    if result == "void":
        return 0.0
    if result not in {"win", "lose"}:
        return None
    if src_stake > 0 and pnl is not None:
        return round(float(pnl) * (float(dest_stake) / src_stake), 2)
    if result == "lose":
        return round(-float(dest_stake), 2)
    return None


def load_closes(path: str) -> dict[str, float]:
    """market hash -> implied % of outcome one at kickoff (the closing line)."""
    out: dict[str, float] = {}
    for row in load_jsonl(path):
        market = str(row.get("market") or "")
        close = row.get("close_mid_pct")
        if not market or close in (None, ""):
            continue
        try:
            out[market] = float(close)
        except (TypeError, ValueError):
            continue
    return out


def attach_clv(view: dict[str, Any], closes: dict[str, float]) -> dict[str, Any]:
    """CLV in probability points: closing prob of our side minus our price.

    Positive = we got a better price than the close. This converges on real
    edge much faster than W-L, and cannot be faked by betting favorites.
    """
    close_one = closes.get(str(view.get("market") or ""))
    odds_pct = view.get("odds_pct")
    if close_one is None or odds_pct in (None, "", 0):
        view["clv_pct"] = None
        return view
    side = str(view.get("side") or "")
    close_side = close_one if side == "outcome_one" else 100.0 - close_one
    view["clv_pct"] = round(close_side - float(odds_pct), 3)
    return view


def clv_record(views: list[dict[str, Any]]) -> dict[str, Any]:
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


def _attach_books(view: dict[str, Any], row: dict[str, Any], settings: Settings) -> dict[str, Any]:
    result = str(view.get("result") or "")
    actual_stake = float(view.get("stake_usdc") or 0)
    actual_pnl = view.get("pnl_usdc")
    flat = float(getattr(settings, "stake_usdc", 5) or 5)
    stamped_flat = row.get("flat_stake_usdc")
    if stamped_flat not in (None, ""):
        try:
            flat = float(stamped_flat)
        except (TypeError, ValueError):
            pass
    view["flat_stake_usdc"] = flat
    view["flat_pnl_usdc"] = scale_pnl(actual_pnl, actual_stake, flat, result)
    kelly = shadow_kelly_from_row(settings, row)
    view["kelly_stake_usdc"] = kelly
    view["kelly_pnl_usdc"] = scale_pnl(actual_pnl, actual_stake, kelly, result)
    view["fair_pct"] = row.get("fair_pct")
    return view


def _paper_view(bet: Any, row: dict[str, Any], settings: Settings) -> dict[str, Any]:
    odds = int(row.get("odds") or 0)
    dec = decimal_odds(to_prob(odds)) if odds else 0.0
    decimal = round(dec, 2) if dec else None
    stake = float(row.get("stake_usdc") or bet.stake_usdc or 0)
    opp = opposite_side(bet.side)
    other_name = _picked_name(row, None, opp, bet.label) or opp
    other_dec = round(complement_decimal(dec), 2) if dec else None
    action = bet.action
    if action in {"take_stale", "take_flow"}:
        verb = "take"
    elif action == "mm_fill":
        verb = "fill"
    elif action == "cancel":
        verb = "cancel"
    else:
        verb = "make"
    picked = bet.picked or bet.side
    # Taker bot: same team as the makers, at taker (or join) odds.
    # Maker bot: quoted team at maker odds. Fill mechanic = someone bet the
    # other side at the complement (SF @ 2.45 fills when someone bets Cincy @ 1.69).
    view = {
        "ts": float(row.get("ts") or 0),
        "when": _utc(row.get("ts")),
        "style": str(row.get("style") or "") or "legacy",
        "action": action,
        "verb": verb,
        "picked": picked,
        "make_on": picked,
        "make_decimal": decimal,
        "take_picked": picked if verb == "take" else other_name,
        "take_on": picked if verb == "take" else other_name,
        "take_decimal": decimal if verb == "take" else other_dec,
        "fill_when": other_name,
        "fill_when_decimal": other_dec,
        "label": bet.label,
        "league": bet.league,
        "decimal": decimal,
        "odds_pct": bet.odds_pct,
        "result": bet.result,
        "pnl_usdc": bet.pnl_usdc,
        "stake_usdc": stake,
        "kickoff": _utc(bet.game_time),
        "market": str(row.get("market") or ""),
        "side": bet.side,
        "motive": bet.motive,
        "imbalance": row.get("imbalance"),
        "with_makers": is_maker_ev(row),
        "best_priced": is_best_priced({"decimal": decimal}),
    }
    return _attach_books(view, row, settings)


def _win_lose_dollars(decimal: float | None, stake: float) -> str:
    if not decimal or decimal <= 1 or stake <= 0:
        return ""
    return f"  win +${stake * (decimal - 1):.2f} / lose −${stake:.0f}"


def make_take_line(b: dict[str, Any]) -> str:
    """Taker = same team as makers. Maker = quoted team; fill = the mirror bet."""
    stake = float(b.get("flat_stake_usdc") or b.get("stake_usdc") or 5)
    action = str(b.get("action") or "")
    picked = str(b.get("picked") or b.get("make_on") or "")
    decimal = b.get("decimal") if b.get("decimal") is not None else b.get("make_decimal")
    fill_when = str(b.get("fill_when") or "")
    fill_dec = b.get("fill_when_decimal")
    if action == "take_flow":
        return (
            f"TAKE ${stake:g} {picked} @{decimal}"
            f"{_win_lose_dollars(decimal, stake)}"
            f"  (same side as makers — pay the spread)"
        )
    if action == "take_stale":
        return (
            f"TAKE ${stake:g} {picked} @{decimal}"
            f"{_win_lose_dollars(decimal, stake)}"
            f"  (stale leftover — same team as the steam)"
        )
    fill_note = ""
    if fill_when and fill_dec:
        fill_note = f"  (fills when someone bets {fill_when} @{fill_dec})"
    return (
        f"MAKE ${stake:g} {picked} @{decimal}"
        f"{_win_lose_dollars(decimal, stake)}"
        f"{fill_note}"
    )


def _record(views: list[dict[str, Any]]) -> dict[str, Any]:
    settled = [v for v in views if v.get("result") in {"win", "lose"}]
    wins = sum(1 for v in settled if v["result"] == "win")
    losses = sum(1 for v in settled if v["result"] == "lose")
    n = wins + losses
    stake = sum(float(v.get("stake_usdc") or 0) for v in settled)
    pnl = sum(float(v.get("pnl_usdc") or 0) for v in settled)
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


def _remap_record(
    views: list[dict[str, Any]],
    stake_key: str,
    pnl_key: str,
    *,
    require_stake: bool = False,
) -> dict[str, Any]:
    remapped: list[dict[str, Any]] = []
    skipped = 0
    for view in views:
        stake = view.get(stake_key)
        if require_stake and not stake:
            if view.get("result") in {"win", "lose"}:
                skipped += 1
            continue
        item = dict(view)
        if stake is not None:
            item["stake_usdc"] = stake
        if view.get(pnl_key) is not None:
            item["pnl_usdc"] = view[pnl_key]
        remapped.append(item)
    rec = _record(remapped)
    rec["skipped"] = skipped
    return rec


def not_ev_log_for(paper_log: str) -> Path:
    return Path(paper_log).with_name("sxbot-not-ev.jsonl")


def _not_ev_card(by_style: dict[str, dict[str, Any]], overall: dict[str, Any]) -> dict[str, Any]:
    card: dict[str, Any] = {
        "all": (
            overall.get("wins"),
            overall.get("losses"),
            overall.get("roi_pct"),
        )
    }
    for style, rec in by_style.items():
        card[style] = (rec.get("wins"), rec.get("losses"), rec.get("roi_pct"))
    return card


def append_not_ev_record(
    paper_log: str,
    *,
    generated_at: str,
    overall: dict[str, Any],
    by_style: dict[str, dict[str, Any]],
) -> None:
    """Append a compact not-EV ROI line when the W–L / ROI card changes."""
    path = not_ev_log_for(paper_log)
    card = _not_ev_card(by_style, overall)
    last = None
    if path.exists():
        try:
            lines = [ln for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]
            if lines:
                last = json.loads(lines[-1])
        except (OSError, json.JSONDecodeError):
            last = None
    if last and last.get("card") == card:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    row = {
        "ts": time.time(),
        "generated_at": generated_at,
        "card": card,
        "not_ev": {
            "wins": overall.get("wins"),
            "losses": overall.get("losses"),
            "roi_pct": overall.get("roi_pct"),
            "pnl_usdc": overall.get("pnl_usdc"),
        },
        "by_style": {
            k: {
                "wins": v.get("wins"),
                "losses": v.get("losses"),
                "roi_pct": v.get("roi_pct"),
                "pnl_usdc": v.get("pnl_usdc"),
            }
            for k, v in by_style.items()
        },
    }
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row) + "\n")


def paper_record(views: list[dict[str, Any]]) -> dict[str, Any]:
    """Unique W–L on the executed paper stake, plus $5 flat and Kelly shadows.

    Follow unique and MM unique are separate bots. Do not flip a make into
    a take book — that is not the taker bot's P&L.
    """
    rec = _record(views)
    rec["flat"] = _remap_record(views, "flat_stake_usdc", "flat_pnl_usdc")
    rec["kelly"] = _remap_record(
        views, "kelly_stake_usdc", "kelly_pnl_usdc", require_stake=True
    )
    return rec


def _settled_n(rec: dict[str, Any] | None) -> int:
    if not rec:
        return 0
    return int(rec.get("wins") or 0) + int(rec.get("losses") or 0)


def live_test_status(
    all_rec: dict[str, Any] | None,
    *,
    follow: dict[str, Any] | None = None,
    mm: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Flag only. Never go live from this — 100 unique settled + both books green."""
    rec = all_rec or {}
    flat = rec.get("flat") or rec
    kelly = rec.get("kelly") or {}
    n = _settled_n(rec)
    kelly_n = _settled_n(kelly)
    flat_roi = flat.get("roi_pct")
    kelly_roi = kelly.get("roi_pct")
    ready = (
        n >= LIVE_TEST_TRADES
        and flat_roi is not None
        and float(flat_roi) > 0
        and kelly_n > 0
        and kelly_roi is not None
        and float(kelly_roi) > 0
    )
    return {
        "target": LIVE_TEST_TRADES,
        "settled": n,
        "remaining": max(0, LIVE_TEST_TRADES - n),
        "follow_settled": _settled_n(follow),
        "mm_settled": _settled_n(mm),
        "flat_roi_pct": flat_roi,
        "kelly_roi_pct": kelly_roi,
        "kelly_settled": kelly_n,
        "kelly_skipped": int(kelly.get("skipped") or 0),
        "ready": ready,
        "note": "Paper only. Do not go live until this flag is ready and you explicitly say so.",
    }


def build_snapshot(
    client: SxClient,
    settings: Settings,
    *,
    tape_rows: list[dict[str, Any]] | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    yesterday, today, current = _day_bounds(now)
    y0, t0, now_ts = int(yesterday.timestamp()), int(today.timestamp()), int(current.timestamp())
    min_usdc = float(settings.big_fill_usdc or 5000)
    who = _who_map(settings)

    if tape_rows is None:
        tape_rows = scrape_big_fills(
            client,
            start=y0,
            end=now_ts,
            min_usdc=min_usdc,
            newest_first=True,
            deadline=time.time() + 45,
        )

    hashes = list(
        dict.fromkeys(str(r.get("marketHash") or "") for r in tape_rows if r.get("marketHash"))
    )
    paper_rows = load_all_paper(settings.paper_log)
    live_rows = load_all_live(settings.paper_log)
    paper_hashes = list(
        dict.fromkeys(str(r.get("market") or "") for r in paper_rows if r.get("market"))
    )
    live_hashes = list(
        dict.fromkeys(str(r.get("market") or "") for r in live_rows if r.get("market"))
    )
    extra_hashes = [h for h in paper_hashes + live_hashes if h not in hashes]
    found = client.find_markets(hashes + extra_hashes) if (hashes or extra_hashes) else []
    markets = index_markets(found)

    fills = [_fill_view(raw, lookup_market(markets, str(raw.get("marketHash") or "")), who) for raw in tape_rows]
    fills.sort(key=lambda r: r["ts"], reverse=True)
    today_fills = [f for f in fills if f["ts"] >= t0]
    yday_fills = [f for f in fills if y0 <= f["ts"] < t0]

    closes = load_closes(getattr(settings, "closes_log", "sxbot-closes.jsonl"))

    def _view_row(row: dict[str, Any]) -> dict[str, Any]:
        view = _paper_view(grade_row(row, lookup_market(markets, str(row.get("market") or ""))), row, settings)
        return attach_clv(view, closes)

    life_bets = [_view_row(row) for row in unique_lifetime_rows(paper_rows)]
    open_bets = [
        b
        for b in (_view_row(row) for row in unique_open_rows(paper_rows))
        if b["result"] in {"pending", "missing"}
    ]
    open_bets.sort(key=lambda b: float(b.get("odds_pct") or 0), reverse=True)
    best_open = open_bets[0] if open_bets else None
    follow_bets = [b for b in life_bets if str(b.get("style") or "") != STYLE_MM]
    best_priced = [b for b in follow_bets if b.get("best_priced")]
    maker_ev = [b for b in follow_bets if b.get("with_makers")]
    not_ev = [b for b in follow_bets if not b.get("with_makers")]
    priced_ev = [b for b in best_priced if b.get("with_makers")]
    by_style: dict[str, list[dict[str, Any]]] = {}
    for bet in life_bets:
        by_style.setdefault(str(bet.get("style") or "legacy"), []).append(bet)
    feed_src = [r for r in paper_rows if str(r.get("action") or "") in _TRADE_ACTIONS | {"cancel"}]
    feed_src = feed_src[-40:]
    feed = [_view_row(row) for row in reversed(feed_src)]

    follow_record = paper_record(follow_bets)
    mm_bets = by_style.get(STYLE_MM) or []
    mm_record = paper_record(mm_bets)
    record = paper_record(life_bets)
    live_life = [_view_row(row) for row in unique_lifetime_rows(live_rows)]
    live_follow = [b for b in live_life if str(b.get("style") or "") != STYLE_MM]
    live_record = paper_record(live_follow)
    not_ev_record = paper_record(not_ev)
    not_ev_by_style = {
        style: paper_record([b for b in not_ev if str(b.get("style") or "") == style])
        for style in NOT_EV_STYLES
    }
    extra_not_ev = sorted(
        {
            str(b.get("style") or "legacy")
            for b in not_ev
            if str(b.get("style") or "legacy") not in NOT_EV_STYLES
        }
    )
    for style in extra_not_ev:
        not_ev_by_style[style] = paper_record(
            [b for b in not_ev if str(b.get("style") or "legacy") == style]
        )
    generated_at = _utc(now_ts)
    append_not_ev_record(
        settings.paper_log,
        generated_at=generated_at,
        overall=not_ev_record,
        by_style=not_ev_by_style,
    )
    return {
        "generated_at": generated_at,
        "generated_ts": now_ts,
        "big_fill_usdc": min_usdc,
        "tape_fills": len(tape_rows),
        "today": today_fills,
        "yesterday": yday_fills,
        "feed": feed,
        "open": open_bets,
        "best_open": best_open,
        "record": record,
        "follow_record": follow_record,
        "live_record": live_record,
        "mm_record": mm_record,
        "follow_clv": clv_record(follow_bets),
        "best_priced": {
            **paper_record(best_priced),
            "max_decimal": BEST_PRICED_MAX_DECIMAL,
        },
        "maker_ev": paper_record(maker_ev),
        "not_ev": not_ev_record,
        "not_ev_by_style": not_ev_by_style,
        "priced_ev": {
            **paper_record(priced_ev),
            "max_decimal": BEST_PRICED_MAX_DECIMAL,
        },
        "by_style": {k: paper_record(v) for k, v in sorted(by_style.items())},
        "today_record": _record(today_fills),
        "yesterday_record": _record(yday_fills),
        "live_test": live_test_status(
            record,
            follow=follow_record,
            mm=mm_record if mm_bets else None,
        ),
    }


def render_html(snap: dict[str, Any], *, refresh: int = 20) -> str:
    def rec(r: dict[str, Any] | None, *, books: bool = False) -> str:
        if not r:
            return "—"
        wp = r.get("win_pct")
        wp_s = f"{wp:.0f}%" if wp is not None else "—"
        roi = r.get("roi_pct")
        roi_s = f"  ROI {roi:+.0f}%" if roi is not None else ""
        pnl = r.get("pnl_usdc")
        pnl_s = f"  PnL {pnl:+.2f}" if pnl is not None and _settled_n(r) else ""
        line = f"{r.get('wins', 0)}–{r.get('losses', 0)}  {wp_s}{roi_s}{pnl_s}   pending {r.get('pending', 0)}"
        kelly = r.get("kelly") if books else None
        if kelly:
            k_roi = kelly.get("roi_pct")
            k_roi_s = f"  ROI {k_roi:+.0f}%" if k_roi is not None else ""
            k_pnl = kelly.get("pnl_usdc")
            k_pnl_s = f"  PnL {k_pnl:+.2f}" if k_pnl is not None and _settled_n(kelly) else ""
            skip = int(kelly.get("skipped") or 0)
            skip_s = f"  skip {skip}" if skip else ""
            line += (
                f" · Kelly {kelly.get('wins', 0)}–{kelly.get('losses', 0)}"
                f"{k_roi_s}{k_pnl_s}{skip_s}"
            )
        return line

    def gate_s(g: dict[str, Any] | None) -> str:
        if not g:
            return "—"
        flag = "READY (still paper)" if g.get("ready") else "not ready"
        return (
            f"{g.get('settled', 0)}/{g.get('target', LIVE_TEST_TRADES)} unique settled  "
            f"follow {g.get('follow_settled', 0)} · MM {g.get('mm_settled', 0)}  · {flag}"
        )

    def clv_s(c: dict[str, Any] | None) -> str:
        if not c or not c.get("n"):
            return "no closes stamped yet (needs a card to pass kickoff)"
        avg = c.get("avg_clv_pct")
        avg_s = f"{avg:+.2f} pts" if avg is not None else "—"
        return (
            f"avg {avg_s} vs close  ·  beat close {c.get('beat_close', 0)} / "
            f"lost {c.get('lost_close', 0)}  (n={c.get('n', 0)})"
        )

    def fill_rows(rows: list[dict[str, Any]]) -> str:
        if not rows:
            return "<tr><td colspan='9' class='empty'>None yet in this window.</td></tr>"
        bits: list[str] = []
        for f in rows:
            result = str(f.get("result") or "")
            pnl = f.get("pnl_usdc")
            pnl_s = "" if pnl is None else f"{pnl:+.0f}"
            bits.append(
                "<tr class='{cls}'>"
                "<td>{when}</td><td>{who}</td><td>{role}</td>"
                "<td class='num'>${stake:,.0f}</td><td class='num'>{dec}</td>"
                "<td>{picked}</td><td>{league} {label}</td>"
                "<td class='res'>{result}</td><td class='num'>{pnl}</td>"
                "</tr>".format(
                    cls=result,
                    when=html.escape(str(f.get("when") or "")),
                    who=html.escape(str(f.get("who") or "")),
                    role=html.escape(str(f.get("role") or "")),
                    stake=float(f.get("stake_usdc") or 0),
                    dec=f.get("decimal") if f.get("decimal") is not None else "",
                    picked=html.escape(str(f.get("picked") or "")),
                    league=html.escape(str(f.get("league") or "")),
                    label=html.escape(str(f.get("label") or "")[:42]),
                    result=html.escape(result),
                    pnl=pnl_s,
                )
            )
        return "\n".join(bits)

    def paper_rows(rows: list[dict[str, Any]]) -> str:
        if not rows:
            return "<tr><td colspan='7' class='empty'>No paper quotes yet. Leave `sxbot run` going.</td></tr>"
        bits: list[str] = []
        for b in rows:
            result = str(b.get("result") or "")
            bits.append(
                "<tr class='{cls}'>"
                "<td>{when}</td><td>{style}</td><td>{verb}</td>"
                "<td>{side}</td>"
                "<td>{league} {label}</td><td class='res'>{result}</td><td>{kick}</td>"
                "</tr>".format(
                    cls=result,
                    when=html.escape(str(b.get("when") or "")),
                    style=html.escape(str(b.get("style") or "")),
                    verb=html.escape(str(b.get("verb") or b.get("action") or "")),
                    side=html.escape(make_take_line(b)),
                    league=html.escape(str(b.get("league") or "")),
                    label=html.escape(str(b.get("label") or "")[:40]),
                    result=html.escape(result),
                    kick=html.escape(str(b.get("kickoff") or "")),
                )
            )
        return "\n".join(bits)

    best_open = snap.get("best_open") or {}
    best_open_s = "none open"
    if best_open:
        best_open_s = (
            f"{make_take_line(best_open)}  "
            f"{best_open.get('league')}  {best_open.get('label')}  "
            f"kickoff {best_open.get('kickoff')}"
        )
    styles = snap.get("by_style") or {}
    style_bits = " · ".join(
        f"{html.escape(k)} {v.get('wins', 0)}–{v.get('losses', 0)}"
        for k, v in styles.items()
    ) or "—"
    not_ev_styles = snap.get("not_ev_by_style") or {}
    not_ev_bits = " · ".join(
        f"{html.escape(k)} {v.get('wins', 0)}–{v.get('losses', 0)}"
        + (f" ROI {v['roi_pct']:+.0f}%" if v.get("roi_pct") is not None else "")
        for k, v in not_ev_styles.items()
        if (v.get("wins") or 0) + (v.get("losses") or 0) > 0 or v.get("pending")
    ) or "—"
    tape_note = ""
    if not snap.get("tape_fills"):
        tape_note = (
            "<p class='note'>5k+ tape scrapes newest-first in a background thread "
            "(V2 /trades is oldest-first inside each window). Paper updates immediately.</p>"
        )
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<meta http-equiv="refresh" content="{int(refresh)}"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>sxbot board</title>
<style>
  :root {{ color-scheme: dark; }}
  body {{ margin: 0; font: 14px/1.4 ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
         background: #0c0f14; color: #e8edf4; }}
  header {{ position: sticky; top: 0; background: #0c0f14ee; border-bottom: 1px solid #243044;
            padding: 12px 18px; backdrop-filter: blur(6px); }}
  h1 {{ font-size: 16px; margin: 0 0 6px; font-weight: 600; }}
  h2 {{ font-size: 13px; margin: 22px 18px 8px; color: #9db0c8; text-transform: uppercase; letter-spacing: .06em; }}
  .meta {{ color: #8b9bb0; font-size: 12px; }}
  .kpis {{ display: flex; flex-wrap: wrap; gap: 10px; padding: 12px 18px 0; }}
  .kpi {{ background: #151b24; border: 1px solid #243044; border-radius: 8px; padding: 10px 12px; min-width: 180px; }}
  .kpi b {{ display: block; color: #9db0c8; font-weight: 500; font-size: 11px; text-transform: uppercase; }}
  table {{ width: calc(100% - 36px); margin: 0 18px 8px; border-collapse: collapse; }}
  th, td {{ text-align: left; padding: 5px 8px; border-bottom: 1px solid #1c2633; vertical-align: top; }}
  th {{ color: #7f93ab; font-size: 11px; text-transform: uppercase; }}
  td.num {{ text-align: right; font-variant-numeric: tabular-nums; }}
  tr.win td.res {{ color: #3dd68c; }}
  tr.lose td.res {{ color: #ff6b6b; }}
  tr.pending td.res, tr.missing td.res {{ color: #d0b45a; }}
  .empty, .note {{ color: #7f93ab; padding: 8px 18px; }}
  .picked {{ color: #e8edf4; }}
</style>
</head>
<body>
<header>
  <h1>sxbot board</h1>
  <div class="meta">open <b>http://127.0.0.1:8765</b> on the <i>same machine</i> as <code>sxbot board</code> · auto-refresh {int(refresh)}s · {html.escape(str(snap.get("generated_at") or ""))} ·
    two bots · <b>taker</b> (<code>sxbot run</code>) rides maker bias on the <i>same</i> team · <b>maker</b> (<code>sxbot mm</code>) stays maker at maker odds (SF @ 2.45 fills when someone bets Cincy @ 1.69) · paper assumes fills</div>
</header>
<div class="kpis">
  <div class="kpi"><b>follow best priced ≤{BEST_PRICED_MAX_DECIMAL:.2f}</b>{html.escape(rec(snap.get("best_priced"), books=True))}</div>
  <div class="kpi"><b>follow maker EV (steam / parked size)</b>{html.escape(rec(snap.get("maker_ev"), books=True))}</div>
  <div class="kpi"><b>follow not EV (no maker lean)</b>{html.escape(rec(snap.get("not_ev"), books=True))}</div>
  <div class="kpi"><b>not EV by style</b>{html.escape(not_ev_bits)}</div>
  <div class="kpi"><b>follow short + EV</b>{html.escape(rec(snap.get("priced_ev"), books=True))}</div>
  <div class="kpi"><b>taker bot unique (`sxbot run`) $5 / Kelly $25</b>{html.escape(rec(snap.get("follow_record"), books=True))}</div>
  <div class="kpi"><b>live unique $1–$4 (new book)</b>{html.escape(rec(snap.get("live_record"), books=True))}</div>
  <div class="kpi"><b>maker bot unique (`sxbot mm`)</b>{html.escape(rec(snap.get("mm_record"), books=True))}</div>
  <div class="kpi"><b>combined unique (gate only — do not mix ROI)</b>{html.escape(rec(snap.get("record"), books=True))}</div>
  <div class="kpi"><b>taker CLV (price vs closing line)</b>{html.escape(clv_s(snap.get("follow_clv")))}</div>
  <div class="kpi"><b>live-test gate ({LIVE_TEST_TRADES} unique settled)</b>{html.escape(gate_s(snap.get("live_test")))}</div>
  <div class="kpi"><b>best priced open</b>{html.escape(best_open_s)}</div>
  <div class="kpi"><b>5k+ today / yesterday</b>{html.escape(rec(snap.get("today_record")))} / {html.escape(rec(snap.get("yesterday_record")))}</div>
  <div class="kpi"><b>by style</b>{style_bits}</div>
</div>
{tape_note}
<h2>Bot paper feed</h2>
<table>
  <thead><tr><th>when</th><th>style</th><th>role</th><th>make / take</th><th>market</th><th>result</th><th>kickoff</th></tr></thead>
  <tbody>{paper_rows(snap.get("feed") or [])}</tbody>
</table>
<h2>Open paper (best price first)</h2>
<table>
  <thead><tr><th>when</th><th>style</th><th>role</th><th>make / take</th><th>market</th><th>result</th><th>kickoff</th></tr></thead>
  <tbody>{paper_rows(snap.get("open") or [])}</tbody>
</table>
<h2>${int(snap.get("big_fill_usdc") or 5000):,}+ today UTC</h2>
<table>
  <thead><tr><th>when</th><th>who</th><th>role</th><th>stake</th><th>dec</th><th>picked</th><th>market</th><th>result</th><th>pnl</th></tr></thead>
  <tbody>{fill_rows(snap.get("today") or [])}</tbody>
</table>
<h2>${int(snap.get("big_fill_usdc") or 5000):,}+ yesterday UTC</h2>
<table>
  <thead><tr><th>when</th><th>who</th><th>role</th><th>stake</th><th>dec</th><th>picked</th><th>market</th><th>result</th><th>pnl</th></tr></thead>
  <tbody>{fill_rows(snap.get("yesterday") or [])}</tbody>
</table>
</body>
</html>
"""


def render_text(snap: dict[str, Any]) -> str:
    def line_fill(f: dict[str, Any]) -> str:
        pnl = f.get("pnl_usdc")
        pnl_s = "" if pnl is None else f" {pnl:+.0f}"
        return (
            f"  {f.get('when')}  ${f.get('stake_usdc'):,.0f} {f.get('role')} {f.get('who')}  "
            f"picked {f.get('picked')} {f.get('decimal')}  {f.get('result')}{pnl_s}  {f.get('label')}"
        )

    def rec(r: dict[str, Any] | None, title: str, *, books: bool = False) -> str:
        if not r:
            return f"{title}: —"
        wp = r.get("win_pct")
        wp_s = f"{wp:.0f}%" if wp is not None else "—"
        roi = r.get("roi_pct")
        roi_s = f" ROI {roi:+.0f}%" if roi is not None else ""
        line = f"{title}: {r.get('wins', 0)}–{r.get('losses', 0)} ({wp_s}{roi_s})"
        kelly = r.get("kelly") if books else None
        if kelly:
            k_roi = kelly.get("roi_pct")
            k_roi_s = f" ROI {k_roi:+.0f}%" if k_roi is not None else ""
            skip = int(kelly.get("skipped") or 0)
            skip_s = f" skip {skip}" if skip else ""
            line += (
                f"  Kelly {kelly.get('wins', 0)}–{kelly.get('losses', 0)}"
                f" ({k_roi_s.strip()}{skip_s})"
            )
        return line

    gate = snap.get("live_test") or {}
    gate_flag = "READY (still paper)" if gate.get("ready") else "not ready"
    clv = snap.get("follow_clv") or {}
    if clv.get("n"):
        avg = clv.get("avg_clv_pct")
        clv_line = (
            f"taker CLV vs close: avg {avg:+.2f} pts  "
            f"beat {clv.get('beat_close', 0)} / lost {clv.get('lost_close', 0)}  (n={clv.get('n', 0)})"
        )
    else:
        clv_line = "taker CLV vs close: no closes stamped yet"
    lines = [
        f"sxbot board  {snap.get('generated_at')}",
        rec(snap.get("best_priced"), f"follow best priced ≤{BEST_PRICED_MAX_DECIMAL:.2f}", books=True),
        rec(snap.get("maker_ev"), "follow maker EV", books=True),
        rec(snap.get("not_ev"), "follow not EV (no maker lean)", books=True),
        rec(snap.get("priced_ev"), "follow short + EV", books=True),
        rec(snap.get("follow_record"), "taker bot unique $5 / Kelly $25 (sxbot run)", books=True),
        rec(snap.get("live_record"), "live unique $1-$4 (new book)", books=True),
        rec(snap.get("mm_record"), "maker bot unique (sxbot mm)", books=True),
        rec(snap.get("record"), "combined unique (gate only)", books=True),
        clv_line,
        (
            f"live-test gate: {gate.get('settled', 0)}/{gate.get('target', LIVE_TEST_TRADES)} unique settled  "
            f"follow {gate.get('follow_settled', 0)} · MM {gate.get('mm_settled', 0)}  · {gate_flag}"
        ),
    ]
    best = snap.get("best_open")
    if best:
        lines.append(
            f"best priced open: {make_take_line(best)}  "
            f"{best.get('label')}  {best.get('kickoff')}"
        )
    else:
        lines.append("best priced open: none")
    for style, rec_s in (snap.get("not_ev_by_style") or {}).items():
        if (rec_s.get("wins") or 0) + (rec_s.get("losses") or 0) == 0 and not rec_s.get("pending"):
            continue
        roi = rec_s.get("roi_pct")
        roi_s = f" ROI {roi:+.0f}%" if roi is not None else ""
        lines.append(
            f"not EV {style}: {rec_s.get('wins', 0)}–{rec_s.get('losses', 0)}{roi_s}"
        )
    lines.append("")
    lines.append(f"5k+ today ({len(snap.get('today') or [])})")
    lines.extend(line_fill(f) for f in (snap.get("today") or [])[:15])
    lines.append("")
    lines.append(f"5k+ yesterday ({len(snap.get('yesterday') or [])})")
    lines.extend(line_fill(f) for f in (snap.get("yesterday") or [])[:15])
    lines.append("")
    lines.append("paper feed")
    for b in (snap.get("feed") or [])[:12]:
        lines.append(
            f"  {b.get('when')}  {b.get('style')} {b.get('verb') or b.get('action')}  "
            f"{make_take_line(b)}  {b.get('result')}  {b.get('label')}"
        )
    return "\n".join(lines)


def send_telegram(token: str, chat_id: str, text: str) -> None:
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {"chat_id": chat_id, "text": text[:3900], "disable_web_page_preview": True}
    with httpx.Client(timeout=15.0) as http:
        response = http.post(url, json=payload)
        if response.status_code >= 400:
            raise RuntimeError(f"telegram HTTP {response.status_code}: {response.text[:200]}")


class BoardState:
    def __init__(self, client: SxClient, settings: Settings) -> None:
        self.client = client
        self.settings = settings
        self.lock = threading.Lock()
        self.snap: dict[str, Any] = {}
        self.tape: list[dict[str, Any]] = []
        self.tape_at = 0.0
        self.seen_fills: set[str] = set()
        self.paper_n = 0
        self.primed = False
        self._stop = threading.Event()

    def refresh_tape(self, *, force: bool = False) -> None:
        if not force and time.time() - self.tape_at < 75:
            return
        yesterday, _, current = _day_bounds()
        start, end = int(yesterday.timestamp()), int(current.timestamp())
        min_usdc = float(self.settings.big_fill_usdc or 5000)
        rows = scrape_big_fills(
            self.client,
            start=start,
            end=end,
            min_usdc=min_usdc,
            newest_first=True,
            deadline=time.time() + 35,
            max_pages=15,
        )
        with self.lock:
            seen = {str(r.get("fillHash") or "") for r in self.tape}
            merged = list(self.tape)
            for raw in rows:
                fh = str(raw.get("fillHash") or "")
                if fh and fh not in seen:
                    seen.add(fh)
                    merged.append(raw)
            merged = [r for r in merged if int(r.get("betTime") or 0) >= start]
            self.tape = merged
            self.tape_at = time.time()

    def _tape_loop(self) -> None:
        while not self._stop.is_set():
            try:
                self.refresh_tape(force=True)
            except Exception:
                log.exception("tape scrape failed")
            self._stop.wait(90)

    def start_tape_worker(self) -> threading.Thread:
        worker = threading.Thread(target=self._tape_loop, name="sxbot-tape", daemon=True)
        worker.start()
        return worker

    def snapshot(self, *, refresh_tape: bool = False) -> dict[str, Any]:
        if refresh_tape:
            try:
                self.refresh_tape()
            except Exception:
                log.exception("tape refresh failed")
        with self.lock:
            tape = list(self.tape)
        snap = build_snapshot(self.client, self.settings, tape_rows=tape)
        with self.lock:
            self.snap = snap
        return snap

    def maybe_telegram(self, snap: dict[str, Any]) -> None:
        token = self.settings.telegram_token
        chat = self.settings.telegram_chat_id
        if not token or not chat:
            return
        alerts: list[str] = []
        first = not self.primed
        for fill in (snap.get("today") or []) + (snap.get("yesterday") or []):
            key = f"{fill.get('market')}|{fill.get('ts')}|{fill.get('who')}|{fill.get('stake_usdc')}"
            if key in self.seen_fills:
                continue
            self.seen_fills.add(key)
            if not first:
                alerts.append(
                    f"${fill.get('stake_usdc'):,.0f} {fill.get('role')} {fill.get('who')}  "
                    f"picked {fill.get('picked')} {fill.get('decimal')}  {fill.get('result')}  {fill.get('label')}"
                )
        paper = load_all_paper(self.settings.paper_log)
        if first:
            self.paper_n = len(paper)
            self.primed = True
            return
        if len(paper) > self.paper_n:
            new = paper[self.paper_n :]
            self.paper_n = len(paper)
            for row in new:
                if str(row.get("action") or "") not in _TRADE_ACTIONS:
                    continue
                alerts.append(
                    f"PAPER {row.get('style') or ''} {row.get('action')}  "
                    f"{row.get('side')} {row.get('label')} @ {row.get('odds_pct')}%"
                )
        if not alerts:
            return
        body = "sxbot\n" + "\n".join(alerts[:12])
        try:
            send_telegram(token, chat, body)
        except Exception:
            log.exception("telegram send failed")

    def loop(self) -> None:
        while not self._stop.is_set():
            try:
                snap = self.snapshot(refresh_tape=False)
                self.maybe_telegram(snap)
            except Exception:
                log.exception("board refresh failed")
            self._stop.wait(max(self.settings.board_refresh_seconds, 10))


def serve_board(client: SxClient, settings: Settings, *, host: str, port: int) -> None:
    state = BoardState(client, settings)
    state.start_tape_worker()
    worker = threading.Thread(target=state.loop, name="sxbot-board", daemon=True)
    worker.start()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt: str, *args: object) -> None:
            log.debug("%s - " + fmt, self.address_string(), *args)

        def _send(self, code: int, body: bytes, content_type: str) -> None:
            self.send_response(code)
            self.send_header("Content-Type", content_type)
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:  # noqa: N802
            path = urlparse(self.path).path
            if path in {"/", "/index.html"}:
                with state.lock:
                    snap = dict(state.snap) if state.snap else None
                if snap is None:
                    snap = state.snapshot(refresh_tape=False)
                page = render_html(snap, refresh=settings.board_refresh_seconds)
                self._send(200, page.encode("utf-8"), "text/html; charset=utf-8")
                return
            if path in {"/api.json", "/api"}:
                with state.lock:
                    snap = dict(state.snap) if state.snap else {}
                self._send(200, json.dumps(snap, default=str).encode("utf-8"), "application/json")
                return
            if path == "/health":
                self._send(200, b"ok\n", "text/plain; charset=utf-8")
                return
            self._send(404, b"not found\n", "text/plain; charset=utf-8")

    httpd = ThreadingHTTPServer((host, port), Handler)
    httpd.allow_reuse_address = True
    log.info("board http://%s:%s  (refresh %ss)", host, port, settings.board_refresh_seconds)
    if settings.telegram_token and settings.telegram_chat_id:
        log.info("telegram alerts on")
        try:
            send_telegram(
                settings.telegram_token,
                settings.telegram_chat_id,
                f"sxbot board up  http://{host}:{port}",
            )
        except Exception:
            log.exception("telegram hello failed")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        log.info("board stopping")
    finally:
        state._stop.set()
        httpd.server_close()
