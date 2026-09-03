"""Your tickets, kept off the bot unique card.

`sxbot bet add` appends one row to `sxbot-manual.jsonl`. Each add is a
ticket — we do not collapse two Krejcikova bets into one unique side.
`sxbot bet settle` writes a settle line when SX cannot grade the market
(off-book, unmatched name). SX-matched tickets grade themselves.
"""

from __future__ import annotations

import json
import time
import uuid
from pathlib import Path
from typing import Any

from sxbot.api import SxClient
from sxbot.config import Settings
from sxbot.journal import load_all_paper, load_jsonl
from sxbot.units import from_percent, to_base_units, to_percent


STYLE_MANUAL = "manual"
ACTION_MANUAL = "manual"
ACTION_SETTLE = "settle"


def manual_log_for(path: str | Path) -> Path:
    return Path(path)


def load_manual(path: str | Path) -> list[dict[str, Any]]:
    """Tickets only. Later settle lines stamp `settled_result` onto the ticket."""
    rows = load_jsonl(path)
    settles: dict[str, str] = {}
    tickets: list[dict[str, Any]] = []
    for row in rows:
        action = str(row.get("action") or "")
        if action == ACTION_SETTLE:
            tid = str(row.get("ticket_id") or "")
            result = str(row.get("result") or "").lower()
            if tid and result in {"win", "lose", "void"}:
                settles[tid] = result
            continue
        if action != ACTION_MANUAL:
            continue
        tickets.append(dict(row))
    for ticket in tickets:
        tid = str(ticket.get("ticket_id") or "")
        if tid in settles and not ticket.get("settled_result"):
            ticket["settled_result"] = settles[tid]
    tickets.sort(key=lambda row: float(row.get("ts") or 0))
    return tickets


def append_jsonl(path: str | Path, row: dict[str, Any]) -> None:
    file = Path(path)
    file.parent.mkdir(parents=True, exist_ok=True)
    with file.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, separators=(",", ":")) + "\n")


def norm_name(value: str) -> str:
    return "".join(ch for ch in (value or "").lower() if ch.isalnum() or ch.isspace()).strip()


def name_hit(needle: str, haystack: str) -> bool:
    n, h = norm_name(needle), norm_name(haystack)
    if not n or not h:
        return False
    if n == h or n in h:
        return True
    # "Barbora Krejcikova" vs market last name only. Skip 1–3 letter
    # fragments so "Nobody" does not match "B".
    if len(h) >= 4 and h in n:
        return True
    last = n.split()[-1]
    return len(last) >= 4 and last in h.split()


def side_for_picked(picked: str, outcome_one: str, outcome_two: str) -> str | None:
    hit_one = name_hit(picked, outcome_one)
    hit_two = name_hit(picked, outcome_two)
    if hit_one and hit_two:
        # Prefer the tighter last-name match.
        n = norm_name(picked)
        if n == norm_name(outcome_one):
            return "outcome_one"
        if n == norm_name(outcome_two):
            return "outcome_two"
        return None
    if hit_one:
        return "outcome_one"
    if hit_two:
        return "outcome_two"
    return None


def parse_odds(raw: str) -> tuple[int, float, float]:
    """American (+150 / -110), decimal (1.78), or implied percent (56.25)."""
    text = (raw or "").strip().replace(",", "")
    if not text:
        raise ValueError("odds are required")
    if text[0] in "+-" and text[1:].replace(".", "", 1).isdigit():
        american = int(float(text))
        if american >= 100:
            decimal = 1.0 + american / 100.0
        elif american <= -100:
            decimal = 1.0 + 100.0 / abs(american)
        else:
            raise ValueError(f"american odds must be <= -100 or >= +100, got {text}")
        pct = 100.0 / decimal
        odds = from_percent(pct)
        return odds, to_percent(odds), round(decimal, 3)
    if not text.replace(".", "", 1).isdigit():
        raise ValueError(f"could not parse odds {raw!r}")
    value = float(text)
    if value > 1.0 and value < 20.0:
        decimal = value
        pct = 100.0 / decimal
    elif 20.0 <= value < 100.0:
        pct = value
        decimal = 100.0 / pct
    else:
        raise ValueError(f"odds {raw!r} is not decimal (1.01–19.99) or percent (20–99)")
    odds = from_percent(pct)
    return odds, to_percent(odds), round(decimal, 3)


