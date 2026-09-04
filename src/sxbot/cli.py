from __future__ import annotations

import argparse
import logging
import sys
import time

from dataclasses import replace

from sxbot.api import SxApiError, SxClient, index_markets
from sxbot.board import build_snapshot, render_text, serve_board
from sxbot.bot import Bot, describe_book, format_radar, print_scan, scan_radar_window
from sxbot.config import Settings
from sxbot.filters import STYLE_TENNIS_DOG
from sxbot.flow import Motive
from sxbot.fingerprint import format_profiles, profile_wallet
from sxbot.grade import format_grade, grade_paper
from sxbot.journal import iter_paper_logs, load_jsonl, print_summary
from sxbot.overlap import format_overlap_report, format_tag
from sxbot.rollout import V3_MAINNET_LIVE_AT, uses_v2_books, v3_mainnet_is_live
from sxbot.scoreboard import format_scoreboard, grade_flow
from sxbot.strategy import evaluate
from sxbot.v2 import book_from_v2_orders


def apply_live_run(settings: Settings) -> Settings:
    """Laptop live unique: take_first, same tennis-dog book as paper.

    Older live smokes set SX_SKIP_STYLES=tennis_dog. Unique follow still
    extracts that band (paper tennis_dog is green). --live turns the skip off.
    """
    keep = tuple(
        style
        for style in settings.skip_styles
        if str(style).strip().lower() != STYLE_TENNIS_DOG
    )
    return replace(
        settings,
        dry_run=False,
        follow_style="take_first",
        enable_take_stale=True,
        skip_styles=keep,
    )


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
    radar = sub.add_parser(
        "radar",
        help="Sweep a window: where the book says smart money is right now (read it, bet yourself)",
    )
    radar.add_argument("--limit", type=int, default=25)
    radar.add_argument("--seconds", type=float, default=30.0, help="How long to sweep before printing")
    sub.add_parser(
        "scoreboard",
        help="Grade flow signals (not paper orders) against real settled outcomes",
    )
    run = sub.add_parser(
        "run",
        help="Paper or live trading loop (dry-run unless SX_DRY_RUN=false or --live)",
    )
    run.add_argument("--once", action="store_true", help="Two polls then exit")
    run.add_argument(
        "--live",
        action="store_true",
        help="Place real orders (overrides SX_DRY_RUN). Needs SX_API_KEY + SX_PRIVATE_KEY.",
    )
    live_ping = sub.add_parser(
        "live-ping",
        help="Post one signed 1 USDC FOK at the touch and print SX's reply",
    )
    live_ping.add_argument(
        "--yes",
        action="store_true",
        help="Required. Sends a real signed 1 USDC FOK at the touch. It can fill.",
    )
    sub.add_parser("summary", help="Print recorded flow + paper-trade logs")
    sub.add_parser(
        "grade",
        help="Score paper bets after games are reported (assumes fills; not a historical book backtest)",
    )
    sub.add_parser(
        "sharp",
        help="Fingerprint SX_SHARP_WALLETS (V2 only) — maker vs taker habits that survive V3",
    )
    archive = sub.add_parser(
        "archive",
        help="Pull V2 fill history for known + candidate wallets into SQLite (winter + summer windows)",
    )
    archive.add_argument("--pages", type=int, default=80, help="Max pages per wallet/role/window (100 fills each)")
    archive.add_argument("--from", dest="since", default=None, help="YYYY-MM-DD single window start")
    archive.add_argument("--to", dest="until", default=None, help="YYYY-MM-DD single window end")
    archive.add_argument(
        "--wallet",
        action="append",
        default=[],
        help="Label or address to archive (repeatable). Default: known four plus research candidates",
    )
    sub.add_parser(
        "profiles",
        help="Print strategy profiles from sxbot-history.sqlite (run archive first)",
    )
    sub.add_parser(
        "makers",
        help="Rank archived wallets that mostly make, by settled fill ROI",
    )
    sub.add_parser(
        "fit",
        help="Fit the maker fill-ROI table from sxbot-history.sqlite (not a neural net)",
    )
    sub.add_parser(
        "overlap",
        help="V2 only: did labeled makers/takers sit on the same side our classifier flagged?",
    )
    mimic = sub.add_parser(
        "mimic",
        help="Paper-trade new fills from labeled wallets (V2 only; skips longshots)",
    )
    mimic.add_argument("--once", action="store_true", help="Prime + one copy poll then exit")
    mm = sub.add_parser(
        "mm",
        help="Pregame paper maker: ghost-quote the heavy side (SX_MM_TWO_SIDED for both)",
    )
    mm.add_argument("--once", action="store_true", help="One quote pass then exit")
    board = sub.add_parser(
        "board",
        help="Auto-refresh dashboard: 5k+ tape today/yesterday + paper feed (browser or Telegram)",
    )
    board.add_argument("--host", default=None, help="Bind address (default SX_BOARD_HOST / 127.0.0.1)")
    board.add_argument("--port", type=int, default=None, help="Port (default SX_BOARD_PORT / 8765)")
    board.add_argument("--once", action="store_true", help="Print one snapshot and exit")

    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    settings = Settings.load()
    if args.cmd == "run" and args.live:
        settings = apply_live_run(settings)
        print(
            "LIVE take_first: same unique-follow team (including tennis dogs), "
            "HIT the other side (IOC). Takes do not sit on SX as offers. "
            "This mode does not post resting joins.",
            flush=True,
        )
    if args.cmd == "summary":
        print_summary(settings.flow_log, settings.paper_log)
        mimic_rows = load_jsonl(settings.mimic_log)
        if mimic_rows:
            print()
            print(f"mimic paper    {len(mimic_rows)}   ({settings.mimic_log})")
        return 0
    if args.cmd == "profiles":
        return cmd_profiles(settings)
    if args.cmd == "makers":
        return cmd_makers(settings)
    if args.cmd == "fit":
        return cmd_fit(settings)
    if args.cmd == "overlap":
        print(format_overlap_report(load_jsonl(settings.flow_log)))
        return 0
    _warn_if_v3_mainnet_not_live(settings)
    with SxClient(settings.api_base, settings.api_key, user_agent=settings.user_agent) as client:
        try:
            if args.cmd == "doctor":
                return cmd_doctor(client, settings)
            if args.cmd == "live-ping":
                return cmd_live_ping(client, settings, args)
            if args.cmd == "grade":
                return cmd_grade(client, settings)
            if args.cmd == "sharp":
                return cmd_sharp(client, settings)
            if args.cmd == "archive":
                return cmd_archive(client, settings, args)
            if args.cmd == "mimic":
                return cmd_mimic(client, settings, once=args.once)
            if args.cmd == "mm":
                return cmd_mm(client, settings, once=args.once)
            if args.cmd == "board":
                return cmd_board(client, settings, args)
            bot = Bot(settings, client)
            if args.cmd == "scan":
                return cmd_scan(bot, args.limit)
            if args.cmd == "watch":
                return cmd_watch(bot, once=args.once)
            if args.cmd == "flow":
                return cmd_flow(bot, once=args.once)
            if args.cmd == "radar":
                return cmd_radar(bot, limit=args.limit, seconds=args.seconds)
            if args.cmd == "scoreboard":
                return cmd_scoreboard(client, settings)
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
            "flow classifier. Maker addresses tag overlap (who was quoting) "
            "until cutover; they are not used to place orders. After cutover "
            "this keeps running against V3 snapshots with no strategy change.",
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
    min_usdc = meta.min_order / 10 ** meta.decimals
    print(f"min order     {meta.min_order} base units  ({min_usdc:g} USDC)")
    print(f"dry_run       {settings.dry_run}")
    print(f"follow_style  {settings.follow_style}")
    print(f"sharp wallets {len(settings.sharp_wallets)}")
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


