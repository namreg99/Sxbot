"""Paper-trade *like* labeled wallets while V2 still attributes fills.

This is not a V3 product. After August 25 you cannot see who took. Until then
we copy **new** taker fills (Gary / Botswana style) at *our* stake, and
optionally rest on a maker's current quotes (HedgeHog style). Long-shot alts
are skipped by default — cypherprod's 7.84 dogs are not the strategy.

First poll only primes seen fill hashes so we do not dump the last hour into
the paper log.
"""

from __future__ import annotations

import logging
import time

from sxbot.api import SxClient
from sxbot.config import Settings
from sxbot.executor import Executor
from sxbot.models import Action, Market, Signal, Side
from sxbot.units import decimal_odds, to_base_units, to_prob
from sxbot.v2 import is_resting
from sxbot.wallets import labeled_targets

log = logging.getLogger("sxbot.mimic")


def _decimal(odds: int) -> float:
    return decimal_odds(to_prob(odds)) if odds else 0.0


def should_copy_taker(
    raw: dict[str, Any],
    market: Market | None,
    settings: Settings,
) -> tuple[bool, str]:
    odds = int(raw.get("odds") or 0)
    dec = _decimal(odds)
    if dec <= 1.0:
        return False, "bad odds"
    if dec > settings.mimic_max_decimal:
        return False, f"longshot {dec:.2f} > {settings.mimic_max_decimal}"
    if market is None:
        return False, "missing market"
    if settings.sport_ids and market.sport_id not in settings.sport_ids:
        return False, f"sport {market.sport_id} not in universe"
    if market.is_live() and not settings.allow_live:
        return False, "live blocked (SX_ALLOW_LIVE=false)"
    return True, "ok"


def signal_from_taker_fill(market: Market, raw: dict[str, Any], *, reason: str) -> Signal:
    side = Side.OUTCOME_ONE if raw.get("bettingOutcomeOne") else Side.OUTCOME_TWO
    odds = int(raw.get("odds") or 0)
    return Signal(
        market=market,
        side=side,
        action=Action.TAKE_FLOW,
        maker_odds=odds,
        reason=reason,
        mid_move_bps=0,
        imbalance=0.0,
        confidence=0.5,
        motive="mimic_taker",
    )


def signal_from_maker_quote(market: Market, order: dict[str, Any], *, reason: str) -> Signal:
    side = Side.OUTCOME_ONE if order.get("isMakerBettingOutcomeOne") else Side.OUTCOME_TWO
    odds = int(order.get("percentageOdds") or 0)
    return Signal(
        market=market,
        side=side,
        action=Action.JOIN_MAKER,
        maker_odds=odds,
        reason=reason,
        mid_move_bps=0,
        imbalance=0.0,
        confidence=0.4,
        motive="mimic_maker",
    )


class MimicBot:
    def __init__(self, settings: Settings, client: SxClient) -> None:
        self.settings = settings
        self.client = client
        self.meta = client.metadata()
        self.executor = Executor(settings, self.meta, client)
        self.targets = labeled_targets(settings)
        self._seen_fills: set[str] = set()
        self._seen_orders: set[str] = set()
        self._primed = False
        self._markets: dict[str, Market] = {}

    def _market(self, market_hash: str) -> Market | None:
        if not market_hash:
            return None
        cached = self._markets.get(market_hash)
        if cached is not None:
            return cached
        found = self.client.find_markets([market_hash])
        if not found:
            return None
        market = Market.from_api(found[0])
        self._markets[market_hash] = market
        return market

    def _prime_taker(self, address: str) -> None:
        start = int(time.time()) - 6 * 3600
        for as_maker in (False, True):
            rows = self.client.v2_trades_for_bettor(
                address,
                as_maker=as_maker,
                start_date=start,
                per_page=100,
                pages=2,
            )
            for raw in rows:
                fill = str(raw.get("fillHash") or "")
                if fill:
                    self._seen_fills.add(fill)

    def _prime_maker(self, address: str) -> None:
        for order in self.client.v2_orders_for_maker(address):
            oid = str(order.get("orderHash") or order.get("hash") or "")
            if oid:
                self._seen_orders.add(oid)

    def step(self) -> int:
        if not self._primed:
            for _, address in self.targets:
                self._prime_taker(address)
                if self.settings.mimic_copy_makers:
                    self._prime_maker(address)
            self._primed = True
            log.info(
                "mimic primed %d fills / %d quotes across %d wallets — next poll copies new ones",
                len(self._seen_fills),
                len(self._seen_orders),
                len(self.targets),
            )
            return 0

        n = 0
        start = int(time.time()) - 30 * 60
        stake = to_base_units(self.settings.stake_usdc, self.meta.decimals)
        for label, address in self.targets:
            n += self._copy_taker(label, address, start, stake)
            if self.settings.mimic_copy_makers:
                n += self._copy_maker(label, address, stake)
        return n

    def _copy_taker(self, label: str, address: str, start: int, stake: int) -> int:
        rows = self.client.v2_trades_for_bettor(
            address,
            as_maker=False,
            start_date=start,
            per_page=100,
            pages=2,
        )
        n = 0
        for raw in rows:
            fill = str(raw.get("fillHash") or "")
            if not fill or fill in self._seen_fills:
                continue
            self._seen_fills.add(fill)
            market = self._market(str(raw.get("marketHash") or ""))
            ok, why = should_copy_taker(raw, market, self.settings)
            if not ok or market is None:
                log.debug("skip %s %s: %s", label, fill[:10], why)
                continue
            signal = signal_from_taker_fill(
                market,
                raw,
                reason=f"mimic {label} taker {_decimal(int(raw.get('odds') or 0)):.2f} {why}",
            )
            self.executor.execute(
                signal,
                stake,
                extra={
                    "source": "mimic",
                    "copied_wallet": label,
                    "copied_fill": fill,
                    "copied_stake_usdc": int(raw.get("stake") or 0) / 1e6,
                },
            )
            n += 1
        return n

    def _copy_maker(self, label: str, address: str, stake: int) -> int:
        n = 0
        for order in self.client.v2_orders_for_maker(address):
            if not is_resting(order):
                continue
            oid = str(order.get("orderHash") or order.get("hash") or "")
            if not oid or oid in self._seen_orders:
                continue
            self._seen_orders.add(oid)
            market = self._market(str(order.get("marketHash") or ""))
            if market is None:
                continue
            if settings_skips_market(self.settings, market):
                continue
            signal = signal_from_maker_quote(
                market, order, reason=f"mimic {label} resting quote"
            )
            self.executor.execute(
                signal,
                stake,
                extra={"source": "mimic", "copied_wallet": label, "copied_order": oid},
            )
            n += 1
        return n

    def run(self) -> None:
        log.info(
            "mimic paper bot — copying %s at %.0f USDC (dry_run=%s, max_decimal=%.2f)",
            ", ".join(label for label, _ in self.targets),
            self.settings.stake_usdc,
            self.settings.dry_run,
            self.settings.mimic_max_decimal,
        )
        self.step()  # prime
        while True:
            n = self.step()
            if n:
                log.info("mimic copied %d new fill(s)", n)
            time.sleep(self.settings.poll_seconds)


def settings_skips_market(settings: Settings, market: Market) -> bool:
    if settings.sport_ids and market.sport_id not in settings.sport_ids:
        return True
    if market.is_live() and not settings.allow_live:
        return True
    return False
