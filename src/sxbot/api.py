from __future__ import annotations

import time
from typing import Any, Iterator

import httpx

from sxbot.models import Book, ExchangeMeta, Market, PublicTrade
from sxbot.rollout import V3_MAINNET_LIVE_AT, v3_mainnet_is_live
from sxbot.v2 import books_from_v2_orders, public_trades_from_v2


class SxApiError(RuntimeError):
    def __init__(self, status: int, url: str, body: str) -> None:
        self.status = status
        self.url = url
        self.body = body
        extra = ""
        if status == 403 and "api.sx.bet" in url and not v3_mainnet_is_live():
            extra = (
                f" V3 is testnet-only until {V3_MAINNET_LIVE_AT.isoformat()} "
                "(docs: 'Do not point a production integration at V3 until August 25th "
                "at 10:00 AM EST'). Sxbot reads mainnet V2 GET /orders until then; "
                "do not call V3 book/trade routes on api.sx.bet before the cutover."
            )
        super().__init__(f"HTTP {status} {url}: {body[:300]}{extra}")


class SxClient:
    def __init__(
        self,
        base_url: str,
        api_key: str | None = None,
        timeout: float = 20.0,
        user_agent: str = "sxbot/0.1",
    ) -> None:
        headers = {
            "Accept": "application/json",
            "User-Agent": user_agent,
        }
        if api_key:
            headers["x-sx-api-key"] = api_key
        self.base_url = base_url.rstrip("/")
        self._http = httpx.Client(base_url=self.base_url, headers=headers, timeout=timeout)

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> SxClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def _get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        delay = 2.0
        last: httpx.Response | None = None
        for _ in range(8):
            response = self._http.get(path, params=params)
            last = response
            if response.status_code == 429:
                time.sleep(delay)
                delay = min(delay * 2, 45.0)
                continue
            return self._parse(response)
        assert last is not None
        raise SxApiError(last.status_code, str(last.request.url), last.text)

    def _parse(self, response: httpx.Response) -> Any:
        if response.status_code >= 400:
            raise SxApiError(response.status_code, str(response.request.url), response.text)
        payload = response.json()
        if isinstance(payload, dict) and payload.get("status") == "failure":
            raise SxApiError(response.status_code, str(response.request.url), response.text)
        if isinstance(payload, dict) and "data" in payload:
            return payload["data"]
        return payload

    def metadata(self) -> ExchangeMeta:
        return ExchangeMeta.from_api(self._get("/metadata/obv3"))

    def sports(self) -> list[dict[str, Any]]:
        data = self._get("/sports")
        return list(data) if isinstance(data, list) else []

    def active_markets(
        self,
        *,
        only_main_line: bool = True,
        sport_ids: tuple[int, ...] = (),
        league_id: int | None = None,
        types: tuple[int, ...] = (),
        live_only: bool | None = None,
        page_size: int = 50,
        limit: int | None = None,
    ) -> Iterator[Market]:
        params: dict[str, Any] = {"pageSize": page_size}
        if only_main_line:
            params["onlyMainLine"] = "true"
        if sport_ids:
            params["sportIds"] = ",".join(str(s) for s in sport_ids)
        if league_id is not None:
            params["leagueId"] = league_id
        if types:
            params["type"] = ",".join(str(t) for t in types)
        if live_only is True:
            params["liveOnly"] = "true"
        next_key: str | None = None
        while True:
            if next_key:
                params["paginationKey"] = next_key
            data = self._get("/markets/active", params)
            rows = data.get("markets") or []
            for row in rows:
                yield Market.from_api(row)
                if limit is not None:
                    limit -= 1
                    if limit <= 0:
                        return
            next_key = data.get("nextKey")
            if not next_key or not rows:
                break

    def snapshot(self, market_hash: str, *, taker: bool = False) -> Book:
        params: dict[str, Any] = {"marketHash": market_hash}
        if taker:
            params["showTakerPerspective"] = "true"
        return Book.from_api(self._get("/orderbook-v3/snapshot", params))

    def public_trades(self, market_hash: str | None = None, per_page: int = 50) -> list[PublicTrade]:
        params: dict[str, Any] = {"perPage": per_page}
        if market_hash:
            params["marketHash"] = market_hash
        data = self._get("/trades-v3/public", params)
        return [PublicTrade.from_api(row) for row in data.get("trades") or []]

    def v2_orders(
        self,
        market_hashes: list[str],
        *,
        per_page: int = 100,
        batch_size: int = 12,
    ) -> list[dict[str, Any]]:
        """Resting V2 quotes. Flat list; group by marketHash yourself."""
        out: list[dict[str, Any]] = []
        for i in range(0, len(market_hashes), batch_size):
            chunk = market_hashes[i : i + batch_size]
            page = 0
            while page < 20:
                data = self._get(
                    "/orders",
                    params={
                        "marketHashes": ",".join(chunk),
                        "perPage": per_page,
                        "page": page,
                    },
                )
                rows = _as_order_rows(data)
                out.extend(rows)
                if len(rows) < per_page:
                    break
                page += 1
        return out

    def v2_trades(
        self,
        market_hashes: list[str],
        *,
        per_page: int = 50,
        batch_size: int = 12,
    ) -> list[PublicTrade]:
        if not market_hashes:
            return []
        rows: list[dict[str, Any]] = []
        for i in range(0, len(market_hashes), batch_size):
            chunk = market_hashes[i : i + batch_size]
            data = self._get(
                "/trades",
                params={"marketHashes": ",".join(chunk), "perPage": per_page},
            )
            if isinstance(data, dict):
                rows.extend(data.get("trades") or [])
            elif isinstance(data, list):
                rows.extend(data)
        return public_trades_from_v2(rows)

    def v2_books(self, market_hashes: list[str], *, version: str | None = None) -> dict[str, Book]:
        orders = self.v2_orders(market_hashes) if market_hashes else []
        return books_from_v2_orders(
            orders, version=version, market_hashes=market_hashes
        )

    def v2_orders_for_maker(self, maker: str, *, per_page: int = 100) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        page = 0
        while page < 10:
            data = self._get(
                "/orders",
                params={"maker": maker, "perPage": per_page, "page": page},
            )
            rows = _as_order_rows(data)
            out.extend(rows)
            if len(rows) < per_page:
                break
            page += 1
        return out

    def v2_trades_for_bettor(
        self,
        bettor: str,
        *,
        as_maker: bool,
        per_page: int = 100,
        pages: int = 2,
        start_date: int | None = None,
        end_date: int | None = None,
    ) -> list[dict[str, Any]]:
        """Fills for one address. Oldest-first from start_date.

        The API returns `nextKey` but the *request* cursor is `paginationKey`
        (same as /markets/active). Passing nextKey as nextKey is a no-op and
        repeats page 1 forever.
        """
        out: list[dict[str, Any]] = []
        pagination_key: str | None = None
        seen_cursors: set[str] = set()
        for _ in range(pages):
            params = v2_trade_query_params(
                bettor,
                as_maker=as_maker,
                per_page=per_page,
                start_date=start_date,
                end_date=end_date,
                pagination_key=pagination_key,
            )
            data = self._get("/trades", params)
            if isinstance(data, dict):
                rows = data.get("trades") or []
                out.extend(rows)
                pagination_key = data.get("nextKey")
            elif isinstance(data, list):
                out.extend(data)
                pagination_key = None
            else:
                break
            if not pagination_key:
                break
            if pagination_key in seen_cursors:
                break
            seen_cursors.add(pagination_key)
            time.sleep(0.25)
        return out

    def find_markets(self, market_hashes: list[str]) -> list[dict[str, Any]]:
        """Active or settled markets by hash. Up to 30 hashes per request."""
        out: list[dict[str, Any]] = []
        for i in range(0, len(market_hashes), 30):
            chunk = market_hashes[i : i + 30]
            data = self._get("/markets/find", params={"marketHashes": ",".join(chunk)})
            out.extend(as_market_rows(data))
        return out

    def create_orders(self, orders: list[dict[str, Any]], *, wait: bool = True) -> dict[str, Any]:
        response = self._http.post(
            "/orders-v3",
            json={"orders": orders, "waitForOutcome": wait},
        )
        return self._parse(response)

    def cancel_orders(self, order_ids: list[str]) -> dict[str, Any]:
        response = self._http.request(
            "DELETE",
            "/orders-v3",
            json={"orders": [{"orderId": oid} for oid in order_ids]},
        )
        return self._parse(response)

    def cancel_all(self) -> dict[str, Any]:
        response = self._http.request("DELETE", "/orders-v3/all")
        return self._parse(response)

    def my_orders(self, market_hash: str | None = None) -> list[dict[str, Any]]:
        params: dict[str, Any] = {"perPage": 100}
        if market_hash:
            params["marketHash"] = market_hash
        data = self._get("/orders-v3", params)
        return list(data.get("orders") or [])

    def heartbeat(self, timeout_seconds: int) -> dict[str, Any]:
        response = self._http.post("/heartbeat/v3", json={"timeoutSeconds": timeout_seconds})
        return self._parse(response)

    def proxy(self) -> dict[str, Any]:
        return self._get("/user/proxy")

    def balance(self) -> dict[str, Any]:
        return self._get("/user/balance-v3")


