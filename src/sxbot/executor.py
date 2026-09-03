from __future__ import annotations

import json
import logging
import secrets
import time
from pathlib import Path
from typing import Any

from sxbot.config import Settings
from sxbot.journal import paper_log_for
from sxbot.kelly import shadow_kelly_usdc
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
        result = self.client.create_orders([order], wait=True)
        record["result"] = result
        record["clientOrderId"] = order.get("clientOrderId")
        log.info("LIVE %s %s -> %s", signal.action.value, signal.market.label, result)
        self._append(record, signal)
        return record

    def _sign_order(self, signal: Signal, stake: int, time_in_force: str) -> dict[str, Any]:
        from eth_account.messages import encode_typed_data

        account = self._account
        salt = "0x" + secrets.token_hex(32)
        expiry = int(time.time()) + 3600
        unsigned = {
            "marketHash": signal.market.market_hash,
            "baseToken": self.meta.base_token,
            "totalBetSize": str(stake),
            "percentageOdds": str(signal.maker_odds),
            "salt": salt,
            "expiry": expiry,
            "maker": account.address,
            "isMakerBettingOutcomeOne": signal.side.is_outcome_one,
        }
        message = {
            **unsigned,
            "totalBetSize": int(unsigned["totalBetSize"]),
            "percentageOdds": int(unsigned["percentageOdds"]),
            "salt": int(unsigned["salt"], 16),
        }
        signable = encode_typed_data(self.meta.domain, ORDER_TYPES, message)
        signed = account.sign_message(signable)
        client_order_id = f"sxbot-{signal.market.market_hash[2:10]}-{signal.side.value[:3]}-{int(time.time())}"
        return {
            **unsigned,
            "timeInForce": time_in_force,
            "orderSignature": signed.signature.to_0x_hex(),
            "clientOrderId": client_order_id[:64],
        }

    def _stamp_books(self, record: dict[str, Any], signal: Signal) -> None:
        """Keep a $5 flat book and a ⅝-Kelly shadow on every paper row."""
        record["flat_stake_usdc"] = float(self.settings.stake_usdc)
        record["bankroll_usdc"] = float(self.settings.bankroll_usdc)
        record["kelly_fraction"] = float(self.settings.kelly_fraction)
        if signal.fair_odds:
            record["fair_pct"] = to_percent(signal.fair_odds)
        else:
            record.setdefault("fair_pct", None)
        record["kelly_stake_usdc"] = shadow_kelly_usdc(self.settings, signal)

    def _append(self, record: dict[str, Any], signal: Signal | None = None) -> None:
        path = self.paper_path
        if not self._paper_path_locked:
            style = (signal.style if signal is not None else "") or "legacy"
            path = paper_log_for(self.paper_path, style)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record) + "\n")


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
