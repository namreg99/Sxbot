from __future__ import annotations

import time
from typing import Any

from sxbot.config import Settings
from sxbot.filters import (
    FLAT_LIVE_DOG_STYLES,
    STYLE_MM,
    STYLE_TENNIS_DOG,
    soccer_team_lock_token,
    soccer_team_lock_token_from_row,
)
from sxbot.kelly import KELLY_ACTIONS, sized_take_usdc, tracker_kelly_usdc
from sxbot.fills import live_filled_base_units, order_ids, row_live_filled
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
        self.chosen_sides: set[tuple[str, str]] = set()
        self.pending_order_ids: dict[str, list[str]] = {}
        self.quoted_at: dict[str, float] = {}
        self.quoted_kickoff: dict[str, int] = {}
        self.quoted_style: dict[str, str] = {}
        self.event_markets: dict[str, set[str]] = {}
        self.locked_teams: set[tuple[str, ...]] = set()
        self.market_team_tokens: dict[str, tuple[str, ...]] = {}
        # Paper/pregame unique we want live to confirm — not a live fill.
        self.thesis_sides: dict[str, str] = {}

    def stake(self) -> int:
        return to_base_units(self.settings.stake_usdc, self.decimals)

    def stake_for(self, signal: Signal) -> int | None:
        """Base-units stake: $1 unique, $4 when paper Kelly would still bet.

        If we already filled $1 and Kelly is still on, this returns the extra
        $3 to match the paper $25 cap. TAKE_STALE with no Kelly edge → skip.
        Soccer/MLB steam dogs stay the unique floor until that book has W–L.
        """
        if (signal.style or "") in FLAT_LIVE_DOG_STYLES:
            already = self.exposure.net(signal.market.market_hash)
            return None if already > 0 else self.stake()
        if signal.action in KELLY_ACTIONS:
            sized = sized_take_usdc(self.settings, signal)
            if sized is None:
                return None
            return to_base_units(sized, self.decimals)
        floor = self.stake()
        already = self.exposure.net(signal.market.market_hash)
        if not bool(getattr(self.settings, "kelly_live_cap", False)):
            return None if already > 0 else floor
        shadow = tracker_kelly_usdc(signal)
        target_usdc = (
            float(self.settings.max_per_market_usdc)
            if shadow is not None and shadow > 0
            else float(self.settings.stake_usdc)
        )
        need = to_base_units(target_usdc, self.decimals) - already
        if need < floor:
            return None
        return need

    def _kelly_topup_room(self, market_hash: str) -> bool:
        """True when a live unique fill is still short of the $4 Kelly cap."""
        if not bool(getattr(self.settings, "kelly_live_cap", False)):
            return False
        if self.quoted_style.get(market_hash) in FLAT_LIVE_DOG_STYLES:
            return False
        already = self.exposure.net(market_hash)
        target = to_base_units(float(self.settings.max_per_market_usdc), self.decimals)
        return already > 0 and (target - already) >= self.stake()

    def hydrate(
        self,
        rows: list[dict[str, Any]],
        *,
        now: int | None = None,
        thesis_only: bool = False,
    ) -> None:
        """Remember market+side already quoted so a restart does not restack.

        Live unique fills restore their matched dollars so a $1 fill can still
        take the extra $3 when Kelly is on. Tennis-dog quotes that are still
        in their live-exit window are restored onto the cap so we keep watching
        after kickoff. Other session caps still reset.

        `thesis_only` loads paper/pregame uniques as a side to confirm, not as
        live fills. Live can still take that same side when makers lean it.
        """
        now = int(now if now is not None else time.time())
        last: dict[tuple[str, str], dict[str, Any]] = {}
        for row in rows:
            market = str(row.get("market") or "")
            side = str(row.get("side") or "")
            if market and side:
                last[(market, side)] = row
        cancelled = {
            market
            for (market, _side), row in last.items()
            if str(row.get("action") or "") == Action.CANCEL.value
        }
        if not thesis_only and not self.settings.dry_run:
            for row in rows:
                action = str(row.get("action") or "")
                if action not in {a.value for a in _TRADE_ACTIONS}:
                    continue
                style = str(row.get("style") or "")
                if style in (STYLE_MM, STYLE_TENNIS_DOG):
                    continue
                if not row_live_filled(row):
                    continue
                market = str(row.get("market") or "")
                side = str(row.get("side") or "")
                if not market or not side or market in cancelled:
                    continue
                try:
                    side_enum = Side(side)
                except ValueError:
                    continue
                amount = _row_fill_units(row)
                if amount > 0:
                    self.exposure.add(market, side_enum, amount)
                    self.quoted.add(market)
        grace = float(self.settings.tennis_dog_live_hours or 0) * 3600.0
        for (market, side), row in last.items():
            action = str(row.get("action") or "")
            if action == Action.CANCEL.value:
                continue
            if action not in {a.value for a in _TRADE_ACTIONS}:
                continue
            try:
                side_enum = Side(side)
            except ValueError:
                side_enum = None
            if side_enum is not None:
                self._add_team_lock(market, soccer_team_lock_token_from_row(row))
            style = str(row.get("style") or "")
            if style:
                self.quoted_style[market] = style
            event_id = str(row.get("event_id") or "")
            if event_id:
                self.event_markets.setdefault(event_id, set()).add(market)
            if thesis_only:
                self.thesis_sides[market] = side
                continue
            self.chosen_sides.add((market, side))
            filled = bool(self.settings.dry_run) or row_live_filled(row)
            if filled:
                self.joined_sides.add((market, side))
                kickoff = int(row.get("game_time") or 0)
                if not self.settings.dry_run and market in self.quoted:
                    self.quoted_at.setdefault(market, float(row.get("ts") or now))
                    self.quoted_kickoff.setdefault(market, kickoff)
            else:
                oids = order_ids(row.get("result"))
                if oids:
                    self.pending_order_ids[market] = oids
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

    def allow(self, signal: Signal, stake: int | None = None) -> str | None:
        """Return a rejection reason, or None if the signal may trade."""
        amount = stake if stake is not None else self.stake()
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
        if self.exposure.total() + amount > max_total:
            return "max total exposure"
        if self.exposure.net(market_hash) + amount > max_mkt:
            return "max per-market exposure"
        thesis = self.thesis_for(market_hash)
        if (
            thesis is not None
            and signal.side is not thesis
            and (signal.style or "") != STYLE_MM
        ):
            return "against pregame unique"
        key = (market_hash, signal.side.value)
        if key in self.joined_sides:
            extra = self.stake_for(signal)
            if extra is None or not self._kelly_topup_room(market_hash):
                return "already on this side"
        if self.settings.one_side_per_market and any(
            item[0] == market_hash and item != key for item in (self.joined_sides | self.chosen_sides)
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
        token = soccer_team_lock_token(signal.market, signal.side)
        if (
            self.settings.one_side_per_event
            and token
            and token in self.locked_teams
            and all(item[0] != market_hash for item in self.chosen_sides)
            and self.thesis_for(market_hash) is not signal.side
        ):
            return "already on this team"
        return None

    def _add_team_lock(self, market_hash: str, token: tuple[str, ...] | None) -> None:
        if not token:
            return
        self.market_team_tokens[market_hash] = token
        self.locked_teams.add(token)

    def _drop_team_lock(self, market_hash: str) -> None:
        self.market_team_tokens.pop(market_hash, None)
        self.locked_teams = set(self.market_team_tokens.values())

    def thesis_for(self, market_hash: str) -> Side | None:
        """Pregame/paper unique, or the live side we already picked."""
        raw = self.thesis_sides.get(market_hash)
        if raw:
            try:
                return Side(raw)
            except ValueError:
                pass
        return self.chosen_side(market_hash)

    def chosen_side(self, market_hash: str) -> Side | None:
        for market, side in self.chosen_sides:
            if market == market_hash:
                try:
                    return Side(side)
                except ValueError:
                    return None
        return None

    def is_filled(self, market_hash: str, side: Side) -> bool:
        return (market_hash, side.value) in self.joined_sides

    def needs_live_entry(self, market_hash: str) -> Side | None:
        side = self.chosen_side(market_hash)
        if side is None:
            return None
        if not self.is_filled(market_hash, side):
            return side
        if self._kelly_topup_room(market_hash):
            return side
        return None

    def mark_chosen(self, signal: Signal) -> None:
        market_hash = signal.market.market_hash
        self.chosen_sides.add((market_hash, signal.side.value))
        self.thesis_sides[market_hash] = signal.side.value
        if signal.style:
            self.quoted_style[market_hash] = signal.style
        event_id = str(signal.market.event_id or "")
        if event_id:
            self.event_markets.setdefault(event_id, set()).add(market_hash)
        self.quoted_kickoff[market_hash] = int(signal.market.game_time or 0)
        self._add_team_lock(market_hash, soccer_team_lock_token(signal.market, signal.side))

    def record(self, signal: Signal, stake: int | None = None) -> None:
        market_hash = signal.market.market_hash
        if signal.action is Action.CANCEL:
            self._drop_slot(market_hash)
            self.joined_sides = {item for item in self.joined_sides if item[0] != market_hash}
            self.chosen_sides = {item for item in self.chosen_sides if item[0] != market_hash}
            self.thesis_sides.pop(market_hash, None)
            self.pending_order_ids.pop(market_hash, None)
            self.quoted_style.pop(market_hash, None)
            self._drop_team_lock(market_hash)
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
            self.chosen_sides.add((market_hash, signal.side.value))
            self.thesis_sides[market_hash] = signal.side.value
            self.pending_order_ids.pop(market_hash, None)
            self._add_team_lock(market_hash, soccer_team_lock_token(signal.market, signal.side))

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


def _row_fill_units(row: dict[str, Any]) -> int:
    raw = row.get("stake")
    try:
        stake = int(str(raw).replace(",", "").strip()) if raw not in (None, "") else 0
    except (TypeError, ValueError):
        stake = 0
    filled = live_filled_base_units(row.get("result"), stake or None)
    return filled if filled > 0 else stake
