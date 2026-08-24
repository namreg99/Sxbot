from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class Side(str, Enum):
    OUTCOME_ONE = "outcome_one"
    OUTCOME_TWO = "outcome_two"

    @property
    def is_outcome_one(self) -> bool:
        return self is Side.OUTCOME_ONE

    def opposite(self) -> Side:
        return Side.OUTCOME_TWO if self is Side.OUTCOME_ONE else Side.OUTCOME_ONE


class Action(str, Enum):
    JOIN_MAKER = "join_maker"
    TAKE_STALE = "take_stale"
    TAKE_FLOW = "take_flow"
    CANCEL = "cancel"


@dataclass(frozen=True)
class Level:
    percentage_odds: int
    size: int

    @classmethod
    def from_api(cls, raw: dict[str, Any] | None) -> Level | None:
        if not raw:
            return None
        return cls(int(raw["percentageOdds"]), int(raw["size"]))


@dataclass(frozen=True)
class Book:
    market_hash: str
    version: str
    outcome_one: tuple[Level, ...]
    outcome_two: tuple[Level, ...]

    @classmethod
    def from_api(cls, raw: dict[str, Any]) -> Book:
        def levels(key: str) -> tuple[Level, ...]:
            rows = raw.get(key) or []
            return tuple(Level(int(r["percentageOdds"]), int(r["size"])) for r in rows)

        return cls(
            market_hash=raw["marketHash"],
            version=str(raw.get("version") or ""),
            outcome_one=levels("outcomeOne"),
            outcome_two=levels("outcomeTwo"),
        )

    def side(self, which: Side) -> tuple[Level, ...]:
        return self.outcome_one if which is Side.OUTCOME_ONE else self.outcome_two


@dataclass(frozen=True)
class Market:
    market_hash: str
    status: str
    type: int
    sport_id: int
    sport_label: str
    league_id: int
    league_label: str
    event_id: str
    team_one: str
    team_two: str
    outcome_one: str
    outcome_two: str
    game_time: int
    live_enabled: bool
    main_line: bool | None
    line: float | None

    @classmethod
    def from_api(cls, raw: dict[str, Any]) -> Market:
        return cls(
            market_hash=raw["marketHash"],
            status=raw.get("status") or "",
            type=int(raw.get("type") or 0),
            sport_id=int(raw.get("sportId") or 0),
            sport_label=raw.get("sportLabel") or "",
            league_id=int(raw.get("leagueId") or 0),
            league_label=raw.get("leagueLabel") or "",
            event_id=raw.get("sportXeventId") or "",
            team_one=raw.get("teamOneName") or "",
            team_two=raw.get("teamTwoName") or "",
            outcome_one=raw.get("outcomeOneName") or "",
            outcome_two=raw.get("outcomeTwoName") or "",
            game_time=int(raw.get("gameTime") or 0),
            live_enabled=bool(raw.get("liveEnabled")),
            main_line=raw.get("mainLine"),
            line=raw.get("line"),
        )

    @property
    def label(self) -> str:
        return f"{self.outcome_one} / {self.outcome_two}"

    def is_live(self, now: int | None = None) -> bool:
        now = now if now is not None else int(time.time())
        return bool(self.game_time) and self.game_time <= now

    def phase(self, now: int | None = None) -> str:
        return "live" if self.is_live(now) else "pregame"


@dataclass(frozen=True)
class PublicTrade:
    trade_id: str
    market_hash: str
    is_betting_outcome_one: bool
    stake: int
    odds: int
    bet_time: str

    @classmethod
    def from_api(cls, raw: dict[str, Any]) -> PublicTrade:
        return cls(
            trade_id=raw["tradeId"],
            market_hash=raw["marketHash"],
            is_betting_outcome_one=bool(raw["isBettingOutcomeOne"]),
            stake=int(raw["totalStake"]),
            odds=int(raw["weightedAverageOdds"]),
            bet_time=raw.get("betTime") or "",
        )


@dataclass(frozen=True)
class ExchangeMeta:
    chain_id: int
    domain: dict[str, Any]
    base_token: str
    decimals: int
    escrow: str
    odds_ladder_step_size: int
    min_order: int
    min_resting: int
    max_create: int
    max_cancel: int

    @classmethod
    def from_api(cls, raw: dict[str, Any]) -> ExchangeMeta:
        asset = raw["activeAsset"]
        limits = raw["limits"]
        return cls(
            chain_id=int(raw["chainId"]),
            domain=dict(raw["domain"]),
            base_token=asset["baseToken"],
            decimals=int(asset["decimals"]),
            escrow=asset["escrowAddress"],
            odds_ladder_step_size=int(raw["oddsLadderStepSize"]),
            min_order=int(limits["orderSizeMinimumBaseUnits"]),
            min_resting=int(limits["minRestingOrderSizeBaseUnits"]),
            max_create=int(limits["maxCreateOrders"]),
            max_cancel=int(limits["maxCancelOrders"]),
        )


@dataclass(frozen=True)
class Signal:
    market: Market
    side: Side
    action: Action
    maker_odds: int
    reason: str
    mid_move_bps: int
    imbalance: float
    confidence: float
    crossed: bool = False
    motive: str = ""
    style: str = ""


@dataclass
class Exposure:
    """Paper / live net maker stake per market, keyed by market hash."""

    by_market: dict[str, dict[str, int]] = field(default_factory=dict)
    # by_market[hash] = {"one": stake, "two": stake}

    def add(self, market_hash: str, side: Side, stake: int) -> None:
        slot = self.by_market.setdefault(market_hash, {"one": 0, "two": 0})
        key = "one" if side is Side.OUTCOME_ONE else "two"
        slot[key] += stake

    def net(self, market_hash: str) -> int:
        slot = self.by_market.get(market_hash, {"one": 0, "two": 0})
        return abs(slot["one"] - slot["two"]) + min(slot["one"], slot["two"])

    def total(self) -> int:
        return sum(self.net(h) for h in self.by_market)

    def open_markets(self) -> int:
        return sum(1 for h, slot in self.by_market.items() if slot["one"] or slot["two"])
