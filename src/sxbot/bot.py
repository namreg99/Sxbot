from __future__ import annotations

import json
import logging
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

from sxbot.api import SxClient
from sxbot.config import Settings
from sxbot.executor import Executor
from sxbot.flow import FlowReport, Motive, SteamTracker, classify, steam_direction
from sxbot.models import Book, Market, PublicTrade
from sxbot.orderbook import BookView, analyze, format_view
from sxbot.risk import RiskGate
from sxbot.rollout import uses_v2_books
from sxbot.strategy import evaluate
from sxbot.units import OddsLadder, to_percent

log = logging.getLogger("sxbot")


class Bot:
    def __init__(self, settings: Settings, client: SxClient) -> None:
        self.settings = settings
        self.client = client
        self.meta = client.metadata()
        self.ladder = OddsLadder(self.meta.odds_ladder_step_size)
        self.risk = RiskGate(settings, self.meta.decimals)
        self.executor = Executor(settings, self.meta, client)
        self.books: dict[str, BookView] = {}
        self.steam = SteamTracker()
        self._seen_trades: set[str] = set()
        self._tape_primed = False
        self._last_heartbeat = 0.0
        self.book_source = "v2" if uses_v2_books(settings.api_base, book_source=settings.book_source) else "v3"

    def qualifying_markets(self, limit: int | None = None) -> list[Market]:
        now = int(time.time())
        cap = limit if limit is not None else self.settings.max_markets
        out: list[Market] = []
        league_ids = self.settings.league_ids
        sport_ids = self.settings.sport_ids or (None,)
        per_sport = max(cap // max(len(self.settings.sport_ids), 1), 12)
        seen: set[str] = set()
        for sport in sport_ids:
            kwargs: dict = dict(
                only_main_line=self.settings.only_main_line,
                types=self.settings.market_types,
                page_size=50,
                limit=per_sport * 2,
            )
            if sport is not None:
                kwargs["sport_ids"] = (sport,)
            for market in self.client.active_markets(**kwargs):
                if market.market_hash in seen:
                    continue
                if league_ids and market.league_id not in league_ids:
                    continue
                if market.status != "ACTIVE":
                    continue
                seen.add(market.market_hash)
                out.append(market)
        return pick_universe(
            out,
            cap,
            now,
            watch_live=self.settings.watch_live or self.settings.allow_live,
        )

    def scan_row(self, market: Market) -> tuple[Market, BookView] | None:
        try:
            book = self.client.snapshot(market.market_hash)
        except Exception as exc:
            log.debug("snapshot failed %s: %s", market.market_hash, exc)
            return None
        return market, analyze(book)

    def scan_many(self, markets: list[Market]) -> list[tuple[Market, BookView]]:
        if not markets:
            return []
        if self.book_source == "v2":
            return self._scan_v2(markets)
        workers = min(8, len(markets))
        with ThreadPoolExecutor(max_workers=workers) as pool:
            return [row for row in pool.map(self.scan_row, markets) if row]

    def _scan_v2(self, markets: list[Market]) -> list[tuple[Market, BookView]]:
        hashes = [market.market_hash for market in markets]
        poll_version = str(int(time.time() * 1000))
        try:
            books = self.client.v2_books(hashes)
        except Exception as exc:
            log.warning("v2 book fetch failed: %s", exc)
            return []
        rows: list[tuple[Market, BookView]] = []
        for market in markets:
            raw = books.get(market.market_hash) or Book(market.market_hash, "empty", (), ())
            book = Book(raw.market_hash, poll_version, raw.outcome_one, raw.outcome_two)
            rows.append((market, analyze(book)))
        return rows

    def pull_tape(self, markets: list[Market] | None = None) -> dict[str, list[PublicTrade]] | None:
        """Fresh prints since last poll. None = tape unavailable."""
        try:
            if self.book_source == "v2":
                hashes = [m.market_hash for m in (markets or [])]
                tape = self.client.v2_trades(hashes) if hashes else []
            else:
                tape = self.client.public_trades(per_page=50)
        except Exception as exc:
            log.debug("public tape failed: %s", exc)
            return None
        if not self._tape_primed:
            self._seen_trades = {t.trade_id for t in tape}
            self._tape_primed = True
            return {}
        fresh: dict[str, list[PublicTrade]] = defaultdict(list)
        for trade in tape:
            if trade.trade_id in self._seen_trades:
                continue
            self._seen_trades.add(trade.trade_id)
            fresh[trade.market_hash].append(trade)
        if len(self._seen_trades) > 4000:
            self._seen_trades = set(list(self._seen_trades)[-2000:])
        return dict(fresh)

    def classify_row(
        self,
        market: Market,
        view: BookView,
        tape: dict[str, list[PublicTrade]] | None,
    ) -> tuple[BookView | None, FlowReport | None]:
        prev = self.books.get(market.market_hash)
        if (
            prev is not None
            and prev.levels_one == view.levels_one
            and prev.levels_two == view.levels_two
        ):
            return prev, None
        self.books[market.market_hash] = view
        if prev is None:
            return None, None
        trades = None if tape is None else tape.get(market.market_hash, [])
        steam_side = steam_direction(prev, view, self.settings.min_mid_move_bps)
        hits = self.steam.record(market.market_hash, steam_side, time.time())
        report = classify(prev, view, self.settings, trades=trades, steam_hits=hits)
        self._log_flow(market, report)
        return prev, report

    def step(self) -> int:
        self._maybe_heartbeat()
        executed = 0
        markets = self.qualifying_markets()
        tape = self.pull_tape(markets)
        for market, view in self.scan_many(markets):
            prev, report = self.classify_row(market, view, tape)
            if prev is None or report is None:
                continue
            for signal in evaluate(
                market, prev, view, self.settings, self.ladder, report=report
            ):
                reason = self.risk.allow(signal)
                if reason:
                    log.info("skip %s %s: %s", signal.action.value, market.label, reason)
                    continue
                stake = max(self.risk.stake(), self.meta.min_order)
                self.executor.execute(signal, stake)
                self.risk.record(signal)
                executed += 1
        return executed

    def run(self) -> None:
        log.info(
            "starting bot dry_run=%s base=%s books=%s min_order=%s USDC step=%s",
            self.settings.dry_run,
            self.settings.api_base,
            self.book_source,
            self.meta.min_order / 10**self.meta.decimals,
            self.meta.odds_ladder_step_size,
        )
        try:
            while True:
                n = self.step()
                if n:
                    log.info("executed %s signal(s) this poll", n)
                time.sleep(self.settings.poll_seconds)
        except KeyboardInterrupt:
            log.info("shutting down")
            if not self.settings.dry_run:
                try:
                    self.client.cancel_all()
                    self.client.heartbeat(0)
                except Exception:
                    log.exception("failed to cancel on shutdown")

    def _log_flow(self, market: Market, report: FlowReport) -> None:
        if report.motive is Motive.NONE:
            return
        path = Path(self.settings.flow_log)
        record = {
            "ts": time.time(),
            "market": market.market_hash,
            "label": market.label,
            "league": market.league_label,
            "phase": market.phase(),
            "book_source": self.book_source,
            "live_enabled": market.live_enabled,
            "motive": report.motive.value,
            "side": report.side.value if report.side else None,
            "move_bps": report.move_bps,
            "persistence": report.persistence,
            "tob_vs_dw_bps": report.tob_vs_dw_bps,
            "tape_prints": report.tape_prints,
            "steam_hits": report.steam_hits,
            "confidence": report.confidence,
            "reasons": list(report.reasons),
            "actionable": report.actionable,
        }
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record) + "\n")

    def _maybe_heartbeat(self) -> None:
        if self.settings.dry_run:
            return
        now = time.time()
        if now - self._last_heartbeat < max(self.settings.heartbeat_seconds / 3, 5):
            return
        try:
            self.client.heartbeat(self.settings.heartbeat_seconds)
            self._last_heartbeat = now
        except Exception:
            log.exception("heartbeat failed")


