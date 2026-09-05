"""SX Bet V3 mainnet rollout window.

Per https://docs.sx.bet/developers/new-in-v3 : "V3 is currently live on
testnet only. Do not point a production integration at V3 until August 25th
at 10:00 AM EST." Until that moment, V3-only mainnet routes such as
GET /orderbook-v3/snapshot and GET /trades-v3/public return 403 while
always-on reference routes (GET /metadata/obv3, GET /markets/active) keep
working — that split is the tell that this is a rollout gate, not an IP/WAF
block.

The source doc says "EST" (not "EDT"); since real-world late August is
Eastern Daylight Time, this constant takes the doc literally as UTC-5.
Treat it as informational — the exchange's own gate is authoritative, this
is only used to produce a clearer error message and startup warning.
"""

from __future__ import annotations

from datetime import datetime, timezone

V3_MAINNET_LIVE_AT = datetime(2026, 8, 25, 15, 0, tzinfo=timezone.utc)  # 10:00 AM EST (UTC-5)


MAINNET_API = "https://api.sx.bet"
TESTNET_API = "https://api.toronto.sx.bet"


def v3_mainnet_is_live(now: datetime | None = None) -> bool:
    now = now or datetime.now(timezone.utc)
    return now >= V3_MAINNET_LIVE_AT


def uses_v2_books(
    api_base: str,
    now: datetime | None = None,
    book_source: str = "auto",
) -> bool:
    """True when we should aggregate V2 `GET /orders` instead of V3 snapshots.

    Testnet is already V3. Mainnet stays on V2 until the documented cutover,
    unless `book_source` forces one protocol.
    """
    source = (book_source or "auto").strip().lower()
    if source == "v2":
        return True
    if source == "v3":
        return False
    if "toronto" in api_base:
        return False
    return not v3_mainnet_is_live(now)
