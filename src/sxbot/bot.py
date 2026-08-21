from __future__ import annotations

import logging
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

from sxbot.api import SxClient
from sxbot.config import Settings
from sxbot.executor import Executor
from sxbot.models import Market
from sxbot.orderbook import BookView, analyze, format_view
from sxbot.risk import RiskGate
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
        self._last_heartbeat = 0.0

    def qualifying_markets(self, limit: int | None = None) -> list[Market]:
        now = int(time.time())
        cap = limit if limit is not None else self.settings.max_markets
        out: list[Market] = []
        league_ids = self.settings.league_ids
        fetch_cap = max(cap * 3, cap)
        for market in self.client.active_markets(
            only_main_line=self.settings.only_main_line,
            sport_ids=self.settings.sport_ids,
            types=self.settings.market_types,
            page_size=50,
            limit=fetch_cap,
        ):
            if league_ids and market.league_id not in league_ids:
                continue
            if market.status != "ACTIVE":
                continue
            if not self.settings.allow_live and market.game_time and market.game_time <= now:
                continue
            out.append(market)
        out.sort(key=lambda m: m.game_time or 10**18)
        return out[:cap]

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
        workers = min(8, len(markets))
        with ThreadPoolExecutor(max_workers=workers) as pool:
            return [row for row in pool.map(self.scan_row, markets) if row]

    def step(self) -> int:
        """One poll over the universe. Returns number of executed signals."""
        self._maybe_heartbeat()
        executed = 0
        markets = self.qualifying_markets()
        for market, view in self.scan_many(markets):
            prev = self.books.get(market.market_hash)
            self.books[market.market_hash] = view
            if prev is None:
                continue
            for signal in evaluate(market, prev, view, self.settings, self.ladder):
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
            "starting bot dry_run=%s base=%s min_order=%s USDC step=%s",
            self.settings.dry_run,
            self.settings.api_base,
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


def print_scan(rows: list[tuple[Market, BookView]], limit: int = 40) -> None:
    ranked = sorted(
        ((m, v) for m, v in rows if v.two_sided),
        key=lambda item: abs(item[1].imbalance),
        reverse=True,
    )
    print(
        f"{'LEAGUE':<12} {'MARKET':<42} {'MID':>8} {'SPRD':>7} {'IMB':>6} "
        f"{'O1%':>8} {'O1$':>8} {'O2%':>8} {'O2$':>8}"
    )
    for market, view in ranked[:limit]:
        spr = view.spread_bps()
        print(
            f"{market.league_label[:12]:<12} {market.label[:42]:<42} "
            f"{_pct(view.mid_one):>8} {str(spr)+'bp' if spr is not None else 'n/a':>7} "
            f"{view.imbalance:+6.2f} "
            f"{_pct(view.best_one):>8} {view.size_one/1e6:8.1f} "
            f"{_pct(view.best_two):>8} {view.size_two/1e6:8.1f}"
        )


def _pct(odds: int | None) -> str:
    return f"{to_percent(odds):.2f}%" if odds is not None else "n/a"


def kickoff_iso(game_time: int) -> str:
    if not game_time:
        return ""
    return datetime.fromtimestamp(game_time, tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def describe_book(market: Market, view: BookView) -> str:
    return f"{market.league_label}  {market.label}  {format_view(view)}  {kickoff_iso(market.game_time)}"