def cmd_live_ping(client: SxClient, settings: Settings, args: argparse.Namespace) -> int:
    """One signed 1 USDC FOK at the touch. Can fill. Linode IPs often 403."""
    from dataclasses import replace

    from sxbot.executor import Executor
    from sxbot.models import Action, Side, Signal
    from sxbot.units import OddsLadder, taker_odds, to_percent

    if not args.yes:
        print("Refusing: pass --yes to send a real signed 1 USDC take.")
        return 2
    if not settings.api_key or not settings.private_key:
        print("Need SX_API_KEY and SX_PRIVATE_KEY in .env")
        return 2

    live_settings = replace(settings, dry_run=False)
    meta = client.metadata()
    executor = Executor(live_settings, meta, client)
    ladder = OddsLadder(meta.odds_ladder_step_size)
    stake = max(meta.min_order, 1_000_000)

    market = None
    price = 0
    picked = ""
    for candidate in client.active_markets(
        only_main_line=True,
        sport_ids=settings.sport_ids or (6, 3, 5),
        page_size=20,
        limit=40,
    ):
        if candidate.game_time and candidate.game_time <= int(time.time()):
            continue
        try:
            book = client.snapshot(candidate.market_hash)
        except SxApiError:
            continue
        if not book.outcome_two:
            continue
        raw = taker_odds(book.outcome_two[0].percentage_odds)
        price = ladder.clamp(raw)
        if price <= 0:
            continue
        market = candidate
        picked = candidate.outcome_one
        break
    if market is None or price <= 0:
        print("No pregame book to take")
        return 2

    signal = Signal(
        market=market,
        side=Side.OUTCOME_ONE,
        action=Action.TAKE_FLOW,
        maker_odds=price,
        reason="1 USDC live take",
        mid_move_bps=0,
        imbalance=0.0,
        confidence=0.0,
    )
    order = executor._sign_order(signal, stake, "FOK")
    print(f"market   {market.label}")
    print(f"take     {picked}")
    print("size     1 USDC FOK at the touch")
    print(f"odds     {to_percent(price):.3f}% implied")
    print("posting  POST /orders-v3 …")
    try:
        result = client.create_orders([order], wait=True)
    except SxApiError as exc:
        print(f"HTTP     {exc.status}")
        print(f"url      {exc.url}")
        print(f"body     {exc.body[:500]}")
        if exc.status == 403:
            print("SX refused the signed $1 order from this IP. $0 spent.")
        return 1
    print("HTTP     200")
    print(f"result   {result}")
    return 0


