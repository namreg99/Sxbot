from __future__ import annotations

import json
import logging
import time
from collections import OrderedDict, defaultdict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from sxbot.api import SxClient
from sxbot.config import Settings
from sxbot.executor import Executor
from sxbot.filters import STYLE_TENNIS_DOG, kickoff_skip_reason, quote_family
from sxbot.flow import FlowReport, Motive, SteamTracker, classify, steam_direction
from sxbot.kelly import TAKE_ACTIONS
from sxbot.journal import load_all_paper
from sxbot.models import Action, Book, Market, PublicTrade, Side, Signal
from sxbot.orderbook import BookView, analyze, format_view
from sxbot.overlap import MarketQuotes, attribute_quotes, attribute_tape, tag_signal
from sxbot.risk import RiskGate
from sxbot.rollout import uses_v2_books
from sxbot.strategy import evaluate
from sxbot.units import OddsLadder, to_percent
from sxbot.v2 import books_from_v2_orders, public_trades_from_v2
from sxbot.wallets import labeled_addresses

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
        self._seen_trades: OrderedDict[str, None] = OrderedDict()
        self._tape_primed = False
        self._last_heartbeat = 0.0
        self.book_source = "v2" if uses_v2_books(settings.api_base, book_source=settings.book_source) else "v3"
        self._labeled = labeled_addresses(settings)
        self._quotes_by_market: dict[str, MarketQuotes] = {}
        self._takers_by_market: dict[str, dict[str, tuple[str, ...]]] = {}
        self.risk.hydrate(load_all_paper(settings.paper_log))

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
        pinned = [m for m in out if m.market_hash in self.risk.quoted]
        filtered: list[Market] = []
        for market in out:
            if market.market_hash in self.risk.quoted:
                filtered.append(market)
                continue
            if kickoff_skip_reason(market, self.settings, now=now):
                continue
            filtered.append(market)
        picked = pick_universe(
            filtered,
            cap,
            now,
            watch_live=self.settings.watch_live or self.settings.allow_live,
            prefer=quote_family,
        )
        seen_pick = {m.market_hash for m in picked}
        for market in pinned:
            if market.market_hash not in seen_pick:
                picked.append(market)
                seen_pick.add(market.market_hash)
        return picked

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
            orders = self.client.v2_orders(hashes)
        except Exception as exc:
            log.warning("v2 book fetch failed: %s", exc)
            self._quotes_by_market = {}
            return []
        self._quotes_by_market = attribute_quotes(orders, self._labeled)
        books = books_from_v2_orders(orders, version=poll_version, market_hashes=hashes)
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
                raw_trades = self.client.v2_trade_rows(hashes) if hashes else []
                self._takers_by_market = attribute_tape(raw_trades, self._labeled)
                tape = public_trades_from_v2(raw_trades)
            else:
                self._takers_by_market = {}
                tape = self.client.public_trades(per_page=50)
        except Exception as exc:
            log.debug("public tape failed: %s", exc)
            return None
        if not self._tape_primed:
            for trade in tape:
                self._seen_trades[trade.trade_id] = None
            self._tape_primed = True
            return {}
        fresh: dict[str, list[PublicTrade]] = defaultdict(list)
        for trade in tape:
            if trade.trade_id in self._seen_trades:
                continue
            self._seen_trades[trade.trade_id] = None
            fresh[trade.market_hash].append(trade)
        while len(self._seen_trades) > 4000:
            self._seen_trades.popitem(last=False)
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
        self._log_flow(market, view, report)
        return prev, report

    def overlap_tag(self, market: Market, report: FlowReport) -> dict:
        market_key = market.market_hash.lower()
        quotes = self._quotes_by_market.get(market_key)
        side = report.side.value if report.side else None
        takers = (self._takers_by_market.get(market_key) or {}).get(side or "", ())
        return tag_signal(quotes, side=side, takers=takers)

    def step(self) -> int:
        self._maybe_heartbeat()
        freed = self.risk.release_finished(int(time.time()))
        if freed:
            log.info("freed %s open slot(s) after kickoff/TTL", len(freed))
        executed = 0
        markets = self.qualifying_markets()
        tape = self.pull_tape(markets)
        for market, view in self.scan_many(markets):
            prev, report = self.classify_row(market, view, tape)
            if prev is None and report is None:
                continue
            exit_signal = self._tennis_dog_exit(market, view, report)
            if exit_signal is not None:
                reason = self.risk.allow(exit_signal)
                if reason:
                    log.info("skip %s %s: %s", exit_signal.action.value, market.label, reason)
                else:
                    stake = max(self.risk.stake(), self.meta.min_order)
                    self.executor.execute(exit_signal, stake)
                    self.risk.record(exit_signal, stake)
                    executed += 1
                continue
            if (
                market.is_live()
                and self.risk.quoted_style.get(market.market_hash) == STYLE_TENNIS_DOG
            ):
                continue
            if prev is None or report is None:
                continue
            for signal in evaluate(
                market, prev, view, self.settings, self.ladder, report=report
            ):
                stake = self.risk.stake_for(signal)
                if stake is None:
                    log.info(
                        "skip %s %s: no Kelly edge vs fair %.3f%%",
                        signal.action.value,
                        market.label,
                        to_percent(signal.fair_odds) if signal.fair_odds else 0.0,
                    )
                    continue
                stake = max(stake, self.meta.min_order) if signal.action not in TAKE_ACTIONS else stake
                if signal.action in TAKE_ACTIONS and stake < self.meta.min_order:
                    log.info("skip %s %s: Kelly size below min order", signal.action.value, market.label)
                    continue
                reason = self.risk.allow(signal, stake=stake)
                if reason:
                    log.info("skip %s %s: %s", signal.action.value, market.label, reason)
                    continue
                extra = {}
                if signal.action in TAKE_ACTIONS:
                    extra = {
                        "fair_pct": to_percent(signal.fair_odds) if signal.fair_odds else None,
                        "kelly_fraction": self.settings.kelly_fraction,
                        "bankroll_usdc": self.settings.bankroll_usdc,
                    }
                self.executor.execute(signal, stake, extra=extra or None)
                self.risk.record(signal, stake)
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
                try:
                    n = self.step()
                    if n:
                        log.info("executed %s signal(s) this poll", n)
                    time.sleep(self.settings.poll_seconds)
                except KeyboardInterrupt:
                    raise
                except Exception:
                    log.exception("poll failed; retrying (paper loop stays up)")
                    time.sleep(max(self.settings.poll_seconds, 8.0))
        except KeyboardInterrupt:
            log.info("shutting down")
            if not self.settings.dry_run:
                try:
                    self.client.cancel_all()
                    self.client.heartbeat(0)
                except Exception:
                    log.exception("failed to cancel on shutdown")

    def _log_flow(self, market: Market, view: BookView, report: FlowReport) -> None:
        if report.motive is Motive.NONE:
            return
        path = Path(self.settings.flow_log)
        record = {
            "ts": time.time(),
            "market": market.market_hash,
            "label": market.label,
            "league": market.league_label,
            "game_time": market.game_time,
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
            # Implied probability of outcome one *at the moment the signal fired*,
            # so scoreboard.py can grade edge vs. the price already in the book,
            # not naive win rate (which just rewards picking favorites).
            "mid_pct": to_percent(view.mid_one) if view.mid_one is not None else None,
        }
        record.update(self.overlap_tag(market, report))
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

    def _tennis_dog_exit(
        self,
        market: Market,
        view: BookView,
        report: FlowReport | None,
    ) -> Signal | None:
        """Pregame tennis dogs stay on the book until steam reverses live."""
        if self.risk.quoted_style.get(market.market_hash) != STYLE_TENNIS_DOG:
            return None
        if market.market_hash not in self.risk.quoted:
            return None
        if not market.is_live():
            return None
        if report is None or not report.actionable or report.side is None:
            return None
        our_side = next(
            (side for hash_, side in self.risk.joined_sides if hash_ == market.market_hash),
            None,
        )
        if our_side is None or report.side.value == our_side:
            return None
        price = view.best(report.side) or 0
        return Signal(
            market=market,
            side=Side(our_side),
            action=Action.CANCEL,
            maker_odds=price,
            reason=f"tennis_dog live exit: steam reversed to {report.side.value}",
            mid_move_bps=report.move_bps,
            imbalance=view.imbalance,
            confidence=report.confidence,
            motive=report.motive.value,
            style=STYLE_TENNIS_DOG,
        )


