from __future__ import annotations

import json
import logging
import secrets
import time
from pathlib import Path
from typing import Any

from sxbot.config import Settings
from sxbot.journal import live_log_for, paper_log_for
from sxbot.fills import live_entry_filled, order_ids
from sxbot.kelly import UNIQUE_BANKROLL_USDC, UNIQUE_FLAT_USDC, UNIQUE_KELLY_FRACTION, tracker_kelly_usdc
from sxbot.models import Action, ExchangeMeta, Signal
from sxbot.units import to_percent, to_usdc

log = logging.getLogger("sxbot.executor")

ORDER_TYPES = {
    "Order": [
        {"name": "marketHash", "type": "bytes32"},
        {"name": "baseToken", "type": "address"},
        {"name": "totalBetSize", "type": "uint256"},
        {"name": "percentageOdds", "type": "uint256"},
        {"name": "salt", "type": "uint256"},
        {"name": "expiry", "type": "uint256"},
        {"name": "maker", "type": "address"},
        {"name": "isMakerBettingOutcomeOne", "type": "bool"},
    ]
}


class Executor:
    def __init__(
        self,
        settings: Settings,
        meta: ExchangeMeta,
        client: Any | None = None,
        paper_path: str | Path | None = None,
    ) -> None:
        self.settings = settings
        self.meta = meta
        self.client = client
        self.paper_path = Path(paper_path or settings.paper_log)
        self._paper_path_locked = paper_path is not None
        self._account = None
        if not settings.dry_run:
            self._account = _load_account(settings.private_key)

    def execute(
        self,
        signal: Signal,
        stake: int,
        extra: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        record = {
            "ts": time.time(),
            "dry_run": self.settings.dry_run,
            "action": signal.action.value,
            "side": signal.side.value,
            "market": signal.market.market_hash,
            "label": signal.market.label,
            "league": signal.market.league_label,
            "odds": str(signal.maker_odds),
            "odds_pct": to_percent(signal.maker_odds),
            "stake": str(stake),
            "stake_usdc": to_usdc(stake, self.meta.decimals),
            "reason": signal.reason,
            "confidence": signal.confidence,
            "mid_move_bps": signal.mid_move_bps,
            "imbalance": signal.imbalance,
            "phase": signal.market.phase(),
            "motive": signal.motive,
            "game_time": signal.market.game_time,
            "outcome_one": signal.market.outcome_one,
            "outcome_two": signal.market.outcome_two,
            "event_id": signal.market.event_id,
            "style": signal.style or "",
        }
        if extra:
            record.update(extra)
        self._stamp_books(record, signal)
        if self.settings.dry_run:
            log.info(
                "PAPER %s %s %s @ %.3f%%  %s",
                signal.action.value,
                signal.side.value,
                signal.market.label,
                record["odds_pct"],
                signal.reason,
            )
            self._append(record, signal)
            record["live_filled"] = True
            return record

        if signal.action is Action.MM_FILL:
            self._append(record, signal)
            return record

        if signal.action is Action.CANCEL:
            if self.client is None:
                raise RuntimeError("live cancel requires an API client")
            result = self.client.cancel_all()
            record["result"] = result
            self._append(record, signal)
            return record

        if self.client is None or self._account is None:
            raise RuntimeError("live trading requires SX_API_KEY and SX_PRIVATE_KEY")

        time_in_force = "GTC" if signal.action is Action.JOIN_MAKER else "IOC"
        order = self._sign_order(signal, stake, time_in_force)
        try:
            result = self.client.create_orders([order], wait=True)
        except Exception as exc:
            from sxbot.api import SxApiError

            if isinstance(exc, SxApiError):
                log.error("LIVE HTTP %s %s %s", exc.status, exc.url, exc.body[:500])
            raise
        record["result"] = result
        record["clientOrderId"] = order.get("clientOrderId")
        filled = live_entry_filled(result, stake)
        record["live_filled"] = filled
        oids = order_ids(result)
        if oids:
            record["order_ids"] = oids
        tif = "GTC offer" if signal.action is Action.JOIN_MAKER else "IOC take (will not rest as an offer)"
        if filled:
            log.info("LIVE %s %s [%s] FILLED -> %s", signal.action.value, signal.market.label, tif, result)
        else:
            log.info(
                "LIVE %s %s [%s] NO FILL (unique side kept, will retry) -> %s",
                signal.action.value,
                signal.market.label,
                tif,
                result,
            )
        self._append(record, signal)
        return record

    def _sign_order(self, signal: Signal, stake: int, time_in_force: str) -> dict[str, Any]:
        from eth_account.messages import encode_typed_data
        from eth_utils import to_checksum_address

        account = self._account
        salt_hex = "0x" + secrets.token_hex(32)
        expiry = int(time.time()) + 3600
        market_hash = _bytes32(signal.market.market_hash)
        base_token = to_checksum_address(self.meta.base_token)
        maker = to_checksum_address(account.address)
        domain = {
            "name": self.meta.domain["name"],
            "version": str(self.meta.domain["version"]),
            "chainId": int(self.meta.domain.get("chainId") or self.meta.chain_id),
            "verifyingContract": to_checksum_address(self.meta.domain["verifyingContract"]),
        }
        message = {
            "marketHash": market_hash,
            "baseToken": base_token,
            "totalBetSize": int(stake),
            "percentageOdds": int(signal.maker_odds),
            "salt": int(salt_hex, 16),
            "expiry": expiry,
            "maker": maker,
            "isMakerBettingOutcomeOne": signal.side.is_outcome_one,
        }
        structured = {
            "types": {
                "EIP712Domain": [
                    {"name": "name", "type": "string"},
                    {"name": "version", "type": "string"},
                    {"name": "chainId", "type": "uint256"},
                    {"name": "verifyingContract", "type": "address"},
                ],
                "Order": ORDER_TYPES["Order"],
            },
            "primaryType": "Order",
            "domain": domain,
            "message": message,
        }
        signable = encode_typed_data(full_message=structured)
        signed = account.sign_message(signable)
        client_order_id = f"sxbot-{market_hash[2:10]}-{signal.side.value[:3]}-{int(time.time())}"
        return {
            "marketHash": market_hash,
            "baseToken": base_token,
            "totalBetSize": str(stake),
            "percentageOdds": str(signal.maker_odds),
            "salt": salt_hex,
            "expiry": expiry,
            "maker": maker,
            "isMakerBettingOutcomeOne": signal.side.is_outcome_one,
            "timeInForce": time_in_force,
            "orderSignature": signed.signature.to_0x_hex(),
            "clientOrderId": client_order_id[:64],
        }

    def _stamp_books(self, record: dict[str, Any], signal: Signal) -> None:
        """$5 unique follow + ⅝-Kelly/$25 shadow, even when live size is $1–$4."""
        record["flat_stake_usdc"] = UNIQUE_FLAT_USDC
        record["bankroll_usdc"] = UNIQUE_BANKROLL_USDC
        record["kelly_fraction"] = UNIQUE_KELLY_FRACTION
        if signal.fair_odds:
            record["fair_pct"] = to_percent(signal.fair_odds)
        else:
            record.setdefault("fair_pct", None)
        record["kelly_stake_usdc"] = tracker_kelly_usdc(signal)

    def _append(self, record: dict[str, Any], signal: Signal | None = None) -> None:
        path = self.paper_path
        if not self._paper_path_locked:
            style = (signal.style if signal is not None else "") or "legacy"
            if self.settings.dry_run:
                path = paper_log_for(self.paper_path, style)
            else:
                path = live_log_for(self.paper_path, style)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record) + "\n")


