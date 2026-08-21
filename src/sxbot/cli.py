from __future__ import annotations

import argparse
import logging
import sys
import time

from sxbot.api import SxApiError, SxClient
from sxbot.bot import Bot, describe_book, print_scan
from sxbot.config import Settings
from sxbot.flow import Motive
from sxbot.rollout import V3_MAINNET_LIVE_AT, v3_mainnet_is_live
from sxbot.strategy import evaluate


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
        help="Classify sharp vs taker flow from the book (V3-safe, no wallets)",
    )
    flow.add_argument("--once", action="store_true")
    run = sub.add_parser("run", help="Paper or live trading loop (dry-run unless SX_DRY_RUN=false)")
    run.add_argument("--once", action="store_true", help="One poll then exit")

    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    settings = Settings.load()
    _warn_if_v3_mainnet_not_live(settings)
    with SxClient(settings.api_base, settings.api_key, user_agent=settings.user_agent) as client:
        try:
            if args.cmd == "doctor":
                return cmd_doctor(client, settings)
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
    if not settings.is_testnet and not v3_mainnet_is_live():
        print(
            f"WARNING: V3 is testnet-only until {V3_MAINNET_LIVE_AT.isoformat()} "
            "(SX Bet docs: 'Do not point a production integration at V3 until "
            "August 25th at 10:00 AM EST'). Book/trade routes on mainnet will 403 "
            "until then. Set SX_API_BASE=https://api.toronto.sx.bet for now.",
            file=sys.stderr,
        )


def cmd_doctor(client: SxClient, settings: Settings) -> int:
    meta = client.metadata()
    print(f"base          {settings.api_base}")
    print(f"chainId       {meta.chain_id}")
    print(f"token         {meta.base_token}")
    print(f"escrow        {meta.escrow}")
    print(f"ladder step   {meta.odds_ladder_step_size} x 1e15  ({meta.odds_ladder_step_size / 1000:.3f}%)")
    print(f"min order     {meta.min_order} base units")
    print(f"dry_run       {settings.dry_run}")
    sports = client.sports()
    print(f"sports        {len(sports)}")
    from itertools import islice

    markets = list(
        islice(
            client.active_markets(
                only_main_line=True,
                sport_ids=settings.sport_ids[:1] or (8,),
                page_size=5,
                limit=5,
            ),
            5,
        )
    )
    print(f"sample mkts   {len(markets)}")
    if not markets:
        print("no markets returned for the configured sports")
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


def cmd_scan(bot: Bot, limit: int) -> int:
    markets = bot.qualifying_markets(limit=max(limit, 12))
    rows = bot.scan_many(markets)
    print(f"{len(rows)} snapshots from {len(markets)} markets (showing {min(limit, len(rows))})")
    print_scan(rows, limit=limit)
    return 0


def cmd_watch(bot: Bot, *, once: bool) -> int:
    print("watching maker line moves — no orders will be posted")
    return _poll_flow(bot, once=once, signals_only=True)


def cmd_flow(bot: Bot, *, once: bool) -> int:
    print("classifying sharp flow from the book (no wallets, V3-safe) — no orders")
    return _poll_flow(bot, once=once, signals_only=False)


def _poll_flow(bot: Bot, *, once: bool, signals_only: bool) -> int:
    rounds = 2 if once else None
    n = 0
    while True:
        tape = bot.pull_tape()
        for market, view in bot.scan_many(bot.qualifying_markets()):
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
