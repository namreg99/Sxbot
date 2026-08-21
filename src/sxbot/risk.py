from __future__ import annotations

import time

from sxbot.config import Settings
from sxbot.models import Action, Exposure, Signal
from sxbot.units import to_base_units


class RiskGate:
    def __init__(self, settings: Settings, decimals: int = 6) -> None:
        self.settings = settings
        self.decimals = decimals
        self.exposure = Exposure()
        self.quoted: set[str] = set()

    def stake(self) -> int:
        return to_base_units(self.settings.stake_usdc, self.decimals)

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
        return None

    def record(self, signal: Signal) -> None:
        if signal.action is Action.CANCEL:
            self.exposure.by_market.pop(signal.market.market_hash, None)
            self.quoted.discard(signal.market.market_hash)
            return
        self.exposure.add(signal.market.market_hash, signal.side, self.stake())
        self.quoted.add(signal.market.market_hash)
