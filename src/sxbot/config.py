from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv

from sxbot.rollout import MAINNET_API, TESTNET_API

# 1 = soccer 2-way (Team / Not Team). 226 = MLB ML. 52 = team vs team.
# 342/3 = spreads. Totals (2, 28) stay out of the default order universe.
DEFAULT_MARKET_TYPES = (1, 226, 52, 342, 3)
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


def _strs(name: str, default: tuple[str, ...] = ()) -> tuple[str, ...]:
    raw = os.getenv(name)
    if not raw or not raw.strip():
        return default
    return tuple(part.strip().lower() for part in raw.split(",") if part.strip())


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
    closes_log: str = "sxbot-closes.jsonl"
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
    skip_totals: bool = True
    skip_not_tie: bool = True
    join_tob_lag: bool = False
    # Backtest (453 unique pregame tob_lag, entry at mid): <=2.20 was +6.3%,
    # dogs 2.20-3.50 were -14.3%. Parked depth on favorites is real; on dogs
    # it is a trap. Joins on this motive skip anything longer than this.
    tob_lag_max_decimal: float = 2.20
    one_side_per_market: bool = True
    one_side_per_event: bool = True
    paper_slot_seconds: float = 7200.0
    max_order_decimal: float = 3.5
    # Skip Sept NFL / Oct NBA in August. 192h = 8 days.
    max_kickoff_hours: float = 192.0
    # Empty-book 1000bp flicker: persist below this AND move >= flicker_bps.
    min_persistence: float = 0.01
    flicker_bps: int = 800
    tennis_dog_live_hours: float = 5.0
    # Quote styles to never join/take. Example: tennis_dog on a $1 live smoke.
    skip_styles: tuple[str, ...] = ()
    kelly_live_cap: bool = False
    board_host: str = "127.0.0.1"
    board_port: int = 8765
    board_refresh_seconds: int = 20
    big_fill_usdc: float = 5000.0
    telegram_token: str | None = None
    telegram_chat_id: str | None = None
    # Pregame maker (`sxbot mm`). Default is one-sided ghost quotes on the
    # heavy book. Two-sided mode widens behind the inside until both quotes
    # sum to less than 100% by this many ticks / bps.
    mm_max_widen_ticks: int = 6
    mm_min_overround_bps: int = 25
    mm_min_decimal: float = 1.12
    # False = ghost-quote the heavy side only (extract trainer). True = classic two-sided MM.
    mm_two_sided: bool = False
    # Empirical-Bayes maker fill-ROI floor. 0 = skip cells that shrink to red.
    mm_min_roi: float = 0.0
    mm_model_path: str = "sxbot-maker-model.json"
    mm_fit_prior_stake: float = 25_000.0
    # Paper Kelly. 0.625 is between half-Kelly (moderate) and 3/4 (aggressive).
    # Takes execute at Kelly. Joins/MM execute flat $5 and keep a Kelly shadow.
    bankroll_usdc: float = 1000.0
    kelly_fraction: float = 0.625
    kelly_max_frac: float = 0.05
    kelly_on_takes: bool = True

    @classmethod
    def load(cls) -> Settings:
        load_dotenv()
        return cls(
            # Real books are on mainnet. Before the V3 cutover the bot aggregates
            # V2 GET /orders into the same Book the V3 classifier uses.
            api_base=os.getenv("SX_API_BASE", MAINNET_API).rstrip("/"),
            api_key=(os.getenv("SX_API_KEY") or "").strip().strip("\"'") or None,
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
            closes_log=os.getenv("SX_CLOSES_LOG", "sxbot-closes.jsonl"),
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
            skip_totals=_bool("SX_SKIP_TOTALS", True),
            skip_not_tie=_bool("SX_SKIP_NOT_TIE", True),
            # Product default on: parked-depth joins on shorts/pick'em. Tests
            # that construct Settings() keep the dataclass False unless they
            # pass join_tob_lag=True.
            join_tob_lag=_bool("SX_JOIN_TOB_LAG", True),
            tob_lag_max_decimal=float(os.getenv("SX_TOB_LAG_MAX_DECIMAL", "2.20")),
            one_side_per_market=_bool("SX_ONE_SIDE_PER_MARKET", True),
            one_side_per_event=_bool("SX_ONE_SIDE_PER_EVENT", True),
            paper_slot_seconds=float(os.getenv("SX_PAPER_SLOT_SECONDS", "7200")),
            max_order_decimal=float(os.getenv("SX_MAX_ORDER_DECIMAL", "3.5")),
            max_kickoff_hours=float(os.getenv("SX_MAX_KICKOFF_HOURS", "192")),
            min_persistence=float(os.getenv("SX_MIN_PERSISTENCE", "0.01")),
            flicker_bps=int(os.getenv("SX_FLICKER_BPS", "800")),
            tennis_dog_live_hours=float(os.getenv("SX_TENNIS_DOG_LIVE_HOURS", "5")),
            skip_styles=_strs("SX_SKIP_STYLES"),
            kelly_live_cap=_bool("SX_KELLY_LIVE_CAP", False),
            board_host=os.getenv("SX_BOARD_HOST", "127.0.0.1").strip() or "127.0.0.1",
            board_port=int(os.getenv("SX_BOARD_PORT", "8765")),
            board_refresh_seconds=int(os.getenv("SX_BOARD_REFRESH_SECONDS", "20")),
            big_fill_usdc=float(os.getenv("SX_BIG_FILL_USDC", "5000")),
            telegram_token=os.getenv("SX_TELEGRAM_TOKEN") or None,
            telegram_chat_id=os.getenv("SX_TELEGRAM_CHAT_ID") or None,
            mm_max_widen_ticks=int(os.getenv("SX_MM_MAX_WIDEN_TICKS", "6")),
            mm_min_overround_bps=int(os.getenv("SX_MM_MIN_OVERROUND_BPS", "25")),
            mm_min_decimal=float(os.getenv("SX_MM_MIN_DECIMAL", "1.12")),
            mm_two_sided=_bool("SX_MM_TWO_SIDED", False),
            mm_min_roi=float(os.getenv("SX_MM_MIN_ROI", "0")),
            mm_model_path=os.getenv("SX_MM_MODEL_PATH", "sxbot-maker-model.json"),
            mm_fit_prior_stake=float(os.getenv("SX_MM_FIT_PRIOR_STAKE", "25000")),
            bankroll_usdc=float(os.getenv("SX_BANKROLL_USDC", "1000")),
            kelly_fraction=float(os.getenv("SX_KELLY_FRACTION", "0.625")),
            kelly_max_frac=float(os.getenv("SX_KELLY_MAX_FRAC", "0.05")),
            kelly_on_takes=_bool("SX_KELLY_ON_TAKES", True),
        )

    @property
    def is_testnet(self) -> bool:
        return "toronto" in self.api_base or self.api_base.rstrip("/") == TESTNET_API.rstrip("/")