def cmd_grade(client: SxClient, settings: Settings) -> int:
    logs = iter_paper_logs(settings.paper_log)
    if not logs:
        _print_grade(client, settings.paper_log, title="join/run paper")
    else:
        for i, path in enumerate(logs):
            if i:
                print()
                print("-" * 70)
                print()
            stem = path.stem
            title = stem.replace("sxbot-paper-", "").replace("sxbot-paper", "join/run")
            if title == "join/run":
                title = "join/run paper"
            else:
                title = f"{title} paper"
            _print_grade(client, str(path), title=title)
    mimic_rows = load_jsonl(settings.mimic_log)
    if mimic_rows:
        print()
        print("-" * 70)
        print()
        _print_grade(client, settings.mimic_log, title="mimic paper")
    return 0


def cmd_board(client: SxClient, settings: Settings, args: argparse.Namespace) -> int:
    host = args.host or settings.board_host
    port = int(args.port or settings.board_port)
    if args.once:
        snap = build_snapshot(client, settings)
        print(render_text(snap))
        return 0
    print(f"board  http://{host}:{port}  (auto-refresh {settings.board_refresh_seconds}s)")
    if host in {"127.0.0.1", "localhost"}:
        print(
            "That URL only works in a browser ON THIS MACHINE. "
            "Opening it on your laptop looks up your laptop, where nothing is listening "
            "— the browser will say the site is unavailable. "
            "Run `sxbot board` on your own computer, or `sxbot board --once` here."
        )
    if settings.telegram_token and settings.telegram_chat_id:
        print("telegram alerts enabled")
    else:
        print(
            "Telegram optional: set SX_TELEGRAM_TOKEN and SX_TELEGRAM_CHAT_ID "
            "(BotFather bot, then message it and use getUpdates for the chat id)."
        )
    serve_board(client, settings, host=host, port=port)
    return 0


def _print_grade(client: SxClient, path: str, *, title: str) -> None:
    rows = load_jsonl(path)
    hashes = list(dict.fromkeys(str(row.get("market") or "") for row in rows if row.get("market")))
    found = client.find_markets(hashes) if hashes else []
    markets = index_markets(found)
    print(f"# {title}  ({path})")
    print(format_grade(grade_paper(rows, markets, decimals=6)))


def cmd_sharp(client: SxClient, settings: Settings) -> int:
    from sxbot.wallets import labeled_targets

    targets = labeled_targets(settings)
    start = int(time.time()) - 30 * 86400
    profiles = []
    for label, address in targets:
        maker_fills = client.v2_trades_for_bettor(
            address, as_maker=True, start_date=start, pages=6
        )
        taker_fills = client.v2_trades_for_bettor(
            address, as_maker=False, start_date=start, pages=6
        )
        open_orders = client.v2_orders_for_maker(address)
        hashes = list(
            dict.fromkeys(
                [str(row.get("marketHash") or "") for row in open_orders]
                + [str(row.get("marketHash") or "") for row in maker_fills[:20]]
                + [str(row.get("marketHash") or "") for row in taker_fills[:20]]
            )
        )
        hashes = [h for h in hashes if h][:30]
        found = client.find_markets(hashes) if hashes else []
        markets = index_markets(found)
        profile = profile_wallet(
            address,
            maker_fills=maker_fills,
            taker_fills=taker_fills,
            open_orders=open_orders,
            markets=markets,
        )
        print(f"# {label}")
        profiles.append(profile)
    print(format_profiles(profiles))
    return 0