def _market_names(market: Any) -> tuple[str, str, str, str, str]:
    if isinstance(market, dict):
        o1 = str(market.get("outcomeOneName") or market.get("outcome_one") or "")
        o2 = str(market.get("outcomeTwoName") or market.get("outcome_two") or "")
        league = str(market.get("leagueLabel") or market.get("league") or "")
        label = f"{o1} / {o2}".strip(" /")
        mh = str(market.get("marketHash") or market.get("market") or "")
        return mh, o1, o2, label, league
    return (
        str(getattr(market, "market_hash", "") or ""),
        str(getattr(market, "outcome_one", "") or ""),
        str(getattr(market, "outcome_two", "") or ""),
        f"{getattr(market, 'outcome_one', '')} / {getattr(market, 'outcome_two', '')}".strip(" /"),
        str(getattr(market, "league_label", "") or ""),
    )


def match_from_rows(
    rows: list[dict[str, Any]],
    *,
    picked: str,
    vs: str = "",
    market_hash: str = "",
) -> dict[str, Any] | None:
    if market_hash:
        want = market_hash.lower()
        for row in reversed(rows):
            if str(row.get("market") or "").lower() == want:
                return row
        return None
    hits: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in reversed(rows):
        mh = str(row.get("market") or "")
        if not mh or mh in seen:
            continue
        o1 = str(row.get("outcome_one") or "")
        o2 = str(row.get("outcome_two") or "")
        label = str(row.get("label") or "")
        if not o1 and " / " in label:
            parts = [p.strip() for p in label.split(" / ")]
            o1 = parts[0] if parts else ""
            o2 = parts[1] if len(parts) > 1 else ""
        side = side_for_picked(picked, o1, o2)
        if side is None:
            continue
        if vs and not (name_hit(vs, o1) or name_hit(vs, o2) or name_hit(vs, label)):
            continue
        seen.add(mh)
        hits.append(row)
    if len(hits) == 1:
        return hits[0]
    return None


def match_from_active(
    client: SxClient,
    settings: Settings,
    *,
    picked: str,
    vs: str = "",
    market_hash: str = "",
) -> Any | None:
    if market_hash:
        found = client.find_markets([market_hash])
        return found[0] if found else None
    hits = []
    for market in client.active_markets(
        only_main_line=settings.only_main_line,
        sport_ids=settings.sport_ids or (),
        types=settings.market_types or (),
        page_size=50,
        limit=500,
    ):
        side = side_for_picked(picked, market.outcome_one, market.outcome_two)
        if side is None:
            continue
        if vs and not (
            name_hit(vs, market.outcome_one)
            or name_hit(vs, market.outcome_two)
            or name_hit(vs, market.team_one)
            or name_hit(vs, market.team_two)
        ):
            continue
        hits.append(market)
        if len(hits) > 8:
            break
    if len(hits) == 1:
        return hits[0]
    return None


def resolve_ticket_market(
    client: SxClient | None,
    settings: Settings,
    *,
    picked: str,
    vs: str = "",
    market_hash: str = "",
) -> dict[str, Any]:
    """Best-effort SX market. Unmatched tickets still log and can be settled by hand."""
    paper = load_all_paper(settings.paper_log)
    row = match_from_rows(paper, picked=picked, vs=vs, market_hash=market_hash)
    if row:
        mh, o1, o2, label, league = _market_names(row)
        side = side_for_picked(picked, o1, o2) or str(row.get("side") or "")
        return {
            "market": mh or str(row.get("market") or ""),
            "side": side,
            "outcome_one": o1,
            "outcome_two": o2,
            "label": label or str(row.get("label") or ""),
            "league": league or str(row.get("league") or ""),
            "game_time": int(row.get("game_time") or 0),
            "event_id": str(row.get("event_id") or ""),
        }
    if client is not None:
        market = match_from_active(
            client, settings, picked=picked, vs=vs, market_hash=market_hash
        )
        if market is not None:
            mh, o1, o2, label, league = _market_names(market)
            side = side_for_picked(picked, o1, o2) or ""
            game_time = int(
                market.get("gameTime") if isinstance(market, dict) else getattr(market, "game_time", 0) or 0
            )
            event_id = str(
                market.get("sportXeventId")
                if isinstance(market, dict)
                else getattr(market, "event_id", "") or ""
            )
            return {
                "market": mh,
                "side": side,
                "outcome_one": o1,
                "outcome_two": o2,
                "label": label,
                "league": league,
                "game_time": game_time,
                "event_id": event_id,
            }
    side = "outcome_one"
    if market_hash:
        side = ""
    return {
        "market": market_hash,
        "side": side,
        "outcome_one": picked if not vs else picked,
        "outcome_two": vs,
        "label": f"{picked} / {vs}".strip(" /"),
        "league": "",
        "game_time": 0,
        "event_id": "",
    }