def pick_universe(
    markets: list[Market],
    cap: int,
    now: int,
    *,
    watch_live: bool,
    prefer: Callable[[Market], object] | None = None,
) -> list[Market]:
    """Soonest pregame first; keep a slice of live books for intel.

    `prefer` is an optional callable(market) -> truthy for sports we actually
    quote (MLB / soccer / NFL / tennis). Those fill the cap first so NPB/KBO
    tonight cannot crowd out the styles we bet.
    """
    pregame = [m for m in markets if not m.is_live(now)]
    live = [m for m in markets if m.is_live(now)]
    pregame.sort(key=lambda m: m.game_time or 10**18)
    live.sort(key=lambda m: m.game_time or 0, reverse=True)
    if prefer is not None:
        hot = [m for m in pregame if prefer(m)]
        cold = [m for m in pregame if not prefer(m)]
        pregame = hot + cold
    if not watch_live:
        return pregame[:cap]
    live_slots = min(len(live), max(8, cap // 5)) if cap >= 10 else min(len(live), max(cap // 2, 1))
    chosen = pregame[: max(cap - live_slots, 0)]
    live_slots = cap - len(chosen)
    return chosen + live[:live_slots]


@dataclass
class RadarRow:
    market: Market
    view: BookView
    report: FlowReport


def scan_radar(bot: Bot) -> list[RadarRow]:
    """One pass: classify the whole universe, return actionable rows.

    A single pass rarely sees anything — most markets do not reprice inside
    one poll interval. Prefer `scan_radar_window` for real use; this is kept
    for callers that already run their own poll loop.
    """
    out: list[RadarRow] = []
    markets = bot.qualifying_markets()
    tape = bot.pull_tape(markets)
    for market, view in bot.scan_many(markets):
        prev, report = bot.classify_row(market, view, tape)
        if prev is None or report is None or report.motive is Motive.NONE:
            continue
        out.append(RadarRow(market=market, view=view, report=report))
    return out


def scan_radar_window(bot: Bot, *, seconds: float, poll_seconds: float | None = None) -> list[RadarRow]:
    """Poll for `seconds` and return the latest flagged row per (market, motive, side).

    Book changes are sparse per market — sweeping a window instead of one
    diff is what makes "where is the smart money right now" actually mean
    something instead of usually coming back empty.
    """
    poll = max(poll_seconds or bot.settings.poll_seconds, 0.5)
    deadline = time.time() + max(seconds, poll)
    latest: dict[tuple[str, str, str], RadarRow] = {}
    while True:
        for row in scan_radar(bot):
            side = row.report.side.value if row.report.side else "-"
            key = (row.market.market_hash, row.report.motive.value, side)
            latest[key] = row
        if time.time() >= deadline:
            break
        time.sleep(poll)
    return list(latest.values())


_MOTIVE_PLAIN: dict[str, str] = {
    "maker_steam": (
        "the market makers just moved their own price toward this side with "
        "nobody betting into it — that is them repricing on information, "
        "not reacting to bettors"
    ),
    "size_rotation": (
        "the market makers pulled their resting money off the other side and "
        "piled it onto this one"
    ),
    "tob_lag": (
        "the real weight of money already sitting in the book is on this "
        "side, even though the displayed best price has not caught up yet"
    ),
    "crossed": (
        "the market makers already moved on, and there is still a stale "
        "price sitting on the other side for the taking"
    ),
}


def _confidence_word(confidence: float) -> str:
    if confidence >= 0.90:
        return "very strong"
    if confidence >= 0.75:
        return "strong"
    if confidence >= 0.60:
        return "worth a look"
    return "early / weak"


def outcome_name(market: Market, side: Side) -> str:
    return market.outcome_one if side.is_outcome_one else market.outcome_two


def plain_pick_sentence(row: RadarRow) -> str:
    report = row.report
    side = report.side
    outcome = outcome_name(row.market, side) if side is not None else "?"
    why = _MOTIVE_PLAIN.get(report.motive.value, report.motive.value)
    word = _confidence_word(report.confidence)
    when = kickoff_iso(row.market.game_time) or "kickoff time unknown"
    return (
        f"{outcome} ({row.market.league_label}, {row.market.label}) — {why}. "
        f"Signal: {word} ({report.confidence:.2f}). Kicks off {when}."
    )


def plain_picks(rows: list[RadarRow], *, limit: int = 5) -> list[str]:
    ranked = sorted(
        (r for r in rows if r.report.actionable),
        key=lambda r: r.report.confidence,
        reverse=True,
    )
    return [plain_pick_sentence(row) for row in ranked[:limit]]


def format_radar(rows: list[RadarRow], *, limit: int = 25) -> str:
    if not rows:
        return (
            "No flow yet. Either the book is quiet right now, or this was the "
            "first poll (the classifier needs two snapshots to see a move) — "
            "run `sxbot radar` again in a few seconds."
        )
    lines: list[str] = []
    picks = plain_picks(rows, limit=5)
    if picks:
        lines.append("TOP PICKS RIGHT NOW (read these, bet yourself — nothing is auto-placed)")
        lines.append("")
        for i, sentence in enumerate(picks, 1):
            lines.append(f"{i}. {sentence}")
        lines.append("")
        lines.append(
            "These are inferred from how the book is being managed, not proof. "
            "Run `sxbot scoreboard` after enough games settle to see whether "
            "this signal actually beats the price already in the book."
        )
        lines.append("")
        lines.append("-" * 70)
        lines.append("")
    ranked = sorted(rows, key=lambda r: r.report.confidence, reverse=True)
    lines.append("Full detail (same data, for anyone who wants the numbers):")
    lines.append("")
    for row in ranked[:limit]:
        report = row.report
        side = report.side.value if report.side else "-"
        label = "ACT" if report.actionable else "fyi"
        lines.append(
            f"[{label}] conf={report.confidence:.2f}  {row.market.phase():<8} "
            f"{row.market.league_label:<12} {row.market.label[:40]:<40}"
        )
        lines.append(
            f"       {report.motive.value:<14} side={side:<12} "
            f"mid={to_percent(row.view.mid_one) if row.view.mid_one is not None else 'n/a'}%  "
            f"{kickoff_iso(row.market.game_time)}"
        )
        if report.reasons:
            lines.append(f"       why: {'; '.join(report.reasons)}")
        lines.append("")
    n_actionable = sum(1 for r in rows if r.report.actionable)
    lines.append(f"{n_actionable} actionable / {len(rows)} flagged this pass.")
    lines.append(
        "Confidence here is a hand-tuned heuristic, not a proven edge — run "
        "`sxbot run` for a while and then `sxbot scoreboard` to check whether "
        "these motives actually beat the price that was already in the book."
    )
    return "\n".join(lines)


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
