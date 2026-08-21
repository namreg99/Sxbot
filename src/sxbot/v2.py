"""Aggregate V2 per-order books into the same Book the V3 classifier uses.

Mainnet is still V2 until the August 25, 2026 cutover. V2 `GET /orders` returns
one row per resting quote (with a maker address we ignore). Summing those rows
by price/side produces the anonymous V3 snapshot shape, so `flow.classify`
does not care which protocol produced the book.

Drop this module after V3 is live — the bot already speaks V3 snapshots.
"""

from __future__ import annotations

import time
from collections import defaultdict
from typing import Any, Iterable

from sxbot.models import Book, Level, PublicTrade


def remaining_size(order: dict[str, Any]) -> int:
    total = int(order.get("totalBetSize") or 0)
    filled = int(order.get("fillAmount") or 0)
    pending = int(order.get("pendingFillAmount") or 0)
    return max(total - filled - pending, 0)


def is_resting(order: dict[str, Any], now: int | None = None) -> bool:
    if (order.get("orderStatus") or "ACTIVE") != "ACTIVE":
        return False
    now = now if now is not None else int(time.time())
    expiry = int(order.get("apiExpiry") or 0)
    if expiry and expiry < now:
        return False
    return remaining_size(order) > 0


def book_from_v2_orders(
    market_hash: str,
    orders: Iterable[dict[str, Any]],
    *,
    version: str,
    now: int | None = None,
) -> Book:
    one: dict[int, int] = {}
    two: dict[int, int] = {}
    for order in orders:
        raw_hash = order.get("marketHash")
        if raw_hash and raw_hash != market_hash:
            continue
        if not is_resting(order, now):
            continue
        odds = int(order["percentageOdds"])
        size = remaining_size(order)
        bucket = one if order.get("isMakerBettingOutcomeOne") else two
        bucket[odds] = bucket.get(odds, 0) + size

    def levels(bucket: dict[int, int]) -> tuple[Level, ...]:
        return tuple(
            Level(odds, size) for odds, size in sorted(bucket.items(), key=lambda item: -item[0])
        )

    return Book(market_hash, version, levels(one), levels(two))


def books_from_v2_orders(
    orders: list[dict[str, Any]] | dict[str, Any],
    *,
    version: str,
    now: int | None = None,
    market_hashes: Iterable[str] | None = None,
) -> dict[str, Book]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    if isinstance(orders, dict):
        for market_hash, rows in orders.items():
            if isinstance(rows, list):
                grouped[str(market_hash)].extend(rows)
    else:
        for order in orders:
            market_hash = order.get("marketHash")
            if market_hash:
                grouped[market_hash].append(order)
    wanted = list(market_hashes) if market_hashes is not None else list(grouped)
    return {
        market_hash: book_from_v2_orders(
            market_hash, grouped.get(market_hash, []), version=version, now=now
        )
        for market_hash in wanted
    }


def _trade_id(raw: dict[str, Any]) -> str:
    return str(raw.get("fillHash") or raw.get("_id") or raw.get("orderHash") or "")


def public_trades_from_v2(rows: Iterable[dict[str, Any]]) -> list[PublicTrade]:
    """Map V2 fills onto the anonymized V3 tape.

    V2 rows are per-bettor and still carry addresses. We throw the address
    away and keep the *taker's* outcome so `flow` can tell a lift from a
    maker reprice — the same signal V3's public tape gives us.
    """
    preferred: dict[str, dict[str, Any]] = {}
    for raw in rows:
        trade_id = _trade_id(raw)
        if not trade_id:
            continue
        existing = preferred.get(trade_id)
        if existing is None or (existing.get("maker") and not raw.get("maker")):
            preferred[trade_id] = raw

    out: list[PublicTrade] = []
    for trade_id, raw in preferred.items():
        maker = bool(raw.get("maker"))
        betting_one = bool(raw.get("bettingOutcomeOne"))
        taker_one = betting_one if not maker else (not betting_one)
        bet_time = raw.get("createdAt") or str(raw.get("betTime") or "")
        out.append(
            PublicTrade(
                trade_id=trade_id,
                market_hash=str(raw.get("marketHash") or ""),
                is_betting_outcome_one=taker_one,
                stake=int(raw.get("stake") or 0),
                odds=int(raw.get("odds") or 0),
                bet_time=str(bet_time),
            )
        )
    return out
