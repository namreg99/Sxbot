from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv

DEFAULT_MARKET_TYPES = (226, 52, 342, 3, 28, 2)


def _bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


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
    user_agent: str = "sxbot/0.1"

    @classmethod
    def load(cls) -> Settings:
        load_dotenv()
        return cls(
            api_base=os.getenv("SX_API_BASE", "https://api.sx.bet").rstrip("/"),
            api_key=os.getenv("SX_API_KEY") or None,
            private_key=os.getenv("SX_PRIVATE_KEY") or None,
            dry_run=_bool("SX_DRY_RUN", True),
            stake_usdc=float(os.getenv("SX_STAKE_USDC", "5")),
            sport_ids=_ints("SX_SPORT_IDS", (8, 1, 3, 2, 5)),
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
        )

    @property
    def is_testnet(self) -> bool:
        return "toronto" in self.api_base