def cmd_archive(client: SxClient, settings: Settings, args: argparse.Namespace) -> int:
    from sxbot.archive import HistoryStore, ingest, parse_day
    from sxbot.wallets import resolve_archive_targets

    try:
        targets = resolve_archive_targets(args.wallet, settings)
    except ValueError as exc:
        print(exc, file=sys.stderr)
        return 2
    windows = None
    if args.since:
        windows = [
            (
                parse_day(args.since),
                parse_day(args.until) if args.until else None,
                "custom",
            )
        ]
    labels = ", ".join(label for label, _ in targets)
    print(
        f"archiving {labels} → {settings.archive_path}  "
        f"(pages={args.pages} per role/window; tennis included in later profiles)"
    )
    with HistoryStore(settings.archive_path) as store:
        summary = ingest(
            client,
            store,
            targets=targets,
            windows=windows,
            pages=args.pages,
            progress=sys.stdout,
        )
    print(
        f"done  fills+={summary['fills']}  markets+={summary['markets']}  "
        f"by_wallet={summary['by_wallet']}"
    )
    print("next: sxbot profiles")
    return 0


def cmd_makers(settings: Settings) -> int:
    from sxbot.archive import HistoryStore, format_maker_roi

    path = settings.archive_path
    with HistoryStore(path) as store:
        print(format_maker_roi(store))
        print(f"\nsource {path}")
    return 0


def cmd_fit(settings: Settings) -> int:
    from pathlib import Path

    from sxbot.archive import HistoryStore
    from sxbot.learn import fit_maker_model, format_maker_model

    sqlite = Path(settings.archive_path)
    if not sqlite.exists():
        print(f"No archive at {sqlite}. Run `sxbot archive` first.")
        return 1
    with HistoryStore(sqlite) as store:
        model = fit_maker_model(store, prior_stake=settings.mm_fit_prior_stake)
    model.save(settings.mm_model_path)
    print(format_maker_model(model))
    print(f"\nwrote {settings.mm_model_path}")
    return 0


def cmd_profiles(settings: Settings) -> int:
    from sxbot.archive import HistoryStore, format_style_profiles, load_profiles

    path = settings.archive_path
    with HistoryStore(path) as store:
        profiles = load_profiles(store)
        print(format_style_profiles(profiles))
        if profiles:
            print(f"\nsource {path}  fills {store.fill_count()}")
    return 0


def cmd_mimic(client: SxClient, settings: Settings, *, once: bool) -> int:
    from sxbot.mimic import MimicBot

    bot = MimicBot(settings, client)
    if not once:
        bot.run()
        return 0
    primed = bot.step()
    copied = bot.step()
    print(f"mimic primed then copied {copied} new fill(s) (prime={primed})")
    return 0


def cmd_mm(client: SxClient, settings: Settings, *, once: bool) -> int:
    from sxbot.mm import MakerBot, mm_log_path

    bot = MakerBot(settings, client)
    print(
        f"pregame ghost maker  dry_run={settings.dry_run}  two_sided={settings.mm_two_sided}  "
        f"log={mm_log_path(settings)}  stake={settings.stake_usdc} USDC, pull at kickoff  "
        f"model={'on' if bot.model is not None else 'off'}"
    )
    if once:
        n = bot.step()
        print(f"mm executed {n} action(s)")
        return 0
    bot.run()
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


def cmd_radar(bot: Bot, *, limit: int, seconds: float) -> int:
    print(f"sweeping the book for {seconds:.0f}s (most markets do not reprice every poll)...")
    rows = scan_radar_window(bot, seconds=seconds)
    print(format_radar(rows, limit=limit))
    return 0


def cmd_scoreboard(client: SxClient, settings: Settings) -> int:
    rows = load_jsonl(settings.flow_log)
    hashes = list(dict.fromkeys(str(row.get("market") or "") for row in rows if row.get("market")))
    found = client.find_markets(hashes) if hashes else []
    markets = index_markets(found)
    print(format_scoreboard(grade_flow(rows, markets)))
    return 0


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
                        f"  SIGNAL {signal.style or '-':<13} {signal.action.value:11} "
                        f"{signal.side.value:12} conf={signal.confidence:.2f}  {signal.reason}"
                    )
                    print(f"  {format_tag(bot.overlap_tag(market, report))}")
                continue
            side = report.side.value if report.side else "-"
            print(describe_book(market, view))
            print(
                f"  FLOW {report.motive.value:14} {side:12} "
                f"conf={report.confidence:.2f} steam={report.steam_hits} "
                f"persist={report.persistence:.2f} tape={report.tape_prints}  "
                f"{'; '.join(report.reasons)}"
            )
            print(f"  {format_tag(bot.overlap_tag(market, report))}")
        n += 1
        if rounds is not None and n >= rounds:
            return 0
        time.sleep(bot.settings.poll_seconds)