def _bytes32(value: str) -> str:
    raw = value[2:] if value.lower().startswith("0x") else value
    raw = raw.lower()
    if len(raw) > 64 or any(c not in "0123456789abcdef" for c in raw):
        raise ValueError("marketHash is not 32-byte hex")
    return "0x" + raw.rjust(64, "0")


def normalize_private_key(private_key: str) -> str:
    """Strip copy-paste junk. eth_account wants 32-byte hex, optional 0x."""
    key = private_key.strip().strip("\ufeff").strip("\"'“”‘’`")
    if key.lower().startswith("0x"):
        key = key[2:]
    key = "".join(key.split())
    if not key:
        raise ValueError("SX_PRIVATE_KEY is empty")
    if any(c not in "0123456789abcdefABCDEF" for c in key):
        raise ValueError("SX_PRIVATE_KEY is not hexadecimal (quotes, spaces, or extra text)")
    if len(key) != 64:
        raise ValueError(f"SX_PRIVATE_KEY must be 64 hex characters, got {len(key)}")
    return "0x" + key.lower()


def _load_account(private_key: str | None) -> Any:
    if not private_key:
        raise RuntimeError("SX_PRIVATE_KEY is required for live trading")
    try:
        from eth_account import Account
    except ImportError as exc:
        raise RuntimeError("Install live-trading extras: pip install 'sxbot[trade]'") from exc
    return Account.from_key(normalize_private_key(private_key))
