import httpx

from sxbot.api import SxApiError, SxClient
from sxbot.rollout import V3_MAINNET_LIVE_AT


def test_mainnet_403_before_cutover_explains_v3_rollout(monkeypatch) -> None:
    monkeypatch.setattr(
        "sxbot.api.v3_mainnet_is_live",
        lambda now=None: False,
    )
    err = SxApiError(403, "https://api.sx.bet/orderbook-v3/snapshot?marketHash=0x1", "Forbidden")
    assert "testnet-only" in str(err)
    assert "August 25th" in str(err)


def test_mainnet_403_after_cutover_has_no_rollout_hint(monkeypatch) -> None:
    monkeypatch.setattr(
        "sxbot.api.v3_mainnet_is_live",
        lambda now=None: True,
    )
    err = SxApiError(403, "https://api.sx.bet/orderbook-v3/snapshot?marketHash=0x1", "Forbidden")
    assert "testnet-only" not in str(err)


def test_testnet_403_has_no_rollout_hint() -> None:
    err = SxApiError(403, "https://api.toronto.sx.bet/orderbook-v3/snapshot?marketHash=0x1", "Forbidden")
    assert "testnet-only" not in str(err)


def test_cutover_constant_referenced_in_hint(monkeypatch) -> None:
    monkeypatch.setattr("sxbot.api.v3_mainnet_is_live", lambda now=None: False)
    err = SxApiError(403, "https://api.sx.bet/trades-v3/public", "Forbidden")
    assert V3_MAINNET_LIVE_AT.isoformat() in str(err)


def test_get_retries_read_timeout(monkeypatch) -> None:
    monkeypatch.setattr("sxbot.api.time.sleep", lambda _s: None)
    client = SxClient("https://api.sx.bet")
    calls = {"n": 0}

    def fake_get(path, params=None):
        calls["n"] += 1
        if calls["n"] < 3:
            raise httpx.ReadTimeout("The read operation timed out")
        request = httpx.Request("GET", "https://api.sx.bet/markets/active")
        return httpx.Response(200, json={"data": {"ok": True}}, request=request)

    client._http.get = fake_get  # type: ignore[method-assign]
    assert client._get("/markets/active") == {"ok": True}
    assert calls["n"] == 3


def test_non_403_status_has_no_rollout_hint(monkeypatch) -> None:
    monkeypatch.setattr("sxbot.api.v3_mainnet_is_live", lambda now=None: False)
    err = SxApiError(401, "https://api.sx.bet/orders-v3", "BAD_AUTH")
    assert "testnet-only" not in str(err)
