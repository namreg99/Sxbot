from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv

from sxbot.rollout import MAINNET_API, TESTNET_API

DEFAULT_MARKET_TYPES = (226, 52, 342, 3, 28, 2)
# Football, basketball, baseball, hockey, soccer, tennis.
DEFAULT_SPORT_IDS = (8, 1, 3, 2, 5, 6)


def _bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _addrs(name: str, default: tuple[str, ...] = ()) -> tuple[str, ...]:
    raw = os.getenv(name)
    if not raw or not raw.strip():
        return default
    out: list[str] = []
    for part in raw.split(","):
        addr = part.strip()
        if addr:
            out.append(addr.lower())
    return tuple(out)


def _ints(name: str, default: tuple[int, ...]) -> tuple[int, ...]:
    raw = os.getenv(name)
    if not raw or not raw.strip():
        return default
    return tuple(int(part.strip()) for part in raw.split(",") if part.strip())


@dataclass(frozen=True)
class Settings:
    api_base: str
    api_key: str | None
    private_key: str | None
    dry_run: bool
    stake_usdc: float
    sport_ids: tuple[int, ...]
    league_ids: tuple[int, ...]
    market_types: tuple[int, ...]
    only_main_line: bool
    allow_live: bool
    min_spread_bps: int
    min_mid_move_bps: int
    min_imbalance: float
    join_ticks_behind: int
    max_open_markets: int
    max_markets: int
    max_exposure_usdc: float
    max_per_market_usdc: float
    poll_seconds: float
    heartbeat_seconds: int
    enable_take_stale: bool
    enable_join_maker: bool
    paper_log: str
    flow_log: str
    min_steam_hits: int
    user_agent: str = "sxbot/0.1"
    watch_live: bool = True
    book_source: str = "auto"
    follow_style: str = "join"
    sharp_wallets: tuple[str, ...] = ()
    sharp_log: str = "sxbot-sharp.jsonl"
    archive_path: str = "sxbot-history.sqlite"
    mimic_max_decimal: float = 3.5
    mimic_copy_makers: bool = True
    mimic_log: str = "sxbot-mimic.jsonl"

    @classmethod
    def load(cls) -> Settings:
        load_dotenv()
        return cls(
            # Real books are on mainnet. Before the V3 cutover the bot aggregates
            # V2 GET /orders into the same Book the V3 classifier uses.
            api_base=os.getenv("SX_API_BASE", MAINNET_API).rstrip("/"),
            api_key=os.getenv("SX_API_KEY") or None,
            private_key=os.getenv("SX_PRIVATE_KEY") or None,
            dry_run=_bool("SX_DRY_RUN", True),
            stake_usdc=float(os.getenv("SX_STAKE_USDC", "5")),
            sport_ids=_ints("SX_SPORT_IDS", DEFAULT_SPORT_IDS),
            league_ids=_ints("SX_LEAGUE_IDS", ()),
            market_types=_ints("SX_MARKET_TYPES", DEFAULT_MARKET_TYPES),
            only_main_line=_bool("SX_ONLY_MAIN_LINE", True),
            allow_live=_bool("SX_ALLOW_LIVE", False),
            min_spread_bps=int(os.getenv("SX_MIN_SPREAD_BPS", "50")),
            min_mid_move_bps=int(os.getenv("SX_MIN_MID_MOVE_BPS", "20")),
            min_imbalance=float(os.getenv("SX_MIN_IMBALANCE", "0.15")),
            join_ticks_behind=int(os.getenv("SX_JOIN_TICKS_BEHIND", "1")),
            max_open_markets=int(os.getenv("SX_MAX_OPEN_MARKETS", "8")),
            max_markets=int(os.getenv("SX_MAX_MARKETS", "80")),
            max_exposure_usdc=float(os.getenv("SX_MAX_EXPOSURE_USDC", "100")),
            max_per_market_usdc=float(os.getenv("SX_MAX_PER_MARKET_USDC", "25")),
            poll_seconds=float(os.getenv("SX_POLL_SECONDS", "2")),
            heartbeat_seconds=int(os.getenv("SX_HEARTBEAT_SECONDS", "60")),
            enable_take_stale=_bool("SX_ENABLE_TAKE_STALE", True),
            enable_join_maker=_bool("SX_ENABLE_JOIN_MAKER", True),
            paper_log=os.getenv("SX_PAPER_LOG", "sxbot-paper.jsonl"),
            flow_log=os.getenv("SX_FLOW_LOG", "sxbot-flow.jsonl"),
            min_steam_hits=int(os.getenv("SX_MIN_STEAM_HITS", "2")),
            watch_live=_bool("SX_WATCH_LIVE", True),
            book_source=os.getenv("SX_BOOK_SOURCE", "auto").strip().lower() or "auto",
            follow_style=(os.getenv("SX_FOLLOW_STYLE", "join").strip().lower() or "join"),
            sharp_wallets=_addrs("SX_SHARP_WALLETS"),
            sharp_log=os.getenv("SX_SHARP_LOG", "sxbot-sharp.jsonl"),
            archive_path=os.getenv("SX_ARCHIVE_PATH", "sxbot-history.sqlite"),
            mimic_max_decimal=float(os.getenv("SX_MIMIC_MAX_DECIMAL", "3.5")),
            mimic_copy_makers=_bool("SX_MIMIC_COPY_MAKERS", True),
            mimic_log=os.getenv("SX_MIMIC_LOG", "sxbot-mimic.jsonl"),
        )

    @property
    def is_testnet(self) -> bool:
        return "toronto" in self.api_base or self.api_base.rstrip("/") == TESTNET_API.rstrip("/")