def build_ticket(
    *,
    picked: str,
    odds_raw: str,
    stake_usdc: float,
    vs: str = "",
    market_hash: str = "",
    side: str = "",
    book: str = "sx",
    note: str = "",
    league: str = "",
    settled_result: str = "",
    resolved: dict[str, Any] | None = None,
    now: float | None = None,
) -> dict[str, Any]:
    if stake_usdc <= 0:
        raise ValueError("stake must be > 0")
    odds, odds_pct, decimal = parse_odds(odds_raw)
    resolved = resolved or {}
    use_side = side or str(resolved.get("side") or "")
    if use_side in {"1", "one", "o1"}:
        use_side = "outcome_one"
    if use_side in {"2", "two", "o2"}:
        use_side = "outcome_two"
    if use_side not in {"outcome_one", "outcome_two"}:
        o1 = str(resolved.get("outcome_one") or picked)
        o2 = str(resolved.get("outcome_two") or vs)
        use_side = side_for_picked(picked, o1, o2) or "outcome_one"
    ts = float(now if now is not None else time.time())
    o1 = str(resolved.get("outcome_one") or (picked if use_side == "outcome_one" else vs))
    o2 = str(resolved.get("outcome_two") or (picked if use_side == "outcome_two" else vs))
    label = str(resolved.get("label") or f"{o1} / {o2}".strip(" /"))
    ticket: dict[str, Any] = {
        "ts": ts,
        "ticket_id": uuid.uuid4().hex[:12],
        "source": "manual",
        "booker": "you",
        "action": ACTION_MANUAL,
        "style": STYLE_MANUAL,
        "side": use_side,
        "market": str(resolved.get("market") or market_hash or ""),
        "label": label,
        "league": league or str(resolved.get("league") or ""),
        "picked": picked,
        "outcome_one": o1,
        "outcome_two": o2,
        "odds": str(odds),
        "odds_pct": odds_pct,
        "decimal": decimal,
        "stake": str(to_base_units(stake_usdc)),
        "stake_usdc": float(stake_usdc),
        "book": (book or "sx").strip().lower() or "sx",
        "note": note,
        "game_time": int(resolved.get("game_time") or 0),
        "event_id": str(resolved.get("event_id") or ""),
        "motive": "manual",
    }
    if settled_result in {"win", "lose", "void"}:
        ticket["settled_result"] = settled_result
    return ticket


def stamp_settled_view(view: dict[str, Any], row: dict[str, Any]) -> dict[str, Any]:
    """If SX has not graded yet, honor a hand-settled result on the ticket."""
    stamped = str(row.get("settled_result") or "").lower()
    if stamped not in {"win", "lose", "void"}:
        return view
    if view.get("result") in {"win", "lose", "void"}:
        return view
    stake = float(view.get("stake_usdc") or row.get("stake_usdc") or 0)
    dec = view.get("decimal") if view.get("decimal") is not None else row.get("decimal")
    view["result"] = stamped
    if stamped == "void":
        view["pnl_usdc"] = 0.0
    elif stamped == "lose":
        view["pnl_usdc"] = round(-stake, 2)
    elif stamped == "win":
        try:
            view["pnl_usdc"] = round(stake * (float(dec) - 1.0), 2) if dec else None
        except (TypeError, ValueError):
            view["pnl_usdc"] = None
    return view


def find_ticket(tickets: list[dict[str, Any]], *, ticket_id: str = "", picked: str = "") -> dict[str, Any]:
    if ticket_id:
        for row in reversed(tickets):
            if str(row.get("ticket_id") or "") == ticket_id:
                return row
        raise ValueError(f"no ticket {ticket_id}")
    if picked:
        hits = [row for row in tickets if name_hit(picked, str(row.get("picked") or ""))]
        open_hits = [row for row in hits if not row.get("settled_result")]
        use = open_hits or hits
        if len(use) == 1:
            return use[0]
        if not use:
            raise ValueError(f"no ticket matching {picked!r}")
        ids = ", ".join(str(r.get("ticket_id")) for r in use[-5:])
        raise ValueError(f"several tickets match {picked!r} — pass --id ({ids})")
    raise ValueError("pass --id or --picked")


def settle_ticket(path: str | Path, ticket: dict[str, Any], result: str) -> dict[str, Any]:
    result = result.lower().strip()
    if result not in {"win", "lose", "void"}:
        raise ValueError("result must be win, lose, or void")
    row = {
        "ts": time.time(),
        "action": ACTION_SETTLE,
        "ticket_id": ticket.get("ticket_id"),
        "result": result,
        "picked": ticket.get("picked"),
        "label": ticket.get("label"),
    }
    append_jsonl(path, row)
    return row
