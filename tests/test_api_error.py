from sxbot.api import SxApiError
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


def test_non_403_status_has_no_rollout_hint(monkeypatch) -> None:
    monkeypatch.setattr("sxbot.api.v3_mainnet_is_live", lambda now=None: False)
    err = SxApiError(401, "https://api.sx.bet/orders-v3", "BAD_AUTH")
    assert "testnet-only" not in str(err)
