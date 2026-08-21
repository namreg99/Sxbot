from __future__ import annotations

from typing import Any, Iterator

import httpx

from sxbot.models import Book, ExchangeMeta, Market, PublicTrade
from sxbot.rollout import V3_MAINNET_LIVE_AT, v3_mainnet_is_live


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
                "at 10:00 AM EST'). Use SX_API_BASE=https://api.toronto.sx.bet until then."
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
        response = self._http.get(path, params=params)
        return self._parse(response)

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
