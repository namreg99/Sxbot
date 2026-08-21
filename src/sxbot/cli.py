from __future__ import annotations

import argparse
import logging
import sys
import time

from sxbot.api import SxApiError, SxClient
from sxbot.bot import Bot, describe_book, print_scan
from sxbot.config import Settings
from sxbot.flow import Motive
from sxbot.grade import format_grade, grade_paper
from sxbot.journal import load_jsonl, print_summary
from sxbot.rollout import V3_MAINNET_LIVE_AT, uses_v2_books, v3_mainnet_is_live
from sxbot.strategy import evaluate
from sxbot.v2 import book_from_v2_orders


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="sxbot",
        description="Follow SX Bet market makers: join their line moves instead of copy-trading wallets.",
    )
    parser.add_argument("-v", "--verbose", action="store_true")
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("doctor", help="Check API connectivity and print exchange metadata")
    scan = sub.add_parser("scan", help="Snapshot current books and rank maker-size imbalance")
    scan.add_argument("--limit", type=int, default=30)
    watch = sub.add_parser("watch", help="Poll books and print maker-follow signals (no orders)")
    watch.add_argument("--once", action="store_true")
    flow = sub.add_parser(
        "flow",
        help="Classify sharp vs taker flow from the book (no wallets)",
    )
    flow.add_argument("--once", action="store_true")
    run = sub.add_parser("run", help="Paper or live trading loop (dry-run unless SX_DRY_RUN=false)")
    run.add_argument("--once", action="store_true", help="Two polls then exit")
    sub.add_parser("summary", help="Print recorded flow + paper-trade logs")
    sub.add_parser(
        "grade",
        help="Score paper bets after games are reported (assumes fills; not a historical book backtest)",
    )

    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    settings = Settings.load()
    if args.cmd == "summary":
        print_summary(settings.flow_log, settings.paper_log)
        return 0
    _warn_if_v3_mainnet_not_live(settings)
    with SxClient(settings.api_base, settings.api_key, user_agent=settings.user_agent) as client:
        try:
            if args.cmd == "doctor":
                return cmd_doctor(client, settings)
            if args.cmd == "grade":
                return cmd_grade(client, settings)
            bot = Bot(settings, client)
            if args.cmd == "scan":
                return cmd_scan(bot, args.limit)
            if args.cmd == "watch":
                return cmd_watch(bot, once=args.once)
            if args.cmd == "flow":
                return cmd_flow(bot, once=args.once)
            if args.cmd == "run":
                if args.once:
                    # Two polls so the strategy has a previous book to compare.
                    total = 0
                    for i in range(2):
                        total += bot.step()
                        if i == 0:
                            time.sleep(bot.settings.poll_seconds)
                    print(f"executed {total} signal(s)")
                    return 0
                bot.run()
                return 0
        except SxApiError as exc:
            print(exc, file=sys.stderr)
            return 2
    return 1


def _warn_if_v3_mainnet_not_live(settings: Settings) -> None:
    if uses_v2_books(settings.api_base, book_source=settings.book_source):
        print(
            f"V3 mainnet books are gated until {V3_MAINNET_LIVE_AT.isoformat()} "
            "(10:00 AM EST Aug 25). Using live V2 order books on the same "
            "flow classifier; maker addresses are ignored. After cutover this "
            "keeps running against V3 snapshots with no strategy change.",
            file=sys.stderr,
        )


