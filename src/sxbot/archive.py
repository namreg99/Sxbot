"""Pull V2 maker/taker history into SQLite and turn it into style profiles.

SX does not keep old order books, so this is not a book backtest. It *is* a
fill-level sample of how labeled wallets actually bet: sport, live vs pregame,
odds bucket, scale-in, and net P&L vs the gross "Won" leaderboard.

Default windows hit winter US majors (Dec/Jan) and summer soccer/baseball/tennis
instead of paginating one long range from the start (Gary prints ~800 taker
fills a day, so a Dec 1 start never reaches January).
"""

from __future__ import annotations

import sqlite3
import time
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from sxbot.api import SxClient
from sxbot.fingerprint import role_from_counts, suggested_style, trade_pnl_usdc
from sxbot.units import decimal_odds, to_prob, to_usdc
from sxbot.wallets import WATCH_SPORT_IDS, labeled_targets, WALLET_NOTES

SCHEMA = """
CREATE TABLE IF NOT EXISTS wallets (
    address TEXT PRIMARY KEY,
    label TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS fills (
    fill_hash TEXT NOT NULL,
    wallet TEXT NOT NULL,
    is_maker INTEGER NOT NULL,
    market_hash TEXT,
    event_id TEXT,
    betting_outcome_one INTEGER,
    stake INTEGER,
    odds TEXT,
    bet_time INTEGER,
    settled INTEGER,
    outcome INTEGER,
    settle_net_return REAL,
    pnl_usdc REAL,
    PRIMARY KEY (fill_hash, wallet, is_maker)
);

CREATE TABLE IF NOT EXISTS markets (
    market_hash TEXT PRIMARY KEY,
    sport_id INTEGER,
    sport_label TEXT,
    league_id INTEGER,
    league_label TEXT,
    event_id TEXT,
    team_one TEXT,
    team_two TEXT,
    outcome_one TEXT,
    outcome_two TEXT,
    game_time INTEGER,
    type INTEGER,
    line REAL,
    main_line INTEGER,
    status TEXT,
    reported_outcome INTEGER
);

CREATE TABLE IF NOT EXISTS ingest_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at INTEGER,
    window_start INTEGER,
    window_end INTEGER,
    pages INTEGER,
    fills_upserted INTEGER
);

CREATE INDEX IF NOT EXISTS idx_fills_wallet_time ON fills(wallet, bet_time);
CREATE INDEX IF NOT EXISTS idx_fills_market ON fills(market_hash);
"""

# Highest-volume slices, not every tiny game. Oldest-first pagination means
# each window must start near the period you actually want.
SAMPLE_WINDOWS: tuple[tuple[str, str | None, str], ...] = (
    ("2025-12-01", "2025-12-20", "Dec NFL / NBA / NHL / winter soccer"),
    ("2026-01-10", "2026-02-10", "Jan NFL playoffs / NBA / NHL"),
    ("2026-06-01", "2026-06-25", "Jun soccer / MLB / tennis"),
    ("2026-07-20", None, "Jul–Aug MLB / tennis / MLS / Liga MX"),
)

ODDS_BUCKETS: tuple[tuple[str, float, float], ...] = (
    ("hammer ≤1.12", 1.0, 1.12),
    ("short 1.12–1.40", 1.12, 1.40),
    ("fav 1.40–1.80", 1.40, 1.80),
    ("pick 1.80–2.20", 1.80, 2.20),
    ("dog 2.20–3.50", 2.20, 3.50),
    ("longshot >3.50", 3.50, 99.0),
)

WATCH_SPORT_SET = set(WATCH_SPORT_IDS)


def _utc(ts: int | None) -> str:
    if not ts:
        return "-"
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d")