def pick_universe(
    markets: list[Market],
    cap: int,
    now: int,
    *,
    watch_live: bool,
) -> list[Market]:
    """Soonest pregame first; keep a slice of live books for intel."""
    pregame = [m for m in markets if not m.is_live(now)]
    live = [m for m in markets if m.is_live(now)]
    pregame.sort(key=lambda m: m.game_time or 10**18)
    live.sort(key=lambda m: m.game_time or 0, reverse=True)
    if not watch_live:
        return pregame[:cap]
    live_slots = min(len(live), max(8, cap // 5)) if cap >= 10 else min(len(live), max(cap // 2, 1))
    chosen = pregame[: max(cap - live_slots, 0)]
    live_slots = cap - len(chosen)
    return chosen + live[:live_slots]


def print_scan(rows: list[tuple[Market, BookView]], limit: int = 40) -> None:
    ranked = sorted(
        ((m, v) for m, v in rows if v.two_sided),
        key=lambda item: abs(item[1].imbalance),
        reverse=True,
    )
    print(
        f"{'PHASE':<8} {'LEAGUE':<12} {'MARKET':<36} {'MID':>7} {'DW':>7} {'SPRD':>6} {'IMB':>6} "
        f"{'O1%':>7} {'O1$':>7} {'O2%':>7} {'O2$':>7}"
    )
    for market, view in ranked[:limit]:
        spr = view.spread_bps()
        print(
            f"{market.phase():<8} {market.league_label[:12]:<12} {market.label[:36]:<36} "
            f"{_pct(view.mid_one):>7} {_pct(view.dw_mid):>7} "
            f"{str(spr)+'bp' if spr is not None else 'n/a':>6} "
            f"{view.imbalance:+6.2f} "
            f"{_pct(view.best_one):>7} {view.size_one/1e6:7.1f} "
            f"{_pct(view.best_two):>7} {view.size_two/1e6:7.1f}"
        )


def _pct(odds: int | None) -> str:
    return f"{to_percent(odds):.2f}%" if odds is not None else "n/a"


def kickoff_iso(game_time: int) -> str:
    if not game_time:
        return ""
    return datetime.fromtimestamp(game_time, tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def describe_book(market: Market, view: BookView) -> str:
    phase = market.phase().upper()
    return (
        f"{phase:<8} {market.league_label}  {market.label}  {format_view(view)}  "
        f"{kickoff_iso(market.game_time)}"
    )
