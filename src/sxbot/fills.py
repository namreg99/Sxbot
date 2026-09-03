"""Did SX actually put us in the game, or only accept/rest/miss the order?"""

from __future__ import annotations

from typing import Any

_FILLED_STATES = {
    "fully_filled",
    "filled",
    "matched",
    "partially_filled",
    "partial",
}


def _int(raw: Any) -> int | None:
    if raw in (None, ""):
        return None
    try:
        return int(str(raw).replace(",", "").strip())
    except (TypeError, ValueError):
        return None


def _orders(result: Any) -> list[dict[str, Any]]:
    if not isinstance(result, dict):
        return []
    orders = result.get("orders")
    if isinstance(orders, list):
        return [item for item in orders if isinstance(item, dict)]
    return []


def order_ids(result: Any) -> list[str]:
    out: list[str] = []
    for order in _orders(result):
        oid = str(order.get("orderId") or "").strip()
        if oid:
            out.append(oid)
    return out


def live_filled_base_units(result: Any, stake: int | None = None) -> int:
    """Base units matched. 0 = RESTED / IOC miss / reject — not a live entry."""
    filled = 0
    for order in _orders(result):
        outcome = order.get("outcome") if isinstance(order.get("outcome"), dict) else {}
        state = str(outcome.get("state") or order.get("status") or "").strip().lower()
        remaining = _int(outcome.get("remainingAmount"))
        fill_amt = _int(outcome.get("filledAmount") or outcome.get("fillAmount") or order.get("fillAmount"))
        total = _int(order.get("totalBetSize") or order.get("stake") or stake)
        if fill_amt and fill_amt > 0:
            filled += fill_amt
            continue
        if remaining is not None and total is not None and remaining < total:
            filled += total - remaining
            continue
        if remaining == 0:
            filled += total or 0
            continue
        if state in _FILLED_STATES:
            if remaining in (None, 0):
                filled += total or 0
            elif total is not None:
                filled += max(total - remaining, 0)
    return filled


def live_entry_filled(result: Any, stake: int | None = None) -> bool:
    return live_filled_base_units(result, stake) > 0


def row_live_filled(row: dict[str, Any]) -> bool:
    if row.get("live_filled") is True:
        return True
    if row.get("live_filled") is False:
        return False
    if row.get("dry_run") is not False:
        return False
    stake = _int(row.get("stake"))
    return live_entry_filled(row.get("result"), stake)
