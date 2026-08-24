from dataclasses import replace
import time

from sxbot.config import Settings
from sxbot.models import Book, Level, Market
from sxbot.units import from_percent


def make_settings(**overrides) -> Settings:
    base = Settings(
        api_base="https://api.toronto.sx.bet",
        api_key=None,
        private_key=None,
        dry_run=True,
        stake_usdc=5,
        sport_ids=(8,),
        league_ids=(),
        market_types=(226, 342, 28),
        only_main_line=True,
        allow_live=True,
        min_spread_bps=50,
        min_mid_move_bps=20,
        min_imbalance=0.15,
        join_ticks_behind=1,
        max_open_markets=8,
        max_markets=80,
        max_exposure_usdc=100,
        max_per_market_usdc=25,
        poll_seconds=2,
        heartbeat_seconds=60,
        enable_take_stale=True,
        enable_join_maker=True,
        paper_log="sxbot-paper.jsonl",
        flow_log="sxbot-flow.jsonl",
        min_steam_hits=2,
    )
    return replace(base, **overrides) if overrides else base


def levels(*pairs: tuple[float, float]) -> tuple[Level, ...]:
    return tuple(Level(from_percent(pct), int(size * 1_000_000)) for pct, size in pairs)


def make_book(
    o1: tuple[tuple[float, float], ...] = (),
    o2: tuple[tuple[float, float], ...] = (),
    version: str = "2",
    market_hash: str = "0xabc",
) -> Book:
    return Book(market_hash, version, levels(*o1), levels(*o2))


def make_market(**overrides) -> Market:
    data = dict(
        market_hash="0xabc",
        status="ACTIVE",
        type=226,
        sport_id=8,
        sport_label="Football",
        league_id=243,
        league_label="NFL",
        event_id="L1",
        team_one="Rams",
        team_two="49ers",
        outcome_one="Rams",
        outcome_two="49ers",
        game_time=int(time.time()) + 4 * 3600,
        live_enabled=True,
        main_line=True,
        line=None,
    )
    data.update(overrides)
    return Market(**data)