def cmd_doctor(client: SxClient, settings: Settings) -> int:
    meta = client.metadata()
    v2 = uses_v2_books(settings.api_base, book_source=settings.book_source)
    print(f"base          {settings.api_base}")
    print(f"books         {'V2 orders (pre-cutover mainnet)' if v2 else 'V3 snapshot'}")
    print(f"v3 live at    {V3_MAINNET_LIVE_AT.isoformat()}  now_live={v3_mainnet_is_live()}")
    print(f"chainId       {meta.chain_id}")
    print(f"token         {meta.base_token}")
    print(f"escrow        {meta.escrow}")
    print(f"ladder step   {meta.odds_ladder_step_size} x 1e15  ({meta.odds_ladder_step_size / 1000:.3f}%)")
    print(f"min order     {meta.min_order} base units")
    print(f"dry_run       {settings.dry_run}")
    print(f"watch_live    {settings.watch_live}  allow_live_trades={settings.allow_live}")
    sports = client.sports()
    print(f"sports        {len(sports)}")
    from itertools import islice

    markets = list(
        islice(
            client.active_markets(
                only_main_line=True,
                sport_ids=settings.sport_ids or (5, 3, 8),
                page_size=10,
                limit=10,
            ),
            10,
        )
    )
    print(f"sample mkts   {len(markets)}")
    if not markets:
        print("no markets returned for the configured sports")
        return 0
    if v2:
        orders = client.v2_orders([m.market_hash for m in markets])
        chosen = next(
            (m for m in markets if any(o.get("marketHash") == m.market_hash for o in orders)),
            markets[0],
        )
        book = book_from_v2_orders(chosen.market_hash, orders, version="doctor")
        n_orders = sum(1 for o in orders if o.get("marketHash") == chosen.market_hash)
        print(
            f"v2 book       {chosen.phase()} {chosen.label}  "
            f"O1={len(book.outcome_one)} O2={len(book.outcome_two)}  orders={n_orders}"
        )
        trades = client.v2_trades([m.market_hash for m in markets], per_page=5)
        print(f"v2 tape       {len(trades)} recent fills (addresses discarded)")
        return 0
    try:
        book = client.snapshot(markets[0].market_hash)
        print(
            f"snapshot      {markets[0].label}  "
            f"O1={len(book.outcome_one)} O2={len(book.outcome_two)}  v={book.version}"
        )
    except SxApiError as exc:
        print(f"snapshot      FAILED ({exc.status})")
        print(exc)
        return 2
    try:
        trades = client.public_trades(per_page=3)
        print(f"public tape   {len(trades)} recent trades")
    except SxApiError as exc:
        print(f"public tape   FAILED ({exc.status})")
    return 0


def cmd_grade(client: SxClient, settings: Settings) -> int:
    rows = load_jsonl(settings.paper_log)
    hashes = list(dict.fromkeys(str(row.get("market") or "") for row in rows if row.get("market")))
    found = client.find_markets(hashes) if hashes else []
    markets = {str(row.get("marketHash") or ""): row for row in found}
    print(format_grade(grade_paper(rows, markets, decimals=6)))
    return 0


def cmd_scan(bot: Bot, limit: int) -> int:
    markets = bot.qualifying_markets(limit=max(limit, 12))
    rows = bot.scan_many(markets)
    two_sided = sum(1 for _, view in rows if view.two_sided)
    print(
        f"{len(rows)} books ({bot.book_source}) from {len(markets)} markets, "
        f"{two_sided} two-sided (showing {min(limit, two_sided)})"
    )
    print_scan(rows, limit=limit)
    return 0


def cmd_watch(bot: Bot, *, once: bool) -> int:
    print("watching maker line moves — no orders will be posted")
    return _poll_flow(bot, once=once, signals_only=True)


def cmd_flow(bot: Bot, *, once: bool) -> int:
    print("classifying sharp flow from the book (no wallets) — no orders")
    return _poll_flow(bot, once=once, signals_only=False)


def _poll_flow(bot: Bot, *, once: bool, signals_only: bool) -> int:
    rounds = 2 if once else None
    n = 0
    while True:
        markets = bot.qualifying_markets()
        tape = bot.pull_tape(markets)
        for market, view in bot.scan_many(markets):
            prev, report = bot.classify_row(market, view, tape)
            if prev is None or report is None:
                continue
            if report.motive is Motive.NONE:
                continue
            if signals_only:
                signals = evaluate(
                    market, prev, view, bot.settings, bot.ladder, report=report
                )
                if not signals:
                    continue
                print(describe_book(market, view))
                for signal in signals:
                    print(
                        f"  SIGNAL {signal.action.value:11} {signal.side.value:12} "
                        f"conf={signal.confidence:.2f}  {signal.reason}"
                    )
                continue
            side = report.side.value if report.side else "-"
            print(describe_book(market, view))
            print(
                f"  FLOW {report.motive.value:14} {side:12} "
                f"conf={report.confidence:.2f} steam={report.steam_hits} "
                f"persist={report.persistence:.2f} tape={report.tape_prints}  "
                f"{'; '.join(report.reasons)}"
            )
        n += 1
        if rounds is not None and n >= rounds:
            return 0
        time.sleep(bot.settings.poll_seconds)
