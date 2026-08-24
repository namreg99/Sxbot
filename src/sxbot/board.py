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
from typing import Any
from urllib.parse import urlparse

import httpx

from sxbot.api import SxClient, index_markets, lookup_market
from sxbot.config import Settings
from sxbot.fingerprint import trade_pnl_usdc
from sxbot.grade import grade_paper, grade_row
from sxbot.journal import load_all_paper
from sxbot.units import decimal_odds, to_percent, to_prob
from sxbot.wallets import CANDIDATE_WALLETS, KNOWN_WALLETS, labeled_addresses

log = logging.getLogger("sxbot.board")

_TRADE_ACTIONS = {"join_maker", "take_stale", "take_flow", "mm_fill"}
BEST_PRICED_MAX_DECIMAL = 1.80


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


def _windows(start: int, end: int, step: int) -> list[tuple[int, int]]:
    out: list[tuple[int, int]] = []
    cur = start
    while cur < end:
        nxt = min(cur + step, end)
        out.append((cur, nxt))
        cur = nxt
    return out


def unique_open_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Last non-cancel join/take per market+side."""
    last: dict[tuple[str, str], dict[str, Any]] = {}
    for row in rows:
        market = str(row.get("market") or "")
        side = str(row.get("side") or "")
        if not market or not side:
            continue
        key = (market, side)
        action = str(row.get("action") or "")
        if action == "cancel":
            last.pop(key, None)
            continue
        if action in _TRADE_ACTIONS:
            last[key] = row
    return list(last.values())


def scrape_big_fills(
    client: SxClient,
    *,
    start: int,
    end: int,
    min_usdc: float,
    step: int = 3 * 3600,
    max_pages: int = 50,
) -> list[dict[str, Any]]:
    """Pull V2 public fills ≥ min_usdc in [start, end). Oldest-first windows."""
    now = int(time.time())
    end = min(int(end), now)
    start = int(start)
    if end <= start:
        return []
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for w0, w1 in _windows(start, end, step):
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


def _paper_view(bet: Any, row: dict[str, Any]) -> dict[str, Any]:
    odds = int(row.get("odds") or 0)
    dec = decimal_odds(to_prob(odds)) if odds else 0.0
    return {
        "ts": float(row.get("ts") or 0),
        "when": _utc(row.get("ts")),
        "style": str(row.get("style") or "") or "legacy",
        "action": bet.action,
        "picked": bet.picked or bet.side,
        "label": bet.label,
        "league": bet.league,
        "decimal": round(dec, 2) if dec else None,
        "odds_pct": bet.odds_pct,
        "result": bet.result,
        "pnl_usdc": bet.pnl_usdc,
        "kickoff": _utc(bet.game_time),
        "market": str(row.get("market") or ""),
        "side": bet.side,
        "motive": bet.motive,
    }


def _record(views: list[dict[str, Any]]) -> dict[str, Any]:
    settled = [v for v in views if v.get("result") in {"win", "lose"}]
    wins = sum(1 for v in settled if v["result"] == "win")
    losses = sum(1 for v in settled if v["result"] == "lose")
    n = wins + losses
    return {
        "n": len(views),
        "wins": wins,
        "losses": losses,
        "pending": sum(1 for v in views if v.get("result") == "pending"),
        "win_pct": round(100.0 * wins / n, 1) if n else None,
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
        tape_rows = scrape_big_fills(client, start=y0, end=now_ts, min_usdc=min_usdc)

    hashes = list(
        dict.fromkeys(str(r.get("marketHash") or "") for r in tape_rows if r.get("marketHash"))
    )
    paper_rows = load_all_paper(settings.paper_log)
    paper_hashes = list(
        dict.fromkeys(str(r.get("market") or "") for r in paper_rows if r.get("market"))
    )
    found = client.find_markets(hashes + [h for h in paper_hashes if h not in hashes]) if (
        hashes or paper_hashes
    ) else []
    markets = index_markets(found)

    fills = [_fill_view(raw, lookup_market(markets, str(raw.get("marketHash") or "")), who) for raw in tape_rows]
    fills.sort(key=lambda r: r["ts"], reverse=True)
    today_fills = [f for f in fills if f["ts"] >= t0]
    yday_fills = [f for f in fills if y0 <= f["ts"] < t0]

    unique_rows = unique_open_rows(paper_rows)
    unique_bets = [
        _paper_view(grade_row(row, lookup_market(markets, str(row.get("market") or ""))), row)
        for row in unique_rows
    ]
    feed_src = [r for r in paper_rows if str(r.get("action") or "") in _TRADE_ACTIONS | {"cancel"}]
    feed_src = feed_src[-40:]
    feed = [
        _paper_view(grade_row(row, lookup_market(markets, str(row.get("market") or ""))), row)
        for row in reversed(feed_src)
    ]
    open_bets = [b for b in unique_bets if b["result"] in {"pending", "missing"}]
    open_bets.sort(key=lambda b: float(b.get("odds_pct") or 0), reverse=True)
    best_open = open_bets[0] if open_bets else None
    settled = [b for b in unique_bets if b["result"] in {"win", "lose"}]
    best_priced = [
        b for b in settled if b.get("decimal") and float(b["decimal"]) <= BEST_PRICED_MAX_DECIMAL
    ]
    by_style: dict[str, list[dict[str, Any]]] = {}
    for bet in unique_bets:
        by_style.setdefault(str(bet.get("style") or "legacy"), []).append(bet)

    return {
        "generated_at": _utc(now_ts),
        "generated_ts": now_ts,
        "big_fill_usdc": min_usdc,
        "tape_fills": len(tape_rows),
        "today": today_fills,
        "yesterday": yday_fills,
        "feed": feed,
        "open": open_bets,
        "best_open": best_open,
        "record": _record(unique_bets),
        "best_priced": {
            **_record(best_priced),
            "max_decimal": BEST_PRICED_MAX_DECIMAL,
        },
        "by_style": {k: _record(v) for k, v in sorted(by_style.items())},
        "today_record": _record(today_fills),
        "yesterday_record": _record(yday_fills),
    }


def render_html(snap: dict[str, Any], *, refresh: int = 20) -> str:
    def rec(r: dict[str, Any] | None) -> str:
        if not r:
            return "—"
        wp = r.get("win_pct")
        wp_s = f"{wp:.0f}%" if wp is not None else "—"
        return f"{r.get('wins', 0)}–{r.get('losses', 0)}  {wp_s}   pending {r.get('pending', 0)}"

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
            return "<tr><td colspan='8' class='empty'>No paper quotes yet. Leave `sxbot run` going.</td></tr>"
        bits: list[str] = []
        for b in rows:
            result = str(b.get("result") or "")
            bits.append(
                "<tr class='{cls}'>"
                "<td>{when}</td><td>{style}</td><td>{action}</td>"
                "<td>{picked}</td><td class='num'>{dec}</td>"
                "<td>{league} {label}</td><td class='res'>{result}</td><td>{kick}</td>"
                "</tr>".format(
                    cls=result,
                    when=html.escape(str(b.get("when") or "")),
                    style=html.escape(str(b.get("style") or "")),
                    action=html.escape(str(b.get("action") or "")),
                    picked=html.escape(str(b.get("picked") or "")),
                    dec=b.get("decimal") if b.get("decimal") is not None else "",
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
            f"{best_open.get('picked')}  {best_open.get('decimal')}  "
            f"{best_open.get('league')}  {best_open.get('label')}  "
            f"kickoff {best_open.get('kickoff')}"
        )
    styles = snap.get("by_style") or {}
    style_bits = " · ".join(
        f"{html.escape(k)} {v.get('wins', 0)}–{v.get('losses', 0)}"
        for k, v in styles.items()
    ) or "—"
    tape_note = ""
    if not snap.get("tape_fills"):
        tape_note = (
            "<p class='note'>5k+ tape is still scraping (V2 /trades is oldest-first; "
            "first load can take a minute). Paper updates immediately.</p>"
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
    names the side that was bet, not the first team in the label · paper assumes fills</div>
</header>
<div class="kpis">
  <div class="kpi"><b>best priced settled ≤{BEST_PRICED_MAX_DECIMAL:.2f}</b>{html.escape(rec(snap.get("best_priced")))}</div>
  <div class="kpi"><b>all unique paper</b>{html.escape(rec(snap.get("record")))}</div>
  <div class="kpi"><b>best priced open</b>{html.escape(best_open_s)}</div>
  <div class="kpi"><b>5k+ today / yesterday</b>{html.escape(rec(snap.get("today_record")))} / {html.escape(rec(snap.get("yesterday_record")))}</div>
  <div class="kpi"><b>by style</b>{style_bits}</div>
</div>
{tape_note}
<h2>Bot paper feed</h2>
<table>
  <thead><tr><th>when</th><th>style</th><th>action</th><th>picked</th><th>dec</th><th>market</th><th>result</th><th>kickoff</th></tr></thead>
  <tbody>{paper_rows(snap.get("feed") or [])}</tbody>
</table>
<h2>Open paper (best price first)</h2>
<table>
  <thead><tr><th>when</th><th>style</th><th>action</th><th>picked</th><th>dec</th><th>market</th><th>result</th><th>kickoff</th></tr></thead>
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

    def rec(r: dict[str, Any] | None, title: str) -> str:
        if not r:
            return f"{title}: —"
        wp = r.get("win_pct")
        wp_s = f"{wp:.0f}%" if wp is not None else "—"
        return f"{title}: {r.get('wins', 0)}–{r.get('losses', 0)} ({wp_s})"

    lines = [
        f"sxbot board  {snap.get('generated_at')}",
        rec(snap.get("best_priced"), f"best priced settled ≤{BEST_PRICED_MAX_DECIMAL:.2f}"),
        rec(snap.get("record"), "all unique paper"),
    ]
    best = snap.get("best_open")
    if best:
        lines.append(
            f"best priced open: picked {best.get('picked')} {best.get('decimal')}  "
            f"{best.get('label')}  {best.get('kickoff')}"
        )
    else:
        lines.append("best priced open: none")
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
            f"  {b.get('when')}  {b.get('style')} {b.get('action')}  "
            f"picked {b.get('picked')} {b.get('decimal')}  {b.get('result')}  {b.get('label')}"
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
        rows = scrape_big_fills(self.client, start=start, end=end, min_usdc=min_usdc)
        with self.lock:
            self.tape = rows
            self.tape_at = time.time()

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
                snap = self.snapshot(refresh_tape=True)
                self.maybe_telegram(snap)
            except Exception:
                log.exception("board refresh failed")
            self._stop.wait(max(self.settings.board_refresh_seconds, 10))


def serve_board(client: SxClient, settings: Settings, *, host: str, port: int) -> None:
    state = BoardState(client, settings)
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