def parse_day(value: str) -> int:
    return int(datetime.strptime(value, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp())


def default_windows() -> list[tuple[int, int | None, str]]:
    out: list[tuple[int, int | None, str]] = []
    for start, end, label in SAMPLE_WINDOWS:
        out.append((parse_day(start), parse_day(end) if end else None, label))
    return out


def odds_bucket(decimal: float) -> str:
    for name, lo, hi in ODDS_BUCKETS:
        if lo <= decimal < hi:
            return name
    return ODDS_BUCKETS[-1][0]


class HistoryStore:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(self.path)
        self._db.row_factory = sqlite3.Row
        self._db.executescript(SCHEMA)
        self._db.commit()

    def close(self) -> None:
        self._db.close()

    def __enter__(self) -> HistoryStore:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def upsert_wallet(self, address: str, label: str) -> None:
        self._db.execute(
            "INSERT INTO wallets(address, label) VALUES(?, ?) "
            "ON CONFLICT(address) DO UPDATE SET label=excluded.label",
            (address.lower(), label),
        )
        self._db.commit()

    def upsert_fills(self, rows: Iterable[dict[str, Any]]) -> int:
        payload = [row for row in rows if row.get("fill_hash")]
        if not payload:
            return 0
        self._db.executemany(
            """
            INSERT INTO fills(
                fill_hash, wallet, is_maker, market_hash, event_id,
                betting_outcome_one, stake, odds, bet_time, settled,
                outcome, settle_net_return, pnl_usdc
            ) VALUES (
                :fill_hash, :wallet, :is_maker, :market_hash, :event_id,
                :betting_outcome_one, :stake, :odds, :bet_time, :settled,
                :outcome, :settle_net_return, :pnl_usdc
            )
            ON CONFLICT(fill_hash, wallet, is_maker) DO UPDATE SET
                settled=excluded.settled,
                outcome=excluded.outcome,
                settle_net_return=excluded.settle_net_return,
                pnl_usdc=excluded.pnl_usdc
            """,
            payload,
        )
        self._db.commit()
        return len(payload)

    def upsert_markets(self, rows: Iterable[dict[str, Any]]) -> int:
        payload = [row for row in rows if row.get("market_hash")]
        if not payload:
            return 0
        self._db.executemany(
            """
            INSERT INTO markets(
                market_hash, sport_id, sport_label, league_id, league_label,
                event_id, team_one, team_two, outcome_one, outcome_two,
                game_time, type, line, main_line, status, reported_outcome
            ) VALUES (
                :market_hash, :sport_id, :sport_label, :league_id, :league_label,
                :event_id, :team_one, :team_two, :outcome_one, :outcome_two,
                :game_time, :type, :line, :main_line, :status, :reported_outcome
            )
            ON CONFLICT(market_hash) DO UPDATE SET
                sport_id=excluded.sport_id,
                sport_label=excluded.sport_label,
                league_label=excluded.league_label,
                game_time=excluded.game_time,
                status=excluded.status,
                reported_outcome=excluded.reported_outcome
            """,
            payload,
        )
        self._db.commit()
        return len(payload)

    def missing_market_hashes(self) -> list[str]:
        rows = self._db.execute(
            """
            SELECT DISTINCT f.market_hash AS h
            FROM fills f
            LEFT JOIN markets m ON m.market_hash = f.market_hash
            WHERE f.market_hash IS NOT NULL AND f.market_hash != ''
              AND m.market_hash IS NULL
            """
        ).fetchall()
        return [str(row["h"]) for row in rows]

    def record_run(
        self,
        *,
        window_start: int,
        window_end: int | None,
        pages: int,
        fills_upserted: int,
    ) -> None:
        self._db.execute(
            """
            INSERT INTO ingest_runs(started_at, window_start, window_end, pages, fills_upserted)
            VALUES(?, ?, ?, ?, ?)
            """,
            (int(time.time()), window_start, window_end, pages, fills_upserted),
        )
        self._db.commit()

    def fills_in_window(
        self,
        wallet: str,
        *,
        as_maker: bool,
        start: int,
        end: int | None,
    ) -> int:
        if end is None:
            row = self._db.execute(
                "SELECT COUNT(*) AS n FROM fills "
                "WHERE wallet=? AND is_maker=? AND bet_time>=?",
                (wallet.lower(), 1 if as_maker else 0, start),
            ).fetchone()
        else:
            row = self._db.execute(
                "SELECT COUNT(*) AS n FROM fills "
                "WHERE wallet=? AND is_maker=? AND bet_time>=? AND bet_time<?",
                (wallet.lower(), 1 if as_maker else 0, start, end),
            ).fetchone()
        return int(row["n"] if row else 0)

    def iter_fills(self, wallet: str | None = None) -> list[sqlite3.Row]:
        if wallet:
            return list(
                self._db.execute(
                    """
                    SELECT f.*, m.sport_id, m.sport_label, m.league_label,
                           m.game_time, m.team_one, m.team_two, m.event_id AS market_event
                    FROM fills f
                    LEFT JOIN markets m ON m.market_hash = f.market_hash
                    WHERE f.wallet = ?
                    ORDER BY f.bet_time
                    """,
                    (wallet.lower(),),
                )
            )
        return list(
            self._db.execute(
                """
                SELECT f.*, m.sport_id, m.sport_label, m.league_label,
                       m.game_time, m.team_one, m.team_two, m.event_id AS market_event
                FROM fills f
                LEFT JOIN markets m ON m.market_hash = f.market_hash
                ORDER BY f.bet_time
                """
            )
        )

    def wallets(self) -> list[tuple[str, str]]:
        rows = self._db.execute("SELECT label, address FROM wallets ORDER BY label").fetchall()
        return [(str(row["label"]), str(row["address"])) for row in rows]


def fill_row(raw: dict[str, Any], *, wallet: str, is_maker: bool) -> dict[str, Any] | None:
    fill_hash = str(raw.get("fillHash") or "")
    if not fill_hash:
        return None
    stake = int(raw.get("stake") or 0)
    odds = int(raw.get("odds") or 0)
    outcome = raw.get("outcome")
    return {
        "fill_hash": fill_hash,
        "wallet": wallet.lower(),
        "is_maker": 1 if is_maker else 0,
        "market_hash": str(raw.get("marketHash") or ""),
        "event_id": str(raw.get("sportXeventId") or ""),
        "betting_outcome_one": 1 if raw.get("bettingOutcomeOne") else 0,
        "stake": stake,
        "odds": str(odds),
        "bet_time": int(raw.get("betTime") or 0),
        "settled": 1 if raw.get("settled") else 0,
        "outcome": int(outcome) if outcome is not None else None,
        "settle_net_return": float(raw.get("settleNetReturnValue") or 0),
        "pnl_usdc": trade_pnl_usdc(raw),
    }


def market_row(raw: dict[str, Any]) -> dict[str, Any] | None:
    market_hash = str(raw.get("marketHash") or "")
    if not market_hash:
        return None
    line = raw.get("line")
    main = raw.get("mainLine")
    outcome = raw.get("outcome")
    return {
        "market_hash": market_hash,
        "sport_id": int(raw.get("sportId") or 0),
        "sport_label": str(raw.get("sportLabel") or ""),
        "league_id": int(raw.get("leagueId") or 0),
        "league_label": str(raw.get("leagueLabel") or ""),
        "event_id": str(raw.get("sportXeventId") or ""),
        "team_one": str(raw.get("teamOneName") or ""),
        "team_two": str(raw.get("teamTwoName") or ""),
        "outcome_one": str(raw.get("outcomeOneName") or ""),
        "outcome_two": str(raw.get("outcomeTwoName") or ""),
        "game_time": int(raw.get("gameTime") or 0),
        "type": int(raw.get("type") or 0),
        "line": float(line) if line is not None else None,
        "main_line": 1 if main else 0 if main is not None else None,
        "status": str(raw.get("status") or ""),
        "reported_outcome": int(outcome) if outcome is not None else None,
    }


def pull_fills(
    client: SxClient,
    address: str,
    *,
    as_maker: bool,
    start_date: int,
    end_date: int | None,
    pages: int,
) -> list[dict[str, Any]]:
    raws = client.v2_trades_for_bettor(
        address,
        as_maker=as_maker,
        start_date=start_date,
        end_date=end_date,
        per_page=100,
        pages=pages,
    )
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in raws:
        row = fill_row(raw, wallet=address, is_maker=as_maker)
        if row is None or row["fill_hash"] in seen:
            continue
        seen.add(row["fill_hash"])
        rows.append(row)
    return rows


def enrich_markets(client: SxClient, store: HistoryStore) -> int:
    hashes = store.missing_market_hashes()
    added = 0
    for i in range(0, len(hashes), 30):
        chunk = hashes[i : i + 30]
        found = client.find_markets(chunk)
        rows = [market_row(raw) for raw in found]
        added += store.upsert_markets(row for row in rows if row)
        time.sleep(0.2)
    return added


def ingest(
    client: SxClient,
    store: HistoryStore,
    *,
    targets: list[tuple[str, str]] | None = None,
    windows: list[tuple[int, int | None, str]] | None = None,
    pages: int = 80,
    roles: tuple[bool, ...] = (False, True),
    progress: Any | None = None,
) -> dict[str, Any]:
    targets = targets or labeled_targets()
    windows = windows or default_windows()
    log = progress.write if progress is not None else (lambda *_: None)
    summary: dict[str, Any] = {"fills": 0, "markets": 0, "by_wallet": {}}
    for label, address in targets:
        store.upsert_wallet(address, label)
        wallet_n = 0
        for start, end, window_label in windows:
            for as_maker in roles:
                role = "maker" if as_maker else "taker"
                existing = store.fills_in_window(
                    address, as_maker=as_maker, start=start, end=end
                )
                if existing >= 1000:
                    log(
                        f"{label:16} {role:6} {window_label[:28]:28} "
                        f"skip ({existing} already archived)\n"
                    )
                    continue
                rows = pull_fills(
                    client,
                    address,
                    as_maker=as_maker,
                    start_date=start,
                    end_date=end,
                    pages=pages,
                )
                n = store.upsert_fills(rows)
                wallet_n += n
                summary["fills"] += n
                store.record_run(
                    window_start=start,
                    window_end=end,
                    pages=pages,
                    fills_upserted=n,
                )
                t0 = min((r["bet_time"] for r in rows), default=0)
                t1 = max((r["bet_time"] for r in rows), default=0)
                log(
                    f"{label:16} {role:6} {window_label[:28]:28} "
                    f"{n:5} fills  {_utc(t0)} → {_utc(t1)}\n"
                )
        summary["by_wallet"][label] = wallet_n
    summary["markets"] = enrich_markets(client, store)
    log(f"markets enriched {summary['markets']}\n")
    return summary


@dataclass
class BucketStat:
    name: str
    fills: int
    pnl_usdc: float
    wins: int
    losses: int


@dataclass
class StyleProfile:
    label: str
    address: str
    note: str
    role: str
    style: str
    fills: int
    maker_fills: int
    taker_fills: int
    settled: int
    wins: int
    losses: int
    pnl_usdc: float
    gross_won_usdc: float
    avg_stake_usdc: float
    avg_decimal: float
    live_share: float | None
    tennis_fills: int
    soccer_fills: int
    sports: tuple[BucketStat, ...]
    leagues: tuple[BucketStat, ...]
    odds_buckets: tuple[BucketStat, ...]
    phase: tuple[BucketStat, ...]
    scale_in_events: int
    two_sided_events: int
    first_bet: int
    last_bet: int


def _bucket_add(store: dict[str, list[float]], name: str, pnl: float | None, win: bool | None) -> None:
    slot = store.setdefault(name, [0, 0.0, 0, 0])
    slot[0] += 1
    if pnl is not None:
        slot[1] += pnl
    if win is True:
        slot[2] += 1
    elif win is False:
        slot[3] += 1


def _bucket_tuple(store: dict[str, list[float]], *, min_fills: int = 1, limit: int = 8) -> tuple[BucketStat, ...]:
    ranked = sorted(store.items(), key=lambda kv: kv[1][0], reverse=True)
    out: list[BucketStat] = []
    for name, (fills, pnl, wins, losses) in ranked:
        if fills < min_fills:
            continue
        out.append(
            BucketStat(name, int(fills), round(float(pnl), 2), int(wins), int(losses))
        )
        if len(out) >= limit:
            break
    return tuple(out)


def profile_from_rows(label: str, address: str, rows: list[sqlite3.Row]) -> StyleProfile:
    maker = taker = 0
    pnls: list[float] = []
    wins = losses = settled = 0
    gross_won = 0.0
    stakes: list[float] = []
    decimals: list[float] = []
    live = pregame = unknown_phase = 0
    tennis = soccer = 0
    sports: dict[str, list[float]] = {}
    leagues: dict[str, list[float]] = {}
    buckets: dict[str, list[float]] = {}
    phases: dict[str, list[float]] = {}
    # event_id -> sides seen, fill counts per side
    event_sides: dict[str, set[int]] = defaultdict(set)
    event_side_counts: dict[tuple[str, int], int] = Counter()
    times: list[int] = []

    for row in rows:
        is_maker = int(row["is_maker"]) == 1
        if is_maker:
            maker += 1
        else:
            taker += 1
        stake_usdc = to_usdc(int(row["stake"] or 0))
        stakes.append(stake_usdc)
        odds = int(row["odds"] or 0)  # stored as text; 1e20 does not fit SQLite INTEGER
        dec = decimal_odds(to_prob(odds)) if odds else 0.0
        if dec:
            decimals.append(dec)
        pnl = row["pnl_usdc"]
        pnl_f = float(pnl) if pnl is not None else None
        win: bool | None = None
        if row["settled"]:
            settled += 1
            if pnl_f is not None:
                pnls.append(pnl_f)
                if pnl_f > 0:
                    wins += 1
                    win = True
                    gross_won += stake_usdc + pnl_f
                elif pnl_f < 0:
                    losses += 1
                    win = False
        sport_id = int(row["sport_id"] or 0) if row["sport_id"] is not None else 0
        sport = str(row["sport_label"] or "") or f"sport-{sport_id}"
        if sport_id not in WATCH_SPORT_SET and sport_id:
            sport = f"other:{sport}"
        if sport_id == 6:
            tennis += 1
        if sport_id == 5:
            soccer += 1
        league = str(row["league_label"] or "") or "unknown"
        game_time = int(row["game_time"] or 0) if row["game_time"] is not None else 0
        bet_time = int(row["bet_time"] or 0)
        if bet_time:
            times.append(bet_time)
        if game_time and bet_time:
            phase = "live" if bet_time >= game_time else "pregame"
            if phase == "live":
                live += 1
            else:
                pregame += 1
        else:
            phase = "unknown"
            unknown_phase += 1
        _bucket_add(sports, sport, pnl_f, win)
        _bucket_add(leagues, league, pnl_f, win)
        _bucket_add(buckets, odds_bucket(dec) if dec else "unknown", pnl_f, win)
        _bucket_add(phases, phase, pnl_f, win)
        event_id = str(row["event_id"] or row["market_event"] or row["market_hash"] or "")
        side = int(row["betting_outcome_one"] or 0)
        if event_id:
            event_sides[event_id].add(side)
            event_side_counts[(event_id, side)] += 1

    total = maker + taker
    known_phase = live + pregame
    scale_in = sum(1 for n in event_side_counts.values() if n >= 3)
    two_sided = sum(1 for sides in event_sides.values() if len(sides) >= 2)
    role = role_from_counts(maker, taker)
    return StyleProfile(
        label=label,
        address=address,
        note=WALLET_NOTES.get(label, ""),
        role=role,
        style=suggested_style(role),
        fills=total,
        maker_fills=maker,
        taker_fills=taker,
        settled=settled,
        wins=wins,
        losses=losses,
        pnl_usdc=round(sum(pnls), 2),
        gross_won_usdc=round(gross_won, 2),
        avg_stake_usdc=round(sum(stakes) / len(stakes), 2) if stakes else 0.0,
        avg_decimal=round(sum(decimals) / len(decimals), 2) if decimals else 0.0,
        live_share=(live / known_phase) if known_phase else None,
        tennis_fills=tennis,
        soccer_fills=soccer,
        sports=_bucket_tuple(sports, limit=8),
        leagues=_bucket_tuple(leagues, min_fills=5, limit=8),
        odds_buckets=_bucket_tuple(buckets, limit=8),
        phase=_bucket_tuple(phases, limit=4),
        scale_in_events=scale_in,
        two_sided_events=two_sided,
        first_bet=min(times) if times else 0,
        last_bet=max(times) if times else 0,
    )


def load_profiles(store: HistoryStore) -> list[StyleProfile]:
    profiles = []
    for label, address in store.wallets():
        rows = store.iter_fills(address)
        if not rows:
            continue
        profiles.append(profile_from_rows(label, address, rows))
    return profiles


def _fmt_bucket(rows: tuple[BucketStat, ...], *, width: int = 22) -> list[str]:
    lines = []
    for row in rows:
        wr = f"{row.wins}/{row.losses}" if (row.wins or row.losses) else "-"
        lines.append(
            f"    {row.name:<{width}} n={row.fills:<5}  {wr:<8}  pnl {row.pnl_usdc:+.1f}"
        )
    return lines


def format_style_profiles(profiles: list[StyleProfile]) -> str:
    if not profiles:
        return (
            "No archived fills. Run `sxbot archive` while V2 still serves "
            "GET /trades?bettor= (until August 25)."
        )
    lines = [
        "Style profiles from archived V2 fills (net P&L = settleNetReturn − stake).",
        "Leaderboard 'Won' is gross winnings, not profit — both numbers are shown.",
        "After V3 these addresses disappear; the *habits* below are what transfer.",
        "",
    ]
    for p in profiles:
        short = p.address[:6] + "…" + p.address[-4:]
        wr = (p.wins / (p.wins + p.losses)) if (p.wins + p.losses) else None
        wr_s = f"{wr:.0%}" if wr is not None else "-"
        live_s = f"{p.live_share:.0%} live" if p.live_share is not None else "phase unknown"
        note = f"  [{p.note}]" if p.note else ""
        lines.append(f"{p.label}  {short}  role={p.role}  follow={p.style}{note}")
        lines.append(
            f"  sample {_utc(p.first_bet)} → {_utc(p.last_bet)}  "
            f"{p.fills} fills (maker {p.maker_fills} / taker {p.taker_fills})"
        )
        lines.append(
            f"  settled {p.settled}  win {p.wins}/{p.losses} ({wr_s})  "
            f"net {p.pnl_usdc:+.1f} USDC  gross-won {p.gross_won_usdc:.0f} USDC"
        )
        lines.append(
            f"  avg stake {p.avg_stake_usdc:.2f}  avg decimal {p.avg_decimal:.2f}  "
            f"{live_s}  tennis {p.tennis_fills}  soccer {p.soccer_fills}"
        )
        lines.append(
            f"  scale-in events (3+ same side) {p.scale_in_events}  "
            f"two-sided events {p.two_sided_events}"
        )
        lines.append("  sports")
        lines.extend(_fmt_bucket(p.sports, width=24))
        lines.append("  odds")
        lines.extend(_fmt_bucket(p.odds_buckets, width=22))
        lines.append("  phase")
        lines.extend(_fmt_bucket(p.phase, width=12))
        if p.leagues:
            lines.append("  leagues")
            lines.extend(_fmt_bucket(p.leagues, width=24))
        lines.append("")
    styles = Counter(p.style for p in profiles)
    if len(styles) == 1:
        lines.append(f"Suggested bot style from this set: SX_FOLLOW_STYLE={next(iter(styles))}")
    else:
        lines.append(
            "These wallets do not agree on style "
            f"({dict(styles)}). Do not clone them as one bot. "
            "`sxbot mimic` copies each wallet's *own* fills; `sxbot run` with "
            "SX_FOLLOW_STYLE=take is the Gary/Botswana habit on anonymous books."
        )
    return "\n".join(lines).rstrip()
