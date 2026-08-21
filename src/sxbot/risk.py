from __future__ import annotations

import time
from typing import Any

from sxbot.config import Settings
from sxbot.models import Action, Exposure, Signal
from sxbot.units import to_base_units

_TRADE_ACTIONS = {Action.JOIN_MAKER, Action.TAKE_STALE, Action.TAKE_FLOW}


class RiskGate:
    def __init__(self, settings: Settings, decimals: int = 6) -> None:
        self.settings = settings
        self.decimals = decimals
        self.exposure = Exposure()
        self.quoted: set[str] = set()
        self.joined_sides: set[tuple[str, str]] = set()

    def stake(self) -> int:
        return to_base_units(self.settings.stake_usdc, self.decimals)

    def hydrate(self, rows: list[dict[str, Any]]) -> None:
        """Remember market+side already quoted so a restart does not restack.

        Does *not* reload dollar exposure — session caps still reset. Game
        hashes are unique, so keeping joined_sides across restarts only
        blocks quoting the same settled/open market again.
        """
        for row in rows:
            action = str(row.get("action") or "")
            market = str(row.get("market") or "")
            side = str(row.get("side") or "")
            if not market or not side:
                continue
            if action == Action.CANCEL.value:
                self.joined_sides = {item for item in self.joined_sides if item[0] != market}
                continue
            if action in {a.value for a in _TRADE_ACTIONS}:
                self.joined_sides.add((market, side))

    def allow(self, signal: Signal) -> str | None:
        """Return a rejection reason, or None if the signal may trade."""
        stake = self.stake()
        max_total = to_base_units(self.settings.max_exposure_usdc, self.decimals)
        max_mkt = to_base_units(self.settings.max_per_market_usdc, self.decimals)
        market_hash = signal.market.market_hash

        if signal.action is Action.CANCEL:
            return None
        if (
            not self.settings.allow_live
            and signal.market.game_time
            and signal.market.game_time <= int(time.time())
        ):
            return "live market disabled"
        if self.exposure.open_markets() >= self.settings.max_open_markets and market_hash not in self.quoted:
            return "max open markets"
        if self.exposure.total() + stake > max_total:
            return "max total exposure"
        if self.exposure.net(market_hash) + stake > max_mkt:
            return "max per-market exposure"
        if (market_hash, signal.side.value) in self.joined_sides:
            return "already on this side"
        return None

    def record(self, signal: Signal, stake: int | None = None) -> None:
        market_hash = signal.market.market_hash
        if signal.action is Action.CANCEL:
            self.exposure.by_market.pop(market_hash, None)
            self.quoted.discard(market_hash)
            self.joined_sides = {item for item in self.joined_sides if item[0] != market_hash}
            return
        amount = stake if stake is not None else self.stake()
        self.exposure.add(market_hash, signal.side, amount)
        self.quoted.add(market_hash)
        if signal.action in _TRADE_ACTIONS:
            self.joined_sides.add((market_hash, signal.side.value))
