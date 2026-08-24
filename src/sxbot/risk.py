from __future__ import annotations

import time
from typing import Any

from sxbot.config import Settings
from sxbot.filters import STYLE_MM, STYLE_TENNIS_DOG
from sxbot.models import Action, Exposure, Side, Signal
from sxbot.units import to_base_units

_TRADE_ACTIONS = {Action.JOIN_MAKER, Action.TAKE_STALE, Action.TAKE_FLOW, Action.MM_FILL}


class RiskGate:
    def __init__(self, settings: Settings, decimals: int = 6) -> None:
        self.settings = settings
        self.decimals = decimals
        self.exposure = Exposure()
        self.quoted: set[str] = set()
        self.joined_sides: set[tuple[str, str]] = set()
        self.quoted_at: dict[str, float] = {}
        self.quoted_kickoff: dict[str, int] = {}
        self.quoted_style: dict[str, str] = {}
        self.event_markets: dict[str, set[str]] = {}

    def stake(self) -> int:
        return to_base_units(self.settings.stake_usdc, self.decimals)

    def hydrate(self, rows: list[dict[str, Any]], *, now: int | None = None) -> None:
        """Remember market+side already quoted so a restart does not restack.

        Does *not* reload dollar exposure for ordinary styles — session caps
        still reset. Tennis-dog quotes that are still in their live-exit
        window are restored onto the cap so we keep watching after kickoff.
        """
        now = int(now if now is not None else time.time())
        last: dict[tuple[str, str], dict[str, Any]] = {}
        for row in rows:
            market = str(row.get("market") or "")
            side = str(row.get("side") or "")
            if market and side:
                last[(market, side)] = row
        grace = float(self.settings.tennis_dog_live_hours or 0) * 3600.0
        for (market, side), row in last.items():
            action = str(row.get("action") or "")
            if action == Action.CANCEL.value:
                continue
            if action not in {a.value for a in _TRADE_ACTIONS}:
                continue
            self.joined_sides.add((market, side))
            style = str(row.get("style") or "")
            if style:
                self.quoted_style[market] = style
            event_id = str(row.get("event_id") or "")
            if event_id:
                self.event_markets.setdefault(event_id, set()).add(market)
            if style == STYLE_MM:
                if action == Action.MM_FILL.value:
                    continue
                kickoff = int(row.get("game_time") or 0)
                if kickoff and now >= kickoff:
                    continue
                try:
                    side_enum = Side(side)
                except ValueError:
                    continue
                self.quoted.add(market)
                self.quoted_at[market] = float(row.get("ts") or now)
                self.quoted_kickoff[market] = kickoff
                self.exposure.add(market, side_enum, self.stake())
                continue
            if style != STYLE_TENNIS_DOG:
                continue
            kickoff = int(row.get("game_time") or 0)
            if not kickoff or now >= kickoff + grace:
                continue
            try:
                side_enum = Side(side)
            except ValueError:
                continue
            self.quoted.add(market)
            self.quoted_at[market] = float(row.get("ts") or now)
            self.quoted_kickoff[market] = kickoff
            self.exposure.add(market, side_enum, self.stake())

    def allow(self, signal: Signal) -> str | None:
        """Return a rejection reason, or None if the signal may trade."""
        stake = self.stake()
        max_total = to_base_units(self.settings.max_exposure_usdc, self.decimals)
        max_mkt = to_base_units(self.settings.max_per_market_usdc, self.decimals)
        market_hash = signal.market.market_hash

        if signal.action is Action.CANCEL:
            return None
        if signal.action is Action.MM_FILL:
            return None
        if (
            not self.settings.allow_live
            and signal.market.game_time
            and signal.market.game_time <= int(time.time())
        ):
            return "live market disabled"
        replacing = (
            signal.style == STYLE_MM
            and signal.action is Action.JOIN_MAKER
            and (market_hash, signal.side.value) in self.joined_sides
        )
        if replacing:
            return None
        if self.exposure.open_markets() >= self.settings.max_open_markets and market_hash not in self.quoted:
            return "max open markets"
        if self.exposure.total() + stake > max_total:
            return "max total exposure"
        if self.exposure.net(market_hash) + stake > max_mkt:
            return "max per-market exposure"
        if (market_hash, signal.side.value) in self.joined_sides:
            return "already on this side"
        if self.settings.one_side_per_market and any(
            item[0] == market_hash for item in self.joined_sides
        ):
            return "already in this market"
        event_id = str(signal.market.event_id or "")
        if (
            self.settings.one_side_per_event
            and event_id
            and event_id in self.event_markets
            and market_hash not in self.event_markets[event_id]
        ):
            return "already in this event"
        return None

    def record(self, signal: Signal, stake: int | None = None) -> None:
        market_hash = signal.market.market_hash
        if signal.action is Action.CANCEL:
            self._drop_slot(market_hash)
            self.joined_sides = {item for item in self.joined_sides if item[0] != market_hash}
            self.quoted_style.pop(market_hash, None)
            for event_id, hashes in list(self.event_markets.items()):
                hashes.discard(market_hash)
                if not hashes:
                    self.event_markets.pop(event_id, None)
            return
        amount = stake if stake is not None else self.stake()
        self.exposure.add(market_hash, signal.side, amount)
        self.quoted.add(market_hash)
        self.quoted_at[market_hash] = time.time()
        self.quoted_kickoff[market_hash] = int(signal.market.game_time or 0)
        if signal.style:
            self.quoted_style[market_hash] = signal.style
        event_id = str(signal.market.event_id or "")
        if event_id:
            self.event_markets.setdefault(event_id, set()).add(market_hash)
        if signal.action in _TRADE_ACTIONS:
            self.joined_sides.add((market_hash, signal.side.value))

    def _drop_slot(self, market_hash: str) -> None:
        """Free the cap slot. Keep joined_sides / event lock so we do not flip."""
        self.exposure.by_market.pop(market_hash, None)
        self.quoted.discard(market_hash)
        self.quoted_at.pop(market_hash, None)
        self.quoted_kickoff.pop(market_hash, None)

    def release_finished(self, now: int | None = None) -> list[str]:
        """Free cap slots. Does not forget joined_sides (no restack).

        Live orders stay blocked, so a kicked-off game is dead inventory —
        except tennis_dog, which we keep through kickoff so a live reverse
        can cancel the paper quote. Paper quotes also expire after
        paper_slot_seconds so a morning KBO join cannot occupy the 8-slot
        cap through Saturday soccer.
        """
        now = int(now if now is not None else time.time())
        ttl = float(self.settings.paper_slot_seconds or 0)
        grace = float(self.settings.tennis_dog_live_hours or 0) * 3600.0
        released: list[str] = []
        for market_hash in list(self.quoted):
            kickoff = int(self.quoted_kickoff.get(market_hash) or 0)
            opened = float(self.quoted_at.get(market_hash) or 0)
            style = self.quoted_style.get(market_hash) or ""
            if style == STYLE_TENNIS_DOG:
                if kickoff and now < kickoff + grace:
                    continue
                self._drop_slot(market_hash)
                released.append(market_hash)
                continue
            live_dead = (
                not self.settings.allow_live and kickoff and kickoff <= now
            )
            paper_stale = (
                self.settings.dry_run
                and ttl > 0
                and opened
                and (now - opened) >= ttl
            )
            if live_dead or paper_stale:
                self._drop_slot(market_hash)
                released.append(market_hash)
        return released