def v2_trade_query_params(
    bettor: str,
    *,
    as_maker: bool,
    per_page: int = 100,
    start_date: int | None = None,
    end_date: int | None = None,
    pagination_key: str | None = None,
) -> dict[str, Any]:
    params: dict[str, Any] = {
        "bettor": bettor,
        "maker": "true" if as_maker else "false",
        "perPage": per_page,
    }
    if start_date is not None:
        params["startDate"] = start_date
    if end_date is not None:
        params["endDate"] = end_date
    if pagination_key:
        params["paginationKey"] = pagination_key
    return params


def as_market_rows(data: Any) -> list[dict[str, Any]]:
    """Unwrap GET /markets/find payloads (list, {markets: [...]}, or a single row)."""
    if isinstance(data, list):
        return [row for row in data if isinstance(row, dict) and row.get("marketHash")]
    if isinstance(data, dict):
        if data.get("marketHash"):
            return [data]
        for key in ("markets", "data"):
            inner = data.get(key)
            if isinstance(inner, list):
                return as_market_rows(inner)
            if isinstance(inner, dict):
                return as_market_rows(inner)
    return []


def index_markets(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Key find_markets rows by lowercased hash so paper logs match the API."""
    out: dict[str, dict[str, Any]] = {}
    for row in rows:
        market_hash = str(row.get("marketHash") or "").lower()
        if market_hash:
            out[market_hash] = row
    return out


def lookup_market(markets: dict[str, dict[str, Any]], market_hash: str) -> dict[str, Any] | None:
    if not market_hash:
        return None
    hit = markets.get(market_hash)
    if hit is not None:
        return hit
    lower = market_hash.lower()
    hit = markets.get(lower)
    if hit is not None:
        return hit
    for key, row in markets.items():
        if str(key).lower() == lower:
            return row
    return None


def _as_order_rows(data: Any) -> list[dict[str, Any]]:
    if isinstance(data, list):
        return [row for row in data if isinstance(row, dict)]
    if isinstance(data, dict):
        rows: list[dict[str, Any]] = []
        for value in data.values():
            if isinstance(value, list):
                rows.extend(row for row in value if isinstance(row, dict))
        return rows
    return []
